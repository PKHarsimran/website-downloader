import os
import requests
import wget
import logging
import subprocess
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import urljoin, urlparse, unquote
import time

# =============================================================================
# Logging Configuration
# =============================================================================
# Configure logging to both file and console for real-time debugging and post-run analysis.
logging.basicConfig(filename='web_scraper.log', level=logging.DEBUG,
                    format='%(asctime)s - %(levelname)s - %(message)s')
console = logging.StreamHandler()
console.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
console.setFormatter(formatter)
logging.getLogger('').addHandler(console)

# =============================================================================
# Persistent Session with Retry Logic
# =============================================================================
# This session is reused for all HTTP requests. The retry logic helps to automatically
# recover from transient network errors.
session = requests.Session()
retries = Retry(total=3, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retries)
session.mount('http://', adapter)
session.mount('https://', adapter)

# =============================================================================
# Utility Functions
# =============================================================================
def save_file(url, destination):
    """
    Downloads a file from a URL to the specified destination using wget.

    Args:
        url (str): URL of the file to download.
        destination (str): Local path where the file will be saved.
    """
    try:
        logging.info(f"Downloading file: {url} to {destination}")
        wget.download(url, destination)
        logging.info(f"Successfully downloaded file: {url}")
    except Exception as e:
        logging.error(f"Error downloading file {url}: {e}")

def create_directory(path):
    """
    Creates a directory if it does not exist.

    Args:
        path (str): The directory path to create.
    """
    if not os.path.exists(path):
        os.makedirs(path)
        logging.info(f"Created directory: {path}")

# =============================================================================
# Core Download Functions
# =============================================================================
def download_page(url, destination):
    """
    Downloads an HTML page using a persistent session and saves it to the specified destination.

    Args:
        url (str): URL of the page to download.
        destination (str): Local directory where the page should be saved.

    Returns:
        tuple: (BeautifulSoup object, path to saved page) if successful, otherwise (None, None)
    """
    try:
        logging.debug(f"Starting download of page: {url}")
        start_time = time.time()
        response = session.get(url, timeout=10)
        response.raise_for_status()
        elapsed = time.time() - start_time
        logging.debug(f"Received response for {url} (Status: {response.status_code}) in {elapsed:.2f} seconds")

        soup = BeautifulSoup(response.text, 'html.parser')

        # Build file path based on the URL structure
        parsed_url = urlparse(url)
        page_path = os.path.join(destination, parsed_url.netloc + parsed_url.path)
        if not page_path.endswith('.html'):
            page_path = page_path.rstrip('/') + ('/index.html' if page_path.endswith('/') else '.html')

        page_path = unquote(page_path)
        page_dir = os.path.dirname(page_path)
        create_directory(page_dir)

        with open(page_path, 'w', encoding='utf-8') as file:
            file.write(soup.prettify())
        logging.info(f"Saved page: {url} to {page_path} (Elapsed time: {elapsed:.2f} seconds)")
        return soup, page_path

    except requests.exceptions.RequestException as e:
        logging.error(f"HTTP error while downloading page {url}: {e}")
        return None, None
    except Exception as e:
        logging.error(f"General error processing page {url}: {e}")
        return None, None

def save_resource(url, destination):
    """
    Downloads a resource (image, CSS, JS, etc.) using a persistent session with streaming,
    and saves it to the specified destination.

    Args:
        url (str): URL of the resource.
        destination (str): Local file path to save the resource.
    """
    try:
        logging.debug(f"Starting download of resource: {url}")
        start_time = time.time()
        response = session.get(url, stream=True, timeout=10)
        response.raise_for_status()
        elapsed = time.time() - start_time
        logging.debug(f"Fetched resource {url} in {elapsed:.2f} seconds (Status: {response.status_code})")

        content_type = response.headers.get('content-type', '')
        if 'text/html' in content_type:
            # Use wget to download if resource is HTML
            save_file(url, destination)
        else:
            with open(destination, 'wb') as file:
                total_bytes = 0
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        file.write(chunk)
                        total_bytes += len(chunk)
                logging.debug(f"Downloaded {total_bytes} bytes for resource: {url}")
        logging.info(f"Successfully saved resource: {url} (Elapsed time: {elapsed:.2f} seconds)")
    except Exception as e:
        logging.error(f"Error saving resource {url}: {e}")

