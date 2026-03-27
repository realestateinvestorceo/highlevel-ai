#!/usr/bin/env python3
"""
schema_audit.py - Audit JSON-LD structured data across all HTML pages.

Extracts <script type="application/ld+json"> blocks from every HTML file in the
site directory, validates them against per-type required-field rules, and produces
a Markdown report plus a JSON fix manifest.

Usage:
    python schema_audit.py              # default audit mode
    python schema_audit.py --verbose    # show all schemas, not just issues
    python schema_audit.py --fix        # (Phase 3 stub)
"""

import sys
import re
import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta

# ── shared config ──────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import *  # noqa: F401, F403  (SITE_DIR, REPORTS_DIR, DATA_DIR, etc.)

logger = setup_logging("schema_audit")

# ── constants ──────────────────────────────────────────────────────────────

# Regex for extracting JSON-LD blocks
JSONLD_PATTERN = r'<script\s+type="application/ld\+json">(.*?)</script>'

# Required fields per @type
REQUIRED_FIELDS: dict[str, list[str]] = {
    "WebPage":             ["name", "description", "url"],
    "Article":             ["headline", "author", "publisher", "datePublished",
                            "dateModified", "mainEntityOfPage", "image"],
    "ReviewArticle":       ["headline", "author", "publisher", "datePublished",
                            "dateModified", "mainEntityOfPage", "image"],
    "BlogPosting":         ["headline", "author", "publisher", "datePublished",
                            "dateModified", "mainEntityOfPage", "image"],
    "FAQPage":             ["mainEntity"],
    "Product":             ["name", "description", "category"],
    "SoftwareApplication": ["name", "applicationCategory", "offers"],
    "Organization":        ["name", "url"],
    "Person":              ["name"],
    "BreadcrumbList":      ["itemListElement"],
}

DATE_FRESHNESS_DAYS = 90

# ── severity helpers ───────────────────────────────────────────────────────

SEVERITY_ERROR   = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO    = "info"


def _severity_icon(sev: str) -> str:
    return {"error": "X", "warning": "!", "info": "i"}.get(sev, "?")


# ── extraction ─────────────────────────────────────────────────────────────

def extract_jsonld_blocks(html_content: str, filepath: Path) -> list[dict]:
    """Return a list of dicts, each with 'data' (parsed JSON or None) and 'raw'."""
    blocks = re.findall(JSONLD_PATTERN, html_content, re.DOTALL)
    results = []
    for raw in blocks:
        raw_stripped = raw.strip()
        try:
            data = json.loads(raw_stripped)
        except json.JSONDecodeError as exc:
            logger.debug("Malformed JSON-LD in %s: %s", filepath, exc)
            data = None
        results.append({"data": data, "raw": raw_stripped})
    return results


def _resolve_type(data: dict) -> list[str]:
    """Return a flat list of @type strings (handles single string or list)."""
    t = data.get("@type", "")
    if isinstance(t, list):
        return [str(x) for x in t]
    return [str(t)] if t else []


def _resolve_graph(data: dict | list) -> list[dict]:
    """Flatten @graph arrays and bare objects into a list of typed dicts."""
    if isinstance(data, list):
        items: list[dict] = []
        for entry in data:
            items.extend(_resolve_graph(entry))
        return items
    if isinstance(data, dict):
        if "@graph" in data:
            return _resolve_graph(data["@graph"])
        return [data]
    return []


# ── validation ─────────────────────────────────────────────────────────────

def _check_missing_fields(schema: dict, schema_type: str) -> list[dict]:
    """Check for required fields missing from a schema of the given type."""
    issues: list[dict] = []
    required = REQUIRED_FIELDS.get(schema_type, [])
    for field in required:
        if field not in schema or schema[field] is None or schema[field] == "":
            issues.append({
                "severity": SEVERITY_ERROR,
                "type": schema_type,
                "field": field,
                "message": f"Missing required field '{field}' on {schema_type}",
                "fix_action": "add",
                "fix_reason": f"Missing required field '{field}'",
            })
    return issues


