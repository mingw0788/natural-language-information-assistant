from __future__ import annotations

from datetime import UTC, datetime

from info_assistant.domain.enums import RunStatus
from info_assistant.domain.models import RunRecord
from info_assistant.services.budget_ledger import SHANGHAI, BudgetLedger
from info_assistant.storage.database import Database
from info_assistant.storage.repository import Repository


class ActiveRunExists(Exception):
    pass


class InvalidRunTransition(Exception):
    pass


class RunControl:
    def __init__(self, database: Database, repository: Repository, ledger: BudgetLedger) -> None:
        self.database = database
        self.repository = repository
        self.ledger = ledger

    def enqueue(self, task_id: str, version_id: str) -> RunRecord:
        return self.repository.create_run(task_id, version_id)

    def start(self, run_id: str, now: datetime | None = None) -> RunRecord:
        current = now or datetime.now(UTC)
        day = current.astimezone(SHANGHAI).date().isoformat()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row["status"] != RunStatus.QUEUED.value:
                raise InvalidRunTransition(row["status"])
            active = connection.execute(
                "SELECT id FROM runs WHERE status IN ('running', 'pausing') LIMIT 1"
            ).fetchone()
            if active:
                raise ActiveRunExists(active["id"])
            existing_start = connection.execute(
                "SELECT id FROM budget_reservations WHERE run_id=? AND resource='active_run' "
                "AND state IN ('reserved', 'settled')",
                (run_id,),
            ).fetchone()
            if not existing_start:
                self.ledger.reserve_in(connection, run_id, "active_run", 1, day, current)
                connection.execute(
                    "UPDATE budget_reservations SET state='settled', actual=1, updated_at=? "
                    "WHERE run_id=? AND resource='active_run' AND state='reserved'",
                    (current.astimezone(UTC).isoformat(), run_id),
                )
            connection.execute(
                "UPDATE runs SET status='running', started_at=COALESCE(started_at, ?) WHERE id=?",
                (current.astimezone(UTC).isoformat(), run_id),
            )
        return self.repository.get_run(run_id)

    def pause(self, run_id: str) -> RunRecord:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row["status"] not in {RunStatus.RUNNING.value, RunStatus.PAUSING.value}:
                raise InvalidRunTransition(row["status"])
            connection.execute("UPDATE runs SET status='paused' WHERE id=?", (run_id,))
        return self.repository.get_run(run_id)

    def resume(self, run_id: str) -> RunRecord:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row["status"] not in {RunStatus.PAUSED.value, RunStatus.INTERRUPTED.value}:
                raise InvalidRunTransition(row["status"])
            connection.execute("UPDATE runs SET status='queued' WHERE id=?", (run_id,))
        return self.repository.get_run(run_id)

    def cancel(self, run_id: str, now: datetime | None = None) -> RunRecord:
        current = now or datetime.now(UTC)
        with self.database.transaction() as connection:
            row = connection.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row["status"] in {
                RunStatus.COMPLETED.value,
                RunStatus.CANCELLED.value,
                RunStatus.FAILED.value,
            }:
                raise InvalidRunTransition(row["status"])
            self.ledger.settle_uncertain_for_run(connection, run_id, current)
            connection.execute(
                "UPDATE runs SET status='cancelled', finished_at=?, "
                "stop_reason='用户取消' WHERE id=?",
                (current.astimezone(UTC).isoformat(), run_id),
            )
        return self.repository.get_run(run_id)

    def finish(self, run_id: str, status: RunStatus, stop_reason: str | None = None) -> RunRecord:
        if status not in {
            RunStatus.COMPLETED,
            RunStatus.PARTIAL,
            RunStatus.BLOCKED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            raise ValueError("finish requires a terminal run status")
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            self.ledger.settle_uncertain_for_run(connection, run_id, datetime.now(UTC))
            connection.execute(
                "UPDATE runs SET status=?, finished_at=?, stop_reason=? WHERE id=?",
                (status.value, now, stop_reason, run_id),
            )
        return self.repository.get_run(run_id)

    def recover_after_restart(self, now: datetime | None = None) -> list[str]:
        current = now or datetime.now(UTC)
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT id FROM runs WHERE status IN ('running', 'pausing')"
            ).fetchall()
            run_ids = [row["id"] for row in rows]
            for run_id in run_ids:
                self.ledger.settle_uncertain_for_run(connection, run_id, current)
                connection.execute(
                    "UPDATE runs SET status='interrupted', finished_at=?, "
                    "stop_reason='程序中断；在途调用按已使用额度保留' WHERE id=?",
                    (current.astimezone(UTC).isoformat(), run_id),
                )
            return run_ids
