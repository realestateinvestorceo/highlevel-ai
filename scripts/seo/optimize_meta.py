#!/usr/bin/env python3
"""
optimize_meta.py -- Auto-optimize page titles and meta descriptions
based on GSC striking-distance keyword data.

Reads the latest seo-dashboard report, finds keywords at position 5-20
with impressions but 0 clicks, and rewrites <title> / <meta description>
to include the missing keyword naturally.

Usage:
    python optimize_meta.py              # dry-run (default)
    python optimize_meta.py --dry-run    # explicit dry-run
    python optimize_meta.py --apply      # actually modify files
"""

import argparse
import re
import sys
import os
from pathlib import Path
from html import unescape

# Add parent dir so config can be imported when running from scripts/seo/
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import (
    SITE_DIR, REPORTS_DIR, setup_logging, get_all_html_files,
    backup_file, save_report, today, now,
)

# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────

MAX_TITLE_LEN = 60
MAX_DESC_LEN = 155
MIN_IMPRESSIONS = 50          # Only optimize keywords with 50+ impressions
MIN_POSITION = 5.0            # Striking distance lower bound
MAX_POSITION = 20.0           # Striking distance upper bound
MAX_PAGES_PER_RUN = 3         # Conservative: at most 3 pages modified

logger = setup_logging("optimize_meta")


# ──────────────────────────────────────────────
# Parse the latest dashboard report
# ──────────────────────────────────────────────

def find_latest_dashboard() -> Path:
    """Find the most recent seo-dashboard-*.md report file."""
    reports = sorted(REPORTS_DIR.glob("seo-dashboard-*.md"), reverse=True)
    if not reports:
        logger.error("No seo-dashboard report found in %s", REPORTS_DIR)
        sys.exit(1)
    logger.info("Using report: %s", reports[0].name)
    return reports[0]


def parse_striking_distance(report_path: Path) -> list[dict]:
    """
    Parse the 'Striking Distance Keywords (Quick Wins)' table from the
    seo-dashboard report.

    Returns a list of dicts with keys:
        query, position, impressions, clicks, ctr, page
    """
    text = report_path.read_text(encoding="utf-8")

    # Find the striking distance section
    marker = "### Striking Distance Keywords"
    idx = text.find(marker)
    if idx == -1:
        logger.warning("No 'Striking Distance Keywords' section found in report")
        return []

    # Extract lines from the table (skip header + separator)
    section = text[idx:]
    lines = section.split("\n")

    results = []
    in_table = False
    header_seen = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_table:
                break  # empty line after table = end of table
            continue

        if stripped.startswith("|") and "Query" in stripped:
            in_table = True
            header_seen = True
            continue
        if stripped.startswith("|--") or stripped.startswith("| --"):
            continue  # separator row
        if in_table and stripped.startswith("|"):
            cols = [c.strip() for c in stripped.split("|")]
            # cols[0] is empty (before first |), cols[-1] is empty (after last |)
            cols = [c for c in cols if c != ""]
            if len(cols) >= 6:
                query = cols[0]
                try:
                    position = float(cols[1])
                    impressions = int(cols[2].replace(",", ""))
                    clicks = int(cols[3])
                    page = cols[5]  # cols[4] is CTR
                except (ValueError, IndexError):
                    continue

                results.append({
                    "query": query,
                    "position": position,
                    "impressions": impressions,
                    "clicks": clicks,
                    "page": page,
                })

    logger.info("Found %d striking-distance keywords in report", len(results))
    return results


def filter_opportunities(keywords: list[dict]) -> list[dict]:
    """
    Filter to actionable opportunities:
    - Position between MIN_POSITION and MAX_POSITION
    - Impressions >= MIN_IMPRESSIONS
    - Clicks == 0
    """
    filtered = []
    for kw in keywords:
        if kw["clicks"] != 0:
            continue
        if kw["impressions"] < MIN_IMPRESSIONS:
            continue
        if not (MIN_POSITION <= kw["position"] <= MAX_POSITION):
            continue
        filtered.append(kw)

    # Sort by impressions descending (highest opportunity first)
    filtered.sort(key=lambda k: k["impressions"], reverse=True)
    logger.info(
        "After filtering (pos %.0f-%.0f, %d+ impr, 0 clicks): %d opportunities",
        MIN_POSITION, MAX_POSITION, MIN_IMPRESSIONS, len(filtered),
    )
    return filtered


