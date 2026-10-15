from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from info_assistant.domain.enums import TaskKind
from info_assistant.domain.models import Condition, SourceInput, TaskField, TaskSpec, TimeWindow

URL_PATTERN = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
URL_TRAILING = ",，。.;；)]}"
COUNT_PATTERN = re.compile(
    r"(?:找|列出|至少|top)\s*(\d+)\s*(?:个|家|项|款|条)?", re.IGNORECASE
)
REQUIRED_PATTERN = re.compile(r"(?:只要|只需|必须是|必须|仅限|只限)\s*([^，,；;。.]+)")
PREFERENCE_PATTERN = re.compile(r"(?:优先|prefer(?:ably)?)\s*([^，,；;。.]+)", re.IGNORECASE)
FIELD_TERMS = (
    (("价格", "价钱", "收费", "费用", "price", "pricing"), "价格"),
    (("限制", "额度", "上限", "限制条件", "limit"), "限制"),
    (("来源", "出处", "链接", "source"), "来源"),
    (("发布日期", "发布于", "更新时间", "发布日期", "date"), "发布日期"),
    (("优点", "优势", "pros"), "优点"),
    (("缺点", "劣势", "cons"), "缺点"),
    (("地点", "城市", "location"), "地点"),
)


@dataclass(frozen=True)
class TaskChange:
    action: str
    spec: TaskSpec
    view_filter: str | None = None
    target_count: int | None = None
    message: str = ""


class TaskPlanner:
    def interpret(
        self,
        raw_input: str,
        now: datetime | None = None,
        defaults: dict | None = None,
        supplemental_material: tuple[str, ...] | list[str] = (),
    ) -> TaskSpec:
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        urls = self._urls(raw_input)
        sources = [SourceInput(kind="url", value=url) for url in urls]
        pasted: list[str] = []
        for material in supplemental_material:
            item = material.strip()
            if not item:
                continue
            material_urls = self._urls(item)
            if material_urls and len(material_urls) == 1 and item == material_urls[0]:
                sources.append(SourceInput(kind="url", value=material_urls[0]))
            else:
                pasted.append(item)

        cleaned = URL_PATTERN.sub(" ", raw_input)
        kind = self._kind(cleaned)
        required = [
            Condition(text=match.group(1).strip(), origin="user")
            for match in REQUIRED_PATTERN.finditer(cleaned)
            if match.group(1).strip()
        ]
        preferences = [
            Condition(text=match.group(1).strip(), origin="user")
            for match in PREFERENCE_PATTERN.finditer(cleaned)
            if match.group(1).strip()
        ]
        fields = self._fields(cleaned)
        count = COUNT_PATTERN.search(cleaned)
        stop_conditions = (
            [f"满足条件的去重对象达到 {int(count.group(1))} 个"] if count else []
        )
        assumptions: list[str] = []
        time_window = None
        if "最近" in cleaned:
            start = current - timedelta(days=7)
            time_window = TimeWindow(start=start, end=current, label="最近 7 天")
            assumptions.append("“最近”按启动时刻前 7 天解释")
        constraints = self._source_constraints(cleaned)
        spec_defaults = defaults or {}
        return TaskSpec(
            raw_input=raw_input.strip(),
            kind=kind,
            summary=cleaned.strip()[:1_000],
            fields=fields,
            required_conditions=required,
            preferences=preferences,
            source_constraints=constraints,
            sources=self._unique_sources(sources),
            pasted_material=pasted,
            region=spec_defaults.get("region"),
            time_window=time_window,
            assumptions=assumptions,
            stop_conditions=stop_conditions,
            display_limit=min(max(int(spec_defaults.get("display_limit", 10)), 1), 100),
        )

    def apply_message(
        self, current_version: TaskSpec, message: str, now: datetime | None = None
    ) -> TaskChange:
        text = message.strip()
        lowered = text.lower()
        if "只看" in text or "show only" in lowered:
            selected_filter = self._view_filter(text)
            return TaskChange("view", current_version, selected_filter, message=text)
        target = COUNT_PATTERN.search(text)
        if any(word in text for word in ("再找", "继续找", "多找")) or "find more" in lowered:
            updated = current_version.model_copy(deep=True)
            if target:
                updated.stop_conditions = [
                    f"满足条件的去重对象达到 {int(target.group(1))} 个"
                ]
            return TaskChange(
                "continue",
                updated,
                target_count=int(target.group(1)) if target else None,
                message=text,
            )
        field_match = re.search(r"(?:加一列|增加一列|再加一列)\s*([^，,；;。.]+)", text)
        if field_match:
            name = field_match.group(1).strip()
            updated = current_version.model_copy(deep=True)
            if name and name not in {field.name for field in updated.fields}:
                updated.fields.append(TaskField(name=name, required=False))
            return TaskChange("new_version", updated, message=text)
        if "撤销条件" in text or "取消上一个条件" in text:
            updated = current_version.model_copy(deep=True)
            if updated.required_conditions:
                updated.required_conditions.pop()
            return TaskChange("new_version", updated, message=text)

        combined = self.interpret(
            f"{current_version.raw_input}\n{text}",
            now=now,
            supplemental_material=[
                *current_version.pasted_material,
                *(source.value for source in current_version.sources),
            ],
            defaults={
                "region": current_version.region,
                "display_limit": current_version.display_limit,
            },
        )
        return TaskChange("new_version", combined, message=text)

    @staticmethod
    def _urls(text: str) -> list[str]:
        urls = []
        for match in URL_PATTERN.findall(text):
            value = match.rstrip(URL_TRAILING)
            if value:
                urls.append(value)
        return urls

    @staticmethod
    def _unique_sources(sources: list[SourceInput]) -> list[SourceInput]:
        unique: dict[tuple[str, str], SourceInput] = {}
        for source in sources:
            unique.setdefault((source.kind, source.value), source)
        return list(unique.values())

    @staticmethod
    def _kind(text: str) -> TaskKind:
        if any(token in text.lower() for token in ("比较", "对比", "compare", " vs ", "versus")):
            return TaskKind.COMPARISON
        if any(
            token in text.lower()
            for token in ("列出", "清单", "有哪些", "找一些", "list", "find ")
        ):
            return TaskKind.LIST
        question = text.lower().rstrip("？? ")
        if any(
            question.endswith(term)
            for term in (
                "哪里",
                "在哪",
                "是谁",
                "是什么",
                "多少",
                "何时",
                "什么时候",
                "怎么",
                "how many",
                "what is",
                "where is",
            )
        ):
            return TaskKind.FACT
        if "?" in text or "？" in text:
            return TaskKind.FACT
        return TaskKind.LIST

    @staticmethod
    def _fields(text: str) -> list[TaskField]:
        names = []
        lower = text.lower()
        for terms, label in FIELD_TERMS:
            if any(term in lower for term in terms):
                names.append(label)
        return [TaskField(name=name) for name in dict.fromkeys(names)]

    @staticmethod
    def _source_constraints(text: str) -> list[str]:
        matches = re.findall(r"(?:只查|仅查|只从|仅从|只能使用)\s*([^，,；;。.]+)", text)
        return [match.strip() for match in matches if match.strip()]

    @staticmethod
    def _view_filter(text: str) -> str:
        if any(word in text for word in ("待核查", "待确认", "需要核查")):
            return "pending_review"
        if any(word in text for word in ("排除", "不符合")):
            return "excluded"
        if any(word in text for word in ("用户确认", "已确认")):
            return "user_confirmed"
        return "confirmed"
