#!/usr/bin/env python3
"""
auto_fix.py -- Automatically fix common SEO issues across all HTML files.

Modes:
  --fix-years   Replace "2025" with "2026" in safe visible-content contexts
  --fix-dates   Refresh stale dateModified / article:modified_time values
  --fix-all     Run both fixes
  --dry-run     Preview changes without writing files

Usage:
  python3 scripts/seo/auto_fix.py --fix-all
  python3 scripts/seo/auto_fix.py --fix-years --dry-run
  python3 scripts/seo/auto_fix.py --fix-dates
"""

import re
import json
import argparse
from datetime import datetime, timedelta
from pathlib import Path

from config import (
    SITE_DIR,
    setup_logging,
    get_all_html_files,
    backup_file,
    save_report,
    today,
    now,
)

# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────

OLD_YEAR = "2025"
NEW_YEAR = "2026"
STALE_DAYS = 7

# Tags whose text content is safe to update year references in
SAFE_TEXT_TAGS = r"title|h[1-6]|p|li|td|th|span|a"

logger = setup_logging("auto_fix")


# ──────────────────────────────────────────────
# Year-fixing helpers
# ──────────────────────────────────────────────

def _fix_years_in_meta(html: str, changes: list, relpath: str) -> str:
    """Replace 2025 -> 2026 inside <meta name='description'> and <meta property='og:...'> content attrs."""

    def _replace_meta(m):
        full = m.group(0)
        if re.search(r"\b" + OLD_YEAR + r"\b", full):
            new = re.sub(r"\b" + OLD_YEAR + r"\b", NEW_YEAR, full)
            # extract content attr for reporting
            content_old = re.search(r'content="([^"]*)"', full)
            content_new = re.search(r'content="([^"]*)"', new)
            if content_old and content_new:
                changes.append({
                    "file": relpath,
                    "type": "year_ref",
                    "old": content_old.group(1)[:80],
                    "new": content_new.group(1)[:80],
                })
            return new
        return full

    # meta description
    html = re.sub(
        r'<meta\s+name="description"\s+content="[^"]*"[^>]*/?>',
        _replace_meta, html, flags=re.IGNORECASE
    )
    # og: meta tags
    html = re.sub(
        r'<meta\s+property="og:[^"]*"\s+content="[^"]*"[^>]*/?>',
        _replace_meta, html, flags=re.IGNORECASE
    )
    return html


def _fix_years_in_text_tags(html: str, changes: list, relpath: str) -> str:
    """Replace 2025 -> 2026 inside safe text-content tags (h1-h6, p, li, etc.)."""

    def _replace_tag_content(m):
        open_tag = m.group(1)
        inner = m.group(2)
        close_tag = m.group(3)

        if not re.search(r"\b" + OLD_YEAR + r"\b", inner):
            return m.group(0)

        # Don't touch anything that looks like a URL inside the tag content
        # Split on href/src patterns and only replace outside them
        new_inner = _safe_replace_outside_urls(inner)

        if new_inner != inner:
            snippet_old = inner.strip()[:80]
            snippet_new = new_inner.strip()[:80]
            changes.append({
                "file": relpath,
                "type": "year_ref",
                "old": snippet_old,
                "new": snippet_new,
            })
        return open_tag + new_inner + close_tag

    pattern = rf"(<(?:{SAFE_TEXT_TAGS})(?:\s[^>]*)?>)(.*?)(</(?:{SAFE_TEXT_TAGS})>)"
    html = re.sub(pattern, _replace_tag_content, html, flags=re.IGNORECASE | re.DOTALL)
    return html


def _fix_years_in_title(html: str, changes: list, relpath: str) -> str:
    """Replace 2025 -> 2026 inside <title> tags (handled separately for clarity)."""

    def _replace_title(m):
        open_t = m.group(1)
        inner = m.group(2)
        close_t = m.group(3)
        if re.search(r"\b" + OLD_YEAR + r"\b", inner):
            new_inner = re.sub(r"\b" + OLD_YEAR + r"\b", NEW_YEAR, inner)
            changes.append({
                "file": relpath,
                "type": "year_ref",
                "old": inner.strip()[:80],
                "new": new_inner.strip()[:80],
            })
            return open_t + new_inner + close_t
        return m.group(0)

    html = re.sub(r"(<title[^>]*>)(.*?)(</title>)", _replace_title, html,
                  flags=re.IGNORECASE | re.DOTALL)
    return html


