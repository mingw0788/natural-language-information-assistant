from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from info_assistant.domain.enums import ObservationStatus, RunStatus, SourceStatus, TaskKind
from info_assistant.domain.models import Observation, SourceRecord, SourceSnapshot, TaskSpec
from info_assistant.providers.base import (
    ModelCallBudget,
    ModelProvider,
    SearchProvider,
    SearchQuery,
)
from info_assistant.services.budget_ledger import BudgetExceeded, BudgetLedger
from info_assistant.services.capabilities import CapabilityChecker
from info_assistant.services.entity_resolution import EntityIdentity, EntityResolver
from info_assistant.services.extractor import Extractor
from info_assistant.services.result_evaluator import ResultEvaluator
from info_assistant.services.run_control import (
    ActiveRunExists,
    InvalidRunTransition,
    RunControl,
)
from info_assistant.services.source_fetcher import FetchOutcome, SourceFetcher
from info_assistant.storage.database import Database
from info_assistant.storage.repository import Repository


class CollectionRunner:
    def __init__(
        self,
        *,
        database: Database,
        repository: Repository,
        control: RunControl,
        ledger: BudgetLedger,
        fetcher: SourceFetcher,
        search_provider: SearchProvider | None = None,
        model_provider: ModelProvider | None = None,
        browser_reader: Any | None = None,
        capability_checker: CapabilityChecker | None = None,
        active_time_limit_seconds: int = 15 * 60,
    ) -> None:
        self.database = database
        self.repository = repository
        self.control = control
        self.ledger = ledger
        self.fetcher = fetcher
        self.search_provider = search_provider
        self.model_provider = model_provider
        self.browser_reader = browser_reader
        self.capability_checker = capability_checker
        self.active_time_limit_seconds = active_time_limit_seconds

    def run(self, run_id: str) -> None:
        run = self.repository.get_run(run_id)
        if run.status != RunStatus.QUEUED:
            return
        try:
            self.control.start(run_id)
        except ActiveRunExists:
            self._event(run_id, "queued", "已有任务正在运行；本任务保留在队列中")
            return
        except InvalidRunTransition:
            return

        spec = self.repository.spec_for_run(run_id)
        self._event(run_id, "stage", "已确认任务版本和停止条件", {"stage": "interpret"})
        if self.capability_checker:
            report = self.capability_checker.assess(spec)
            if not report.ready:
                self._event(
                    run_id,
                    "blocked",
                    "缺少完成自动发现所需的服务配置",
                    {"missing": report.missing, "data_flow": report.data_flow},
                )
                self.control.finish(run_id, RunStatus.BLOCKED, "；".join(report.missing))
                return

        sources = [
            (source.value, source.label or source.value)
            for source in spec.sources
            if source.kind == "url"
        ]
        has_local_material = bool(spec.pasted_material) or any(
            source.kind == "text" for source in spec.sources
        )
        used_search = not sources and not has_local_material
        if used_search:
            if self.search_provider is None:
                reason = "未配置搜索服务，无法发现公开网页"
                self._event(run_id, "blocked", reason)
                self.control.finish(run_id, RunStatus.BLOCKED, reason)
                return
            try:
                reservation = self.ledger.reserve(run_id, "search_calls", 1)
            except BudgetExceeded as exc:
                self._finish_budget(run_id, exc, had_results=False)
                return
            self._event(run_id, "stage", "正在搜索公开网页", {"stage": "search"})
            try:
                freshness = None
                if spec.time_window:
                    freshness = (
                        f"{spec.time_window.start.date().isoformat()}to"
                        f"{spec.time_window.end.date().isoformat()}"
                    )
                response = self.search_provider.search(
                    SearchQuery(query=spec.raw_input, count=8, freshness=freshness)
                )
            except Exception:
                self.ledger.settle(reservation.id, None)
                self._event(
                    run_id,
                    "source_error",
                    "搜索服务调用失败；本次额度按结果未知计入",
                    {"stage": "search"},
                )
                self.control.finish(run_id, RunStatus.BLOCKED, "搜索服务调用失败")
                return
            self.ledger.settle(reservation.id, 1)
            sources = [(item.url, item.title or item.url) for item in response.results[:8]]
            if not sources:
                self._event(
                    run_id,
                    "search_complete",
                    "初始搜索没有返回来源；将检查是否需要有限补搜",
                )
            else:
                self._event(
                    run_id,
                    "search_complete",
                    f"搜索得到 {len(sources)} 个待核验来源；摘要只作为线索",
                    {"count": len(sources), "provider": response.provider},
                )

        for index, (url, label) in enumerate(sources, start=1):
            if not self._can_continue(run_id):
                return
            self._collect_url(run_id, spec, url, label, index)
            if not self._can_continue(run_id):
                return
            target_count = self._target_count(spec)
            if target_count is not None and self._confirmed_entity_count(run_id) >= target_count:
                self._event(run_id, "stop_condition", "已达到用户指定的去重对象数量")
                break

        if used_search:
            initial_urls = {url.strip().casefold().rstrip("/") for url, _label in sources}
            self._supplement_sources(run_id, spec, initial_urls)

        for index, text in enumerate(spec.pasted_material, start=1):
            if not self._can_continue(run_id):
                return
            self._collect_local_text(run_id, spec, text, f"用户粘贴材料 {index}")

        text_sources = [source.value for source in spec.sources if source.kind == "text"]
        for index, text in enumerate(text_sources, start=1):
            if not self._can_continue(run_id):
                return
            self._collect_local_text(run_id, spec, text, f"本地文字材料 {index}")

        current = self.repository.get_run(run_id)
        if current.status != RunStatus.RUNNING:
            return
        states = self.repository.source_states_for_run(run_id)
        failed = any(state["status"] in {"failed", "unknown"} for state in states)
        fetched = any(state["status"] == "fetched" for state in states)
        if used_search and not states:
            self.control.finish(run_id, RunStatus.PARTIAL, "搜索没有发现可读取来源")
            self._event(run_id, "finished", "搜索与有限补充搜索均未发现可读取来源")
        elif self.control.elapsed_active_seconds(run_id) >= self.active_time_limit_seconds:
            self.control.finish(run_id, RunStatus.PARTIAL, "单轮主动运行达到 15 分钟上限")
            self._event(run_id, "finished", "已达到单轮主动运行时间上限")
        elif failed and fetched:
            self.control.finish(run_id, RunStatus.PARTIAL, "部分来源读取失败")
            self._event(run_id, "finished", "采集部分完成；可用来源已保留")
        elif failed:
            self.control.finish(run_id, RunStatus.PARTIAL, "来源读取失败")
            self._event(run_id, "finished", "采集部分完成；未能读取来源")
        else:
            self.control.finish(run_id, RunStatus.COMPLETED)
            self._event(run_id, "finished", "采集完成")

    def _supplement_sources(self, run_id: str, spec: TaskSpec, visited: set[str]) -> None:
        if self.search_provider is None:
            return
        for round_number in range(1, 3):
            if not self._can_continue(run_id) or not self._needs_supplement(run_id, spec):
                return
            candidates: list[tuple[str, str]] = []
            queries = self._supplement_queries(spec)
            for query in queries[:2]:
                if not self._can_continue(run_id):
                    return
                try:
                    reservation = self.ledger.reserve(run_id, "search_calls", 1)
                except BudgetExceeded as exc:
                    self._finish_budget(
                        run_id,
                        exc,
                        had_results=bool(self.repository.observations_for_run(run_id)),
                    )
                    return
                self._event(
                    run_id,
                    "stage",
                    f"正在进行第 {round_number} 轮补充搜索",
                    {"stage": "supplement_search", "round": round_number},
                )
                freshness = None
                if spec.time_window:
                    freshness = (
                        f"{spec.time_window.start.date().isoformat()}to"
                        f"{spec.time_window.end.date().isoformat()}"
                    )
                try:
                    response = self.search_provider.search(
                        SearchQuery(query=query, count=2, freshness=freshness)
                    )
                except Exception:
                    self.ledger.settle(reservation.id, None)
                    self._event(run_id, "search_error", "补充搜索失败；本轮已用额度按结果未知计入")
                    continue
                self.ledger.settle(reservation.id, 1)
                for result in response.results[:2]:
                    key = result.url.strip().casefold().rstrip("/")
                    if key and key not in visited:
                        visited.add(key)
                        candidates.append((result.url, result.title or result.url))
            if not candidates:
                self._event(run_id, "search_complete", "补充搜索没有发现新的来源")
                return
            self._event(
                run_id,
                "search_complete",
                f"第 {round_number} 轮补充搜索得到 {len(candidates)} 个新来源；摘要只作为线索",
                {"round": round_number, "count": len(candidates)},
            )
            for index, (url, label) in enumerate(candidates, start=1):
                if not self._can_continue(run_id):
                    return
                self._collect_url(run_id, spec, url, label, index)
                if not self._can_continue(run_id):
                    return
                target_count = self._target_count(spec)
                if (
                    target_count is not None
                    and self._confirmed_entity_count(run_id) >= target_count
                ):
                    self._event(run_id, "stop_condition", "已达到用户指定的去重对象数量")
                    return
            if not self._needs_supplement(run_id, spec):
                return

    @staticmethod
    def _target_count(spec: TaskSpec) -> int | None:
        for condition in spec.stop_conditions:
            match = re.search(r"(?:达到|至少)\s*(\d+)\s*个", condition)
            if match:
                return int(match.group(1))
        return None

    def _confirmed_entity_count(self, run_id: str) -> int:
        entities = {
            item.entity_key
            for item in self.repository.observations_for_run(run_id)
            if item.status == ObservationStatus.CONFIRMED
        }
        return len(entities)

    def _needs_supplement(self, run_id: str, spec: TaskSpec) -> bool:
        target = self._target_count(spec)
        if target is not None:
            return self._confirmed_entity_count(run_id) < target
        observations = self.repository.observations_for_run(run_id)
        if not observations:
            return True
        if spec.required_conditions and any(
            item.status == ObservationStatus.PENDING_REVIEW for item in observations
        ):
            return True
        return spec.kind == TaskKind.LIST and not any(
            item.status == ObservationStatus.CONFIRMED for item in observations
        )

    @staticmethod
    def _supplement_queries(spec: TaskSpec) -> list[str]:
        base = spec.raw_input.strip()[:280]
        requested_fields = " ".join(field.name for field in spec.fields[:8])
        conditions = " ".join(condition.text for condition in spec.required_conditions[:4])
        if requested_fields or conditions:
            return [
                f"{base} 官方来源",
                f"{base} {conditions or requested_fields}".strip(),
            ]
        return [f"{base} 官方来源", f"{base} 其他可靠来源"]

    def _collect_url(
        self,
        run_id: str,
        spec: TaskSpec,
        url: str,
        label: str,
        index: int,
    ) -> None:
        state = self.repository.source_state(run_id, url)
        if state and state["status"] in {"fetched", "unknown"}:
            if state["status"] == "unknown":
                self._event(
                    run_id,
                    "uncertain",
                    f"来源 {label} 存在中断中的请求；未自动重复派发",
                    {"url": url},
                )
            return
        if state and state["status"] == "fetching":
            self.repository.mark_source_state(run_id, url, "unknown", next_url=state["next_url"])
            self._event(
                run_id,
                "uncertain",
                f"来源 {label} 的上次请求结果未知；未自动重复派发",
                {"url": url},
            )
            return

        current_url = state["next_url"] if state and state["status"] == "paused" else None
        current_url = (
            current_url
            or (state["next_url"] if state and state["status"] == "queued" else None)
            or url
        )
        pages = 0
        seen_hashes: set[str] = set()
        self._event(
            run_id,
            "stage",
            f"正在读取来源 {index}：{label[:160]}",
            {"stage": "fetch", "url": url, "index": index},
        )
        while current_url and pages < 5:
            if not self._can_continue(run_id):
                self.repository.mark_source_state(run_id, url, "paused", next_url=current_url)
                return
            try:
                reservation = self.ledger.reserve(run_id, "document_fetch", 1)
            except BudgetExceeded as exc:
                self._finish_budget(
                    run_id, exc, had_results=bool(self.repository.observations_for_run(run_id))
                )
                return
            self.repository.mark_source_state(run_id, url, "fetching", next_url=current_url)
            try:
                outcome = self.fetcher.fetch(current_url, timeout=30)
            except Exception:
                self.ledger.settle(reservation.id, None)
                self.repository.mark_source_state(
                    run_id,
                    url,
                    "failed",
                    next_url=current_url,
                    error_code="fetch_error",
                    error_message="来源读取失败；额度按结果未知计入",
                )
                self._event(run_id, "source_error", f"无法读取来源：{label}", {"url": url})
                return
            self.ledger.settle(reservation.id, 1)
            if outcome.status != "fetched":
                self._save_failed_source(outcome)
                self.repository.mark_source_state(
                    run_id,
                    url,
                    "failed",
                    next_url=current_url,
                    error_code=outcome.error_code,
                    error_message=outcome.error_message,
                )
                self._event(
                    run_id,
                    "source_error",
                    outcome.error_message or "来源无法读取",
                    {"url": url, "code": outcome.error_code},
                )
                return
            snapshot = self._save_outcome(run_id, outcome)
            content_hash = hashlib.sha256(snapshot.text.encode("utf-8")).hexdigest()
            if content_hash in seen_hashes:
                self.repository.mark_source_state(run_id, url, "fetched")
                return
            seen_hashes.add(content_hash)
            self._extract_snapshot(run_id, spec, snapshot)
            pages += 1
            self._event(
                run_id,
                "source_fetched",
                f"已读取来源 {label[:120]}（第 {pages} 页）",
                {"url": outcome.final_url, "page": pages},
            )
            if self.repository.get_run(run_id).status == RunStatus.CANCELLED:
                self.repository.mark_source_state(run_id, url, "fetched")
                return
            next_url = outcome.next_url if pages < 5 else None
            if next_url:
                self.repository.mark_source_state(run_id, url, "queued", next_url=next_url)
            else:
                self.repository.mark_source_state(run_id, url, "fetched")
            if self.repository.get_run(run_id).status == RunStatus.PAUSED:
                return
            current_url = next_url
        if pages >= 5 and current_url:
            self._event(run_id, "limit", "单个来源达到最多 5 页的读取上限", {"url": url})
            self.repository.mark_source_state(run_id, url, "fetched")

    def _collect_local_text(self, run_id: str, spec: TaskSpec, text: str, title: str) -> None:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        url = f"local://material/{digest}"
        source = self.repository.save_source(
            SourceRecord(
                url=url,
                canonical_url=url,
                title=title,
                status=SourceStatus.FETCHED,
                last_checked_at=datetime.now(UTC),
            )
        )
        snapshot = self.repository.save_snapshot(
            SourceSnapshot(
                source_id=source.id,
                run_id=run_id,
                text=text[:2_000_000],
                content_type="text/plain",
            )
        )
        self._extract_snapshot(run_id, spec, snapshot)
        self._event(run_id, "source_fetched", f"已处理{title}；内容只在本机处理")

    def _save_outcome(self, run_id: str, outcome: FetchOutcome) -> SourceSnapshot:
        canonical_url = outcome.final_url or outcome.requested_url
        source = self.repository.save_source(
            SourceRecord(
                url=outcome.requested_url,
                canonical_url=canonical_url,
                title=outcome.title,
                status=SourceStatus.FETCHED,
                last_checked_at=datetime.now(UTC),
            )
        )
        return self.repository.save_snapshot(
            SourceSnapshot(
                source_id=source.id,
                run_id=run_id,
                text=outcome.text[:2_000_000],
                content_type=outcome.content_type or "text/plain",
                http_status=outcome.http_status,
                page_count=outcome.page_count,
            )
        )

    def _save_failed_source(self, outcome: FetchOutcome) -> None:
        self.repository.save_source(
            SourceRecord(
                url=outcome.requested_url,
                canonical_url=outcome.final_url or outcome.requested_url,
                title=outcome.title,
                status=SourceStatus(outcome.status)
                if outcome.status in SourceStatus._value2member_map_
                else SourceStatus.FAILED,
                last_checked_at=datetime.now(UTC),
                error_code=outcome.error_code,
                error_message=outcome.error_message,
            )
        )

    def _extract_snapshot(self, run_id: str, spec: TaskSpec, snapshot: SourceSnapshot) -> None:
        extractor: Extractor
        if self.model_provider:
            extractor = Extractor(
                model_provider=_BudgetedModelProvider(self.model_provider, self.ledger, run_id)
            )
        else:
            extractor = Extractor()
        try:
            observations = extractor.extract(spec, snapshot)
        except BudgetExceeded as exc:
            self._event(run_id, "budget", f"模型提取达到额度上限：{exc.resource}")
            observations = Extractor().extract(spec, snapshot)
        except Exception:
            self._event(run_id, "model_error", "结构化提取未完成；尝试本机明确字段规则")
            observations = Extractor().extract(spec, snapshot)
        if (
            not observations
            and self.browser_reader
            and snapshot.content_type
            in {
                "text/html",
                "application/xhtml+xml",
            }
        ):
            try:
                browser_reservation = self.ledger.reserve(run_id, "browser_fetch", 1)
                document_reservation = self.ledger.reserve(run_id, "document_fetch", 1)
            except BudgetExceeded:
                if "browser_reservation" in locals():
                    self.ledger.release(browser_reservation.id)
                self._event(run_id, "budget", "动态页面读取额度不足，保留静态正文")
            else:
                try:
                    rendered = self.browser_reader.render(
                        snapshot.source_id and self._source_url(snapshot.source_id)
                    )
                except Exception:
                    self.ledger.settle(browser_reservation.id, None)
                    self.ledger.settle(document_reservation.id, None)
                    self._event(run_id, "browser_error", "动态页面读取失败，保留静态正文")
                else:
                    self.ledger.settle(browser_reservation.id, 1)
                    self.ledger.settle(document_reservation.id, 1)
                    if rendered.status == "fetched" and rendered.text:
                        dynamic_snapshot = self._save_outcome(run_id, rendered)
                        observations = Extractor().extract(spec, dynamic_snapshot)
                        snapshot = dynamic_snapshot
        task_id = self.repository.get_run(run_id).task_id
        source_host = self._source_host(snapshot.source_id)
        identities = self._entity_identities_for_task(task_id)
        grouped: dict[tuple[str, str], list[Observation]] = {}
        for observation in observations:
            candidate = EntityIdentity(name=observation.entity_key, source_host=source_host)
            decision = EntityResolver().match(candidate, identities)
            if decision.kind == "match" and decision.matched_identity:
                identity_key = decision.matched_identity.identity_key
            else:
                identity_key = EntityResolver.new_identity_key(
                    candidate,
                    snapshot.source_id,
                    ambiguous_snapshot_id=snapshot.id if decision.kind == "ambiguous" else None,
                )
                identities.append(
                    EntityIdentity(
                        name=observation.entity_key,
                        source_host=source_host,
                        identity_key=identity_key,
                    )
                )
            grouped.setdefault((identity_key, observation.entity_key), []).append(observation)

        existing_for_run = self.repository.observations_for_run(run_id)
        for (identity_key, entity_name), candidates in grouped.items():
            existing = [item for item in existing_for_run if item.entity_key == identity_key]
            evaluation = ResultEvaluator().evaluate(spec, entity_name, existing + candidates)
            target = self._target_count(spec)
            already_confirmed = any(
                item.entity_key == identity_key and item.status == ObservationStatus.CONFIRMED
                for item in existing
            )
            if (
                target is not None
                and evaluation.official_count_eligible
                and not already_confirmed
                and self._confirmed_entity_count(run_id) >= target
            ):
                continue
            for candidate in candidates:
                status = ObservationStatus(evaluation.status)
                saved = candidate.model_copy(
                    update={
                        "status": status,
                        "metadata": {
                            **candidate.metadata,
                            "evaluation_reasons": evaluation.reasons,
                            "official_count_eligible": evaluation.official_count_eligible,
                        },
                    }
                )
                key = ":".join(
                    [
                        run_id,
                        snapshot.id,
                        identity_key.casefold(),
                        candidate.field_name.casefold(),
                        candidate.raw_value or "",
                    ]
                )
                self.repository.upsert_observation(
                    self.repository.get_run(run_id).task_id,
                    run_id,
                    saved,
                    hashlib.sha256(key.encode("utf-8")).hexdigest(),
                    identity_key=identity_key,
                )
                self._event(
                    run_id,
                    "result",
                    f"新增观察值：{entity_name} · {candidate.field_name}",
                    {
                        "entity": entity_name,
                        "field": candidate.field_name,
                        "status": status.value,
                        "value": candidate.raw_value,
                    },
                )

    def _entity_identities_for_task(self, task_id: str) -> list[EntityIdentity]:
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT DISTINCT e.identity_key, e.canonical_name, "
                "COALESCE(s.canonical_url, s.url) AS source_url "
                "FROM entities e JOIN observations o ON o.entity_id=e.id "
                "LEFT JOIN observation_evidence oe ON oe.observation_id=o.id "
                "LEFT JOIN source_snapshots ss ON ss.id=oe.snapshot_id "
                "LEFT JOIN sources s ON s.id=ss.source_id "
                "WHERE e.task_id=?",
                (task_id,),
            ).fetchall()
        return [
            EntityIdentity(
                name=row["canonical_name"],
                source_host=urlsplit(row["source_url"] or "").hostname,
                identity_key=row["identity_key"],
            )
            for row in rows
        ]

    def _source_host(self, source_id: str) -> str | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT COALESCE(canonical_url, url) AS source_url FROM sources WHERE id=?",
                (source_id,),
            ).fetchone()
        return urlsplit(row["source_url"] or "").hostname if row else None

    def _source_url(self, source_id: str) -> str:
        with self.database.connection() as connection:
            row = connection.execute("SELECT url FROM sources WHERE id=?", (source_id,)).fetchone()
        return row["url"] if row else ""

    def _can_continue(self, run_id: str) -> bool:
        current = self.repository.get_run(run_id)
        if current.status != RunStatus.RUNNING:
            return False
        if self.control.elapsed_active_seconds(run_id) >= self.active_time_limit_seconds:
            self.control.finish(run_id, RunStatus.PARTIAL, "单轮主动运行达到 15 分钟上限")
            self._event(run_id, "finished", "已达到单轮主动运行时间上限")
            return False
        return True

    def _finish_budget(self, run_id: str, error: BudgetExceeded, had_results: bool) -> None:
        status = RunStatus.PARTIAL if had_results else RunStatus.BLOCKED
        self._event(
            run_id,
            "budget",
            f"{error.resource} 达到额度上限，本轮已安全停止",
            {
                "resource": error.resource,
                "scope": error.scope,
                "used": error.used,
                "reserved": error.reserved,
                "requested": error.requested,
                "limit": error.limit,
            },
        )
        self.control.finish(run_id, status, f"{error.resource} 额度不足")

    def _event(
        self,
        run_id: str,
        kind: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        stable = f"{kind}:{message}:{payload or {}}"
        key = hashlib.sha256(f"{run_id}:{stable}".encode()).hexdigest()
        self.repository.append_event(run_id, kind, message, key, payload)


class _BudgetedModelProvider:
    def __init__(self, provider: ModelProvider, ledger: BudgetLedger, run_id: str) -> None:
        self.provider = provider
        self.ledger = ledger
        self.run_id = run_id

    def generate_json(
        self, schema_name: str, schema: dict[str, Any], input_text: str, budget: ModelCallBudget
    ):
        calls = self.ledger.reserve(self.run_id, "model_calls", 1)
        estimated_input = min(60_000, max(5_000, len(input_text) * 2 + 2_000))
        input_reservation = None
        output_reservation = None
        try:
            input_reservation = self.ledger.reserve(self.run_id, "input_tokens", estimated_input)
            output_reservation = self.ledger.reserve(
                self.run_id, "output_tokens", min(4_000, budget.max_output_tokens)
            )
        except Exception:
            self.ledger.release(calls.id)
            if input_reservation:
                self.ledger.release(input_reservation.id)
            raise
        try:
            result = self.provider.generate_json(schema_name, schema, input_text, budget)
        except Exception:
            self.ledger.settle(calls.id, None)
            self.ledger.settle(input_reservation.id, None)
            self.ledger.settle(output_reservation.id, None)
            raise
        self.ledger.settle(calls.id, 1)
        self.ledger.settle(
            input_reservation.id,
            result.input_tokens if result.input_tokens > 0 else None,
        )
        self.ledger.settle(
            output_reservation.id,
            result.output_tokens if result.output_tokens > 0 else None,
        )
        return result
