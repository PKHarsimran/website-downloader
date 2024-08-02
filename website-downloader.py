import os
import requests
import wget
import logging
import subprocess
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, unquote

# Configure logging
logging.basicConfig(filename='web_scraper.log', level=logging.DEBUG, 
                    format='%(asctime)s - %(levelname)s - %(message)s')

def save_file(url, destination):
    """
    Downloads a file from a URL to a specified destination.

    Args:
        url (str): URL of the file to download.
        destination (str): Path to save the downloaded file.
    """
    try:
        logging.info(f"Downloading {url} to {destination}")
        wget.download(url, destination)
        logging.info(f"Successfully downloaded {url}")
    except Exception as e:
        logging.error(f"Error downloading {url}: {e}")

def create_directory(path):
    """
    Creates a directory if it doesn't exist.

    Args:
        path (str): Path of the directory to create.
    """
    if not os.path.exists(path):
        os.makedirs(path)
        logging.info(f"Created directory {path}")

def download_page(url, destination):
    """
    Downloads an HTML page and saves it to the specified destination.

    Args:
        url (str): URL of the page to download.
        destination (str): Path to save the downloaded page.

    Returns:
        soup (BeautifulSoup): Parsed HTML content of the page.
        page_path (str): Path where the page was saved.
    """
    try:
        response = requests.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        # Save the HTML page with appropriate naming
        parsed_url = urlparse(url)
        page_path = os.path.join(destination, parsed_url.netloc + parsed_url.path)
        if not page_path.endswith('.html'):
            if page_path.endswith('/'):
                page_path += 'index.html'
            else:
                page_path += '.html'
        
        page_path = unquote(page_path)
        page_dir = os.path.dirname(page_path)
        create_directory(page_dir)

        with open(page_path, 'w', encoding='utf-8') as file:
            file.write(soup.prettify())
        logging.info(f"Saved page {url} to {page_path}")

        return soup, page_path

    except requests.exceptions.RequestException as e:
        logging.error(f"HTTP error for {url}: {e}")
    except Exception as e:
        logging.error(f"Error processing {url}: {e}")

def save_resource(url, destination):
    """
    Save a resource file to the specified destination.

    Args:
        url (str): URL of the resource.
        destination (str): Path to save the resource.
    """
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        content_type = response.headers.get('content-type')
        if 'text/html' in content_type:
            save_file(url, destination)
        else:
            with open(destination, 'wb') as file:
                for chunk in response.iter_content(chunk_size=8192):
                    file.write(chunk)
        logging.info(f"Successfully saved resource {url}")
    except Exception as e:
        logging.error(f"Error saving resource {url}: {e}")

def download_resources(soup, base_url, destination):
    """
    Downloads all resources (images, CSS, JS, and linked pages) from a parsed HTML page.

    Args:
        soup (BeautifulSoup): Parsed HTML content of the page.
        base_url (str): Base URL of the page.
        destination (str): Path to save the resources.

    Returns:
        list: A list of sub-pages (internal links) found in the resources.
    """
    resources = set()
    for tag in soup.find_all(['img', 'link', 'script', 'a']):
        src = tag.get('src') or tag.get('href')
        if src:
            resource_url = urljoin(base_url, src)
            resource_path = os.path.join(destination, os.path.basename(urlparse(resource_url).path))
            resources.add((resource_url, resource_path))

    for resource_url, resource_path in resources:
        if not os.path.exists(resource_path):
            save_resource(resource_url, resource_path)
    
    return [url for url, _ in resources if url.endswith('.html') or url.endswith('/')]

def download_website(url, destination):
    """
    Recursively downloads the main page and all linked internal pages and resources.

    Args:
        url (str): URL of the main page to start the download.
        destination (str): Path to save the downloaded website.
    """
    to_download = [(url, destination)]
    downloaded = set()

    while to_download:
        current_url, current_dest = to_download.pop()
        if current_url in downloaded:
            continue
        downloaded.add(current_url)

        soup, page_path = download_page(current_url, current_dest)
        if soup and page_path:
            sub_pages = download_resources(soup, current_url, os.path.dirname(page_path))
            for sub_page in sub_pages:
                if sub_page not in downloaded:
                    to_download.append((sub_page, current_dest))

def get_default_folder_name(url):
    """
    Generates a default folder name based on the URL.

    Args:
        url (str): URL of the website.

    Returns:
        str: Default folder name.
    """
    parsed_url = urlparse(url)
    folder_name = parsed_url.netloc.replace('.', '_')
    return folder_name

if __name__ == "__main__":
    website_url = input("Enter the URL of the website to download: ").strip()
    download_destination = input("Enter the destination folder to save the website (leave empty to use default): ").strip()

    # Ensure the URL has a scheme
    if not website_url.startswith(('http://', 'https://')):
        website_url = 'http://' + website_url

    # Set a default destination folder if none is provided
    if not download_destination:
        download_destination = get_default_folder_name(website_url)

    logging.info(f"Starting download for {website_url} into {download_destination}")

    try:
        download_website(website_url, download_destination)
        print("\nWebsite downloaded successfully.")
        logging.info("Website downloaded successfully.")
    except Exception as e:
        print(f"\nAn error occurred: {e}")
        logging.error(f"An error occurred: {e}")

    # Ask user if they want to check the downloaded website
    check_download = input("Would you like to check if the website is correctly downloaded? (yes/no): ").strip().lower()
    if check_download == 'yes':
        subprocess.run(['python', 'check_download.py', '--url', website_url, '--dir', download_destination])

        # Read missing files and attempt to download them again
        if os.path.exists('missing_files.txt'):
            with open('missing_files.txt', 'r') as file:
                missing_files = [line.strip() for line in file.readlines()]
            
            if missing_files:
                print("\nAttempting to re-download missing files...")
                logging.info("Attempting to re-download missing files...")
                for resource_url in missing_files:
                    resource_path = os.path.join(download_destination, os.path.basename(urlparse(resource_url).path))
                    save_file(resource_url, resource_path)

                # Re-check the downloaded website
                subprocess.run(['python', 'check_download.py', '--url', website_url, '--dir', download_destination])
