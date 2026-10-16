from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from info_assistant.domain.models import TaskRecord, TaskSpec, TimeWindow
from info_assistant.services.conflicts import ConflictService
from info_assistant.services.export_service import ExportService
from info_assistant.services.retention_service import RetentionService
from info_assistant.services.run_control import InvalidRunTransition
from info_assistant.services.task_planner import TaskChange

router = APIRouter(prefix="/api")


class TaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    raw_input: str = Field(min_length=1, max_length=10_000)
    supplemental_material: str = Field(default="", max_length=100_000)


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=10_000)


class CorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    field: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=10_000)


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    format: str = Field(pattern="^(text|csv|xlsx|json|markdown)$")
    view: str = Field(
        default="all",
        pattern="^(all|confirmed|user_confirmed|pending_review|excluded)$",
    )
    entity_ids: list[str] | None = Field(default=None, max_length=1000)
    fields: list[str] | None = Field(default=None, max_length=50)


class DeleteTaskDataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keep_derived_results: bool = False


def _model_json(model) -> dict:
    return model.model_dump(mode="json")


def _active_model(request: Request):
    return request.app.state.runner


def _capability(request: Request, spec):
    return request.app.state.capability_checker.assess(spec)


def _version_for_new_run(request: Request, task: TaskRecord, spec: TaskSpec | None = None):
    current_spec = spec or task.latest_version.spec
    if "最近" not in current_spec.raw_input:
        return (
            task.latest_version
            if current_spec == task.latest_version.spec
            else request.app.state.repository.create_version(task.id, current_spec)
        )
    now = datetime.now(UTC)
    assumptions = [item for item in current_spec.assumptions if "最近" not in item]
    assumptions.append("“最近”按运行开始时刻前 7 天解释")
    frozen = current_spec.model_copy(
        update={
            "time_window": TimeWindow(
                start=now - timedelta(days=7),
                end=now,
                label="最近 7 天",
            ),
            "assumptions": assumptions,
        }
    )
    return request.app.state.repository.create_version(task.id, frozen)


def _current_run(request: Request, task_id: str, version_id: str):
    with request.app.state.database.connection() as connection:
        return connection.execute(
            "SELECT * FROM runs WHERE task_id=? AND version_id=? ORDER BY created_at DESC LIMIT 1",
            (task_id, version_id),
        ).fetchone()


@router.post("/tasks", status_code=201)
def create_task(body: TaskCreateRequest, request: Request) -> dict:
    supplements = [line.strip() for line in body.supplemental_material.splitlines() if line.strip()]
    spec = request.app.state.planner.interpret(
        body.raw_input,
        supplemental_material=supplements,
    )
    task = request.app.state.repository.create_task(spec)
    return {
        "id": task.id,
        "version": task.latest_version.number,
        "spec": _model_json(task.latest_version.spec),
        "search_query": request.app.state.planner.build_search_query(spec),
        "capability": _model_json(_capability(request, spec)),
    }


@router.get("/tasks/{task_id}")
def get_task(task_id: str, request: Request) -> dict:
    try:
        task = request.app.state.repository.get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在") from None
    run = _current_run(request, task_id, task.latest_version.id)
    return {
        **_model_json(task),
        "spec": _model_json(task.latest_version.spec),
        "search_query": request.app.state.planner.build_search_query(task.latest_version.spec),
        "capability": _model_json(_capability(request, task.latest_version.spec)),
        "current_run": _run_json(run) if run else None,
    }


@router.post("/tasks/{task_id}/runs", status_code=202)
def start_run(task_id: str, request: Request) -> dict:
    try:
        task = request.app.state.repository.get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在") from None
    capability = _capability(request, task.latest_version.spec)
    if not capability.ready:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "capability_missing",
                "missing": capability.missing,
                "data_flow": capability.data_flow,
                "local_fallbacks": capability.local_fallbacks,
            },
        )
    version = _version_for_new_run(request, task)
    run = request.app.state.control.enqueue(task_id, version.id)
    request.app.state.dispatch_run(run.id)
    return _run_json(request.app.state.repository.get_run(run.id))


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request) -> dict:
    try:
        run = request.app.state.repository.get_run(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="运行记录不存在") from None
    return {**_run_json(run), "budget": _model_json(request.app.state.ledger.snapshot(run_id))}


