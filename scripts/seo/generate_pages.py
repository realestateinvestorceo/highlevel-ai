#!/usr/bin/env python3
"""
Programmatic page generator for highlevel.ai

Renders Jinja2 templates with seed data from JSON files to produce
static HTML pages matching the existing site design.

Usage:
    python generate_pages.py --type vs --dry-run
    python generate_pages.py --type industry --generate
    python generate_pages.py --type feature --generate
    python generate_pages.py --all --generate
    python generate_pages.py --validate
"""

import sys
import argparse
import re
from pathlib import Path
from datetime import datetime
from collections import Counter

# Import shared config
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (
    SITE_DIR, SCRIPTS_DIR, DATA_DIR, TEMPLATES_DIR, SITE_URL,
    AFFILIATE_LINK, AFFILIATE_PARAM,
    load_json, save_json, setup_logging, ensure_dirs, today
)

try:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
except ImportError:
    print("ERROR: Jinja2 is required. Install with: pip install Jinja2")
    sys.exit(1)


# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────

REGISTRY_FILE = DATA_DIR / "page_registry.json"
CURRENT_YEAR = datetime.now().year

logger = setup_logging("generate_pages")


# ──────────────────────────────────────────────
# Jinja2 Environment Setup
# ──────────────────────────────────────────────

def create_jinja_env() -> Environment:
    """Create and configure the Jinja2 template environment."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(default_for_string=False, default=False),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env


# ──────────────────────────────────────────────
# Validation Helpers
# ──────────────────────────────────────────────

def word_count(text: str) -> int:
    """Count words in a string, stripping HTML tags."""
    clean = re.sub(r'<[^>]+>', '', str(text))
    return len(clean.split())


def extract_sentences(text: str) -> list[str]:
    """Extract sentences from a text string for uniqueness checking."""
    clean = re.sub(r'<[^>]+>', '', str(text))
    sentences = re.split(r'[.!?]+', clean)
    return [s.strip().lower() for s in sentences if len(s.strip()) > 20]


def check_sentence_overlap(text_a: str, text_b: str) -> float:
    """Return the fraction of sentences in text_a that also appear in text_b."""
    sents_a = set(extract_sentences(text_a))
    sents_b = set(extract_sentences(text_b))
    if not sents_a:
        return 0.0
    overlap = sents_a & sents_b
    return len(overlap) / len(sents_a)


def validate_meta_length(title: str, description: str) -> list[str]:
    """Check title and description length constraints."""
    issues = []
    if len(title) > 60:
        issues.append(f"Title too long ({len(title)} chars, max 60): {title[:60]}...")
    if len(description) > 155:
        issues.append(f"Description too long ({len(description)} chars, max 155): {description[:80]}...")
    return issues


# ──────────────────────────────────────────────
# Date Formatting
# ──────────────────────────────────────────────

def format_date_display(date_str: str) -> str:
    """Convert YYYY-MM-DD to 'Month DD, YYYY' display format."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%B %d, %Y").replace(" 0", " ")
    except (ValueError, TypeError):
        return date_str


