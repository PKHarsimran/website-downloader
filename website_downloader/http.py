from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from .constants import CHUNK_SIZE, DEFAULT_HEADERS, HTML_PARSER, TIMEOUT
from .paths import create_dir, shorten_segment
from .rewrite import EnqueueAsset, rewrite_css_text, rewrite_js_text
from .urltools import is_allowed_external, is_httpish, is_internal, is_non_fetchable

log = logging.getLogger(__name__)


@dataclass
class HtmlFetchResult:
    soup: BeautifulSoup | None
    status_code: int | None
    headers: dict[str, str]
    not_modified: bool = False
    content_type: str | None = None


@dataclass
class BinaryFetchResult:
    path: Path
    status_code: int | None
    headers: dict[str, str]
    not_modified: bool = False
    content_type: str | None = None


@dataclass
class FetchFailure:
    reason: str
    status_code: int | None = None
    message: str | None = None
    error_type: str | None = None
    skipped: bool = False

    def manifest_fields(self) -> dict:
        return {
            "status": "skipped" if self.skipped else "failed",
            "status_code": self.status_code,
            "reason": self.reason,
            "errors": (
                [{"type": self.error_type, "message": self.message}] if not self.skipped else []
            ),
        }


class AssetTooLargeError(ValueError):
    pass


