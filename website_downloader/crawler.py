from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from .cache import CrawlCache
from .constants import ASSET_EXTENSIONS, DEFAULT_USER_AGENT, PAGE_SUFFIXES, TIMEOUT
from .exports import SavedResource, create_zip_archive, write_warc
from .http import FetchFailure, create_session, fetch_binary, fetch_html
from .manifest import CrawlManifest
from .paths import cdn_local_path, create_dir, safe_write_text, to_local_asset_path, to_local_path
from .progress import create_progress_reporter
from .render import PlaywrightRenderer
from .rewrite import extract_css_assets, link_rel_is_fetchable, rewrite_links
from .sitemap import load_sitemap_urls
from .urltools import (
    canonical_netloc,
    canonicalize_url,
    is_allowed_external,
    is_blacklisted,
    is_httpish,
    is_internal,
    is_non_fetchable,
)

log = logging.getLogger(__name__)

# Tags whose asset references are downloaded even when the URL has no file
# extension. Media served from extensionless endpoints (e.g. OpenProject
# attachment ".../attachments/228/content" URLs) must be fetched as binary,
# not skipped or mistaken for an HTML page.
EXTENSIONLESS_ASSET_TAGS = {"script", "link", "img", "source", "video", "audio", "embed", "track"}


@dataclass
class CrawlOptions:
    start_url: str
    root: Path
    extra_start_urls: list[str] = field(default_factory=list)
    max_pages: int = 50
    max_depth: int | None = None
    threads: int = 6
    page_threads: int = 1
    download_external_assets: bool = False
    external_domains: set[str] | None = None
    exclude_patterns: list[str] | None = None
    cookies: dict[str, str] | None = None
    headers: dict[str, str] | None = None
    timeout: int = TIMEOUT
    delay: float = 0.0
    max_asset_bytes: int | None = None
    user_agent: str | None = None
    respect_robots: bool = False
    render_js: bool = False
    render_wait_until: str = "networkidle"
    render_timeout_ms: int = 30000
    update: bool = False
    cache_file: Path | None = None
    sitemap: str | None = None
    progress: bool = False
    zip_output: Path | None = None
    warc_output: Path | None = None
    manifest: Path | None = None


@dataclass
class CrawlStats:
    pages_seen: int
    assets_queued: int
    elapsed_seconds: float
    pages_cached: int = 0
    assets_cached: int = 0
    pages_written: int = 0
    assets_written: int = 0
    errors: int = 0


