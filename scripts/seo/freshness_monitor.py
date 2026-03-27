#!/usr/bin/env python3
"""
Freshness Monitor for highlevel.ai

Scans all HTML pages for outdated content:
  1. Outdated year references (2025 or older in visible text)
  2. Stale dateModified in JSON-LD / meta tags (>90 days old)
  3. Pricing mismatches vs. canonical pricing data
  4. Broken external links (HTTP HEAD with caching)

Generates a markdown report in reports/freshness-audit-YYYY-MM-DD.md
"""

import sys
import re
import json
import time
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from html.parser import HTMLParser

# ── Shared config import ────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import *

# ── Optional: requests for link checking ────────
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# ── Logging ─────────────────────────────────────
logger = setup_logging("freshness_monitor")

# ── Constants ───────────────────────────────────
CURRENT_YEAR = datetime.now().year
STALE_DAYS = 90
LINK_CACHE_FILE = DATA_DIR / "freshness_cache.json"
LINK_CACHE_MAX_AGE_DAYS = 7
RATE_LIMIT_DELAY = 0.5  # 2 requests/sec max

# Tags whose text content counts as "visible text"
VISIBLE_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "td", "span", "title"}

# Year regex: matches 2000-2025
YEAR_PATTERN = re.compile(r"\b20[0-2][0-5]\b")

# Dollar amount pattern
PRICE_PATTERN = re.compile(r"\$[\d,]+(?:/\w+)?(?:/\w+)?")

# Brand name variants for proximity matching
BRAND_NAMES = {
    "gohighlevel": ["gohighlevel", "go high level", "go highlevel", "highlevel"],
    "hubspot": ["hubspot"],
    "clickfunnels": ["clickfunnels", "click funnels"],
    "activecampaign": ["activecampaign", "active campaign"],
    "keap": ["keap", "infusionsoft"],
    "salesforce": ["salesforce"],
}

# ────────────────────────────────────────────────
# HTML text extraction helpers
# ────────────────────────────────────────────────

class VisibleTextExtractor(HTMLParser):
    """Extract text from specific visible-content tags, ignoring scripts/styles/comments."""

    def __init__(self):
        super().__init__()
        self.results = []         # list of (tag, text) tuples
        self._current_tag = None
        self._depth_stack = []
        self._skip_tags = {"script", "style", "noscript"}
        self._skip_depth = 0
        self._buffer = []

    def handle_starttag(self, tag, attrs):
        tag_lower = tag.lower()
        self._depth_stack.append(tag_lower)
        if tag_lower in self._skip_tags:
            self._skip_depth += 1
        if self._skip_depth == 0 and tag_lower in VISIBLE_TAGS:
            self._current_tag = tag_lower
            self._buffer = []

    def handle_endtag(self, tag):
        tag_lower = tag.lower()
        if tag_lower in self._skip_tags and self._skip_depth > 0:
            self._skip_depth -= 1
        if self._current_tag == tag_lower and self._skip_depth == 0:
            text = " ".join("".join(self._buffer).split())
            if text:
                self.results.append((self._current_tag, text))
            self._current_tag = None
            self._buffer = []
        if self._depth_stack and self._depth_stack[-1] == tag_lower:
            self._depth_stack.pop()

    def handle_data(self, data):
        if self._skip_depth == 0 and self._current_tag is not None:
            self._buffer.append(data)


def extract_visible_text(html: str) -> list[tuple[str, str]]:
    """Return list of (tag, text) from visible content tags."""
    parser = VisibleTextExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    return parser.results


def extract_meta_description_content(html: str) -> str:
    """Extract the content attribute from <meta name='description'>."""
    m = re.search(
        r'<meta\s+[^>]*name\s*=\s*["\']description["\'][^>]*content\s*=\s*["\']([^"\']*)["\']',
        html, re.IGNORECASE
    )
    if not m:
        m = re.search(
            r'<meta\s+[^>]*content\s*=\s*["\']([^"\']*)["\'][^>]*name\s*=\s*["\']description["\']',
            html, re.IGNORECASE
        )
    return m.group(1) if m else ""


