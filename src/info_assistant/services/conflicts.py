from __future__ import annotations

import hashlib
from dataclasses import dataclass

from info_assistant.domain.models import Observation


@dataclass(frozen=True)
class ConflictValue:
    value: str
    observations: list[Observation]
    independent_source_count: int


@dataclass(frozen=True)
class ConflictGroup:
    field_name: str
    values: list[ConflictValue]


class ConflictService:
    def group(
        self,
        observations: list[Observation],
        source_by_snapshot: dict[str, str] | None = None,
    ) -> list[ConflictGroup]:
        source_by_snapshot = source_by_snapshot or {}
        by_field: dict[str, list[Observation]] = {}
        for observation in observations:
            by_field.setdefault(observation.field_name, []).append(observation)
        conflicts = []
        for field_name, items in by_field.items():
            by_value: dict[str, list[Observation]] = {}
            for item in items:
                value = item.normalized_value or item.raw_value
                if value:
                    by_value.setdefault(value, []).append(item)
            if len(by_value) < 2:
                continue
            values = [
                ConflictValue(
                    value=value,
                    observations=records,
                    independent_source_count=self._independent_count(records, source_by_snapshot),
                )
                for value, records in by_value.items()
            ]
            conflicts.append(ConflictGroup(field_name=field_name, values=values))
        return conflicts

    @staticmethod
    def _independent_count(
        observations: list[Observation], source_by_snapshot: dict[str, str]
    ) -> int:
        domains: set[str] = set()
        quote_domains: dict[str, set[str]] = {}
        for observation in observations:
            for evidence in observation.evidence:
                domain = source_by_snapshot.get(evidence.snapshot_id, evidence.snapshot_id)
                domains.add(domain.casefold())
                quote_hash = hashlib.sha256(evidence.quote.encode("utf-8")).hexdigest()
                quote_domains.setdefault(quote_hash, set()).add(domain.casefold())
        if not domains:
            return 0
        syndication_hashes = [
            quote_hash
            for quote_hash, quote_sources in quote_domains.items()
            if len(quote_sources) > 1
        ]
        if syndication_hashes:
            syndicated_domains = set().union(*(quote_domains[item] for item in syndication_hashes))
            return max(len(domains - syndicated_domains), len(syndication_hashes))
        return len(domains)
