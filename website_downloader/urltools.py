from __future__ import annotations

from urllib.parse import ParseResult, urljoin, urlparse

from .constants import NON_FETCHABLE_SCHEMES


def canonical_netloc(parsed: ParseResult) -> str:
    """Lowercase hostname and drop default ports."""
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if not host:
        return parsed.netloc.lower()

    if (parsed.scheme == "https" and port == 443) or (parsed.scheme == "http" and port == 80):
        port = None

    return f"{host}:{port}" if port else host


def protocol_fix(url: str, base_url: str) -> str:
    """Resolve protocol-relative URLs using the current page scheme."""
    if url.startswith("//"):
        base = urlparse(base_url)
        scheme = base.scheme or "https"
        return f"{scheme}:{url}"
    return url


def normalize_url(url: str) -> str:
    """Drop fragments so different anchors do not duplicate a crawl target."""
    parsed = urlparse(url)
    return parsed._replace(fragment="").geturl()


def canonicalize_url(url: str, base_url: str = "") -> str:
    """Produce a stable absolute URL key for crawling and asset mapping."""
    url = urljoin(base_url, protocol_fix(url, base_url)) if base_url else protocol_fix(url, url)

    parsed = urlparse(url)
    if not parsed.scheme and not parsed.netloc and base_url:
        parsed = urlparse(urljoin(base_url, url))

    netloc = canonical_netloc(parsed) if parsed.netloc else ""
    return parsed._replace(fragment="", netloc=netloc).geturl()


def is_httpish(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https", "")


def is_non_fetchable(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in NON_FETCHABLE_SCHEMES


def is_internal(link: str, root_netloc: str) -> bool:
    parsed = urlparse(link)
    netloc = canonical_netloc(parsed)

    if not netloc:
        return True
    if netloc == root_netloc:
        return True

    netloc = netloc[4:] if netloc.startswith("www.") else netloc
    root = root_netloc[4:] if root_netloc.startswith("www.") else root_netloc
    return netloc == root


def is_allowed_external(url: str, allowed_domains: set[str] | None) -> bool:
    if allowed_domains is None:
        return True
    host = (urlparse(url).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in allowed_domains)


def normalize_external_domains(domains: list[str] | None) -> set[str] | None:
    if not domains:
        return None
    normalized: set[str] = set()
    for domain in domains:
        parsed = urlparse(domain)
        host = parsed.hostname.lower() if parsed.hostname else domain.lower()
        normalized.add(host.strip().lstrip("."))
    return normalized
