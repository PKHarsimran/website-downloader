from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

from website_downloader.crawler import CrawlOptions, crawl_site


def test_crawl_site_mirrors_local_fixture(local_site, tmp_path: Path) -> None:
    base_url, _site = local_site
    output = tmp_path / "mirror"

    stats = crawl_site(
        CrawlOptions(
            start_url=base_url,
            root=output,
            max_pages=2,
            threads=2,
        )
    )

    assert stats.pages_seen == 2
    assert (output / "index.html").exists()
    assert (output / "about.html").exists()
    assert (output / "assets" / "site.css").exists()
    assert (output / "assets" / "extra.css").exists()
    assert (output / "assets" / "app.js").exists()
    assert (output / "img" / "logo.png").exists()
    assert (output / "img" / "bg.png").exists()
    assert (output / "img" / "from-js.png").exists()

    html = (output / "index.html").read_text(encoding="utf-8")
    assert 'href="assets/site.css"' in html
    assert 'href="https://example.invalid/keep"' in html
    assert 'src="img/logo.png"' in html

    css = (output / "assets" / "site.css").read_text(encoding="utf-8")
    assert '@import "extra.css";' in css
    assert "url('../img/bg.png')" in css

    js = (output / "assets" / "app.js").read_text(encoding="utf-8")
    assert "'../img/from-js.png'" in js
    assert "'/api/data'" in js


def test_crawl_site_uses_sitemap_seed(local_site, tmp_path: Path) -> None:
    base_url, _site = local_site
    output = tmp_path / "mirror"

    crawl_site(
        CrawlOptions(
            start_url=base_url,
            root=output,
            max_pages=2,
            sitemap="auto",
        )
    )

    assert (output / "sitemap-only.html").exists()


def test_crawl_site_writes_zip_and_warc_outputs(local_site, tmp_path: Path) -> None:
    base_url, _site = local_site
    output = tmp_path / "mirror"
    zip_path = tmp_path / "mirror.zip"
    warc_path = tmp_path / "mirror.warc"

    crawl_site(
        CrawlOptions(
            start_url=base_url,
            root=output,
            max_pages=1,
            zip_output=zip_path,
            warc_output=warc_path,
        )
    )

    assert zip_path.exists()
    with ZipFile(zip_path) as archive:
        assert "index.html" in archive.namelist()

    warc_text = warc_path.read_text(encoding="utf-8")
    assert "WARC/1.1" in warc_text
    assert "WARC-Type: response" in warc_text


def test_update_mode_uses_cache_metadata(conditional_site, tmp_path: Path) -> None:
    base_url, _site = conditional_site
    output = tmp_path / "mirror"
    cache_file = tmp_path / "cache.json"

    first = crawl_site(
        CrawlOptions(
            start_url=base_url,
            root=output,
            max_pages=1,
            update=True,
            cache_file=cache_file,
        )
    )
    second = crawl_site(
        CrawlOptions(
            start_url=base_url,
            root=output,
            max_pages=1,
            update=True,
            cache_file=cache_file,
        )
    )

    assert first.pages_written == 1
    assert second.pages_cached == 1
    assert cache_file.exists()
