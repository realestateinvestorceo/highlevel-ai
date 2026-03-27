#!/usr/bin/env python3
"""
Daily SEO Summary Generator for highlevel.ai

Reads the latest report from each automation task and generates
a unified daily summary in reports/daily-summary-YYYY-MM-DD.md.

Usage:
    python scripts/seo/daily_summary.py
"""

import re
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import *

# ──────────────────────────────────────────────
# Report patterns to scan for
# ──────────────────────────────────────────────
REPORT_PATTERNS = {
    "freshness": "freshness-audit-*.md",
    "schema": "schema-audit-*.md",
    "dashboard": "seo-dashboard-*.md",
    "llm_visibility": "llm-visibility-*.md",
    "internal_links": "internal-links-report.md",
}

SECTION_TITLES = {
    "freshness": "Freshness Monitor",
    "schema": "Schema Audit",
    "dashboard": "SEO Dashboard",
    "llm_visibility": "AI Visibility",
    "internal_links": "Internal Linking",
}


# ──────────────────────────────────────────────
# Report discovery
# ──────────────────────────────────────────────

def find_latest_report(pattern: str) -> Path | None:
    """Find the most recent report matching the given glob pattern."""
    matches = sorted(REPORTS_DIR.glob(pattern))
    if not matches:
        return None
    # For date-stamped files, the last sorted entry is the most recent
    return matches[-1]


def find_all_latest_reports() -> dict[str, Path | None]:
    """Find the latest report file for each task type."""
    return {
        key: find_latest_report(pattern)
        for key, pattern in REPORT_PATTERNS.items()
    }


# ──────────────────────────────────────────────
# Content extraction
# ──────────────────────────────────────────────

def read_report(filepath: Path | None) -> str:
    """Read a report file and return its content, or empty string if missing."""
    if filepath is None or not filepath.exists():
        return ""
    return filepath.read_text(encoding="utf-8")


def extract_first_section(content: str) -> str:
    """
    Extract the first section after the title (first H1).
    Returns everything between the first H2 (or after H1) and the second H2.
    If no H2 exists, returns the first ~20 meaningful lines after the title.
    """
    if not content.strip():
        return ""

    lines = content.split("\n")

    # Skip title (first H1) and blank lines after it
    start_idx = 0
    for i, line in enumerate(lines):
        if line.startswith("# ") and not line.startswith("## "):
            start_idx = i + 1
            break

    # Skip blank lines after title
    while start_idx < len(lines) and not lines[start_idx].strip():
        start_idx += 1

    # Collect lines until the next H2 header (or up to 30 lines)
    section_lines = []
    found_content = False
    for i in range(start_idx, min(start_idx + 30, len(lines))):
        line = lines[i]
        # Stop at the second H2 if we already found content
        if line.startswith("## ") and found_content:
            break
        section_lines.append(line)
        if line.strip():
            found_content = True

    return "\n".join(section_lines).strip()


def extract_table(content: str) -> list[list[str]]:
    """Extract the first markdown table from content as a list of rows."""
    lines = content.split("\n")
    table_rows = []
    in_table = False

    for line in lines:
        stripped = line.strip()
        if "|" in stripped and not stripped.startswith("|-"):
            # Check if it's a table row (has at least 2 pipe chars)
            if stripped.count("|") >= 2:
                # Skip separator rows like |---|---|
                cells = [c.strip() for c in stripped.split("|")]
                cells = [c for c in cells if c]  # Remove empty from leading/trailing |
                if cells and not all(re.match(r'^[-:]+$', c) for c in cells):
                    table_rows.append(cells)
                    in_table = True
        elif in_table:
            # End of table
            break

    return table_rows


def extract_bullet_items(content: str, max_items: int = 10) -> list[str]:
    """Extract bullet points and numbered list items from content."""
    items = []
    for line in content.split("\n"):
        stripped = line.strip()
        # Match "- item" or "1. item" or "* item"
        match = re.match(r'^(?:[-*]|\d+\.)\s+(.+)$', stripped)
        if match:
            items.append(match.group(1))
            if len(items) >= max_items:
                break
    return items