def extract_jsonld_blocks(html: str) -> list[dict]:
    """Extract all JSON-LD objects from <script type='application/ld+json'> blocks."""
    blocks = []
    pattern = re.compile(
        r'<script\s+type\s*=\s*["\']application/ld\+json["\']\s*>(.*?)</script>',
        re.DOTALL | re.IGNORECASE
    )
    for m in pattern.finditer(html):
        try:
            data = json.loads(m.group(1))
            blocks.append(data)
        except (json.JSONDecodeError, ValueError):
            pass
    return blocks


def extract_external_links(html: str) -> list[str]:
    """Find all external <a href='https://...'> URLs not pointing to highlevel.ai."""
    pattern = re.compile(r'<a\s+[^>]*href\s*=\s*["\']?(https?://[^"\'>\s]+)', re.IGNORECASE)
    urls = []
    for m in pattern.finditer(html):
        url = m.group(1)
        # Skip internal links
        if "highlevel.ai" in url.lower():
            continue
        urls.append(url)
    return list(set(urls))


def extract_article_modified_time(html: str) -> str | None:
    """Extract <meta property='article:modified_time'> content."""
    m = re.search(
        r'<meta\s+[^>]*property\s*=\s*["\']article:modified_time["\'][^>]*content\s*=\s*["\']([^"\']*)["\']',
        html, re.IGNORECASE
    )
    if not m:
        m = re.search(
            r'<meta\s+[^>]*content\s*=\s*["\']([^"\']*)["\'][^>]*property\s*=\s*["\']article:modified_time["\']',
            html, re.IGNORECASE
        )
    return m.group(1) if m else None


# ────────────────────────────────────────────────
# Check 1: Outdated year references
# ────────────────────────────────────────────────

def check_outdated_years(html: str, filepath: Path) -> list[dict]:
    """Find year references <= 2025 in visible text."""
    issues = []
    visible = extract_visible_text(html)

    # Also check meta description
    meta_desc = extract_meta_description_content(html)
    if meta_desc:
        visible.append(("meta description", meta_desc))

    for tag, text in visible:
        for m in YEAR_PATTERN.finditer(text):
            year = int(m.group())
            if year < CURRENT_YEAR:
                start = max(0, m.start() - 30)
                end = min(len(text), m.end() + 30)
                context = text[start:end].strip()
                issues.append({
                    "type": "outdated_year",
                    "severity": "warning",
                    "year": year,
                    "tag": tag,
                    "context": f"...{context}...",
                    "file": str(filepath.relative_to(SITE_DIR)),
                })
    return issues


# ────────────────────────────────────────────────
# Check 2: Stale dateModified
# ────────────────────────────────────────────────

def parse_date_string(date_str: str) -> datetime | None:
    """Try to parse a date string in common formats."""
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%B %d, %Y"):
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return dt.replace(tzinfo=None) if dt.tzinfo else dt
        except ValueError:
            continue
    return None


def check_stale_dates(html: str, filepath: Path) -> list[dict]:
    """Check JSON-LD dateModified and article:modified_time for staleness."""
    issues = []
    cutoff = datetime.now() - timedelta(days=STALE_DAYS)

    # JSON-LD blocks
    for block in extract_jsonld_blocks(html):
        date_str = block.get("dateModified", "")
        if date_str:
            dt = parse_date_string(date_str)
            if dt and dt < cutoff:
                days_old = (datetime.now() - dt).days
                issues.append({
                    "type": "stale_date",
                    "severity": "warning",
                    "source": "JSON-LD dateModified",
                    "date": date_str,
                    "days_old": days_old,
                    "file": str(filepath.relative_to(SITE_DIR)),
                })

    # article:modified_time meta tag
    modified_time = extract_article_modified_time(html)
    if modified_time:
        dt = parse_date_string(modified_time)
        if dt and dt < cutoff:
            days_old = (datetime.now() - dt).days
            issues.append({
                "type": "stale_date",
                "severity": "warning",
                "source": "meta article:modified_time",
                "date": modified_time,
                "days_old": days_old,
                "file": str(filepath.relative_to(SITE_DIR)),
            })

    return issues


# ────────────────────────────────────────────────
# Check 3: Pricing mismatches
# ────────────────────────────────────────────────

