from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .constants import (
    ASSET_EXTENSIONS,
    CSS_IMPORT_RE,
    CSS_URL_RE,
    JS_ABS_URL_RE,
    JS_URL_RE,
    RESOURCE_LINK_RELS,
)
from .paths import cdn_local_path, rel_url, to_local_asset_path, to_local_path
from .urltools import (
    canonical_netloc,
    canonicalize_url,
    is_allowed_external,
    is_httpish,
    is_internal,
    is_non_fetchable,
    protocol_fix,
)

log = logging.getLogger(__name__)

EnqueueAsset = Callable[[str, Path], None]


def _skippable_url(url: str) -> bool:
    return (
        not url
        or url.startswith("#")
        or url.startswith(("data:", "javascript:", "about:"))
        or is_non_fetchable(url)
        or not is_httpish(url)
    )


def _map_asset_url(
    url_part: str,
    base_url: str,
    *,
    site_root: Path,
    root_netloc: str,
    base_dir: Path,
    download_external_assets: bool,
    external_domains: set[str] | None = None,
    enqueue_asset: EnqueueAsset | None = None,
) -> str | None:
    if _skippable_url(url_part):
        return None

    fixed_url = protocol_fix(url_part, base_url)
    if _skippable_url(fixed_url):
        return None

    abs_url = canonicalize_url(fixed_url, base_url)
    parsed = urlparse(abs_url)
    if not parsed.path.lower().endswith(ASSET_EXTENSIONS):
        return None

    is_ext = not is_internal(abs_url, root_netloc)
    if is_ext and (
        not download_external_assets or not is_allowed_external(abs_url, external_domains)
    ):
        return None

    local_path = (
        cdn_local_path(parsed, site_root) if is_ext else to_local_asset_path(parsed, site_root)
    )
    if enqueue_asset is not None:
        enqueue_asset(abs_url, local_path)

    rel = rel_url(local_path, base_dir)
    if parsed.fragment:
        rel = f"{rel}#{parsed.fragment}"
    return rel


def rewrite_css_text(
    css_text: str,
    base_url: str,
    *,
    site_root: Path,
    root_netloc: str,
    base_dir: Path,
    download_external_assets: bool,
    external_domains: set[str] | None = None,
    enqueue_asset: EnqueueAsset | None = None,
) -> str:
    def repl_url(match: re.Match[str]) -> str:
        raw = match.group(1).strip()
        quote = ""
        url_part = raw
        if len(raw) >= 2 and raw[0] in ("'", '"') and raw[-1] == raw[0]:
            quote = raw[0]
            url_part = raw[1:-1].strip()

        mapped = _map_asset_url(
            url_part,
            base_url,
            site_root=site_root,
            root_netloc=root_netloc,
            base_dir=base_dir,
            download_external_assets=download_external_assets,
            external_domains=external_domains,
            enqueue_asset=enqueue_asset,
        )
        if mapped is None:
            return match.group(0)
        return f"url({quote}{mapped}{quote})" if quote else f"url({mapped})"

    def repl_import(match: re.Match[str]) -> str:
        url_part = match.group(1).strip().strip("'\"")
        mapped = _map_asset_url(
            url_part,
            base_url,
            site_root=site_root,
            root_netloc=root_netloc,
            base_dir=base_dir,
            download_external_assets=download_external_assets,
            external_domains=external_domains,
            enqueue_asset=enqueue_asset,
        )
        if mapped is None:
            return match.group(0)
        return f'@import "{mapped}";'

    return CSS_IMPORT_RE.sub(repl_import, CSS_URL_RE.sub(repl_url, css_text))


def rewrite_js_text(
    js_text: str,
    base_url: str,
    *,
    site_root: Path,
    root_netloc: str,
    base_dir: Path,
    download_external_assets: bool,
    external_domains: set[str] | None = None,
    enqueue_asset: EnqueueAsset | None = None,
) -> str:
    def replace(match: re.Match[str]) -> str:
        url_part = match.group(1)
        mapped = _map_asset_url(
            url_part,
            base_url,
            site_root=site_root,
            root_netloc=root_netloc,
            base_dir=base_dir,
            download_external_assets=download_external_assets,
            external_domains=external_domains,
            enqueue_asset=enqueue_asset,
        )
        if mapped is None:
            return match.group(0)
        quote = match.group(0)[0]
        return f"{quote}{mapped}{quote}"

    return JS_ABS_URL_RE.sub(replace, JS_URL_RE.sub(replace, js_text))