def create_session(
    *,
    user_agent: str | None = None,
    cookies: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> requests.Session:
    session = requests.Session()
    retry_strategy = Retry(
        total=5,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(DEFAULT_HEADERS)
    if user_agent:
        session.headers["User-Agent"] = user_agent
    if headers:
        session.headers.update(headers)
    if cookies:
        session.cookies.update(cookies)
    log.debug("Accept-Encoding configured as: %s", session.headers.get("Accept-Encoding"))
    return session


def fetch_html(
    url: str,
    *,
    session: requests.Session,
    timeout: int = TIMEOUT,
    renderer=None,
    conditional_headers: dict[str, str] | None = None,
    cached_path: Path | None = None,
) -> HtmlFetchResult | FetchFailure:
    response = None
    try:
        if renderer is not None:
            rendered = renderer.fetch(url)
            if rendered.status_code is not None and rendered.status_code >= 400:
                return FetchFailure(
                    "http_error",
                    rendered.status_code,
                    f"Rendered navigation returned HTTP {rendered.status_code}",
                    "HTTPError",
                )
            return HtmlFetchResult(
                soup=BeautifulSoup(rendered.html, HTML_PARSER),
                status_code=rendered.status_code,
                headers=rendered.headers,
                content_type=rendered.headers.get("content-type", "text/html"),
            )
        response = session.get(url, timeout=timeout, headers=conditional_headers)
        headers = dict(response.headers)
        if response.status_code == 304:
            soup = _read_cached_html(cached_path)
            if soup is not None:
                return HtmlFetchResult(
                    soup=soup,
                    status_code=304,
                    headers=headers,
                    not_modified=True,
                    content_type=response.headers.get("Content-Type"),
                )
            log.info("Server sent 304 but local copy is missing; refetching %s", url)
            response = session.get(url, timeout=timeout)
            headers = dict(response.headers)
        response.raise_for_status()
        # Parse from bytes so BeautifulSoup can honor <meta charset> and BOMs;
        # response.text falls back to ISO-8859-1 when no charset header is sent.
        return HtmlFetchResult(
            soup=BeautifulSoup(response.content, HTML_PARSER),
            status_code=response.status_code,
            headers=headers,
            content_type=response.headers.get("Content-Type"),
        )
    except Exception as exc:
        log.warning("HTTP error for %s: %s", url, exc)
        return FetchFailure(
            "fetch_error",
            response.status_code if response is not None else None,
            str(exc),
            type(exc).__name__,
        )


def fetch_binary(
    url: str,
    dest: Path,
    *,
    session: requests.Session,
    root_netloc: str,
    site_root: Path,
    download_external_assets: bool = False,
    external_domains: set[str] | None = None,
    timeout: int = TIMEOUT,
    max_asset_bytes: int | None = None,
    enqueue_asset: EnqueueAsset | None = None,
    update: bool = False,
    conditional_headers: dict[str, str] | None = None,
) -> BinaryFetchResult | FetchFailure:
    is_ext = not is_internal(url, root_netloc)
    if is_ext and not download_external_assets:
        log.debug("Blocked external asset because external downloads are disabled: %s", url)
        return FetchFailure("external_downloads_disabled", skipped=True)
    if is_ext and not is_allowed_external(url, external_domains):
        log.info("Blocked external asset outside whitelist: %s", url)
        return FetchFailure("external_domain_not_allowed", skipped=True)
    if is_non_fetchable(url) or not is_httpish(url):
        log.debug("Skipping non-fetchable URL: %s", url)
        return FetchFailure("non_fetchable_url", skipped=True)
    if dest.exists() and not update:
        # Already mirrored in a previous run; report it as cached.
        return BinaryFetchResult(
            path=dest,
            status_code=None,
            headers={},
            not_modified=True,
        )

    response = None
    try:
        response = session.get(url, timeout=timeout, stream=True, headers=conditional_headers)
        headers = dict(response.headers)
        if response.status_code == 304:
            if dest.exists():
                return BinaryFetchResult(
                    path=dest,
                    status_code=304,
                    headers=headers,
                    not_modified=True,
                    content_type=response.headers.get("Content-Type"),
                )
            # A 304 has no body; without the local file we must refetch,
            # otherwise we would write an empty asset.
            log.info("Server sent 304 but local copy is missing; refetching %s", url)
            response = session.get(url, timeout=timeout, stream=True)
            headers = dict(response.headers)
        response.raise_for_status()
        if _too_large(response, max_asset_bytes):
            log.warning("Skipping asset over size limit: %s", url)
            return FetchFailure("max_asset_bytes", response.status_code, skipped=True)

        create_dir(dest.parent)
        final_dest = _write_stream(response, dest, max_asset_bytes=max_asset_bytes)
        log.debug("Saved resource: %s -> %s", url, final_dest)

        suffix = final_dest.suffix.lower()
        if suffix == ".css":
            _rewrite_downloaded_css(
                final_dest,
                url,
                site_root=site_root,
                root_netloc=root_netloc,
                download_external_assets=download_external_assets,
                external_domains=external_domains,
                enqueue_asset=enqueue_asset,
            )
        elif suffix in {".js", ".mjs"}:
            _rewrite_downloaded_js(
                final_dest,
                url,
                site_root=site_root,
                root_netloc=root_netloc,
                download_external_assets=download_external_assets,
                external_domains=external_domains,
                enqueue_asset=enqueue_asset,
            )
        return BinaryFetchResult(
            path=final_dest,
            status_code=response.status_code,
            headers=headers,
            content_type=response.headers.get("Content-Type"),
        )
    except Exception as exc:
        log.error("Failed to save %s: %s", url, exc)
        return FetchFailure(
            "max_asset_bytes" if isinstance(exc, AssetTooLargeError) else "fetch_error",
            response.status_code if response is not None else None,
            str(exc),
            type(exc).__name__,
            skipped=isinstance(exc, AssetTooLargeError),
        )
    finally:
        if response is not None:
            response.close()


def _read_cached_html(cached_path: Path | None) -> BeautifulSoup | None:
    if cached_path is None or not cached_path.exists():
        return None
    return BeautifulSoup(cached_path.read_text(encoding="utf-8", errors="ignore"), HTML_PARSER)


def _too_large(response: requests.Response, max_asset_bytes: int | None) -> bool:
    if max_asset_bytes is None:
        return False
    content_length = response.headers.get("Content-Length")
    if not content_length:
        return False
    try:
        return int(content_length) > max_asset_bytes
    except ValueError:
        return False


def _write_stream(
    response: requests.Response,
    dest: Path,
    *,
    max_asset_bytes: int | None,
) -> Path:
    final_dest = dest
    try:
        _stream_to_path(response, final_dest, max_asset_bytes=max_asset_bytes)
        return final_dest
    except OSError as exc:
        log.warning("Binary write failed for %s: %s. Using fallback.", dest, exc)
        digest = dest.name.encode("utf-8").hex()[:16]
        final_dest = dest.with_name(shorten_segment(f"{dest.stem}-{digest}{dest.suffix}"))
        create_dir(final_dest.parent)
        _stream_to_path(response, final_dest, max_asset_bytes=max_asset_bytes)
        return final_dest


def _stream_to_path(
    response: requests.Response,
    dest: Path,
    *,
    max_asset_bytes: int | None,
) -> None:
    written = 0
    with dest.open("wb") as file:
        for chunk in response.iter_content(CHUNK_SIZE):
            if not chunk:
                continue
            written += len(chunk)
            if max_asset_bytes is not None and written > max_asset_bytes:
                file.close()
                dest.unlink(missing_ok=True)
                raise AssetTooLargeError(f"Asset exceeds --max-asset-bytes ({max_asset_bytes})")
            file.write(chunk)


def _rewrite_downloaded_css(
    path: Path,
    base_url: str,
    *,
    site_root: Path,
    root_netloc: str,
    download_external_assets: bool,
    external_domains: set[str] | None,
    enqueue_asset: EnqueueAsset | None,
) -> None:
    try:
        css_text = path.read_text(encoding="utf-8", errors="ignore")
        rewritten = rewrite_css_text(
            css_text,
            base_url,
            site_root=site_root,
            root_netloc=root_netloc,
            base_dir=path.parent,
            download_external_assets=download_external_assets,
            external_domains=external_domains,
            enqueue_asset=enqueue_asset,
        )
        if rewritten != css_text:
            path.write_text(rewritten, encoding="utf-8")
    except Exception as exc:
        log.debug("CSS rewrite failed for %s: %s", path, exc)


def _rewrite_downloaded_js(
    path: Path,
    base_url: str,
    *,
    site_root: Path,
    root_netloc: str,
    download_external_assets: bool,
    external_domains: set[str] | None,
    enqueue_asset: EnqueueAsset | None,
) -> None:
    try:
        js_text = path.read_text(encoding="utf-8", errors="ignore")
        rewritten = rewrite_js_text(
            js_text,
            base_url,
            site_root=site_root,
            root_netloc=root_netloc,
            base_dir=path.parent,
            download_external_assets=download_external_assets,
            external_domains=external_domains,
            enqueue_asset=enqueue_asset,
        )
        if rewritten != js_text:
            path.write_text(rewritten, encoding="utf-8")
    except Exception as exc:
        log.debug("JS rewrite failed for %s: %s", path, exc)
