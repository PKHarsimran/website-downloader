# 🌐 Website Downloader

Website Downloader is a powerful Python script designed to download entire websites along with all their assets. This tool allows you to create a local copy of a website, including HTML pages, images, CSS, JavaScript files, and other resources. It ensures that all internal links and resources are downloaded for offline viewing. Perfect for web archiving and offline browsing!

## ✨ Features

- 🔄 Recursively download entire websites.
- 💾 Save HTML pages with all linked assets.
- 🔗 Convert links for offline viewing.
- 🖼️ Handle various file types including images, CSS, and JavaScript.
- 📜 Log download progress and errors.
- ✔️ Verify the completeness of the downloaded website and re-download missing files if necessary.

## 📥 Installation

1. Clone the repository:
   
   ```bash
   git clone https://github.com/PKHarsimran/website-downloader.git
   cd website-downloader
   ```
2. Install the required dependencies:
   
   ```bash
    pip install -r requirements.txt
   ```

## 🚀 Usage

1. Run the website downloader script:
   ```bash
   python website-downloader.py
   ```
2. Follow the prompts to enter the URL of the website to download and the destination folder.
3. After the download is complete, you will be prompted to check if the website is correctly downloaded.
## 🛠️ Libraries Used

- **requests**: 🌐 A simple and elegant HTTP library for Python. Used to send HTTP requests to download HTML pages and resources.
- **wget**: 📥 A utility for non-interactive download of files from the web. Used to download resources such as images, CSS, and JavaScript files.
- **BeautifulSoup**: 🍜 A library for parsing HTML and XML documents. Used to extract links to resources from HTML pages.
- **logging**: 📝 A standard Python library for generating log messages. Used to log download progress, performance metrics, and errors.
- **subprocess**: ⚙️ A standard Python library to spawn new processes, connect to their input/output/error pipes, and obtain their return codes. Used to run the verification script.
- **argparse**: 🛠️ A standard Python library for parsing command-line arguments. Used in the verification script to handle input parameters.
- **time**, **threading**, **queue**, **re**: Standard Python libraries used for measuring download times, concurrent resource checks, deduplication, and log parsing.

## 🗂️ Project Structure

- `website-downloader.py`: The main script for downloading the website and its resources.
  - **New Features:**
    - Uses a persistent session with retry logic for improved performance and error recovery.
    - Implements enhanced logging (to both file and console) with detailed timing and status information.
    - Dynamically generates default folder names based on the website's domain.
    - Estimates download time per page and overall crawl time.
    - Automatically calls the verification script after downloading.
- `check_download.py`: The verification script for checking the completeness of the downloaded website.
  - **New Features:**
    - Auto-detects the download folder by parsing the log file.
    - Provides detailed statistics including the total number of HTML files processed, unique resources found, and missing resource percentages.
- `requirements.txt`: A file listing the required dependencies.

---

# CLI Refactor: Moving from input() to argparse

## 🎯 Objective

This update improves the usability and automation of the project by replacing all `input()` prompts with `argparse` command-line flags.

## 🛠️ Changes Made

- Replaced interactive `input()` calls with standard argparse flags:
  - `--url` for the target website URL
  - `--destination` for the download folder path
  - `--max-pages` to set the maximum number of pages to crawl (default = 50)
- Added `--help` documentation to guide users
- Enforced required arguments for critical parameters like `--url`
- Introduced cleaner defaults where applicable (e.g., default output directory)

## ✅ Benefits

- 🧩 **Scriptable**: Can now run the script in automated pipelines or cron jobs
- 💻 **User-Friendly**: Users can discover options via `--help`
- 🔁 **Repeatable**: Enables consistent execution with no manual input required
- 📦 **Future-Proof**: Prepares the project for more advanced CLI features (like subcommands)

## 🗓️ Next Steps (Planned)

- Merge both `website-downloader.py` and `check_download.py` into a unified CLI using subcommands (`download`, `verify`)
- Add flags for retries, request timeout, include-external-resources, and more
- Package the project with a `console_scripts` entry point for system-wide usage

---

## 🤝 Contributing

Contributions are welcome! Please open an issue or submit a pull request for any improvements or bug fixes.

## 📜 License

This project is licensed under the MIT License.
