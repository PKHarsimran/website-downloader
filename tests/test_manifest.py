from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from website_downloader.cli import main
from website_downloader.crawler import CrawlOptions, CrawlStats, crawl_site
from website_downloader.http import FetchFailure, create_session, fetch_binary, fetch_html
from website_downloader.manifest import CrawlManifest
from website_downloader.render import PlaywrightRenderer, RenderedPage


def read_report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_cli_manifest_covers_parallel_pages_shared_and_nested_assets(local_site, tmp_path):
    base, site = local_site
    (site / "index.html").write_text(
        "<title>Café &amp; Docs</title><h1>Hello <em>world</em></h1><h3>Details</h3>"
        '<a href="/about.html">About</a><img src="/img/logo.png">'
        '<link rel="stylesheet" href="/assets/site.css">',
        encoding="utf-8",
    )
    (site / "about.html").write_text('<img src="/img/logo.png">', encoding="utf-8")
    output = tmp_path / "mirror"
    report = output / "crawl.json"
    assert (
        main(
            [
                "--url",
                base,
                "--destination",
                str(output),
                "--manifest",
                str(report),
                "--page-threads",
                "4",
                "--threads",
                "4",
                "--max-pages",
                "2",
                "--header",
                "X-Secret: do-not-export",
                "--cookie",
                "session=private",
            ]
        )
        == 0
    )
    data = read_report(report)
    assert data["schema_version"] == 1
    assert data["completed"] is True
    assert data["start_urls"] == [base]
    assert data["errors"] == []
    page = next(row for row in data["pages"] if row["url"] == base)
    assert page["title"] == "Café & Docs"
    assert page["headings"] == [
        {"level": 1, "text": "Hello world"},
        {"level": 3, "text": "Details"},
    ]
    assert page["local_path"] == "index.html"
    assert page["status_code"] == 200
    assert page["assets"] == [base + "assets/site.css", base + "img/logo.png"]
    assert len({row["url"] for row in data["assets"]}) == len(data["assets"])
    assert base + "img/bg.png" in {row["url"] for row in data["assets"]}
    assert all(row["status"] == "downloaded" for row in data["pages"] + data["assets"])
    assert all((output / row["local_path"]).is_file() for row in data["pages"] + data["assets"])
    assert "do-not-export" not in report.read_text(encoding="utf-8")
    assert "private" not in report.read_text(encoding="utf-8")


def test_manifest_preserves_page_and_asset_http_failures(local_site, tmp_path):
    base, site = local_site
    (site / "index.html").write_text(
        '<img src="/missing.png"><a href="/missing.html">Missing</a>', encoding="utf-8"
    )
    report = tmp_path / "report.json"
    stats = crawl_site(CrawlOptions(start_url=base, root=tmp_path / "out", manifest=report))
    data = read_report(report)
    failed = [row for row in data["pages"] + data["assets"] if row["status"] == "failed"]
    assert len(failed) == stats.errors == len(data["errors"]) == 2
    assert all(row["status_code"] == 404 and row["local_path"] is None for row in failed)
    assert all(row["errors"][0]["type"] == "HTTPError" for row in failed)


def test_manifest_distinguishes_network_failure_from_http_status(monkeypatch, tmp_path):
    def timeout(*args, **kwargs):
        raise requests.Timeout("request timed out")

    monkeypatch.setattr(requests.Session, "get", timeout)
    report = tmp_path / "report.json"
    crawl_site(
        CrawlOptions(start_url="https://example.com/", root=tmp_path / "out", manifest=report)
    )
    row = read_report(report)["pages"][0]
    assert row["status"] == "failed"
    assert row["status_code"] is None
    assert row["errors"] == [{"type": "Timeout", "message": "request timed out"}]


@pytest.mark.parametrize("page_threads", [1, 4])
def test_manifest_records_page_limit_and_exclusions(blacklist_site, tmp_path, page_threads):
    base, _ = blacklist_site
    report = tmp_path / "report.json"
    crawl_site(
        CrawlOptions(
            start_url=base,
            root=tmp_path / "out",
            manifest=report,
            max_pages=1,
            page_threads=page_threads,
            exclude_patterns=["*/forum/*"],
        )
    )
    data = read_report(report)
    reasons = {row["url"]: row["reason"] for row in data["pages"]}
    assert data["termination_reason"] == "max_pages"
    assert reasons[base + "about.html"] == "max_pages"
    assert reasons[base + "forum/thread1.html"] == "excluded"
    assert data["errors"] == []


