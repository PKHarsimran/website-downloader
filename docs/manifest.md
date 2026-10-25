# Crawl manifests

Use `--manifest FILE` to save a machine-readable report alongside a mirror:

```bash
website-downloader --url https://example.com --destination mirror --manifest mirror/crawl.json
```

The filename is used as supplied: relative paths are relative to the current working directory,
not automatically relative to `--destination`. Parent directories are created. Without this
flag no manifest is written. Choose a separate filename that is not also a downloaded resource,
cache file, or export archive. A report stored inside the mirror is included in `--zip-output`.

The report is written after the page and asset workers finish, using a temporary file in the
same directory and an atomic replacement. Graceful interruption after crawling starts also
writes a partial report before re-raising the interrupt. A hard process termination or power
loss cannot produce a fresh report. This feature does not save checkpoints or resume a crawl.

## Schema version 1

| Top-level field | Meaning |
| --- | --- |
| `schema_version` | Format version (`1`). |
| `generator` | Tool name and version. |
| `start_url`, `start_urls` | Canonical first seed and deduplicated explicit seeds, including URLs loaded from `--url-file`. Sitemap/cache discoveries appear in `pages`. |
| `started_at`, `finished_at` | UTC timestamps in ISO 8601 format. |
| `completed` | Whether the crawl loop finished normally, including configured limits. This does not imply that every resource succeeded or that the entire website was discovered. |
| `termination_reason` | `finished`, `max_pages`, `interrupted`, or `error`. |
| `stats` | Page/asset counts, cache hits, errors, and elapsed seconds. `pages_seen` counts attempted pages, and `assets_queued` counts scheduled downloads. Skipped resources are listed separately in the resource arrays. |
| `pages`, `assets` | Resource records, deduplicated within each kind and sorted by canonical URL. |
| `errors` | Flattened resource errors, with the resource `url` and `kind` (`page` or `asset`). |

Each resource has these fields:

| Field | Meaning |
| --- | --- |
| `url` | Canonical requested URL. |
| `local_path` | Actual saved file path relative to the mirror root, with `/` separators; `null` when no saved copy is recorded. |
| `status` | `downloaded`, `cached`, `failed`, or `skipped`. |
| `status_code` | Observed HTTP response code, including `304`, or `null` when unknown. A transport failure or locally reused asset has no fabricated HTTP 200. |
| `content_type` | Response MIME header when available; otherwise `null`. |
| `reason` | Machine-readable failure/skip reason, or `null`. |
| `errors` | Error objects containing `type` and `message`; empty for ordinary skips. |

Page records also contain `depth` (zero for explicit seeds), `title` (`null` if absent),
`headings` (ordered `{ "level": 1, "text": "Heading" }` objects for h1–h6), and `assets`
(sorted canonical URLs directly referenced by the page, including recognized external assets
that policy skips). Assets discovered inside downloaded CSS/JS appear in the global `assets`
array; the page list is not a transitive dependency graph.

Skip reasons include `excluded`, `robots_txt`, `max_depth`, `max_pages`,
`external_downloads_disabled`, `external_domain_not_allowed`, and `max_asset_bytes`.
Resources left pending by a graceful interruption or fatal crawl error use `interrupted`
or `error`. Failure reasons include `fetch_error`, `http_error` (rendered navigation),
and `processing_error`. A size-limited response can have HTTP 200 while its resource
status is `skipped`; it is not counted as a download error.

For example, a downloaded page record can look like this:

```json
{
  "url": "https://example.com/",
  "local_path": "index.html",
  "status": "downloaded",
  "status_code": 200,
  "content_type": "text/html; charset=utf-8",
  "reason": null,
  "errors": [],
  "depth": 0,
  "title": "Example",
  "headings": [{ "level": 1, "text": "Welcome" }],
  "assets": ["https://example.com/style.css"]
}
```

## Cache and rendering behavior

With `--update`, HTTP 304 pages and assets remain in the report as `cached`. Page titles and
headings are read from the saved HTML. Original direct asset references are retained in the
update cache on fresh downloads, even when `--manifest` was not requested. Older cache files
remain compatible, but a cached page's `assets` is `null` when those original references were
not recorded; the crawler does not guess URLs from rewritten local links. Fetch a fresh mirror
to populate that metadata. An empty list means the page had no recognized direct asset URLs.

Without `--update`, pre-existing assets are reused without a network request, so their
`status_code` is `null`. With `--render-js`, navigation response codes are reported and
HTTP error pages are recorded as failures rather than successful downloads.

The report covers URLs recognized by the crawler, not every possible browser request.
It does not inventory external navigation links, unsupported URL schemes, or arbitrary
runtime API calls. References filtered out before CSS/JS enqueueing are not inventoried.
A completed report therefore supports auditing but is not proof of full offline completeness.

Request cookies and headers are not exported. Resource URLs, page text, and exception messages
are included, so a report for a private site can still contain private information.
