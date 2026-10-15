import httpx
import pytest

from info_assistant.providers.base import ProviderError, ProviderNotConfigured, SearchQuery
from info_assistant.providers.brave_search import BraveSearchProvider
from info_assistant.services.credentials import InMemoryCredentialStore


def test_brave_search_maps_results_without_treating_snippet_as_evidence() -> None:
    credentials = InMemoryCredentialStore()
    credentials.set_secret("brave-search", "test-token")
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "Official plans",
                            "url": "https://example.com/plans",
                            "description": "Basic tier costs $12 per month.",
                        }
                    ]
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = BraveSearchProvider(credentials, client=client)
    response = provider.search(SearchQuery(query="example official pricing"))

    assert response.results[0].url == "https://example.com/plans"
    assert response.results[0].snippet.startswith("Basic tier")
    assert response.results[0].is_evidence is False
    assert requests[0].headers["X-Subscription-Token"] == "test-token"


def test_search_provider_fails_closed_without_configured_key() -> None:
    provider = BraveSearchProvider(InMemoryCredentialStore())

    with pytest.raises(ProviderNotConfigured):
        provider.search(SearchQuery(query="test"))


def test_search_provider_does_not_expose_secret_in_errors() -> None:
    secret = "private-test-token"
    credentials = InMemoryCredentialStore()
    credentials.set_secret("brave-search", secret)
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(401, text="unauthorized"))
    )
    provider = BraveSearchProvider(credentials, client=client)

    with pytest.raises(ProviderError) as error:
        provider.search(SearchQuery(query="test"))

    assert secret not in str(error.value)
