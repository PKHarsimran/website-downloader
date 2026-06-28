from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .constants import LOG_FMT
from .crawler import CrawlOptions, crawl_site
from .paths import make_root
from .urltools import normalize_external_domains

log = logging.getLogger(__name__)


def configure_logging(log_file: str = "web_scraper.log") -> None:
    logging.basicConfig(
        filename=log_file,
        level=logging.DEBUG,
        format=LOG_FMT,
        datefmt="%H:%M:%S",
        force=True,
    )
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(LOG_FMT, datefmt="%H:%M:%S"))
    logging.getLogger().addHandler(console)


def parse_cookie_header(cookie_header: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in cookie_header.strip().split(";"):
        entry = part.strip()
        if not entry:
            continue
        if "=" not in entry:
            raise ValueError(f"Invalid cookie entry: {entry}")
        name, value = entry.split("=", 1)
        cookies[name.strip()] = value.strip()
    return cookies


def parse_header(header: str) -> tuple[str, str]:
    if ":" not in header:
        raise ValueError(f"Invalid header entry: {header}")
    name, value = header.split(":", 1)
    name = name.strip()
    value = value.strip()
    if not name or not value:
        raise ValueError(f"Invalid header entry: {header}")
    return name, value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recursively mirror a website for offline use.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--url", required=True, help="Starting URL to crawl.")
    parser.add_argument(
        "--destination",
        default=None,
        help="Output folder. Defaults to a folder derived from the URL.",
    )
    parser.add_argument("--max-pages", type=int, default=50, help="Maximum HTML pages to crawl.")
    parser.add_argument("--threads", type=int, default=6, help="Concurrent asset download workers.")
    parser.add_argument(
        "--download-external-assets",
        action="store_true",
        help="Download external CDN/static assets and rewrite allowed links locally.",
    )
    parser.add_argument(
        "--external-domains",
        nargs="+",
        default=None,
        help="Whitelist external domains to download from. Implies external downloads.",
    )
    parser.add_argument(
        "--cookie",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="Set cookies to send with all requests. Can be repeated.",
    )
    parser.add_argument(
        "--cookie-file",
        action="append",
        default=[],
        metavar="FILE",
        help="Read cookies from a file containing cookie header syntax.",
    )
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        metavar="NAME: VALUE",
        help="Add a custom HTTP header. Can be repeated, e.g. --header 'Authorization: Bearer ...'.",
    )
    parser.add_argument(
        "--delay", type=float, default=0.0, help="Delay in seconds between page fetches."
    )
    parser.add_argument(
        "--max-asset-bytes",
        type=int,
        default=None,
        help="Skip assets larger than this many bytes.",
    )
    parser.add_argument(
        "--user-agent", default=None, help="Override the default browser-like User-Agent."
    )
    parser.add_argument(
        "--respect-robots",
        action="store_true",
        help="Respect robots.txt for HTML page crawling.",
    )
    parser.add_argument(
        "--render-js",
        action="store_true",
        help="Render pages with Playwright before parsing. Requires the render extra.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Alias for --render-js. Uses headless Playwright rendering.",
    )
    parser.add_argument(
        "--render-wait-until",
        choices=["commit", "domcontentloaded", "load", "networkidle"],
        default="networkidle",
        help="Playwright page.goto wait condition for --render-js.",
    )
    parser.add_argument(
        "--render-timeout-ms",
        type=int,
        default=30000,
        help="Playwright navigation timeout for --render-js.",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Use ETag/Last-Modified cache metadata to skip unchanged resources.",
    )
    parser.add_argument(
        "--cache-file",
        type=Path,
        default=None,
        help="Path to the update metadata cache. Defaults to DESTINATION/.website-downloader-cache.json.",
    )
    parser.add_argument(
        "--sitemap",
        nargs="?",
        const="auto",
        default=None,
        metavar="URL_OR_FILE",
        help="Seed the crawl from sitemap.xml. Omit the value to use /sitemap.xml.",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help="Show a live Rich progress dashboard when the ux extra is installed.",
    )
    parser.add_argument(
        "--zip-output",
        type=Path,
        default=None,
        help="Write the finished mirror to a zip archive.",
    )
    parser.add_argument(
        "--warc-output",
        type=Path,
        default=None,
        help="Write downloaded resources to a simple WARC 1.1 response archive.",
    )
    return parser.parse_args(argv)


def load_cookies(cookie_values: list[str], cookie_files: list[str]) -> dict[str, str]:
    cookie_dict: dict[str, str] = {}
    for cookie in cookie_values:
        cookie_dict.update(parse_cookie_header(cookie))

    for cookie_path in cookie_files:
        raw = Path(cookie_path).expanduser().read_text(encoding="utf-8").strip()
        cookie_dict.update(parse_cookie_header(raw))

    return cookie_dict


def load_headers(header_values: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for header in header_values:
        name, value = parse_header(header)
        headers[name] = value
    return headers


def validate_args(args: argparse.Namespace) -> None:
    if args.max_pages < 1:
        raise ValueError("--max-pages must be >= 1")
    if args.threads < 1:
        raise ValueError("--threads must be >= 1")
    if args.delay < 0:
        raise ValueError("--delay must be >= 0")
    if args.max_asset_bytes is not None and args.max_asset_bytes < 1:
        raise ValueError("--max-asset-bytes must be >= 1")
    if args.render_timeout_ms < 1:
        raise ValueError("--render-timeout-ms must be >= 1")
    if args.cache_file is not None and not args.update:
        raise ValueError("--cache-file requires --update")


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = parse_args(argv)
    try:
        validate_args(args)
        cookies = load_cookies(args.cookie, args.cookie_file)
        headers = load_headers(args.header)
    except (OSError, ValueError) as exc:
        log.error("%s", exc)
        return 2

    download_external_assets = args.download_external_assets or args.external_domains is not None
    render_js = args.render_js or args.headless
    options = CrawlOptions(
        start_url=args.url,
        root=make_root(args.url, args.destination),
        max_pages=args.max_pages,
        threads=args.threads,
        download_external_assets=download_external_assets,
        external_domains=normalize_external_domains(args.external_domains),
        cookies=cookies,
        headers=headers,
        delay=args.delay,
        max_asset_bytes=args.max_asset_bytes,
        user_agent=args.user_agent,
        respect_robots=args.respect_robots,
        render_js=render_js,
        render_wait_until=args.render_wait_until,
        render_timeout_ms=args.render_timeout_ms,
        update=args.update,
        cache_file=args.cache_file,
        sitemap=args.sitemap,
        progress=args.progress,
        zip_output=args.zip_output,
        warc_output=args.warc_output,
    )
    crawl_site(options)
    return 0
