from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit


class UnsafeUrl(Exception):
    pass


@dataclass(frozen=True)
class PinnedTarget:
    url: str
    scheme: str
    host: str
    port: int
    address: str
    path_query: str


class UrlPolicy:
    def __init__(self, resolver=None, test_loopback_allowlist: set[str] | None = None) -> None:
        self.resolver = resolver or self._resolve
        self.test_loopback_allowlist = {
            ipaddress.ip_address(item).compressed for item in (test_loopback_allowlist or set())
        }

    def validate_and_pin(self, url: str) -> PinnedTarget:
        if not isinstance(url, str) or len(url) > 4_096:
            raise UnsafeUrl("网址无效")
        try:
            parts = urlsplit(url.strip())
            if parts.scheme.lower() not in {"http", "https"}:
                raise UnsafeUrl("只允许 HTTP 或 HTTPS 网址")
            if not parts.hostname or parts.username is not None or parts.password is not None:
                raise UnsafeUrl("网址缺少主机名或包含登录凭据")
            if parts.fragment:
                url = url.split("#", maxsplit=1)[0]
                parts = urlsplit(url)
            host = parts.hostname.rstrip(".").encode("idna").decode("ascii").lower()
            if host in {"localhost", "localhost.localdomain"} or host.endswith(
                (".localhost", ".local", ".internal")
            ):
                raise UnsafeUrl("本机或内部域名不允许访问")
            port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
            if not 1 <= port <= 65_535:
                raise UnsafeUrl("端口号无效")
        except (UnicodeError, ValueError) as exc:
            if isinstance(exc, UnsafeUrl):
                raise
            raise UnsafeUrl("网址格式无效") from None

        try:
            addresses = list(dict.fromkeys(self.resolver(host, port)))
        except (OSError, socket.gaierror, ValueError):
            raise UnsafeUrl("无法安全解析目标地址") from None
        if not addresses:
            raise UnsafeUrl("目标没有可用地址")
        normalized = []
        for raw_address in addresses:
            try:
                address = ipaddress.ip_address(raw_address.split("%", maxsplit=1)[0])
            except ValueError:
                raise UnsafeUrl("目标解析到无效地址") from None
            allowed_test_loopback = (
                address.is_loopback and address.compressed in self.test_loopback_allowlist
            )
            if not address.is_global and not allowed_test_loopback:
                raise UnsafeUrl("目标解析到本机、私网或保留地址")
            normalized.append(address.compressed)

        path_query = parts.path or "/"
        if parts.query:
            path_query += f"?{parts.query}"
        normalized_url = parts._replace(fragment="").geturl()
        return PinnedTarget(
            url=normalized_url,
            scheme=parts.scheme.lower(),
            host=host,
            port=port,
            address=normalized[0],
            path_query=path_query,
        )

    @staticmethod
    def _resolve(host: str, port: int) -> list[str]:
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            return [item[4][0] for item in results]
        return [literal.compressed]