def _check_faqpage_questions(schema: dict) -> list[dict]:
    """Validate FAQPage has at least 1 well-formed Question."""
    issues: list[dict] = []
    main_entity = schema.get("mainEntity")
    if not main_entity:
        return issues  # already caught by missing-field check
    if not isinstance(main_entity, list):
        main_entity = [main_entity]
    if len(main_entity) == 0:
        issues.append({
            "severity": SEVERITY_ERROR,
            "type": "FAQPage",
            "field": "mainEntity",
            "message": "FAQPage mainEntity has no Question items",
            "fix_action": "fix",
            "fix_reason": "mainEntity must contain at least 1 Question",
        })
        return issues
    for idx, q in enumerate(main_entity):
        if not isinstance(q, dict):
            continue
        if not q.get("name"):
            issues.append({
                "severity": SEVERITY_ERROR,
                "type": "FAQPage",
                "field": f"mainEntity[{idx}].name",
                "message": f"Question #{idx} missing 'name'",
                "fix_action": "fix",
                "fix_reason": f"Question #{idx} needs 'name'",
            })
        accepted = q.get("acceptedAnswer", {})
        if isinstance(accepted, dict) and not accepted.get("text"):
            issues.append({
                "severity": SEVERITY_ERROR,
                "type": "FAQPage",
                "field": f"mainEntity[{idx}].acceptedAnswer.text",
                "message": f"Question #{idx} missing 'acceptedAnswer.text'",
                "fix_action": "fix",
                "fix_reason": f"Question #{idx} needs 'acceptedAnswer.text'",
            })
        elif not isinstance(accepted, dict):
            issues.append({
                "severity": SEVERITY_ERROR,
                "type": "FAQPage",
                "field": f"mainEntity[{idx}].acceptedAnswer",
                "message": f"Question #{idx} missing 'acceptedAnswer' object",
                "fix_action": "fix",
                "fix_reason": f"Question #{idx} needs 'acceptedAnswer' with 'text'",
            })
    return issues


def _check_breadcrumb_items(schema: dict) -> list[dict]:
    """Validate BreadcrumbList has at least 1 ListItem."""
    issues: list[dict] = []
    items = schema.get("itemListElement")
    if not items:
        return issues  # caught by missing-field check
    if not isinstance(items, list) or len(items) == 0:
        issues.append({
            "severity": SEVERITY_ERROR,
            "type": "BreadcrumbList",
            "field": "itemListElement",
            "message": "BreadcrumbList has no ListItem entries",
            "fix_action": "fix",
            "fix_reason": "itemListElement must contain at least 1 ListItem",
        })
    return issues


def _check_image_format(schema: dict, schema_type: str) -> list[dict]:
    """Flag image fields that are plain strings instead of ImageObject."""
    issues: list[dict] = []
    image = schema.get("image")
    if image is None:
        return issues  # missing image caught elsewhere
    if isinstance(image, str):
        issues.append({
            "severity": SEVERITY_WARNING,
            "type": schema_type,
            "field": "image",
            "message": f"'image' is a plain string; should be an ImageObject with url/width/height",
            "fix_action": "fix",
            "fix_reason": "Image is string, should be ImageObject",
        })
    elif isinstance(image, dict):
        for prop in ("url", "width", "height"):
            if prop not in image:
                issues.append({
                    "severity": SEVERITY_WARNING,
                    "type": schema_type,
                    "field": f"image.{prop}",
                    "message": f"ImageObject missing '{prop}'",
                    "fix_action": "fix",
                    "fix_reason": f"ImageObject needs '{prop}'",
                })
    return issues


def _check_date_freshness(schema: dict, schema_type: str) -> list[dict]:
    """Warn if dateModified is more than DATE_FRESHNESS_DAYS old."""
    issues: list[dict] = []
    dm = schema.get("dateModified")
    if not dm or not isinstance(dm, str):
        return issues
    try:
        # Handle ISO formats: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS...
        date_str = dm[:10]
        mod_date = datetime.strptime(date_str, "%Y-%m-%d")
        if datetime.now() - mod_date > timedelta(days=DATE_FRESHNESS_DAYS):
            issues.append({
                "severity": SEVERITY_WARNING,
                "type": schema_type,
                "field": "dateModified",
                "message": f"dateModified '{dm}' is more than {DATE_FRESHNESS_DAYS} days old",
                "fix_action": "fix",
                "fix_reason": f"dateModified older than {DATE_FRESHNESS_DAYS} days",
            })
    except ValueError:
        issues.append({
            "severity": SEVERITY_WARNING,
            "type": schema_type,
            "field": "dateModified",
            "message": f"Could not parse dateModified value: '{dm}'",
            "fix_action": "fix",
            "fix_reason": "dateModified has unparseable format",
        })
    return issues


