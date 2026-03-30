#!/usr/bin/env python3
"""
apply_schema_fixes.py
---------------------
Reads schema_fixes.json and injects missing JSON-LD structured data blocks
into HTML pages.

Usage:
    python apply_schema_fixes.py            # dry-run (default) — report only
    python apply_schema_fixes.py --apply    # actually modify files
"""

import sys
import os
import re
import json
import argparse
from pathlib import Path

# Ensure the scripts/seo directory is on the path so config can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import SITE_DIR, setup_logging, get_all_html_files, backup_file, save_report, today, now
from config import SITE_URL, AUTHOR, PUBLISHER, OG_IMAGE, DATA_DIR


logger = setup_logging("apply_schema_fixes")

FIXES_FILE = DATA_DIR / "schema_fixes.json"


# ──────────────────────────────────────────────
# Schema generators
# ──────────────────────────────────────────────

def _extract_title(html: str) -> str:
    """Extract the <title> text from HTML."""
    m = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else "Untitled"


def _extract_meta_description(html: str) -> str:
    """Extract the meta description content from HTML."""
    m = re.search(r'<meta\s+name="description"\s+content="(.*?)"', html, re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _page_url(rel_path: str) -> str:
    """Build the absolute URL for a page given its path relative to SITE_DIR."""
    url = SITE_URL + "/" + rel_path
    if url.endswith("/index.html"):
        url = url[:-10]  # /blog/index.html -> /blog/
    return url


def generate_schema(schema_type: str, rel_path: str, html: str) -> dict | None:
    """
    Generate a JSON-LD schema dict for a given type.
    Returns None if the type is unrecognized.
    """
    title = _extract_title(html)
    description = _extract_meta_description(html)
    url = _page_url(rel_path)

    if schema_type == "WebPage":
        return {
            "@context": "https://schema.org",
            "@type": "WebPage",
            "name": title,
            "description": description,
            "url": url,
            "author": AUTHOR,
            "publisher": PUBLISHER,
        }

    if schema_type == "Person":
        return {
            "@context": "https://schema.org",
            "@type": "Person",
            "name": AUTHOR["name"],
            "jobTitle": AUTHOR["jobTitle"],
            "url": AUTHOR["url"],
            "description": AUTHOR["description"],
        }

    if schema_type == "BlogPosting":
        return {
            "@context": "https://schema.org",
            "@type": "BlogPosting",
            "headline": title,
            "description": description,
            "url": url,
            "author": AUTHOR,
            "publisher": PUBLISHER,
            "image": OG_IMAGE,
        }

    if schema_type == "SoftwareApplication":
        return {
            "@context": "https://schema.org",
            "@type": "SoftwareApplication",
            "name": "GoHighLevel",
            "applicationCategory": "BusinessApplication",
            "operatingSystem": "Web",
            "url": "https://www.gohighlevel.com/?fp_ref=ai",
            "offers": {
                "@type": "Offer",
                "price": "97",
                "priceCurrency": "USD",
            },
        }

    if schema_type == "ReviewArticle":
        return {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": title,
            "description": description,
            "url": url,
            "author": AUTHOR,
            "publisher": PUBLISHER,
            "image": OG_IMAGE,
        }

    logger.warning("Unknown schema type: %s — skipping", schema_type)
    return None


# ──────────────────────────────────────────────
# Injection logic
# ──────────────────────────────────────────────

def _already_has_schema(html: str, schema_type: str) -> bool:
    """Check if the HTML already contains a JSON-LD block with the given @type."""
    # Find all existing JSON-LD blocks and check their @type
    for m in re.finditer(
        r'<script\s+type="application/ld\+json">(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    ):
        try:
            data = json.loads(m.group(1))
            existing_type = data.get("@type", "")
            if existing_type == schema_type:
                return True
            # ReviewArticle maps to Article
            if schema_type == "ReviewArticle" and existing_type == "Article":
                return True
        except (json.JSONDecodeError, AttributeError):
            continue
    return False


def build_jsonld_tag(schema: dict) -> str:
    """Return a properly formatted <script type="application/ld+json"> tag."""
    payload = json.dumps(schema, indent=2, ensure_ascii=False)
    return f'<script type="application/ld+json">\n{payload}\n</script>'


def inject_schema(html: str, schema: dict) -> str:
    """Inject a JSON-LD block just before </head>."""
    tag = build_jsonld_tag(schema)
    # Insert right before </head>
    return html.replace("</head>", f"{tag}\n</head>", 1)


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Apply missing JSON-LD schema fixes to HTML pages.")
    parser.add_argument("--apply", action="store_true", help="Actually modify files (default is dry-run).")
    args = parser.parse_args()

    dry_run = not args.apply
    mode_label = "DRY RUN" if dry_run else "APPLY"
    logger.info("Schema fix tool started — mode: %s", mode_label)

    # Load fixes
    if not FIXES_FILE.exists():
        logger.error("Fixes file not found: %s", FIXES_FILE)
        sys.exit(1)

    with open(FIXES_FILE, "r", encoding="utf-8") as f:
        fixes = json.load(f)

    logger.info("Loaded %d pages with schema fixes from %s", len(fixes), FIXES_FILE.name)

    # Track results for report
    results = []  # list of dicts: {page, schemas_added, skipped, error}
    total_injected = 0
    total_skipped = 0
    total_errors = 0

    for rel_path, fix_list in sorted(fixes.items()):
        filepath = SITE_DIR / rel_path
        page_result = {"page": rel_path, "schemas_added": [], "skipped": [], "error": None}

        if not filepath.exists():
            msg = f"File not found: {filepath}"
            logger.warning(msg)
            page_result["error"] = msg
            total_errors += 1
            results.append(page_result)
            continue

        html = filepath.read_text(encoding="utf-8")
        modified = False

        for fix in fix_list:
            if fix.get("action") != "add":
                logger.debug("Skipping non-add action for %s: %s", rel_path, fix)
                continue

            schema_type = fix["type"]

            # Skip if already present
            if _already_has_schema(html, schema_type):
                logger.info("  SKIP %s — %s already present", rel_path, schema_type)
                page_result["skipped"].append(schema_type)
                total_skipped += 1
                continue

            schema = generate_schema(schema_type, rel_path, html)
            if schema is None:
                page_result["skipped"].append(f"{schema_type} (unknown)")
                total_skipped += 1
                continue

            html = inject_schema(html, schema)
            modified = True
            page_result["schemas_added"].append(schema_type)
            total_injected += 1
            logger.info("  ADD  %s — %s", rel_path, schema_type)

        # Write changes
        if modified and not dry_run:
            backup_path = backup_file(filepath, label="schema")
            logger.info("  Backup: %s", backup_path.name)
            filepath.write_text(html, encoding="utf-8")
            logger.info("  Written: %s", filepath)

        results.append(page_result)

    # ── Generate report ──────────────────────────
    report_lines = [
        f"# Schema Fixes Report — {today()}",
        f"",
        f"**Mode:** {mode_label}",
        f"**Run at:** {now()}",
        f"**Fixes file:** `{FIXES_FILE.name}`",
        f"",
        f"## Summary",
        f"",
        f"| Metric | Count |",
        f"|--------|-------|",
        f"| Pages processed | {len(results)} |",
        f"| Schemas injected | {total_injected} |",
        f"| Schemas skipped (already present) | {total_skipped} |",
        f"| Errors (file not found) | {total_errors} |",
        f"",
        f"## Details",
        f"",
    ]

    for r in results:
        page = r["page"]
        if r["error"]:
            report_lines.append(f"### `{page}`")
            report_lines.append(f"- **Error:** {r['error']}")
            report_lines.append("")
            continue

        if not r["schemas_added"] and not r["skipped"]:
            continue

        report_lines.append(f"### `{page}`")
        for s in r["schemas_added"]:
            action_word = "Would inject" if dry_run else "Injected"
            report_lines.append(f"- {action_word} **{s}**")
        for s in r["skipped"]:
            report_lines.append(f"- Skipped **{s}** (already present)")
        report_lines.append("")

    report_content = "\n".join(report_lines)
    report_filename = f"schema-fixes-applied-{today()}.md"
    report_path = save_report(report_filename, report_content)
    logger.info("Report saved to %s", report_path)

    # Print summary
    print(f"\n{'=' * 50}")
    print(f"  Schema Fixes — {mode_label}")
    print(f"{'=' * 50}")
    print(f"  Pages processed:    {len(results)}")
    print(f"  Schemas injected:   {total_injected}")
    print(f"  Skipped (present):  {total_skipped}")
    print(f"  Errors:             {total_errors}")
    print(f"  Report:             {report_path}")
    if dry_run:
        print(f"\n  (Dry run — no files modified. Use --apply to write changes.)")
    print()


if __name__ == "__main__":
    main()
