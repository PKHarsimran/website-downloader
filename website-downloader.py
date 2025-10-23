#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import os
import queue
import sys
import threading
import time
from hashlib import sha256
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

# ---------------------------------------------------------------------------
# Config / constants
# ---------------------------------------------------------------------------

LOG_FMT = "%(asctime)s | %(levelname)-8s | %(threadName)s | %(message)s"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) "
    "Gecko/20100101 Firefox/128.0"
}

TIMEOUT = 15  # seconds
CHUNK_SIZE = 8192  # bytes

# Conservative margins under common OS limits (~255–260 bytes)
MAX_PATH_LEN = 240
MAX_SEG_LEN = 120


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


def sanitize(url_fragment: str) -> str:
    """Strip back-references and Windows backslashes."""
    return url_fragment.replace("\\", "/").replace("..", "").strip()


NON_FETCHABLE_SCHEMES = {"mailto", "tel", "sms", "javascript", "data", "geo", "blob"}


def is_httpish(u: str) -> bool:
    """True iff the URL is http(s) or relative (no scheme)."""
    p = urlparse(u)
    return (p.scheme in ("http", "https")) or (p.scheme == "")


def is_non_fetchable(u: str) -> bool:
    """True iff the URL clearly shouldn't be fetched (mailto:, tel:, data:, ...)."""
    p = urlparse(u)
    return p.scheme in NON_FETCHABLE_SCHEMES


def is_internal(link: str, root_netloc: str) -> bool:
    """Return True if link belongs to root_netloc (or is protocol-relative)."""
    parsed = urlparse(link)
    return not parsed.netloc or parsed.netloc == root_netloc


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


def to_local_path(parsed: urlparse, site_root: Path) -> Path:
    """
    Map an internal URL to a local file path under site_root.

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

    # Shorten individual segments
    parts = Path(rel).parts
    parts = tuple(_shorten_segment(seg, MAX_SEG_LEN) for seg in parts)
    local_path = site_root / Path(*parts)

    # If full path is still too long, hash the leaf
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


def fetch_binary(url: str, dest: Path) -> None:
    """Stream url to dest unless it already exists. Safe against long paths."""
    if dest.exists():
        return
    try:
        resp = SESSION.get(url, timeout=TIMEOUT, stream=True)
        resp.raise_for_status()
        create_dir(dest.parent)
        try:
            with dest.open("wb") as fh:
                for chunk in resp.iter_content(CHUNK_SIZE):
                    fh.write(chunk)
            log.debug("Saved resource -> %s", dest)
        except OSError as exc:
            # Fallback to hashed leaf if OS rejects path
            log.warning("Binary write failed for %s: %s. Using fallback.", dest, exc)
            p = dest
            h = sha256(str(p).encode("utf-8")).hexdigest()[:16]
            fallback = p.with_name(
                _shorten_segment(f"{p.stem}-{h}{p.suffix}", MAX_SEG_LEN)
            )
            create_dir(fallback.parent)
            with fallback.open("wb") as fh:
                for chunk in resp.iter_content(CHUNK_SIZE):
                    fh.write(chunk)
            log.debug("Saved resource (fallback) -> %s", fallback)
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to save %s – %s", url, exc)


# ---------------------------------------------------------------------------
# Link rewriting
# ---------------------------------------------------------------------------


def rewrite_links(
    soup: BeautifulSoup, page_url: str, site_root: Path, page_dir: Path
) -> None:
    """Rewrite internal links to local relative paths under site_root."""
    root_netloc = urlparse(page_url).netloc
    for tag in soup.find_all(["a", "img", "script", "link"]):
        attr = "href" if tag.name in {"a", "link"} else "src"
        if not tag.has_attr(attr):
            continue
        original = sanitize(tag[attr])
        if (
            original.startswith("#")
            or is_non_fetchable(original)
            or not is_httpish(original)
        ):
            continue
        abs_url = urljoin(page_url, original)
        if not is_internal(abs_url, root_netloc):
            continue  # external – leave untouched
        local_path = to_local_path(urlparse(abs_url), site_root)
        try:
            tag[attr] = os.path.relpath(local_path, page_dir)
        except ValueError:
            # Different drives on Windows, etc.
            tag[attr] = str(local_path)


# ---------------------------------------------------------------------------
# Crawl coordinator
# ---------------------------------------------------------------------------


def crawl_site(start_url: str, root: Path, max_pages: int, threads: int) -> None:
    """Breadth-first crawl limited to max_pages. Downloads assets via workers."""
    q_pages: queue.Queue[str] = queue.Queue()
    q_pages.put(start_url)
    seen_pages: set[str] = set()
    download_q: queue.Queue[tuple[str, Path]] = queue.Queue()

    def worker() -> None:
        while True:
            try:
                url, dest = download_q.get(timeout=3)
            except queue.Empty:
                return
            if is_non_fetchable(url) or not is_httpish(url):
                log.debug("Skip non-fetchable: %s", url)
                download_q.task_done()
                continue
            fetch_binary(url, dest)
            download_q.task_done()

    workers: list[threading.Thread] = []
    for i in range(max(1, threads)):
        t = threading.Thread(target=worker, name=f"DL-{i+1}", daemon=True)
        t.start()
        workers.append(t)

    start_time = time.time()
    root_netloc = urlparse(start_url).netloc

    while not q_pages.empty() and len(seen_pages) < max_pages:
        page_url = q_pages.get()
        if page_url in seen_pages:
            continue
        seen_pages.add(page_url)
        log.info("[%s/%s] %s", len(seen_pages), max_pages, page_url)

        soup = fetch_html(page_url)
        if soup is None:
            continue

        # Gather links & assets
        for tag in soup.find_all(["img", "script", "link", "a"]):
            link = tag.get("src") or tag.get("href")
            if not link:
                continue
            link = sanitize(link)
            if link.startswith("#") or is_non_fetchable(link) or not is_httpish(link):
                continue
            abs_url = urljoin(page_url, link)
            parsed = urlparse(abs_url)
            if not is_internal(abs_url, root_netloc):
                continue

            dest_path = to_local_path(parsed, root)
            # HTML?
            if parsed.path.endswith("/") or not Path(parsed.path).suffix:
                if abs_url not in seen_pages and abs_url not in list(
                    q_pages.queue
                ):  # type: ignore[arg-type]
                    q_pages.put(abs_url)
            else:
                download_q.put((abs_url, dest_path))

        # Save current page
        local_path = to_local_path(urlparse(page_url), root)
        create_dir(local_path.parent)
        rewrite_links(soup, page_url, root, local_path.parent)
        html = soup.prettify()
        final_path = safe_write_text(local_path, html, encoding="utf-8")
        log.debug("Saved page %s", final_path)

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
    crawl_site(host, root, args.max_pages, args.threads)
