# 🌐 Website Downloader CLI  

[![CI – Website Downloader](https://github.com/PKHarsimran/website-downloader/actions/workflows/python-app.yml/badge.svg)](https://github.com/PKHarsimran/website-downloader/actions/workflows/python-app.yml)
[![Lint & Style](https://github.com/PKHarsimran/website-downloader/actions/workflows/lint.yml/badge.svg)](https://github.com/PKHarsimran/website-downloader/actions/workflows/lint.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Code style: Black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

Website Downloader CLI is a lightweight, pure-Python site mirroring tool that creates a fully browsable offline copy of any publicly accessible website.

* Recursively crawls every same-origin link (including “pretty” `/about/` URLs)
* Downloads **all internal assets** (images, CSS, JS, fonts, etc.)
* Optional support for downloading **external CDN assets** for complete offline mirroring
* Rewrites links so pages load correctly from your local filesystem
* Streams files concurrently using worker threads for faster downloads
* Uses automatic retry and back-off for unstable connections
* Generates a clean, organized directory structure (`example_com/index.html`, `example_com/about/index.html`, …)
* Stores CDN resources under a structured path (`cdn/<domain>/...`)
* Safely handles extremely long filenames using hashing and graceful fallbacks
* Skips non-fetchable schemes (`mailto:`, `tel:`, `data:`, `javascript:`) to avoid crawler errors
> Perfect for web archiving, pentesting labs, long flights, or just poking around a site without an internet connection.


## ❤️ Support This Project

If you find this tool useful, consider supporting the project:

[Donate via
PayPal](https://www.paypal.com/donate/?business=MVEWG3QAX6UBC&no_recurring=1&item_name=Github+Project+-+Website+downloader&currency_code=CAD)

---

## 🚀 Quick Start

```bash
# 1. Grab the code
git clone https://github.com/PKHarsimran/website-downloader.git
cd website-downloader

# 2. Install dependencies (only two runtime libs!)
pip install -r requirements.txt

# 3. Mirror a site – no prompts needed
python website-downloader.py \
    --url https://harsim.ca \
    --destination harsim_ca_backup \
    --max-pages 100 \
    --threads 8
```

---

## 🛠️ Libraries Used

| Library | Purpose |
|----------|----------|
| **requests** + **urllib3.Retry** | HTTP client with automatic retry, backoff, and persistent session handling |
| **BeautifulSoup (bs4)** | Parses HTML and extracts `<a>`, `<img>`, `<script>`, and `<link>` elements |
| **argparse** | Provides structured CLI argument parsing and validation |
| **logging** | Dual console + file logging with crawl progress and summary metrics |
| **threading** & **queue** | Concurrent asset downloading via lightweight worker pool |
| **pathlib** & **os** | Cross-platform filesystem management and safe directory creation |
| **urllib.parse** | URL parsing, normalization, and safe internal link rewriting |
| **hashlib (sha256)** | Generates stable hashes for long filenames and query-string collisions |
| **posixpath** | Normalizes URL paths while preventing traversal |
| **time** | Measures crawl duration and per-page performance |
| **sys** | Handles CLI exit codes and stream output management |
| **re** | Normalizes path segments and collapses malformed multi-dot filenames |

## 🗂️ Project Structure

| Path | What it is | Key features |
|------|------------|--------------|
| `website_downloader.py` | **Single-entry CLI** that performs the entire crawl *and* link-rewriting pipeline. | • Persistent `requests.Session` with automatic retries<br>• Breadth-first crawl capped by `--max-pages` (default = 50)<br>• Thread-pool (configurable via `--threads`, default = 6) to fetch images/CSS/JS in parallel<br>• Robust link rewriting so every internal URL works offline (pretty-URL folders ➜ `index.html`, plain paths ➜ `.html`)<br>• Smart output folder naming (`example.com` → `example_com`)<br>• Colourised console + file logging with per-page latency and crawl summary |
| `requirements.txt` | Minimal dependency pin-list. Only **`requests`** and **`beautifulsoup4`** are third-party; everything else is Python ≥ 3.10 std-lib. |
| `web_scraper.log` | Auto-generated run log (rotates/overwrites on each invocation). Useful for troubleshooting or audit trails. |
| `README.md` | The document you’re reading – quick-start, flags, and architecture notes. |
| *(output folder)* | Created at runtime (`example_com/ …`) – mirrors the remote directory tree with `index.html` stubs and all static assets. |

> **Removed:** The old `check_download.py` verifier is no longer required because the new downloader performs integrity checks (missing files, broken internal links) during the crawl and reports any issues directly in the log summary.

## ✨ Recent Improvements

### ✅ Type Conversion Fix
Resolved a `TypeError` caused by `int(..., 10)` when non-string arguments were passed, improving input robustness and CLI reliability.

### ✅ Safer Path Handling
Added intelligent path shortening and hashing for long filenames to prevent  
`OSError: [Errno 36] File name too long` errors across different operating systems.

### ✅ Improved CLI Experience
Rebuilt argument parsing using `argparse` for cleaner syntax, better validation, and clearer error messages.

### ✅ Code Quality & Linting
Standardized formatting using **Black**, **isort**, and **Ruff**.  
The project now passes all CI formatting and lint checks.

### ✅ Logging & Stability
Improved structured logging, retry handling, and safe-write fallbacks to make crawls more resilient against network failures and filesystem issues.

### ✅ Skip Non-Fetchable Schemes
The crawler now safely skips `mailto:`, `tel:`, `javascript:`, `data:`, `geo:`, and `blob:` links instead of attempting to download them.  
This prevents `requests.exceptions.InvalidSchema` errors while preserving those links in saved HTML.

### ✅ Improved URL Resolution (CDN-Safe Handling)
Fixed incorrect URL normalization that previously caused malformed asset paths and 404 errors.

- URLs are resolved before sanitization  
- Protocol-relative URLs (`//cdn.domain.com/file.css`) are correctly converted to `https://`  
- Prevents broken paths like `https://example.com/npm/...`  
- Reduces asset download failures on modern CDN-heavy websites

### ✅ Optional CDN Asset Downloading
Added a new CLI option to download external static assets for complete offline site mirroring.
`--download-external-assets`

When enabled:

- External assets such as CDN **CSS, JS, fonts, and images** are downloaded
- Files are stored under:
`cdn/<domain>/<path>`

- HTML references are automatically rewritten to use local copies

This allows mirrored websites to function fully offline even when they rely on external CDNs.

### ✅ Enhanced Path Normalization
Improved filename normalization to reduce filesystem edge cases:

- Decodes URL-encoded segments (`%20` → space)
- Trims unnecessary whitespace
- Collapses accidental multi-dot filenames (`file....jpg` → `file.jpg`)
- Maintains traversal protection and hashing safeguards

------------------------------------------------------------------------


## 🤝 Contributing

Contributions are welcome! Please open an issue or submit a pull request for any improvements or bug fixes.

## 📜 License

This project is licensed under the MIT License.
