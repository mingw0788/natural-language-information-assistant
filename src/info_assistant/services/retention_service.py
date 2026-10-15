from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from info_assistant.domain.enums import ObservationStatus
from info_assistant.storage.database import Database


@dataclass(frozen=True)
class PurgeReport:
    snapshots_deleted: int
    observations_deleted: int
    sources_deleted: int


@dataclass(frozen=True)
class DeleteReport:
    task_id: str
    snapshots_deleted: int
    tasks_deleted: int
    observations_preserved: int


class RetentionService:
    def __init__(self, database: Database, source_days: int = 30, result_days: int = 180) -> None:
        self.database = database
        self.source_days = source_days
        self.result_days = result_days

    def purge_expired(self, now: datetime | None = None) -> PurgeReport:
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("retention clock must include a timezone")
        source_cutoff = (current - timedelta(days=self.source_days)).astimezone(UTC).isoformat()
        result_cutoff = (current - timedelta(days=self.result_days)).astimezone(UTC).isoformat()
        with self.database.transaction() as connection:
            snapshot_count = connection.execute(
                "SELECT COUNT(*) FROM source_snapshots WHERE fetched_at < ?", (source_cutoff,)
            ).fetchone()[0]
            connection.execute(
                "DELETE FROM source_snapshots WHERE fetched_at < ?", (source_cutoff,)
            )
            self._mark_missing_evidence(connection, current)
            observation_count = connection.execute(
                "SELECT COUNT(*) FROM observations WHERE observed_at < ?", (result_cutoff,)
            ).fetchone()[0]
            connection.execute("DELETE FROM observations WHERE observed_at < ?", (result_cutoff,))
            source_count = connection.execute(
                "SELECT COUNT(*) FROM sources s WHERE NOT EXISTS "
                "(SELECT 1 FROM source_snapshots ss WHERE ss.source_id=s.id) "
                "AND s.last_checked_at IS NOT NULL AND s.last_checked_at < ?",
                (source_cutoff,),
            ).fetchone()[0]
            connection.execute(
                "DELETE FROM sources WHERE NOT EXISTS "
                "(SELECT 1 FROM source_snapshots ss WHERE ss.source_id=sources.id) "
                "AND last_checked_at IS NOT NULL AND last_checked_at < ?",
                (source_cutoff,),
            )
        return PurgeReport(snapshot_count, observation_count, source_count)

    def delete_task_material(self, task_id: str, keep_derived_results: bool) -> DeleteReport:
        with self.database.transaction() as connection:
            task = connection.execute("SELECT id FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise KeyError(task_id)
            run_ids = [
                row["id"]
                for row in connection.execute("SELECT id FROM runs WHERE task_id=?", (task_id,))
            ]
            placeholders = ",".join("?" for _ in run_ids)
            if run_ids:
                snapshot_count = connection.execute(
                    f"SELECT COUNT(*) FROM source_snapshots WHERE run_id IN ({placeholders})",
                    run_ids,
                ).fetchone()[0]
            else:
                snapshot_count = 0
            if keep_derived_results:
                source_ids: list[str] = []
                if run_ids:
                    source_ids = [
                        row["source_id"]
                        for row in connection.execute(
                            "SELECT DISTINCT source_id FROM source_snapshots "
                            f"WHERE run_id IN ({placeholders})",
                            run_ids,
                        )
                    ]
                    connection.execute(
                        f"DELETE FROM source_snapshots WHERE run_id IN ({placeholders})", run_ids
                    )
                self._mark_missing_evidence(connection, datetime.now(UTC))
                observation_count = connection.execute(
                    "SELECT COUNT(*) FROM observations WHERE task_id=?", (task_id,)
                ).fetchone()[0]
                for source_id in source_ids:
                    connection.execute(
                        "DELETE FROM sources WHERE id=? AND NOT EXISTS "
                        "(SELECT 1 FROM source_snapshots ss WHERE ss.source_id=sources.id)",
                        (source_id,),
                    )
                return DeleteReport(task_id, snapshot_count, 0, observation_count)

            source_ids: list[str] = []
            if run_ids:
                source_ids = [
                    row["source_id"]
                    for row in connection.execute(
                        "SELECT DISTINCT source_id FROM source_snapshots "
                        f"WHERE run_id IN ({placeholders})",
                        run_ids,
                    )
                ]
            connection.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            for source_id in source_ids:
                connection.execute(
                    "DELETE FROM sources WHERE id=? AND NOT EXISTS "
                    "(SELECT 1 FROM source_snapshots ss WHERE ss.source_id=sources.id)",
                    (source_id,),
                )
            return DeleteReport(task_id, snapshot_count, 1, 0)

    @staticmethod
    def _mark_missing_evidence(connection, now: datetime) -> None:
        rows = connection.execute(
            "SELECT DISTINCT o.id, o.metadata_json FROM observations o "
            "JOIN observation_evidence oe ON oe.observation_id=o.id "
            "WHERE oe.snapshot_id IS NULL AND o.origin='source' "
            "AND o.status IN ('confirmed', 'user_confirmed', 'stale') "
            "AND NOT EXISTS (SELECT 1 FROM observation_evidence oe2 "
            "WHERE oe2.observation_id=o.id AND oe2.snapshot_id IS NOT NULL)"
        ).fetchall()
        for row in rows:
            metadata = json.loads(row["metadata_json"] or "{}")
            metadata["evidence_retention"] = "来源快照已清理；保留摘录但无法重新定位原文"
            connection.execute(
                "UPDATE observations SET status=?, metadata_json=? WHERE id=?",
                (
                    ObservationStatus.EVIDENCE_MISSING.value,
                    json.dumps(metadata, ensure_ascii=False),
                    row["id"],
                ),
            )