def load_canonical_pricing() -> dict:
    """Load canonical pricing and flatten to {brand: set_of_prices}."""
    pricing_file = DATA_DIR / "current_pricing.json"
    raw = load_json(pricing_file, {})
    flat = {}
    for brand, plans in raw.items():
        prices = set()
        for plan_name, value in plans.items():
            # Extract dollar amounts from values like "$97/mo", "14-day free trial"
            for pm in PRICE_PATTERN.finditer(str(value)):
                prices.add(pm.group())
        flat[brand] = prices
    return flat


def check_pricing(html: str, filepath: Path, canonical: dict) -> list[dict]:
    """Find dollar amounts near brand mentions and flag mismatches."""
    issues = []
    # Get all visible text as one blob for proximity search
    visible = extract_visible_text(html)
    full_text = " ".join(text for _, text in visible).lower()

    if not full_text:
        return issues

    for brand, variants in BRAND_NAMES.items():
        if brand not in canonical:
            continue
        known_prices = canonical[brand]
        # Normalize known prices to lowercase for comparison
        known_lower = {p.lower() for p in known_prices}

        for variant in variants:
            # Find all positions of this brand variant in text
            variant_lower = variant.lower()
            start = 0
            while True:
                idx = full_text.find(variant_lower, start)
                if idx == -1:
                    break
                # Look for dollar amounts within 200 chars on either side
                window_start = max(0, idx - 200)
                window_end = min(len(full_text), idx + len(variant_lower) + 200)
                window = full_text[window_start:window_end]

                for pm in PRICE_PATTERN.finditer(window):
                    found_price = pm.group()
                    if found_price not in known_lower:
                        # Get context around the price
                        p_start = max(0, pm.start() - 20)
                        p_end = min(len(window), pm.end() + 20)
                        context = window[p_start:p_end].strip()
                        issues.append({
                            "type": "pricing_mismatch",
                            "severity": "critical",
                            "brand": brand,
                            "found_price": found_price,
                            "known_prices": sorted(known_prices),
                            "context": f"...{context}...",
                            "file": str(filepath.relative_to(SITE_DIR)),
                        })
                start = idx + 1

    # Deduplicate by (file, brand, found_price)
    seen = set()
    unique = []
    for issue in issues:
        key = (issue["file"], issue["brand"], issue["found_price"])
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    return unique


# ────────────────────────────────────────────────
# Check 4: Broken external links
# ────────────────────────────────────────────────

def load_link_cache() -> dict:
    """Load cached link check results. Format: {url: {status, checked, error}}."""
    return load_json(LINK_CACHE_FILE, {})


def save_link_cache(cache: dict):
    """Persist link cache."""
    save_json(LINK_CACHE_FILE, cache)


def is_cache_fresh(entry: dict) -> bool:
    """Return True if the cache entry was checked within LINK_CACHE_MAX_AGE_DAYS."""
    checked = entry.get("checked", "")
    if not checked:
        return False
    try:
        dt = datetime.strptime(checked, "%Y-%m-%d")
        return (datetime.now() - dt).days < LINK_CACHE_MAX_AGE_DAYS
    except ValueError:
        return False


def check_external_links(html: str, filepath: Path, cache: dict) -> list[dict]:
    """Check all external links via HTTP HEAD. Returns issues for broken links."""
    if not HAS_REQUESTS:
        logger.warning("requests library not installed -- skipping link checks")
        return []

    issues = []
    urls = extract_external_links(html)
    rel_file = str(filepath.relative_to(SITE_DIR))

    for url in urls:
        # Check cache first
        if url in cache and is_cache_fresh(cache[url]):
            status = cache[url].get("status", 0)
            error = cache[url].get("error", "")
        else:
            # Rate limit
            time.sleep(RATE_LIMIT_DELAY)
            status = 0
            error = ""
            try:
                resp = requests.head(
                    url,
                    timeout=5,
                    allow_redirects=True,
                    headers={"User-Agent": "HighlevelAI-FreshnessMonitor/1.0"}
                )
                status = resp.status_code
            except requests.exceptions.Timeout:
                error = "timeout"
            except requests.exceptions.ConnectionError:
                error = "connection_error"
            except requests.exceptions.TooManyRedirects:
                error = "too_many_redirects"
            except Exception as e:
                error = str(e)[:100]

            # Update cache
            cache[url] = {
                "status": status,
                "checked": today(),
                "error": error,
            }

        # Flag 4xx/5xx or connection errors
        if error or (status >= 400):
            issues.append({
                "type": "broken_link",
                "severity": "critical",
                "url": url,
                "status": status,
                "error": error,
                "file": rel_file,
            })

    return issues


