import os
from bs4 import beautifulsoup4
from urllib.parse import urljoin, urlparse
import argparse
import threading
import queue

def get_linked_resources(html_file, base_url):
    """
    Parses an HTML file to extract all linked resources (images, CSS, JS).

    Args:
        html_file (str): Path to the HTML file.
        base_url (str): Base URL of the HTML file.

    Returns:
        set: A set of resource URLs found in the HTML file.
    """
    try:
        with open(html_file, 'r', encoding='utf-8') as file:
            soup = BeautifulSoup(file, 'html.parser')

        resources = set()
        for tag in soup.find_all(['img', 'link', 'script']):
            src = tag.get('src') or tag.get('href')
            if src:
                resource_url = urljoin(base_url, src)
                resources.add(resource_url)

        return resources
    except Exception as e:
        print(f"Error parsing {html_file}: {e}")
        return set()

def check_resource(resource_url, base_dir, missing_files_queue):
    """
    Checks if a resource exists in the downloaded directory.

    Args:
        resource_url (str): URL of the resource.
        base_dir (str): Path to the downloaded website directory.
        missing_files_queue (queue.Queue): Queue to store missing files.
    """
    try:
        resource_path = os.path.join(base_dir, os.path.basename(urlparse(resource_url).path))
        if not os.path.exists(resource_path):
            missing_files_queue.put(resource_url)
    except Exception as e:
        print(f"Error checking resource {resource_url}: {e}")

def check_downloaded_resources(base_dir, base_url):
    """
    Checks if all resources linked in HTML files are present in the downloaded directory.

    Args:
        base_dir (str): Path to the downloaded website directory.
        base_url (str): Base URL of the website.

    Returns:
        list: A list of missing resource URLs.
    """
    # Collect all HTML files in the downloaded directory
    html_files = [os.path.join(dp, f) for dp, dn, filenames in os.walk(base_dir) for f in filenames if f.endswith('.html')]
    missing_files_queue = queue.Queue()
    threads = []

    for html_file in html_files:
        resources = get_linked_resources(html_file, base_url)
        for resource in resources:
            thread = threading.Thread(target=check_resource, args=(resource, base_dir, missing_files_queue))
            thread.start()
            threads.append(thread)

    # Wait for all threads to finish
    for thread in threads:
        thread.join()

    # Collect missing files from the queue
    missing_files = []
    while not missing_files_queue.empty():
        missing_files.append(missing_files_queue.get())

    return missing_files

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check if the website is correctly downloaded.")
    parser.add_argument('--url', required=True, help='The base URL of the website')
    parser.add_argument('--dir', required=True, help='The path to the downloaded website directory')
    args = parser.parse_args()

    base_url = args.url
    download_directory = args.dir

    # Check for missing files
    missing_files = check_downloaded_resources(download_directory, base_url)

    if missing_files:
        print("The following files are missing:")
        for file in missing_files:
            print(file)
    else:
        print("All files are correctly downloaded.")

    # Output missing files in a format that can be read by the main script
    with open('missing_files.txt', 'w') as file:
        for item in missing_files:
            file.write("%s\n" % item)