# ──────────────────────────────────────────────
# Read current meta from HTML files
# ──────────────────────────────────────────────

def resolve_page_path(page_url: str) -> Path | None:
    """Convert a report page URL (e.g. /blog/foo.html) to a SITE_DIR file path."""
    # Strip leading slash
    rel = page_url.lstrip("/")
    if rel == "" or rel.endswith("/"):
        # Root page or directory -- map to index.html
        rel = rel + "index.html" if rel else "index.html"

    filepath = SITE_DIR / rel
    if filepath.exists():
        return filepath

    logger.warning("Page file not found: %s (url: %s)", filepath, page_url)
    return None


def extract_title(html: str) -> str | None:
    """Extract the content of the <title> tag."""
    m = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        return unescape(m.group(1)).strip()
    return None


def extract_meta_description(html: str) -> str | None:
    """Extract the content attribute of <meta name="description">."""
    m = re.search(
        r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']',
        html, re.IGNORECASE | re.DOTALL,
    )
    if m:
        return unescape(m.group(1)).strip()
    return None


# ──────────────────────────────────────────────
# Title and description rewriting
# ──────────────────────────────────────────────

def keyword_present(text: str, keyword: str) -> bool:
    """
    Check if the essential parts of the keyword appear in the text.

    For multi-word keywords, we check if the most distinctive 2-word
    phrases from the keyword appear in the text (case-insensitive).
    Single-word keywords must appear exactly.
    """
    text_lower = text.lower()
    kw_lower = keyword.lower()

    # Direct containment check first
    if kw_lower in text_lower:
        return True

    # For multi-word keywords, extract distinctive phrases
    words = kw_lower.split()
    if len(words) <= 2:
        # For short keywords, all words must appear
        return all(w in text_lower for w in words)

    # Filter out stop words and generic terms for phrase matching
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "for", "of",
        "to", "in", "on", "at", "and", "or", "with", "by", "from",
        "2025", "2026", "2027",
    }
    meaningful = [w for w in words if w not in stop_words and len(w) > 2]

    if not meaningful:
        meaningful = words

    # Check if at least the core bigrams are present
    # e.g. "gohighlevel pricing change march 2026" -> check "pricing change"
    for i in range(len(meaningful) - 1):
        bigram = f"{meaningful[i]} {meaningful[i+1]}"
        if bigram in text_lower:
            return True

    # Fallback: check if all meaningful words are present
    return all(w in text_lower for w in meaningful)


def rewrite_title(current_title: str, keyword: str) -> str | None:
    """
    Rewrite a title to include the keyword if it's missing.

    Returns the new title, or None if no change needed or rewrite
    would exceed MAX_TITLE_LEN.

    Strategy:
    - Extract key phrases from the keyword
    - Try to insert them naturally into the existing title
    - Keep the title under MAX_TITLE_LEN chars
    - Preserve the existing SEO value
    """
    if keyword_present(current_title, keyword):
        return None  # Already present

    kw_words = keyword.lower().split()
    title_lower = current_title.lower()

    # Identify which meaningful words from the keyword are missing
    stop_words = {
        "the", "a", "an", "is", "are", "for", "of", "to", "in", "on",
        "and", "or", "with",
    }
    missing_words = [
        w for w in kw_words
        if w not in stop_words and w not in title_lower and len(w) > 2
    ]

    if not missing_words:
        return None  # All meaningful words already present

    # Strategy 1: If keyword contains a distinctive phrase like "pricing change",
    # try to insert it by replacing a similar existing phrase
    new_title = _try_phrase_insertion(current_title, keyword, missing_words)
    if new_title and len(new_title) <= MAX_TITLE_LEN:
        return new_title

    # Strategy 2: Append a short qualifier after the colon or dash
    new_title = _try_append_qualifier(current_title, missing_words)
    if new_title and len(new_title) <= MAX_TITLE_LEN:
        return new_title

    # Strategy 3: Prepend the missing phrase
    new_title = _try_prepend_qualifier(current_title, missing_words)
    if new_title and len(new_title) <= MAX_TITLE_LEN:
        return new_title

    logger.debug(
        "Could not rewrite title under %d chars for keyword '%s'",
        MAX_TITLE_LEN, keyword,
    )
    return None


