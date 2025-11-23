# 🌐 Website Downloader CLI  
[![CI – Website Downloader](https://github.com/PKHarsimran/website-downloader/actions/workflows/python-app.yml/badge.svg)](https://github.com/PKHarsimran/website-downloader/actions/workflows/python-app.yml)
[![Lint & Style](https://github.com/PKHarsimran/website-downloader/actions/workflows/lint.yml/badge.svg)](https://github.com/PKHarsimran/website-downloader/actions/workflows/lint.yml)
[![Automatic Dependency Submission](https://github.com/PKHarsimran/website-downloader/actions/workflows/dependency-graph/auto-submission/badge.svg)](https://github.com/PKHarsimran/website-downloader/actions/workflows/dependency-graph/auto-submission)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Code style: Black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

Website Downloader CLI is a **tiny, pure-Python** site-mirroring tool that lets you grab a complete, browsable offline copy of any publicly reachable website:

* Recursively crawls every same-origin link (including “pretty” `/about/` URLs)
* Downloads **all** assets (images, CSS, JS, …)
* Rewrites internal links so pages open flawlessly from your local disk
* Streams files concurrently with automatic retry / back-off
* Generates a clean, flat directory tree (`example_com/index.html`, `example_com/about/index.html`, …)
* Handles extremely long filenames safely via hashing and graceful fallbacks

> Perfect for web archiving, pentesting labs, long flights, or just poking around a site without an internet connection.

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

| Library | Emoji | Purpose in this project |
|---------|-------|-------------------------|
| **requests** + **urllib3.Retry** | 🌐 | High-level HTTP client with automatic retry / back-off for flaky hosts |
| **BeautifulSoup (bs4)** | 🍜 | Parses downloaded HTML and extracts every `<a>`, `<img>`, `<script>`, and `<link>` |
| **argparse** | 🛠️ | Powers the modern CLI (`--url`, `--destination`, `--max-pages`, `--threads`, …) |
| **logging** | 📝 | Dual console / file logging with colour + crawl-time stats |
| **threading** & **queue** | ⚙️ | Lightweight thread-pool that streams images/CSS/JS concurrently |
| **pathlib** & **os** | 📂 | Cross-platform file-system helpers (`Path` magic, directory creation, etc.) |
| **time** | ⏱️ | Measures per-page latency and total crawl duration |
| **urllib.parse** | 🔗 | Safely joins / analyses URLs and rewrites them to local relative paths |
| **sys** | 🖥️ | Directs log output to `stdout` and handles graceful interrupts (`Ctrl-C`) |
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

✅ Type Conversion Fix
Fixed a TypeError caused by int(..., 10) when non-string arguments were passed.

✅ Safer Path Handling
Added intelligent path shortening and hashing for long filenames to prevent
OSError: [Errno 36] File name too long errors.

✅ Improved CLI Experience
Rebuilt argument parsing with argparse for cleaner syntax and validation.

✅ Code Quality & Linting
Applied Black + Flake8 formatting; the project now passes all CI lint checks.

✅ Logging & Stability
Improved error handling, logging, and fallback mechanisms for failed writes.

✅ Skip Non-Fetchable Schemes  
The crawler now safely skips `mailto:`, `tel:`, `javascript:`, and `data:` links instead of trying to download them.  
This prevents `requests.exceptions.InvalidSchema: No connection adapters were found` errors and keeps those links intact in saved HTML.


## 🤝 Contributing

Contributions are welcome! Please open an issue or submit a pull request for any improvements or bug fixes.

## 📜 License

This project is licensed under the MIT License.

## Donation
https://www.paypal.com/donate/?business=MVEWG3QAX6UBC&no_recurring=1&item_name=Github+Project+-+Website+downloader&currency_code=CAD
