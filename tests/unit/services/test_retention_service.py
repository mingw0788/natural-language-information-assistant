from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from info_assistant.domain.enums import ObservationStatus, ValueOrigin
from info_assistant.domain.models import (
    EvidenceRef,
    Observation,
    SourceRecord,
    SourceSnapshot,
    TaskSpec,
)
from info_assistant.services.retention_service import RetentionService
from info_assistant.storage.database import Database
from info_assistant.storage.repository import Repository


def setup_material(tmp_path: Path, observed_at: datetime):
    database = Database(tmp_path / "retention.sqlite3")
    database.migrate()
    repository = Repository(database)
    task = repository.create_task(TaskSpec(raw_input="查价格"))
    run = repository.create_run(task.id, task.latest_version.id)
    source = repository.save_source(
        SourceRecord(
            url="https://example.test/pricing",
            canonical_url="https://example.test/pricing",
            title="Pricing",
        )
    )
    quote = "价格：每月 10 元"
    snapshot = repository.save_snapshot(
        SourceSnapshot(
            source_id=source.id,
            run_id=run.id,
            fetched_at=observed_at,
            text=f"Acme\n{quote}",
        )
    )
    observation = Observation(
        entity_key="Acme",
        field_name="价格",
        raw_value="每月 10 元",
        normalized_value="每月 10 元",
        origin=ValueOrigin.SOURCE,
        status=ObservationStatus.CONFIRMED,
        evidence=[
            EvidenceRef(
                snapshot_id=snapshot.id,
                start=5,
                end=5 + len(quote),
                quote=quote,
            )
        ],
        observed_at=observed_at,
    )
    repository.upsert_observation(task.id, run.id, observation, "retention-test")
    return database, repository, task, run, snapshot


def test_old_snapshots_are_removed_but_excerpt_is_retained_unverified(tmp_path: Path) -> None:
    now = datetime(2026, 10, 15, tzinfo=UTC)
    old = now - timedelta(days=31)
    database, repository, task, run, snapshot = setup_material(tmp_path, old)

    report = RetentionService(database).purge_expired(now)

    assert report.snapshots_deleted == 1
    observation = repository.observations_for_task(task.id)[0]
    assert observation.status == ObservationStatus.EVIDENCE_MISSING
    assert observation.evidence[0].snapshot_id is None
    assert observation.evidence[0].quote == "价格：每月 10 元"
    with database.connection() as connection:
        evidence = connection.execute(
            "SELECT source_url, source_title FROM observation_evidence WHERE observation_id=?",
            (observation.id,),
        ).fetchone()
        snapshot_count = connection.execute(
            "SELECT COUNT(*) FROM source_snapshots WHERE id=?", (snapshot.id,)
        ).fetchone()[0]
    assert evidence["source_url"] == "https://example.test/pricing"
    assert evidence["source_title"] == "Pricing"
    assert snapshot_count == 0


def test_expired_derived_results_are_removed_after_180_days(tmp_path: Path) -> None:
    now = datetime(2026, 10, 15, tzinfo=UTC)
    old = now - timedelta(days=181)
    database, repository, task, _run, _snapshot = setup_material(tmp_path, old)

    report = RetentionService(database).purge_expired(now)

    assert report.observations_deleted == 1
    assert repository.observations_for_task(task.id) == []


def test_explicit_task_material_deletion_can_keep_derived_quote(tmp_path: Path) -> None:
    database, repository, task, _run, _snapshot = setup_material(
        tmp_path, datetime.now(UTC)
    )

    report = RetentionService(database).delete_task_material(task.id, keep_derived_results=True)

    assert report.snapshots_deleted == 1
    assert report.tasks_deleted == 0
    retained = repository.observations_for_task(task.id)
    assert retained[0].status == ObservationStatus.EVIDENCE_MISSING
    assert retained[0].evidence[0].quote == "价格：每月 10 元"

    delete_all = RetentionService(database).delete_task_material(
        task.id, keep_derived_results=False
    )
    assert delete_all.tasks_deleted == 1
