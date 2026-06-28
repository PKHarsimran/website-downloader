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
