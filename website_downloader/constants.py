from __future__ import annotations

import re
from importlib.util import find_spec

HAS_BROTLI = find_spec("brotli") is not None or find_spec("brotlicffi") is not None

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

CSS_URL_RE = re.compile(r"url\(([^)]+)\)")
CSS_IMPORT_RE = re.compile(
    r"""@import\s+(?:url\()?['"]?([^'"\);]+)['"]?\)?\s*;""",
    re.IGNORECASE,
)

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

RESOURCE_LINK_RELS = {
    "stylesheet",
    "icon",
    "shortcut",
    "apple-touch-icon",
    "preload",
    "modulepreload",
    "manifest",
}

DEFAULT_ACCEPT_ENCODING = "gzip, deflate, br" if HAS_BROTLI else "gzip, deflate"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": DEFAULT_ACCEPT_ENCODING,
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

TIMEOUT = 15
CHUNK_SIZE = 8192
MAX_PATH_LEN = 240
MAX_SEG_LEN = 120
