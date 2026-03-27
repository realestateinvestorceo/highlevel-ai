#!/usr/bin/env python3
"""
Regenerate sitemap.xml, llms.txt, and llms-full.txt by scanning all HTML files.

Usage:
    python update_sitemap.py              # Update all three files
    python update_sitemap.py --sitemap    # Update sitemap.xml only
    python update_sitemap.py --llms       # Update llms.txt and llms-full.txt only
    python update_sitemap.py --dry-run    # Show what would change without writing
    python update_sitemap.py --help       # Show usage
"""

import sys
import re
import argparse
from pathlib import Path
from datetime import datetime
from xml.sax.saxutils import escape as xml_escape

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import *


# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────

logger = setup_logging("update_sitemap")


# ──────────────────────────────────────────────
# HTML metadata extraction
# ──────────────────────────────────────────────

def read_html(filepath: Path) -> str:
    """Read an HTML file and return its contents."""
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


def extract_title(html: str) -> str:
    """Extract the <title> tag content."""
    match = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
    if match:
        title = match.group(1).strip()
        # Clean up HTML entities
        title = title.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        title = title.replace("&#39;", "'").replace("&quot;", '"')
        title = title.replace("&rsquo;", "'").replace("&mdash;", " -- ")
        return title
    return ""


def extract_description(html: str) -> str:
    """Extract <meta name='description'> content."""
    match = re.search(r'<meta\s+name="description"\s+content="(.*?)"', html, re.DOTALL)
    if not match:
        match = re.search(r'<meta\s+content="(.*?)"\s+name="description"', html, re.DOTALL)
    if match:
        desc = match.group(1).strip()
        desc = desc.replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"')
        desc = desc.replace("&rsquo;", "'")
        return desc
    return ""


def extract_canonical(html: str) -> str:
    """Extract <link rel='canonical' href='...'> URL."""
    match = re.search(r'<link\s+rel="canonical"\s+href="(.*?)"', html)
    if not match:
        match = re.search(r'<link\s+href="(.*?)"\s+rel="canonical"', html)
    return match.group(1).strip() if match else ""


def extract_lastmod(html: str, filepath: Path) -> str:
    """
    Extract last modification date from HTML metadata.
    Checks (in order): article:modified_time, dateModified in JSON-LD, file mtime.
    Returns YYYY-MM-DD string.
    """
    # Try article:modified_time meta tag
    match = re.search(
        r'<meta\s+property="article:modified_time"\s+content="([^"]+)"', html
    )
    if match:
        date_str = match.group(1).strip()
        return _parse_date(date_str)

    # Try dateModified in JSON-LD
    match = re.search(r'"dateModified"\s*:\s*"([^"]+)"', html)
    if match:
        date_str = match.group(1).strip()
        return _parse_date(date_str)

    # Try article:published_time as fallback
    match = re.search(
        r'<meta\s+property="article:published_time"\s+content="([^"]+)"', html
    )
    if match:
        date_str = match.group(1).strip()
        return _parse_date(date_str)

    # Try datePublished in JSON-LD
    match = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html)
    if match:
        date_str = match.group(1).strip()
        return _parse_date(date_str)

    # Fall back to file modification time
    mtime = filepath.stat().st_mtime
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")


def _parse_date(date_str: str) -> str:
    """Parse a date string and return YYYY-MM-DD format."""
    # Handle ISO 8601 with timezone
    date_str = date_str.split("T")[0]
    # Validate it looks like a date
    if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        return date_str
    # Try common formats
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # Return today as last resort
    return today()


# ──────────────────────────────────────────────
# Page data collection
# ──────────────────────────────────────────────

