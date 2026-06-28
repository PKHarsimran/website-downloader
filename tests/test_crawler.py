from __future__ import annotations

from pathlib import Path

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
