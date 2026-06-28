from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree

import requests

from .urltools import canonicalize_url, is_internal

log = logging.getLogger(__name__)


def load_sitemap_urls(
    sitemap: str,
    *,
    start_url: str,
    session: requests.Session,
    timeout: int,
    root_netloc: str,
) -> list[str]:
    sitemap_url = _resolve_sitemap_location(sitemap, start_url)
    seen_sitemaps: set[str] = set()
    return _load_sitemap_urls(
        sitemap_url,
        session=session,
        timeout=timeout,
        root_netloc=root_netloc,
        seen_sitemaps=seen_sitemaps,
    )


def _resolve_sitemap_location(sitemap: str, start_url: str) -> str:
    if sitemap == "auto":
        parsed = urlparse(start_url)
        return f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"
    return sitemap


def _load_sitemap_urls(
    sitemap: str,
    *,
    session: requests.Session,
    timeout: int,
    root_netloc: str,
    seen_sitemaps: set[str],
) -> list[str]:
    if sitemap in seen_sitemaps:
        return []
    seen_sitemaps.add(sitemap)

    try:
        text = _read_sitemap_text(sitemap, session=session, timeout=timeout)
        root = ElementTree.fromstring(text)
    except Exception as exc:
        log.warning("Could not read sitemap %s: %s", sitemap, exc)
        return []

    tag = _strip_namespace(root.tag)
    urls: list[str] = []
    if tag == "urlset":
        for loc in root.findall(".//{*}url/{*}loc"):
            if loc.text:
                url = canonicalize_url(loc.text.strip(), sitemap)
                if is_internal(url, root_netloc):
                    urls.append(url)
    elif tag == "sitemapindex":
        for loc in root.findall(".//{*}sitemap/{*}loc"):
            if loc.text:
                nested = canonicalize_url(loc.text.strip(), sitemap)
                urls.extend(
                    _load_sitemap_urls(
                        nested,
                        session=session,
                        timeout=timeout,
                        root_netloc=root_netloc,
                        seen_sitemaps=seen_sitemaps,
                    )
                )
    else:
        log.warning("Unsupported sitemap root %s in %s", tag, sitemap)

    return list(dict.fromkeys(urls))


def _read_sitemap_text(sitemap: str, *, session: requests.Session, timeout: int) -> str:
    parsed = urlparse(sitemap)
    if parsed.scheme in {"http", "https"}:
        response = session.get(sitemap, timeout=timeout)
        response.raise_for_status()
        return response.text
    return Path(sitemap).expanduser().read_text(encoding="utf-8")


def _strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
