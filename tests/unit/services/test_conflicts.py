from info_assistant.domain.enums import ObservationStatus, ValueOrigin
from info_assistant.domain.models import EvidenceRef, Observation
from info_assistant.services.conflicts import ConflictService


def make_observation(value: str, quote: str, snapshot: str) -> Observation:
    return Observation(
        entity_key="Tool A",
        field_name="price",
        raw_value=value,
        normalized_value=value,
        origin=ValueOrigin.SOURCE,
        status=ObservationStatus.CONFIRMED,
        evidence=[EvidenceRef(snapshot_id=snapshot, start=0, end=len(quote), quote=quote)],
    )


def test_conflict_keeps_both_values_and_collapses_syndicated_quote_support() -> None:
    observations = [
        make_observation("$12/month", "$12/month", "snapshot-a"),
        make_observation("$12/month", "$12/month", "snapshot-b"),
        make_observation("$15/month", "$15/month", "snapshot-c"),
    ]
    sources = {
        "snapshot-a": "vendor.example",
        "snapshot-b": "news.example",
        "snapshot-c": "vendor.example",
    }

    groups = ConflictService().group(observations, sources)

    assert len(groups) == 1
    assert {value.value for value in groups[0].values} == {"$12/month", "$15/month"}
    syndicated = next(value for value in groups[0].values if value.value == "$12/month")
    assert len(syndicated.observations) == 2
    assert syndicated.independent_source_count == 1
