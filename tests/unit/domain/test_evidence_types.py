import pytest
from pydantic import ValidationError

from info_assistant.domain.models import EvidenceRef


def test_evidence_ref_requires_quote_matching_span_length() -> None:
    with pytest.raises(ValidationError):
        EvidenceRef(snapshot_id="snapshot-1", start=4, end=10, quote="only five")


def test_evidence_ref_rejects_negative_or_empty_span() -> None:
    with pytest.raises(ValidationError):
        EvidenceRef(snapshot_id="snapshot-1", start=-1, end=0, quote="")


def test_evidence_ref_accepts_exact_span_metadata() -> None:
    evidence = EvidenceRef(
        snapshot_id="snapshot-1",
        start=10,
        end=16,
        quote="¥99/月起",
        page_number=2,
    )

    assert evidence.end - evidence.start == len(evidence.quote)
    assert evidence.page_number == 2