def test_manifest_records_depth_and_robots_skips(depth_chain_site, tmp_path):
    base, site = depth_chain_site
    report = tmp_path / "report.json"
    crawl_site(CrawlOptions(start_url=base, root=tmp_path / "out", manifest=report, max_depth=0))
    assert (
        next(row for row in read_report(report)["pages"] if row["url"].endswith("level1.html"))[
            "reason"
        ]
        == "max_depth"
    )
    (site / "robots.txt").write_text("User-agent: *\nDisallow: /level1.html\n", encoding="utf-8")
    crawl_site(
        CrawlOptions(start_url=base, root=tmp_path / "out", manifest=report, respect_robots=True)
    )
    assert (
        next(row for row in read_report(report)["pages"] if row["url"].endswith("level1.html"))[
            "reason"
        ]
        == "robots_txt"
    )


@pytest.mark.parametrize(
    "allow_external,reason",
    [
        (False, "external_downloads_disabled"),
        (True, "external_domain_not_allowed"),
    ],
)
def test_manifest_records_external_and_size_skips(local_site, tmp_path, allow_external, reason):
    base, site = local_site
    (site / "index.html").write_text(
        '<img src="https://example.invalid/image.png"><img src="/img/logo.png">', encoding="utf-8"
    )
    report = tmp_path / "report.json"
    stats = crawl_site(
        CrawlOptions(
            start_url=base,
            root=tmp_path / "out",
            manifest=report,
            max_asset_bytes=1,
            download_external_assets=allow_external,
            external_domains={"allowed.invalid"},
        )
    )
    data = read_report(report)
    reasons = {row["url"]: row["reason"] for row in data["assets"]}
    assert reasons["https://example.invalid/image.png"] == reason
    assert reasons[base + "img/logo.png"] == "max_asset_bytes"
    assert stats.errors == 0
    assert data["errors"] == []


def test_stream_size_limit_is_a_skip_and_removes_partial_file(monkeypatch, tmp_path):
    response = requests.Response()
    response.status_code = 200
    response._content = b"123456"
    response._content_consumed = True
    session = create_session()
    monkeypatch.setattr(session, "get", lambda *args, **kwargs: response)
    path = tmp_path / "asset.bin"
    result = fetch_binary(
        "https://example.com/asset.bin",
        path,
        session=session,
        root_netloc="example.com",
        site_root=tmp_path,
        max_asset_bytes=2,
    )
    assert isinstance(result, FetchFailure) and result.skipped
    assert result.status_code == 200
    assert not path.exists()


def test_update_manifest_includes_304_pages_assets_and_original_references(
    conditional_site, tmp_path
):
    base, _ = conditional_site
    report = tmp_path / "report.json"
    options = CrawlOptions(start_url=base, root=tmp_path / "out", update=True, manifest=report)
    crawl_site(options)
    crawl_site(options)
    data = read_report(report)
    assert len(data["pages"]) == 2
    assert len(data["assets"]) == 1
    assert all(
        row["status"] == "cached" and row["status_code"] == 304
        for row in data["pages"] + data["assets"]
    )
    assert next(row for row in data["pages"] if row["url"] == base)["assets"] == [
        base + "style.css"
    ]
    cache_path = options.root / ".website-downloader-cache.json"
    cache = read_report(cache_path)
    for entry in cache["entries"].values():
        entry.pop("assets", None)
    cache_path.write_text(json.dumps(cache), encoding="utf-8")
    crawl_site(options)
    assert all(row["assets"] is None for row in read_report(report)["pages"])


def test_existing_asset_has_unknown_status_and_no_default_manifest(attachment_site, tmp_path):
    base, _, _ = attachment_site
    output = tmp_path / "out"
    crawl_site(CrawlOptions(start_url=base, root=output))
    assert not list(output.rglob("*.json"))
    report = tmp_path / "report.json"
    crawl_site(CrawlOptions(start_url=base, root=output, manifest=report))
    row = read_report(report)["assets"][0]
    assert row["status"] == "cached"
    assert row["status_code"] is None


