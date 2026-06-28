from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from website_downloader.rewrite import rewrite_css_text, rewrite_js_text, rewrite_links
from website_downloader.urltools import canonical_netloc


def test_rewrite_css_queues_nested_asset(tmp_path: Path) -> None:
    queued: list[tuple[str, Path]] = []
    rewritten = rewrite_css_text(
        "body { background: url('../img/bg.png'); }",
        "https://example.com/assets/site.css",
        site_root=tmp_path,
        root_netloc="example.com",
        base_dir=tmp_path / "assets",
        download_external_assets=False,
        enqueue_asset=lambda url, path: queued.append((url, path)),
    )

    assert "../img/bg.png" in rewritten
    assert queued == [("https://example.com/img/bg.png", tmp_path / "img" / "bg.png")]


def test_rewrite_js_leaves_api_routes_alone(tmp_path: Path) -> None:
    rewritten = rewrite_js_text(
        "const img = '/static/logo.png'; const api = '/api/data';",
        "https://example.com/app.js",
        site_root=tmp_path,
        root_netloc="example.com",
        base_dir=tmp_path,
        download_external_assets=False,
    )

    assert "'static/logo.png'" in rewritten
    assert "'/api/data'" in rewritten


def test_rewrite_links_skips_canonical_and_rewrites_assets(tmp_path: Path) -> None:
    soup = BeautifulSoup(
        """
        <html>
          <head>
            <base href="https://example.com/">
            <link rel="canonical" href="https://example.com/page">
            <link rel="stylesheet" href="/assets/site.css" integrity="abc" crossorigin>
          </head>
          <body><a href="/about">About</a><img src="/img/logo.png"></body>
        </html>
        """,
        "html.parser",
    )
    rewrite_links(
        soup,
        "https://example.com/index.html",
        tmp_path,
        tmp_path,
        download_external_assets=False,
    )

    assert soup.find("base") is None
    assert soup.find("link", rel="canonical")["href"] == "https://example.com/page"
    assert soup.find("link", rel="stylesheet")["href"] == "assets/site.css"
    assert soup.find("a")["href"] == "about.html"
    assert soup.find("img")["src"] == "img/logo.png"


def test_rewrite_external_asset_strips_sri(tmp_path: Path) -> None:
    soup = BeautifulSoup(
        '<script src="https://cdn.example.com/app.js" integrity="abc" crossorigin="anonymous"></script>',
        "html.parser",
    )
    rewrite_links(
        soup,
        "https://example.com/index.html",
        tmp_path,
        tmp_path,
        download_external_assets=True,
        external_domains={"cdn.example.com"},
    )

    script = soup.find("script")
    assert script["src"] == "cdn/cdn.example.com/app.js"
    assert "integrity" not in script.attrs
    assert "crossorigin" not in script.attrs


def test_canonical_netloc_import_keeps_public_api_available() -> None:
    assert canonical_netloc(urlparse("https://EXAMPLE.com:443/a")) == "example.com"