def _fix_years_in_jsonld(html: str, changes: list, relpath: str) -> str:
    """Replace year portion of datePublished / dateModified in JSON-LD blocks."""

    def _replace_jsonld_block(m):
        block = m.group(0)
        script_open = m.group(1)
        body = m.group(2)
        script_close = m.group(3)

        new_body = body

        for field in ("datePublished", "dateModified"):
            pattern = rf'("{field}"\s*:\s*")({OLD_YEAR})([-\dT:+Z]*")'
            match = re.search(pattern, new_body)
            if match:
                changes.append({
                    "file": relpath,
                    "type": "year_ref",
                    "old": f'{field}: {OLD_YEAR}{match.group(3).rstrip(chr(34))}',
                    "new": f'{field}: {NEW_YEAR}{match.group(3).rstrip(chr(34))}',
                })
                new_body = re.sub(pattern, rf"\g<1>{NEW_YEAR}\g<3>", new_body)

        return script_open + new_body + script_close

    html = re.sub(
        r'(<script\s+type="application/ld\+json"[^>]*>)(.*?)(</script>)',
        _replace_jsonld_block, html, flags=re.IGNORECASE | re.DOTALL
    )
    return html


def _safe_replace_outside_urls(text: str) -> str:
    """Replace \\b2025\\b with 2026 in text, but skip anything that looks like a URL."""
    # Tokenise: pull out URLs, replace only in non-URL segments
    url_pattern = r'https?://[^\s<>"\']+|href="[^"]*"|src="[^"]*"'
    parts = re.split(f"({url_pattern})", text)
    result = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            # This is a URL token -- leave it alone
            result.append(part)
        else:
            result.append(re.sub(r"\b" + OLD_YEAR + r"\b", NEW_YEAR, part))
    return "".join(result)


def fix_years(html_files: list[Path], dry_run: bool = False) -> list[dict]:
    """Scan files and replace 2025 -> 2026 in safe contexts. Returns list of changes."""
    all_changes: list[dict] = []

    for filepath in html_files:
        relpath = str(filepath.relative_to(SITE_DIR))
        try:
            original = filepath.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"Could not read {relpath}: {e}")
            continue

        if OLD_YEAR not in original:
            continue

        changes_before = len(all_changes)
        html = original

        # Apply fixes in order
        html = _fix_years_in_title(html, all_changes, relpath)
        html = _fix_years_in_meta(html, all_changes, relpath)
        html = _fix_years_in_text_tags(html, all_changes, relpath)
        html = _fix_years_in_jsonld(html, all_changes, relpath)

        if len(all_changes) > changes_before and html != original:
            if dry_run:
                logger.info(f"[DRY RUN] Would fix years in {relpath}")
            else:
                backup_file(filepath, "autofix")
                filepath.write_text(html, encoding="utf-8")
                logger.info(f"Fixed years in {relpath}")

    return all_changes


# ──────────────────────────────────────────────
# Date-fixing helpers
# ──────────────────────────────────────────────

def _extract_jsonld_date_modified(html: str) -> str | None:
    """Extract the first dateModified value from JSON-LD blocks."""
    m = re.search(
        r'<script\s+type="application/ld\+json"[^>]*>(.*?)</script>',
        html, flags=re.IGNORECASE | re.DOTALL
    )
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError):
        return None

    # Handle @graph arrays
    if isinstance(data, dict) and "@graph" in data:
        items = data["@graph"]
    elif isinstance(data, list):
        items = data
    else:
        items = [data]

    for item in items:
        if isinstance(item, dict) and "dateModified" in item:
            return item["dateModified"]
    return None


def _is_stale(date_str: str, threshold_days: int = STALE_DAYS) -> bool:
    """Check if a date string (YYYY-MM-DD or ISO) is older than threshold_days."""
    try:
        # Handle full ISO datetime or plain date
        date_part = date_str[:10]
        dt = datetime.strptime(date_part, "%Y-%m-%d")
        return (datetime.now() - dt) > timedelta(days=threshold_days)
    except (ValueError, TypeError):
        return False


def fix_dates(html_files: list[Path], dry_run: bool = False) -> list[dict]:
    """Update stale dateModified values to today. Returns list of changes."""
    all_changes: list[dict] = []
    today_str = today()

    for filepath in html_files:
        relpath = str(filepath.relative_to(SITE_DIR))
        try:
            html = filepath.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"Could not read {relpath}: {e}")
            continue

        date_mod = _extract_jsonld_date_modified(html)
        if not date_mod or not _is_stale(date_mod):
            continue

        original = html
        old_date_part = date_mod[:10]

        # Update dateModified in JSON-LD
        # Match: "dateModified": "YYYY-MM-DD..." and replace just the date portion
        def _replace_date_modified_jsonld(m):
            return m.group(1) + today_str + m.group(3)

        html = re.sub(
            r'("dateModified"\s*:\s*")(\d{4}-\d{2}-\d{2})(T[^"]*"|\s*")',
            _replace_date_modified_jsonld, html
        )

        # Update <meta property="article:modified_time"> if present
        old_meta_match = re.search(
            r'(<meta\s+property="article:modified_time"\s+content=")([^"]+)(")',
            html, flags=re.IGNORECASE
        )
        meta_updated = False
        if old_meta_match:
            old_meta_date = old_meta_match.group(2)
            html = re.sub(
                r'(<meta\s+property="article:modified_time"\s+content=")[^"]+(")',
                rf"\g<1>{today_str}\g<2>", html, flags=re.IGNORECASE
            )
            meta_updated = True

        if html != original:
            all_changes.append({
                "file": relpath,
                "type": "dateModified",
                "old": old_date_part,
                "new": today_str,
            })
            if meta_updated:
                all_changes.append({
                    "file": relpath,
                    "type": "article:modified_time",
                    "old": old_meta_date[:10] if old_meta_match else old_date_part,
                    "new": today_str,
                })

            if dry_run:
                logger.info(f"[DRY RUN] Would refresh dateModified in {relpath} ({old_date_part} -> {today_str})")
            else:
                backup_file(filepath, "autofix")
                filepath.write_text(html, encoding="utf-8")
                logger.info(f"Refreshed dateModified in {relpath} ({old_date_part} -> {today_str})")

    return all_changes


