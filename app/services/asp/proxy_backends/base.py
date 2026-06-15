"""Pluggable residential/datacenter proxy URL builders."""

from __future__ import annotations

from abc import ABC, abstractmethod
from urllib.parse import quote


class ProxyBackend(ABC):
    name: str

    @abstractmethod
    def is_configured(self) -> bool:
        ...

    @abstractmethod
    def build_urls(self, *, country: str = "us", session_id: str | None = None) -> list[str]:
        """Return one or more proxy URLs for this backend."""


class StaticProxyBackend(ProxyBackend):
    name = "static"

    def __init__(self, urls: list[str]) -> None:
        self._urls = urls

    def is_configured(self) -> bool:
        return bool(self._urls)

    def build_urls(self, *, country: str = "us", session_id: str | None = None) -> list[str]:
        return list(self._urls)


class SmartproxyBackend(ProxyBackend):
    name = "smartproxy"

    def __init__(self, user: str, password: str, host: str, port: int) -> None:
        self._user = user
        self._password = password
        self._host = host
        self._port = port

    def is_configured(self) -> bool:
        return bool(self._user and self._password)

    def build_urls(self, *, country: str = "us", session_id: str | None = None) -> list[str]:
        # Residential rotating — country via username suffix
        user = f"user-{self._user}-country-{country}"
        if session_id:
            user = f"{user}-session-{session_id[:12]}"
        return [f"http://{quote(user)}:{quote(self._password)}@{self._host}:{self._port}"]


class IPRoyalBackend(ProxyBackend):
    name = "iproyal"

    def __init__(self, user: str, password: str, host: str, port: int) -> None:
        self._user = user
        self._password = password
        self._host = host
        self._port = port

    def is_configured(self) -> bool:
        return bool(self._user and self._password)

    def build_urls(self, *, country: str = "us", session_id: str | None = None) -> list[str]:
        # IPRoyal residential: password embeds geo + session
        pw = f"{self._password}_country-{country}"
        if session_id:
            pw = f"{pw}_session-{session_id[:12]}_lifetime-30m"
        return [f"http://{quote(self._user)}:{quote(pw)}@{self._host}:{self._port}"]


class OxylabsBackend(ProxyBackend):
    name = "oxylabs"

    def __init__(self, user: str, password: str, host: str, port: int) -> None:
        self._user = user
        self._password = password
        self._host = host
        self._port = port

    def is_configured(self) -> bool:
        return bool(self._user and self._password)

    def build_urls(self, *, country: str = "us", session_id: str | None = None) -> list[str]:
        # Oxylabs residential: customer-USER-cc-COUNTRY
        user = f"customer-{self._user}-cc-{country.upper()}"
        if session_id:
            user = f"{user}-sessid-{session_id[:12]}"
        return [f"http://{quote(user)}:{quote(self._password)}@{self._host}:{self._port}"]
