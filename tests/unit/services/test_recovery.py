from datetime import UTC, datetime

from info_assistant.domain.models import TaskSpec
from info_assistant.services.budget_ledger import BudgetLedger
from info_assistant.services.progress_events import EventReader
from info_assistant.services.recovery import RecoveryService
from info_assistant.services.run_control import RunControl
from info_assistant.storage.database import Database
from info_assistant.storage.repository import Repository


def test_recovery_preserves_progress_and_does_not_redispatch_uncertain_source(tmp_path) -> None:
    database = Database(tmp_path / "recovery.sqlite3")
    database.migrate()
    repository = Repository(database)
    control = RunControl(database, repository, BudgetLedger(database))
    task = repository.create_task(TaskSpec(raw_input="查资料"))
    run = control.enqueue(task.id, task.latest_version.id)
    now = datetime(2026, 10, 15, 12, tzinfo=UTC)
    control.start(run.id, now)
    repository.mark_source_state(
        run.id,
        "https://example.test/data",
        "fetching",
        next_url="https://example.test/data",
    )

    recovery = RecoveryService(database, repository, control)
    recovered = recovery.recover_after_restart(now.replace(minute=2))
    events = EventReader(repository).after(run.id, 0)

    state = repository.source_state(run.id, "https://example.test/data")
    assert recovered == [run.id]
    assert repository.get_run(run.id).status.value == "interrupted"
    assert state["status"] == "unknown"
    assert any("不会自动重发" in event.message for event in events)


def test_resume_records_event_and_keeps_same_run_identity(tmp_path) -> None:
    database = Database(tmp_path / "resume.sqlite3")
    database.migrate()
    repository = Repository(database)
    control = RunControl(database, repository, BudgetLedger(database))
    task = repository.create_task(TaskSpec(raw_input="查资料"))
    run = control.enqueue(task.id, task.latest_version.id)
    control.start(run.id)
    control.pause(run.id)

    resumed = RecoveryService(database, repository, control).resume(run.id)

    assert resumed.id == run.id
    assert resumed.status.value == "queued"
    assert EventReader(repository).after(run.id, 0)[-1].kind == "resumed"
