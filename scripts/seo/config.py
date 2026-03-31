"""
Shared configuration module for the highlevel.ai SEO automation suite.

All scripts import paths, URLs, property IDs, and helper functions from here.
"""

import os
import sys
import json
import shutil
import logging
import glob
from pathlib import Path
from datetime import datetime

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):
        pass  # Gracefully skip if python-dotenv not installed

# ──────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────

SCRIPTS_DIR = Path(__file__).resolve().parent                 # scripts/seo/
_candidate_root = SCRIPTS_DIR.parent.parent                   # Two levels up

# Detect if we're inside the site/ repo directly or the outer project root
if (_candidate_root / "site").is_dir():
    # Running from outer project: HighlevelAI/scripts/seo/ → site/ is a sibling
    PROJECT_ROOT = _candidate_root
    SITE_DIR = PROJECT_ROOT / "site"
else:
    # Running from inside repo (GitHub Actions): site/scripts/seo/ → repo root is site/
    SITE_DIR = _candidate_root
    PROJECT_ROOT = SITE_DIR
DATA_DIR = SCRIPTS_DIR / "data"
TEMPLATES_DIR = SCRIPTS_DIR / "templates"
BACKUPS_DIR = SCRIPTS_DIR / "backups"
LOGS_DIR = SCRIPTS_DIR / "logs"
REPORTS_DIR = SCRIPTS_DIR / "reports"

# Existing analytics scripts (always in scripts/ relative to site root)
EXISTING_SCRIPTS_DIR = SITE_DIR / "scripts"
CREDENTIALS_DIR = EXISTING_SCRIPTS_DIR / ".credentials"

# Also check if scripts are siblings (repo layout: scripts/ is at repo root)
if not EXISTING_SCRIPTS_DIR.exists() and SCRIPTS_DIR.parent.exists():
    EXISTING_SCRIPTS_DIR = SCRIPTS_DIR.parent  # scripts/ directory
    CREDENTIALS_DIR = EXISTING_SCRIPTS_DIR / ".credentials"

# ──────────────────────────────────────────────
# Load environment variables
# ──────────────────────────────────────────────

load_dotenv(PROJECT_ROOT / ".env", override=True)

# ──────────────────────────────────────────────
# Site configuration
# ──────────────────────────────────────────────

SITE_URL = "https://www.highlevel.ai"
SITE_DOMAIN = "www.highlevel.ai"
AFFILIATE_LINK = "https://www.gohighlevel.com/?fp_ref=ai"
AFFILIATE_PARAM = "fp_ref=ai"

# ──────────────────────────────────────────────
# Google API configuration
# ──────────────────────────────────────────────

GA4_PROPERTY_ID = os.getenv("GA4_PROPERTY_ID", "526181718")
GA4_SERVICE_ACCOUNT_FILE = os.getenv(
    "GA4_SERVICE_ACCOUNT_FILE",
    os.path.expanduser("~/Downloads/ai-projects-487616-878fcd9f633c.json")
)
GSC_SITE_URL = os.getenv("GSC_SITE_URL", "sc-domain:highlevel.ai")
GSC_CREDENTIALS_FILE = str(CREDENTIALS_DIR / "client_secret.json")
GSC_TOKEN_FILE = str(CREDENTIALS_DIR / "token.json")

# ──────────────────────────────────────────────
# LLM API keys (for visibility tracker)
# ──────────────────────────────────────────────

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "")

# ──────────────────────────────────────────────
# Video pipeline (Slack + Google Sheets)
# ──────────────────────────────────────────────

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID", "")
VIDEO_SHEET_ID = os.getenv("VIDEO_SHEET_ID", "")

# ──────────────────────────────────────────────
# YouTube API
# ──────────────────────────────────────────────

YOUTUBE_CLIENT_SECRET = str(CREDENTIALS_DIR / "youtube_client_secret.json")
YOUTUBE_TOKEN_FILE = str(CREDENTIALS_DIR / "youtube_token.json")

# ──────────────────────────────────────────────
# Thumbnail / Image generation
# ──────────────────────────────────────────────

IDEOGRAM_API_KEY = os.getenv("IDEOGRAM_API_KEY", "")

# ──────────────────────────────────────────────
# Author defaults (for schema generation)
# ──────────────────────────────────────────────

AUTHOR = {
    "@type": "Person",
    "name": "Josh Miller",
    "jobTitle": "Marketing Technology Consultant",
    "url": f"{SITE_URL}/about.html",
    "description": "GoHighLevel user since 2019, marketing automation expert"
}

PUBLISHER = {
    "@type": "Organization",
    "name": "highlevel.ai",
    "url": SITE_URL
}

OG_IMAGE = f"{SITE_URL}/og-image.png"

# ──────────────────────────────────────────────
# Page type classification
# ──────────────────────────────────────────────

PAGE_PRIORITY = {
    "index.html": 1.0,
    "pricing-explained.html": 0.9,
    "gohighlevel-reviews.html": 0.9,
}

