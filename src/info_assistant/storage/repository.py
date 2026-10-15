from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from info_assistant.domain.enums import RunStatus
from info_assistant.domain.models import (
    Observation,
    RunEvent,
    RunRecord,
    SourceRecord,
    SourceSnapshot,
    TaskPage,
    TaskRecord,
    TaskSpec,
    TaskVersion,
)
from info_assistant.storage.database import Database


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


class Repository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_task(self, spec: TaskSpec) -> TaskRecord:
        task_id = str(uuid4())
        version = TaskVersion(task_id=task_id, number=1, spec=spec)
        created = _stamp(version.created_at)
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO tasks(id, created_at, updated_at, latest_version) "
                "VALUES (?, ?, ?, 1)",
                (task_id, created, created),
            )
            self._insert_version(connection, version)
        return TaskRecord(
            id=task_id,
            created_at=version.created_at,
            updated_at=version.created_at,
            latest_version=version,
        )

    def create_version(self, task_id: str, spec: TaskSpec) -> TaskVersion:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT latest_version FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            version = TaskVersion(task_id=task_id, number=row["latest_version"] + 1, spec=spec)
            self._insert_version(connection, version)
            connection.execute(
                "UPDATE tasks SET latest_version = ?, updated_at = ? WHERE id = ?",
                (version.number, _stamp(version.created_at), task_id),
            )
        return version

    @staticmethod
    def _insert_version(connection: sqlite3.Connection, version: TaskVersion) -> None:
        connection.execute(
            "INSERT INTO task_versions(id, task_id, version, spec_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                version.id,
                version.task_id,
                version.number,
                version.spec.model_dump_json(),
                _stamp(version.created_at),
            ),
        )

    def get_task(self, task_id: str) -> TaskRecord:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT t.*, v.id AS version_id, v.version, v.spec_json, "
                "v.created_at AS version_created, "
                "(SELECT status FROM runs WHERE task_id=t.id ORDER BY created_at DESC LIMIT 1) "
                "AS run_status "
                "FROM tasks t JOIN task_versions v ON v.task_id=t.id "
                "AND v.version=t.latest_version WHERE t.id=?",
                (task_id,),
            ).fetchone()
        if row is None:
            raise KeyError(task_id)
        return self._task_record(row)

    @staticmethod
    def _task_record(row: sqlite3.Row) -> TaskRecord:
        version = TaskVersion(
            id=row["version_id"],
            task_id=row["id"],
            number=row["version"],
            spec=TaskSpec.model_validate_json(row["spec_json"]),
            created_at=datetime.fromisoformat(row["version_created"]),
        )
        return TaskRecord(
            id=row["id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            latest_version=version,
            latest_run_status=RunStatus(row["run_status"]) if row["run_status"] else None,
        )

    def list_history(self, query: str, limit: int, offset: int) -> TaskPage:
        safe_limit = min(max(limit, 1), 100)
        safe_offset = max(offset, 0)
        pattern = f"%{query.strip()}%"
        with self.database.connection() as connection:
            total = connection.execute(
                "SELECT COUNT(*) FROM tasks t JOIN task_versions v "
                "ON v.task_id=t.id AND v.version=t.latest_version "
                "WHERE v.spec_json LIKE ?",
                (pattern,),
            ).fetchone()[0]
            rows = connection.execute(
                "SELECT t.*, v.id AS version_id, v.version, v.spec_json, "
                "v.created_at AS version_created, "
                "(SELECT status FROM runs WHERE task_id=t.id ORDER BY created_at DESC LIMIT 1) "
                "AS run_status "
                "FROM tasks t JOIN task_versions v ON v.task_id=t.id "
                "AND v.version=t.latest_version WHERE v.spec_json LIKE ? "
                "ORDER BY t.updated_at DESC LIMIT ? OFFSET ?",
                (pattern, safe_limit, safe_offset),
            ).fetchall()
        return TaskPage(
            items=[self._task_record(row) for row in rows],
            total=total,
            limit=safe_limit,
            offset=safe_offset,
        )

    def create_run(self, task_id: str, version_id: str) -> RunRecord:
        run = RunRecord(task_id=task_id, version_id=version_id)
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO runs(id, task_id, version_id, status, created_at, active_seconds) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run.id,
                    run.task_id,
                    run.version_id,
                    run.status.value,
                    _stamp(run.created_at),
                    run.active_seconds,
                ),
            )
        return run

    def get_run(self, run_id: str) -> RunRecord:
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return RunRecord(
            id=row["id"],
            task_id=row["task_id"],
            version_id=row["version_id"],
            status=RunStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            finished_at=(
                datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None
            ),
            active_seconds=row["active_seconds"],
            stop_reason=row["stop_reason"],
        )

    def append_event(
        self,
        run_id: str,
        kind: str,
        message: str,
        idempotency_key: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> int:
        event = RunEvent(
            run_id=run_id,
            kind=kind,
            message=message,
            payload=payload or {},
        )
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO run_events(run_id, kind, message, payload_json, idempotency_key, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(idempotency_key) DO NOTHING",
                (
                    event.run_id,
                    event.kind,
                    event.message,
                    json.dumps(event.payload, ensure_ascii=False),
                    idempotency_key,
                    _stamp(event.created_at),
                ),
            )
            if cursor.lastrowid:
                return cursor.lastrowid
            row = connection.execute(
                "SELECT id FROM run_events WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if row is None:
                raise RuntimeError("idempotent event insert failed")
            return row["id"]

    def events_after(self, run_id: str, cursor: int) -> list[RunEvent]:
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM run_events WHERE run_id=? AND id>? ORDER BY id",
                (run_id, max(cursor, 0)),
            ).fetchall()
        return [
            RunEvent(
                id=row["id"],
                run_id=row["run_id"],
                kind=row["kind"],
                message=row["message"],
                payload=json.loads(row["payload_json"]),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def save_source(self, source: SourceRecord) -> SourceRecord:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO sources(id, url, canonical_url, title, status, last_checked_at, "
                "error_code, error_message) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(canonical_url) DO UPDATE SET url=excluded.url, title=excluded.title, "
                "status=excluded.status, last_checked_at=excluded.last_checked_at, "
                "error_code=excluded.error_code, error_message=excluded.error_message",
                (
                    source.id,
                    source.url,
                    source.canonical_url,
                    source.title,
                    source.status.value,
                    _stamp(source.last_checked_at) if source.last_checked_at else None,
                    source.error_code,
                    source.error_message,
                ),
            )
            row = connection.execute(
                "SELECT * FROM sources WHERE canonical_url=?", (source.canonical_url,)
            ).fetchone()
        return SourceRecord(
            id=row["id"],
            url=row["url"],
            canonical_url=row["canonical_url"],
            title=row["title"],
            status=row["status"],
            last_checked_at=(
                datetime.fromisoformat(row["last_checked_at"])
                if row["last_checked_at"]
                else None
            ),
            error_code=row["error_code"],
            error_message=row["error_message"],
        )

    def save_snapshot(self, snapshot: SourceSnapshot) -> SourceSnapshot:
        content_hash = snapshot.content_hash or hashlib.sha256(
            snapshot.text.encode("utf-8")
        ).hexdigest()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO source_snapshots(id, source_id, run_id, fetched_at, content_type, "
                "text, content_hash, http_status, page_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source_id, run_id, content_hash) DO NOTHING",
                (
                    snapshot.id,
                    snapshot.source_id,
                    snapshot.run_id,
                    _stamp(snapshot.fetched_at),
                    snapshot.content_type,
                    snapshot.text,
                    content_hash,
                    snapshot.http_status,
                    snapshot.page_count,
                ),
            )
            row = connection.execute(
                "SELECT * FROM source_snapshots WHERE source_id=? AND run_id=? AND content_hash=?",
                (snapshot.source_id, snapshot.run_id, content_hash),
            ).fetchone()
        return SourceSnapshot(
            id=row["id"],
            source_id=row["source_id"],
            run_id=row["run_id"],
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
            content_type=row["content_type"],
            text=row["text"],
            content_hash=row["content_hash"],
            http_status=row["http_status"],
            page_count=row["page_count"],
        )

    def upsert_observation(
        self,
        task_id: str,
        run_id: str,
        observation: Observation,
        idempotency_key: str,
    ) -> Observation:
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT id, entity_id FROM observations WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                return self._load_observation(connection, existing["id"])
            entity = connection.execute(
                "SELECT id FROM entities WHERE task_id=? AND identity_key=?",
                (task_id, observation.entity_key),
            ).fetchone()
            entity_id = entity["id"] if entity else str(uuid4())
            if entity is None:
                connection.execute(
                    "INSERT INTO entities(id, task_id, canonical_name, identity_key, "
                    "aliases_json, created_at) VALUES (?, ?, ?, ?, '[]', ?)",
                    (
                        entity_id,
                        task_id,
                        observation.entity_key,
                        observation.entity_key,
                        _stamp(datetime.now(UTC)),
                    ),
                )
            connection.execute(
                "INSERT INTO observations(id, task_id, run_id, entity_id, field_name, raw_value, "
                "normalized_value, unit, period, qualifiers_json, origin, status, confidence, "
                "observed_at, metadata_json, idempotency_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    observation.id,
                    task_id,
                    run_id,
                    entity_id,
                    observation.field_name,
                    observation.raw_value,
                    observation.normalized_value,
                    observation.unit,
                    observation.period,
                    json.dumps(observation.qualifiers, ensure_ascii=False),
                    observation.origin.value,
                    observation.status.value,
                    observation.confidence,
                    _stamp(observation.observed_at),
                    json.dumps(observation.metadata, ensure_ascii=False),
                    idempotency_key,
                ),
            )
            for evidence in observation.evidence:
                connection.execute(
                    "INSERT INTO observation_evidence(observation_id, snapshot_id, start_offset, "
                    "end_offset, quote, page_number, locator) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        observation.id,
                        evidence.snapshot_id,
                        evidence.start,
                        evidence.end,
                        evidence.quote,
                        evidence.page_number,
                        evidence.locator,
                    ),
                )
            return self._load_observation(connection, observation.id)

    @staticmethod
    def _load_observation(connection: sqlite3.Connection, observation_id: str) -> Observation:
        row = connection.execute(
            "SELECT o.*, e.identity_key FROM observations o "
            "JOIN entities e ON e.id=o.entity_id WHERE o.id=?",
            (observation_id,),
        ).fetchone()
        evidence_rows = connection.execute(
            "SELECT * FROM observation_evidence WHERE observation_id=? ORDER BY start_offset",
            (observation_id,),
        ).fetchall()
        return Observation(
            id=row["id"],
            entity_key=row["identity_key"],
            field_name=row["field_name"],
            raw_value=row["raw_value"],
            normalized_value=row["normalized_value"],
            unit=row["unit"],
            period=row["period"],
            qualifiers=json.loads(row["qualifiers_json"]),
            origin=row["origin"],
            status=row["status"],
            evidence=[
                {
                    "snapshot_id": evidence["snapshot_id"],
                    "start": evidence["start_offset"],
                    "end": evidence["end_offset"],
                    "quote": evidence["quote"],
                    "page_number": evidence["page_number"],
                    "locator": evidence["locator"],
                }
                for evidence in evidence_rows
            ],
            confidence=row["confidence"],
            observed_at=datetime.fromisoformat(row["observed_at"]),
            metadata=json.loads(row["metadata_json"]),
        )

    def observations_for_task(self, task_id: str) -> list[Observation]:
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT id FROM observations WHERE task_id=? ORDER BY observed_at, id", (task_id,)
            ).fetchall()
            return [self._load_observation(connection, row["id"]) for row in rows]

