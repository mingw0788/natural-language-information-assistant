from info_assistant.domain.enums import TaskKind
from info_assistant.domain.models import SourceInput, TaskSpec
from info_assistant.services.capabilities import CapabilityChecker
from info_assistant.services.credentials import InMemoryCredentialStore
from info_assistant.services.provider_settings import ProviderSettingsStore
from info_assistant.storage.database import Database


def test_url_task_can_run_locally_without_search_or_model(tmp_path) -> None:
    database = Database(tmp_path / "assistant.sqlite3")
    database.migrate()
    credentials = InMemoryCredentialStore()
    settings = ProviderSettingsStore(database, credentials)
    checker = CapabilityChecker(settings)
    task = TaskSpec(
        raw_input="从这个网址查价格",
        kind=TaskKind.FACT,
        sources=[SourceInput(kind="url", value="https://example.com/pricing")],
    )

    report = checker.assess(task)

    assert report.ready is True
    assert any(
        fallback.startswith("直接读取用户提供的网址")
        for fallback in report.local_fallbacks
    )


def test_discovery_task_reports_missing_search_before_start(tmp_path) -> None:
    database = Database(tmp_path / "assistant.sqlite3")
    database.migrate()
    checker = CapabilityChecker(ProviderSettingsStore(database, InMemoryCredentialStore()))
    task = TaskSpec(raw_input="列出十个公开的数据可视化工具", kind=TaskKind.LIST)

    report = checker.assess(task)

    assert report.ready is False
    assert "搜索服务" in report.missing[0]