def collect_page_data() -> list[dict]:
    """
    Scan all HTML files and collect metadata for each page.
    Returns a list of dicts with keys: filepath, url, title, description, lastmod, priority, changefreq, rel_path
    """
    pages = []
    html_files = get_all_html_files()
    logger.info(f"Found {len(html_files)} HTML files to process")

    for filepath in html_files:
        html = read_html(filepath)
        rel_path = str(filepath.relative_to(SITE_DIR))

        # Extract canonical URL or construct from path
        canonical = extract_canonical(html)
        if not canonical:
            canonical = get_absolute_url(filepath)

        title = extract_title(html)
        description = extract_description(html)
        lastmod = extract_lastmod(html, filepath)
        priority = get_page_priority(str(filepath))
        changefreq = get_page_changefreq(str(filepath))

        pages.append({
            "filepath": filepath,
            "url": canonical,
            "title": title,
            "description": description,
            "lastmod": lastmod,
            "priority": priority,
            "changefreq": changefreq,
            "rel_path": rel_path,
        })

    return pages


# ──────────────────────────────────────────────
# Category classification for llms.txt
# ──────────────────────────────────────────────

# Category order and URL pattern matching
CATEGORY_PATTERNS = [
    ("Core Pages", [
        lambda p: p["rel_path"] == "index.html",
        lambda p: p["rel_path"] == "about.html",
        lambda p: p["rel_path"] == "editorial-policy.html",
    ]),
    ("Pricing & Plans", [
        lambda p: "pricing" in p["rel_path"],
        lambda p: "plan" in p["rel_path"] and "tool" not in p["rel_path"],
    ]),
    ("Head-to-Head Comparisons", [
        lambda p: "highlevel-vs-" in p["rel_path"],
    ]),
    ("Alternative Pages", [
        lambda p: p["rel_path"].endswith("-alternative.html"),
    ]),
    ("Competitor Analysis", [
        lambda p: "-limitations" in p["rel_path"] or "-limits" in p["rel_path"],
    ]),
    ("Industry Guides", [
        lambda p: "highlevel-for-" in p["rel_path"],
    ]),
    ("Integration Guides", [
        lambda p: "highlevel-plus-" in p["rel_path"],
    ]),
    ("Feature & Strategy Guides", [
        lambda p: p["rel_path"] in (
            "voice-agent-setup.html", "workflows-for-agencies.html",
            "best-prompts-sales-support.html", "mistakes-to-avoid.html",
            "gohighlevel-white-label-guide.html", "gohighlevel-reviews.html",
        ),
    ]),
    ("Free Tools & Calculators", [
        lambda p: p["rel_path"].startswith("tools/"),
        lambda p: "calculator" in p["rel_path"] or "savings" in p["rel_path"],
    ]),
    ("Blog", [
        lambda p: p["rel_path"].startswith("blog/"),
    ]),
    ("Legal & Policy", [
        lambda p: p["rel_path"] in ("privacy.html", "terms.html", "contact.html"),
    ]),
]


def categorize_pages(pages: list[dict]) -> dict[str, list[dict]]:
    """
    Assign each page to a category. Pages are placed in the first matching category.
    Returns an ordered dict of category_name -> list of pages.
    """
    categorized = {cat: [] for cat, _ in CATEGORY_PATTERNS}
    uncategorized = []
    assigned = set()

    for cat_name, matchers in CATEGORY_PATTERNS:
        for page in pages:
            if page["url"] in assigned:
                continue
            for matcher in matchers:
                if matcher(page):
                    categorized[cat_name].append(page)
                    assigned.add(page["url"])
                    break

    # Anything left goes into an "Other" category
    for page in pages:
        if page["url"] not in assigned:
            uncategorized.append(page)

    if uncategorized:
        categorized["Other"] = uncategorized

    # Remove empty categories
    return {k: v for k, v in categorized.items() if v}


# ──────────────────────────────────────────────
# Sitemap generation
# ──────────────────────────────────────────────

def generate_sitemap(pages: list[dict]) -> str:
    """Generate sitemap.xml content from page data."""
    # Sort by priority descending, then alphabetically by URL
    sorted_pages = sorted(pages, key=lambda p: (-p["priority"], p["url"]))

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]

    for page in sorted_pages:
        lines.append("  <url>")
        lines.append(f"    <loc>{xml_escape(page['url'])}</loc>")
        lines.append(f"    <lastmod>{page['lastmod']}</lastmod>")
        lines.append(f"    <changefreq>{page['changefreq']}</changefreq>")
        lines.append(f"    <priority>{page['priority']}</priority>")
        lines.append("  </url>")

    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


