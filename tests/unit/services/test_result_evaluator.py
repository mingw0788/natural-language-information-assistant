from info_assistant.domain.enums import ObservationStatus, TaskKind, ValueOrigin
from info_assistant.domain.models import (
    Condition,
    EvidenceRef,
    Observation,
    TaskField,
    TaskSpec,
)
from info_assistant.services.result_evaluator import ResultEvaluator, compare_observations


def observation(field: str, value: str | None, quote: str | None = None) -> Observation:
    evidence = []
    if quote:
        evidence = [EvidenceRef(snapshot_id=f"s-{field}", start=0, end=len(quote), quote=quote)]
    return Observation(
        entity_key="Tool A",
        field_name=field,
        raw_value=value,
        normalized_value=value,
        origin=ValueOrigin.SOURCE,
        status=ObservationStatus.CONFIRMED if evidence else ObservationStatus.PENDING_REVIEW,
        evidence=evidence,
    )


def test_optional_field_missing_keeps_entity_but_required_unknown_is_pending() -> None:
    spec = TaskSpec(
        raw_input="列出工具",
        kind=TaskKind.LIST,
        fields=[TaskField(name="website")],
    )
    evaluator = ResultEvaluator()

    official = evaluator.evaluate(
        spec, "Tool A", [observation("website", "https://a.test", "a.test")]
    )
    required_spec = spec.model_copy(
        update={"fields": [*spec.fields, TaskField(name="license", required=True)]}
    )
    pending = evaluator.evaluate(
        required_spec, "Tool B", [observation("website", "https://b.test", "b.test")]
    )

    assert official.status == "confirmed"
    assert official.official_count_eligible is True
    assert pending.status == "pending_review"
    assert "license" in " ".join(pending.reasons)


def test_required_free_condition_uses_evidence_and_explicit_negation() -> None:
    spec = TaskSpec(
        raw_input="只要免费的工具",
        kind=TaskKind.LIST,
        required_conditions=[Condition(text="免费", origin="user")],
    )

    included = ResultEvaluator().evaluate(
        spec, "Tool A", [observation("price", "Free", "Free tier")]
    )
    excluded = ResultEvaluator().evaluate(
        spec, "Tool B", [observation("price", "Not free", "Not free")]
    )

    assert included.status == "confirmed"
    assert excluded.status == "excluded"


def test_different_currency_or_period_is_not_ranked_as_comparable() -> None:
    first = observation("price", "10 USD/month", "10 USD/month")
    second = observation("price", "80 CNY/year", "80 CNY/year")

    comparison = compare_observations([first, second])

    assert comparison.comparable is False
    assert "币种" in comparison.reason