def _try_phrase_insertion(title: str, keyword: str, missing_words: list[str]) -> str | None:
    """Try inserting the missing keyword phrase into an existing title structure."""
    kw_lower = keyword.lower()
    title_lower = title.lower()
    kw_words = kw_lower.split()

    # Find consecutive missing words in the keyword to build a phrase
    # e.g. from "gohighlevel pricing change march 2026", if "pricing" and
    # "change" are missing, build "Pricing Change" as the insertion phrase
    consecutive_missing = []
    for word in kw_words:
        if word in [w.lower() for w in missing_words]:
            consecutive_missing.append(word)
        else:
            if consecutive_missing:
                break  # stop at the first non-missing word after a run

    if not consecutive_missing:
        return None

    insert_phrase = " ".join(w.title() for w in consecutive_missing)

    # Find the best place to insert: look for an anchor word in the title
    # that appears just before or after the missing phrase in the keyword
    for i, word in enumerate(kw_words):
        if word.lower() in title_lower and word.lower() not in [w.lower() for w in missing_words]:
            # This keyword word IS in the title -- potential anchor
            pattern = re.compile(re.escape(word), re.IGNORECASE)
            match = pattern.search(title)
            if not match:
                continue

            # Try inserting the phrase after this anchor word
            pos = match.end()
            candidate = title[:pos] + " " + insert_phrase + title[pos:]
            candidate = re.sub(r"  +", " ", candidate)
            if len(candidate) <= MAX_TITLE_LEN:
                return candidate

    # No good anchor -- try inserting the phrase into the subtitle
    for sep in [": ", " | ", " -- ", " - "]:
        if sep in title:
            parts = title.split(sep, 1)
            candidate = f"{parts[0]}{sep}{insert_phrase}, {parts[1]}"
            if len(candidate) <= MAX_TITLE_LEN:
                return candidate
            break

    return None


def _try_append_qualifier(title: str, missing_words: list[str]) -> str | None:
    """Try weaving missing words into the existing title after a separator."""
    qualifier = " ".join(w.title() for w in missing_words[:2])

    # If title has a separator, try to blend qualifier into the subtitle
    for sep in [": ", " | ", " -- ", " - "]:
        if sep in title:
            parts = title.split(sep, 1)
            subtitle = parts[1]

            # Option A: prepend qualifier to the subtitle portion
            candidate = f"{parts[0]}{sep}{qualifier}, {subtitle}"
            if len(candidate) <= MAX_TITLE_LEN:
                return candidate

            # Option B: append qualifier to the subtitle with &
            candidate = f"{parts[0]}{sep}{subtitle} & {qualifier}"
            if len(candidate) <= MAX_TITLE_LEN:
                return candidate

            # Option C: shorten subtitle to make room for qualifier
            # Keep the first part of the subtitle plus qualifier
            dangling = {"&", "and", "or", ",", "-", "|", "+"}
            subtitle_words = subtitle.split()
            for n in range(len(subtitle_words), 0, -1):
                short_sub = " ".join(subtitle_words[:n])
                # Don't leave a dangling connector at the end
                if short_sub.rstrip(",;:").split()[-1].lower() in dangling:
                    continue
                short_sub = short_sub.rstrip(",;: ")
                candidate = f"{parts[0]}{sep}{qualifier}, {short_sub}"
                if len(candidate) <= MAX_TITLE_LEN:
                    return candidate

            # Option D: replace subtitle entirely only as last resort
            candidate = f"{parts[0]}{sep}{qualifier}"
            if len(candidate) <= MAX_TITLE_LEN:
                return candidate

    # No separator -- append with a pipe
    candidate = f"{title} | {qualifier}"
    return candidate if len(candidate) <= MAX_TITLE_LEN else None


def _try_prepend_qualifier(title: str, missing_words: list[str]) -> str | None:
    """Try prepending the missing concept to the title."""
    qualifier = " ".join(w.title() for w in missing_words[:2])
    candidate = f"{qualifier}: {title}"
    if len(candidate) <= MAX_TITLE_LEN:
        return candidate

    # Try shorter -- just the first missing word
    if missing_words:
        candidate = f"{missing_words[0].title()}: {title}"
        if len(candidate) <= MAX_TITLE_LEN:
            return candidate

    return None