def crawl_site(options: CrawlOptions) -> CrawlStats:
    q_pages: queue.Queue[tuple[str, int]] = queue.Queue()
    start_url = canonicalize_url(options.start_url)
    manifest = CrawlManifest(
        options.manifest,
        options.root,
        list(
            dict.fromkeys([start_url, *(canonicalize_url(url) for url in options.extra_start_urls)])
        ),
    )
    seen_pages: set[str] = set()
    queued_pages: set[str] = set()
    queued_assets: set[str] = set()
    asset_lock = threading.Lock()
    cache_lock = threading.Lock()
    record_lock = threading.Lock()
    page_lock = threading.Lock()
    stats_lock = threading.Lock()
    download_q: queue.Queue[tuple[str, Path] | None] = queue.Queue()
    saved_records: list[SavedResource] = []

    root_netloc = canonical_netloc(urlparse(start_url))
    user_agent = options.user_agent or DEFAULT_USER_AGENT
    page_session = create_session(
        user_agent=user_agent,
        cookies=options.cookies,
        headers=options.headers,
    )
    thread_sessions = threading.local()
    thread_sessions.session = page_session

    def _session_for_thread():
        session = getattr(thread_sessions, "session", None)
        if session is None:
            session = create_session(
                user_agent=user_agent,
                cookies=options.cookies,
                headers=options.headers,
            )
            thread_sessions.session = session
        return session

    page_threads = max(1, options.page_threads)
    if options.render_js and page_threads > 1:
        log.info("JavaScript rendering is single-threaded; using one page worker.")
        page_threads = 1
    if options.delay and page_threads > 1:
        log.warning(
            "--delay applies per page worker; total request rate scales with --page-threads."
        )
    robots = (
        _load_robots(start_url, page_session, options.timeout) if options.respect_robots else None
    )
    cache_path = options.cache_file or (options.root / ".website-downloader-cache.json")
    crawl_cache = CrawlCache.load(cache_path) if options.update else CrawlCache()
    stats = CrawlStats(pages_seen=0, assets_queued=0, elapsed_seconds=0.0)

    def enqueue_page(url: str, depth: int = 0) -> None:
        normalized = canonicalize_url(url, start_url)
        if options.max_depth is not None and depth > options.max_depth:
            manifest.record(
                "page", normalized, only_new=True, status="skipped", reason="max_depth", depth=depth
            )
            return
        if is_blacklisted(normalized, options.exclude_patterns):
            log.debug("Excluded page (blacklist match): %s", normalized)
            manifest.record(
                "page", normalized, only_new=True, status="skipped", reason="excluded", depth=depth
            )
            return
        with page_lock:
            if normalized not in queued_pages and normalized not in seen_pages:
                q_pages.put((normalized, depth))
                queued_pages.add(normalized)
                manifest.record("page", normalized, status="pending", reason=None, depth=depth)

    def enqueue_asset(url: str, dest: Path) -> None:
        abs_url = canonicalize_url(url)
        if not is_internal(abs_url, root_netloc):
            reason = None
            if not options.download_external_assets:
                reason = "external_downloads_disabled"
            elif not is_allowed_external(abs_url, options.external_domains):
                reason = "external_domain_not_allowed"
            if reason:
                manifest.record("asset", abs_url, only_new=True, status="skipped", reason=reason)
                return
        with asset_lock:
            if abs_url in queued_assets:
                return
            queued_assets.add(abs_url)
            stats.assets_queued = len(queued_assets)
            manifest.record("asset", abs_url)
        progress_reporter.asset_queued()
        log.debug("Queue asset: %s -> %s", abs_url, dest)
        download_q.put((abs_url, dest))

    enqueue_page(start_url)
    for extra_url in options.extra_start_urls:
        enqueue_page(canonicalize_url(extra_url))
    if options.update:
        # Saved pages contain rewritten local links, so a 304 page cannot be
        # re-discovered from its own HTML. Seed known pages from the cache.
        for cached_url, entry in crawl_cache.entries.items():
            if entry.kind == "page":
                enqueue_page(cached_url)
    if options.sitemap:
        for sitemap_url in load_sitemap_urls(
            options.sitemap,
            start_url=start_url,
            session=page_session,
            timeout=options.timeout,
            root_netloc=root_netloc,
        ):
            enqueue_page(sitemap_url)

    start_time = time.time()
    renderer_context = (
        PlaywrightRenderer(
            start_url=start_url,
            cookies=options.cookies,
            headers=options.headers,
            timeout_ms=options.render_timeout_ms,
            wait_until=options.render_wait_until,
            user_agent=user_agent,
        )
        if options.render_js
        else None
    )
    progress_reporter = create_progress_reporter(options.progress, options.max_pages)
    workers: list[threading.Thread] = []
    completed = False
    termination_reason = "error"

    try:
        with (
            _optional_renderer(renderer_context) as renderer,
            progress_reporter as progress,
        ):
            workers = _start_workers(
                threads=options.threads,
                download_q=download_q,
                enqueue_asset=enqueue_asset,
                options=options,
                root_netloc=root_netloc,
                user_agent=user_agent,
                cache=crawl_cache,
                cache_lock=cache_lock,
                records=saved_records,
                record_lock=record_lock,
                stats=stats,
                stats_lock=stats_lock,
                progress=progress,
                manifest=manifest,
            )
            if options.update:
                for cached_url, entry in list(crawl_cache.entries.items()):
                    if entry.kind == "asset":
                        enqueue_asset(cached_url, Path(entry.path))

            def claim_page(page_url: str) -> bool:
                with page_lock:
                    if page_url in seen_pages:
                        return False
                    if robots is not None and not robots.can_fetch(user_agent, page_url):
                        log.info("Blocked by robots.txt: %s", page_url)
                        manifest.record("page", page_url, status="skipped", reason="robots_txt")
                        return False
                    seen_pages.add(page_url)
                    claimed = len(seen_pages)
                stats.pages_seen = claimed
                log.info("[%s/%s] %s", claimed, options.max_pages, page_url)
                progress.page_seen(claimed, options.max_pages, page_url)
                return True

            def process_page(page_url: str, depth: int) -> None:
                page_assets: set[str] = set()

                def enqueue_child_page(url: str) -> None:
                    enqueue_page(url, depth + 1)

                def enqueue_page_asset(url: str, dest: Path) -> None:
                    page_assets.add(canonicalize_url(url))
                    enqueue_asset(url, dest)

                try:
                    local_path = to_local_path(urlparse(page_url), options.root)
                    result = fetch_html(
                        page_url,
                        session=_session_for_thread(),
                        timeout=options.timeout,
                        renderer=renderer,
                        conditional_headers=(
                            crawl_cache.conditional_headers(page_url) if options.update else None
                        ),
                        cached_path=local_path,
                    )
                    if isinstance(result, FetchFailure):
                        manifest.record("page", page_url, **result.manifest_fields())
                        with stats_lock:
                            stats.errors += 1
                        progress.error()
                        return

                    if options.manifest is not None:
                        manifest.record(
                            "page",
                            page_url,
                            status_code=result.status_code,
                            content_type=result.content_type,
                            title=(
                                result.soup.title.get_text(" ", strip=True)
                                if result.soup.title
                                else None
                            ),
                            headings=[
                                {"level": int(tag.name[1]), "text": tag.get_text(" ", strip=True)}
                                for tag in result.soup.find_all(
                                    ["h1", "h2", "h3", "h4", "h5", "h6"]
                                )
                            ],
                        )
                    if result.not_modified:
                        # The saved copy has links rewritten to local paths, so
                        # discovering references from it would queue bogus URLs.
                        with stats_lock:
                            stats.pages_cached += 1
                        progress.page_saved(cached=True)
                        entry = crawl_cache.get(page_url)
                        if entry is not None and entry.assets is not None:
                            for asset_url in entry.assets:
                                parsed_asset = urlparse(asset_url)
                                asset_path = (
                                    to_local_asset_path(parsed_asset, options.root)
                                    if is_internal(asset_url, root_netloc)
                                    else cdn_local_path(parsed_asset, options.root)
                                )
                                enqueue_asset(asset_url, asset_path)
                        manifest.record(
                            "page",
                            page_url,
                            status="cached",
                            path=local_path,
                            assets=entry.assets if entry else None,
                        )
                    else:
                        _discover_references(
                            result.soup,
                            page_url=page_url,
                            root=options.root,
                            root_netloc=root_netloc,
                            enqueue_page=enqueue_child_page,
                            enqueue_asset=enqueue_page_asset,
                            options=options,
                        )
                        create_dir(local_path.parent)
                        rewrite_links(
                            result.soup,
                            page_url,
                            options.root,
                            local_path.parent,
                            options.download_external_assets,
                            options.external_domains,
                            options.exclude_patterns,
                        )
                        local_path = safe_write_text(local_path, str(result.soup), encoding="utf-8")
                        manifest.record(
                            "page",
                            page_url,
                            status="downloaded",
                            path=local_path,
                            assets=sorted(page_assets),
                        )
                        with stats_lock:
                            stats.pages_written += 1
                        progress.page_saved()
                        if options.update:
                            with cache_lock:
                                crawl_cache.update(
                                    url=page_url,
                                    path=local_path,
                                    kind="page",
                                    status_code=result.status_code,
                                    response_headers=result.headers,
                                    assets=sorted(page_assets),
                                )
                        with record_lock:
                            saved_records.append(
                                SavedResource(
                                    url=page_url,
                                    path=local_path,
                                    kind="page",
                                    status_code=result.status_code,
                                    content_type=result.content_type or "text/html",
                                )
                            )
                except Exception as exc:
                    log.exception("Failed to process page %s", page_url)
                    manifest.record(
                        "page",
                        page_url,
                        status="failed",
                        reason="processing_error",
                        errors=[{"type": type(exc).__name__, "message": str(exc)}],
                    )
                    with stats_lock:
                        stats.errors += 1
                    progress.error()
                if options.delay:
                    time.sleep(options.delay)

            if page_threads == 1:
                # Inline path keeps Playwright rendering on this thread.
                while not q_pages.empty() and len(seen_pages) < options.max_pages:
                    page_url, depth = q_pages.get()
                    if claim_page(page_url):
                        process_page(page_url, depth)
            else:
                _run_page_pool(
                    page_threads=page_threads,
                    q_pages=q_pages,
                    claim_page=claim_page,
                    process_page=process_page,
                    max_pages=options.max_pages,
                    seen_pages=seen_pages,
                )
            completed = True
            termination_reason = "max_pages" if not q_pages.empty() else "finished"
    except KeyboardInterrupt:
        termination_reason = "interrupted"
        raise
    finally:
        download_q.join()
        for _ in workers:
            download_q.put(None)
        download_q.join()
        for worker in workers:
            worker.join(timeout=5)
        stats.elapsed_seconds = time.time() - start_time
        manifest.write(stats, completed=completed, termination_reason=termination_reason)

    elapsed = time.time() - start_time
    stats.elapsed_seconds = elapsed
    if seen_pages:
        log.info(
            "Crawl finished: %s pages (%s cached), %s assets (%s cached), %s errors in %.2fs (%.2fs/page)",
            len(seen_pages),
            stats.pages_cached,
            stats.assets_written,
            stats.assets_cached,
            stats.errors,
            elapsed,
            elapsed / len(seen_pages),
        )
    else:
        log.warning("Nothing downloaded; check URL or connectivity")

    if options.update:
        crawl_cache.save(cache_path)
    if options.zip_output is not None:
        create_zip_archive(options.root, options.zip_output)
        log.info("Wrote zip archive: %s", options.zip_output)
    if options.warc_output is not None:
        write_warc(saved_records, options.warc_output)
        log.info("Wrote WARC archive: %s", options.warc_output)

    return stats


