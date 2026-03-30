#!/usr/bin/env python3
"""
generate_dashboard_data.py

Reads the latest report files from scripts/seo/reports/ and compiles them
into a single site/dashboard-data.json file that a dashboard HTML page can fetch.

Usage:
    python3 scripts/seo/generate_dashboard_data.py
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Ensure the script's own directory is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import REPORTS_DIR, SITE_DIR, today, now, setup_logging, add_existing_scripts_to_path

logger = setup_logging("generate_dashboard_data")

# ──────────────────────────────────────────────
# Report discovery
# ──────────────────────────────────────────────

REPORT_PATTERNS = {
    "freshness": "freshness-audit-*.md",
    "schema": "schema-audit-*.md",
    "seo_dashboard": "seo-dashboard-*.md",
    "llm_visibility": "llm-visibility-*.md",
    "daily_summary": "daily-summary-*.md",
    "internal_links": "internal-links-report.md",
}


def find_latest_report(pattern: str) -> Path | None:
    """Find the most recent report matching a glob pattern.

    Files are sorted by name descending so that date-stamped filenames
    (e.g. freshness-audit-2026-03-27.md) naturally resolve to the latest.
    """
    matches = sorted(REPORTS_DIR.glob(pattern), reverse=True)
    if matches:
        return matches[0]
    return None


def extract_date_from_filename(path: Path) -> str | None:
    """Pull a YYYY-MM-DD date from a report filename, if present."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
    return m.group(1) if m else None


def read_report(path: Path | None) -> str:
    """Safely read report text; returns empty string if path is None or missing."""
    if path is None or not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return ""


# ──────────────────────────────────────────────
# Parsers
# ──────────────────────────────────────────────

def parse_freshness(text: str) -> dict:
    """Extract key data from a freshness-audit report."""
    result = {
        "total_issues": 0,
        "critical": 0,
        "warnings": 0,
        "pages_checked": 0,
        "by_type": {},
        "items": [],
    }
    if not text:
        return result

    # Summary numbers: "**Pages checked:** 47", "- **Total issues:** 628", etc.
    # Use a liberal pattern that strips markdown bold and finds the trailing number.
    for key, field in [
        ("pages checked", "pages_checked"),
        ("total issues", "total_issues"),
        ("critical", "critical"),
        ("warnings", "warnings"),
    ]:
        m = re.search(rf"{key}\D*?(\d+)", text, re.IGNORECASE)
        if m:
            result[field] = int(m.group(1))

    # Issue type table: "| outdated_year | 5 |"
    for m in re.finditer(r"\|\s*(\w+)\s*\|\s*(\d+)\s*\|", text):
        name = m.group(1).strip()
        if name.lower() in ("issue", "type", "count", "---", "metric"):
            continue
        result["by_type"][name] = int(m.group(2))

    # Critical issues list items (first 20)
    # Format: - **Pricing mismatch** in `file.html`: found `$297` for **brand** (known: ...)
    # Or:     - **Outdated year** in `file.html`: found `2025`, expected `2026`
    items = []
    issue_pattern = re.compile(
        r"^- \*\*(.+?)\*\* in `(.+?)`.*?found `(.+?)`"
        r"(?:.*?expected `(.+?)`)?"
        r"(?:.*?for \*\*(.+?)\*\*)?",
        re.MULTILINE,
    )
    for m in issue_pattern.finditer(text):
        issue_type = m.group(1).strip().lower().replace(" ", "_")
        item = {
            "file": m.group(2).strip(),
            "type": issue_type,
            "found": m.group(3).strip(),
            "expected": m.group(4).strip() if m.group(4) else None,
        }
        items.append(item)
        if len(items) >= 20:
            break

    result["items"] = items
    return result


