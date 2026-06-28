from __future__ import annotations

from website_downloader.urltools import (
    canonicalize_url,
    is_allowed_external,
    is_internal,
    normalize_external_domains,
)


def test_canonicalize_url_drops_fragments_and_default_ports() -> None:
    result = canonicalize_url("/docs/page.html#part", "https://EXAMPLE.com:443/base/")
    assert result == "https://example.com/docs/page.html"


def test_is_internal_treats_www_as_same_site() -> None:
    assert is_internal("https://www.example.com/a.css", "example.com")
    assert is_internal("/a.css", "example.com")
    assert not is_internal("https://cdn.example.net/a.css", "example.com")


def test_external_domain_whitelist_accepts_subdomains() -> None:
    domains = normalize_external_domains(["https://cdn.example.com", "assets.test"])
    assert domains == {"cdn.example.com", "assets.test"}
    assert is_allowed_external("https://img.cdn.example.com/a.png", domains)
    assert not is_allowed_external("https://example.org/a.png", domains)