def _run_page_pool(
    *,
    page_threads: int,
    q_pages: queue.Queue[tuple[str, int]],
    claim_page: Callable[[str], bool],
    process_page: Callable[[str, int], None],
    max_pages: int,
    seen_pages: set[str],
) -> None:
    """Fetch pages concurrently while honoring the max_pages budget.

    Pages are claimed (marked seen) before submission, so the budget can
    never be exceeded. The crawl is done when the queue is empty and no
    page is in flight; in-flight pages may still enqueue new ones.
    """
    with ThreadPoolExecutor(max_workers=page_threads, thread_name_prefix="Page") as pool:
        pending: set[Future] = set()
        while True:
            while len(seen_pages) < max_pages and len(pending) < page_threads:
                try:
                    page_url, depth = q_pages.get_nowait()
                except queue.Empty:
                    break
                if claim_page(page_url):
                    pending.add(pool.submit(process_page, page_url, depth))
            if not pending:
                break
            _done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in _done:
                future.result()


class _optional_renderer:
    def __init__(self, renderer):
        self.renderer = renderer

    def __enter__(self):
        if self.renderer is None:
            return None
        return self.renderer.__enter__()

    def __exit__(self, exc_type, exc, tb):
        if self.renderer is None:
            return None
        return self.renderer.__exit__(exc_type, exc, tb)


