# 🌐 Website Downloader CLI

Website Downloader CLI is a **tiny, pure-Python** site-mirroring tool that lets you grab a complete, browsable offline copy of any publicly-reachable website:

* Recursively crawls every same-origin link (including “pretty” `/about/` URLs)
* Downloads **all** assets (images, CSS, JS, …)
* Rewrites internal links so pages open flawlessly from your local disk
* Streams files concurrently with automatic retry / back-off
* Generates a clean, flat directory tree (`example_com/index.html`, `example_com/about/index.html`, …)

> Perfect for web-archiving, pentesting labs, long flights, or just poking around a site without an internet connection.

---

## 🚀 Quick Start

```bash
# 1.  Grab the code
git clone https://github.com/PKHarsimran/website-downloader.git
cd website-downloader

# 2.  Install deps (only two runtime libs!)
pip install -r requirements.txt

# 3.  Mirror a site – no prompts needed
python website_downloader.py \
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

## 🤝 Contributing

Contributions are welcome! Please open an issue or submit a pull request for any improvements or bug fixes.

## 📜 License

This project is licensed under the MIT License.
