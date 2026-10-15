from info_assistant.domain.models import EvidenceRef
from info_assistant.services.evidence_validator import EvidenceValidator


def test_evidence_validator_requires_exact_snapshot_substring() -> None:
    validator = EvidenceValidator()
    good = EvidenceRef(snapshot_id="s", start=7, end=13, quote="$12/mo")
    bad = EvidenceRef(snapshot_id="s", start=7, end=13, quote="price!")

    assert validator.validate("Price: $12/mo", good).valid is True
    assert validator.validate("Price: $12/mo", bad).valid is False
