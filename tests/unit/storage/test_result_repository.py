from info_assistant.domain.enums import ObservationStatus, TaskKind, ValueOrigin
from info_assistant.domain.models import Observation, SourceRecord, SourceSnapshot, TaskSpec
from info_assistant.storage.database import Database
from info_assistant.storage.repository import Repository


def test_snapshot_and_observation_write_are_idempotent(tmp_path) -> None:
    repository = Repository(Database(tmp_path / "assistant.sqlite3"))
    repository.database.migrate()
    task = repository.create_task(TaskSpec(raw_input="查产品价格", kind=TaskKind.FACT))
    run = repository.create_run(task.id, task.latest_version.id)
    source = SourceRecord(url="https://example.com", canonical_url="https://example.com")
    repository.save_source(source)
    snapshot = SourceSnapshot(
        source_id=source.id,
        run_id=run.id,
        text="价格为 ¥99/月。",
        content_type="text/html",
    )
    repository.save_snapshot(snapshot)
    observation = Observation(
        entity_key="example-product",
        field_name="price",
        raw_value="¥99/月",
        normalized_value="99 CNY/month",
        origin=ValueOrigin.SOURCE,
        status=ObservationStatus.PENDING_REVIEW,
    )

    saved = repository.upsert_observation(task.id, run.id, observation, "price-1")
    repeated = repository.upsert_observation(task.id, run.id, observation, "price-1")

    assert saved.id == repeated.id
    assert repository.observations_for_task(task.id)[0].raw_value == "¥99/月"