def extract_number_after_keyword(content: str, keywords: list[str]) -> str | None:
    """
    Look for a number following any of the given keywords in the content.
    Returns the number as a string, or None if not found.
    """
    for keyword in keywords:
        # Match keyword followed by optional punctuation/whitespace and a number
        pattern = rf'{re.escape(keyword)}[:\s]*(\d+(?:\.\d+)?%?)'
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


# ──────────────────────────────────────────────
# Stats gathering
# ──────────────────────────────────────────────

def gather_quick_stats(reports: dict[str, Path | None]) -> dict[str, str]:
    """
    Pull key numbers from available reports for the Quick Stats table.
    Returns a dict of metric name -> value (as string).
    """
    stats = {}

    # Count pages on site
    try:
        html_files = get_all_html_files()
        stats["Pages on site"] = str(len(html_files))
    except Exception:
        stats["Pages on site"] = "?"

    # Freshness report
    freshness_content = read_report(reports.get("freshness"))
    if freshness_content:
        val = extract_number_after_keyword(freshness_content,
            ["warning", "warnings", "stale", "outdated"])
        stats["Freshness warnings"] = val or "0"

        val = extract_number_after_keyword(freshness_content,
            ["broken link", "broken links", "dead link", "dead links"])
        stats["Broken links"] = val or "0"
    else:
        stats["Freshness warnings"] = "N/A"
        stats["Broken links"] = "N/A"

    # Schema report
    schema_content = read_report(reports.get("schema"))
    if schema_content:
        val = extract_number_after_keyword(schema_content,
            ["issue", "issues", "error", "errors", "warning", "warnings"])
        stats["Schema issues"] = val or "0"
    else:
        stats["Schema issues"] = "N/A"

    # LLM Visibility report
    llm_content = read_report(reports.get("llm_visibility"))
    if llm_content:
        val = extract_number_after_keyword(llm_content,
            ["citation rate", "citation", "mentioned", "visibility"])
        stats["AI citation rate"] = val or "N/A"
    else:
        stats["AI citation rate"] = "N/A"

    # Internal links report
    links_content = read_report(reports.get("internal_links"))
    if links_content:
        val = extract_number_after_keyword(links_content,
            ["injected", "added", "suggested", "new link", "new links",
             "opportunities", "opportunity"])
        stats["Internal links suggested"] = val or "0"
    else:
        stats["Internal links suggested"] = "N/A"

    return stats


# ──────────────────────────────────────────────
# Key issues extraction
# ──────────────────────────────────────────────

def gather_key_issues(reports: dict[str, Path | None], max_issues: int = 5) -> list[str]:
    """
    Pull the most important issues from all available reports.
    Prioritizes errors > warnings > info items.
    """
    issues = []

    for key, filepath in reports.items():
        content = read_report(filepath)
        if not content:
            continue

        section_name = SECTION_TITLES.get(key, key)

        # Look for lines with strong negative signals
        for line in content.split("\n"):
            stripped = line.strip()
            lower = stripped.lower()

            # Prioritize lines containing error/warning/critical/broken
            if any(kw in lower for kw in ["error", "critical", "broken", "fail", "missing"]):
                # Clean up the line (remove markdown formatting like -, *, emoji)
                clean = re.sub(r'^[-*]\s*', '', stripped)
                clean = re.sub(r'^#+\s*', '', clean)
                clean = re.sub(r'[^\x00-\x7F]+', '', clean).strip()  # Remove emoji
                if clean and len(clean) > 10:
                    issues.append(f"[{section_name}] {clean}")

            if len(issues) >= max_issues * 3:  # Gather extra, trim later
                break

    # Deduplicate and limit
    seen = set()
    unique = []
    for issue in issues:
        normalized = issue.lower()
        if normalized not in seen:
            seen.add(normalized)
            unique.append(issue)

    return unique[:max_issues]


# ──────────────────────────────────────────────
# Recommended actions
# ──────────────────────────────────────────────