def rewrite_description(current_desc: str, keyword: str) -> str | None:
    """
    Rewrite a meta description to include the keyword if it's missing.

    Returns the new description, or None if no change needed.
    """
    if keyword_present(current_desc, keyword):
        return None  # Already present

    kw_words = keyword.lower().split()
    desc_lower = current_desc.lower()

    stop_words = {
        "the", "a", "an", "is", "are", "for", "of", "to", "in", "on",
        "and", "or", "with",
    }
    missing_words = [
        w for w in kw_words
        if w not in stop_words and w not in desc_lower and len(w) > 2
    ]

    if not missing_words:
        return None

    missing_phrase = " ".join(missing_words[:3])

    # Strategy 1: Try appending the missing concept at the end
    candidate = current_desc.rstrip(".")
    append = f", plus {missing_phrase}."
    if len(candidate) + len(append) <= MAX_DESC_LEN:
        return candidate + append

    # Strategy 2: Try a shorter append
    append = f" Includes {missing_phrase}."
    if len(candidate) + len(append) <= MAX_DESC_LEN:
        return candidate + append

    # Strategy 3: Trim the original description to make room for append
    trim_to = MAX_DESC_LEN - len(append)
    if trim_to >= 50:
        trimmed = current_desc[:trim_to].rsplit(" ", 1)[0]
        candidate = trimmed.rstrip(".,;:") + "." + append
        if len(candidate) <= MAX_DESC_LEN:
            return candidate

    # Strategy 4: Prepend a natural lead-in
    lead_in = f"Covers {missing_phrase}. "
    available = MAX_DESC_LEN - len(lead_in)
    if available >= 50:
        trimmed = current_desc[:available].rsplit(" ", 1)[0]
        candidate = lead_in + trimmed.rstrip(".,;:") + "."
        if len(candidate) <= MAX_DESC_LEN:
            return candidate

    return None


# ──────────────────────────────────────────────
# Apply changes to HTML
# ──────────────────────────────────────────────

def update_title_in_html(html: str, new_title: str) -> str:
    """Replace the <title> tag content in HTML."""
    return re.sub(
        r"(<title>)(.*?)(</title>)",
        lambda m: m.group(1) + new_title + m.group(3),
        html,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )


def update_description_in_html(html: str, new_desc: str) -> str:
    """Replace the meta description content attribute in HTML."""
    # Escape for HTML attribute
    escaped = new_desc.replace('"', "&quot;").replace("&", "&amp;")
    return re.sub(
        r'(<meta\s+name=["\']description["\']\s+content=["\']).*?(["\'])',
        lambda m: m.group(1) + escaped + m.group(2),
        html,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Optimize page titles & meta descriptions from GSC data"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True,
                      help="Show proposed changes without modifying files (default)")
    mode.add_argument("--apply", action="store_true",
                      help="Actually modify HTML files (backs up first)")
    args = parser.parse_args()

    apply_mode = args.apply

    logger.info("=" * 60)
    logger.info("optimize_meta.py -- %s mode", "APPLY" if apply_mode else "DRY-RUN")
    logger.info("=" * 60)

    # 1. Find and parse the latest dashboard report
    report_path = find_latest_dashboard()
    keywords = parse_striking_distance(report_path)
    opportunities = filter_opportunities(keywords)

    if not opportunities:
        logger.info("No actionable striking-distance opportunities found.")
        return

    # 2. Process each opportunity
    changes = []
    pages_modified = set()

    for opp in opportunities:
        if len(pages_modified) >= MAX_PAGES_PER_RUN:
            logger.info(
                "Reached max pages per run (%d). Stopping.", MAX_PAGES_PER_RUN
            )
            break

        keyword = opp["query"]
        page_url = opp["page"]

        filepath = resolve_page_path(page_url)
        if filepath is None:
            continue

        html = filepath.read_text(encoding="utf-8")
        current_title = extract_title(html)
        current_desc = extract_meta_description(html)

        logger.info("")
        logger.info("Keyword: '%s' (pos %.1f, %d impr, page %s)",
                     keyword, opp["position"], opp["impressions"], page_url)

        title_change = None
        desc_change = None

        # Check title
        if current_title:
            if keyword_present(current_title, keyword):
                logger.info("  Title: keyword already present -- OK")
            else:
                new_title = rewrite_title(current_title, keyword)
                if new_title:
                    title_change = {
                        "old": current_title,
                        "new": new_title,
                    }
                    logger.info("  Title CHANGE:")
                    logger.info("    OLD: %s (%d chars)", current_title, len(current_title))
                    logger.info("    NEW: %s (%d chars)", new_title, len(new_title))
                else:
                    logger.info("  Title: could not fit keyword naturally")
        else:
            logger.warning("  No <title> found in %s", filepath.name)

        # Check description
        if current_desc:
            if keyword_present(current_desc, keyword):
                logger.info("  Description: keyword already present -- OK")
            else:
                new_desc = rewrite_description(current_desc, keyword)
                if new_desc:
                    desc_change = {
                        "old": current_desc,
                        "new": new_desc,
                    }
                    logger.info("  Desc CHANGE:")
                    logger.info("    OLD: %s (%d chars)", current_desc, len(current_desc))
                    logger.info("    NEW: %s (%d chars)", new_desc, len(new_desc))
                else:
                    logger.info("  Description: could not fit keyword naturally")
        else:
            logger.warning("  No <meta description> found in %s", filepath.name)

        if title_change or desc_change:
            change_record = {
                "keyword": keyword,
                "page": page_url,
                "filepath": str(filepath),
                "position": opp["position"],
                "impressions": opp["impressions"],
                "title_change": title_change,
                "desc_change": desc_change,
            }
            changes.append(change_record)
            pages_modified.add(str(filepath))

            # Apply if not dry-run
            if apply_mode:
                backup_path = backup_file(filepath, label="meta-optimize")
                logger.info("  Backed up to: %s", backup_path.name)

                modified_html = html
                if title_change:
                    modified_html = update_title_in_html(
                        modified_html, title_change["new"]
                    )
                if desc_change:
                    modified_html = update_description_in_html(
                        modified_html, desc_change["new"]
                    )

                filepath.write_text(modified_html, encoding="utf-8")
                logger.info("  APPLIED changes to %s", filepath.name)

    # 3. Generate report
    _generate_report(changes, apply_mode)

    logger.info("")
    logger.info("Done. %d change(s) across %d page(s).",
                len(changes), len(pages_modified))
    if not apply_mode and changes:
        logger.info("Run with --apply to modify files.")


def _generate_report(changes: list[dict], applied: bool) -> None:
    """Save a markdown report of proposed/applied changes."""
    mode_label = "Applied" if applied else "Proposed (dry-run)"
    lines = [
        f"# Meta Optimization Report -- {today()}",
        f"Generated: {now()}",
        f"Mode: {mode_label}",
        "",
    ]

    if not changes:
        lines.append("No changes needed -- all striking-distance keywords "
                      "already present in titles/descriptions.")
    else:
        lines.append(f"## {mode_label} Changes ({len(changes)})")
        lines.append("")

        for i, ch in enumerate(changes, 1):
            lines.append(f"### {i}. Keyword: `{ch['keyword']}`")
            lines.append(f"- **Page:** {ch['page']}")
            lines.append(f"- **Position:** {ch['position']}")
            lines.append(f"- **Impressions:** {ch['impressions']}")
            lines.append("")

            if ch["title_change"]:
                tc = ch["title_change"]
                lines.append(f"**Title Change:**")
                lines.append(f"- Old ({len(tc['old'])} chars): `{tc['old']}`")
                lines.append(f"- New ({len(tc['new'])} chars): `{tc['new']}`")
                lines.append("")

            if ch["desc_change"]:
                dc = ch["desc_change"]
                lines.append(f"**Description Change:**")
                lines.append(f"- Old ({len(dc['old'])} chars): `{dc['old']}`")
                lines.append(f"- New ({len(dc['new'])} chars): `{dc['new']}`")
                lines.append("")

    lines.append("---")
    lines.append(f"_Report generated by optimize_meta.py | {now()}_")

    report_text = "\n".join(lines)
    report_file = save_report(f"meta-optimize-{today()}.md", report_text)
    logger.info("Report saved: %s", report_file)


if __name__ == "__main__":
    main()
