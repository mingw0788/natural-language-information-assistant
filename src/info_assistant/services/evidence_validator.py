from __future__ import annotations

from dataclasses import dataclass

from info_assistant.domain.models import EvidenceRef


@dataclass(frozen=True)
class EvidenceValidation:
    valid: bool
    reason: str | None = None


class EvidenceValidator:
    def validate(self, snapshot_text: str, evidence: EvidenceRef) -> EvidenceValidation:
        if evidence.start < 0 or evidence.end > len(snapshot_text):
            return EvidenceValidation(False, "证据位置超出已保存正文")
        actual = snapshot_text[evidence.start : evidence.end]
        if actual != evidence.quote:
            return EvidenceValidation(False, "引用内容与已保存正文不一致")
        if not actual.strip():
            return EvidenceValidation(False, "证据内容为空")
        return EvidenceValidation(True)