def download_resources(soup, base_url, destination):
    """
    Downloads all resources (images, CSS, JS, and internal links) from a parsed HTML page.
    Only resources from the same domain as base_url are processed.

    Args:
        soup (BeautifulSoup): Parsed HTML content of the page.
        base_url (str): The URL of the page to determine the allowed domain.
        destination (str): Local directory to save the resources.

    Returns:
        list: List of sub-page URLs (internal links) extracted from the page.
    """
    resources = set()
    allowed_domain = urlparse(base_url).netloc
    logging.debug(f"Allowed domain for resources: {allowed_domain}")

    # Extract resource URLs from specified tags
    for tag in soup.find_all(['img', 'link', 'script', 'a']):
        src = tag.get('src') or tag.get('href')
        if src:
            resource_url = urljoin(base_url, src)
            resource_domain = urlparse(resource_url).netloc
            if resource_domain and resource_domain != allowed_domain:
                logging.debug(f"Skipping external resource: {resource_url}")
                continue
            logging.debug(f"Processing resource: {resource_url}")
            resource_path = os.path.join(destination, os.path.basename(urlparse(resource_url).path))
            resources.add((resource_url, resource_path))

    # Download each resource if it doesn't already exist
    for resource_url, resource_path in resources:
        if not os.path.exists(resource_path):
            save_resource(resource_url, resource_path)
        else:
            logging.debug(f"Resource already exists, skipping: {resource_url}")

    # Return only internal links that look like HTML pages or directories
    sub_pages = [url for url, _ in resources if url.endswith('.html') or url.endswith('/')]
    logging.debug(f"Found {len(sub_pages)} sub-pages in resources")
    return sub_pages

def download_website(url, destination, max_pages=50):
    """
    Recursively downloads the main page and all linked internal pages and resources.
    Stops when the number of downloaded pages reaches max_pages. Also estimates and logs
    the total and remaining download time.

    Args:
        url (str): Starting URL for the website crawl.
        destination (str): Local directory to save the website.
        max_pages (int): Maximum number of pages to download.
    """
    allowed_domain = urlparse(url).netloc
    logging.info(f"Allowed domain for website crawl: {allowed_domain}")
    to_download = [(url, destination)]
    downloaded = set()
    total_page_time = 0.0  # Total time taken for page downloads

    while to_download and len(downloaded) < max_pages:
        current_url, current_dest = to_download.pop()
        logging.debug(f"Processing URL: {current_url}")
        if urlparse(current_url).netloc and urlparse(current_url).netloc != allowed_domain:
            logging.debug(f"Skipping external URL: {current_url}")
            continue
        if current_url in downloaded:
            logging.debug(f"Already downloaded: {current_url}")
            continue

        start_page = time.time()
        downloaded.add(current_url)
        logging.info(f"Downloading page {len(downloaded)}/{max_pages}: {current_url}")
        soup, page_path = download_page(current_url, current_dest)
        elapsed_page = time.time() - start_page
        total_page_time += elapsed_page

        # Calculate average page download time and estimate total and remaining time
        pages_downloaded = len(downloaded)
        average_page_time = total_page_time / pages_downloaded
        estimated_total_time = average_page_time * max_pages
        remaining_time = estimated_total_time - total_page_time
        logging.info(f"Page {pages_downloaded} downloaded in {elapsed_page:.2f}s. "
                     f"Avg. page time: {average_page_time:.2f}s. "
                     f"Estimated total time: {estimated_total_time:.2f}s. "
                     f"Estimated remaining time: {remaining_time:.2f}s.")

        if soup and page_path:
            sub_pages = download_resources(soup, current_url, os.path.dirname(page_path))
            logging.debug(f"Sub-pages found: {sub_pages}")
            for sub_page in sub_pages:
                if sub_page not in downloaded:
                    to_download.append((sub_page, current_dest))

    if downloaded:
        logging.info(f"Crawling finished. Total pages downloaded: {len(downloaded)}. "
                     f"Total page download time: {total_page_time:.2f}s. "
                     f"Average page time: {total_page_time / len(downloaded):.2f}s.")
    else:
        logging.warning("No pages were downloaded.")

def get_default_folder_name(url):
    """
    Generates a default folder name based on the URL's domain.

    Args:
        url (str): The URL to base the folder name on.

    Returns:
        str: A sanitized folder name derived from the domain.
    """
    parsed_url = urlparse(url)
    folder_name = parsed_url.netloc.replace('.', '_')
    return folder_name

# =============================================================================
# Main Execution
# =============================================================================
if __name__ == "__main__":
    # Retrieve environment variables (if set)
    url_to_download = os.getenv('URL_TO_DOWNLOAD')
    download_destination = os.getenv('DOWNLOAD_DESTINATION')

    # If not provided via environment, prompt the user for input
    if not url_to_download:
        url_to_download = input("Enter the URL of the website to download: ").strip()
    if not download_destination:
        download_destination = input("Enter the destination folder to save the website (leave empty to use default): ").strip()

    # Use a default destination based on the domain if no folder is provided
    if not download_destination:
        download_destination = get_default_folder_name(url_to_download)

    logging.info(f"Starting download for {url_to_download} into {download_destination}")

    try:
        download_website(url_to_download, download_destination)
        print("\nWebsite downloaded successfully.")
        logging.info("Website downloaded successfully.")
    except Exception as e:
        print(f"\nAn error occurred: {e}")
        logging.error(f"An error occurred: {e}")

    # Call the check script and pass the log file for additional checks.
    check_download = input("Would you like to check if the website is correctly downloaded? (yes/no): ").strip().lower()
    if check_download == 'yes':
        # Pass the log file ('web_scraper.log') along with URL and directory.
        subprocess.run(['python', 'check_download.py', '--url', url_to_download,
                        '--dir', download_destination, '--log', 'web_scraper.log'])
