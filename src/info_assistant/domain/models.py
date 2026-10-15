from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from info_assistant.domain.enums import (
    ObservationStatus,
    RunStatus,
    SourceStatus,
    TaskKind,
    ValueOrigin,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Condition(StrictModel):
    text: str = Field(min_length=1, max_length=500)
    origin: str = Field(default="user", pattern="^(user|assumption|inference)$")


class TaskField(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    required: bool = False


class TimeWindow(StrictModel):
    start: datetime
    end: datetime
    label: str = ""

    @model_validator(mode="after")
    def validate_range(self) -> TimeWindow:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("time window timestamps must include a timezone")
        if self.end <= self.start:
            raise ValueError("time window end must be after start")
        return self


class SourceInput(StrictModel):
    kind: str = Field(pattern="^(url|text)$")
    value: str = Field(min_length=1, max_length=20_000)
    label: str = ""


class TaskSpec(StrictModel):
    raw_input: str = Field(min_length=1, max_length=10_000)
    kind: TaskKind = TaskKind.LIST
    summary: str = Field(default="", max_length=1_000)
    fields: list[TaskField] = Field(default_factory=list, max_length=50)
    required_conditions: list[Condition] = Field(default_factory=list, max_length=50)
    preferences: list[Condition] = Field(default_factory=list, max_length=50)
    source_constraints: list[str] = Field(default_factory=list, max_length=50)
    sources: list[SourceInput] = Field(default_factory=list, max_length=100)
    pasted_material: list[str] = Field(default_factory=list, max_length=50)
    region: str | None = Field(default=None, max_length=200)
    time_window: TimeWindow | None = None
    assumptions: list[str] = Field(default_factory=list, max_length=50)
    stop_conditions: list[str] = Field(default_factory=list, max_length=50)
    display_limit: int = Field(default=10, ge=1, le=100)


class TaskVersion(StrictModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    number: int = Field(ge=1)
    spec: TaskSpec
    created_at: datetime = Field(default_factory=utc_now)


class TaskRecord(StrictModel):
    id: str
    created_at: datetime
    updated_at: datetime
    latest_version: TaskVersion
    latest_run_status: RunStatus | None = None


class TaskPage(StrictModel):
    items: list[TaskRecord]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class RunRecord(StrictModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    version_id: str
    status: RunStatus = RunStatus.QUEUED
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    active_seconds: int = Field(default=0, ge=0)
    stop_reason: str | None = None


class SourceRecord(StrictModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    url: str
    canonical_url: str
    title: str = ""
    status: SourceStatus = SourceStatus.PENDING
    last_checked_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None


class SourceSnapshot(StrictModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    source_id: str
    run_id: str
    fetched_at: datetime = Field(default_factory=utc_now)
    content_type: str = "text/html"
    text: str = Field(max_length=2_000_000)
    content_hash: str = ""
    http_status: int | None = Field(default=None, ge=100, le=599)
    page_count: int | None = Field(default=None, ge=1)


class EvidenceRef(StrictModel):
    snapshot_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=2_000)
    page_number: int | None = Field(default=None, ge=1)
    locator: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_span(self) -> EvidenceRef:
        if self.end <= self.start:
            raise ValueError("evidence span must not be empty")
        if self.end - self.start != len(self.quote):
            raise ValueError("evidence quote length must match its character span")
        return self


class EntityRecord(StrictModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    canonical_name: str = Field(min_length=1, max_length=500)
    identity_key: str = Field(min_length=1, max_length=500)
    aliases: list[str] = Field(default_factory=list, max_length=100)
    created_at: datetime = Field(default_factory=utc_now)


class Observation(StrictModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    entity_key: str = Field(min_length=1, max_length=500)
    field_name: str = Field(min_length=1, max_length=120)
    raw_value: str | None = Field(default=None, max_length=10_000)
    normalized_value: str | None = Field(default=None, max_length=10_000)
    unit: str | None = Field(default=None, max_length=100)
    period: str | None = Field(default=None, max_length=120)
    qualifiers: list[str] = Field(default_factory=list, max_length=50)
    origin: ValueOrigin
    status: ObservationStatus = ObservationStatus.PENDING_REVIEW
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=30)
    confidence: float | None = Field(default=None, ge=0, le=1)
    observed_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_confirmation(self) -> Observation:
        if (
            self.origin == ValueOrigin.SOURCE
            and self.status in {ObservationStatus.CONFIRMED, ObservationStatus.USER_CONFIRMED}
            and not self.evidence
        ):
            raise ValueError("confirmed source observations require evidence")
        return self


class BudgetSnapshot(StrictModel):
    run_id: str
    local_day: str
    used: dict[str, int] = Field(default_factory=dict)
    reserved: dict[str, int] = Field(default_factory=dict)
    limits: dict[str, int] = Field(default_factory=dict)


class CapabilityReport(StrictModel):
    ready: bool
    available: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    data_flow: list[str] = Field(default_factory=list)
    local_fallbacks: list[str] = Field(default_factory=list)


class RunEvent(StrictModel):
    id: int | None = None
    run_id: str
    kind: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=2_000)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