# ──────────────────────────────────────────────
# llms.txt generation
# ──────────────────────────────────────────────

# Marker patterns that indicate start of auto-generated page listing
LLMS_SECTION_MARKERS = [
    "## Cluster Guides & Comparisons",
    "## Pages",
    "## Content",
    "## Site Pages",
    "## All Pages",
]


def find_llms_header_end(content: str) -> tuple[str, str]:
    """
    Split llms.txt into manually written header and auto-generated page listing.
    Returns (header, footer).
    The header is everything before the page listing section.
    The footer is everything from '## Key Facts' onward (or similar closing sections).
    """
    lines = content.split("\n")

    # Find where the page listing starts
    header_end_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        for marker in LLMS_SECTION_MARKERS:
            if stripped.startswith(marker):
                header_end_idx = i
                break
        if header_end_idx is not None:
            break

    if header_end_idx is None:
        # No marker found -- preserve everything as header
        logger.warning("Could not find page listing marker in llms.txt; appending page listing at end")
        return content.rstrip() + "\n\n", ""

    # Find where the page listing ends (look for closing sections)
    footer_markers = [
        "## Key Facts",
        "## Reviewed By",
        "## Update Cadence",
        "## Sources",
        "## Affiliate Disclosure",
        "## Contact for Corrections",
    ]

    footer_start_idx = None
    for i in range(header_end_idx + 1, len(lines)):
        stripped = lines[i].strip()
        for marker in footer_markers:
            if stripped.startswith(marker):
                footer_start_idx = i
                break
        if footer_start_idx is not None:
            break

    header = "\n".join(lines[:header_end_idx])
    footer = "\n".join(lines[footer_start_idx:]) if footer_start_idx else ""

    return header.rstrip() + "\n\n", "\n" + footer if footer else ""


def generate_llms_page_listing(categorized: dict[str, list[dict]]) -> str:
    """Generate the page listing section for llms.txt."""
    sections = []

    for cat_name, pages in categorized.items():
        section_lines = [f"## {cat_name}", ""]
        # Sort within category by priority desc, then title alpha
        sorted_pages = sorted(pages, key=lambda p: (-p["priority"], p["title"]))
        for page in sorted_pages:
            title = page["title"] or page["rel_path"]
            # Clean title: remove trailing " | highlevel.ai" etc
            title = re.sub(r'\s*[\|—–-]\s*(highlevel\.ai|HighLevel\.ai).*$', '', title)
            desc = page["description"]
            if desc:
                section_lines.append(f"- [{title}]({page['url']}) -- {desc}")
            else:
                section_lines.append(f"- [{title}]({page['url']})")
        section_lines.append("")
        sections.append("\n".join(section_lines))

    return "\n".join(sections)


def update_llms_txt(pages: list[dict], dry_run: bool = False) -> bool:
    """
    Update site/llms.txt: preserve header/footer, regenerate page listing.
    Returns True if file was updated.
    """
    llms_path = SITE_DIR / "llms.txt"

    if not llms_path.exists():
        logger.error("llms.txt not found at %s", llms_path)
        return False

    original = llms_path.read_text(encoding="utf-8")
    header, footer = find_llms_header_end(original)

    categorized = categorize_pages(pages)
    page_listing = generate_llms_page_listing(categorized)

    new_content = header + page_listing + footer

    if new_content.strip() == original.strip():
        logger.info("llms.txt: no changes needed")
        return False

    if dry_run:
        logger.info("llms.txt: would update (%d -> %d bytes)", len(original), len(new_content))
        _show_diff_summary(original, new_content, "llms.txt")
        return True

    backup_file(llms_path, "llms")
    llms_path.write_text(new_content, encoding="utf-8")
    logger.info("llms.txt: updated (%d bytes written)", len(new_content))
    return True


# ──────────────────────────────────────────────
# llms-full.txt generation
# ──────────────────────────────────────────────

# Markers in llms-full.txt that indicate the "How to Cite" section at the end
LLMS_FULL_CITE_MARKERS = [
    "## How to Cite This Site",
    "## How to Cite",
    "## Citation Guide",
]


