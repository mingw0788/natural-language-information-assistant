from info_assistant.domain.enums import TaskKind
from info_assistant.domain.models import Condition, TaskSpec
from info_assistant.storage.database import Database
from info_assistant.storage.repository import Repository


def test_create_task_and_version_keeps_prior_spec_immutable(tmp_path) -> None:
    repository = Repository(Database(tmp_path / "assistant.sqlite3"))
    repository.database.migrate()
    first = TaskSpec(raw_input="列出免费的地图工具", kind=TaskKind.LIST)

    task = repository.create_task(first)
    second = TaskSpec(
        raw_input="列出免费的地图工具",
        kind=TaskKind.LIST,
        required_conditions=[Condition(text="开源", origin="user")],
    )
    version = repository.create_version(task.id, second)
    history = repository.list_history("地图", limit=20, offset=0)

    assert task.latest_version.number == 1
    assert version.number == 2
    assert task.latest_version.spec.required_conditions == []
    assert history.total == 1


def test_event_append_is_idempotent(tmp_path) -> None:
    repository = Repository(Database(tmp_path / "assistant.sqlite3"))
    repository.database.migrate()
    task = repository.create_task(TaskSpec(raw_input="查事实", kind=TaskKind.FACT))
    run = repository.create_run(task.id, task.latest_version.id)

    first_id = repository.append_event(run.id, "started", "开始", "event-started")
    duplicate_id = repository.append_event(run.id, "started", "开始", "event-started")

    assert first_id == duplicate_id
    assert len(repository.events_after(run.id, 0)) == 1
