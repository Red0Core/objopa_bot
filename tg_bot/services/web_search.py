import asyncio
import re
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from core.logger import logger

MAX_QUERY_LENGTH = 400
MAX_RESULTS = 6
CACHE_TTL = 300
MAX_CACHE_ENTRIES = 128


def normalize_query(query: str) -> str:
    return " ".join(query.split())


@dataclass(frozen=True)
class SearchSource:
    title: str
    link: str
    snippet: str


@dataclass(frozen=True)
class SearchResult:
    sources: tuple[SearchSource, ...] = ()
    error: str | None = None


class WebSearchService:
    def __init__(self, api_key: str, transport: httpx.AsyncBaseTransport | None = None):
        self.api_key = api_key
        self.transport = transport
        self._cache: dict[str, tuple[float, SearchResult]] = {}
        self._lock = asyncio.Lock()

    async def search(self, query: str) -> SearchResult:
        query = normalize_query(query)
        if not query or len(query) > MAX_QUERY_LENGTH:
            return SearchResult(error=f"Search query must contain 1–{MAX_QUERY_LENGTH} characters.")
        cache_key = query.casefold()
        safe_query = query.replace(self.api_key, "[REDACTED]") if self.api_key else query
        if not self.api_key:
            logger.warning("Web search unavailable: SERPER_API_KEY is not configured")
            return SearchResult(error="Web search failed: search is not configured; fresh facts cannot be verified.")

        async with self._lock:
            now = time.monotonic()
            self._cache = {key: entry for key, entry in self._cache.items() if entry[0] > now}
            cached = self._cache.get(cache_key)
            if cached:
                logger.info(
                    "Web search cache hit: query={!r}, cache_hit=True, results={}, domains={}",
                    safe_query,
                    len(cached[1].sources),
                    [
                        (urlsplit(s.link).hostname or "").replace(self.api_key, "[REDACTED]")
                        for s in cached[1].sources[:3]
                    ],
                )
                return cached[1]
            logger.info("Web search cache miss: query={!r}", safe_query)
            try:
                async with httpx.AsyncClient(timeout=15.0, transport=self.transport, trust_env=False) as client:
                    response = await client.post(
                        "https://google.serper.dev/search",
                        headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
                        json={"q": query, "num": 10},
                    )
                    response.raise_for_status()
                    data = response.json()
                    if not isinstance(data, dict):
                        raise ValueError("Unexpected search response")
                    result = SearchResult(sources=self._parse_sources(data, query))
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                logger.warning("Web search HTTP error: status={}, query={!r}", status, safe_query)
                return SearchResult(error=f"Web search failed: HTTP {status}; fresh facts cannot be verified.")
            except (httpx.RequestError, ValueError) as exc:
                logger.warning("Web search failed: type={}, query={!r}", type(exc).__name__, safe_query)
                return SearchResult(
                    error="Web search failed: timeout or unavailable service; fresh facts cannot be verified."
                )
            logger.info(
                "Web search complete: query={!r}, results={}, cache_hit=False, domains={}",
                safe_query,
                len(result.sources),
                [(urlsplit(s.link).hostname or "").replace(self.api_key, "[REDACTED]") for s in result.sources[:3]],
            )
            if len(self._cache) >= MAX_CACHE_ENTRIES:
                self._cache.pop(next(iter(self._cache)))
            self._cache[cache_key] = (time.monotonic() + CACHE_TTL, result)
            return result

    @staticmethod
    def _matches_domain(source: SearchSource, domains: tuple[str, ...]) -> bool:
        host = (urlsplit(source.link).hostname or "").lower()
        return any(host == domain or host.endswith("." + domain) for domain in domains)

    @staticmethod
    def _parse_sources(data: dict, query: str = "") -> tuple[SearchSource, ...]:
        entries = []
        answer_box = data.get("answerBox")
        if isinstance(answer_box, dict):
            entries.append(
                {
                    "title": answer_box.get("title", "Search answer"),
                    "link": answer_box.get("link"),
                    "snippet": answer_box.get("answer") or answer_box.get("snippet"),
                }
            )
        organic = data.get("organic", [])
        if isinstance(organic, list):
            entries.extend(organic)
        sources: list[SearchSource] = []
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            link, title, snippet = (entry.get(key) for key in ("link", "title", "snippet"))
            if not all(isinstance(value, str) and value.strip() for value in (link, title, snippet)):
                continue
            assert isinstance(link, str) and isinstance(title, str) and isinstance(snippet, str)
            try:
                parsed = urlsplit(link)
                if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                    continue
            except ValueError:
                continue
            if re.search(r"[\s<>]", link) or link in seen:
                continue
            seen.add(link)
            sources.append(SearchSource(normalize_query(title)[:200], link, normalize_query(snippet)[:1000]))
        # This mapping only ranks retrieved evidence; it never changes the query or calls search.
        words = set(re.findall(r"[a-z]+", query.casefold()))
        domains = {
            "gpt": ("openai.com",),
            "openai": ("openai.com",),
            "python": ("python.org",),
            "apple": ("apple.com",),
            "iphone": ("apple.com",),
            "google": ("google.com", "ai.google.dev", "blog.google"),
            "gemini": ("google.com", "ai.google.dev", "blog.google"),
            "samsung": ("samsung.com",),
            "github": ("github.com",),
        }
        official = tuple(domain for word in words for domain in domains.get(word, ()))

        specialist = ("reuters.com", "arstechnica.com", "theverge.com", "techcrunch.com")

        def priority(source: SearchSource) -> int:
            if WebSearchService._matches_domain(source, official):
                host = urlsplit(source.link).hostname or ""
                return 1 if host.startswith(("docs.", "developers.", "platform.")) or host == "ai.google.dev" else 0
            if WebSearchService._matches_domain(source, specialist):
                return 2
            return 3

        if official:
            sources.sort(key=priority)
        selected: list[SearchSource] = []
        domain_counts: dict[str, int] = {}
        evidence_seen = set()
        for source in sources:
            host = (urlsplit(source.link).hostname or "").removeprefix("www.")
            fingerprint = (host, source.title.casefold(), source.snippet.casefold())
            if fingerprint in evidence_seen or domain_counts.get(host, 0) >= 2:
                continue
            evidence_seen.add(fingerprint)
            domain_counts[host] = domain_counts.get(host, 0) + 1
            selected.append(source)
            if len(selected) == MAX_RESULTS:
                break
        return tuple(selected)