def parse_schema(text: str) -> dict:
    """Extract key data from a schema-audit report."""
    result = {
        "pages_scanned": 0,
        "total_schemas": 0,
        "issues": 0,
        "items": [],
    }
    if not text:
        return result

    # Summary table: "| Total pages scanned | 47 |"
    for m in re.finditer(r"\|\s*(.+?)\s*\|\s*(\d+)\s*\|", text):
        label = m.group(1).strip().lower()
        value = int(m.group(2))
        if "pages scanned" in label or "total pages" in label:
            result["pages_scanned"] = value
        elif "schemas found" in label or "total schemas" in label:
            result["total_schemas"] = value
        elif "error" in label:
            result["issues"] += value
        elif "warning" in label:
            result["issues"] += value

    # Also try inline format on single lines: "Pages scanned: N", "Schemas found: N", "Issues: N"
    for line in text.splitlines():
        ll = line.lower()
        if "pages scanned" in ll or "total pages" in ll:
            m = re.search(r"(\d+)", line[line.lower().index("page"):])
            if m and result["pages_scanned"] == 0:
                result["pages_scanned"] = int(m.group(1))
        elif "schemas found" in ll or "total schemas" in ll:
            m = re.search(r"(\d+)", line[line.lower().index("schema"):])
            if m and result["total_schemas"] == 0:
                result["total_schemas"] = int(m.group(1))
        elif re.search(r"\bissues\b", ll) and "fix" not in ll and "no issues" not in ll:
            m = re.search(r"(\d+)", line)
            if m and result["issues"] == 0:
                result["issues"] = int(m.group(1))

    # Collect any issue lines (e.g. "- **`file.html`**: ...")
    # from the "Issues & Fix Suggestions" section
    in_issues_section = False
    for line in text.splitlines():
        if re.search(r"issues.*fix|fix.*suggestions", line, re.IGNORECASE):
            in_issues_section = True
            continue
        if in_issues_section:
            if line.startswith("## ") or line.startswith("---"):
                in_issues_section = False
                continue
            m = re.match(r"^- (.+)", line.strip())
            if m and "no issues" not in m.group(1).lower():
                result["items"].append(m.group(1).strip())

    return result


def parse_seo_dashboard(text: str) -> dict:
    """Extract striking-distance keywords and top pages from an SEO dashboard report."""
    result = {
        "striking_distance": [],
        "top_pages": [],
        "traffic_summary": {},
    }
    if not text:
        return result

    # Striking Distance Keywords section
    # Expect a markdown table with columns like: keyword | position | impressions | clicks
    sd_section = _extract_section(text, r"striking.?distance", next_heading_level=2)
    if sd_section:
        for m in re.finditer(
            r"\|\s*(.+?)\s*\|\s*([\d.]+)\s*\|\s*([\d,]+)\s*\|\s*([\d,]+)\s*\|",
            sd_section,
        ):
            kw = m.group(1).strip()
            if kw.startswith("-") or "keyword" in kw.lower():
                continue
            result["striking_distance"].append({
                "keyword": kw,
                "position": float(m.group(2)),
                "impressions": int(m.group(3).replace(",", "")),
                "clicks": int(m.group(4).replace(",", "")),
            })

    # Top Pages section
    tp_section = _extract_section(text, r"top.?pages", next_heading_level=2)
    if tp_section:
        for m in re.finditer(
            r"\|\s*(.+?)\s*\|\s*([\d,]+)\s*\|\s*([\d,]+)\s*\|", tp_section
        ):
            page = m.group(1).strip()
            if page.startswith("-") or "page" in page.lower():
                continue
            result["top_pages"].append({
                "page": page,
                "clicks": int(m.group(2).replace(",", "")),
                "impressions": int(m.group(3).replace(",", "")),
            })

    # Traffic summary — look for key-value pairs
    for key in ("total clicks", "total impressions", "average position", "average ctr"):
        m = re.search(
            rf"\*?\*?{key}\*?\*?[:\s]+([\d,.%]+)", text, re.IGNORECASE
        )
        if m:
            result["traffic_summary"][key.replace(" ", "_")] = m.group(1).strip()

    return result


def parse_llm_visibility(text: str) -> dict:
    """Extract citation rates from an LLM visibility report."""
    result = {
        "citation_rate": None,
        "by_platform": {},
    }
    if not text:
        return result

    # Overall citation rate: "Overall citation rate: 45%"  or "Citation rate: 45%"
    m = re.search(
        r"(?:overall\s+)?citation\s+rate[:\s]+([\d.]+)%", text, re.IGNORECASE
    )
    if m:
        result["citation_rate"] = float(m.group(1))

    # Per-platform: "| ChatGPT | 60% |" or "- ChatGPT: 60%"
    for m in re.finditer(r"\|\s*(\w[\w\s]*?)\s*\|\s*([\d.]+)%\s*\|", text):
        name = m.group(1).strip()
        if name.lower() in ("platform", "---", "metric"):
            continue
        result["by_platform"][name] = float(m.group(2))

    # Also try list format: "- ChatGPT: 60%"
    for m in re.finditer(r"^[-*]\s*(\w[\w\s]*?)\s*:\s*([\d.]+)%", text, re.MULTILINE):
        name = m.group(1).strip()
        if name.lower() not in result["by_platform"]:
            result["by_platform"][name] = float(m.group(2))

    return result