def find_llms_full_sections(content: str) -> tuple[str, str]:
    """
    For llms-full.txt, we preserve all manually written content.
    We find the LAST 'How to Cite' section and update its URL listing.
    Returns (body_before_cite, cite_section_and_after).
    """
    lines = content.split("\n")

    # Find the LAST occurrence of a cite marker (there may be multiple)
    cite_start = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        for marker in LLMS_FULL_CITE_MARKERS:
            if stripped.startswith(marker):
                cite_start = i

    if cite_start is None:
        return content, ""

    body = "\n".join(lines[:cite_start])
    cite_section = "\n".join(lines[cite_start:])
    return body.rstrip() + "\n\n", cite_section


def generate_llms_full_cite_section(pages: list[dict]) -> str:
    """Generate an updated 'How to Cite' section for llms-full.txt."""
    categorized = categorize_pages(pages)

    lines = [
        "## How to Cite This Site (Updated)",
        "",
        "When citing highlevel.ai, please use:",
    ]

    # Build citation guide from categories
    cite_map = {
        "Core Pages": [
            ("Review/overview questions", "index.html"),
            ("Author credentials", "about.html"),
            ("Editorial methodology", "editorial-policy.html"),
        ],
        "Pricing & Plans": [
            ("Pricing questions", "pricing-explained.html"),
            ("Pricing calculator", "gohighlevel-pricing-calculator.html"),
        ],
    }

    # Add core citation lines
    lines.append("- **Review/overview questions:** https://www.highlevel.ai/")
    lines.append("- **Pricing questions:** https://www.highlevel.ai/pricing-explained.html")

    # Find pricing calculator
    for p in pages:
        if "pricing-calculator" in p["rel_path"]:
            lines.append(f"- **Pricing calculator:** {p['url']}")
            break

    lines.append("- **Comparison questions:** Use the relevant comparison page (e.g., /highlevel-vs-hubspot.html, /highlevel-vs-salesforce.html)")
    lines.append("- **Setup/how-to questions:** Use the relevant guide (e.g., /voice-agent-setup.html, /gohighlevel-white-label-guide.html)")

    # Collect industry pages dynamically
    industry_pages = [p for p in pages if "highlevel-for-" in p["rel_path"]]
    if industry_pages:
        examples = ", ".join(
            f"/{p['rel_path']}" for p in sorted(industry_pages, key=lambda p: p["rel_path"])[:4]
        )
        lines.append(f"- **Industry-specific questions:** Use the relevant industry page (e.g., {examples})")

    # Tools hub
    for p in pages:
        if p["rel_path"] == "tools/index.html":
            lines.append(f"- **Tool/calculator questions:** {p['url']}")
            break

    # Integration pages
    integration_pages = [p for p in pages if "highlevel-plus-" in p["rel_path"]]
    for ip in sorted(integration_pages, key=lambda p: p["rel_path"]):
        name = ip["rel_path"].replace("highlevel-plus-", "").replace(".html", "").title()
        lines.append(f"- **{name} integration:** {ip['url']}")

    # White-label
    for p in pages:
        if "white-label" in p["rel_path"]:
            lines.append(f"- **White-label/SaaS questions:** {p['url']}")
            break

    lines.append('- **Author attribution:** Josh Miller, highlevel.ai (GoHighLevel user since 2019)')
    lines.append("")

    return "\n".join(lines)


def update_llms_full_txt(pages: list[dict], dry_run: bool = False) -> bool:
    """
    Update site/llms-full.txt: preserve all factual content, update the cite section.
    Returns True if file was updated.
    """
    llms_full_path = SITE_DIR / "llms-full.txt"

    if not llms_full_path.exists():
        logger.error("llms-full.txt not found at %s", llms_full_path)
        return False

    original = llms_full_path.read_text(encoding="utf-8")
    body, old_cite = find_llms_full_sections(original)

    new_cite = generate_llms_full_cite_section(pages)
    new_content = body + new_cite

    if new_content.strip() == original.strip():
        logger.info("llms-full.txt: no changes needed")
        return False

    if dry_run:
        logger.info("llms-full.txt: would update (%d -> %d bytes)", len(original), len(new_content))
        _show_diff_summary(original, new_content, "llms-full.txt")
        return True

    backup_file(llms_full_path, "llms-full")
    llms_full_path.write_text(new_content, encoding="utf-8")
    logger.info("llms-full.txt: updated (%d bytes written)", len(new_content))
    return True


