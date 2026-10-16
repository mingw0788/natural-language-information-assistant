from datetime import UTC, datetime, timedelta

from info_assistant.domain.enums import TaskKind
from info_assistant.services.task_planner import TaskPlanner

NOW = datetime(2026, 10, 15, 4, 0, tzinfo=UTC)


def test_interpret_identifies_facts_lists_and_comparisons() -> None:
    planner = TaskPlanner()

    fact = planner.interpret("OpenAI 的总部在哪里？", NOW)
    listing = planner.interpret("列出 10 个免费的绘图工具", NOW)
    comparison = planner.interpret("比较 A 和 B 的价格与限制", NOW)

    assert fact.kind == TaskKind.FACT
    assert listing.kind == TaskKind.LIST
    assert listing.stop_conditions == ["满足条件的去重对象达到 10 个"]
    assert comparison.kind == TaskKind.COMPARISON
    assert {field.name for field in comparison.fields} >= {"价格", "限制"}


def test_interpret_does_not_infer_region_or_time_and_separates_conditions() -> None:
    planner = TaskPlanner()
    task = planner.interpret("只要免费的，优先支持中文的笔记工具", NOW)

    assert task.region is None
    assert task.time_window is None
    assert task.required_conditions[0].text == "免费的"
    assert task.preferences[0].text == "支持中文的笔记工具"


def test_recent_resolves_to_visible_seven_day_window() -> None:
    task = TaskPlanner().interpret("找最近发布的产品更新", NOW)

    assert task.time_window is not None
    assert task.time_window.start == NOW - timedelta(days=7)
    assert task.time_window.end == NOW
    assert task.assumptions == ["“最近”按启动时刻前 7 天解释"]


def test_multiple_urls_and_supplemental_text_are_preserved() -> None:
    task = TaskPlanner().interpret(
        "查这两家价格 https://example.com/a, https://sample.org/b",
        NOW,
        supplemental_material=("只看官方定价页", "我关心月付价格"),
    )

    assert [source.value for source in task.sources if source.kind == "url"] == [
        "https://example.com/a",
        "https://sample.org/b",
    ]
    assert "只看官方定价页" in task.pasted_material
    assert "我关心月付价格" in task.pasted_material


def test_search_query_keeps_subject_and_requested_details_without_request_filler() -> None:
    task = TaskPlanner().interpret(
        "请帮我找几款适合家庭的空气净化器，比较价格、适用面积和滤芯成本，优先使用官方来源",
        NOW,
    )
    build_search_query = getattr(TaskPlanner, "build_search_query", None)

    assert callable(build_search_query), "TaskPlanner must prepare natural-language search terms"
    query = build_search_query(task)

    assert "请帮我" not in query
    assert "空气净化器" in query
    assert "价格" in query
    assert "适用面积" in query
    assert "滤芯成本" in query
    assert "官方来源" in query


def test_interpret_extracts_common_product_comparison_fields() -> None:
    task = TaskPlanner().interpret(
        "请帮我比较空气净化器的价格、适用面积、滤芯成本、耗电量和保修期",
        NOW,
    )

    assert {field.name for field in task.fields} >= {
        "价格",
        "适用面积",
        "滤芯成本",
        "耗电量",
        "保修期",
    }


def test_message_view_filter_does_not_change_collection_spec() -> None:
    planner = TaskPlanner()
    current = planner.interpret("列出 8 个数据工具", NOW)

    change = planner.apply_message(current, "只看正式结果")

    assert change.action == "view"
    assert change.view_filter == "confirmed"
    assert change.spec is current


def test_adding_display_field_creates_updated_spec_without_reinterpreting_scope() -> None:
    planner = TaskPlanner()
    current = planner.interpret("比较 A 和 B", NOW)

    change = planner.apply_message(current, "加一列来源")

    assert change.action == "new_version"
    assert any(field.name == "来源" for field in change.spec.fields)
    assert change.spec.raw_input == current.raw_input
