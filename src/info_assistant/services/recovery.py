from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from info_assistant.domain.models import RunRecord
from info_assistant.services.run_control import RunControl
from info_assistant.storage.database import Database
from info_assistant.storage.repository import Repository


class RecoveryService:
    def __init__(
        self,
        database: Database,
        repository: Repository,
        control: RunControl,
    ) -> None:
        self.database = database
        self.repository = repository
        self.control = control

    def recover_after_restart(self, now: datetime | None = None) -> list[str]:
        current = now or datetime.now(UTC)
        run_ids = self.control.recover_after_restart(current)
        for run_id in run_ids:
            for state in self.repository.source_states_for_run(run_id):
                if state["status"] == "fetching":
                    self.repository.mark_source_state(
                        run_id,
                        state["canonical_url"],
                        "unknown",
                        next_url=state["next_url"],
                        error_code="request_result_unknown",
                        error_message="程序中断时请求结果未知；不会自动重复派发",
                    )
            self._event(
                run_id,
                "interrupted",
                "程序重启后已保存进度；中断中的请求按已使用额度保留，未知请求不会自动重发",
            )
        return run_ids

    def resume(self, run_id: str) -> RunRecord:
        run = self.control.resume(run_id)
        self._event(run_id, "resumed", "恢复同一任务版本、时间范围和额度记录")
        return run

    def _event(self, run_id: str, kind: str, message: str) -> None:
        key = hashlib.sha256(f"{run_id}:{kind}:{message}".encode()).hexdigest()
        self.repository.append_event(run_id, kind, message, key)