def format_month_year(date_str: str) -> str:
    """Convert YYYY-MM-DD to 'Month YYYY' display format."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%B %Y")
    except (ValueError, TypeError):
        return date_str


# ──────────────────────────────────────────────
# Page Generation: Competitor Comparisons
# ──────────────────────────────────────────────

def generate_vs_pages(env: Environment, dry_run: bool = True) -> list[dict]:
    """Generate competitor comparison pages."""
    competitors = load_json(DATA_DIR / "competitors.json", default=[])
    template = env.get_template("vs_competitor.html")
    generated = []

    # Build related pages list from all competitors
    all_related = [
        {"slug": c["slug"], "type": "Comparison", "title": f"GoHighLevel vs {c['name']}: Full Comparison"}
        for c in competitors
    ]

    for comp in competitors:
        slug = comp["slug"]
        filename = f"{slug}.html"
        date_published = comp.get("date_published", today())
        date_modified = comp.get("date_modified", today())

        title = f"GoHighLevel vs {comp['name']} {CURRENT_YEAR}: Full Comparison"
        description = f"HighLevel vs {comp['name']} side-by-side: features, pricing, pros, cons, and which platform fits your business in {CURRENT_YEAR}."

        # Auto-trim meta tags
        if len(title) > 60:
            title = f"GoHighLevel vs {comp['name']}: Full Comparison"
        if len(description) > 155:
            description = description[:152] + "..."

        # Related pages (exclude self)
        related = [r for r in all_related if r["slug"] != slug]

        context = {
            "title": title,
            "description": description,
            "canonical_url": f"{SITE_URL}/{filename}",
            "og_title": title,
            "og_description": description,
            "date_published": date_published,
            "date_modified": date_modified,
            "date_modified_display": format_date_display(date_modified),
            "year": CURRENT_YEAR,
            "read_time": 12,
            "competitor": comp,
            "related_pages": related,
            "affiliate_link": AFFILIATE_LINK,
        }

        html = template.render(**context)
        output_path = SITE_DIR / filename

        if dry_run:
            logger.info(f"[DRY RUN] Would write: {output_path} ({len(html)} bytes)")
        else:
            output_path.write_text(html, encoding="utf-8")
            logger.info(f"Generated: {output_path} ({len(html)} bytes)")

        generated.append({
            "type": "vs",
            "slug": slug,
            "filename": filename,
            "title": title,
            "description": description,
            "canonical_url": f"{SITE_URL}/{filename}",
            "date_published": date_published,
            "date_modified": date_modified,
            "bytes": len(html),
        })

    return generated


# ──────────────────────────────────────────────
# Page Generation: Industry Guides
# ──────────────────────────────────────────────

def generate_industry_pages(env: Environment, dry_run: bool = True) -> list[dict]:
    """Generate industry guide pages."""
    industries = load_json(DATA_DIR / "industries.json", default=[])
    template = env.get_template("industry.html")
    generated = []

    all_related = [
        {"slug": i["slug"], "type": "Industry", "title": f"GoHighLevel for {i['name']}: Complete Guide"}
        for i in industries
    ]

    for ind in industries:
        slug = ind["slug"]
        filename = f"{slug}.html"
        date_published = ind.get("date_published", today())
        date_modified = ind.get("date_modified", today())

        title = f"GoHighLevel for {ind['name']} {CURRENT_YEAR}: Complete Guide"
        description = f"How {ind['name'].lower()} use GoHighLevel to automate bookings, follow-ups, reviews, and marketing. Setup guide with ROI analysis."

        if len(title) > 60:
            title = f"GoHighLevel for {ind['name']}: Complete Guide"
        if len(description) > 155:
            description = description[:152] + "..."

        related = [r for r in all_related if r["slug"] != slug]

        context = {
            "title": title,
            "description": description,
            "canonical_url": f"{SITE_URL}/{filename}",
            "og_title": title,
            "og_description": description,
            "date_published": date_published,
            "date_modified": date_modified,
            "date_modified_display": format_date_display(date_modified),
            "year": CURRENT_YEAR,
            "read_time": 10,
            "industry": ind,
            "related_pages": related,
            "affiliate_link": AFFILIATE_LINK,
        }

        html = template.render(**context)
        output_path = SITE_DIR / filename

        if dry_run:
            logger.info(f"[DRY RUN] Would write: {output_path} ({len(html)} bytes)")
        else:
            output_path.write_text(html, encoding="utf-8")
            logger.info(f"Generated: {output_path} ({len(html)} bytes)")

        generated.append({
            "type": "industry",
            "slug": slug,
            "filename": filename,
            "title": title,
            "description": description,
            "canonical_url": f"{SITE_URL}/{filename}",
            "date_published": date_published,
            "date_modified": date_modified,
            "bytes": len(html),
        })

    return generated


# ──────────────────────────────────────────────
# Page Generation: Feature Reviews
# ──────────────────────────────────────────────

def generate_feature_pages(env: Environment, dry_run: bool = True) -> list[dict]:
    """Generate feature review pages."""
    features = load_json(DATA_DIR / "features.json", default=[])
    template = env.get_template("feature_review.html")
    generated = []

    all_related = [
        {"slug": f["slug"], "type": "Feature", "title": f"GoHighLevel {f['name']} Review"}
        for f in features
    ]

    for feat in features:
        slug = feat["slug"]
        filename = f"{slug}.html"
        date_published = feat.get("date_published", today())
        date_modified = feat.get("date_modified", today())

        title = f"GoHighLevel {feat['name']} Review {CURRENT_YEAR}"
        description = f"GoHighLevel {feat['name']} review: setup guide, pricing, pros, cons, and honest verdict after hands-on testing."

        if len(title) > 60:
            title = f"GoHighLevel {feat['name']} Review"
        if len(description) > 155:
            description = description[:152] + "..."

        related = [r for r in all_related if r["slug"] != slug]

        context = {
            "title": title,
            "description": description,
            "canonical_url": f"{SITE_URL}/{filename}",
            "og_title": title,
            "og_description": description,
            "date_published": date_published,
            "date_modified": date_modified,
            "date_modified_display": format_date_display(date_modified),
            "year": CURRENT_YEAR,
            "read_time": 8,
            "feature": feat,
            "related_pages": related,
            "affiliate_link": AFFILIATE_LINK,
        }

        html = template.render(**context)
        output_path = SITE_DIR / filename

        if dry_run:
            logger.info(f"[DRY RUN] Would write: {output_path} ({len(html)} bytes)")
        else:
            output_path.write_text(html, encoding="utf-8")
            logger.info(f"Generated: {output_path} ({len(html)} bytes)")

        generated.append({
            "type": "feature",
            "slug": slug,
            "filename": filename,
            "title": title,
            "description": description,
            "canonical_url": f"{SITE_URL}/{filename}",
            "date_published": date_published,
            "date_modified": date_modified,
            "bytes": len(html),
        })

    return generated


# ──────────────────────────────────────────────
# Uniqueness Validation
# ──────────────────────────────────────────────

def validate_uniqueness() -> list[str]:
    """
    Validate that editorial/verdict content meets minimum word counts
    and that no two pages share >50% identical sentences.
    """
    issues = []

    # Check competitor verdicts
    competitors = load_json(DATA_DIR / "competitors.json", default=[])
    verdicts = {}
    for comp in competitors:
        name = comp["name"]
        verdict = comp.get("verdict", "")
        wc = word_count(verdict)
        if wc < 100:
            issues.append(f"Competitor '{name}' verdict is only {wc} words (minimum 100)")
        verdicts[name] = verdict

    # Check cross-competitor verdict overlap
    comp_names = list(verdicts.keys())
    for i, name_a in enumerate(comp_names):
        for name_b in comp_names[i+1:]:
            overlap = check_sentence_overlap(verdicts[name_a], verdicts[name_b])
            if overlap > 0.5:
                issues.append(
                    f"Competitor verdicts '{name_a}' and '{name_b}' share "
                    f"{overlap:.0%} sentence overlap (max 50%)"
                )

    # Check industry editorials
    industries = load_json(DATA_DIR / "industries.json", default=[])
    editorials = {}
    for ind in industries:
        name = ind["name"]
        editorial = ind.get("editorial", "")
        wc = word_count(editorial)
        if wc < 200:
            issues.append(f"Industry '{name}' editorial is only {wc} words (minimum 200)")
        editorials[name] = editorial

    # Check cross-industry editorial overlap
    ind_names = list(editorials.keys())
    for i, name_a in enumerate(ind_names):
        for name_b in ind_names[i+1:]:
            overlap = check_sentence_overlap(editorials[name_a], editorials[name_b])
            if overlap > 0.5:
                issues.append(
                    f"Industry editorials '{name_a}' and '{name_b}' share "
                    f"{overlap:.0%} sentence overlap (max 50%)"
                )

    # Check feature editorials
    features = load_json(DATA_DIR / "features.json", default=[])
    feat_editorials = {}
    for feat in features:
        name = feat["name"]
        editorial = feat.get("editorial", "")
        wc = word_count(editorial)
        if wc < 200:
            issues.append(f"Feature '{name}' editorial is only {wc} words (minimum 200)")
        feat_editorials[name] = editorial

    feat_names = list(feat_editorials.keys())
    for i, name_a in enumerate(feat_names):
        for name_b in feat_names[i+1:]:
            overlap = check_sentence_overlap(feat_editorials[name_a], feat_editorials[name_b])
            if overlap > 0.5:
                issues.append(
                    f"Feature editorials '{name_a}' and '{name_b}' share "
                    f"{overlap:.0%} sentence overlap (max 50%)"
                )

    # Check FAQ answer uniqueness across all types
    all_faq_answers = []
    for comp in competitors:
        for faq in comp.get("faqs", []):
            all_faq_answers.append((f"vs:{comp['name']}", faq["a"]))
    for ind in industries:
        for faq in ind.get("faqs", []):
            all_faq_answers.append((f"industry:{ind['name']}", faq["a"]))
    for feat in features:
        for faq in feat.get("faqs", []):
            all_faq_answers.append((f"feature:{feat['name']}", faq["a"]))

    # Check for duplicate FAQ answers
    answer_hashes = Counter()
    for label, answer in all_faq_answers:
        # Normalize for comparison
        normalized = re.sub(r'\s+', ' ', answer.strip().lower())
        answer_hashes[normalized] += 1

    for answer, count in answer_hashes.items():
        if count > 1:
            issues.append(f"Duplicate FAQ answer found {count} times: '{answer[:60]}...'")

    return issues


# ──────────────────────────────────────────────
# Registry Management
# ──────────────────────────────────────────────

def update_registry(pages: list[dict]):
    """Update the page registry with generated page metadata."""
    registry = load_json(REGISTRY_FILE, default={"generated_at": None, "pages": []})
    registry["generated_at"] = datetime.now().isoformat()

    # Merge: update existing entries, add new ones
    existing = {p["slug"]: p for p in registry.get("pages", [])}
    for page in pages:
        existing[page["slug"]] = page

    registry["pages"] = sorted(existing.values(), key=lambda p: p["slug"])
    save_json(REGISTRY_FILE, registry)
    logger.info(f"Registry updated: {len(registry['pages'])} pages tracked in {REGISTRY_FILE}")


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate programmatic SEO pages for highlevel.ai"
    )
    parser.add_argument(
        "--type",
        choices=["vs", "industry", "feature"],
        help="Generate pages of a specific type",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Generate all page types",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be generated without writing files",
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Actually write generated files to site/",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Check uniqueness and content quality only",
    )

    args = parser.parse_args()

    # Must specify at least one action
    if not any([args.type, args.all, args.validate]):
        parser.print_help()
        sys.exit(1)

    ensure_dirs()

    # Validate-only mode
    if args.validate:
        logger.info("Running uniqueness and quality validation...")
        issues = validate_uniqueness()
        if issues:
            logger.warning(f"Found {len(issues)} issues:")
            for issue in issues:
                logger.warning(f"  - {issue}")
            sys.exit(1)
        else:
            logger.info("All content passes validation checks.")
            sys.exit(0)

    # Determine dry_run mode
    dry_run = not args.generate
    if dry_run and not args.dry_run:
        logger.info("No --generate flag specified. Use --dry-run to preview or --generate to write files.")
        sys.exit(1)

    # Run validation first
    logger.info("Running pre-generation validation...")
    issues = validate_uniqueness()
    if issues:
        logger.warning(f"Found {len(issues)} content issues:")
        for issue in issues:
            logger.warning(f"  - {issue}")
        if not dry_run:
            logger.error("Fix validation issues before generating pages. Use --dry-run to preview anyway.")
            sys.exit(1)

    env = create_jinja_env()
    all_generated = []

    # Generate requested types
    types_to_generate = []
    if args.all:
        types_to_generate = ["vs", "industry", "feature"]
    elif args.type:
        types_to_generate = [args.type]

    for page_type in types_to_generate:
        if page_type == "vs":
            logger.info("Generating competitor comparison pages...")
            pages = generate_vs_pages(env, dry_run=dry_run)
            all_generated.extend(pages)

        elif page_type == "industry":
            logger.info("Generating industry guide pages...")
            pages = generate_industry_pages(env, dry_run=dry_run)
            all_generated.extend(pages)

        elif page_type == "feature":
            logger.info("Generating feature review pages...")
            pages = generate_feature_pages(env, dry_run=dry_run)
            all_generated.extend(pages)

    # Summary
    logger.info(f"\n{'=' * 50}")
    logger.info(f"{'DRY RUN ' if dry_run else ''}Generation Complete")
    logger.info(f"{'=' * 50}")
    logger.info(f"Total pages: {len(all_generated)}")

    for page in all_generated:
        status = "[DRY RUN]" if dry_run else "[WRITTEN]"
        logger.info(f"  {status} {page['filename']} - {page['title']}")

    # Validate meta tag lengths
    meta_issues = []
    for page in all_generated:
        meta_issues.extend(validate_meta_length(page["title"], page["description"]))
    if meta_issues:
        logger.warning(f"\nMeta tag issues ({len(meta_issues)}):")
        for issue in meta_issues:
            logger.warning(f"  - {issue}")

    # Update registry (only on actual generation)
    if not dry_run and all_generated:
        update_registry(all_generated)

    # Print sitemap-ready data
    if all_generated:
        logger.info(f"\nSitemap entries:")
        for page in all_generated:
            logger.info(f"  <url>")
            logger.info(f"    <loc>{page['canonical_url']}</loc>")
            logger.info(f"    <lastmod>{page['date_modified']}</lastmod>")
            logger.info(f"    <priority>0.8</priority>")
            logger.info(f"  </url>")


if __name__ == "__main__":
    main()
