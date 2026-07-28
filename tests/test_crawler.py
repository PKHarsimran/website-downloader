from __future__ import annotations

import os
import shutil
import time
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


def test_crawl_site_parallel_pages_mirrors_local_fixture(local_site, tmp_path: Path) -> None:
    base_url, _site = local_site
    output = tmp_path / "mirror"

    stats = crawl_site(
        CrawlOptions(
            start_url=base_url,
            root=output,
            max_pages=2,
            threads=2,
            page_threads=4,
        )
    )

    assert stats.pages_seen == 2
    assert stats.pages_written == 2
    assert (output / "index.html").exists()
    assert (output / "about.html").exists()
    assert (output / "assets" / "site.css").exists()
    assert (output / "img" / "logo.png").exists()

    html = (output / "index.html").read_text(encoding="utf-8")
    assert 'href="assets/site.css"' in html


def test_crawl_downloads_extensionless_attachment_image(attachment_site, tmp_path: Path) -> None:
    base_url, _site, png_bytes = attachment_site
    output = tmp_path / "mirror"

    crawl_site(CrawlOptions(start_url=base_url, root=output, max_pages=1))

    saved = output / "attachments" / "228" / "content"
    assert saved.exists(), "extensionless attachment image should be downloaded"
    assert saved.read_bytes() == png_bytes, "image bytes must be saved unchanged"

    # The <img> must point at the local file, and it must NOT be turned into a
    # crawled .html page.
    html = (output / "index.html").read_text(encoding="utf-8")
    assert "attachments/228/content" in html
    assert not (output / "attachments" / "228" / "content.html").exists()


def test_crawl_site_skips_blacklisted_pages(blacklist_site, tmp_path: Path) -> None:
    base_url, _site = blacklist_site
    output = tmp_path / "mirror"

    stats = crawl_site(
        CrawlOptions(
            start_url=base_url,
            root=output,
            max_pages=10,
            exclude_patterns=["*/forum/*"],
        )
    )

    assert stats.pages_seen == 2
    assert (output / "index.html").exists()
    assert (output / "about.html").exists()
    assert not (output / "forum" / "thread1.html").exists()

    html = (output / "index.html").read_text(encoding="utf-8")
    # The excluded page was never downloaded, so the saved link must point at
    # the live URL instead of a local path that doesn't exist.
    assert f'href="{base_url}forum/thread1.html"' in html


def test_crawl_site_respects_max_depth(depth_chain_site, tmp_path: Path) -> None:
    base_url, _site = depth_chain_site
    output = tmp_path / "mirror"

    stats = crawl_site(
        CrawlOptions(
            start_url=base_url,
            root=output,
            max_pages=10,
            max_depth=1,
        )
    )

    assert stats.pages_seen == 2
    assert (output / "index.html").exists()
    assert (output / "level1.html").exists()
    assert not (output / "level2.html").exists()
    assert not (output / "level3.html").exists()


def test_update_reseed_preserves_recorded_depth(depth_chain_site, tmp_path: Path) -> None:
    base_url, site = depth_chain_site
    output = tmp_path / "mirror"
    cache_file = tmp_path / "cache.json"

    def options() -> CrawlOptions:
        return CrawlOptions(
            start_url=base_url,
            root=output,
            max_pages=10,
            max_depth=1,
            update=True,
            cache_file=cache_file,
        )

    crawl_site(options())
    assert (output / "level1.html").exists()
    assert not (output / "level2.html").exists()

    # Force a real refetch of level1 (not a 304) on the next run, so its
    # links are rediscovered and the reseed-depth path is actually exercised.
    level1_path = site / "level1.html"
    future = time.time() + 3600
    level1_path.write_text(level1_path.read_text(encoding="utf-8"), encoding="utf-8")
    os.utime(level1_path, (future, future))

    # On a second --update run, level1 is reseeded from the cache. If its
    # recorded depth (1) were not preserved, it would reseed at depth 0 and
    # incorrectly rediscover level2 within --max-depth 1.
    crawl_site(options())
    assert not (output / "level2.html").exists()


def test_crawl_site_max_depth_zero_only_fetches_seeds(depth_chain_site, tmp_path: Path) -> None:
    base_url, _site = depth_chain_site
    output = tmp_path / "mirror"

    stats = crawl_site(
        CrawlOptions(
            start_url=base_url,
            root=output,
            max_pages=10,
            max_depth=0,
        )
    )

    assert stats.pages_seen == 1
    assert (output / "index.html").exists()
    assert not (output / "level1.html").exists()


def test_crawl_site_unlimited_depth_follows_full_chain(depth_chain_site, tmp_path: Path) -> None:
    base_url, _site = depth_chain_site
    output = tmp_path / "mirror"

    stats = crawl_site(
        CrawlOptions(
            start_url=base_url,
            root=output,
            max_pages=10,
        )
    )

    assert stats.pages_seen == 4
    assert (output / "level3.html").exists()


def test_crawl_site_extra_start_urls_seed_disconnected_sections(
    multi_seed_site, tmp_path: Path
) -> None:
    base_url, _site = multi_seed_site
    output = tmp_path / "mirror"

    stats = crawl_site(
        CrawlOptions(
            start_url=f"{base_url}docs/",
            extra_start_urls=[f"{base_url}blog/"],
            root=output,
            max_pages=10,
        )
    )

    assert stats.pages_seen == 2
    assert (output / "docs" / "index.html").exists()
    assert (output / "blog" / "index.html").exists()


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


def test_update_recrawl_revisits_pages_from_cache_not_rewritten_links(
    conditional_site, tmp_path: Path
) -> None:
    base_url, _site = conditional_site
    output = tmp_path / "mirror"
    cache_file = tmp_path / "cache.json"

    def options() -> CrawlOptions:
        return CrawlOptions(
            start_url=base_url,
            root=output,
            max_pages=5,
            update=True,
            cache_file=cache_file,
        )

    first = crawl_site(options())
    assert first.pages_written == 2

    second = crawl_site(options())
    assert second.pages_cached == 2
    assert second.pages_written == 0
    assert second.assets_cached == 1


def test_update_refetches_when_local_mirror_deleted(conditional_site, tmp_path: Path) -> None:
    base_url, _site = conditional_site
    output = tmp_path / "mirror"
    cache_file = tmp_path / "cache.json"

    def options() -> CrawlOptions:
        return CrawlOptions(
            start_url=base_url,
            root=output,
            max_pages=5,
            update=True,
            cache_file=cache_file,
        )

    crawl_site(options())
    shutil.rmtree(output)

    second = crawl_site(options())
    assert second.pages_written == 2
    assert (output / "index.html").exists()
    assert (output / "style.css").read_bytes() != b""


def test_crawl_counts_fetch_failures_in_stats(local_site, tmp_path: Path) -> None:
    base_url, _site = local_site
    output = tmp_path / "mirror"

    stats = crawl_site(
        CrawlOptions(
            start_url=f"{base_url}missing-page.html",
            root=output,
            max_pages=1,
        )
    )

    assert stats.errors == 1
    assert stats.pages_written == 0


def test_recrawl_without_update_treats_existing_assets_as_cached(
    local_site, tmp_path: Path
) -> None:
    base_url, _site = local_site
    output = tmp_path / "mirror"

    def options() -> CrawlOptions:
        return CrawlOptions(start_url=base_url, root=output, max_pages=2, threads=2)

    crawl_site(options())
    second = crawl_site(options())

    assert second.assets_written == 0
    assert second.assets_cached > 0