def parse_daily_summary(text: str) -> dict:
    """Extract priority actions and quick stats from a daily summary report."""
    result = {
        "priority_actions": [],
        "quick_stats": {},
    }
    if not text:
        return result

    # Priority / Recommended Actions section
    actions_section = _extract_section(
        text, r"(priority|recommended)\s+actions?", next_heading_level=2
    )
    if actions_section:
        for m in re.finditer(r"^[-*\d.]+\s+(.+)", actions_section, re.MULTILINE):
            action = m.group(1).strip()
            if action:
                result["priority_actions"].append(action)

    # Quick Stats table: "| Metric | Value |"
    stats_section = _extract_section(text, r"quick.?stats", next_heading_level=2)
    if stats_section:
        for m in re.finditer(r"\|\s*(.+?)\s*\|\s*(.+?)\s*\|", stats_section):
            key = m.group(1).strip()
            val = m.group(2).strip()
            if key.startswith("-") or key.lower() in ("metric", "stat"):
                continue
            result["quick_stats"][key] = val

    return result


def _extract_section(text: str, heading_pattern: str, next_heading_level: int = 2) -> str:
    """Extract the text under the first heading matching a regex pattern,
    up to the next heading of the same or higher level."""
    prefix = "#" * next_heading_level
    pattern = re.compile(
        rf"^{re.escape(prefix)}\s+.*{heading_pattern}.*$",
        re.IGNORECASE | re.MULTILINE,
    )
    m = pattern.search(text)
    if not m:
        return ""
    start = m.end()
    # Find next heading of same or higher level
    next_heading = re.compile(rf"^#{{{1},{next_heading_level}}}\s+", re.MULTILINE)
    n = next_heading.search(text, start)
    end = n.start() if n else len(text)
    return text[start:end]


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate dashboard-data.json from latest SEO report files."
    )
    parser.parse_args()

    logger.info("Scanning for latest reports in %s", REPORTS_DIR)

    # Discover latest report for each type
    report_paths: dict[str, Path | None] = {}
    for key, pattern in REPORT_PATTERNS.items():
        report_paths[key] = find_latest_report(pattern)
        if report_paths[key]:
            logger.info("  Found %s: %s", key, report_paths[key].name)
        else:
            logger.info("  No report found for %s", key)

    # Read report texts
    texts = {key: read_report(path) for key, path in report_paths.items()}

    # Parse each report
    freshness = parse_freshness(texts["freshness"])
    schema = parse_schema(texts["schema"])
    seo = parse_seo_dashboard(texts["seo_dashboard"])
    llm = parse_llm_visibility(texts["llm_visibility"])
    daily = parse_daily_summary(texts["daily_summary"])

    # Build report_dates
    report_dates = {}
    for key in ("freshness", "schema", "seo_dashboard", "llm_visibility"):
        path = report_paths.get(key)
        if path:
            report_dates[key] = extract_date_from_filename(path) or today()
        else:
            report_dates[key] = None

    # Pull live GA4 traffic data
    traffic_data = {
        "sessions": 0, "users": 0, "pageviews": 0,
        "bounce_rate": 0, "avg_duration": 0,
        "sources": [], "top_pages": [], "cta_clicks": 0
    }
    try:
        add_existing_scripts_to_path()
        import ga4_analyze
        from google.analytics.data_v1beta.types import DateRange
        client = ga4_analyze.get_client()
        dr = DateRange(start_date="30daysAgo", end_date="today")

        totals, ga4_daily = ga4_analyze.get_overview(client, dr)
        traffic_data["daily"] = [
            {"date": d["date"], "sessions": d["sessions"], "users": d["users"]}
            for d in ga4_daily
        ]
        traffic_data["sessions"] = totals.get("sessions", 0)
        traffic_data["users"] = totals.get("users", 0)
        traffic_data["pageviews"] = totals.get("pageviews", 0)
        traffic_data["bounce_rate"] = round(totals.get("bounce_rate", 0), 2)
        traffic_data["avg_duration"] = round(totals.get("avg_duration", 0), 1)

        sources = ga4_analyze.get_traffic_sources(client, dr, limit=15)
        traffic_data["sources"] = [
            {"source": s.get("source", "?"), "medium": s.get("medium", "?"), "sessions": int(s.get("sessions", 0))}
            for s in sources
        ]

        cta = ga4_analyze.get_cta_clicks(client, dr)
        traffic_data["cta_clicks"] = sum(int(c.get("count", 0) or c.get("eventCount", 0)) for c in cta)

        logger.info("GA4 data: %d sessions, %d users, %d CTA clicks", traffic_data["sessions"], traffic_data["users"], traffic_data["cta_clicks"])
    except Exception as e:
        logger.warning("Could not fetch GA4 data: %s", e)

    # Pull live GSC keyword data
    gsc_keywords = {"striking_distance": [], "top_pages": []}
    try:
        add_existing_scripts_to_path()
        import gsc_analyze
        from config import GSC_SITE_URL
        service = gsc_analyze.authenticate(GSC_SITE_URL)
        qp, pages, queries, s, e2 = gsc_analyze.fetch_data(service, GSC_SITE_URL, 30)

        # Striking distance keywords (position 5-20, 10+ impressions)
        for row in qp:
            pos = row.get("position", 0)
            imp = row.get("impressions", 0)
            if 5 <= pos <= 20 and imp >= 10:
                gsc_keywords["striking_distance"].append({
                    "keyword": row["keys"][0],
                    "page": row["keys"][1].replace("https://www.highlevel.ai", "") if len(row["keys"]) > 1 else "",
                    "position": round(pos, 1),
                    "impressions": imp,
                    "clicks": row.get("clicks", 0),
                })
        gsc_keywords["striking_distance"].sort(key=lambda x: x["impressions"], reverse=True)
        gsc_keywords["striking_distance"] = gsc_keywords["striking_distance"][:20]

        # Top pages
        for p in pages:
            url = p["keys"][0].replace("https://www.highlevel.ai", "") or "/"
            gsc_keywords["top_pages"].append({
                "page": url,
                "clicks": p["clicks"],
                "impressions": p["impressions"],
                "position": round(p["position"], 1),
            })
        gsc_keywords["top_pages"].sort(key=lambda x: x["clicks"], reverse=True)
        gsc_keywords["top_pages"] = gsc_keywords["top_pages"][:15]

        logger.info("GSC data: %d striking distance keywords, %d pages", len(gsc_keywords["striking_distance"]), len(gsc_keywords["top_pages"]))
    except Exception as ex:
        logger.warning("Could not fetch GSC data: %s", ex)

    # Compile the dashboard data
    dashboard = {
        "generated_at": now(),
        "report_dates": report_dates,
        "stats": {
            "total_pages": freshness["pages_checked"] or schema["pages_scanned"] or 0,
            "freshness_issues": freshness["total_issues"],
            "schema_issues": schema["issues"],
            "striking_distance_keywords": len(gsc_keywords["striking_distance"]) or len(seo["striking_distance"]),
        },
        "priority_actions": daily["priority_actions"],
        "freshness": {
            "total_issues": freshness["total_issues"],
            "critical": freshness["critical"],
            "warnings": freshness["warnings"],
            "by_type": freshness["by_type"],
            "items": freshness["items"],
        },
        "schema": {
            "total_schemas": schema["total_schemas"],
            "issues": schema["issues"],
            "items": schema["items"],
        },
        "keywords": {
            "striking_distance": gsc_keywords["striking_distance"] or seo["striking_distance"],
            "top_pages": gsc_keywords["top_pages"] or seo["top_pages"],
            "traffic_summary": seo["traffic_summary"],
        },
        "llm_visibility": {
            "citation_rate": llm["citation_rate"],
            "by_platform": llm["by_platform"],
        },
        "traffic": traffic_data,
    }

    # Write output
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    output_path = SITE_DIR / "dashboard-data.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(dashboard, f, indent=2, ensure_ascii=False)

    logger.info("Wrote dashboard data to %s", output_path)
    logger.info(
        "Stats: %d pages, %d freshness issues, %d schema issues, %d striking-distance keywords",
        dashboard["stats"]["total_pages"],
        dashboard["stats"]["freshness_issues"],
        dashboard["stats"]["schema_issues"],
        dashboard["stats"]["striking_distance_keywords"],
    )


if __name__ == "__main__":
    main()