# ──────────────────────────────────────────────
# Report generation
# ──────────────────────────────────────────────

def generate_report(
    files_scanned: int,
    files_modified: int,
    year_changes: list[dict],
    date_changes: list[dict],
    dry_run: bool = False,
) -> str:
    """Build the markdown report and save it."""
    all_changes = year_changes + date_changes
    date_updates = [c for c in date_changes if c["type"] in ("dateModified", "article:modified_time")]

    header = f"# Auto-Fix Report {'(DRY RUN) ' if dry_run else ''}-- {today()}\n\n"
    summary = (
        "## Summary\n"
        f"- Files scanned: {files_scanned}\n"
        f"- Files modified: {files_modified}\n"
        f"- Year fixes: {len(year_changes)} ({OLD_YEAR} -> {NEW_YEAR})\n"
        f"- Date updates: {len(date_updates)} (dateModified refreshed)\n\n"
    )

    if all_changes:
        table = "## Changes\n"
        table += "| File | Type | Old | New |\n"
        table += "|------|------|-----|-----|\n"
        for c in all_changes:
            # Escape pipes in values for markdown table safety
            old_val = c["old"].replace("|", "\\|")
            new_val = c["new"].replace("|", "\\|")
            table += f'| {c["file"]} | {c["type"]} | {old_val} | {new_val} |\n'
    else:
        table = "## Changes\nNo changes needed.\n"

    report = header + summary + table
    filename = f"auto-fix-{today()}.md"
    report_path = save_report(filename, report)
    logger.info(f"Report saved to {report_path}")
    return report


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Automatically fix common SEO issues across HTML files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 scripts/seo/auto_fix.py --fix-all\n"
            "  python3 scripts/seo/auto_fix.py --fix-years\n"
            "  python3 scripts/seo/auto_fix.py --fix-dates\n"
            "  python3 scripts/seo/auto_fix.py --fix-all --dry-run\n"
        ),
    )
    parser.add_argument("--fix-years", action="store_true", help="Replace 2025 with 2026 in safe contexts")
    parser.add_argument("--fix-dates", action="store_true", help="Refresh stale dateModified values")
    parser.add_argument("--fix-all", action="store_true", help="Run all fixes (years + dates)")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without modifying files")

    args = parser.parse_args()

    if not (args.fix_years or args.fix_dates or args.fix_all):
        parser.print_help()
        return

    do_years = args.fix_all or args.fix_years
    do_dates = args.fix_all or args.fix_dates
    dry_run = args.dry_run

    mode_label = "DRY RUN" if dry_run else "LIVE"
    logger.info(f"auto_fix starting ({mode_label}) -- years={do_years}, dates={do_dates}")
    logger.info(f"Site directory: {SITE_DIR}")

    html_files = get_all_html_files()
    files_scanned = len(html_files)
    logger.info(f"Found {files_scanned} HTML files")

    year_changes: list[dict] = []
    date_changes: list[dict] = []

    if do_years:
        year_changes = fix_years(html_files, dry_run=dry_run)
        logger.info(f"Year fixes: {len(year_changes)} changes")

    if do_dates:
        date_changes = fix_dates(html_files, dry_run=dry_run)
        date_update_count = len([c for c in date_changes if c["type"] == "dateModified"])
        logger.info(f"Date updates: {date_update_count} files refreshed")

    # Count distinct files modified
    modified_files = set()
    for c in year_changes + date_changes:
        modified_files.add(c["file"])
    files_modified = len(modified_files)

    generate_report(files_scanned, files_modified, year_changes, date_changes, dry_run=dry_run)

    logger.info(f"Done. {files_modified} files {'would be ' if dry_run else ''}modified.")


if __name__ == "__main__":
    main()
