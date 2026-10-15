from datetime import UTC, datetime

import pytest

from info_assistant.domain.enums import RunStatus, TaskKind
from info_assistant.domain.models import TaskSpec
from info_assistant.services.budget_ledger import BudgetLedger
from info_assistant.services.run_control import ActiveRunExists, RunControl
from info_assistant.storage.database import Database
from info_assistant.storage.repository import Repository


def setup_control(tmp_path):
    database = Database(tmp_path / "assistant.sqlite3")
    database.migrate()
    repository = Repository(database)
    ledger = BudgetLedger(database)
    control = RunControl(database, repository, ledger)
    task = repository.create_task(TaskSpec(raw_input="查资料", kind=TaskKind.FACT))
    return database, repository, ledger, control, task


def test_only_one_run_can_be_active_and_pause_resume_persist(tmp_path) -> None:
    _, repository, _, control, task = setup_control(tmp_path)
    first = control.enqueue(task.id, task.latest_version.id)
    second = control.enqueue(task.id, task.latest_version.id)

    started = control.start(first.id, datetime(2026, 10, 15, tzinfo=UTC))
    with pytest.raises(ActiveRunExists):
        control.start(second.id, datetime(2026, 10, 15, tzinfo=UTC))
    paused = control.pause(first.id)
    resumed = control.resume(first.id)

    assert started.status == RunStatus.RUNNING
    assert paused.status == RunStatus.PAUSED
    assert resumed.status == RunStatus.QUEUED
    assert repository.get_run(first.id).status == RunStatus.QUEUED


def test_restart_marks_running_run_interrupted_and_keeps_its_identity(tmp_path) -> None:
    _, repository, ledger, control, task = setup_control(tmp_path)
    run = control.enqueue(task.id, task.latest_version.id)
    control.start(run.id, datetime(2026, 10, 15, tzinfo=UTC))
    pending = ledger.reserve(run.id, "model_calls", 1, datetime(2026, 10, 15, tzinfo=UTC))

    recovered = control.recover_after_restart(datetime(2026, 10, 16, tzinfo=UTC))
    snapshot = ledger.snapshot(run.id, datetime(2026, 10, 16, tzinfo=UTC))

    assert recovered == [run.id]
    assert repository.get_run(run.id).status == RunStatus.INTERRUPTED
    assert snapshot.used.get("model_calls", 0) == 1
    assert pending.id
