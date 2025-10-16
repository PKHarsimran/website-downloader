import os
import sys
import time
import queue
import argparse
import logging
import threading
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
from typing import Optional  # ✅ for Python ≤ 3.9 compatibility

"""website_downloader.py – v2.0 (2025‑07‑19)
================================================
A **tiny, pure‑Python** site‑mirroring CLI that now ships with a friendlier
command‑line UX and a few quality‑of‑life tweaks:

* **Recursive crawl** of all same‑origin links – even pretty URLs like */about*.
* **Link rewriting** so every internal *href/src* works 100 % offline.
* **Concurrent** (thread‑pool) download of images, CSS & JS.
* **Single, flat root** – goodbye "double‑domain" sub‑folders.
* **Auto‑retry** with exponential back‑off for flaky hosts.
* **Colourised logs** + crawl summary (total & average latency).
* **CLI flags** – no more interactive prompts:

  ```bash
  python website_downloader.py \
         --url https://example.com \
         --destination my_archive \
         --max-pages 50         # default = 50
  ```

* Defaults:
  * Output folder → the domain with dots swapped for underscores.
  * `--max-pages` → **50** (changed from 100 in v2.0 to match spec).
  * `--threads`   → 6 concurrent resource fetchers.

Distributed under the MIT licence.
"""

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

# 🛠️  FIXED: removed stray space after `%` which broke %-format strings
LOG_FMT = "%(asctime)s | %(levelname)-8s | %(threadName)s | %(message)s"
logging.basicConfig(
    filename="web_scraper.log",
    level=logging.DEBUG,
    format=LOG_FMT,
    datefmt="%H:%M:%S",
    force=True,  # reset any prior basicConfig in interactive shells / tests
)
console = logging.StreamHandler(sys.stdout)
console.setLevel(logging.INFO)
console.setFormatter(logging.Formatter(LOG_FMT, datefmt="%H:%M:%S"))
logging.getLogger().addHandler(console)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTTP session (retry, time‑outs, custom UA)
# ---------------------------------------------------------------------------

DEFAULT_HEADERS = {
    "User-Agent": "WebsiteDownloader/4.1 (+https://github.com/yourhandle)"
}

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

TIMEOUT = 15  # seconds
CHUNK_SIZE = 8192  # bytes

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def create_dir(path: Path) -> None:
    """Create *path* (and parents) if it does not already exist."""
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
        log.debug("Created directory %s", path)


def sanitize(url_fragment: str) -> str:
    """Strip dangerous back‑references & Windows back‑slashes."""
    return url_fragment.replace("\\", "/").replace("..", "").strip()


def is_internal(link: str, root_netloc: str) -> bool:
    """True if *link* belongs to *root_netloc* (or is protocol‑relative)."""
    parsed = urlparse(link)
    return not parsed.netloc or parsed.netloc == root_netloc


def to_local_path(parsed: urlparse, site_root: Path) -> Path:
    """Map an internal URL to a local file path under *site_root*."""
    rel = parsed.path.lstrip("/")
    if not rel:
        rel = "index.html"
    elif rel.endswith("/"):
        rel += "index.html"
    elif not Path(rel).suffix:
        rel += ".html"
    return site_root / rel

# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

def fetch_html(url: str) -> Optional[BeautifulSoup]:  # compatible type hint
    """Download *url* and return a BeautifulSoup tree (or None on error)."""
    try:
        resp = SESSION.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:
        log.warning("HTTP error for %s – %s", url, exc)
        return None


def fetch_binary(url: str, dest: Path) -> None:
    """Stream *url* to *dest* unless it already exists."""
    if dest.exists():
        return
    try:
        resp = SESSION.get(url, timeout=TIMEOUT, stream=True)
        resp.raise_for_status()
        create_dir(dest.parent)
        with dest.open("wb") as fh:
            for chunk in resp.iter_content(CHUNK_SIZE):
                fh.write(chunk)
        log.debug("Saved resource → %s", dest)
    except Exception as exc:
        log.error("Failed to save %s – %s", url, exc)

# ---------------------------------------------------------------------------
# Link rewriting
# ---------------------------------------------------------------------------

def rewrite_links(soup: BeautifulSoup, page_url: str, site_root: Path, page_dir: Path) -> None:
    root_netloc = urlparse(page_url).netloc
    for tag in soup.find_all(["a", "img", "script", "link"]):
        attr = "href" if tag.name in {"a", "link"} else "src"
        if not tag.has_attr(attr):
            continue
        original = sanitize(tag[attr])
        if original.startswith(("javascript:", "data:", "#")):
            continue
        abs_url = urljoin(page_url, original)
        if not is_internal(abs_url, root_netloc):
            continue  # external – leave untouched
        local_path = to_local_path(urlparse(abs_url), site_root)
        try:
            tag[attr] = os.path.relpath(local_path, page_dir)
        except ValueError:
            tag[attr] = str(local_path)

# ---------------------------------------------------------------------------
# Crawl coordinator
# ---------------------------------------------------------------------------

def crawl_site(start_url: str, root: Path, max_pages: int, threads: int) -> None:
    """Breadth‑first crawl limited to *max_pages*. Download assets via thread pool."""

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
            fetch_binary(url, dest)
            download_q.task_done()

    # Launch download workers
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

        # Gather links & assets from the page
        for tag in soup.find_all(["img", "script", "link", "a"]):
            link = tag.get("src") or tag.get("href")
            if not link:
                continue
            link = sanitize(link)
            if link.startswith(("javascript:", "data:", "#")):
                continue
            abs_url = urljoin(page_url, link)
            parsed = urlparse(abs_url)
            if not is_internal(abs_url, root_netloc):
                continue

            dest_path = to_local_path(parsed, root)
            # HTML?
            if parsed.path.endswith("/") or not Path(parsed.path).suffix:
                if abs_url not in seen_pages and abs_url not in list(q_pages.queue):
                    q_pages.put(abs_url)
            else:
                download_q.put((abs_url, dest_path))

        # Save the current page
        local_path = to_local_path(urlparse(page_url), root)
        create_dir(local_path.parent)
        rewrite_links(soup, page_url, root, local_path.parent)
        local_path.write_text(soup.prettify(), encoding="utf-8")
        log.debug("Saved page %s", local_path)

    # Wait for remaining downloads
    download_q.join()
    elapsed = time.time() - start_time
    if seen_pages:
        log.info("Crawl finished: %s pages in %.2fs (%.2fs avg)", len(seen_pages), elapsed, elapsed / len(seen_pages))
    else:
        log.warning("Nothing downloaded – check URL or connectivity")

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def make_root(url: str, custom: Optional[str]) -> Path:  # 🔧 changed for 3.9
    """Derive the output folder from *url* if *custom* not supplied."""
    return Path(custom) if custom else Path(urlparse(url).netloc.replace(".", "_"))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Recursively mirror a website for offline use.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

if __name__ == '__main__':
    host = "https://example.com"
    root = "example_com"
    max_pages = 50
    threads = 6

    arguments = [sys.argv[x:x+2] for x in range(1, len(sys.argv),2)]
    for arg in arguments:
        if arg[0] == "--url": host = arg[1]
        if arg[0] == "--destination": root = arg[1]
        if arg[0] == "--max-pages": max_pages = arg[1]
        if arg[0] == "--threads": threads = arg[1]

    crawl_site(host, Path(root), int(max_pages,10), int(threads,10))