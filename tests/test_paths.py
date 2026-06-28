from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from website_downloader.paths import (
    cdn_local_path,
    sanitize_segment,
    to_local_asset_path,
    to_local_path,
)


def test_to_local_path_adds_html_and_query_hash(tmp_path: Path) -> None:
    local = to_local_path(urlparse("https://example.com/products?id=1"), tmp_path)
    assert local.parent == tmp_path
    assert local.name.startswith("products-q")
    assert local.suffix == ".html"


def test_to_local_asset_path_preserves_extensionless_assets(tmp_path: Path) -> None:
    local = to_local_asset_path(urlparse("https://example.com/assets/font?id=1"), tmp_path)
    assert local.name.startswith("font-q")
    assert local.suffix == ""


def test_cdn_local_path_namespaces_by_domain(tmp_path: Path) -> None:
    local = cdn_local_path(urlparse("https://CDN.example.com/lib/app.js"), tmp_path)
    assert local == tmp_path / "cdn" / "cdn.example.com" / "lib" / "app.js"


def test_sanitize_segment_neutralizes_windows_reserved_names() -> None:
    assert sanitize_segment("CON") == "_CON_"
    assert sanitize_segment("../bad:name") == "_bad_name"