@router.get("/runs/{run_id}/events")
def get_events(
    run_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
) -> dict:
    try:
        request.app.state.repository.get_run(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="运行记录不存在") from None
    events = request.app.state.event_reader.after(run_id, after)
    return {
        "items": [_model_json(item) for item in events],
        "next_cursor": events[-1].id if events else after,
    }


@router.post("/runs/{run_id}/pause")
def pause_run(run_id: str, request: Request) -> dict:
    try:
        run = request.app.state.control.pause(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="运行记录不存在") from None
    except InvalidRunTransition:
        raise HTTPException(status_code=409, detail="当前状态不能暂停") from None
    return _run_json(run)


@router.post("/runs/{run_id}/resume", status_code=202)
def resume_run(run_id: str, request: Request) -> dict:
    try:
        run = request.app.state.recovery.resume(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="运行记录不存在") from None
    except InvalidRunTransition:
        raise HTTPException(status_code=409, detail="当前状态不能恢复") from None
    request.app.state.dispatch_run(run_id)
    return _run_json(run)


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, request: Request) -> dict:
    try:
        run = request.app.state.control.cancel(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="运行记录不存在") from None
    except InvalidRunTransition:
        raise HTTPException(status_code=409, detail="当前状态不能取消") from None
    return _run_json(run)


@router.post("/tasks/{task_id}/messages")
def task_message(task_id: str, body: MessageRequest, request: Request) -> dict:
    try:
        task = request.app.state.repository.get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在") from None
    change: TaskChange = request.app.state.planner.apply_message(
        task.latest_version.spec,
        body.message,
    )
    if change.action == "view":
        return {"action": "view", "view_filter": change.view_filter}
    if change.action == "continue":
        capability = _capability(request, change.spec)
        if not capability.ready:
            raise HTTPException(
                status_code=409,
                detail={"code": "capability_missing", "missing": capability.missing},
            )
        version = _version_for_new_run(request, task, change.spec)
        run = request.app.state.control.enqueue(task_id, version.id)
        request.app.state.dispatch_run(run.id)
        return {"action": "continue", "run": _run_json(run)}
    version = request.app.state.repository.create_version(task_id, change.spec)
    return {
        "action": "new_version",
        "version": _model_json(version),
        "search_query": request.app.state.planner.build_search_query(version.spec),
        "capability": _model_json(_capability(request, version.spec)),
    }