def _start_workers(
    *,
    threads: int,
    download_q: queue.Queue[tuple[str, Path] | None],
    enqueue_asset,
    options: CrawlOptions,
    root_netloc: str,
    user_agent: str,
    cache: CrawlCache,
    cache_lock: threading.Lock,
    records: list[SavedResource],
    record_lock: threading.Lock,
    stats: CrawlStats,
    stats_lock: threading.Lock,
    progress,
    manifest: CrawlManifest,
) -> list[threading.Thread]:
    def worker() -> None:
        session = create_session(
            user_agent=user_agent,
            cookies=options.cookies,
            headers=options.headers,
        )
        while True:
            item = download_q.get()
            try:
                if item is None:
                    return
                url, dest = item
                result = fetch_binary(
                    url,
                    dest,
                    session=session,
                    site_root=options.root,
                    root_netloc=root_netloc,
                    download_external_assets=options.download_external_assets,
                    external_domains=options.external_domains,
                    timeout=options.timeout,
                    max_asset_bytes=options.max_asset_bytes,
                    enqueue_asset=enqueue_asset,
                    update=options.update,
                    conditional_headers=cache.conditional_headers(url) if options.update else None,
                )
                if isinstance(result, FetchFailure):
                    manifest.record("asset", url, **result.manifest_fields())
                    if not result.skipped:
                        with stats_lock:
                            stats.errors += 1
                        progress.error()
                    continue
                manifest.record(
                    "asset",
                    url,
                    path=result.path,
                    status="cached" if result.not_modified else "downloaded",
                    status_code=result.status_code,
                    content_type=result.content_type,
                )
                if result.not_modified:
                    with stats_lock:
                        stats.assets_cached += 1
                    progress.asset_saved(cached=True)
                    continue
                with stats_lock:
                    stats.assets_written += 1
                progress.asset_saved()
                if options.update:
                    with cache_lock:
                        cache.update(
                            url=url,
                            path=result.path,
                            kind="asset",
                            status_code=result.status_code,
                            response_headers=result.headers,
                        )
                with record_lock:
                    records.append(
                        SavedResource(
                            url=url,
                            path=result.path,
                            kind="asset",
                            status_code=result.status_code,
                            content_type=result.content_type,
                        )
                    )
            except Exception as exc:
                log.exception("Failed to process asset %s", url)
                manifest.record(
                    "asset",
                    url,
                    status="failed",
                    reason="processing_error",
                    errors=[{"type": type(exc).__name__, "message": str(exc)}],
                )
                with stats_lock:
                    stats.errors += 1
                progress.error()
            finally:
                download_q.task_done()

    workers: list[threading.Thread] = []
    for index in range(max(1, threads)):
        thread = threading.Thread(target=worker, name=f"DL-{index + 1}", daemon=True)
        thread.start()
        workers.append(thread)
    return workers