def audit_schema(schema: dict, schema_type: str) -> list[dict]:
    """Run all checks for a single schema object. Returns list of issue dicts."""
    issues: list[dict] = []

    # Required fields
    issues.extend(_check_missing_fields(schema, schema_type))

    # Type-specific deep checks
    if schema_type == "FAQPage":
        issues.extend(_check_faqpage_questions(schema))
    if schema_type == "BreadcrumbList":
        issues.extend(_check_breadcrumb_items(schema))

    # Image format (for types that use image)
    if schema_type in ("Article", "ReviewArticle", "BlogPosting"):
        issues.extend(_check_image_format(schema, schema_type))

    # Date freshness
    if schema_type in ("Article", "ReviewArticle", "BlogPosting"):
        issues.extend(_check_date_freshness(schema, schema_type))

    return issues


def check_duplicate_types(types_on_page: list[str]) -> list[dict]:
    """Flag duplicate @type values on the same page (except Question)."""
    issues: list[dict] = []
    seen: dict[str, int] = {}
    for t in types_on_page:
        if t == "Question":
            continue
        seen[t] = seen.get(t, 0) + 1
    for t, count in seen.items():
        if count > 1:
            issues.append({
                "severity": SEVERITY_WARNING,
                "type": t,
                "field": "@type",
                "message": f"Duplicate @type '{t}' appears {count} times on this page",
                "fix_action": "fix",
                "fix_reason": f"Duplicate {t} schema ({count} instances)",
            })
    return issues


# ── page-level audit ───────────────────────────────────────────────────────

def audit_page(filepath: Path) -> dict:
    """
    Audit a single HTML file. Returns:
        {
            "filepath": str,
            "schemas_found": [{"type": ..., "data": ...}, ...],
            "issues": [issue_dict, ...],
            "invalid_json_count": int,
        }
    """
    result = {
        "filepath": str(filepath),
        "rel_path": str(filepath.relative_to(SITE_DIR)) if filepath.is_relative_to(SITE_DIR) else str(filepath),
        "schemas_found": [],
        "issues": [],
        "invalid_json_count": 0,
    }

    try:
        html_content = filepath.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Could not read %s: %s", filepath, exc)
        return result

    blocks = extract_jsonld_blocks(html_content, filepath)

    all_types_on_page: list[str] = []

    for block in blocks:
        if block["data"] is None:
            result["invalid_json_count"] += 1
            result["issues"].append({
                "severity": SEVERITY_ERROR,
                "type": "JSON-LD",
                "field": "N/A",
                "message": "Invalid JSON in <script type=\"application/ld+json\"> block",
                "fix_action": "fix",
                "fix_reason": "Malformed JSON-LD block",
            })
            continue

        items = _resolve_graph(block["data"])
        for item in items:
            types = _resolve_type(item)
            for t in types:
                all_types_on_page.append(t)
                result["schemas_found"].append({"type": t, "data": item})
                issues = audit_schema(item, t)
                result["issues"].extend(issues)

    # Duplicate type check
    result["issues"].extend(check_duplicate_types(all_types_on_page))

    return result


# ── report generation ──────────────────────────────────────────────────────

def _suggest_schemas(rel_path: str) -> list[str]:
    """Heuristic: suggest which schema types a page should probably have."""
    suggestions = ["WebPage"]  # every page should have WebPage

    if "blog/" in rel_path:
        suggestions.append("BlogPosting")
        suggestions.append("BreadcrumbList")
    elif "review" in rel_path.lower():
        suggestions.append("ReviewArticle")
        suggestions.append("BreadcrumbList")
    elif rel_path in ("index.html",):
        suggestions.append("Organization")
        suggestions.append("SoftwareApplication")
    elif rel_path in ("about.html",):
        suggestions.append("Person")
    elif "faq" in rel_path.lower():
        suggestions.append("FAQPage")
    elif "pricing" in rel_path.lower():
        suggestions.append("SoftwareApplication")
    elif "vs-" in rel_path or "alternative" in rel_path.lower():
        suggestions.append("Article")
        suggestions.append("BreadcrumbList")
    elif "highlevel-for-" in rel_path or "highlevel-plus-" in rel_path:
        suggestions.append("Article")
        suggestions.append("BreadcrumbList")
    elif "gohighlevel-" in rel_path:
        suggestions.append("Article")
        suggestions.append("BreadcrumbList")
    elif "limitations" in rel_path or "limits" in rel_path:
        suggestions.append("Article")
        suggestions.append("BreadcrumbList")

    return suggestions


