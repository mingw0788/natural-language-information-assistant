from __future__ import annotations

from pathlib import Path

import pytest

from info_assistant.domain.enums import RunStatus
from info_assistant.domain.models import SourceInput, TaskField, TaskSpec
from info_assistant.providers.base import SearchResponse, SearchResult
from info_assistant.services.budget_ledger import BudgetLedger
from info_assistant.services.collection_runner import CollectionRunner
from info_assistant.services.run_control import RunControl
from info_assistant.services.source_fetcher import FetchOutcome
from info_assistant.storage.database import Database
from info_assistant.storage.repository import Repository


class FakeFetcher:
    def __init__(self, outcomes: dict[str, FetchOutcome], after_fetch=None) -> None:
        self.outcomes = outcomes
        self.after_fetch = after_fetch
        self.requested: list[str] = []

    def fetch(self, url: str, timeout: float = 30) -> FetchOutcome:
        self.requested.append(url)
        outcome = self.outcomes[url]
        if self.after_fetch:
            self.after_fetch(url)
        return outcome

    def fetch_pages(self, url: str, max_pages: int = 5) -> list[FetchOutcome]:
        return [self.fetch(url)]


class FakeSearch:
    def __init__(self, urls: list[str]) -> None:
        self.urls = urls
        self.queries: list[str] = []

    def search(self, query) -> SearchResponse:
        self.queries.append(query.query)
        return SearchResponse(
            provider="fake",
            results=[SearchResult(title=url, url=url, snippet="lead only") for url in self.urls],
        )


class SequencedSearch:
    def __init__(self, results_by_call: list[list[str]]) -> None:
        self.results_by_call = results_by_call
        self.queries: list[str] = []

    def search(self, query) -> SearchResponse:
        index = len(self.queries)
        self.queries.append(query.query)
        urls = self.results_by_call[min(index, len(self.results_by_call) - 1)]
        return SearchResponse(
            provider="fake",
            results=[SearchResult(title=url, url=url, snippet="lead only") for url in urls],
        )


def fetched(url: str, text: str, title: str = "Source") -> FetchOutcome:
    return FetchOutcome(
        status="fetched",
        requested_url=url,
        final_url=url,
        text=text,
        title=title,
        content_type="text/html",
        http_status=200,
    )


def failed(url: str) -> FetchOutcome:
    return FetchOutcome(
        status="failed",
        requested_url=url,
        final_url=url,
        error_code="fixture_error",
        error_message="fixture source unavailable",
    )


def setup_runner(
    tmp_path: Path,
    fetcher: FakeFetcher,
    search=None,
) -> tuple[CollectionRunner, Repository, RunControl]:
    database = Database(tmp_path / "assistant.sqlite3")
    database.migrate()
    repository = Repository(database)
    ledger = BudgetLedger(database)
    control = RunControl(database, repository, ledger)
    return (
        CollectionRunner(
            database=database,
            repository=repository,
            control=control,
            ledger=ledger,
            fetcher=fetcher,
            search_provider=search,
        ),
        repository,
        control,
    )


def test_runner_persists_exact_evidence_and_completes(tmp_path: Path) -> None:
    url = "https://example.test/pricing"
    runner, repository, _ = setup_runner(
        tmp_path,
        FakeFetcher({url: fetched(url, "Acme\n价格：每月 10 元")}),
    )
    task = repository.create_task(
        TaskSpec(
            raw_input="查 Acme 价格",
            sources=[SourceInput(kind="url", value=url)],
            fields=[TaskField(name="价格")],
        )
    )
    run = repository.create_run(task.id, task.latest_version.id)

    runner.run(run.id)

    result = repository.get_run(run.id)
    observations = repository.observations_for_task(task.id)
    assert result.status == RunStatus.COMPLETED
    assert len(observations) == 1
    assert observations[0].raw_value == "每月 10 元"
    assert observations[0].evidence[0].quote == "每月 10 元"
    events = repository.events_after(run.id, 0)
    assert any(event.kind == "stage" for event in events)
    assert any(event.kind == "result" for event in events)


def test_search_snippets_are_only_leads_and_failed_source_is_partial(tmp_path: Path) -> None:
    good = "https://example.test/ok"
    bad = "https://example.test/bad"
    fetcher = FakeFetcher({good: fetched(good, "Acme\n价格：$5/月"), bad: failed(bad)})
    search = FakeSearch([good, bad])
    runner, repository, _ = setup_runner(tmp_path, fetcher, search)
    task = repository.create_task(
        TaskSpec(raw_input="列出 Acme 价格", fields=[TaskField(name="价格")])
    )
    run = repository.create_run(task.id, task.latest_version.id)

    runner.run(run.id)

    assert repository.get_run(run.id).status == RunStatus.PARTIAL
    assert search.queries == ["列出 Acme 价格"]
    assert fetcher.requested == [good, bad]
    assert repository.observations_for_task(task.id)[0].evidence


def test_supplemental_search_is_bounded_and_stops_at_requested_count(tmp_path: Path) -> None:
    first = "https://example.test/first"
    second = "https://example.test/second"
    search = SequencedSearch([[first], [second], [second]])
    fetcher = FakeFetcher(
        {
            first: fetched(first, "Acme\n价格：每月 10 元"),
            second: fetched(second, "Beta\n价格：每月 20 元"),
        }
    )
    runner, repository, _ = setup_runner(tmp_path, fetcher, search)
    task = repository.create_task(
        TaskSpec(
            raw_input="找 2 个产品价格",
            fields=[TaskField(name="价格")],
            stop_conditions=["满足条件的去重对象达到 2 个"],
        )
    )
    run = repository.create_run(task.id, task.latest_version.id)

    runner.run(run.id)

    assert repository.get_run(run.id).status == RunStatus.COMPLETED
    assert len(search.queries) == 3
    assert fetcher.requested == [first, second]
    assert len(repository.observations_for_task(task.id)) == 2