# ────────────────────────────────────────────────
# Report generation
# ────────────────────────────────────────────────

def generate_report(all_issues: list[dict], pages_checked: int) -> str:
    """Build a markdown report from all collected issues."""
    critical = [i for i in all_issues if i["severity"] == "critical"]
    warnings = [i for i in all_issues if i["severity"] == "warning"]

    # Count by type
    type_counts = {}
    for issue in all_issues:
        t = issue["type"]
        type_counts[t] = type_counts.get(t, 0) + 1

    lines = []
    lines.append(f"# Freshness Audit Report -- {today()}")
    lines.append("")

    # ── Summary ─────────────────────
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Pages checked:** {pages_checked}")
    lines.append(f"- **Total issues:** {len(all_issues)}")
    lines.append(f"- **Critical:** {len(critical)}")
    lines.append(f"- **Warnings:** {len(warnings)}")
    lines.append("")
    if type_counts:
        lines.append("| Issue Type | Count |")
        lines.append("|---|---|")
        for t, c in sorted(type_counts.items()):
            lines.append(f"| {t} | {c} |")
        lines.append("")

    # ── Critical Issues ─────────────
    lines.append("## Critical Issues")
    lines.append("")
    if not critical:
        lines.append("No critical issues found.")
        lines.append("")
    else:
        for issue in critical:
            if issue["type"] == "broken_link":
                status_info = f"HTTP {issue['status']}" if issue["status"] else issue.get("error", "unknown")
                lines.append(f"- **Broken link** in `{issue['file']}`: [{issue['url']}]({issue['url']}) -- {status_info}")
            elif issue["type"] == "pricing_mismatch":
                lines.append(
                    f"- **Pricing mismatch** in `{issue['file']}`: "
                    f"found `{issue['found_price']}` for **{issue['brand']}** "
                    f"(known: {', '.join(issue['known_prices'])})"
                )
                lines.append(f"  - Context: `{issue['context']}`")
            else:
                lines.append(f"- **{issue['type']}** in `{issue['file']}`")
        lines.append("")

    # ── Warnings ────────────────────
    lines.append("## Warnings")
    lines.append("")
    if not warnings:
        lines.append("No warnings found.")
        lines.append("")
    else:
        for issue in warnings:
            if issue["type"] == "outdated_year":
                lines.append(
                    f"- **Outdated year** `{issue['year']}` in `{issue['file']}` "
                    f"(`<{issue['tag']}>`) -- `{issue['context']}`"
                )
            elif issue["type"] == "stale_date":
                lines.append(
                    f"- **Stale date** in `{issue['file']}`: "
                    f"{issue['source']} = `{issue['date']}` ({issue['days_old']} days old)"
                )
            else:
                lines.append(f"- **{issue['type']}** in `{issue['file']}`")
        lines.append("")

    # ── Full Details (per-page) ─────
    lines.append("## Full Details")
    lines.append("")

    # Group issues by file
    by_file: dict[str, list[dict]] = {}
    for issue in all_issues:
        f = issue["file"]
        by_file.setdefault(f, []).append(issue)

    if not by_file:
        lines.append("No issues found in any page.")
        lines.append("")
    else:
        for file_path in sorted(by_file.keys()):
            file_issues = by_file[file_path]
            crit_count = sum(1 for i in file_issues if i["severity"] == "critical")
            warn_count = sum(1 for i in file_issues if i["severity"] == "warning")
            lines.append(f"### `{file_path}`")
            lines.append(f"_{crit_count} critical, {warn_count} warnings_")
            lines.append("")
            for issue in file_issues:
                if issue["type"] == "broken_link":
                    status_info = f"HTTP {issue['status']}" if issue["status"] else issue.get("error", "unknown")
                    lines.append(f"- [{issue['severity'].upper()}] Broken link: `{issue['url']}` -- {status_info}")
                elif issue["type"] == "pricing_mismatch":
                    lines.append(
                        f"- [CRITICAL] Pricing: found `{issue['found_price']}` for {issue['brand']} "
                        f"(expected one of: {', '.join(issue['known_prices'])})"
                    )
                elif issue["type"] == "outdated_year":
                    lines.append(f"- [WARNING] Year `{issue['year']}` in `<{issue['tag']}>`: `{issue['context']}`")
                elif issue["type"] == "stale_date":
                    lines.append(f"- [WARNING] {issue['source']}: `{issue['date']}` ({issue['days_old']}d old)")
                else:
                    lines.append(f"- [{issue['severity'].upper()}] {issue['type']}")
            lines.append("")

    lines.append(f"---\n_Generated by freshness_monitor.py at {now()}_")
    return "\n".join(lines)