# URL patterns for priority assignment
PRIORITY_PATTERNS = [
    ("highlevel-vs-", 0.8),
    ("-alternative.html", 0.8),
    ("highlevel-for-", 0.7),
    ("highlevel-plus-", 0.7),
    ("gohighlevel-", 0.7),
    ("-limitations.html", 0.7),
    ("-limits.html", 0.7),
    ("tools/", 0.6),
    ("blog/", 0.5),
    ("about.html", 0.3),
    ("contact.html", 0.3),
    ("editorial-policy.html", 0.3),
    ("privacy.html", 0.2),
    ("terms.html", 0.2),
]


def get_page_priority(filepath: str) -> float:
    """Get sitemap priority for a page based on its filename/path."""
    rel = os.path.relpath(filepath, SITE_DIR)
    if rel in PAGE_PRIORITY:
        return PAGE_PRIORITY[rel]
    for pattern, priority in PRIORITY_PATTERNS:
        if pattern in rel:
            return priority
    return 0.5  # default


def get_page_changefreq(filepath: str) -> str:
    """Get sitemap changefreq for a page."""
    rel = os.path.relpath(filepath, SITE_DIR)
    if "blog/" in rel:
        return "weekly"
    if rel in ("privacy.html", "terms.html", "editorial-policy.html"):
        return "yearly"
    return "monthly"


# ──────────────────────────────────────────────
# Helper functions
# ──────────────────────────────────────────────

def ensure_dirs():
    """Create all required directories if they don't exist."""
    for d in [DATA_DIR, TEMPLATES_DIR, BACKUPS_DIR, LOGS_DIR, REPORTS_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def get_all_html_files() -> list[Path]:
    """Return all HTML files in the site directory, sorted."""
    files = []
    for pattern in ["*.html", "blog/*.html", "tools/*.html"]:
        files.extend(SITE_DIR.glob(pattern))
    return sorted(set(files))


def get_relative_url(filepath: Path) -> str:
    """Convert a file path to a relative URL (e.g., /pricing-explained.html)."""
    rel = filepath.relative_to(SITE_DIR)
    url = "/" + str(rel)
    if url.endswith("/index.html"):
        url = url[:-10]  # /blog/index.html -> /blog/
    return url


def get_absolute_url(filepath: Path) -> str:
    """Convert a file path to an absolute URL."""
    return SITE_URL + get_relative_url(filepath)


def backup_file(filepath: Path, label: str = "") -> Path:
    """
    Copy a file to the backups directory with a timestamp.
    Returns the backup file path.
    """
    ensure_dirs()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = filepath.stem
    suffix = filepath.suffix
    tag = f"_{label}" if label else ""
    backup_name = f"{name}{tag}_{timestamp}{suffix}"
    backup_path = BACKUPS_DIR / backup_name
    shutil.copy2(filepath, backup_path)
    return backup_path


def setup_logging(script_name: str, level=logging.INFO) -> logging.Logger:
    """
    Configure logging with both console and file output.
    Returns a configured logger.
    """
    ensure_dirs()
    logger = logging.getLogger(script_name)
    logger.setLevel(level)

    # Avoid duplicate handlers
    if logger.handlers:
        return logger

    # Console handler
    console = logging.StreamHandler()
    console.setLevel(level)
    console_fmt = logging.Formatter("%(levelname)s: %(message)s")
    console.setFormatter(console_fmt)
    logger.addHandler(console)

    # File handler
    date_str = datetime.now().strftime("%Y-%m-%d")
    log_file = LOGS_DIR / f"{date_str}.log"
    file_handler = logging.FileHandler(log_file, mode="a")
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    file_handler.setFormatter(file_fmt)
    logger.addHandler(file_handler)

    return logger


def load_json(filepath: Path, default=None):
    """Load a JSON file, returning default if file doesn't exist."""
    if not filepath.exists():
        return default if default is not None else {}
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(filepath: Path, data, indent: int = 2):
    """Save data to a JSON file."""
    ensure_dirs()
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)


def save_report(filename: str, content: str) -> Path:
    """Save a markdown report to the reports directory. Returns file path."""
    ensure_dirs()
    filepath = REPORTS_DIR / filename
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return filepath


def today() -> str:
    """Return today's date as YYYY-MM-DD."""
    return datetime.now().strftime("%Y-%m-%d")


def now() -> str:
    """Return current datetime as a readable string."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def add_existing_scripts_to_path():
    """Add the existing site/scripts/ directory to sys.path for imports."""
    path = str(EXISTING_SCRIPTS_DIR)
    if path not in sys.path:
        sys.path.insert(0, path)


def add_root_scripts_to_path():
    """Add the root scripts/ directory to sys.path for importing llm_rank_tracker."""
    path = str(PROJECT_ROOT / "scripts")
    if path not in sys.path:
        sys.path.insert(0, path)
