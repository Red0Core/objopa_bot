"""Validation and retrieval-status checks for Gemini's server-side URL Context."""

import ipaddress
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

from google.genai import types

MAX_URLS = 20
_URL = re.compile(r"[a-z][a-z0-9+.-]*://[^\s<>\"`]+", re.IGNORECASE)
_SUCCESS = "URL_RETRIEVAL_STATUS_SUCCESS"


def extract_urls(text: str) -> list[str]:
    urls = []
    for match in _URL.finditer(text):
        url = match.group().rstrip(".,;!\"'")
        while url.endswith((")", "]", "}")) and any(
            url.count(close) > url.count(opening) for opening, close in (("(", ")"), ("[", "]"), ("{", "}"))
        ):
            url = url[:-1]
        if url not in urls:
            urls.append(url)
    return urls


def is_public_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii").lower()
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
        ):
            return False
        if parsed.port not in (None, 80, 443) or "%" in host or re.search(r"[\s\x00-\x1f\\]", url):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            pass
        if host.endswith(
            (
                ".localhost",
                ".local",
                ".internal",
                ".lan",
                ".home",
                ".invalid",
                ".nip.io",
                ".sslip.io",
                ".localtest.me",
                ".lvh.me",
                ".ngrok.io",
                ".ngrok-free.app",
                ".pinggy.link",
            )
        ) or host in {"localtest.me", "lvh.me"}:
            return False
        if "." not in host or all(label.isdigit() for label in host.split(".")):
            return False
        if re.fullmatch(r"(?:0x[0-9a-f]+|[0-9.]+)", host):
            return False
        return bool(re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host)) and all(
            label and not label.startswith("-") and not label.endswith("-") for label in host.split(".")
        )
    except (ValueError, UnicodeError):
        return False


def safe_url_label(url: str) -> str:
    """Do not expose credentials, query tokens or fragments in diagnostics."""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or "invalid-url"
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except ValueError:
        return "invalid-url"


def redact_urls(text: str) -> str:
    # A tool query can include a user URL. Logs need no full path/query or credentials.
    return _URL.sub("[URL]", text)


def _key(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))


@dataclass
class UrlContextTurn:
    urls: list[str]
    statuses: dict[str, str] = field(default_factory=dict)

    def observe(self, response: types.GenerateContentResponse) -> None:
        if not response.candidates:
            return
        candidate = response.candidates[0]
        records = list(candidate.url_context_metadata.url_metadata or []) if candidate.url_context_metadata else []
        for part in candidate.content.parts or [] if candidate.content else []:
            if part.tool_response and part.tool_response.tool_type == types.ToolType.URL_CONTEXT:
                payload = part.tool_response.response or {}
                entries = payload.get("url_metadata", payload.get("urlMetadata", []))
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if isinstance(entry, dict):
                        retrieved = entry.get("retrieved_url", entry.get("retrievedUrl"))
                        raw_status = entry.get("url_retrieval_status", entry.get("urlRetrievalStatus"))
                        status = (
                            types.UrlRetrievalStatus(raw_status)
                            if isinstance(raw_status, str)
                            and raw_status in {value.value for value in types.UrlRetrievalStatus}
                            else None
                        )
                        if isinstance(retrieved, str):
                            records.append(types.UrlMetadata(retrieved_url=retrieved, url_retrieval_status=status))
        for record in records:
            for url in self.urls:
                if (
                    record.retrieved_url
                    and is_public_url(record.retrieved_url)
                    and _key(record.retrieved_url) == _key(url)
                ):
                    status = record.url_retrieval_status
                    self.statuses[url] = (
                        status.value
                        if isinstance(status, types.UrlRetrievalStatus)
                        else "URL_RETRIEVAL_STATUS_UNSPECIFIED"
                    )

    @property
    def succeeded(self) -> list[str]:
        return [url for url in self.urls if self.statuses.get(url) == _SUCCESS]

    def finish(self, text: str) -> str:
        failed = [url for url in self.urls if url not in self.succeeded]
        if not failed:
            return text
        # Do not expose unverified page analysis, including invented comparisons of partially fetched pages.
        lines = ["Не удалось подтвердить чтение всех указанных страниц; анализ неполученных страниц не выполнялся."]
        if self.succeeded:
            lines.append(
                "Успешно прочитаны: "
                + ", ".join(f"URL {self.urls.index(url) + 1}: {safe_url_label(url)}" for url in self.succeeded)
            )
        reasons = {
            "URL_RETRIEVAL_STATUS_PAYWALL": "требуется доступ / paywall",
            "URL_RETRIEVAL_STATUS_UNSAFE": "заблокировано по безопасности",
            "URL_RETRIEVAL_STATUS_ERROR": "ошибка получения или неподдерживаемое содержимое",
        }
        for url in failed:
            reason = reasons.get(self.statuses.get(url, ""), "нет подтверждения успешного извлечения")
            lines.append(f"Не прочитана {safe_url_label(url)} (URL {self.urls.index(url) + 1}): {reason}.")
        return "\n".join(lines)
