# Changelog

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