def _load_robots(start_url: str, session, timeout: int) -> RobotFileParser | None:
    parsed = urlparse(start_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    parser = RobotFileParser()
    parser.set_url(robots_url)
    try:
        response = session.get(robots_url, timeout=timeout)
        if response.status_code >= 400:
            return None
        parser.parse(response.text.splitlines())
        return parser
    except Exception as exc:
        log.debug("Could not read robots.txt from %s: %s", robots_url, exc)
        return None


def _discover_references(
    soup,
    *,
    page_url: str,
    root: Path,
    root_netloc: str,
    enqueue_page,
    enqueue_asset,
    options: CrawlOptions,
) -> None:
    for tag in soup.find_all(True):
        if tag.name == "link" and not link_rel_is_fetchable(tag):
            continue

        for attr in ("src", "href", "data-src", "poster"):
            if not tag.has_attr(attr):
                continue
            _discover_attr(
                tag,
                attr,
                page_url=page_url,
                root=root,
                root_netloc=root_netloc,
                enqueue_page=enqueue_page,
                enqueue_asset=enqueue_asset,
                options=options,
            )

        if tag.name == "meta":
            _discover_meta_image(
                tag,
                page_url=page_url,
                root=root,
                root_netloc=root_netloc,
                enqueue_asset=enqueue_asset,
                options=options,
            )

        if tag.has_attr("srcset"):
            for entry in str(tag["srcset"]).split(","):
                if entry.strip():
                    _enqueue_asset_candidate(
                        entry.strip().split()[0],
                        page_url=page_url,
                        root=root,
                        root_netloc=root_netloc,
                        enqueue_asset=enqueue_asset,
                        options=options,
                        tag_name=tag.name,
                    )

        if tag.has_attr("style"):
            for asset in extract_css_assets(str(tag["style"])):
                _enqueue_asset_candidate(
                    asset,
                    page_url=page_url,
                    root=root,
                    root_netloc=root_netloc,
                    enqueue_asset=enqueue_asset,
                    options=options,
                )

        if tag.name == "style":
            css_text = tag.string or tag.get_text()
            for asset in extract_css_assets(css_text or ""):
                _enqueue_asset_candidate(
                    asset,
                    page_url=page_url,
                    root=root,
                    root_netloc=root_netloc,
                    enqueue_asset=enqueue_asset,
                    options=options,
                )


def _discover_attr(
    tag,
    attr: str,
    *,
    page_url: str,
    root: Path,
    root_netloc: str,
    enqueue_page,
    enqueue_asset,
    options: CrawlOptions,
) -> None:
    link_raw = str(tag.get(attr, "")).strip()
    if _skip_candidate(link_raw):
        return

    abs_url = canonicalize_url(link_raw, page_url)
    parsed = urlparse(abs_url)
    is_ext = not is_internal(abs_url, root_netloc)
    suffix = Path(parsed.path).suffix.lower()
    is_page = (
        tag.name == "a"
        and attr == "href"
        and not is_ext
        and (parsed.path.endswith("/") or suffix in PAGE_SUFFIXES)
    )

    if is_page:
        enqueue_page(abs_url)
        return

    _enqueue_asset_candidate(
        abs_url,
        page_url=page_url,
        root=root,
        root_netloc=root_netloc,
        enqueue_asset=enqueue_asset,
        options=options,
        already_absolute=True,
        tag_name=tag.name,
    )


def _discover_meta_image(
    tag,
    *,
    page_url: str,
    root: Path,
    root_netloc: str,
    enqueue_asset,
    options: CrawlOptions,
) -> None:
    content = str(tag.get("content", "")).strip()
    prop = (tag.get("property") or tag.get("name") or "").lower()
    if content and ("og:image" in prop or "twitter:image" in prop):
        _enqueue_asset_candidate(
            content,
            page_url=page_url,
            root=root,
            root_netloc=root_netloc,
            enqueue_asset=enqueue_asset,
            options=options,
        )


def _enqueue_asset_candidate(
    candidate: str,
    *,
    page_url: str,
    root: Path,
    root_netloc: str,
    enqueue_asset,
    options: CrawlOptions,
    already_absolute: bool = False,
    tag_name: str | None = None,
) -> None:
    if _skip_candidate(candidate):
        return
    abs_url = candidate if already_absolute else canonicalize_url(candidate, page_url)
    parsed = urlparse(abs_url)
    if _skip_candidate(abs_url):
        return

    is_ext = not is_internal(abs_url, root_netloc)
    if not is_ext and not parsed.path:
        return

    if (
        parsed.path
        and not parsed.path.lower().endswith(ASSET_EXTENSIONS)
        and tag_name not in EXTENSIONLESS_ASSET_TAGS
    ):
        return

    dest_path = cdn_local_path(parsed, root) if is_ext else to_local_asset_path(parsed, root)
    enqueue_asset(abs_url, dest_path)


def _skip_candidate(value: str) -> bool:
    return not value or value.startswith("#") or is_non_fetchable(value) or not is_httpish(value)
