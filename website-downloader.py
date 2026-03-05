#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import os
import queue
import re
import sys
import threading
import time
from hashlib import sha256
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urljoin, urlparse, ParseResult

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

# ---------------------------------------------------------------------------
# Config / constants
# ---------------------------------------------------------------------------

LOG_FMT = "%(asctime)s | %(levelname)-8s | %(threadName)s | %(message)s"

ASSET_EXTENSIONS = (
    ".css",
    ".js",
    ".mjs",
    ".map",
    ".json",
    ".wasm",
    ".webmanifest",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".avif",
    ".svg",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp4",
    ".webm",
    ".mp3",
)

JS_URL_RE = re.compile(
    r"""["'](/[^"']+\.(?:png|jpg|jpeg|gif|svg|webp|avif|ico|css|js|mjs|map|woff|woff2|ttf|eot|json|wasm|webmanifest)(?:\?[^"']*)?)["']""",
    re.IGNORECASE,
)

JS_ABS_URL_RE = re.compile(
    r"""["']((?:https?:)?//[^"']+\.(?:png|jpg|jpeg|gif|svg|webp|avif|ico|css|js|mjs|map|woff|woff2|ttf|eot|json|wasm|webmanifest)(?:\?[^"']*)?)["']""",
    re.IGNORECASE,
)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

TIMEOUT = 15  # seconds
CHUNK_SIZE = 8192  # bytes

# Conservative margins under common OS limits (~255–260 bytes)
MAX_PATH_LEN = 240
MAX_SEG_LEN = 120
_MULTI_DOTS_RE = re.compile(r"\.{3,}")  # collapse 3+ dots to single dot
CSS_URL_RE = re.compile(r"url\(([^)]+)\)")
CSS_IMPORT_RE = re.compile(
    r"""@import\s+(?:url\()?['"]?([^'"\);]+)['"]?\)?\s*;""",
    re.IGNORECASE,
)

_BAD_SEG_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1F]')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    filename="web_scraper.log",
    level=logging.DEBUG,
    format=LOG_FMT,
    datefmt="%H:%M:%S",
    force=True,
)
_console = logging.StreamHandler(sys.stdout)
_console.setLevel(logging.INFO)
_console.setFormatter(logging.Formatter(LOG_FMT, datefmt="%H:%M:%S"))
logging.getLogger().addHandler(_console)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HTTP session (retry, timeouts, custom UA)
# ---------------------------------------------------------------------------

