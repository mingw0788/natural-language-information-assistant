from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from info_assistant.domain.models import BudgetSnapshot
from info_assistant.storage.database import Database

LIMITS: dict[str, tuple[int, int]] = {
    "active_run": (1, 20),
    "document_fetch": (100, 500),
    "browser_fetch": (20, 100),
    "search_calls": (12, 60),
    "model_calls": (40, 200),
    "input_tokens": (120_000, 600_000),
    "output_tokens": (20_000, 100_000),
}
SHANGHAI = ZoneInfo("Asia/Shanghai")


class BudgetExceeded(Exception):
    def __init__(
        self,
        resource: str,
        scope: str,
        used: int,
        reserved: int,
        requested: int,
        limit: int,
    ):
        self.resource = resource
        self.scope = scope
        self.used = used
        self.reserved = reserved
        self.requested = requested
        self.limit = limit
        super().__init__(
            f"{resource} {scope} limit {limit} would be exceeded "
            f"({used} used + {reserved} reserved + {requested} requested)"
        )


@dataclass(frozen=True)
class Reservation:
    id: str
    run_id: str
    resource: str
    amount: int
    local_day: str


class BudgetLedger:
    def __init__(self, database: Database) -> None:
        self.database = database

    def reserve(
        self, run_id: str, resource: str, maximum: int, now: datetime | None = None
    ) -> Reservation:
        current = now or datetime.now(UTC)
        day = current.astimezone(SHANGHAI).date().isoformat()
        with self.database.transaction() as connection:
            return self.reserve_in(connection, run_id, resource, maximum, day, current)

    def reserve_in(self, connection, run_id: str, resource: str, maximum: int,
                   local_day: str, now: datetime) -> Reservation:
        if resource not in LIMITS:
            raise ValueError(f"unknown budget resource: {resource}")
        if maximum <= 0:
            raise ValueError("reservation amount must be positive")
        run_limit, daily_limit = LIMITS[resource]
        self._check_scope(connection, run_id, resource, local_day, maximum, "run", run_limit)
        self._check_scope(connection, run_id, resource, local_day, maximum, "day", daily_limit)
        reservation = Reservation(str(uuid4()), run_id, resource, maximum, local_day)
        stamp = now.astimezone(UTC).isoformat()
        connection.execute(
            "INSERT INTO budget_reservations(id, run_id, local_day, resource, amount, state, "
            "actual, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'reserved', NULL, ?, ?)",
            (
                reservation.id,
                run_id,
                local_day,
                resource,
                maximum,
                stamp,
                stamp,
            ),
        )
        return reservation

    @staticmethod
    def _check_scope(connection, run_id, resource, local_day, requested, scope, limit) -> None:
        where = "run_id=?" if scope == "run" else "local_day=?"
        value = run_id if scope == "run" else local_day
        totals = connection.execute(
            "SELECT COALESCE(SUM(CASE WHEN state='settled' THEN actual ELSE 0 END), 0) used, "
            "COALESCE(SUM(CASE WHEN state='reserved' THEN amount ELSE 0 END), 0) reserved "
            f"FROM budget_reservations WHERE resource=? AND {where}",
            (resource, value),
        ).fetchone()
        if totals["used"] + totals["reserved"] + requested > limit:
            raise BudgetExceeded(
                resource,
                scope,
                totals["used"],
                totals["reserved"],
                requested,
                limit,
            )

    def settle(self, reservation_id: str, actual: int | None) -> None:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT amount, state FROM budget_reservations WHERE id=?", (reservation_id,)
            ).fetchone()
            if row is None:
                raise KeyError(reservation_id)
            if row["state"] != "reserved":
                return
            value = row["amount"] if actual is None else max(0, actual)
            connection.execute(
                "UPDATE budget_reservations SET state='settled', actual=?, updated_at=? WHERE id=?",
                (value, datetime.now(UTC).isoformat(), reservation_id),
            )

    def release(self, reservation_id: str) -> None:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT state FROM budget_reservations WHERE id=?", (reservation_id,)
            ).fetchone()
            if row is None:
                raise KeyError(reservation_id)
            if row["state"] == "reserved":
                connection.execute(
                    "UPDATE budget_reservations SET state='released', actual=0, updated_at=? "
                    "WHERE id=?",
                    (datetime.now(UTC).isoformat(), reservation_id),
                )

    def snapshot(self, run_id: str, now: datetime | None = None) -> BudgetSnapshot:
        current = now or datetime.now(UTC)
        day = current.astimezone(SHANGHAI).date().isoformat()
        used: dict[str, int] = {}
        reserved: dict[str, int] = {}
        limits: dict[str, int] = {}
        with self.database.connection() as connection:
            for resource, (run_limit, day_limit) in LIMITS.items():
                for prefix, where, value, limit in (
                    ("", "run_id=?", run_id, run_limit),
                    ("daily:", "local_day=?", day, day_limit),
                ):
                    row = connection.execute(
                        "SELECT COALESCE(SUM(CASE WHEN state='settled' "
                        "THEN actual ELSE 0 END), 0) used, "
                        "COALESCE(SUM(CASE WHEN state='reserved' "
                        "THEN amount ELSE 0 END), 0) reserved "
                        f"FROM budget_reservations WHERE resource=? AND {where}",
                        (resource, value),
                    ).fetchone()
                    used[prefix + resource] = row["used"]
                    reserved[prefix + resource] = row["reserved"]
                    limits[prefix + resource] = limit
        return BudgetSnapshot(
            run_id=run_id,
            local_day=day,
            used=used,
            reserved=reserved,
            limits=limits,
        )

    def settle_uncertain_for_run(self, connection, run_id: str, now: datetime) -> None:
        connection.execute(
            "UPDATE budget_reservations SET state='settled', actual=amount, updated_at=? "
            "WHERE run_id=? AND state='reserved'",
            (now.astimezone(UTC).isoformat(), run_id),
        )