# ────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Scan highlevel.ai HTML pages for outdated content, broken links, and pricing mismatches.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python freshness_monitor.py              # Run all checks\n"
            "  python freshness_monitor.py --skip-links  # Everything except link checking (faster)\n"
            "  python freshness_monitor.py --pricing     # Only check pricing\n"
            "  python freshness_monitor.py --links       # Only check external links\n"
            "  python freshness_monitor.py --dates       # Only check date freshness\n"
        ),
    )
    parser.add_argument("--pricing", action="store_true", help="Only check pricing mismatches")
    parser.add_argument("--links", action="store_true", help="Only check external links")
    parser.add_argument("--dates", action="store_true", help="Only check date freshness (years + dateModified)")
    parser.add_argument("--skip-links", action="store_true", help="Run everything except link checking (faster)")

    args = parser.parse_args()

    # Determine which checks to run
    # If any specific flag is set, only run those checks
    specific = args.pricing or args.links or args.dates
    run_years = (not specific) or args.dates
    run_dates = (not specific) or args.dates
    run_pricing = (not specific) or args.pricing
    run_links = ((not specific) or args.links) and (not args.skip_links)

    ensure_dirs()

    html_files = get_all_html_files()
    if not html_files:
        logger.error("No HTML files found in %s", SITE_DIR)
        sys.exit(1)

    logger.info("Freshness monitor starting -- %d HTML files to scan", len(html_files))

    # Load pricing data if needed
    canonical_pricing = load_canonical_pricing() if run_pricing else {}

    # Load link cache if needed
    link_cache = load_link_cache() if run_links else {}

    all_issues: list[dict] = []
    pages_checked = 0

    for filepath in html_files:
        rel = filepath.relative_to(SITE_DIR)
        logger.info("Scanning %s", rel)
        try:
            html = filepath.read_text(encoding="utf-8")
        except Exception as e:
            logger.error("Failed to read %s: %s", rel, e)
            continue

        pages_checked += 1

        try:
            if run_years:
                all_issues.extend(check_outdated_years(html, filepath))
        except Exception as e:
            logger.error("Year check failed on %s: %s", rel, e)

        try:
            if run_dates:
                all_issues.extend(check_stale_dates(html, filepath))
        except Exception as e:
            logger.error("Date check failed on %s: %s", rel, e)

        try:
            if run_pricing:
                all_issues.extend(check_pricing(html, filepath, canonical_pricing))
        except Exception as e:
            logger.error("Pricing check failed on %s: %s", rel, e)

        try:
            if run_links:
                all_issues.extend(check_external_links(html, filepath, link_cache))
        except Exception as e:
            logger.error("Link check failed on %s: %s", rel, e)

    # Save link cache
    if run_links and link_cache:
        save_link_cache(link_cache)
        logger.info("Link cache saved (%d entries)", len(link_cache))

    # Generate report
    report = generate_report(all_issues, pages_checked)
    report_name = f"freshness-audit-{today()}.md"
    report_path = save_report(report_name, report)
    logger.info("Report saved to %s", report_path)

    # Print summary to console
    critical_count = sum(1 for i in all_issues if i["severity"] == "critical")
    warning_count = sum(1 for i in all_issues if i["severity"] == "warning")
    print(f"\n{'='*50}")
    print(f"Freshness Audit Complete")
    print(f"{'='*50}")
    print(f"Pages checked:  {pages_checked}")
    print(f"Critical issues: {critical_count}")
    print(f"Warnings:        {warning_count}")
    print(f"Report: {report_path}")

    # Exit with non-zero if critical issues found
    if critical_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