def generate_recommendations(
    reports: dict[str, Path | None],
    stats: dict[str, str],
    max_actions: int = 5,
) -> list[str]:
    """Generate actionable recommendations based on report data."""
    actions = []

    # Check freshness
    freshness_warnings = stats.get("Freshness warnings", "0")
    if freshness_warnings not in ("0", "N/A"):
        actions.append(
            f"Review {freshness_warnings} pages with stale content "
            f"(see freshness report for details)"
        )

    # Check broken links
    broken = stats.get("Broken links", "0")
    if broken not in ("0", "N/A"):
        actions.append(f"Fix {broken} broken links found during freshness audit")

    # Check schema issues
    schema_issues = stats.get("Schema issues", "0")
    if schema_issues not in ("0", "N/A"):
        actions.append(
            f"Address {schema_issues} schema validation issues "
            f"(run schema_audit.py --fix for auto-repair)"
        )

    # Check internal links
    links_val = stats.get("Internal links suggested", "0")
    if links_val not in ("0", "N/A"):
        actions.append(
            f"Review {links_val} internal linking opportunities "
            f"(run auto_internal_links.js --apply after review)"
        )

    # Check AI visibility
    citation = stats.get("AI citation rate", "N/A")
    if citation != "N/A":
        try:
            pct = float(citation.replace("%", ""))
            if pct < 50:
                actions.append(
                    "Improve AI visibility — citation rate is below 50%. "
                    "Consider adding more structured data and FAQ sections."
                )
        except (ValueError, AttributeError):
            pass

    # Always suggest sitemap check
    if not actions:
        actions.append("All checks passed. Consider running --full mode for deeper analysis.")

    return actions[:max_actions]


# ──────────────────────────────────────────────
# Summary generation
# ──────────────────────────────────────────────

def generate_summary() -> str:
    """Generate the complete daily summary markdown."""
    ensure_dirs()

    date_str = today()
    timestamp = now()

    reports = find_all_latest_reports()
    stats = gather_quick_stats(reports)
    key_issues = gather_key_issues(reports)
    recommendations = generate_recommendations(reports, stats)

    # Track which reports were found
    found = {k: v for k, v in reports.items() if v is not None}
    missing = {k for k, v in reports.items() if v is None}

    # Build markdown
    lines = []
    lines.append(f"# Daily SEO Summary — {date_str}")
    lines.append(f"Generated: {timestamp}")
    lines.append("")

    # Quick Stats table
    lines.append("## Quick Stats")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    for metric, value in stats.items():
        lines.append(f"| {metric} | {value} |")
    lines.append("")

    # Key Issues
    lines.append("## Key Issues")
    if key_issues:
        for i, issue in enumerate(key_issues, 1):
            lines.append(f"{i}. {issue}")
    else:
        lines.append("No critical issues detected across all reports.")
    lines.append("")

    # Individual report sections
    for key in ["freshness", "schema", "dashboard", "llm_visibility", "internal_links"]:
        title = SECTION_TITLES[key]
        lines.append(f"## {title}")

        filepath = reports.get(key)
        if filepath is None or not filepath.exists():
            lines.append(f"*No report found. Run the {title.lower()} task to generate one.*")
            lines.append("")
            continue

        content = read_report(filepath)
        section = extract_first_section(content)

        if section:
            lines.append(f"*Source: {filepath.name}*")
            lines.append("")
            lines.append(section)
        else:
            lines.append(f"*Report exists ({filepath.name}) but no summary section found.*")
        lines.append("")

    # Recommended Actions
    lines.append("## Recommended Actions")
    for i, action in enumerate(recommendations, 1):
        lines.append(f"{i}. {action}")
    lines.append("")

    # Footer
    if missing:
        missing_names = ", ".join(SECTION_TITLES[k] for k in missing)
        lines.append(f"---")
        lines.append(f"*Missing reports: {missing_names}. "
                      f"Run `./scripts/seo/run_all.sh --weekly` for full coverage.*")
        lines.append("")

    return "\n".join(lines)


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    logger = setup_logging("daily_summary")

    logger.info("Generating daily SEO summary...")
    summary = generate_summary()

    filename = f"daily-summary-{today()}.md"
    filepath = save_report(filename, summary)

    logger.info(f"Summary saved to {filepath}")

    # Also print the summary to stdout for the run_all.sh runner
    print(summary)


if __name__ == "__main__":
    main()
