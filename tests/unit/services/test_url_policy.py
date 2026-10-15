import socket

import pytest

from info_assistant.services.url_policy import UnsafeUrl, UrlPolicy


def resolver_for(*addresses: str):
    def resolve(host: str, port: int) -> list[str]:
        return list(addresses)

    return resolve


@pytest.mark.parametrize(
    "url",
    [
        "file:///C:/Windows/win.ini",
        "ftp://example.com/file",
        "http://user:pass@example.com/",
        "http://localhost/",
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.1.1/",
        "http://[::1]/",
    ],
)
def test_url_policy_rejects_non_web_and_non_public_targets(url: str) -> None:
    with pytest.raises(UnsafeUrl):
        UrlPolicy().validate_and_pin(url)


def test_dns_result_is_pinned_and_private_answer_is_rejected() -> None:
    policy = UrlPolicy(resolver=resolver_for("93.184.216.34"))
    target = policy.validate_and_pin("https://example.test/path")

    assert target.address == "93.184.216.34"
    assert target.host == "example.test"
    assert target.path_query == "/path"

    mixed_dns = UrlPolicy(resolver=resolver_for("93.184.216.34", "10.0.0.4"))
    with pytest.raises(UnsafeUrl):
        mixed_dns.validate_and_pin("https://example.test/")


def test_test_loopback_allowlist_is_explicit_and_exact() -> None:
    resolver = resolver_for("127.0.0.1")
    with pytest.raises(UnsafeUrl):
        UrlPolicy(resolver=resolver).validate_and_pin("http://fixture.test/")

    allowed = UrlPolicy(resolver=resolver, test_loopback_allowlist={"127.0.0.1"})
    assert allowed.validate_and_pin("http://fixture.test/").address == "127.0.0.1"


def test_dns_lookup_failure_fails_closed() -> None:
    def fail(host: str, port: int) -> list[str]:
        raise socket.gaierror("no DNS")

    with pytest.raises(UnsafeUrl):
        UrlPolicy(resolver=fail).validate_and_pin("https://example.test/")
