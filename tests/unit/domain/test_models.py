import pytest
from pydantic import ValidationError

from info_assistant.domain.enums import ObservationStatus, TaskKind, ValueOrigin
from info_assistant.domain.models import Condition, Observation, TaskSpec


def test_task_spec_keeps_required_and_preferred_conditions_separate() -> None:
    task = TaskSpec(
        raw_input="只要免费的，优先支持中文",
        kind=TaskKind.LIST,
        required_conditions=[Condition(text="免费", origin="user")],
        preferences=[Condition(text="支持中文", origin="user")],
    )

    assert [condition.text for condition in task.required_conditions] == ["免费"]
    assert [condition.text for condition in task.preferences] == ["支持中文"]


def test_missing_value_is_not_coerced_to_zero() -> None:
    observation = Observation(
        entity_key="product-1",
        field_name="monthly_price",
        raw_value=None,
        normalized_value=None,
        origin=ValueOrigin.SOURCE,
        status=ObservationStatus.PENDING_REVIEW,
    )

    assert observation.raw_value is None
    assert observation.normalized_value is None


def test_source_observation_requires_evidence_before_confirmation() -> None:
    with pytest.raises(ValidationError):
        Observation(
            entity_key="product-1",
            field_name="monthly_price",
            raw_value="¥99/月",
            normalized_value="99 CNY/month",
            origin=ValueOrigin.SOURCE,
            status=ObservationStatus.CONFIRMED,
        )
