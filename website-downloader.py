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
from importlib.util import find_spec
from pathlib import Path
from typing import Optional
from urllib.parse import ParseResult, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

HAS_BROTLI = find_spec("brotli") is not None or find_spec("brotlicffi") is not None

# ---------------------------------------------------------------------------
# Config / constants
# ---------------------------------------------------------------------------

# Consistent log format across file + console. Thread name is helpful because
# asset downloads happen in worker threads.
LOG_FMT = "%(asctime)s | %(levelname)-8s | %(threadName)s | %(message)s"

# Extensions we treat as “static assets” worth downloading and rewriting.
# Used in multiple places: HTML attribute rewriting, CSS url(...) rewriting,
# JS string rewriting, and crawl-time asset detection.
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

# Conservative JS string rewriting:
# - JS_URL_RE: matches root-relative strings like "/assets/app.js"
# - JS_ABS_URL_RE: matches absolute or protocol-relative strings like
#   "https://cdn.example.com/app.js" or "//cdn.example.com/app.js"
#
# This is intentionally limited to common static file extensions to avoid
# rewriting API endpoints or dynamic URLs that could break functionality.
JS_URL_RE = re.compile(
    r"""["'](/[^"']+\.(?:png|jpg|jpeg|gif|svg|webp|avif|ico|css|js|mjs|map|woff|woff2|ttf|eot|json|wasm|webmanifest)(?:\?[^"']*)?)["']""",
    re.IGNORECASE,
)

JS_ABS_URL_RE = re.compile(
    r"""["']((?:https?:)?//[^"']+\.(?:png|jpg|jpeg|gif|svg|webp|avif|ico|css|js|mjs|map|woff|woff2|ttf|eot|json|wasm|webmanifest)(?:\?[^"']*)?)["']""",
    re.IGNORECASE,
)

# Default headers can help with sites that block "non-browser" clients.
_ACCEPT_ENCODING = "gzip, deflate, br" if HAS_BROTLI else "gzip, deflate"

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
    "Accept-Encoding": _ACCEPT_ENCODING,
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# Network timeouts + streaming chunk size for binary downloads.
TIMEOUT = 15  # seconds
CHUNK_SIZE = 8192  # bytes

# Conservative margins under common OS limits (~255–260 bytes).
# These protect you from “File name too long” and odd Windows path rules.
MAX_PATH_LEN = 240
MAX_SEG_LEN = 120

# Collapse 3+ dots ("....") down to a single dot to avoid weird filenames.
_MULTI_DOTS_RE = re.compile(r"\.{3,}")

# CSS url(...) extractor. Note: this is simple (not a full CSS parser),
# but good enough for most sites.
CSS_URL_RE = re.compile(r"url\(([^)]+)\)")

# CSS @import extractor. Also simple-but-effective.
CSS_IMPORT_RE = re.compile(
    r"""@import\s+(?:url\()?['"]?([^'"\);]+)['"]?\)?\s*;""",
    re.IGNORECASE,
)

# Characters that commonly cause filesystem issues, especially on Windows.
_BAD_SEG_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1F]')

# Windows reserved filenames; writing these can fail or behave badly.
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

