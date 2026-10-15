from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class ProviderError(Exception):
    pass


class ProviderNotConfigured(ProviderError):
    pass


@dataclass(frozen=True)
class SearchQuery:
    query: str
    count: int = 10
    country: str | None = None
    language: str | None = None
    freshness: str | None = None


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    is_evidence: bool = False


@dataclass(frozen=True)
class SearchResponse:
    results: list[SearchResult]
    provider: str


@dataclass(frozen=True)
class ModelCallBudget:
    max_output_tokens: int


@dataclass(frozen=True)
class ModelResult:
    data: dict[str, Any]
    input_tokens: int
    output_tokens: int
    model: str


class SearchProvider(Protocol):
    def search(self, query: SearchQuery) -> SearchResponse: ...


class ModelProvider(Protocol):
    def generate_json(
        self,
        schema_name: str,
        schema: dict[str, Any],
        input_text: str,
        budget: ModelCallBudget,
    ) -> ModelResult: ...