SESSION = requests.Session()
RETRY_STRAT = Retry(
    total=5,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET", "HEAD"],
)
SESSION.mount("http://", HTTPAdapter(max_retries=RETRY_STRAT))
SESSION.mount("https://", HTTPAdapter(max_retries=RETRY_STRAT))
SESSION.headers.update(DEFAULT_HEADERS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def create_dir(path: Path) -> None:
    """Create path (and parents) if it does not already exist."""
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
        log.debug("Created directory %s", path)


NON_FETCHABLE_SCHEMES = {"mailto", "tel", "sms", "javascript", "data", "geo", "blob", "about"}


def is_httpish(u: str) -> bool:
    """True iff the URL is http(s) or relative (no scheme)."""
    p = urlparse(u)
    return (p.scheme in ("http", "https")) or (p.scheme == "")


def is_non_fetchable(u: str) -> bool:
    """True iff the URL clearly shouldn't be fetched (mailto:, tel:, data:, ...)."""
    p = urlparse(u)
    return p.scheme in NON_FETCHABLE_SCHEMES


def is_internal(link: str, root_netloc: str) -> bool:
    parsed = urlparse(link)
    netloc = _canonical_netloc(parsed)

    if not netloc:
        return True

    if netloc == root_netloc:
        return True

    # normalize www
    if netloc.startswith("www."):
        netloc = netloc[4:]
    root = root_netloc[4:] if root_netloc.startswith("www.") else root_netloc

    return netloc == root


def _sanitize_segment(segment: str) -> str:
    """
    Sanitize a single path segment:
    - URL decode
    - Strip whitespace
    - Collapse accidental multi-dots
    - Remove/replace characters that are problematic on common filesystems
    - Neutralize '.' and '..' segments (avoid traversal)
    """
    segment = unquote(segment).strip()
    segment = segment.strip(" .")  # Windows: trailing dot/space are problematic
    segment = _MULTI_DOTS_RE.sub(".", segment)
    segment = _BAD_SEG_CHARS_RE.sub("_", segment)

    if segment in ("", ".", ".."):
        segment = "_"

    if segment.upper() in _WINDOWS_RESERVED_NAMES:
        segment = f"_{segment}_"

    return segment


def _shorten_segment(segment: str, limit: int = MAX_SEG_LEN) -> str:
    """
    Shorten a single path segment if over limit.
    Preserve extension; append a short hash to keep it unique.
    """
    if len(segment) <= limit:
        return segment
    p = Path(segment)
    stem, suffix = p.stem, p.suffix
    h = sha256(segment.encode("utf-8")).hexdigest()[:12]
    # leave room for '-' + hash + suffix
    keep = max(0, limit - len(suffix) - 13)
    return f"{stem[:keep]}-{h}{suffix}"


def _rel_url(target: Path, base_dir: Path) -> str:
    """
    Compute a *URL* relative path (always forward slashes), not an OS path.
    """
    try:
        rel = os.path.relpath(target, base_dir)
    except ValueError:
        return target.as_posix()
    return Path(rel).as_posix()


def to_local_path(parsed: ParseResult, site_root: Path) -> Path:
    """
    Map an internal *page* URL to a local file path under site_root.

    - Adds 'index.html' where appropriate.
    - Converts extensionless paths to '.html'.
    - Appends a short query-hash when ?query is present to avoid collisions.
    - Enforces per-segment and overall path length limits. If still too long,
      hashes the leaf name.
    """
    rel = parsed.path.lstrip("/")
    if not rel:
        rel = "index.html"
    elif rel.endswith("/"):
        rel += "index.html"
    elif not Path(rel).suffix:
        rel += ".html"

    if parsed.query:
        qh = sha256(parsed.query.encode("utf-8")).hexdigest()[:10]
        p = Path(rel)
        rel = str(p.with_name(f"{p.stem}-q{qh}{p.suffix}"))

    # Sanitize and shorten individual segments
    parts = Path(rel).parts
    parts = tuple(_sanitize_segment(seg) for seg in parts)
    parts = tuple(_shorten_segment(seg, MAX_SEG_LEN) for seg in parts)
    local_path = site_root / Path(*parts)

    # If full path is still too long, hash the leaf
    if len(str(local_path)) > MAX_PATH_LEN:
        p = local_path
        h = sha256(parsed.geturl().encode("utf-8")).hexdigest()[:16]
        leaf = _shorten_segment(f"{p.stem}-{h}{p.suffix}", MAX_SEG_LEN)
        local_path = p.with_name(leaf)

    return local_path


def to_local_asset_path(parsed: ParseResult, site_root: Path) -> Path:
    """
    Map an internal *asset* URL to a local file path under site_root.

    Unlike to_local_path(), this does NOT force `.html` for extensionless paths.
    """
    rel = parsed.path.lstrip("/")
    if not rel:
        rel = "index"
    elif rel.endswith("/"):
        rel += "index"

    if parsed.query:
        qh = sha256(parsed.query.encode("utf-8")).hexdigest()[:10]
        p = Path(rel)
        # keep suffix if present, otherwise keep the full name
        name = f"{p.stem}-q{qh}{p.suffix}" if p.suffix else f"{p.name}-q{qh}"
        rel = str(p.with_name(name))

    parts = Path(rel).parts
    parts = tuple(_sanitize_segment(seg) for seg in parts)
    parts = tuple(_shorten_segment(seg, MAX_SEG_LEN) for seg in parts)
    local_path = site_root / Path(*parts)

    if len(str(local_path)) > MAX_PATH_LEN:
        p = local_path
        h = sha256(parsed.geturl().encode("utf-8")).hexdigest()[:16]
        leaf = _shorten_segment(f"{p.stem}-{h}{p.suffix}", MAX_SEG_LEN)
        local_path = p.with_name(leaf)

    return local_path


def cdn_local_path(parsed: ParseResult, site_root: Path) -> Path:
    """
    Map an external (CDN) URL to a local path under `site_root/cdn/<netloc>/...`.
    """
    rel = parsed.path.lstrip("/")
    if not rel:
        rel = "index"
    elif rel.endswith("/"):
        rel += "index"

    if parsed.query:
        qh = sha256(parsed.query.encode("utf-8")).hexdigest()[:10]
        p = Path(rel)
        name = f"{p.stem}-q{qh}{p.suffix}" if p.suffix else f"{p.name}-q{qh}"
        rel = str(p.with_name(name))

    parts = Path(rel).parts
    parts = tuple(_sanitize_segment(seg) for seg in parts)
    parts = tuple(_shorten_segment(seg, MAX_SEG_LEN) for seg in parts)

    netloc = _canonical_netloc(parsed)
    local_path = site_root / "cdn" / _sanitize_segment(netloc) / Path(*parts)

    if len(str(local_path)) > MAX_PATH_LEN:
        p = local_path
        h = sha256(parsed.geturl().encode("utf-8")).hexdigest()[:16]
        leaf = _shorten_segment(f"{p.stem}-{h}{p.suffix}", MAX_SEG_LEN)
        local_path = p.with_name(leaf)

    return local_path


def safe_write_text(path: Path, text: str, encoding: str = "utf-8") -> Path:
    """
    Write text to path, falling back to a hashed filename if OS rejects it
    (e.g., filename too long). Returns the final path used.
    """
    try:
        path.write_text(text, encoding=encoding)
        return path
    except OSError as exc:
        log.warning("Write failed for %s: %s. Falling back to hashed leaf.", path, exc)
        p = path
        h = sha256(str(p).encode("utf-8")).hexdigest()[:16]
        fallback = p.with_name(_shorten_segment(f"{p.stem}-{h}{p.suffix}", MAX_SEG_LEN))
        create_dir(fallback.parent)
        fallback.write_text(text, encoding=encoding)
        return fallback


def normalize_url(url: str) -> str:
    """Normalize URLs to avoid duplicates caused by fragments."""
    parsed = urlparse(url)
    clean = parsed._replace(fragment="")
    return clean.geturl()


def _protocol_fix(url: str, base_url: str) -> str:
    """Normalize protocol-relative URLs (//host/path) to absolute ones."""
    if url.startswith("//"):
        base = urlparse(base_url)
        scheme = base.scheme or "https"
        return f"{scheme}:{url}"
    return url


def rewrite_css_text(
    css_text: str,
    base_url: str,
    *,
    site_root: Path,
    root_netloc: str,
    base_dir: Path,
    download_external_assets: bool,
    download_q: Optional[queue.Queue[tuple[str, Path]]] = None,
) -> str:
    """
    Rewrite CSS url(...) and @import references to local relative paths.

    - base_url: the remote URL of the CSS *context* (stylesheet URL for external CSS,
      page URL for <style> blocks / style="" attributes).
    - base_dir: local directory that will contain the CSS (used for rel path output).
    """

    def map_one(url_part: str) -> Optional[str]:
        url_part = url_part.strip()

        if not url_part:
            return None
        if url_part.startswith("#"):
            return None
        if url_part.startswith(("data:", "javascript:", "about:")):
            return None

        url_part2 = _protocol_fix(url_part, base_url)
        if is_non_fetchable(url_part2) or not is_httpish(url_part2):
            return None

        abs_url = canonicalize_url(url_part2, base_url)
        parsed = urlparse(abs_url)
        if not parsed.path:
            return None

        # Be conservative: only rewrite things that look like static assets.
        if not parsed.path.lower().endswith(ASSET_EXTENSIONS):
            return None

        is_ext = not is_internal(abs_url, root_netloc)
        if is_ext and not download_external_assets:
            return None

        local_path = cdn_local_path(parsed, site_root) if is_ext else to_local_asset_path(parsed, site_root)
        if download_q is not None and not local_path.exists():
            log.debug("Queue asset (rewrite): %s -> %s", abs_url, local_path)
            download_q.put((abs_url, local_path))

        rel = _rel_url(local_path, base_dir)
        if parsed.fragment:
            rel = f"{rel}#{parsed.fragment}"
        return rel

    def repl_url(m: re.Match) -> str:
        raw = m.group(1).strip()
        quote = ""
        url_part = raw

        if len(raw) >= 2 and raw[0] in ("'", '"') and raw[-1] == raw[0]:
            quote = raw[0]
            url_part = raw[1:-1].strip()

        mapped = map_one(url_part)
        if mapped is None:
            return m.group(0)

        if quote:
            return f"url({quote}{mapped}{quote})"
        return f"url({mapped})"

    def repl_import(m: re.Match) -> str:
        url_part = m.group(1).strip().strip("'\"")
        mapped = map_one(url_part)
        if mapped is None:
            return m.group(0)
        return f'@import "{mapped}";'

    css_text = CSS_URL_RE.sub(repl_url, css_text)
    css_text = CSS_IMPORT_RE.sub(repl_import, css_text)
    return css_text


def rewrite_js_text(
    js_text: str,
    base_url: str,
    *,
    site_root: Path,
    root_netloc: str,
    base_dir: Path,
    download_external_assets: bool,
    download_q: Optional[queue.Queue[tuple[str, Path]]] = None,
) -> str:
    """
    Rewrite obvious static asset URL strings inside JS.

    This intentionally stays conservative: it rewrites only string literals that
    point to common static-asset extensions (JS_URL_RE / JS_ABS_URL_RE).
    """

    def map_one(url_part: str) -> Optional[str]:
        url_part = url_part.strip()

        if not url_part:
            return None
        if url_part.startswith("#"):
            return None
        if url_part.startswith(("data:", "javascript:", "about:")):
            return None

        url_part2 = _protocol_fix(url_part, base_url)
        if is_non_fetchable(url_part2) or not is_httpish(url_part2):
            return None

        abs_url = canonicalize_url(url_part2, base_url)
        parsed = urlparse(abs_url)

        if not parsed.path.lower().endswith(ASSET_EXTENSIONS):
            return None

        is_ext = not is_internal(abs_url, root_netloc)
        if is_ext and not download_external_assets:
            return None

        local_path = cdn_local_path(parsed, site_root) if is_ext else to_local_asset_path(parsed, site_root)
        if download_q is not None and not local_path.exists():
            log.debug("Queue asset (rewrite): %s -> %s", abs_url, local_path)
            download_q.put((abs_url, local_path))

        rel = _rel_url(local_path, base_dir)
        if parsed.fragment:
            rel = f"{rel}#{parsed.fragment}"
        return rel

    def repl_root_rel(m: re.Match) -> str:
        url_part = m.group(1)
        mapped = map_one(url_part)
        if mapped is None:
            return m.group(0)
        quote = m.group(0)[0]
        return f'{quote}{mapped}{quote}'

    def repl_abs(m: re.Match) -> str:
        url_part = m.group(1)
        mapped = map_one(url_part)
        if mapped is None:
            return m.group(0)
        quote = m.group(0)[0]
        return f'{quote}{mapped}{quote}'

    js_text = JS_URL_RE.sub(repl_root_rel, js_text)
    js_text = JS_ABS_URL_RE.sub(repl_abs, js_text)
    return js_text


def _canonical_netloc(parsed: ParseResult) -> str:
    """
    Lowercase hostname and drop default ports so we don't create different
    local folders for the same CDN host.
    """
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if not host:
        return parsed.netloc.lower()

    # Drop default ports
    if (parsed.scheme == "https" and port == 443) or (parsed.scheme == "http" and port == 80):
        port = None

    return f"{host}:{port}" if port else host


def canonicalize_url(url: str, base_url: str = "") -> str:
    """
    Produce a stable absolute URL key for de-duping + mapping.
    - Fix protocol-relative URLs
    - Join relative URLs to base_url if provided
    - Drop fragments
    - Normalize host casing + default ports
    """
    if base_url:
        url = urljoin(base_url, _protocol_fix(url, base_url))
    else:
        url = _protocol_fix(url, url)

    p = urlparse(url)
    # If still relative, keep as-is (caller can decide)
    if not p.scheme and not p.netloc:
        p = urlparse(urljoin(base_url, url)) if base_url else p

    netloc = _canonical_netloc(p) if p.netloc else ""
    p = p._replace(fragment="", netloc=netloc)
    return p.geturl()


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------


def fetch_html(url: str) -> Optional[BeautifulSoup]:
    """Download url and return a BeautifulSoup tree (or None on error)."""
    try:
        resp = SESSION.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:  # noqa: BLE001
        log.warning("HTTP error for %s – %s", url, exc)
        return None


def fetch_binary(
    url: str,
    dest: Path,
    download_q: Optional[queue.Queue[tuple[str, Path]]] = None,
    *,
    site_root: Optional[Path] = None,
    root_netloc: str = "",
    download_external_assets: bool = False,
) -> None:
    """Stream URL to dest unless it already exists."""

    if dest.exists():
        return

    try:
        resp = SESSION.get(url, timeout=TIMEOUT, stream=True)
        resp.raise_for_status()

        create_dir(dest.parent)

        try:
            with dest.open("wb") as fh:
                for chunk in resp.iter_content(CHUNK_SIZE):
                    if chunk:
                        fh.write(chunk)
            log.debug("Saved resource -> %s", dest)

        except OSError as exc:
            log.warning("Binary write failed for %s: %s. Using fallback.", dest, exc)

            h = sha256(str(dest).encode("utf-8")).hexdigest()[:16]
            fallback = dest.with_name(_shorten_segment(f"{dest.stem}-{h}{dest.suffix}", MAX_SEG_LEN))
            create_dir(fallback.parent)

            with fallback.open("wb") as fh:
                for chunk in resp.iter_content(CHUNK_SIZE):
                    if chunk:
                        fh.write(chunk)

            log.debug("Saved resource (fallback) -> %s", fallback)
            dest = fallback

        # Rewrite CSS + enqueue referenced assets
        if (
            dest.suffix.lower() == ".css"
            and download_q is not None
            and site_root is not None
            and root_netloc
        ):
            try:
                css_text = dest.read_text(encoding="utf-8", errors="ignore")
                rewritten = rewrite_css_text(
                    css_text,
                    url,
                    site_root=site_root,
                    root_netloc=root_netloc,
                    base_dir=dest.parent,
                    download_external_assets=download_external_assets,
                    download_q=download_q,
                )
                if rewritten != css_text:
                    dest.write_text(rewritten, encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                log.debug("CSS rewrite failed for %s – %s", dest, exc)

        # Rewrite JS + enqueue referenced assets
        if (
            dest.suffix.lower() in {".js", ".mjs"}
            and download_q is not None
            and site_root is not None
            and root_netloc
        ):
            try:
                js_text = dest.read_text(encoding="utf-8", errors="ignore")
                rewritten = rewrite_js_text(
                    js_text,
                    url,
                    site_root=site_root,
                    root_netloc=root_netloc,
                    base_dir=dest.parent,
                    download_external_assets=download_external_assets,
                    download_q=download_q,
                )
                if rewritten != js_text:
                    dest.write_text(rewritten, encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                log.debug("JS rewrite failed for %s – %s", dest, exc)

    except Exception as exc:  # noqa: BLE001
        log.error("Failed to save %s – %s", url, exc)


# ---------------------------------------------------------------------------
# Link rewriting
# ---------------------------------------------------------------------------


def rewrite_links(
    soup: BeautifulSoup,
    page_url: str,
    site_root: Path,
    page_dir: Path,
    download_external_assets: bool = False,
) -> None:
    """Rewrite links so internal + optional CDN assets point to local files."""

    root_netloc = _canonical_netloc(urlparse(page_url))

    # Remove <base href="..."> because it affects how browsers resolve relative URLs
    base_tag = soup.find("base")
    if base_tag is not None and base_tag.has_attr("href"):
        base_tag.decompose()

    url_attrs = {"src", "href", "data-src", "poster"}

    for tag in soup.find_all(True):

        # Keep <link> rewriting for rel that actually fetches assets.
        if tag.name == "link":
            rel = tag.get("rel", [])
            if isinstance(rel, str):
                rel = [rel]

            rel = [r.lower() for r in rel]
            if not any(r in rel for r in ("stylesheet", "icon", "preload", "modulepreload")):
                continue

        for attr in url_attrs:
            if not tag.has_attr(attr):
                continue

            original_raw = str(tag.get(attr, "")).strip()
            if not original_raw:
                continue

            original = _protocol_fix(original_raw, page_url)

            if original.startswith("#") or is_non_fetchable(original) or not is_httpish(original):
                continue

            abs_url = canonicalize_url(original, page_url)
            parsed = urlparse(abs_url)

            is_ext = not is_internal(abs_url, root_netloc)
            if is_ext and not download_external_assets:
                continue

            treat_as_page = (tag.name == "a" and attr == "href")

            if is_ext:
                # Do not rewrite CDN links in HTML — keep original URLs
                continue
            else:
                local_path = to_local_path(parsed, site_root) if treat_as_page else to_local_asset_path(parsed,
                                                                                                        site_root)

            rel = _rel_url(local_path, page_dir)
            if parsed.fragment:
                rel = f"{rel}#{parsed.fragment}"
            tag[attr] = rel

        # srcset rewriting
        if tag.has_attr("srcset"):
            new_entries = []
            for entry in str(tag["srcset"]).split(","):
                entry = entry.strip()
                if not entry:
                    continue
                parts = entry.split()
                url_part = _protocol_fix(parts[0], page_url)
                if url_part.startswith("#") or is_non_fetchable(url_part) or not is_httpish(url_part):
                    new_entries.append(entry)
                    continue

                abs_url = normalize_url(canonicalize_url(url_part, page_url))
                parsed = urlparse(abs_url)

                is_ext = not is_internal(abs_url, root_netloc)
                if is_ext and not download_external_assets:
                    new_entries.append(entry)
                    continue

                local_path = cdn_local_path(parsed, site_root) if is_ext else to_local_asset_path(parsed, site_root)
                rel = _rel_url(local_path, page_dir)
                if parsed.fragment:
                    rel = f"{rel}#{parsed.fragment}"

                parts[0] = rel
                new_entries.append(" ".join(parts))
            tag["srcset"] = ", ".join(new_entries)

        # inline style rewriting
        if tag.has_attr("style"):
            style = str(tag["style"])

            def repl_style(m: re.Match) -> str:
                raw = m.group(1).strip()
                quote = ""
                url_part = raw
                if len(raw) >= 2 and raw[0] in ("'", '"') and raw[-1] == raw[0]:
                    quote = raw[0]
                    url_part = raw[1:-1].strip()

                if (
                    not url_part
                    or url_part.startswith("#")
                    or url_part.startswith(("data:", "javascript:", "about:"))
                ):
                    return m.group(0)

                url_part2 = _protocol_fix(url_part, page_url)
                if is_non_fetchable(url_part2) or not is_httpish(url_part2):
                    return m.group(0)

                abs_url = canonicalize_url(url_part2, page_url)
                parsed = urlparse(abs_url)

                if not parsed.path.lower().endswith(ASSET_EXTENSIONS):
                    return m.group(0)

                is_ext = not is_internal(abs_url, root_netloc)
                if is_ext and not download_external_assets:
                    return m.group(0)

                local_path = cdn_local_path(parsed, site_root) if is_ext else to_local_asset_path(parsed, site_root)
                rel = _rel_url(local_path, page_dir)
                if parsed.fragment:
                    rel = f"{rel}#{parsed.fragment}"

                if quote:
                    return f"url({quote}{rel}{quote})"
                return f"url({rel})"

            style = CSS_URL_RE.sub(repl_style, style)
            tag["style"] = style

    # rewrite <style> blocks too
    for style_tag in soup.find_all("style"):
        try:
            css_text = style_tag.string or style_tag.get_text()
            if not css_text:
                continue
            rewritten = rewrite_css_text(
                css_text,
                page_url,
                site_root=site_root,
                root_netloc=root_netloc,
                base_dir=page_dir,
                download_external_assets=download_external_assets,
                download_q=None,
            )
            if rewritten != css_text:
                style_tag.string = rewritten
        except Exception as exc:  # noqa: BLE001
            log.debug("Inline <style> rewrite failed on %s – %s", page_url, exc)


# ---------------------------------------------------------------------------
# Crawl coordinator
# ---------------------------------------------------------------------------


def extract_css_assets(css_text: str) -> list[str]:
    """Extract asset URLs from CSS url(...) and @import patterns safely."""
    results: list[str] = []

    for match in CSS_URL_RE.findall(css_text):
        url = match.strip().strip("'\"")
        if not url or url.startswith(("data:", "javascript:", "about:", "#")):
            continue
        results.append(url)

    for match in CSS_IMPORT_RE.findall(css_text):
        url = match.strip().strip("'\"")
        if not url or url.startswith(("data:", "javascript:", "about:", "#")):
            continue
        results.append(url)

    return results


def crawl_site(
    start_url: str,
    root: Path,
    max_pages: int,
    threads: int,
    download_external_assets: bool = False,
) -> None:
    """Breadth-first crawl limited to max_pages. Downloads assets via workers."""
    q_pages: queue.Queue[str] = queue.Queue()
    q_pages.put(start_url)

    seen_pages: set[str] = set()
    queued_pages: set[str] = {start_url}
    queued_assets: set[str] = set()
    download_q: queue.Queue[tuple[str, Path]] = queue.Queue()

    root_netloc = _canonical_netloc(urlparse(start_url))

    def worker() -> None:
        while True:
            url, dest = download_q.get()
            try:
                if is_non_fetchable(url) or not is_httpish(url):
                    log.debug("Skip non-fetchable: %s", url)
                    continue
                fetch_binary(
                    url,
                    dest,
                    download_q,
                    site_root=root,
                    root_netloc=root_netloc,
                    download_external_assets=download_external_assets,
                )
            finally:
                download_q.task_done()

    for i in range(max(1, threads)):
        t = threading.Thread(target=worker, name=f"DL-{i+1}", daemon=True)
        t.start()

    start_time = time.time()
    PAGE_SUFFIXES = {"", ".html", ".htm"}

    while not q_pages.empty() and len(seen_pages) < max_pages:
        page_url = canonicalize_url(q_pages.get())
        if page_url in seen_pages:
            continue
        seen_pages.add(page_url)
        log.info("[%s/%s] %s", len(seen_pages), max_pages, page_url)

        soup = fetch_html(page_url)
        if soup is None:
            continue

        # Gather links & assets (including srcset + inline CSS urls)
        for tag in soup.find_all(True):
            for attr in ("src", "href", "data-src", "poster"):
                if not tag.has_attr(attr):
                    continue
                link_raw = str(tag.get(attr, "")).strip()
                if not link_raw:
                    continue

                link = _protocol_fix(link_raw, page_url)
                if link.startswith("#") or is_non_fetchable(link) or not is_httpish(link):
                    continue

                abs_url = normalize_url(canonicalize_url(link, page_url))
                parsed = urlparse(abs_url)
                is_ext = not is_internal(abs_url, root_netloc)

                # Pages: only crawl internal pages from <a href=...>
                suffix = Path(parsed.path).suffix.lower()
                is_page = (
                    tag.name == "a"
                    and not is_ext
                    and (parsed.path.endswith("/") or suffix in PAGE_SUFFIXES)
                )

                if is_page:
                    if abs_url not in seen_pages and abs_url not in queued_pages:
                        q_pages.put(abs_url)
                        queued_pages.add(abs_url)
                    continue

                # Assets
                if is_ext:
                    if not download_external_assets:
                        continue

                    # allow scripts and styles without extensions
                    if tag.name not in ("script", "link") and not parsed.path.lower().endswith(ASSET_EXTENSIONS):
                        continue

                    dest_path = cdn_local_path(parsed, root)
                else:
                    dest_path = to_local_asset_path(parsed, root)

                if abs_url not in queued_assets:
                    queued_assets.add(abs_url)
                    create_dir(dest_path.parent)
                    log.debug("Queue asset: %s -> %s", abs_url, dest_path)
                    download_q.put((abs_url, dest_path))

            # srcset candidates
            if tag.has_attr("srcset"):
                for entry in str(tag["srcset"]).split(","):
                    entry = entry.strip()
                    if not entry:
                        continue
                    url_part = _protocol_fix(entry.split()[0], page_url)
                    if url_part.startswith("#") or is_non_fetchable(url_part) or not is_httpish(url_part):
                        continue

                    abs_url = normalize_url(canonicalize_url(url_part, page_url))
                    parsed = urlparse(abs_url)
                    is_ext = not is_internal(abs_url, root_netloc)

                    if is_ext:
                        if not download_external_assets:
                            continue
                        if not parsed.path.lower().endswith(ASSET_EXTENSIONS):
                            continue
                        dest_path = cdn_local_path(parsed, root)
                    else:
                        dest_path = to_local_asset_path(parsed, root)

                    if abs_url not in queued_assets:
                        queued_assets.add(abs_url)
                        create_dir(dest_path.parent)
                        log.debug("Queue asset: %s -> %s", abs_url, dest_path)
                        download_q.put((abs_url, dest_path))

            # inline style="...url(...)..."
            if tag.has_attr("style"):
                style = str(tag["style"])
                for match in CSS_URL_RE.findall(style):
                    url_part = _protocol_fix(match.strip().strip("'\""), page_url)
                    if (
                        not url_part
                        or url_part.startswith("#")
                        or url_part.startswith(("data:", "javascript:", "about:"))
                        or is_non_fetchable(url_part)
                        or not is_httpish(url_part)
                    ):
                        continue

                    abs_url = normalize_url(canonicalize_url(url_part, page_url))
                    parsed = urlparse(abs_url)
                    if not parsed.path.lower().endswith(ASSET_EXTENSIONS):
                        continue

                    is_ext = not is_internal(abs_url, root_netloc)
                    if is_ext and not download_external_assets:
                        continue

                    dest_path = cdn_local_path(parsed, root) if is_ext else to_local_asset_path(parsed, root)
                    if abs_url not in queued_assets:
                        queued_assets.add(abs_url)
                        create_dir(dest_path.parent)
                        log.debug("Queue asset: %s -> %s", abs_url, dest_path)
                        download_q.put((abs_url, dest_path))

            # <style> blocks
            if tag.name == "style":
                css_text = tag.string or tag.get_text()
                if not css_text:
                    continue
                for asset in extract_css_assets(css_text):
                    asset = _protocol_fix(asset, page_url)
                    if (
                        not asset
                        or asset.startswith("#")
                        or asset.startswith(("data:", "javascript:", "about:"))
                        or is_non_fetchable(asset)
                        or not is_httpish(asset)
                    ):
                        continue

                    abs_url = canonicalize_url(asset, page_url)
                    parsed = urlparse(abs_url)

                    if not parsed.path.lower().endswith(ASSET_EXTENSIONS):
                        continue

                    is_ext = not is_internal(abs_url, root_netloc)
                    if is_ext and not download_external_assets:
                        continue

                    dest_path = cdn_local_path(parsed, root) if is_ext else to_local_asset_path(parsed, root)
                    if abs_url not in queued_assets:
                        queued_assets.add(abs_url)
                        create_dir(dest_path.parent)
                        log.debug("Queue asset: %s -> %s", abs_url, dest_path)
                        download_q.put((abs_url, dest_path))

        # Save current page
        local_path = to_local_path(urlparse(page_url), root)
        create_dir(local_path.parent)
        rewrite_links(soup, page_url, root, local_path.parent, download_external_assets)
        safe_write_text(local_path, str(soup), encoding="utf-8")

    download_q.join()
    elapsed = time.time() - start_time
    if seen_pages:
        log.info(
            "Crawl finished: %s pages in %.2fs (%.2fs avg)",
            len(seen_pages),
            elapsed,
            elapsed / len(seen_pages),
        )
    else:
        log.warning("Nothing downloaded – check URL or connectivity")


# ---------------------------------------------------------------------------
# Helper function for output folder
# ---------------------------------------------------------------------------


def make_root(url: str, custom: Optional[str]) -> Path:
    """Derive output folder from URL if custom not supplied."""
    return Path(custom) if custom else Path(urlparse(url).netloc.replace(".", "_"))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Recursively mirror a website for offline use.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--url",
        required=True,
        help="Starting URL to crawl (e.g., https://example.com/).",
    )
    p.add_argument(
        "--destination",
        default=None,
        help="Output folder (defaults to a folder derived from the URL).",
    )
    p.add_argument(
        "--max-pages",
        type=int,
        default=50,
        help="Maximum number of HTML pages to crawl.",
    )
    p.add_argument(
        "--threads",
        type=int,
        default=6,
        help="Number of concurrent download workers.",
    )
    p.add_argument(
        "--download-external-assets",
        action="store_true",
        help="Download external CDN/static assets and rewrite links for offline use.",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.max_pages < 1:
        log.error("--max-pages must be >= 1")
        sys.exit(2)
    if args.threads < 1:
        log.error("--threads must be >= 1")
        sys.exit(2)

    host = args.url
    root = make_root(args.url, args.destination)
    crawl_site(
        host,
        root,
        args.max_pages,
        args.threads,
        args.download_external_assets,
    )
