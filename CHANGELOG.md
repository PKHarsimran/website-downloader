# Changelog

## v2.6.0 - 2026-07-06

### Added

- `--page-threads` fetches HTML pages concurrently (default 1 keeps the previous polite sequential behavior). Roughly 3-4x faster on multi-page sites with 4 workers. `--render-js` always uses a single page worker because Playwright's sync API is single-threaded.

### Fixed

- `--update` re-crawls no longer discover links from saved pages (whose links are already rewritten to local paths, producing bogus 404 URLs); known pages and assets are re-seeded from the update cache instead.
- A `304 Not Modified` response with a missing local copy now triggers a clean refetch instead of writing a zero-byte asset or skipping the page.
- Assets already present on disk are reported as cached instead of counted as errors when re-running into the same folder.
- CSS `@import url(...)` rules are mapped once; previously the rewritten local path was re-mapped and a garbage download URL was enqueued.
- The Rich progress dashboard's cached counter now tracks cached pages and assets correctly.

### Changed

- New optional `.[fast]` extra installs lxml for faster HTML parsing; it is auto-detected and used when available.
- Pages are parsed from raw bytes so BeautifulSoup honors `<meta charset>` declarations and BOMs when servers omit a charset header.
- `CrawlStats` gained an `errors` counter, and the end-of-crawl summary now reports pages, assets, cache hits, and errors.
- A page that fails mid-processing is now logged and counted as an error instead of aborting the whole crawl.
- Removed redundant URL re-parsing in the link discovery hot path and no longer pre-creates directories for queued assets (avoids empty folders for failed downloads).

## v2.5.0 - 2026-06-28

This release turns Website Downloader CLI into a more complete modern-site mirroring tool while keeping the default install lightweight and developer-friendly.

### Added

- Installable package layout with the `website-downloader` console command.
- Backward-compatible `website-downloader.py` wrapper.
- Optional Playwright rendering with `--render-js` and the `--headless` alias.
- Repeatable custom request headers with `--header "Name: Value"`.
- Incremental update mode with `--update`, using `ETag` and `Last-Modified` cache metadata.
- Sitemap-aware crawl seeding with `--sitemap`.
- Optional Rich terminal progress dashboard with `--progress` and the `.[ux]` extra.
- Portable archive export with `--zip-output`.
- Lightweight WARC response export with `--warc-output`.
- Local pytest coverage for URL normalization, path mapping, rewriting, CLI validation, sitemap crawling, update cache behavior, zip export, and WARC export.

### Changed

- Reworked the project into focused modules for CLI, crawling, HTTP, path mapping, rewriting, rendering, cache, sitemap, progress, and exports.
- Updated CI to run local tests and linting instead of relying on a live-site smoke test.
- Refreshed the README with clearer setup instructions, feature tables, workflow diagrams, and examples.
- Removed the unused `wget` dependency.

### Notes

- WARC export currently writes simple WARC 1.1 response records from saved resources. Future releases can improve replay compatibility and metadata depth.
- JavaScript rendering remains optional because Playwright is heavier than the default `requests` and BeautifulSoup path.

