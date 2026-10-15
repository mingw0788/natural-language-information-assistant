from __future__ import annotations

from typing import Protocol


class CredentialStore(Protocol):
    def set_secret(self, name: str, value: str) -> None: ...

    def get_secret(self, name: str) -> str | None: ...

    def delete_secret(self, name: str) -> None: ...


class CredentialUnavailable(Exception):
    pass


class WindowsCredentialStore:
    _SERVICE = "NaturalLanguageInformationAssistant"

    def set_secret(self, name: str, value: str) -> None:
        try:
            import keyring

            keyring.set_password(self._SERVICE, name, value)
        except Exception as exc:
            raise CredentialUnavailable("Windows 凭据存储不可用") from exc

    def get_secret(self, name: str) -> str | None:
        try:
            import keyring

            return keyring.get_password(self._SERVICE, name)
        except Exception as exc:
            raise CredentialUnavailable("Windows 凭据存储不可用") from exc

    def delete_secret(self, name: str) -> None:
        try:
            import keyring

            keyring.delete_password(self._SERVICE, name)
        except Exception:
            return


class InMemoryCredentialStore:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def set_secret(self, name: str, value: str) -> None:
        self._values[name] = value

    def get_secret(self, name: str) -> str | None:
        return self._values.get(name)

    def delete_secret(self, name: str) -> None:
        self._values.pop(name, None)