RESOURCE_LINK_RELS = {
    "stylesheet",
    "icon",
    "shortcut",
    "apple-touch-icon",
    "preload",
    "modulepreload",
    "manifest",
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

# File logging is DEBUG to help you trace rewrites and queue behavior.
logging.basicConfig(
    filename="web_scraper.log",
    level=logging.DEBUG,
    format=LOG_FMT,
    datefmt="%H:%M:%S",
    force=True,
)

# Console logging is INFO to keep output readable while running.
_console = logging.StreamHandler(sys.stdout)
_console.setLevel(logging.INFO)
_console.setFormatter(logging.Formatter(LOG_FMT, datefmt="%H:%M:%S"))
logging.getLogger().addHandler(_console)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTTP session (retry, timeouts, custom UA)
# ---------------------------------------------------------------------------

# Shared session improves performance and keeps connection pooling.
SESSION = requests.Session()

# Retry strategy for transient issues (rate limits, 5xx). Helps stability.
RETRY_STRAT = Retry(
    total=5,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET", "HEAD"],
)

SESSION.mount("http://", HTTPAdapter(max_retries=RETRY_STRAT))
SESSION.mount("https://", HTTPAdapter(max_retries=RETRY_STRAT))
SESSION.headers.update(DEFAULT_HEADERS)
log.debug("Accept-Encoding configured as: %s", SESSION.headers.get("Accept-Encoding"))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def create_dir(path: Path) -> None:
    """Create path (and parents) if it does not already exist."""
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
        log.debug("Created directory %s", path)


# Schemes that are valid URLs in HTML but are not HTTP fetch targets.
# If we try to request these, requests will throw InvalidSchema.
NON_FETCHABLE_SCHEMES = {
    "mailto",
    "tel",
    "sms",
    "javascript",
    "data",
    "geo",
    "blob",
    "about",
}


def is_httpish(u: str) -> bool:
    """
    True iff the URL is http(s) or relative (no scheme).

    Why:
    - We only fetch http(s) resources.
    - Relative URLs should still be handled because we can join them to base URLs.
    """
    p = urlparse(u)
    return (p.scheme in ("http", "https")) or (p.scheme == "")


def is_non_fetchable(u: str) -> bool:
    """
    True iff the URL clearly shouldn't be fetched (mailto:, tel:, data:, ...).
    """
    p = urlparse(u)
    return p.scheme in NON_FETCHABLE_SCHEMES


def is_internal(link: str, root_netloc: str) -> bool:
    """
    Decide whether `link` belongs to the same site as `root_netloc`.

    Notes:
    - Relative URLs are internal.
    - We normalize "www." so example.com and www.example.com count as same.
    """
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
    Sanitize a single path segment for safe writing to disk.

    - URL decode (turn %20 into space, etc.)
    - Strip whitespace / trailing dot-space combos (Windows issues)
    - Collapse accidental multi-dots
    - Replace illegal filesystem chars with '_'
    - Neutralize '.' and '..' to prevent traversal-like paths
    - Avoid Windows reserved names (CON, PRN, COM1, ...)
    """
    segment = unquote(segment).strip()
    segment = segment.strip(" .")
    segment = _MULTI_DOTS_RE.sub(".", segment)
    segment = _BAD_SEG_CHARS_RE.sub("_", segment)

    if segment in ("", ".", ".."):
        segment = "_"

    if segment.upper() in _WINDOWS_RESERVED_NAMES:
        segment = f"_{segment}_"

    return segment


def _shorten_segment(segment: str, limit: int = MAX_SEG_LEN) -> str:
    """
    Shorten a path segment if it exceeds a length limit.

    Strategy:
    - Keep the original extension
    - Truncate the stem
    - Append a short hash so different long names don't collide
    """
    if len(segment) <= limit:
        return segment
    p = Path(segment)
    stem, suffix = p.stem, p.suffix
    h = sha256(segment.encode("utf-8")).hexdigest()[:12]
    keep = max(0, limit - len(suffix) - 13)  # '-' + hash is 13 chars total
    return f"{stem[:keep]}-{h}{suffix}"


def _rel_url(target: Path, base_dir: Path) -> str:
    """
    Compute a URL-style relative path (forward slashes),
    not an OS-specific path.
    """
    try:
        rel = os.path.relpath(target, base_dir)
    except ValueError:
        # Happens if paths are on different drives on Windows.
        return target.as_posix()
    return Path(rel).as_posix()


def to_local_path(parsed: ParseResult, site_root: Path) -> Path:
    """
    Map an internal *page* URL to a local HTML file under site_root.

    Rules:
    - "/" -> index.html
    - "/foo/" -> /foo/index.html
    - "/foo" (no extension) -> /foo.html
    - query strings get a short hash to prevent collisions:
      /page?id=1 and /page?id=2 should not overwrite each other
    - filesystem hardening: sanitize segments, limit segment length and overall path
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


def to_local_asset_path(parsed: ParseResult, site_root: Path) -> Path:
    """
    Map an internal *asset* URL to a local file path under site_root.

    Difference vs to_local_path():
    - We do NOT force .html for extensionless paths.
      (Some sites serve extensionless assets, though less common.)
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
    local_path = site_root / Path(*parts)

    if len(str(local_path)) > MAX_PATH_LEN:
        p = local_path
        h = sha256(parsed.geturl().encode("utf-8")).hexdigest()[:16]
        leaf = _shorten_segment(f"{p.stem}-{h}{p.suffix}", MAX_SEG_LEN)
        local_path = p.with_name(leaf)

    return local_path


def cdn_local_path(parsed: ParseResult, site_root: Path) -> Path:
    """
    Map an external (CDN) URL to a local path under:
        site_root/cdn/<netloc>/...

    Why:
    - Keeps external host assets separated from internal assets.
    - Avoids collisions where internal and external paths look similar.
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
    Write text to path safely.

    If the OS rejects the filename/path (often: path too long), we:
    - hash the leaf name
    - write to a fallback name
    - return the final path used
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
    """
    Normalize URLs to avoid duplicates caused by fragments.

    Example:
    - https://site/page#section1 and https://site/page#section2
      are the same document for our crawler.
    """
    parsed = urlparse(url)
    clean = parsed._replace(fragment="")
    return clean.geturl()


def _protocol_fix(url: str, base_url: str) -> str:
    """
    Normalize protocol-relative URLs (//host/path) to absolute ones.

    Browsers interpret //example.com/a.css as "use the current page scheme".
    We do the same using base_url's scheme.
    """
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
    external_domains: Optional[set[str]] = None,
    download_q: Optional[queue.Queue[tuple[str, Path]]] = None,
) -> str:
    """
    Rewrite CSS url(...) and @import references to local relative paths.

    base_url:
      - the remote URL of the CSS *context*
      - external stylesheet URL for downloaded .css
      - page URL for inline <style> blocks or style="..."

    base_dir:
      - local directory where this CSS lives (controls the relative path output)

    Also:
    - If download_q is provided, enqueue newly discovered assets referenced by CSS.
    """

    def map_one(url_part: str) -> Optional[str]:
        url_part = url_part.strip()

        # Skip empties / anchors / non-fetchable schemes.
        if not url_part:
            return None
        if url_part.startswith("#"):
            return None
        if url_part.startswith(("data:", "javascript:", "about:")):
            return None

        url_part2 = _protocol_fix(url_part, base_url)
        if is_non_fetchable(url_part2) or not is_httpish(url_part2):
            return None

        # Canonicalize to a stable absolute URL
        abs_url = canonicalize_url(url_part2, base_url)
        parsed = urlparse(abs_url)
        if not parsed.path:
            return None

        # Only rewrite things that look like static assets.
        # (Avoid rewriting API URLs accidentally.)
        if not parsed.path.lower().endswith(ASSET_EXTENSIONS):
            return None

        is_ext = not is_internal(abs_url, root_netloc)
        if is_ext and not is_allowed_external(abs_url, external_domains):
            return None

        if is_ext and not download_external_assets:
            return None

        # Decide where to store it locally
        local_path = (
            cdn_local_path(parsed, site_root)
            if is_ext
            else to_local_asset_path(parsed, site_root)
        )

        # Queue it for downloading if not already present
        if download_q is not None and not local_path.exists():
            log.debug("Queue asset (rewrite): %s -> %s", abs_url, local_path)
            download_q.put((abs_url, local_path))

        # Output a relative URL for the rewritten CSS
        rel = _rel_url(local_path, base_dir)
        if parsed.fragment:
            rel = f"{rel}#{parsed.fragment}"
        return rel

    # Replace url(...) references
    def repl_url(m: re.Match) -> str:
        raw = m.group(1).strip()
        quote = ""
        url_part = raw

        # Preserve quoting style if present
        if len(raw) >= 2 and raw[0] in ("'", '"') and raw[-1] == raw[0]:
            quote = raw[0]
            url_part = raw[1:-1].strip()

        mapped = map_one(url_part)
        if mapped is None:
            return m.group(0)

        if quote:
            return f"url({quote}{mapped}{quote})"
        return f"url({mapped})"

    # Replace @import references
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
    external_domains: Optional[set[str]] = None,
    download_q: Optional[queue.Queue[tuple[str, Path]]] = None,
) -> str:
    """
    Rewrite obvious static asset URL strings inside JS.

    Important:
    - This does NOT parse JS AST; it does simple regex matching on string literals.
    - It ONLY rewrites strings that look like static assets by extension.
    - This prevents accidentally rewriting API endpoints or app routes.
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
        if is_ext and not is_allowed_external(abs_url, external_domains):
            return None

        if is_ext and not download_external_assets:
            return None

        local_path = (
            cdn_local_path(parsed, site_root)
            if is_ext
            else to_local_asset_path(parsed, site_root)
        )
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
        return f"{quote}{mapped}{quote}"

    def repl_abs(m: re.Match) -> str:
        url_part = m.group(1)
        mapped = map_one(url_part)
        if mapped is None:
            return m.group(0)
        quote = m.group(0)[0]
        return f"{quote}{mapped}{quote}"

    js_text = JS_URL_RE.sub(repl_root_rel, js_text)
    js_text = JS_ABS_URL_RE.sub(repl_abs, js_text)
    return js_text


def _canonical_netloc(parsed: ParseResult) -> str:
    """
    Lowercase hostname and drop default ports so we don't create different
    local folders for the same host.

    Example:
      https://EXAMPLE.com:443/a.css -> example.com
    """
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if not host:
        return parsed.netloc.lower()

    if (parsed.scheme == "https" and port == 443) or (
        parsed.scheme == "http" and port == 80
    ):
        port = None

    return f"{host}:{port}" if port else host


def canonicalize_url(url: str, base_url: str = "") -> str:
    """
    Produce a stable absolute URL key for de-duping + mapping.

    Steps:
    - Fix protocol-relative URLs
    - Join relative URLs against base_url
    - Drop fragments (#...)
    - Normalize host casing + default ports
    """
    if base_url:
        url = urljoin(base_url, _protocol_fix(url, base_url))
    else:
        url = _protocol_fix(url, url)

    p = urlparse(url)

    # If still relative, join using base_url (when available).
    if not p.scheme and not p.netloc:
        p = urlparse(urljoin(base_url, url)) if base_url else p

    netloc = _canonical_netloc(p) if p.netloc else ""
    p = p._replace(fragment="", netloc=netloc)
    return p.geturl()


def is_allowed_external(url: str, allowed_domains: Optional[set[str]]) -> bool:
    if allowed_domains is None:
        return True

    host = (urlparse(url).hostname or "").lower()

    return any(host == d or host.endswith("." + d) for d in allowed_domains)


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------


def fetch_html(url: str) -> Optional[BeautifulSoup]:
    """
    Download an HTML page and return a BeautifulSoup tree.

    We return None on error so the crawler can continue on failures.
    """
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
    external_domains: Optional[set[str]] = None,
) -> None:
    """
    Stream a binary/static resource to disk.

    Notes:
    - If already exists, skip.
    - Writes using streaming so we don't keep big files in memory.
    - If the file is CSS or JS, rewrite embedded asset URLs and enqueue them.
    """
    is_ext = not is_internal(url, root_netloc)

    if is_ext:
        if not download_external_assets:
            log.debug("Blocked external (fetch disabled): %s", url)
            return

        if not is_allowed_external(url, external_domains):
            log.info("[BLOCKED EXT] %s", url)
            return

    if dest.exists():
        return

    try:
        resp = SESSION.get(url, timeout=TIMEOUT, stream=True)
        resp.raise_for_status()

        create_dir(dest.parent)

        # Try normal write
        try:
            with dest.open("wb") as fh:
                for chunk in resp.iter_content(CHUNK_SIZE):
                    if chunk:
                        fh.write(chunk)
            log.debug("Saved resource -> %s", dest)

        # If filesystem rejects it (path too long, invalid name), fallback
        except OSError as exc:
            log.warning("Binary write failed for %s: %s. Using fallback.", dest, exc)

            h = sha256(str(dest).encode("utf-8")).hexdigest()[:16]
            fallback = dest.with_name(
                _shorten_segment(f"{dest.stem}-{h}{dest.suffix}", MAX_SEG_LEN)
            )
            create_dir(fallback.parent)

            with fallback.open("wb") as fh:
                for chunk in resp.iter_content(CHUNK_SIZE):
                    if chunk:
                        fh.write(chunk)

            log.debug("Saved resource (fallback) -> %s", fallback)
            dest = fallback

        # If we downloaded CSS, rewrite its url(...) and @import references,
        # and enqueue referenced assets (images/fonts/etc).
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
                    external_domains=external_domains,
                    download_q=download_q,
                )
                if rewritten != css_text:
                    dest.write_text(rewritten, encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                log.debug("CSS rewrite failed for %s – %s", dest, exc)

        # If we downloaded JS, rewrite obvious static URL strings,
        # and enqueue referenced assets (only those matching ASSET_EXTENSIONS).
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
                    external_domains=external_domains,
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
    external_domains: Optional[set[str]] = None,
) -> None:
    """
    Rewrite HTML so it can be opened offline.

    Rules:
    - Internal page links (<a href>) become local HTML file paths.
    - Internal asset links (img/src, script/src, link/href, etc) become local asset paths.
    - External asset links are rewritten to local cdn/... paths when
        external downloading is enabled and the URL is allowed.
    - External page links (for example <a href="https://...">) are kept unchanged.
    - Remove <base href="..."> because it changes browser URL resolution offline.
    """
    root_netloc = _canonical_netloc(urlparse(page_url))

    # <base href> breaks relative paths when opening offline.
    base_tag = soup.find("base")
    if base_tag is not None and base_tag.has_attr("href"):
        base_tag.decompose()

    # Common attributes that contain URL-like values.
    url_attrs = {"src", "href", "data-src", "poster"}

    def strip_sri_and_cors(tag) -> None:
        for attr in ("integrity", "crossorigin"):
            if tag.has_attr(attr):
                del tag[attr]

    for tag in soup.find_all(True):

        # For <link>, only rewrite rel-types that are actually fetched by browsers.
        # This avoids rewriting <link rel="canonical"> or <link rel="alternate"> etc.
        if tag.name == "link":
            rel = tag.get("rel", [])
            if isinstance(rel, str):
                rel = [rel]
            rel = [r.lower() for r in rel]

            rel_set = set(rel)
            if not rel_set & RESOURCE_LINK_RELS:
                continue

        # ------------------------------------------------------------------
        # META IMAGE REWRITE (make og/twitter images local)
        # ------------------------------------------------------------------
        if tag.name == "meta":
            content = str(tag.get("content", "")).strip()
            prop = (tag.get("property") or tag.get("name") or "").lower()

            if content and ("og:image" in prop or "twitter:image" in prop):

                url_part = _protocol_fix(content, page_url)

                if (
                    not url_part
                    or url_part.startswith("#")
                    or url_part.startswith(("data:", "javascript:", "about:"))
                    or is_non_fetchable(url_part)
                    or not is_httpish(url_part)
                ):
                    continue

                abs_url = canonicalize_url(url_part, page_url)
                parsed = urlparse(abs_url)

                is_ext = not is_internal(abs_url, root_netloc)

                if is_ext:
                    if not download_external_assets:
                        continue
                    if not is_allowed_external(abs_url, external_domains):
                        continue

                # map to local path
                local_path = (
                    cdn_local_path(parsed, site_root)
                    if is_ext
                    else to_local_asset_path(parsed, site_root)
                )

                # rewrite to relative path
                rel = _rel_url(local_path, page_dir)
                tag["content"] = rel

        # Rewrite each URL attribute we care about
        for attr in url_attrs:
            if not tag.has_attr(attr):
                continue

            original_raw = str(tag.get(attr, "")).strip()
            if not original_raw:
                continue

            original = _protocol_fix(original_raw, page_url)

            # Skip anchors, non-fetchable schemes, and things that are not http(s)/relative.
            if (
                original.startswith("#")
                or is_non_fetchable(original)
                or not is_httpish(original)
            ):
                continue

            abs_url = canonicalize_url(original, page_url)
            parsed = urlparse(abs_url)

            is_ext = not is_internal(abs_url, root_netloc)
            if is_ext:
                if not download_external_assets:
                    continue
                if not is_allowed_external(abs_url, external_domains):
                    continue

            # Treat <a href> as a "page". Everything else is treated as an asset.
            treat_as_page = tag.name == "a" and attr == "href"

            rewritten_external_asset = False

            if is_ext and treat_as_page:
                continue

            if is_ext:
                if not download_external_assets:
                    continue
                if not is_allowed_external(abs_url, external_domains):
                    continue
                local_path = cdn_local_path(parsed, site_root)
                rewritten_external_asset = True
            else:
                local_path = (
                    to_local_path(parsed, site_root)
                    if treat_as_page
                    else to_local_asset_path(parsed, site_root)
                )

            rel = _rel_url(local_path, page_dir)
            if parsed.fragment:
                rel = f"{rel}#{parsed.fragment}"
            tag[attr] = rel

            if rewritten_external_asset and tag.name in {"script", "link"}:
                strip_sri_and_cors(tag)

        # srcset="url1 1x, url2 2x" needs special parsing
        if tag.has_attr("srcset"):
            new_entries = []
            for entry in str(tag["srcset"]).split(","):
                entry = entry.strip()
                if not entry:
                    continue

                parts = entry.split()
                url_part = _protocol_fix(parts[0], page_url)

                if (
                    url_part.startswith("#")
                    or is_non_fetchable(url_part)
                    or not is_httpish(url_part)
                ):
                    new_entries.append(entry)
                    continue

                abs_url = normalize_url(canonicalize_url(url_part, page_url))
                parsed = urlparse(abs_url)

                is_ext = not is_internal(abs_url, root_netloc)
                if is_ext:
                    if not download_external_assets:
                        new_entries.append(entry)
                        continue

                    if not is_allowed_external(abs_url, external_domains):
                        new_entries.append(entry)
                        continue

                    local_path = cdn_local_path(parsed, site_root)
                else:
                    local_path = to_local_asset_path(parsed, site_root)

                rel = _rel_url(local_path, page_dir)
                if parsed.fragment:
                    rel = f"{rel}#{parsed.fragment}"

                parts[0] = rel
                new_entries.append(" ".join(parts))

            tag["srcset"] = ", ".join(new_entries)

        # Inline style="background:url(...)" rewriting
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

                # Only rewrite things that look like assets.
                if not parsed.path.lower().endswith(ASSET_EXTENSIONS):
                    return m.group(0)

                is_ext = not is_internal(abs_url, root_netloc)
                if is_ext:
                    if not download_external_assets:
                        return m.group(0)

                    if not is_allowed_external(abs_url, external_domains):
                        return m.group(0)

                    local_path = cdn_local_path(parsed, site_root)
                else:
                    local_path = to_local_asset_path(parsed, site_root)

                rel = _rel_url(local_path, page_dir)
                if parsed.fragment:
                    rel = f"{rel}#{parsed.fragment}"

                if quote:
                    return f"url({quote}{rel}{quote})"
                return f"url({rel})"

            style = CSS_URL_RE.sub(repl_style, style)
            tag["style"] = style

    # Rewrite <style> blocks too (internal assets only; CDN kept unchanged here)
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
                external_domains=external_domains,
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
    """
    Extract asset URLs from CSS url(...) and @import patterns.

    This is used when scanning <style> blocks during HTML parse time
    (before the CSS is written to disk).
    """
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
    external_domains: Optional[set[str]] = None,
) -> None:
    """
    Breadth-first crawl limited to max_pages.

    - q_pages: pages to crawl (HTML only, internal-only)
    - download_q: assets to download (internal, and optionally external)
    - worker threads: process download_q and write to disk
    """
    q_pages: queue.Queue[str] = queue.Queue()
    q_pages.put(start_url)

    seen_pages: set[str] = set()
    queued_pages: set[str] = {start_url}

    # queued_assets ensures we don't enqueue the same asset URL many times.
    queued_assets: set[str] = set()

    # download_q holds (abs_url, destination_path) pairs.
    download_q: queue.Queue[tuple[str, Path]] = queue.Queue()

    root_netloc = _canonical_netloc(urlparse(start_url))

    def worker() -> None:
        """Download worker thread: pulls tasks from download_q and writes them."""
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
                    external_domains=external_domains,
                )
            finally:
                download_q.task_done()

    # Spawn the asset download workers.
    for i in range(max(1, threads)):
        t = threading.Thread(target=worker, name=f"DL-{i + 1}", daemon=True)
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

        # Walk the DOM once and:
        # 1) enqueue internal pages from <a href=...>
        # 2) enqueue assets referenced via src/href/data-src/poster/srcset/style/<style>
        for tag in soup.find_all(True):

            # Common URL-bearing attributes
            for attr in ("src", "href", "data-src", "poster"):
                if not tag.has_attr(attr):
                    continue

                link_raw = str(tag.get(attr, "")).strip()
                if not link_raw:
                    continue

                link = _protocol_fix(link_raw, page_url)
                if (
                    link.startswith("#")
                    or is_non_fetchable(link)
                    or not is_httpish(link)
                ):
                    continue

                abs_url = normalize_url(canonicalize_url(link, page_url))
                parsed = urlparse(abs_url)
                is_ext = not is_internal(abs_url, root_netloc)

                # Only crawl internal HTML pages from <a href=...>
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

                # Otherwise treat it as an asset candidate.
                if is_ext:
                    parsed_host = (urlparse(abs_url).hostname or "").lower()
                    log.debug("[EXT-ASSET] %s", parsed_host)

                    if not download_external_assets:
                        continue

                    if not is_allowed_external(abs_url, external_domains):
                        log.debug("Blocked external (not whitelisted): %s", abs_url)
                        continue

                    # External assets without extensions are only allowed for <script> and <link>
                    # because CDNs sometimes serve JS/CSS without filename extensions.
                    if tag.name not in (
                        "script",
                        "link",
                    ) and not parsed.path.lower().endswith(ASSET_EXTENSIONS):
                        continue

                    dest_path = cdn_local_path(parsed, root)
                else:
                    dest_path = to_local_asset_path(parsed, root)

                if abs_url not in queued_assets:
                    queued_assets.add(abs_url)
                    create_dir(dest_path.parent)
                    log.debug("Queue asset: %s -> %s", abs_url, dest_path)
                    download_q.put((abs_url, dest_path))

            # ------------------------------------------------------------------
            # META IMAGE SUPPORT (og:image, twitter:image)
            # ------------------------------------------------------------------
            if tag.name == "meta":
                content = str(tag.get("content", "")).strip()
                prop = (tag.get("property") or tag.get("name") or "").lower()

                if content and ("og:image" in prop or "twitter:image" in prop):
                    url_part = _protocol_fix(content, page_url)

                    if (
                        not url_part
                        or url_part.startswith("#")
                        or url_part.startswith(("data:", "javascript:", "about:"))
                        or is_non_fetchable(url_part)
                        or not is_httpish(url_part)
                    ):
                        continue
                    else:
                        abs_url = normalize_url(canonicalize_url(url_part, page_url))
                        parsed = urlparse(abs_url)

                        if parsed.path.lower().endswith(ASSET_EXTENSIONS):
                            is_ext = not is_internal(abs_url, root_netloc)

                            if is_ext:
                                if not download_external_assets:
                                    continue
                                elif not is_allowed_external(abs_url, external_domains):
                                    log.debug("Blocked external (meta): %s", abs_url)
                                    continue
                                else:
                                    dest_path = cdn_local_path(parsed, root)

                                    if abs_url not in queued_assets:
                                        queued_assets.add(abs_url)
                                        create_dir(dest_path.parent)
                                        log.debug(
                                            "Queue meta asset: %s -> %s",
                                            abs_url,
                                            dest_path,
                                        )
                                        download_q.put((abs_url, dest_path))
                            else:
                                dest_path = to_local_asset_path(parsed, root)

                                if abs_url not in queued_assets:
                                    queued_assets.add(abs_url)
                                    create_dir(dest_path.parent)
                                    log.debug(
                                        "Queue meta asset: %s -> %s", abs_url, dest_path
                                    )
                                    download_q.put((abs_url, dest_path))

            # srcset handling (images at multiple resolutions)
            if tag.has_attr("srcset"):
                for entry in str(tag["srcset"]).split(","):
                    entry = entry.strip()
                    if not entry:
                        continue

                    url_part = _protocol_fix(entry.split()[0], page_url)
                    if (
                        url_part.startswith("#")
                        or is_non_fetchable(url_part)
                        or not is_httpish(url_part)
                    ):
                        continue

                    abs_url = normalize_url(canonicalize_url(url_part, page_url))
                    parsed = urlparse(abs_url)
                    is_ext = not is_internal(abs_url, root_netloc)

                    if is_ext:
                        if not download_external_assets:
                            continue

                        if not is_allowed_external(abs_url, external_domains):
                            log.debug("Blocked external (srcset): %s", abs_url)
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

            # inline style="...url(...)..." assets
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

                    if is_ext:
                        if not download_external_assets:
                            continue

                        if not is_allowed_external(abs_url, external_domains):
                            log.debug("Blocked external (inline style): %s", abs_url)
                            continue

                    dest_path = (
                        cdn_local_path(parsed, root)
                        if is_ext
                        else to_local_asset_path(parsed, root)
                    )
                    if abs_url not in queued_assets:
                        queued_assets.add(abs_url)
                        create_dir(dest_path.parent)
                        log.debug("Queue asset: %s -> %s", abs_url, dest_path)
                        download_q.put((abs_url, dest_path))

            # <style> blocks: extract CSS asset references and enqueue them
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

                    if is_ext:
                        if not download_external_assets:
                            continue

                        if not is_allowed_external(abs_url, external_domains):
                            log.debug("Blocked external (<style>): %s", abs_url)
                            continue

                    dest_path = (
                        cdn_local_path(parsed, root)
                        if is_ext
                        else to_local_asset_path(parsed, root)
                    )
                    if abs_url not in queued_assets:
                        queued_assets.add(abs_url)
                        create_dir(dest_path.parent)
                        log.debug("Queue asset: %s -> %s", abs_url, dest_path)
                        download_q.put((abs_url, dest_path))

        # Save current page:
        # - determine local filename
        # - rewrite links inside the HTML
        # - write out the HTML
        local_path = to_local_path(urlparse(page_url), root)
        create_dir(local_path.parent)
        rewrite_links(
            soup,
            page_url,
            root,
            local_path.parent,
            download_external_assets,
            external_domains,
        )
        safe_write_text(local_path, str(soup), encoding="utf-8")

    # Wait for all queued asset downloads to finish
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
    """
    Derive output folder from URL if custom not supplied.

    Example:
      https://example.com -> example_com
    """
    return Path(custom) if custom else Path(urlparse(url).netloc.replace(".", "_"))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_cookie_header(cookie_header: str) -> dict[str, str]:
    """
    Parse a cookie header string like "key1=val1; key2=val2" into a dict.
    """
    cookies: dict[str, str] = {}
    for part in re.split(r";\s*", cookie_header.strip()):
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"Invalid cookie entry: {part}")
        name, value = part.split("=", 1)
        cookies[name.strip()] = value.strip()
    return cookies


def parse_args() -> argparse.Namespace:
    """
    Parse command-line arguments.

    --download-external-assets:
      When enabled, we ALSO download assets from other hosts (CDNs).
      Your HTML rewriting currently keeps CDN URLs unchanged in HTML,
      but CSS/JS rewriting can still localize them if those files are downloaded.
    """
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
    p.add_argument(
        "--external-domains",
        nargs="+",
        default=None,
        help="Whitelist of external domains to download from (implies external download).",
    )
    p.add_argument(
        "--cookie",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help=(
            "Set a cookie to send with all requests. "
            "Repeat for multiple cookies, e.g. --cookie sessionid=abc --cookie csrftoken=xyz."
        ),
    )
    p.add_argument(
        "--cookie-file",
        action="append",
        default=[],
        metavar="FILE",
        help=(
            "Read cookies from a file containing cookie header syntax like "
            "key1=val1; key2=val2;. Can be repeated."
        ),
    )
    return p.parse_args()


if __name__ == "__main__":
    # Basic argument validation
    args = parse_args()
    if args.max_pages < 1:
        log.error("--max-pages must be >= 1")
        sys.exit(2)
    if args.threads < 1:
        log.error("--threads must be >= 1")
        sys.exit(2)

    # start URL + output root folder
    host = args.url
    root = make_root(args.url, args.destination)

    external_domains = (
        {
            urlparse(d).hostname.lower() if "://" in d else d.lower()
            for d in args.external_domains
        }
        if args.external_domains
        else None
    )

    download_external_assets = (
        args.download_external_assets or args.external_domains is not None
    )

    # Process cookies from command line and cookie files, and add them to the session.
    if args.cookie or args.cookie_file:
        cookie_dict: dict[str, str] = {}

        for cookie in args.cookie:
            try:
                cookie_dict.update(parse_cookie_header(cookie))
            except ValueError as exc:
                log.error("Invalid cookie value: %s", exc)
                sys.exit(2)

        for cookie_path in args.cookie_file:
            try:
                raw = Path(cookie_path).expanduser().read_text(encoding="utf-8").strip()
            except Exception as exc:
                log.error("Cannot read cookie file %s: %s", cookie_path, exc)
                sys.exit(2)
            try:
                cookie_dict.update(parse_cookie_header(raw))
            except ValueError as exc:
                log.error("Invalid cookie file %s: %s", cookie_path, exc)
                sys.exit(2)

        SESSION.cookies.update(cookie_dict)
        log.debug("Added cookies to session: %s", list(cookie_dict.keys()))

    # Kick off crawl
    crawl_site(
        host,
        root,
        args.max_pages,
        args.threads,
        download_external_assets,
        external_domains,
    )
