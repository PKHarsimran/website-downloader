from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return


class ConditionalHandler(QuietHandler):
    etag = '"test-etag"'

    def end_headers(self) -> None:
        self.send_header("ETag", self.etag)
        self.send_header("Last-Modified", "Wed, 21 Oct 2015 07:28:00 GMT")
        super().end_headers()

    def do_GET(self) -> None:
        if self.headers.get("If-None-Match") == self.etag:
            self.send_response(304)
            self.end_headers()
            return
        super().do_GET()


@contextmanager
def serve_directory(directory: Path) -> Iterator[str]:
    cwd = Path.cwd()
    os.chdir(directory)
    server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        os.chdir(cwd)


@contextmanager
def serve_directory_with_handler(
    directory: Path,
    handler: type[SimpleHTTPRequestHandler],
) -> Iterator[str]:
    cwd = Path.cwd()
    os.chdir(directory)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        os.chdir(cwd)


@pytest.fixture
def local_site(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    site = tmp_path / "site"
    (site / "assets").mkdir(parents=True)
    (site / "img").mkdir()
    (site / "index.html").write_text(
        """
        <html>
          <head>
            <link rel="stylesheet" href="/assets/site.css">
            <link rel="canonical" href="https://example.invalid/keep">
            <meta property="og:image" content="/img/social.png">
          </head>
          <body style="background:url('/img/body.png')">
            <a href="/about.html">About</a>
            <img src="/img/logo.png" srcset="/img/logo.png 1x, /img/logo@2x.png 2x">
            <script src="/assets/app.js"></script>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    (site / "about.html").write_text("<html><body>About</body></html>", encoding="utf-8")
    (site / "sitemap-only.html").write_text(
        "<html><body>Only in sitemap</body></html>",
        encoding="utf-8",
    )
    (site / "sitemap.xml").write_text(
        """
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>/sitemap-only.html</loc></url>
        </urlset>
        """,
        encoding="utf-8",
    )
    (site / "assets" / "site.css").write_text(
        "@import '/assets/extra.css'; body { background: url('../img/bg.png'); }",
        encoding="utf-8",
    )
    (site / "assets" / "extra.css").write_text("main { color: #111; }", encoding="utf-8")
    (site / "assets" / "app.js").write_text(
        "const logo = '/img/from-js.png'; const api = '/api/data';",
        encoding="utf-8",
    )
    for name in ["logo.png", "logo@2x.png", "social.png", "body.png", "bg.png", "from-js.png"]:
        (site / "img" / name).write_bytes(b"png")

    with serve_directory(site) as base_url:
        yield base_url, site


@pytest.fixture
def conditional_site(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    site = tmp_path / "conditional-site"
    site.mkdir()
    (site / "index.html").write_text(
        """
        <html>
          <head><link rel="stylesheet" href="/style.css"></head>
          <body>Cached <a href="/other">Other</a></body>
        </html>
        """,
        encoding="utf-8",
    )
    (site / "other").write_text("<html><body>Other page</body></html>", encoding="utf-8")
    (site / "style.css").write_text("body { color: red; }", encoding="utf-8")

    with serve_directory_with_handler(site, ConditionalHandler) as base_url:
        yield base_url, site
