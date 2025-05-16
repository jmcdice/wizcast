# gitcast_library/utils.py
import os
import re
import sys
import logging
from datetime import datetime, timedelta, date
import calendar
import requests
from urllib.parse import urljoin
from bs4 import BeautifulSoup

# --- Logging System ---
logger = logging.getLogger('wizcast')

def setup_logging(log_level=logging.INFO, log_format=None, log_file=None):
    """Setup application logging with syslog-style format."""
    if not log_format:
        log_format = '%(asctime)s %(name)s[%(process)d] %(levelname)s: %(message)s'
    
    # Configure the root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Clear existing handlers to avoid duplicates
    for handler in root_logger.handlers:
        root_logger.removeHandler(handler)
    
    # Always add console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(log_format))
    root_logger.addHandler(console_handler)
    
    # Add file handler if specified
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(logging.Formatter(log_format))
        root_logger.addHandler(file_handler)
    
    # Return the main application logger
    return logger

# --- Path and File Helpers ---
def sanitize_filename(name: str) -> str:
    name = str(name)
    name = re.sub(r'[^\w\s-]', '', name).strip()
    name = re.sub(r'[-\s]+', '-', name)
    return name

def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

def load_file_content(filepath: str) -> str | None:
    if not os.path.exists(filepath):
        logger.error(f"File not found - {filepath}")
        return None
    if os.path.getsize(filepath) == 0:
        logger.warning(f"File is empty - {filepath}")
        return ""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logger.error(f"Error reading file {filepath}: {e}")
        return None

# --- Date Helpers ---
def get_monday_of_week(input_date: date) -> date:
    return input_date - timedelta(days=input_date.weekday())

def get_file_modification_date(filepath: str) -> date | None:
    """Get the modification date of a file."""
    try:
        mtime = os.path.getmtime(filepath)
        return datetime.fromtimestamp(mtime).date()
    except (OSError, ValueError) as e:
        logger.warning(f"Could not get modification date for '{filepath}': {e}")
        return None
        
def parse_date_from_release_note_filename(filename: str, current_year: int, full_filepath: str = None) -> date | None:
    """Parse the date from a release note filename.
    
    Args:
        filename: The filename to parse
        current_year: The current year as a fallback
        full_filepath: If provided, used to check file metadata when year is missing from filename
        
    Returns:
        The Monday of the week for the parsed date, or None if parsing failed
    """
    match = re.search(r"week-of-([a-zA-Z]+)-(\d+)(?:st|nd|rd|th)?(?:-(\d{4}))?", filename, re.IGNORECASE)
    if not match:
        return None
        
    month_name_str, day_str, year_str = match.groups()
    day = int(day_str)
    
    # Get year: prioritize explicit year in filename, then file metadata, then current year
    year = None
    if year_str:  # Year is explicitly in the filename
        year = int(year_str)
        logger.debug(f"Using explicit year {year} from filename '{filename}'")
    elif full_filepath and os.path.exists(full_filepath):  # Check file metadata
        file_date = get_file_modification_date(full_filepath)
        if file_date:
            # Only use the file modification date's year if it's reasonable (not older than 2 years)
            if file_date.year >= current_year - 2:
                year = file_date.year
                logger.debug(f"Using year {year} from file metadata for '{filename}'")
            else:
                logger.warning(f"File '{filename}' modification date is too old ({file_date.year}), ignoring")
    
    # Fallback to current year if we couldn't determine year from filename or metadata
    if not year:
        year = current_year
        logger.debug(f"Falling back to current year {year} for '{filename}'")
    
    try:
        month_abbr = month_name_str[:3].capitalize()
        month_number = list(calendar.month_abbr).index(month_abbr)
    except ValueError:
        try:
            month_number = datetime.strptime(month_name_str, "%B").month
        except ValueError:
            logger.warning(f"Could not parse month '{month_name_str}' from RN filename '{filename}'")
            return None
    if month_number == 0: # Should not happen
        logger.warning(f"Month '{month_name_str}' resulted in 0 from RN filename '{filename}'")
        return None
    try:
        parsed_date_in_filename = date(year, month_number, day)
        return get_monday_of_week(parsed_date_in_filename)
    except ValueError:
        logger.warning(f"Invalid date (y:{year},m:{month_number},d:{day}) from RN filename '{filename}'")
        return None

def parse_blog_post_date_from_text(text_containing_date: str) -> date | None:
    if not text_containing_date:
        return None
    date_patterns = [
        r"([A-Za-z]+\s+\d{1,2},\s+\d{4})",  # Month DD, YYYY (e.g., May 14, 2025)
        r"(\d{1,2}\s+[A-Za-z]+\s+\d{4})",  # DD Month YYYY (e.g., 14 May 2025)
        r"([A-Za-z]+\s+\d{1,2}\s+\d{4})",  # Month DD YYYY (e.g., May 14 2025) - no comma
        r"(\d{4}-\d{2}-\d{2})",            # YYYY-MM-DD
        r"(\d{1,2}/\d{1,2}/\d{4})"          # MM/DD/YYYY or DD/MM/YYYY (parsing handles ambiguity later)
    ]
    extracted_date_str = None
    for pattern in date_patterns:
        match = re.search(pattern, text_containing_date)
        if match:
            extracted_date_str = match.group(1)
            break
    if not extracted_date_str:
        return None

    formats_to_try = [
        "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y",
        "%B %d %Y", "%b %d %Y", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"
    ]
    for fmt in formats_to_try:
        try:
            # Normalize spaces for formats like "Month DD YYYY"
            normalized_date_str = ' '.join(extracted_date_str.strip().replace(',', '').split())
            parsed_dt = datetime.strptime(normalized_date_str, fmt).date()
            return parsed_dt
        except ValueError:
            continue
    logger.warning(f"Could not parse extracted blog date string: '{extracted_date_str}' (from: '{text_containing_date[:100]}...')")
    return None

# --- Web Content Fetching ---
def fetch_url_content_text(url: str, timeout: int = 15) -> str | None:
    try:
        headers = {'User-Agent': 'GitCastBot/1.0 (LanguageModelGenerated)'}
        response = requests.get(url, timeout=timeout, headers=headers)
        response.raise_for_status()
        return response.text
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching URL {url}: {e}")
        return None

# --- Text Manipulation ---
def markdown_to_plain_text(markdown_text: str) -> str:
    if not markdown_text: return ""
    soup = BeautifulSoup(markdown_text, "html.parser")
    text = soup.get_text(separator="\n")
    text = re.sub(r"^\s*#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text); text = re.sub(r"__(.*?)__", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text); text = re.sub(r"_(.*?)_", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^\)]+\)", r"\1", text)
    text = re.sub(r"^\s*[\*\-\+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[\-\*\_]{3,}\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"`(.*?)`", r"\1", text)
    text = re.sub(r"\n\s*\n", "\n\n", text)
    return text.strip()