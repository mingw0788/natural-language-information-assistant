from __future__ import annotations

import re
from dataclasses import dataclass

from info_assistant.domain.enums import ObservationStatus
from info_assistant.domain.models import Observation, TaskSpec


@dataclass(frozen=True)
class Evaluation:
    entity_key: str
    status: str
    official_count_eligible: bool
    reasons: list[str]
    preference_score: float


@dataclass(frozen=True)
class ComparisonAssessment:
    comparable: bool
    reason: str
    currency: str | None = None
    period: str | None = None


class ResultEvaluator:
    def evaluate(
        self,
        spec: TaskSpec,
        entity_key: str,
        observations: list[Observation],
        corrections: dict[str, str] | None = None,
    ) -> Evaluation:
        corrections = corrections or {}
        supported = [
            item
            for item in observations
            if item.status in {ObservationStatus.CONFIRMED, ObservationStatus.USER_CONFIRMED}
            and item.evidence
        ]
        reasons: list[str] = []
        pending = False
        excluded = False

        if spec.time_window is not None:
            # Observations currently have no validated publication-date evidence contract.
            # Retrieval/observation timestamps and search date filters cannot establish it.
            pending = True
            reasons.append("来源发布日期尚未核实，无法确认符合任务时间范围")

        by_field = {item.field_name.casefold(): item for item in observations}
        for field in spec.fields:
            if not field.required:
                continue
            item = by_field.get(field.name.casefold())
            if (
                not item
                or not item.raw_value
                or item.status
                not in {
                    ObservationStatus.CONFIRMED,
                    ObservationStatus.USER_CONFIRMED,
                }
            ):
                pending = True
                reasons.append(f"必需字段 {field.name} 尚未核实")

        values = " ".join(
            [
                *(item.raw_value or "" for item in supported),
                *(item.normalized_value or "" for item in supported),
                *(qualifier for item in supported for qualifier in item.qualifiers),
            ]
        ).casefold()
        for condition in spec.required_conditions:
            if condition.text.casefold() in corrections.values():
                reasons.append(f"用户修正满足条件：{condition.text}")
                continue
            result = self._condition_result(condition.text, values)
            if result is True:
                reasons.append(f"证据支持必要条件：{condition.text}")
            elif result is False:
                excluded = True
                reasons.append(f"证据显示不满足必要条件：{condition.text}")
            else:
                pending = True
                reasons.append(f"必要条件尚未核实：{condition.text}")

        if excluded:
            status = "excluded"
            official = False
        elif pending or not supported:
            status = "pending_review"
            official = False
        elif corrections:
            status = "user_confirmed"
            official = False
            reasons.append("结果包含用户修正，单独计入用户确认")
        else:
            status = "confirmed"
            official = True
            if not reasons:
                reasons.append("已有可定位证据，未发现必要条件冲突")
        scores = [item.confidence for item in supported if item.confidence is not None]
        preference_score = sum(scores) / len(scores) if scores else 0.0
        return Evaluation(entity_key, status, official, reasons, preference_score)

    @staticmethod
    def _condition_result(condition: str, values: str) -> bool | None:
        normalized = condition.casefold()
        free_terms = {"免费", "免费的", "free", "free tier"}
        if normalized in free_terms or "免费" in normalized or "free" in normalized:
            if any(term in values for term in ("not free", "不免费", "非免费", "paid", "收费")):
                return False
            if any(term in values for term in ("free", "免费", "$0", "0 元", "0元")):
                return True
            return None
        if not values:
            return None
        if normalized in values:
            return True
        return None


def compare_observations(observations: list[Observation]) -> ComparisonAssessment:
    values = [item.raw_value or "" for item in observations]
    if len(values) < 2 or any(not value.strip() for value in values):
        return ComparisonAssessment(False, "数据缺失，不能形成可比结论")
    currencies = {_currency(value) for value in values}
    if None in currencies or len(currencies) > 1:
        return ComparisonAssessment(False, "币种不同或不明确，不能直接排名")
    periods = {_period(value) for value in values}
    if None in periods or len(periods) > 1:
        return ComparisonAssessment(False, "计费周期不同或不明确，不能直接排名")
    numbers = [_number(value) for value in values]
    if any(number is None for number in numbers):
        return ComparisonAssessment(False, "数值口径不明确，不能直接排名")
    currency = next(iter(currencies))
    period = next(iter(periods))
    return ComparisonAssessment(True, "币种、周期和数值均可比", currency, period)


def _currency(value: str) -> str | None:
    upper = value.upper()
    if "USD" in upper or "$" in value:
        return "USD"
    if "CNY" in upper or "RMB" in upper or "¥" in value or "元" in value:
        return "CNY"
    if "EUR" in upper or "€" in value:
        return "EUR"
    if "JPY" in upper:
        return "JPY"
    return None


def _period(value: str) -> str | None:
    lower = value.casefold()
    if any(term in lower for term in ("/month", "monthly", "per month", "每月", "月")):
        return "month"
    if any(term in lower for term in ("/year", "yearly", "annual", "per year", "每年", "年")):
        return "year"
    if "/week" in lower or "weekly" in lower or "每周" in lower:
        return "week"
    return None


def _number(value: str) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
    return float(match.group()) if match else None
