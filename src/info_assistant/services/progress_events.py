from __future__ import annotations

from info_assistant.domain.models import RunEvent
from info_assistant.storage.repository import Repository


class EventReader:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def after(self, run_id: str, cursor: int) -> list[RunEvent]:
        return self.repository.events_after(run_id, cursor)