def generate_report(page_results: list[dict]) -> str:
    """Build the Markdown audit report."""
    date_str = today()
    total_pages = len(page_results)
    total_schemas = sum(len(p["schemas_found"]) for p in page_results)

    errors = 0
    warnings = 0
    infos = 0
    for p in page_results:
        for issue in p["issues"]:
            sev = issue.get("severity", "")
            if sev == SEVERITY_ERROR:
                errors += 1
            elif sev == SEVERITY_WARNING:
                warnings += 1
            elif sev == SEVERITY_INFO:
                infos += 1

    lines: list[str] = []
    lines.append(f"# Schema Audit Report - {date_str}")
    lines.append("")
    lines.append(f"Generated: {now()}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"| Metric | Count |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total pages scanned | {total_pages} |")
    lines.append(f"| Total schemas found | {total_schemas} |")
    lines.append(f"| Errors | {errors} |")
    lines.append(f"| Warnings | {warnings} |")
    lines.append(f"| Info | {infos} |")
    lines.append("")

    # ── per-page breakdown ────────────────────────────────────────────
    lines.append("## Per-Page Breakdown")
    lines.append("")
    for p in page_results:
        rel = p["rel_path"]
        types_found = [s["type"] for s in p["schemas_found"]]
        issue_count = len(p["issues"])
        status = "PASS" if issue_count == 0 else f"{issue_count} issue(s)"
        types_str = ", ".join(types_found) if types_found else "_none_"
        lines.append(f"### `{rel}` - {status}")
        lines.append("")
        lines.append(f"**Schemas found:** {types_str}")
        if p["invalid_json_count"]:
            lines.append(f"**Invalid JSON-LD blocks:** {p['invalid_json_count']}")
        lines.append("")
        if p["issues"]:
            lines.append("| Severity | Type | Field | Issue |")
            lines.append("|----------|------|-------|-------|")
            for issue in p["issues"]:
                sev = issue["severity"].upper()
                lines.append(
                    f"| {sev} | {issue['type']} | `{issue['field']}` | {issue['message']} |"
                )
            lines.append("")

    # ── specific issues with fix suggestions ──────────────────────────
    lines.append("## Issues & Fix Suggestions")
    lines.append("")
    issue_num = 0
    for p in page_results:
        for issue in p["issues"]:
            issue_num += 1
            sev = issue["severity"].upper()
            lines.append(f"**{issue_num}. [{sev}] `{p['rel_path']}` - {issue['type']}**")
            lines.append(f"- Field: `{issue['field']}`")
            lines.append(f"- Problem: {issue['message']}")
            lines.append(f"- Suggested fix: {issue.get('fix_reason', 'Review and correct')}")
            lines.append("")

    if issue_num == 0:
        lines.append("No issues found. All schemas look good!")
        lines.append("")

    # ── missing schemas section ───────────────────────────────────────
    lines.append("## Missing Schemas")
    lines.append("")
    lines.append("Pages that should likely have certain schemas but don't:")
    lines.append("")
    missing_found = False
    for p in page_results:
        rel = p["rel_path"]
        types_found = {s["type"] for s in p["schemas_found"]}
        suggested = _suggest_schemas(rel)
        missing = [s for s in suggested if s not in types_found]
        if missing:
            missing_found = True
            lines.append(f"- **`{rel}`**: missing {', '.join(missing)}")
    if not missing_found:
        lines.append("_All pages have their expected schema types._")
    lines.append("")

    lines.append("---")
    lines.append(f"_Report generated by schema_audit.py on {now()}_")
    return "\n".join(lines)


def generate_fix_data(page_results: list[dict]) -> dict:
    """Build the schema_fixes.json mapping."""
    fixes: dict[str, list[dict]] = {}

    for p in page_results:
        rel = p["rel_path"]
        page_fixes: list[dict] = []

        for issue in p["issues"]:
            fix_entry: dict[str, str] = {
                "action": issue.get("fix_action", "fix"),
                "type": issue.get("type", "unknown"),
                "reason": issue.get("fix_reason", issue.get("message", "")),
            }
            if issue.get("field") and issue["field"] != "N/A":
                fix_entry["field"] = issue["field"]
            page_fixes.append(fix_entry)

        # Also add entries for missing suggested schemas
        types_found = {s["type"] for s in p["schemas_found"]}
        suggested = _suggest_schemas(rel)
        for s in suggested:
            if s not in types_found:
                page_fixes.append({
                    "action": "add",
                    "type": s,
                    "reason": f"Missing {s} schema",
                })

        if page_fixes:
            fixes[rel] = page_fixes

    return fixes


# ── main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Audit JSON-LD structured data across all HTML pages.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python schema_audit.py              # audit (read-only)\n"
            "  python schema_audit.py --verbose     # show all schemas\n"
            "  python schema_audit.py --fix         # apply fixes (Phase 3)\n"
        ),
    )
    parser.add_argument(
        "--audit", action="store_true", default=True,
        help="Run audit only (default, read-only)",
    )
    parser.add_argument(
        "--fix", action="store_true",
        help="Apply fixes from schema_fixes.json (Phase 3 feature)",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Show all schemas found, not just issues",
    )
    args = parser.parse_args()

    if args.fix:
        logger.info("Fix mode not yet implemented")
        print("Fix mode not yet implemented")
        sys.exit(0)

    # ── collect HTML files ────────────────────────────────────────────
    ensure_dirs()
    html_files = get_all_html_files()
    if not html_files:
        logger.warning("No HTML files found in %s", SITE_DIR)
        print(f"No HTML files found in {SITE_DIR}")
        sys.exit(1)

    logger.info("Scanning %d HTML files in %s", len(html_files), SITE_DIR)

    # ── audit each page ──────────────────────────────────────────────
    page_results: list[dict] = []
    for filepath in html_files:
        result = audit_page(filepath)
        page_results.append(result)

        issue_count = len(result["issues"])
        schema_count = len(result["schemas_found"])
        types_found = [s["type"] for s in result["schemas_found"]]

        if args.verbose:
            if types_found:
                logger.info(
                    "  %s: %d schema(s) [%s], %d issue(s)",
                    result["rel_path"], schema_count, ", ".join(types_found), issue_count,
                )
            else:
                logger.info(
                    "  %s: no schemas found, %d issue(s)",
                    result["rel_path"], issue_count,
                )
        elif issue_count > 0:
            logger.info(
                "  %s: %d issue(s) found",
                result["rel_path"], issue_count,
            )

    # ── summary stats ────────────────────────────────────────────────
    total_schemas = sum(len(p["schemas_found"]) for p in page_results)
    total_issues = sum(len(p["issues"]) for p in page_results)
    total_errors = sum(
        1 for p in page_results for i in p["issues"] if i["severity"] == SEVERITY_ERROR
    )
    total_warnings = sum(
        1 for p in page_results for i in p["issues"] if i["severity"] == SEVERITY_WARNING
    )

    logger.info(
        "Audit complete: %d pages, %d schemas, %d issues (%d errors, %d warnings)",
        len(page_results), total_schemas, total_issues, total_errors, total_warnings,
    )

    # ── generate report ──────────────────────────────────────────────
    report_content = generate_report(page_results)
    report_filename = f"schema-audit-{today()}.md"
    report_path = save_report(report_filename, report_content)
    logger.info("Report saved: %s", report_path)

    # ── generate fix data ────────────────────────────────────────────
    fix_data = generate_fix_data(page_results)
    fix_path = DATA_DIR / "schema_fixes.json"
    save_json(fix_path, fix_data)
    logger.info("Fix data saved: %s", fix_path)

    print(f"\nSchema audit complete.")
    print(f"  Pages scanned: {len(page_results)}")
    print(f"  Schemas found: {total_schemas}")
    print(f"  Issues: {total_issues} ({total_errors} errors, {total_warnings} warnings)")
    print(f"  Report: {report_path}")
    print(f"  Fix data: {fix_path}")


if __name__ == "__main__":
    main()