@router.get("/tasks/{task_id}/results")
def task_results(
    task_id: str,
    request: Request,
    view: str = Query(
        default="all", pattern="^(all|confirmed|user_confirmed|pending_review|excluded)$"
    ),
) -> dict:
    try:
        task = request.app.state.repository.get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在") from None
    run = _current_run(request, task_id, task.latest_version.id)
    if not run:
        return {"run_id": None, "run_status": None, "items": [], "sources": [], "view": view}

    with request.app.state.database.connection() as connection:
        snapshot_rows = connection.execute(
            "SELECT ss.id AS snapshot_id, s.title, s.url, ss.fetched_at, "
            "substr(ss.text, 1, 1200) AS excerpt "
            "FROM source_snapshots ss JOIN sources s ON s.id=ss.source_id "
            "WHERE ss.run_id=? ORDER BY ss.fetched_at, ss.id LIMIT 100",
            (run["id"],),
        ).fetchall()
        sources = [dict(row) for row in snapshot_rows]
        rows = connection.execute(
            "SELECT o.*, e.id AS result_entity_id, e.canonical_name, e.identity_key "
            "FROM observations o JOIN entities e ON e.id=o.entity_id "
            "WHERE o.run_id=? ORDER BY e.canonical_name COLLATE NOCASE, "
            "o.field_name, o.observed_at",
            (run["id"],),
        ).fetchall()
        result_items: dict[str, dict] = {}
        observations_for_conflicts = {}
        source_by_snapshot: dict[str, str] = {}
        for row in rows:
            entity_id = row["result_entity_id"]
            if entity_id not in result_items:
                result_items[entity_id] = {
                    "entity_id": entity_id,
                    "entity": row["canonical_name"],
                    "identity_key": row["identity_key"],
                    "observations": [],
                    "reasons": [],
                    "official_count_eligible": True,
                }
                observations_for_conflicts[entity_id] = []
            evidence_rows = connection.execute(
                "SELECT oe.*, ss.fetched_at, ss.content_type, s.url, s.title "
                "FROM observation_evidence oe LEFT JOIN source_snapshots ss "
                "ON ss.id=oe.snapshot_id LEFT JOIN sources s ON s.id=ss.source_id "
                "WHERE oe.observation_id=? ORDER BY oe.start_offset",
                (row["id"],),
            ).fetchall()
            correction = connection.execute(
                "SELECT value FROM entity_corrections WHERE entity_id=? AND field_name=? "
                "AND reverted_at IS NULL ORDER BY created_at DESC LIMIT 1",
                (entity_id, row["field_name"]),
            ).fetchone()
            metadata = json.loads(row["metadata_json"])
            original_status = row["status"]
            status = "user_confirmed" if correction else original_status
            value = correction["value"] if correction else row["raw_value"]
            evidence = []
            for item in evidence_rows:
                source_url = item["url"] or item["source_url"] or ""
                source_title = item["title"] or item["source_title"] or ""
                host = urlsplit(source_url).hostname or ""
                if item["snapshot_id"]:
                    source_by_snapshot[item["snapshot_id"]] = host
                evidence.append(
                    {
                        "snapshot_id": item["snapshot_id"],
                        "quote": item["quote"],
                        "url": source_url,
                        "title": source_title,
                        "page_number": item["page_number"],
                        "locator": item["locator"],
                        "snapshot_available": item["snapshot_id"] is not None,
                        "fetched_at": item["fetched_at"] or item["source_fetched_at"],
                    }
                )
            result_items[entity_id]["observations"].append(
                {
                    "id": row["id"],
                    "field": row["field_name"],
                    "value": value,
                    "original_value": row["raw_value"],
                    "normalized_value": row["normalized_value"],
                    "unit": row["unit"],
                    "period": row["period"],
                    "qualifiers": json.loads(row["qualifiers_json"]),
                    "status": status,
                    "origin": "user_correction" if correction else row["origin"],
                    "confidence": row["confidence"],
                    "reasons": metadata.get("evaluation_reasons", []),
                    "evidence": evidence,
                }
            )
            result_items[entity_id]["reasons"].extend(metadata.get("evaluation_reasons", []))
            result_items[entity_id]["official_count_eligible"] &= bool(
                metadata.get("official_count_eligible", False)
            ) and not bool(correction)
            observation = request.app.state.repository._load_observation(connection, row["id"])
            observations_for_conflicts[entity_id].append(observation)

    output = []
    for entity_id, entity in result_items.items():
        statuses = [item["status"] for item in entity["observations"]]
        if "pending_review" in statuses:
            entity_status = "pending_review"
        elif "excluded" in statuses:
            entity_status = "excluded"
        elif "user_confirmed" in statuses:
            entity_status = "user_confirmed"
        elif "confirmed" in statuses:
            entity_status = "confirmed"
        else:
            entity_status = statuses[0] if statuses else "pending_review"
        entity["status"] = entity_status
        entity["reasons"] = list(dict.fromkeys(entity["reasons"]))
        entity["conflicts"] = [
            {
                "field": group.field_name,
                "values": [
                    {
                        "value": value.value,
                        "independent_source_count": value.independent_source_count,
                    }
                    for value in group.values
                ],
            }
            for group in ConflictService().group(
                observations_for_conflicts[entity_id], source_by_snapshot
            )
        ]
        if (
            view == "all"
            or entity_status == view
            or any(observation["status"] == view for observation in entity["observations"])
        ):
            output.append(entity)
    return {
        "run_id": run["id"],
        "run_status": run["status"],
        "items": output,
        "sources": sources,
        "view": view,
    }


