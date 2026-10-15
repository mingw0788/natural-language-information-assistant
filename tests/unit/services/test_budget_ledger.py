from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

from info_assistant.domain.enums import TaskKind
from info_assistant.domain.models import TaskSpec
from info_assistant.services.budget_ledger import BudgetExceeded, BudgetLedger
from info_assistant.storage.database import Database
from info_assistant.storage.repository import Repository


def make_run(database: Database) -> str:
    repository = Repository(database)
    task = repository.create_task(TaskSpec(raw_input="查资料", kind=TaskKind.FACT))
    return repository.create_run(task.id, task.latest_version.id).id


def test_reservation_counts_in_flight_work_against_run_and_daily_limits(tmp_path) -> None:
    database = Database(tmp_path / "assistant.sqlite3")
    database.migrate()
    ledger = BudgetLedger(database)
    run_id = make_run(database)
    now = datetime(2026, 10, 15, 1, tzinfo=UTC)

    first = ledger.reserve(run_id, "document_fetch", 90, now)
    with pytest.raises(BudgetExceeded):
        ledger.reserve(run_id, "document_fetch", 11, now)

    ledger.settle(first.id, 80)
    snapshot = ledger.snapshot(run_id, now)

    assert snapshot.used["document_fetch"] == 80
    assert snapshot.reserved["document_fetch"] == 0


def test_unknown_call_is_charged_at_reserved_maximum_and_release_is_explicit(tmp_path) -> None:
    database = Database(tmp_path / "assistant.sqlite3")
    database.migrate()
    ledger = BudgetLedger(database)
    run_id = make_run(database)
    now = datetime(2026, 10, 15, 1, tzinfo=UTC)

    uncertain = ledger.reserve(run_id, "model_calls", 1, now)
    ledger.settle(uncertain.id, None)
    releasable = ledger.reserve(run_id, "search_calls", 1, now)
    ledger.release(releasable.id)
    snapshot = ledger.snapshot(run_id, now)

    assert snapshot.used["model_calls"] == 1
    assert snapshot.reserved["search_calls"] == 0


def test_concurrent_reservations_cannot_exceed_last_daily_unit(tmp_path) -> None:
    database = Database(tmp_path / "assistant.sqlite3")
    database.migrate()
    ledger = BudgetLedger(database)
    run_ids = [make_run(database) for _ in range(6)]
    now = datetime(2026, 10, 15, 1, tzinfo=UTC)

    def reserve(run_id: str) -> bool:
        try:
            ledger.reserve(run_id, "search_calls", 12, now)
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=6) as executor:
        outcomes = list(executor.map(reserve, run_ids))

    assert sorted(outcomes) == [False, True, True, True, True, True]