def link_rel_is_fetchable(tag) -> bool:
    if tag.name != "link":
        return True
    rel = tag.get("rel", [])
    if isinstance(rel, str):
        rel = [rel]
    return bool({item.lower() for item in rel} & RESOURCE_LINK_RELS)


def rewrite_links(
    soup: BeautifulSoup,
    page_url: str,
    site_root: Path,
    page_dir: Path,
    download_external_assets: bool = False,
    external_domains: set[str] | None = None,
) -> None:
    root_netloc = canonical_netloc(urlparse(page_url))

    base_tag = soup.find("base")
    if base_tag is not None and base_tag.has_attr("href"):
        base_tag.decompose()

    def strip_sri_and_cors(tag) -> None:
        for attr in ("integrity", "crossorigin"):
            if tag.has_attr(attr):
                del tag[attr]

    for tag in soup.find_all(True):
        if tag.name == "link" and not link_rel_is_fetchable(tag):
            continue

        if tag.name == "meta":
            content = str(tag.get("content", "")).strip()
            prop = (tag.get("property") or tag.get("name") or "").lower()
            if content and ("og:image" in prop or "twitter:image" in prop):
                mapped = _map_asset_url(
                    content,
                    page_url,
                    site_root=site_root,
                    root_netloc=root_netloc,
                    base_dir=page_dir,
                    download_external_assets=download_external_assets,
                    external_domains=external_domains,
                )
                if mapped is not None:
                    tag["content"] = mapped

        for attr in ("src", "href", "data-src", "poster"):
            if not tag.has_attr(attr):
                continue

            original = str(tag.get(attr, "")).strip()
            if _skippable_url(original):
                continue

            abs_url = canonicalize_url(protocol_fix(original, page_url), page_url)
            parsed = urlparse(abs_url)
            is_ext = not is_internal(abs_url, root_netloc)
            treat_as_page = tag.name == "a" and attr == "href"
            rewritten_external_asset = False

            if is_ext and treat_as_page:
                continue
            if is_ext and (
                not download_external_assets or not is_allowed_external(abs_url, external_domains)
            ):
                continue

            if is_ext:
                local_path = cdn_local_path(parsed, site_root)
                rewritten_external_asset = True
            else:
                local_path = (
                    to_local_path(parsed, site_root)
                    if treat_as_page
                    else to_local_asset_path(parsed, site_root)
                )

            rel = rel_url(local_path, page_dir)
            if parsed.fragment:
                rel = f"{rel}#{parsed.fragment}"
            tag[attr] = rel

            if rewritten_external_asset and tag.name in {"script", "link"}:
                strip_sri_and_cors(tag)

        if tag.has_attr("srcset"):
            tag["srcset"] = _rewrite_srcset(
                str(tag["srcset"]),
                page_url,
                site_root=site_root,
                root_netloc=root_netloc,
                page_dir=page_dir,
                download_external_assets=download_external_assets,
                external_domains=external_domains,
            )

        if tag.has_attr("style"):
            style = str(tag["style"])
            tag["style"] = rewrite_css_text(
                style,
                page_url,
                site_root=site_root,
                root_netloc=root_netloc,
                base_dir=page_dir,
                download_external_assets=download_external_assets,
                external_domains=external_domains,
            )

    for style_tag in soup.find_all("style"):
        css_text = style_tag.string or style_tag.get_text()
        if not css_text:
            continue
        try:
            rewritten = rewrite_css_text(
                css_text,
                page_url,
                site_root=site_root,
                root_netloc=root_netloc,
                base_dir=page_dir,
                download_external_assets=download_external_assets,
                external_domains=external_domains,
            )
            if rewritten != css_text:
                style_tag.string = rewritten
        except Exception as exc:
            log.debug("Inline <style> rewrite failed on %s: %s", page_url, exc)


def _rewrite_srcset(
    srcset: str,
    page_url: str,
    *,
    site_root: Path,
    root_netloc: str,
    page_dir: Path,
    download_external_assets: bool,
    external_domains: set[str] | None,
) -> str:
    entries: list[str] = []
    for entry in srcset.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split()
        mapped = _map_asset_url(
            parts[0],
            page_url,
            site_root=site_root,
            root_netloc=root_netloc,
            base_dir=page_dir,
            download_external_assets=download_external_assets,
            external_domains=external_domains,
        )
        if mapped is not None:
            parts[0] = mapped
        entries.append(" ".join(parts))
    return ", ".join(entries)


def extract_css_assets(css_text: str) -> list[str]:
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
