from __future__ import annotations

import logging
import os
import re
from hashlib import sha256
from pathlib import Path
from urllib.parse import ParseResult, unquote, urlparse

from .constants import MAX_PATH_LEN, MAX_SEG_LEN
from .urltools import canonical_netloc

log = logging.getLogger(__name__)

_MULTI_DOTS_RE = re.compile(r"\.{3,}")
_BAD_SEG_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1F]')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def create_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def sanitize_segment(segment: str) -> str:
    segment = unquote(segment).strip()
    segment = segment.strip(" .")
    segment = _MULTI_DOTS_RE.sub(".", segment)
    segment = _BAD_SEG_CHARS_RE.sub("_", segment)

    if segment in ("", ".", ".."):
        segment = "_"
    if segment.upper() in _WINDOWS_RESERVED_NAMES:
        segment = f"_{segment}_"

    return segment


def shorten_segment(segment: str, limit: int = MAX_SEG_LEN) -> str:
    if len(segment) <= limit:
        return segment
    path = Path(segment)
    digest = sha256(segment.encode("utf-8")).hexdigest()[:12]
    keep = max(0, limit - len(path.suffix) - 13)
    return f"{path.stem[:keep]}-{digest}{path.suffix}"


def rel_url(target: Path, base_dir: Path) -> str:
    try:
        rel = os.path.relpath(target, base_dir)
    except ValueError:
        return target.as_posix()
    return Path(rel).as_posix()


def _safe_parts(rel: str) -> tuple[str, ...]:
    return tuple(shorten_segment(sanitize_segment(part)) for part in Path(rel).parts)


def _shorten_path_if_needed(local_path: Path, source_url: str) -> Path:
    if len(str(local_path)) <= MAX_PATH_LEN:
        return local_path

    digest = sha256(source_url.encode("utf-8")).hexdigest()[:16]
    leaf = shorten_segment(f"{local_path.stem}-{digest}{local_path.suffix}")
    return local_path.with_name(leaf)


def to_local_path(parsed: ParseResult, site_root: Path) -> Path:
    rel = parsed.path.lstrip("/")
    if not rel:
        rel = "index.html"
    elif rel.endswith("/"):
        rel += "index.html"
    elif not Path(rel).suffix:
        rel += ".html"

    if parsed.query:
        digest = sha256(parsed.query.encode("utf-8")).hexdigest()[:10]
        path = Path(rel)
        rel = str(path.with_name(f"{path.stem}-q{digest}{path.suffix}"))

    local_path = site_root / Path(*_safe_parts(rel))
    return _shorten_path_if_needed(local_path, parsed.geturl())


def to_local_asset_path(parsed: ParseResult, site_root: Path) -> Path:
    rel = parsed.path.lstrip("/")
    if not rel:
        rel = "index"
    elif rel.endswith("/"):
        rel += "index"

    if parsed.query:
        digest = sha256(parsed.query.encode("utf-8")).hexdigest()[:10]
        path = Path(rel)
        name = f"{path.stem}-q{digest}{path.suffix}" if path.suffix else f"{path.name}-q{digest}"
        rel = str(path.with_name(name))

    local_path = site_root / Path(*_safe_parts(rel))
    return _shorten_path_if_needed(local_path, parsed.geturl())


def cdn_local_path(parsed: ParseResult, site_root: Path) -> Path:
    rel = parsed.path.lstrip("/")
    if not rel:
        rel = "index"
    elif rel.endswith("/"):
        rel += "index"

    if parsed.query:
        digest = sha256(parsed.query.encode("utf-8")).hexdigest()[:10]
        path = Path(rel)
        name = f"{path.stem}-q{digest}{path.suffix}" if path.suffix else f"{path.name}-q{digest}"
        rel = str(path.with_name(name))

    netloc = sanitize_segment(canonical_netloc(parsed))
    local_path = site_root / "cdn" / netloc / Path(*_safe_parts(rel))
    return _shorten_path_if_needed(local_path, parsed.geturl())


def safe_write_text(path: Path, text: str, encoding: str = "utf-8") -> Path:
    try:
        path.write_text(text, encoding=encoding)
        return path
    except OSError as exc:
        log.warning("Write failed for %s: %s. Falling back to hashed leaf.", path, exc)
        digest = sha256(str(path).encode("utf-8")).hexdigest()[:16]
        fallback = path.with_name(shorten_segment(f"{path.stem}-{digest}{path.suffix}"))
        create_dir(fallback.parent)
        fallback.write_text(text, encoding=encoding)
        return fallback


def make_root(url: str, custom: str | None) -> Path:
    return Path(custom) if custom else Path(urlparse(url).netloc.replace(".", "_"))
