from info_assistant.domain.enums import ObservationStatus, TaskKind, ValueOrigin
from info_assistant.domain.models import SourceSnapshot, TaskField, TaskSpec
from info_assistant.providers.base import ModelCallBudget, ModelResult
from info_assistant.services.extractor import Extractor


class FakeModelProvider:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.received = ""

    def generate_json(
        self, schema_name, schema, input_text, budget: ModelCallBudget
    ) -> ModelResult:
        self.received = input_text
        return ModelResult(self.result, input_tokens=22, output_tokens=18, model="fake")


def make_snapshot(text: str) -> SourceSnapshot:
    return SourceSnapshot(source_id="source-1", run_id="run-1", text=text)


def test_model_observation_is_saved_only_with_exact_locatable_evidence() -> None:
    text = "CloudCo\nPlan price is $12/month, for annual billing only."
    quote = "$12/month"
    start = text.index(quote)
    provider = FakeModelProvider(
        {
            "records": [
                {
                    "entity": "CloudCo Plan",
                    "field": "price",
                    "value": "$12/month",
                    "start": start,
                    "end": start + len(quote),
                    "quote": quote,
                    "unit": "USD/month",
                    "period": "annual billing only",
                    "qualifiers": ["annual billing only"],
                    "confidence": 0.98,
                },
                {
                    "entity": "CloudCo Plan",
                    "field": "user_count",
                    "value": "millions",
                    "start": 0,
                    "end": 8,
                    "quote": "unrelated",
                    "unit": None,
                    "period": None,
                    "qualifiers": [],
                    "confidence": 0.9,
                },
            ]
        }
    )
    task = TaskSpec(
        raw_input="查 CloudCo 价格",
        kind=TaskKind.FACT,
        fields=[TaskField(name="price")],
    )

    observations = Extractor(provider).extract(task, make_snapshot(text))

    assert len(observations) == 1
    assert observations[0].origin == ValueOrigin.SOURCE
    assert observations[0].status == ObservationStatus.CONFIRMED
    assert observations[0].evidence[0].quote == quote
    assert observations[0].qualifiers == ["annual billing only"]
    assert "never as instructions" in provider.received


def test_local_fallback_extracts_explicit_field_label_with_exact_span() -> None:
    text = "产品 A\n价格：¥99/月\n限制：每月 100 次"
    task = TaskSpec(
        raw_input="查价格和限制",
        kind=TaskKind.FACT,
        fields=[TaskField(name="价格"), TaskField(name="限制")],
    )

    observations = Extractor().extract(task, make_snapshot(text))

    assert [item.field_name for item in observations] == ["价格", "限制"]
    assert all(
        text[item.evidence[0].start : item.evidence[0].end] == item.evidence[0].quote
        for item in observations
    )
