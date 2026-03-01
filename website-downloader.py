#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import os
import posixpath
import queue
import re
import sys
import threading
import time
from hashlib import sha256
from pathlib import Path
from typing import Optional
from urllib.parse import ParseResult, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

# ---------------------------------------------------------------------------
# Config / constants
# ---------------------------------------------------------------------------

LOG_FMT = "%(asctime)s | %(levelname)-8s | %(threadName)s | %(message)s"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) "
        "Gecko/20100101 Firefox/128.0"
    )
}

TIMEOUT = 15
CHUNK_SIZE = 8192

MAX_PATH_LEN = 240
MAX_SEG_LEN = 120

_MULTI_DOTS_RE = re.compile(r"\.{3,}")
NON_FETCHABLE_SCHEMES = {"mailto", "tel", "sms", "javascript", "data", "geo", "blob"}

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
# HTTP session
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
    """Create directory (and parents) if it does not already exist."""
    path.mkdir(parents=True, exist_ok=True)


def sanitize_url_fragment(url_fragment: str) -> str:
    """
    Normalize URL fragments safely.

    - Convert backslashes to forward slashes
    - Prevent traversal outside root
    """
    url_fragment = url_fragment.replace("\\", "/").strip()
    parsed = urlparse(url_fragment)
    normalized_path = posixpath.normpath(parsed.path)

    if normalized_path.startswith("../"):
        normalized_path = normalized_path.lstrip("../")

    return normalized_path


def is_httpish(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https", "")


def is_non_fetchable(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in NON_FETCHABLE_SCHEMES


def is_internal(link: str, root_netloc: str) -> bool:
    parsed = urlparse(link)
    return not parsed.netloc or parsed.netloc == root_netloc


def _sanitize_segment(segment: str) -> str:
    """
    Sanitize a single path segment:
    - URL decode
    - Strip whitespace
    - Collapse accidental multi-dots
    """
    segment = unquote(segment).strip()
    segment = _MULTI_DOTS_RE.sub(".", segment)
    return segment or "unnamed"


def _shorten_segment(segment: str, limit: int = MAX_SEG_LEN) -> str:
    if len(segment) <= limit:
        return segment

    p = Path(segment)
    stem, suffix = p.stem, p.suffix
    h = sha256(segment.encode("utf-8")).hexdigest()[:12]

    keep = max(0, limit - len(suffix) - 13)
    return f"{stem[:keep]}-{h}{suffix}"


def to_local_path(parsed: ParseResult, site_root: Path) -> Path:
    """
    Map an internal URL to a safe local file path.
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
    parts = tuple(_sanitize_segment(p) for p in parts)
    parts = tuple(_shorten_segment(p) for p in parts)

    local_path = site_root / Path(*parts)

    if len(str(local_path)) > MAX_PATH_LEN:
        h = sha256(parsed.geturl().encode("utf-8")).hexdigest()[:16]
        leaf = _shorten_segment(f"{local_path.stem}-{h}{local_path.suffix}")
        local_path = local_path.with_name(leaf)

    return local_path


def safe_write_text(path: Path, text: str, encoding: str = "utf-8") -> Path:
    try:
        create_dir(path.parent)
        path.write_text(text, encoding=encoding)
        return path
    except OSError:
        h = sha256(str(path).encode("utf-8")).hexdigest()[:16]
        fallback = path.with_name(
            _shorten_segment(f"{path.stem}-{h}{path.suffix}")
        )
        create_dir(fallback.parent)
        fallback.write_text(text, encoding=encoding)
        return fallback


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------


def fetch_html(url: str) -> Optional[BeautifulSoup]:
    try:
        resp = SESSION.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:  # noqa: BLE001
        log.warning("HTTP error for %s – %s", url, exc)
        return None


def fetch_binary(url: str, dest: Path) -> None:
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
        except OSError:
            h = sha256(str(dest).encode("utf-8")).hexdigest()[:16]
            fallback = dest.with_name(
                _shorten_segment(f"{dest.stem}-{h}{dest.suffix}")
            )
            create_dir(fallback.parent)
            with fallback.open("wb") as fh:
                for chunk in resp.iter_content(CHUNK_SIZE):
                    fh.write(chunk)

    except Exception as exc:  # noqa: BLE001
        log.error("Failed to save %s – %s", url, exc)


# ---------------------------------------------------------------------------
# Crawl
# ---------------------------------------------------------------------------


def crawl_site(start_url: str, root: Path, max_pages: int, threads: int) -> None:
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

            if not is_httpish(url) or is_non_fetchable(url):
                download_q.task_done()
                continue

            fetch_binary(url, dest)
            download_q.task_done()

    workers = [
        threading.Thread(target=worker, daemon=True, name=f"DL-{i+1}")
        for i in range(max(1, threads))
    ]

    for w in workers:
        w.start()

    root_netloc = urlparse(start_url).netloc
    start_time = time.time()

    while not q_pages.empty() and len(seen_pages) < max_pages:
        page_url = q_pages.get()

        if page_url in seen_pages:
            continue

        seen_pages.add(page_url)
        log.info("[%s/%s] %s", len(seen_pages), max_pages, page_url)

        soup = fetch_html(page_url)
        if soup is None:
            continue

        for tag in soup.find_all(["img", "script", "link", "a"]):
            link = tag.get("src") or tag.get("href")
            if not link:
                continue

            link = sanitize_url_fragment(link)

            if (
                link.startswith("#")
                or not is_httpish(link)
                or is_non_fetchable(link)
            ):
                continue

            abs_url = urljoin(page_url, link)

            if not is_internal(abs_url, root_netloc):
                continue

            parsed = urlparse(abs_url)
            dest_path = to_local_path(parsed, root)

            if parsed.path.endswith("/") or not Path(parsed.path).suffix:
                if abs_url not in seen_pages:
                    q_pages.put(abs_url)
            else:
                download_q.put((abs_url, dest_path))

        local_path = to_local_path(urlparse(page_url), root)
        create_dir(local_path.parent)

        for tag in soup.find_all(["a", "img", "script", "link"]):
            attr = "href" if tag.name in {"a", "link"} else "src"
            if not tag.has_attr(attr):
                continue

            original = sanitize_url_fragment(tag[attr])

            if (
                original.startswith("#")
                or not is_httpish(original)
                or is_non_fetchable(original)
            ):
                continue

            abs_url = urljoin(page_url, original)

            if not is_internal(abs_url, root_netloc):
                continue

            local_target = to_local_path(urlparse(abs_url), root)

            try:
                tag[attr] = os.path.relpath(local_target, local_path.parent)
            except ValueError:
                tag[attr] = str(local_target)

        safe_write_text(local_path, soup.prettify())

    download_q.join()

    elapsed = time.time() - start_time
    if seen_pages:
        log.info(
            "Crawl finished: %s pages in %.2fs (%.2fs avg)",
            len(seen_pages),
            elapsed,
            elapsed / len(seen_pages),
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def make_root(url: str, custom: Optional[str]) -> Path:
    return Path(custom) if custom else Path(urlparse(url).netloc.replace(".", "_"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recursively mirror a website for offline use.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument("--url", required=True)
    parser.add_argument("--destination", default=None)
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--threads", type=int, default=6)

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.max_pages < 1 or args.threads < 1:
        sys.exit("Invalid arguments.")

    crawl_site(
        start_url=args.url,
        root=make_root(args.url, args.destination),
        max_pages=args.max_pages,
        threads=args.threads,
    )
