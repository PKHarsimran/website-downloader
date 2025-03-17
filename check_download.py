import os
import re
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import argparse
import threading
import queue
import logging

# Configure logging
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s - %(levelname)s - %(message)s')


def file_exists_in_dir(filename, base_dir):
    """
    Recursively checks if a file with the given filename exists anywhere in the base_dir.

    Args:
        filename (str): The file name to search for.
        base_dir (str): The directory to search within.

    Returns:
        bool: True if the file exists, False otherwise.
    """
    for dp, dn, filenames in os.walk(base_dir):
        if filename in filenames:
            return True
    return False


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
        logging.debug(f"Extracted {len(resources)} resources from {html_file}")
        return resources
    except Exception as e:
        logging.error(f"Error parsing {html_file}: {e}")
        return set()


def check_resource(resource_url, base_dir, missing_files_queue):
    """
    Checks if a resource exists in the downloaded directory by searching recursively.

    Args:
        resource_url (str): URL of the resource.
        base_dir (str): Path to the downloaded website directory.
        missing_files_queue (queue.Queue): Queue to store missing files.
    """
    try:
        filename = os.path.basename(urlparse(resource_url).path)
        if not filename:
            logging.debug(f"No valid filename found for resource: {resource_url}")
            return
        # Search recursively in base_dir for the file
        if not file_exists_in_dir(filename, base_dir):
            logging.debug(f"Missing resource detected: {resource_url}")
            missing_files_queue.put(resource_url)
    except Exception as e:
        logging.error(f"Error checking resource {resource_url}: {e}")


def check_downloaded_resources(base_dir, base_url):
    """
    Checks if all resources linked in HTML files are present in the downloaded directory,
    and collects overall statistics.

    Args:
        base_dir (str): Path to the downloaded website directory.
        base_url (str): Base URL of the website.

    Returns:
        dict: A dictionary containing:
            - missing: list of missing resource URLs
            - total_html: total HTML files processed
            - total_resources: total unique resources extracted
            - missing_count: count of missing resources
    """
    # Collect all HTML files in the downloaded directory
    html_files = [os.path.join(dp, f) for dp, dn, filenames in os.walk(base_dir)
                  for f in filenames if f.endswith('.html')]
    total_html = len(html_files)
    logging.info(f"Found {total_html} HTML files to check.")

    # Deduplicate resource URLs from all HTML files
    all_resources = set()
    for html_file in html_files:
        resources = get_linked_resources(html_file, base_url)
        all_resources.update(resources)
    total_resources = len(all_resources)
    logging.info(f"Total unique resources extracted: {total_resources}")

    # Check each resource in parallel
    missing_files_queue = queue.Queue()
    threads = []
    for resource in all_resources:
        thread = threading.Thread(target=check_resource, args=(resource, base_dir, missing_files_queue))
        thread.start()
        threads.append(thread)

    for thread in threads:
        thread.join()

    # Collect missing files
    missing_files = []
    while not missing_files_queue.empty():
        missing_files.append(missing_files_queue.get())

    missing_count = len(missing_files)
    logging.info(f"Missing resources: {missing_count} out of {total_resources} "
                 f"({(missing_count/total_resources*100 if total_resources else 0):.2f}%).")
    return {
        'missing': missing_files,
        'total_html': total_html,
        'total_resources': total_resources,
        'missing_count': missing_count
    }


def detect_download_folder_from_log(log_filename="web_scraper.log"):
    """
    Attempts to detect the download folder from the given log file.
    It looks for a line matching the pattern:
        "Starting download for <url> into <folder>"

    Args:
        log_filename (str): The name of the log file to parse.

    Returns:
        str or None: The detected folder path if found and exists; otherwise, None.
    """
    if not os.path.exists(log_filename):
        logging.debug(f"Log file {log_filename} not found.")
        return None

    folder = None
    pattern = re.compile(r"Starting download for .* into (.*)")
    try:
        with open(log_filename, 'r', encoding='utf-8') as log_file:
            for line in log_file:
                match = pattern.search(line)
                if match:
                    candidate = match.group(1).strip()
                    # Check if the candidate folder exists
                    if os.path.exists(candidate):
                        folder = candidate
                        logging.info(f"Detected download folder from log: {folder}")
                        break
                    else:
                        logging.debug(f"Candidate folder {candidate} does not exist.")
    except Exception as e:
        logging.error(f"Error reading log file {log_filename}: {e}")
    return folder


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check if the website is correctly downloaded and generate a report.")
    parser.add_argument('--url', help='The base URL of the website')
    parser.add_argument('--dir', help='The path to the downloaded website directory')
    args = parser.parse_args()

    # If base URL is not provided, prompt the user.
    base_url = args.url or input("Enter the base URL of the website: ").strip()

    # If download directory is not provided, try to detect it from the log file.
    download_directory = args.dir
    if not download_directory:
        download_directory = detect_download_folder_from_log()
        if not download_directory:
            download_directory = input("Download folder not detected from log. Please enter the folder path: ").strip()

    logging.info(f"Checking downloaded resources in {download_directory} using base URL {base_url}")
    stats = check_downloaded_resources(download_directory, base_url)
    missing_files = stats.get('missing', [])

    # Print summary statistics
    print("\nDownload Check Summary:")
    print(f"HTML files processed: {stats.get('total_html')}")
    print(f"Unique resources found: {stats.get('total_resources')}")
    print(f"Missing resources: {stats.get('missing_count')} "
          f"({(stats.get('missing_count')/stats.get('total_resources')*100 if stats.get('total_resources') else 0):.2f}%)\n")

    if missing_files:
        logging.info("The following files are missing:")
        for file in missing_files:
            logging.info(file)
        print("The following files are missing:")
        for file in missing_files:
            print(file)
    else:
        logging.info("All files are correctly downloaded.")
        print("All files are correctly downloaded.")

    # Save missing files and stats to text files for use by the main script
    try:
        with open('missing_files.txt', 'w', encoding='utf-8') as file:
            for item in missing_files:
                file.write("%s\n" % item)
        logging.info("Missing files have been saved to missing_files.txt")
    except Exception as e:
        logging.error(f"Error writing missing files to disk: {e}")

    # Save summary stats to a report file
    try:
        with open('download_report.txt', 'w', encoding='utf-8') as report:
            report.write("Download Check Summary:\n")
            report.write(f"Base URL: {base_url}\n")
            report.write(f"Download Directory: {download_directory}\n")
            report.write(f"HTML files processed: {stats.get('total_html')}\n")
            report.write(f"Unique resources found: {stats.get('total_resources')}\n")
            report.write(f"Missing resources: {stats.get('missing_count')} "
                         f"({(stats.get('missing_count')/stats.get('total_resources')*100 if stats.get('total_resources') else 0):.2f}%)\n\n")
            if missing_files:
                report.write("Missing Files:\n")
                for item in missing_files:
                    report.write(f"{item}\n")
            else:
                report.write("All files are correctly downloaded.\n")
        logging.info("Download report has been saved to download_report.txt")
    except Exception as e:
        logging.error(f"Error writing download report to disk: {e}")
