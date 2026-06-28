# Website Downloader CLI

[![CI - Website Downloader](https://github.com/PKHarsimran/website-downloader/actions/workflows/python-app.yml/badge.svg)](https://github.com/PKHarsimran/website-downloader/actions/workflows/python-app.yml)
[![Lint & Style](https://github.com/PKHarsimran/website-downloader/actions/workflows/lint.yml/badge.svg)](https://github.com/PKHarsimran/website-downloader/actions/workflows/lint.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)

Website Downloader CLI mirrors public or authorized websites into browsable offline copies. It crawls same-origin pages, downloads static assets, and rewrites references so the result can be opened from disk.

## Features

- Recursively crawls same-origin HTML pages.
- Downloads images, CSS, JavaScript, fonts, media, manifests, and common web assets.
- Rewrites links in `href`, `src`, `data-src`, `poster`, `srcset`, inline styles, CSS `url(...)`, CSS `@import`, and obvious static asset strings in JavaScript.
- Optionally downloads whitelisted external CDN/static assets into `cdn/<domain>/...`.
- Supports authenticated crawling with `--cookie` and `--cookie-file`.
- Skips non-fetchable schemes such as `mailto:`, `tel:`, `javascript:`, `data:`, `blob:`, and `about:`.
- Uses retry/backoff, path sanitization, query-string hashing, and long-path fallbacks.
- Can optionally render JavaScript-heavy pages with Playwright via `--render-js`.

## Quick Start

```bash
git clone https://github.com/PKHarsimran/website-downloader.git
cd website-downloader

python -m venv .venv
.venv\Scripts\activate
pip install -e .

website-downloader --url https://example.com --destination example_backup --max-pages 100
```

The old script entry point still works:

```bash
python website-downloader.py --url https://example.com --destination example_backup
```

## Authenticated Crawling

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

## JavaScript-Rendered Sites

For sites that need browser rendering before their links/assets exist in the DOM, install the optional render extra:

```bash
pip install -e ".[render]"
playwright install chromium
website-downloader --url https://example.com --render-js --max-pages 20
```

`--render-js` is intentionally optional because Playwright is heavier than the normal `requests` + BeautifulSoup workflow.

## Safer Crawling Options

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

Use `--download-external-assets` to mirror third-party static assets. Prefer `--external-domains` when you know exactly which CDN hosts you want:

```bash
website-downloader ^
  --url https://example.com ^
  --download-external-assets ^
  --external-domains cdn.example.com fonts.gstatic.com
```

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
| `tests/` | Local pytest suite with a tiny fixture HTTP server. |

## Responsible Use

Only mirror sites you own, have permission to archive, or are legally allowed to access. Authentication cookies can expose private content, so keep cookie files out of source control and avoid sharing generated mirrors that contain private data. Use `--respect-robots`, lower `--threads`, and `--delay` for polite crawling.

## Licensing And Ownership

This project is licensed under the MIT License. That means others may use, copy, modify, and distribute the code if they keep the license notice. Your original code remains your copyrighted work, but the MIT license intentionally allows broad reuse.

If the project becomes a product, consider choosing a distinctive brand name and protecting that brand separately from the source code license.

## Support This Project

[Donate via PayPal](https://www.paypal.com/donate/?business=PJVPSXG6V4CUG&no_recurring=1&item_name=Thank+you+for+the+coffee+%3A%29&currency_code=CAD)

## Contributing

Contributions are welcome. Please open an issue or pull request for bug reports, feature ideas, or improvements.

## License

This project is licensed under the MIT License.