def test_search_and_supplement_rounds_have_a_hard_call_cap(tmp_path: Path) -> None:
    search = FakeSearch([])
    runner, repository, _ = setup_runner(tmp_path, FakeFetcher({}), search)
    task = repository.create_task(
        TaskSpec(raw_input="列出产品价格", fields=[TaskField(name="价格")])
    )
    run = repository.create_run(task.id, task.latest_version.id)

    runner.run(run.id)

    snapshot = runner.ledger.snapshot(run.id)
    assert repository.get_run(run.id).status == RunStatus.PARTIAL
    assert len(search.queries) <= 5
    assert snapshot.used["search_calls"] == len(search.queries)


def test_pause_stops_before_dispatching_next_source(tmp_path: Path) -> None:
    first = "https://example.test/first"
    second = "https://example.test/second"
    fetcher = FakeFetcher(
        {
            first: fetched(first, "First\n价格：10 元"),
            second: fetched(second, "Second\n价格：20 元"),
        }
    )
    runner, repository, control = setup_runner(tmp_path, fetcher)
    task = repository.create_task(
        TaskSpec(
            raw_input="读取两页价格",
            sources=[SourceInput(kind="url", value=first), SourceInput(kind="url", value=second)],
            fields=[TaskField(name="价格")],
        )
    )
    run = repository.create_run(task.id, task.latest_version.id)
    fetcher.after_fetch = lambda url: control.pause(run.id) if url == first else None

    runner.run(run.id)

    assert repository.get_run(run.id).status == RunStatus.PAUSED
    assert fetcher.requested == [first]
    assert len(repository.observations_for_task(task.id)) == 1

    fetcher.after_fetch = None
    control.resume(run.id)
    runner.run(run.id)

    assert repository.get_run(run.id).status == RunStatus.COMPLETED
    assert fetcher.requested == [first, second]
    assert len(repository.observations_for_task(task.id)) == 2


def test_repeated_runner_dispatch_does_not_refetch_terminal_run(tmp_path: Path) -> None:
    url = "https://example.test/pricing"
    fetcher = FakeFetcher({url: fetched(url, "Acme\n价格：10 元")})
    runner, repository, _ = setup_runner(tmp_path, fetcher)
    task = repository.create_task(
        TaskSpec(
            raw_input="查价格",
            sources=[SourceInput(kind="url", value=url)],
            fields=[TaskField(name="价格")],
        )
    )
    run = repository.create_run(task.id, task.latest_version.id)

    runner.run(run.id)
    runner.run(run.id)

    assert fetcher.requested == [url]
    assert repository.get_run(run.id).status == RunStatus.COMPLETED


def test_entity_resolution_merges_same_host_alias_and_separates_other_hosts(
    tmp_path: Path,
) -> None:
    first = "https://acme.example/products/one"
    alias = "https://acme.example/products/two"
    unrelated = "https://catalog.example/acme"
    fetcher = FakeFetcher(
        {
            first: fetched(first, "Acme Inc\n价格：每月 10 元"),
            alias: fetched(alias, "ACME\n价格：每月 10 元"),
            unrelated: fetched(unrelated, "Acme\n价格：每月 10 元"),
        }
    )
    runner, repository, _ = setup_runner(tmp_path, fetcher)
    task = repository.create_task(
        TaskSpec(
            raw_input="查 Acme 产品价格",
            sources=[
                SourceInput(kind="url", value=first),
                SourceInput(kind="url", value=alias),
                SourceInput(kind="url", value=unrelated),
            ],
            fields=[TaskField(name="价格")],
        )
    )
    run = repository.create_run(task.id, task.latest_version.id)

    runner.run(run.id)

    with runner.database.connection() as connection:
        groups = connection.execute(
            "SELECT e.canonical_name, COUNT(DISTINCT o.id) AS observation_count "
            "FROM entities e JOIN observations o ON o.entity_id=e.id "
            "WHERE o.run_id=? GROUP BY e.id ORDER BY e.canonical_name",
            (run.id,),
        ).fetchall()
    assert [(row["canonical_name"], row["observation_count"]) for row in groups] == [
        ("Acme", 1),
        ("Acme Inc", 2),
    ]


@pytest.mark.parametrize("has_sources", [False, True])
def test_missing_search_provider_blocks_discovery_without_network(
    tmp_path: Path, has_sources: bool
) -> None:
    url = "https://example.test/pricing"
    fetcher = FakeFetcher({url: fetched(url, "Acme\n价格：10 元")})
    runner, repository, _ = setup_runner(tmp_path, fetcher)
    spec = TaskSpec(
        raw_input="列出 Acme 价格",
        sources=[SourceInput(kind="url", value=url)] if has_sources else [],
        fields=[TaskField(name="价格")],
    )
    task = repository.create_task(spec)
    run = repository.create_run(task.id, task.latest_version.id)

    runner.run(run.id)

    assert repository.get_run(run.id).status == (
        RunStatus.COMPLETED if has_sources else RunStatus.BLOCKED
    )
    assert fetcher.requested == ([url] if has_sources else [])