@router.post("/tasks/{task_id}/export")
def export_task(task_id: str, body: ExportRequest, request: Request) -> Response:
    try:
        task = request.app.state.repository.get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在") from None
    results = task_results(task_id, request, body.view)
    try:
        artifact = ExportService(request.app.state.database).export(
            task_id=task_id,
            task_version_id=task.latest_version.id,
            run_id=results["run_id"],
            spec=task.latest_version.spec,
            records=results["items"],
            sources=results.get("sources", []),
            format=body.format,
            view=body.view,
            selected_entity_ids=body.entity_ids,
            selected_fields=body.fields,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return Response(
        content=artifact.content,
        media_type=artifact.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{artifact.filename}"',
            "X-Export-Id": artifact.export_id,
            "X-Export-Row-Count": str(artifact.record_count),
        },
    )


@router.delete("/tasks/{task_id}/data")
def delete_task_data(
    task_id: str,
    request: Request,
    keep_derived_results: bool = Query(default=False),
) -> dict:
    try:
        report = RetentionService(request.app.state.database).delete_task_material(
            task_id, keep_derived_results=keep_derived_results
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在") from None
    return {
        "task_id": report.task_id,
        "snapshots_deleted": report.snapshots_deleted,
        "tasks_deleted": report.tasks_deleted,
        "observations_preserved": report.observations_preserved,
        "keep_derived_results": keep_derived_results,
    }


@router.patch("/entities/{entity_id}")
def correct_entity(entity_id: str, body: CorrectionRequest, request: Request) -> dict:
    database = request.app.state.database
    with database.transaction() as connection:
        entity = connection.execute("SELECT id FROM entities WHERE id=?", (entity_id,)).fetchone()
        if not entity:
            raise HTTPException(status_code=404, detail="结果对象不存在")
        prior = connection.execute(
            "SELECT value FROM entity_corrections WHERE entity_id=? AND field_name=? "
            "AND reverted_at IS NULL ORDER BY created_at DESC LIMIT 1",
            (entity_id, body.field),
        ).fetchone()
        if prior:
            previous = prior["value"]
        else:
            observation = connection.execute(
                "SELECT raw_value FROM observations WHERE entity_id=? AND field_name=? "
                "ORDER BY observed_at DESC LIMIT 1",
                (entity_id, body.field),
            ).fetchone()
            previous = observation["raw_value"] if observation else None
        connection.execute(
            "INSERT INTO entity_corrections(id, entity_id, field_name, value, previous_value, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(uuid4()),
                entity_id,
                body.field,
                body.value,
                previous,
                datetime.now(UTC).isoformat(),
            ),
        )
    return {
        "entity_id": entity_id,
        "field": body.field,
        "value": body.value,
        "origin": "user_correction",
    }


@router.delete("/entities/{entity_id}/corrections/{field_name}")
def undo_correction(entity_id: str, field_name: str, request: Request) -> dict:
    with request.app.state.database.transaction() as connection:
        cursor = connection.execute(
            "UPDATE entity_corrections SET reverted_at=? WHERE id=(SELECT id FROM "
            "entity_corrections WHERE entity_id=? AND field_name=? AND reverted_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1)",
            (datetime.now(UTC).isoformat(), entity_id, field_name),
        )
        if not cursor.rowcount:
            raise HTTPException(status_code=404, detail="没有可撤销的修正")
    return {"entity_id": entity_id, "field": field_name, "undone": True}


def _run_json(run) -> dict:
    return run.model_dump(mode="json") if hasattr(run, "model_dump") else dict(run)