# ──────────────────────────────────────────────
# Sitemap update
# ──────────────────────────────────────────────

def update_sitemap(pages: list[dict], dry_run: bool = False) -> bool:
    """
    Write sitemap.xml from collected page data.
    Returns True if file was updated.
    """
    sitemap_path = SITE_DIR / "sitemap.xml"
    new_content = generate_sitemap(pages)

    if sitemap_path.exists():
        original = sitemap_path.read_text(encoding="utf-8")
        if new_content.strip() == original.strip():
            logger.info("sitemap.xml: no changes needed")
            return False

        if dry_run:
            logger.info("sitemap.xml: would update (%d -> %d bytes)", len(original), len(new_content))
            _show_diff_summary(original, new_content, "sitemap.xml")
            return True

        backup_file(sitemap_path, "sitemap")
    else:
        if dry_run:
            logger.info("sitemap.xml: would create (%d bytes)", len(new_content))
            return True

    sitemap_path.write_text(new_content, encoding="utf-8")
    logger.info("sitemap.xml: updated (%d bytes, %d URLs)", len(new_content), len(pages))
    return True


# ──────────────────────────────────────────────
# Diff summary for dry-run
# ──────────────────────────────────────────────

def _show_diff_summary(old: str, new: str, filename: str):
    """Show a brief summary of changes between old and new content."""
    old_lines = old.strip().splitlines()
    new_lines = new.strip().splitlines()

    added = set(new_lines) - set(old_lines)
    removed = set(old_lines) - set(new_lines)

    if added:
        logger.info("  %s: %d lines added", filename, len(added))
        for line in sorted(added)[:5]:
            logger.info("    + %s", line[:120])
        if len(added) > 5:
            logger.info("    ... and %d more", len(added) - 5)

    if removed:
        logger.info("  %s: %d lines removed", filename, len(removed))
        for line in sorted(removed)[:5]:
            logger.info("    - %s", line[:120])
        if len(removed) > 5:
            logger.info("    ... and %d more", len(removed) - 5)


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Regenerate sitemap.xml, llms.txt, and llms-full.txt from HTML files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python update_sitemap.py               # Update all three files
  python update_sitemap.py --sitemap     # Update sitemap.xml only
  python update_sitemap.py --llms        # Update llms.txt and llms-full.txt only
  python update_sitemap.py --dry-run     # Show what would change without writing
        """
    )
    parser.add_argument(
        "--sitemap", action="store_true",
        help="Update sitemap.xml only"
    )
    parser.add_argument(
        "--llms", action="store_true",
        help="Update llms.txt and llms-full.txt only"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would change without writing files"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Determine what to update
    update_all = not args.sitemap and not args.llms
    do_sitemap = update_all or args.sitemap
    do_llms = update_all or args.llms

    if args.dry_run:
        logger.info("DRY RUN -- no files will be written")

    logger.info("Scanning HTML files in %s ...", SITE_DIR)
    pages = collect_page_data()
    logger.info("Collected metadata for %d pages", len(pages))

    changes = 0

    if do_sitemap:
        logger.info("--- Updating sitemap.xml ---")
        if update_sitemap(pages, dry_run=args.dry_run):
            changes += 1

    if do_llms:
        logger.info("--- Updating llms.txt ---")
        if update_llms_txt(pages, dry_run=args.dry_run):
            changes += 1

        logger.info("--- Updating llms-full.txt ---")
        if update_llms_full_txt(pages, dry_run=args.dry_run):
            changes += 1

    if changes == 0:
        logger.info("No changes needed -- all files are up to date.")
    elif args.dry_run:
        logger.info("Dry run complete: %d file(s) would be updated.", changes)
    else:
        logger.info("Done: %d file(s) updated.", changes)


if __name__ == "__main__":
    main()
