from enum import StrEnum


class TaskKind(StrEnum):
    FACT = "fact"
    LIST = "list"
    COMPARISON = "comparison"
    RESEARCH = "research"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class ObservationStatus(StrEnum):
    CONFIRMED = "confirmed"
    USER_CONFIRMED = "user_confirmed"
    PENDING_REVIEW = "pending_review"
    EXCLUDED = "excluded"
    STALE = "stale"
    EVIDENCE_MISSING = "evidence_missing"


class ValueOrigin(StrEnum):
    USER_INPUT = "user_input"
    ASSUMPTION = "assumption"
    SOURCE = "source"
    INFERENCE = "inference"
    USER_CORRECTION = "user_correction"


class SourceStatus(StrEnum):
    PENDING = "pending"
    FETCHED = "fetched"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"
    ROBOTS_BLOCKED = "robots_blocked"
    BUDGET_EXCEEDED = "budget_exceeded"
    NOT_FETCHED = "not_fetched"