def test_rendering_uses_actual_navigation_status(monkeypatch):
    page = SimpleNamespace(
        goto=lambda *args, **kwargs: SimpleNamespace(
            status=404, headers={"content-type": "text/html"}
        ),
        content=lambda: "<title>Missing</title>",
        close=lambda: None,
    )
    renderer = PlaywrightRenderer(start_url="https://example.com/")
    renderer._context = SimpleNamespace(new_page=lambda: page)
    rendered = renderer.fetch("https://example.com/missing")
    assert rendered.status_code == 404
    result = fetch_html("https://example.com/missing", session=create_session(), renderer=renderer)
    assert isinstance(result, FetchFailure) and result.status_code == 404
    monkeypatch.setattr(
        renderer, "fetch", lambda url: RenderedPage("<title>Created</title>", 201, {})
    )
    assert (
        fetch_html("https://example.com/", session=create_session(), renderer=renderer).status_code
        == 201
    )


def test_interrupted_crawl_writes_partial_report(monkeypatch, tmp_path):
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("website_downloader.crawler.fetch_html", interrupt)
    report = tmp_path / "report.json"
    with pytest.raises(KeyboardInterrupt):
        crawl_site(
            CrawlOptions(start_url="https://example.com/", root=tmp_path / "out", manifest=report)
        )
    data = read_report(report)
    assert data["completed"] is False
    assert data["termination_reason"] == "interrupted"
    assert data["pages"][0]["status"] == "skipped"


def test_atomic_write_keeps_previous_report_on_failure(monkeypatch, tmp_path):
    path = tmp_path / "report.json"
    path.write_text('{"previous": true}', encoding="utf-8")
    manifest = CrawlManifest(path, tmp_path, ["https://example.com/"])

    def fail_replace(*args):
        raise OSError("cannot replace")

    monkeypatch.setattr("website_downloader.manifest.os.replace", fail_replace)
    with pytest.raises(OSError, match="cannot replace"):
        manifest.write(CrawlStats(0, 0, 0), completed=True, termination_reason="finished")
    assert read_report(path) == {"previous": True}
    assert not list(tmp_path.glob("*.tmp"))


def test_manifest_uses_actual_page_fallback_path_and_is_in_zip(local_site, monkeypatch, tmp_path):
    from zipfile import ZipFile

    base, _ = local_site
    output = tmp_path / "out"
    report = output / "crawl.json"
    archive = tmp_path / "mirror.zip"

    def fallback(path, text, encoding):
        actual = path.with_name("fallback.html")
        actual.write_text(text, encoding=encoding)
        return actual

    monkeypatch.setattr("website_downloader.crawler.safe_write_text", fallback)
    crawl_site(
        CrawlOptions(start_url=base, root=output, manifest=report, zip_output=archive, max_pages=1)
    )
    assert (
        next(row for row in read_report(report)["pages"] if row["url"] == base)["local_path"]
        == "fallback.html"
    )
    with ZipFile(archive) as file:
        assert "crawl.json" in file.namelist()
        assert json.loads(file.read("crawl.json"))["schema_version"] == 1


def test_update_manifest_keeps_skipped_external_references(conditional_site, tmp_path):
    base, site = conditional_site
    (site / "index.html").write_text(
        '<img src="https://example.invalid/image.png">', encoding="utf-8"
    )
    report = tmp_path / "report.json"
    options = CrawlOptions(start_url=base, root=tmp_path / "out", update=True, manifest=report)
    crawl_site(options)
    crawl_site(options)
    data = read_report(report)
    assert data["pages"][0]["assets"] == ["https://example.invalid/image.png"]
    assert data["assets"][0]["reason"] == "external_downloads_disabled"


def test_unexpected_asset_exception_does_not_lose_the_worker(local_site, monkeypatch, tmp_path):
    from website_downloader.http import fetch_binary as real_fetch_binary

    base, _ = local_site
    report = tmp_path / "report.json"

    def fetch(url, *args, **kwargs):
        if url.endswith("site.css"):
            raise RuntimeError("unexpected processing error")
        return real_fetch_binary(url, *args, **kwargs)

    monkeypatch.setattr("website_downloader.crawler.fetch_binary", fetch)
    stats = crawl_site(
        CrawlOptions(start_url=base, root=tmp_path / "out", manifest=report, threads=1)
    )
    rows = read_report(report)["assets"]
    failed = next(row for row in rows if row["url"].endswith("site.css"))
    assert failed["status"] == "failed"
    assert failed["reason"] == "processing_error"
    assert any(row["status"] == "downloaded" for row in rows)
    assert stats.errors == 1
