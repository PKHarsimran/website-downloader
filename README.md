# Website Downloader CLI

[![CI - Website Downloader](https://github.com/PKHarsimran/website-downloader/actions/workflows/python-app.yml/badge.svg)](https://github.com/PKHarsimran/website-downloader/actions/workflows/python-app.yml)
[![Lint & Style](https://github.com/PKHarsimran/website-downloader/actions/workflows/lint.yml/badge.svg)](https://github.com/PKHarsimran/website-downloader/actions/workflows/lint.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)

Website Downloader CLI turns a public or authorized website into a browsable offline copy. It crawls pages, downloads assets, rewrites links, and saves everything into a local folder you can open, inspect, archive, or move into a migration workflow.

It is built for developers who want something more modern and hackable than `wget --mirror`, without jumping straight into a heavy crawler framework.

## Why Use It

| Need | What this tool gives you |
| --- | --- |
| Offline browsing | Saves HTML pages and local asset references that work from disk. |
| Migration prep | Captures the old site before a rebuild, redesign, or host move. |
| Static-site review | Lets you inspect pages, CSS, JS, images, fonts, and media locally. |
| Authenticated snapshots | Reuses cookies for portals, intranets, and staging sites you are allowed to access. |
| Modern asset handling | Understands `srcset`, `data-src`, `poster`, inline styles, CSS imports, meta images, and common JS asset strings. |
| Controlled CDN mirroring | Downloads only the external domains you allow into `cdn/<domain>/...`. |
| Ongoing archives | Uses `ETag` and `Last-Modified` metadata to skip unchanged resources with `--update`. |
| Portable exports | Can produce zip archives and WARC response archives for sharing or long-term storage. |

## Quick Start

```bash
git clone https://github.com/PKHarsimran/website-downloader.git
cd website-downloader

python -m venv .venv
.venv\Scripts\activate
pip install -e .

website-downloader --url https://example.com --destination example_backup --max-pages 100
```

The compatibility script still works too:

```bash
python website-downloader.py --url https://example.com --destination example_backup
```

On macOS or Linux, activate the virtual environment with:

```bash
source .venv/bin/activate
```

## Install Options

Start with the core install, then add extras only when you need them:

| Install | Use when you want |
| --- | --- |
| `pip install -e .` | Normal static-site crawling with `requests` and BeautifulSoup. |
| `pip install -e ".[dev]"` | Tests, formatting, linting, and local contributor work. |
| `pip install -e ".[render]"` | Playwright-powered JavaScript rendering with `--render-js` or `--headless`. |
| `pip install -e ".[ux]"` | Rich-powered terminal progress with `--progress`. |

## How It Works

```mermaid
flowchart TD
    A["Start with a URL and CLI options"] --> B["Create session with cookies, headers, retries"]
    B --> C{"Use sitemap?"}
    C -- "Yes" --> D["Load sitemap URLs into the page queue"]
    C -- "No" --> E["Queue the starting URL"]
    D --> F["Fetch next page"]
    E --> F
    F --> G{"Update cache says unchanged?"}
    G -- "Yes" --> H["Reuse saved local file"]
    G -- "No" --> I{"Render JavaScript?"}
    I -- "No" --> J["Download HTML with requests"]
    I -- "Yes" --> K["Render page with Playwright"]
    J --> L["Parse HTML with BeautifulSoup"]
    K --> L
    H --> L
    L --> M["Find page links and asset links"]
    M --> N{"Same-site page?"}
    N -- "Yes" --> O["Queue page for crawling"]
    N -- "No" --> P{"Asset allowed?"}
    P -- "Yes" --> Q["Download asset"]
    P -- "No" --> R["Keep original reference or skip"]
    O --> S["Rewrite links for offline browsing"]
    Q --> S
    R --> S
    S --> T["Save mirror folder"]
    T --> U{"Export requested?"}
    U -- "Zip/WARC" --> V["Write portable archive"]
    U -- "No" --> W["Open index.html locally"]
    V --> W
```

In plain English:

1. You give the CLI a starting URL and optional crawl settings.
2. It can seed pages from `sitemap.xml`, custom headers, cookies, and robots rules.
3. It downloads or optionally renders each page with Playwright.
4. It finds links, images, scripts, stylesheets, fonts, media, and metadata assets.
5. It follows same-site pages up to your `--max-pages` limit.
6. It saves assets locally and rewrites references so pages still work offline.
7. With `--update`, unchanged resources can be skipped using cache metadata.
8. With `--zip-output` or `--warc-output`, the result can also be exported as an archive.

## Common Commands

Mirror a small public site:

```bash
website-downloader ^
  --url https://example.com ^
  --destination example_backup ^
  --max-pages 50
```

Download selected CDN assets:

```bash
website-downloader ^
  --url https://example.com ^
  --destination example_backup ^
  --download-external-assets ^
  --external-domains cdn.example.com fonts.gstatic.com
```

Mirror an authorized site with cookies:

```bash
website-downloader ^
  --url https://intranet.example.com ^
  --destination intranet_backup ^
  --cookie-file example-cookie.txt
```

Cookie files use normal cookie header syntax:

```text
sessionid=abc123; csrftoken=xyz789
```

Send custom headers such as bearer tokens:

```bash
website-downloader ^
  --url https://docs.example.com ^
  --destination docs_backup ^
  --header "Authorization: Bearer <token>" ^
  --header "X-Environment: staging"
```

Use a sitemap as the crawl seed:

```bash
website-downloader ^
  --url https://example.com ^
  --destination example_backup ^
  --sitemap
```

Point at a custom sitemap URL or local sitemap file:

```bash
website-downloader --url https://example.com --sitemap https://example.com/sitemap.xml
```

Use safer crawl limits:

```bash
website-downloader ^
  --url https://example.com ^
  --max-pages 50 ^
  --threads 4 ^
  --delay 0.25 ^
  --respect-robots ^
  --max-asset-bytes 25000000 ^
  --user-agent "WebsiteDownloader/0.2"
```

Update an existing mirror without re-downloading unchanged resources:

```bash
website-downloader ^
  --url https://example.com ^
  --destination example_backup ^
  --update
```

Export a portable zip and WARC archive:

```bash
website-downloader ^
  --url https://example.com ^
  --destination example_backup ^
  --zip-output example_backup.zip ^
  --warc-output example_backup.warc
```

## JavaScript-Rendered Sites

Some modern sites do not expose their real links and assets until JavaScript runs. For those, install the optional Playwright extra:

```bash
pip install -e ".[render]"
playwright install chromium
website-downloader --url https://example.com --render-js --max-pages 20
```

`--headless` is also available as a friendly alias for `--render-js`.

`--render-js` and `--headless` are optional because Playwright is heavier than the default `requests` + BeautifulSoup path. Use them when a normal crawl only captures an empty app shell or misses important client-rendered links.

## Live Progress

Install the optional UX extra for a Rich-powered terminal dashboard:

```bash
pip install -e ".[ux]"
website-downloader --url https://example.com --progress
```

If `rich` is not installed, the crawler falls back to normal logging instead of failing.

## Feature Flags At A Glance

| Flag | What it does | Best for |
| --- | --- | --- |
| `--render-js` / `--headless` | Uses Playwright before parsing the page. | React, Vue, Angular, Next.js, and other client-rendered sites. |
| `--cookie-file` | Sends saved browser/session cookies. | Authorized portals, staging sites, docs behind login. |
| `--header` | Adds custom request headers. | Bearer tokens, staging headers, API gateway headers. |
| `--update` | Reuses cache metadata and skips unchanged resources when the server supports it. | Recurring mirrors and archives. |
| `--sitemap` | Seeds the crawl from `sitemap.xml` or a supplied sitemap. | Faster, more complete discovery. |
| `--progress` | Shows a Rich terminal progress dashboard when installed. | Long crawls where visibility matters. |
| `--zip-output` | Exports the mirror folder as a zip. | Sharing, attaching, or storing snapshots. |
| `--warc-output` | Writes a simple WARC response archive. | Archival workflows and future replay tooling. |

## What Gets Rewritten

| Source | Rewritten for offline use |
| --- | --- |
| Page links | `<a href>` for same-site pages |
| Images and media | `src`, `data-src`, `poster`, `srcset` |
| Stylesheets and icons | `<link href>` for fetchable resource types |
| Metadata images | `og:image`, `twitter:image` |
| Inline styles | `style="background: url(...)"` |
| CSS files | `url(...)` and `@import` |
| JavaScript files | Common static asset strings like `/img/logo.png` |
| External assets | Optional CDN copies under `cdn/<domain>/...` |

When external scripts or stylesheets are localized, the tool removes `integrity` and `crossorigin` where needed because those attributes often break offline copies.

## Output Example

```text
example_backup/
  index.html
  about.html
  assets/
    site.css
    app.js
  img/
    logo.png
    hero.webp
  fonts/
    inter.woff2
  cdn/
    cdn.example.com/
      library.js
```

Open `index.html` in your browser to browse the mirrored copy.

## Feature Snapshot

- Same-origin recursive crawling.
- Optional external asset downloading with domain allowlists.
- Cookie-based authenticated crawling.
- Custom request headers for bearer tokens and staging environments.
- Optional JavaScript rendering with Playwright.
- Sitemap-aware crawl seeding.
- Incremental update mode using `ETag` and `Last-Modified`.
- Optional Rich-powered progress dashboard.
- Zip and WARC output formats.
- Retry and backoff for unstable requests.
- Worker-thread asset downloads.
- Path sanitization for Windows/macOS/Linux.
- Query-string hashing to avoid filename collisions.
- Long-path fallback handling.
- Local pytest suite and CI checks.

## Local Development

Install the development extra:

```bash
pip install -e ".[dev]"
pytest
black . --check
isort . --check-only
ruff check .
```

### PyCharm

1. Open this repository folder in PyCharm.
2. Create or select a Python 3.10+ virtual environment.
3. In the PyCharm terminal, run `pip install -e ".[dev]"`.
4. Run the `tests` folder with PyCharm's pytest runner.
5. For manual CLI testing, create a Python run configuration for `website_downloader.cli` or run `python website-downloader.py --help`.

## Project Structure

| Path | Purpose |
| --- | --- |
| `website_downloader/cli.py` | Argument parsing, validation, logging, and CLI entry point. |
| `website_downloader/crawler.py` | Crawl coordination, asset queueing, workers, robots.txt support, and stats. |
| `website_downloader/http.py` | Requests sessions, HTML fetches, binary downloads, and downloaded CSS/JS post-processing. |
| `website_downloader/rewrite.py` | HTML, CSS, JavaScript, and `srcset` reference rewriting. |
| `website_downloader/paths.py` | Filesystem-safe page, asset, and CDN path mapping. |
| `website_downloader/render.py` | Optional Playwright page rendering. |
| `website_downloader/cache.py` | Update-mode metadata for `ETag` and `Last-Modified`. |
| `website_downloader/sitemap.py` | Sitemap and sitemap-index loading. |
| `website_downloader/progress.py` | Optional Rich progress dashboard. |
| `website_downloader/exports.py` | Zip and WARC export helpers. |
| `tests/` | Local pytest suite with a tiny fixture HTTP server. |

## Roadmap Ideas

These are natural next steps for making the project more useful to developers:

- `--manifest crawl.json` with pages, assets, status codes, titles, headings, and errors.
- Login-flow recording for complex SSO sites.
- Stronger WARC metadata and replay compatibility.
- Visual diff mode for migration and redesign checks.

## Responsible Use

Only mirror sites you own, have permission to archive, or are legally allowed to access. Authentication cookies can expose private content, so keep cookie files out of source control and avoid sharing generated mirrors that contain private data. Use `--respect-robots`, lower `--threads`, and `--delay` for polite crawling.

## Licensing And Ownership

This project is licensed under the MIT License. Others may use, copy, modify, and distribute the code if they keep the license notice. Your original code remains your copyrighted work, but the MIT license intentionally allows broad reuse.

If the project becomes a product, consider choosing a distinctive brand name and protecting that brand separately from the source code license.

## Support This Project

[Donate via PayPal](https://www.paypal.com/donate/?business=PJVPSXG6V4CUG&no_recurring=1&item_name=Thank+you+for+the+coffee+%3A%29&currency_code=CAD)

## Contributing

Contributions are welcome. Please open an issue or pull request for bug reports, feature ideas, or improvements.

## License

This project is licensed under the MIT License.
