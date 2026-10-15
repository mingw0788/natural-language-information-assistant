from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class EntityIdentity:
    name: str
    organization: str | None = None
    location: str | None = None
    variant: str | None = None
    identifier: str | None = None
    source_host: str | None = None
    identity_key: str | None = None


@dataclass(frozen=True)
class MatchDecision:
    kind: str
    matched_identity: EntityIdentity | None
    reason: str


class EntityResolver:
    _COMPANY_SUFFIXES = (
        "incorporated",
        "corporation",
        "company",
        "limited",
        "inc",
        "corp",
        "ltd",
        "llc",
        "gmbh",
    )

    def match(self, candidate: EntityIdentity, existing: list[EntityIdentity]) -> MatchDecision:
        same_name = [
            item
            for item in existing
            if self._normalize(item.name) == self._normalize(candidate.name)
        ]
        if not same_name:
            return MatchDecision("distinct", None, "未发现相同规范名称")
        plausible: list[EntityIdentity] = []
        conflicting_identity = False
        for item in same_name:
            if self._conflict(candidate.organization, item.organization):
                conflicting_identity = True
                continue
            if self._conflict(candidate.location, item.location):
                conflicting_identity = True
                continue
            if self._conflict(candidate.variant, item.variant):
                conflicting_identity = True
                continue
            if candidate.identifier and item.identifier:
                if candidate.identifier == item.identifier:
                    plausible.append(item)
                continue
            if candidate.source_host and item.source_host:
                if candidate.source_host.casefold() == item.source_host.casefold():
                    plausible.append(item)
                continue
            if candidate.organization and item.organization:
                if self._normalize(candidate.organization) == self._normalize(item.organization):
                    plausible.append(item)
                continue
            if candidate.location and item.location:
                if self._normalize(candidate.location) == self._normalize(item.location):
                    plausible.append(item)
        if len(plausible) == 1:
            return MatchDecision("match", plausible[0], "名称和可用身份信息一致")
        if len(plausible) > 1 or same_name:
            if conflicting_identity and not plausible and len(same_name) == 1:
                return MatchDecision("distinct", None, "公司、地点或规格信息不一致")
            return MatchDecision("ambiguous", None, "身份信息不足，保留为分列候选")
        return MatchDecision("distinct", None, "身份信息存在冲突")

    @classmethod
    def new_identity_key(
        cls,
        candidate: EntityIdentity,
        source_id: str,
        *,
        ambiguous_snapshot_id: str | None = None,
    ) -> str:
        normalized_name = cls._normalize(candidate.name)[:200] or "unknown"
        source_scope = (candidate.source_host or f"source:{source_id}").casefold()[:255]
        parts = [normalized_name, source_scope]
        if ambiguous_snapshot_id:
            parts.append(ambiguous_snapshot_id)
        return "\x1f".join(parts)

    @classmethod
    def _normalize(cls, value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).casefold()
        normalized = re.sub(r"[^\w\u4e00-\u9fff]+", " ", normalized)
        tokens = normalized.split()
        while tokens and tokens[-1] in cls._COMPANY_SUFFIXES:
            tokens.pop()
        return " ".join(tokens)

    @classmethod
    def _conflict(cls, left: str | None, right: str | None) -> bool:
        return bool(left and right and cls._normalize(left) != cls._normalize(right))
