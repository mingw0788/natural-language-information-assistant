from datetime import UTC, datetime

from info_assistant.domain.enums import ObservationStatus, TaskKind, ValueOrigin
from info_assistant.domain.models import (
    Condition,
    EvidenceRef,
    Observation,
    SourceSnapshot,
    TaskField,
    TaskSpec,
)
from info_assistant.services.extractor import Extractor
from info_assistant.services.result_evaluator import ResultEvaluator, compare_observations
from info_assistant.services.task_planner import TaskPlanner


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


def test_recent_product_price_is_pending_when_source_publication_date_is_unknown() -> None:
    now = datetime(2026, 10, 15, 8, tzinfo=UTC)
    spec = TaskPlanner().interpret("查最近的产品价格", now=now)
    snapshot = SourceSnapshot(
        source_id="product-page",
        run_id="test-run",
        text="产品甲\n价格：10元",
        fetched_at=now,
    )
    observations = Extractor().extract(spec, snapshot)

    result = ResultEvaluator().evaluate(spec, "产品甲", observations)

    assert spec.time_window is not None
    assert spec.required_conditions == []
    assert observations[0].status == ObservationStatus.CONFIRMED
    assert result.status == "pending_review"
    assert result.official_count_eligible is False
    assert "发布日期" in " ".join(result.reasons)
    assert "时间范围" in " ".join(result.reasons)


def test_observation_timestamps_and_unverified_date_claims_do_not_satisfy_time_window() -> None:
    now = datetime(2026, 10, 15, 8, tzinfo=UTC)
    spec = TaskPlanner().interpret("查最近的产品价格", now=now)
    price = observation("价格", "10元", "10元").model_copy(
        update={
            "observed_at": now,
            "qualifiers": ["最近7天", "发布日期：2026-10-14"],
            "metadata": {"published_at": "2026-10-14", "fetched_at": now.isoformat()},
        }
    )

    result = ResultEvaluator().evaluate(spec, "产品甲", [price])

    assert result.status == "pending_review"
    assert result.official_count_eligible is False
    assert "发布日期" in " ".join(result.reasons)
