#!/usr/bin/env python3
"""
GSC Performance Analyzer for highlevel.ai

Pulls search performance data from Google Search Console and generates
an actionable content strategy report.

Usage:
    python3 scripts/gsc_analyze.py
    python3 scripts/gsc_analyze.py --days 14 --min-impressions 5
    python3 scripts/gsc_analyze.py --output reports/report.md
"""

import argparse
import datetime
import os
import sys
from collections import defaultdict

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SITE_URL = "sc-domain:highlevel.ai"
SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_DIR = os.path.join(SCRIPT_DIR, ".credentials")
CLIENT_SECRET_FILE = os.path.join(CREDENTIALS_DIR, "client_secret.json")
TOKEN_FILE = os.path.join(CREDENTIALS_DIR, "token.json")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "reports")

# Expected CTR by position (industry averages, used for comparative scoring)
EXPECTED_CTR = {
    1: 0.280, 2: 0.155, 3: 0.110, 4: 0.080, 5: 0.050,
    6: 0.035, 7: 0.025, 8: 0.020, 9: 0.015, 10: 0.010,
}

# Page-to-topic mapping for content gap analysis
PAGE_TOPICS = {
    "/": {
        "title": "HighLevel AI Review 2026",
        "keywords": [
            "highlevel review", "gohighlevel review", "highlevel ai review",
            "highlevel ai", "gohighlevel", "highlevel features",
            "highlevel pros cons", "highlevel verdict", "what is highlevel",
            "is gohighlevel worth it", "highlevel 2026",
        ],
    },
    "/pricing-explained.html": {
        "title": "HighLevel Pricing Explained",
        "keywords": [
            "highlevel pricing", "gohighlevel pricing", "highlevel cost",
            "highlevel plans", "highlevel starter", "highlevel unlimited",
            "highlevel saas pro", "gohighlevel cost", "highlevel hidden costs",
            "highlevel twilio cost", "highlevel monthly cost",
        ],
    },
    "/highlevel-vs-hubspot.html": {
        "title": "HighLevel vs HubSpot",
        "keywords": [
            "highlevel vs hubspot", "gohighlevel vs hubspot",
            "highlevel hubspot comparison", "highlevel or hubspot",
        ],
    },
    "/highlevel-vs-zapier-make.html": {
        "title": "HighLevel vs Zapier & Make",
        "keywords": [
            "highlevel vs zapier", "highlevel vs make", "gohighlevel zapier",
            "highlevel automation", "highlevel integrations",
        ],
    },
    "/highlevel-vs-chatgpt.html": {
        "title": "HighLevel vs ChatGPT",
        "keywords": [
            "highlevel vs chatgpt", "gohighlevel chatgpt",
            "highlevel ai vs chatgpt", "highlevel chatbot vs chatgpt",
        ],
    },
    "/voice-agent-setup.html": {
        "title": "Voice Agent Setup Guide",
        "keywords": [
            "highlevel voice agent", "gohighlevel voice agent",
            "highlevel voice ai", "highlevel voice agent setup",
            "highlevel twilio voice", "ghl voice agent",
        ],
    },
    "/workflows-for-agencies.html": {
        "title": "Workflows for Agencies",
        "keywords": [
            "highlevel workflows", "gohighlevel workflows",
            "highlevel agency workflows", "highlevel automation templates",
            "highlevel workflow templates", "ghl workflows",
        ],
    },
    "/best-prompts-sales-support.html": {
        "title": "Best Prompts for Sales & Support",
        "keywords": [
            "highlevel prompts", "gohighlevel prompts", "highlevel ai prompts",
            "highlevel sales prompts", "highlevel support prompts",
            "highlevel conversation ai prompts",
        ],
    },
    "/highlevel-for-med-spas.html": {
        "title": "HighLevel for Med Spas",
        "keywords": [
            "highlevel med spa", "gohighlevel med spa",
            "highlevel medical spa", "highlevel hipaa",
            "crm for med spas", "med spa crm", "med spa marketing",
        ],
    },
    "/highlevel-for-real-estate.html": {
        "title": "HighLevel for Real Estate",
        "keywords": [
            "highlevel real estate", "gohighlevel real estate",
            "highlevel for realtors", "crm for real estate",
            "highlevel real estate automation", "real estate crm",
        ],
    },
    "/mistakes-to-avoid.html": {
        "title": "Mistakes to Avoid",
        "keywords": [
            "highlevel mistakes", "gohighlevel mistakes",
            "highlevel problems", "highlevel issues",
            "highlevel pitfalls", "highlevel tips",
        ],
    },
    "/gohighlevel-reviews.html": {
        "title": "GoHighLevel Reviews",
        "keywords": [
            "gohighlevel reviews", "gohighlevel capterra rating",
            "gohighlevel g2 rating", "highlevel reviews", "highlevel ratings",
            "gohighlevel trustpilot", "gohighlevel user reviews",
        ],
    },
    "/about.html": {
        "title": "About highlevel.ai",
        "keywords": ["highlevel ai about", "who runs highlevel ai"],
    },
    "/editorial-policy.html": {
        "title": "Editorial Policy",
        "keywords": ["highlevel review methodology"],
    },
}

# Brand terms stripped during gap matching (present on every page, not distinctive)
BRAND_TERMS = {"highlevel", "gohighlevel", "ghl", "high", "level", "ai"}

# Common stopwords
STOPWORDS = {
    "a", "an", "the", "is", "it", "in", "on", "at", "to", "for", "of",
    "and", "or", "but", "not", "with", "from", "by", "as", "this", "that",
    "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "will", "would", "could", "should", "can", "may",
    "i", "you", "we", "they", "he", "she", "my", "your", "our", "their",
    "what", "which", "who", "how", "when", "where", "why",
}


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def authenticate(site_url):
    """Authenticate with GSC API via OAuth2 desktop flow."""
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing access token...")
            creds.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRET_FILE):
                print(f"ERROR: client_secret.json not found at {CLIENT_SECRET_FILE}")
                print("See scripts/README.md for setup instructions.")
                sys.exit(1)

            print("Opening browser for Google OAuth consent...")
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        os.makedirs(CREDENTIALS_DIR, exist_ok=True)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        print("Token saved.")

    service = build("searchconsole", "v1", credentials=creds)

    # Verify the site is accessible
    site_list = service.sites().list().execute()
    available = [s["siteUrl"] for s in site_list.get("siteEntry", [])]
    if site_url not in available:
        print(f"\nERROR: '{site_url}' not found in your GSC account.")
        print("Available properties:")
        for s in available:
            print(f"  - {s}")
        print(f"\nTry running with: --site-url <one of the above>")
        # Also try domain property format
        domain = site_url.replace("https://", "").replace("http://", "").rstrip("/")
        domain_no_www = domain.replace("www.", "")
        print(f"  Or if you have a domain property: --site-url sc-domain:{domain_no_www}")
        sys.exit(1)

    print(f"Authenticated. Connected to: {site_url}")
    return service


# ---------------------------------------------------------------------------
# Data Fetching
# ---------------------------------------------------------------------------

def fetch_data(service, site_url, days_back):
    """Fetch search analytics from GSC API. Returns 3 datasets."""
    # GSC data has ~3-day lag
    end_date = (datetime.date.today() - datetime.timedelta(days=3)).isoformat()
    start_date = (datetime.date.today() - datetime.timedelta(days=days_back + 3)).isoformat()

    print(f"Fetching data: {start_date} to {end_date} ({days_back} days)")

    # Call 1: Query + Page
    resp1 = service.searchanalytics().query(siteUrl=site_url, body={
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": ["query", "page"],
        "type": "web",
        "rowLimit": 25000,
        "dataState": "all",
    }).execute()
    query_page_rows = resp1.get("rows", [])
    print(f"  Query+Page rows: {len(query_page_rows)}")

    # Call 2: Page only
    resp2 = service.searchanalytics().query(siteUrl=site_url, body={
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": ["page"],
        "type": "web",
        "rowLimit": 1000,
        "dataState": "all",
    }).execute()
    page_rows = resp2.get("rows", [])
    print(f"  Page rows: {len(page_rows)}")

    # Call 3: Query only
    resp3 = service.searchanalytics().query(siteUrl=site_url, body={
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": ["query"],
        "type": "web",
        "rowLimit": 5000,
        "dataState": "all",
    }).execute()
    query_rows = resp3.get("rows", [])
    print(f"  Query rows: {len(query_rows)}")

    total_clicks = sum(r.get("clicks", 0) for r in query_rows)
    total_impressions = sum(r.get("impressions", 0) for r in query_rows)

    if total_impressions == 0:
        print("\nNo data found for this period.")
        print("GSC typically needs 2-4 weeks to accumulate data for a new site.")
        print("Try again in a week, or use --days 7 to check a shorter window.")
        sys.exit(0)

    return query_page_rows, page_rows, query_rows, start_date, end_date


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def get_expected_ctr(position):
    """Return expected CTR for an average position."""
    pos = max(1, min(round(position), 100))
    if pos <= 10:
        return EXPECTED_CTR[pos]
    elif pos <= 20:
        return 0.005
    return 0.002


def url_to_path(url):
    """Convert full URL to path (e.g., https://www.highlevel.ai/about.html -> /about.html)."""
    path = url.split("highlevel.ai")[-1] if "highlevel.ai" in url else url
    return path if path else "/"


def tokenize(text):
    """Split query into meaningful tokens."""
    tokens = text.lower().replace("-", " ").replace("_", " ").split()
    return [t for t in tokens if t not in STOPWORDS and len(t) > 1]


def content_tokens(text):
    """Tokenize and strip brand terms for gap matching."""
    return [t for t in tokenize(text) if t not in BRAND_TERMS]


def match_score(query, page_keywords):
    """Score how well a query matches a page's target keywords (0.0 - 1.0)."""
    q_tokens = set(content_tokens(query))
    if not q_tokens:
        return 0.0

    best = 0.0
    for kw in page_keywords:
        kw_tokens = set(content_tokens(kw))
        if not kw_tokens:
            continue
        overlap = len(q_tokens & kw_tokens)
        score = overlap / max(len(q_tokens), len(kw_tokens))
        best = max(best, score)
    return best


def fmt_pct(value):
    """Format a decimal as percentage string."""
    return f"{value * 100:.1f}%"


def fmt_pos(value):
    """Format position."""
    return f"{value:.1f}"


# ---------------------------------------------------------------------------
# Analysis Functions
# ---------------------------------------------------------------------------

def analyze_top_performers(query_rows, page_rows, top_n=10):
    """Top queries and pages by clicks and impressions."""
    queries_by_clicks = sorted(query_rows, key=lambda r: r["clicks"], reverse=True)[:top_n]
    queries_by_impr = sorted(query_rows, key=lambda r: r["impressions"], reverse=True)[:top_n]
    pages_by_clicks = sorted(page_rows, key=lambda r: r["clicks"], reverse=True)[:top_n]

    return {
        "queries_by_clicks": queries_by_clicks,
        "queries_by_impressions": queries_by_impr,
        "pages_by_clicks": pages_by_clicks,
    }


def analyze_low_hanging_fruit(query_page_rows, min_impressions):
    """Queries at position 5-20 with opportunity to improve."""
    results = []
    for row in query_page_rows:
        pos = row["position"]
        impr = row["impressions"]
        ctr = row["ctr"]
        if 5.0 <= pos <= 20.0 and impr >= min_impressions:
            expected = get_expected_ctr(pos)
            if ctr < expected:
                opportunity = impr * (expected - ctr)
                results.append({
                    "query": row["keys"][0],
                    "page": url_to_path(row["keys"][1]),
                    "position": pos,
                    "impressions": impr,
                    "clicks": row["clicks"],
                    "ctr": ctr,
                    "expected_ctr": expected,
                    "opportunity": opportunity,
                })
    results.sort(key=lambda r: r["opportunity"], reverse=True)
    return results[:20]


def analyze_ctr_optimization(page_rows):
    """Pages with CTR significantly below expected for their position."""
    results = []
    for row in page_rows:
        pos = row["position"]
        ctr = row["ctr"]
        impr = row["impressions"]
        if impr < 5:
            continue
        expected = get_expected_ctr(pos)
        if expected > 0 and ctr < expected * 0.7:
            gap = (ctr - expected) / expected
            path = url_to_path(row["keys"][0])
            title = PAGE_TOPICS.get(path, {}).get("title", path)
            results.append({
                "page": path,
                "title": title,
                "position": pos,
                "impressions": impr,
                "ctr": ctr,
                "expected_ctr": expected,
                "gap_pct": gap,
            })
    results.sort(key=lambda r: r["gap_pct"])
    return results[:10]


def analyze_content_gaps(query_page_rows, min_impressions):
    """Queries with impressions that no existing page targets well."""
    # Aggregate impressions per query across all pages
    query_agg = defaultdict(lambda: {"impressions": 0, "clicks": 0, "position": 0, "count": 0})
    for row in query_page_rows:
        q = row["keys"][0]
        query_agg[q]["impressions"] += row["impressions"]
        query_agg[q]["clicks"] += row["clicks"]
        query_agg[q]["position"] += row["position"] * row["impressions"]
        query_agg[q]["count"] += row["impressions"]

    gaps = []
    for query, agg in query_agg.items():
        if agg["impressions"] < min_impressions:
            continue
        avg_pos = agg["position"] / agg["count"] if agg["count"] else 0

        # Find best matching page
        best_page = None
        best_score = 0.0
        for page_path, info in PAGE_TOPICS.items():
            score = match_score(query, info["keywords"])
            if score > best_score:
                best_score = score
                best_page = page_path

        # If no page matches well, it's a gap
        if best_score < 0.4:
            gaps.append({
                "query": query,
                "impressions": agg["impressions"],
                "clicks": agg["clicks"],
                "position": avg_pos,
                "best_page": best_page or "/",
                "match_score": best_score,
            })

    gaps.sort(key=lambda r: r["impressions"], reverse=True)
    return gaps[:30]


def analyze_query_clusters(gaps):
    """Group gap queries into thematic clusters for new article suggestions."""
    if not gaps:
        return []

    # Build token-to-queries index
    token_index = defaultdict(list)
    for g in gaps:
        tokens = content_tokens(g["query"])
        for t in tokens:
            token_index[t].append(g)

    # Find clusters: groups of queries sharing distinctive tokens
    used = set()
    clusters = []

    # Sort tokens by how many queries they appear in (descending)
    sorted_tokens = sorted(token_index.items(), key=lambda x: len(x[1]), reverse=True)

    for token, queries in sorted_tokens:
        if token in used:
            continue
        # Only consider tokens appearing in 2+ gap queries
        if len(queries) < 2:
            continue

        cluster_queries = []
        total_impr = 0
        total_pos = 0
        count = 0
        for q in queries:
            if q["query"] not in [cq["query"] for cq in cluster_queries]:
                cluster_queries.append(q)
                total_impr += q["impressions"]
                total_pos += q["position"] * q["impressions"]
                count += q["impressions"]

        if total_impr > 0:
            avg_pos = total_pos / count
            clusters.append({
                "theme": token,
                "queries": cluster_queries,
                "total_impressions": total_impr,
                "avg_position": avg_pos,
                "query_count": len(cluster_queries),
            })
            used.add(token)

    clusters.sort(key=lambda c: c["total_impressions"], reverse=True)
    return clusters[:10]


# ---------------------------------------------------------------------------
# Report Generator
# ---------------------------------------------------------------------------

def generate_report(top, lhf, ctr_opt, gaps, clusters,
                    query_rows, start_date, end_date):
    """Generate markdown report."""
    total_clicks = sum(r["clicks"] for r in query_rows)
    total_impressions = sum(r["impressions"] for r in query_rows)
    avg_ctr = total_clicks / total_impressions if total_impressions else 0
    avg_pos = (sum(r["position"] * r["impressions"] for r in query_rows) /
               total_impressions if total_impressions else 0)

    lines = []
    lines.append("# GSC Performance Report: www.highlevel.ai")
    lines.append(f"**Period:** {start_date} to {end_date}")
    lines.append(f"**Generated:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Totals:** {total_clicks} clicks | {total_impressions:,} impressions | "
                 f"{fmt_pct(avg_ctr)} CTR | {fmt_pos(avg_pos)} avg position")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Section 1: Top Performers ---
    lines.append("## 1. Top Queries (by clicks)")
    lines.append("")
    lines.append("| # | Query | Clicks | Impressions | CTR | Position |")
    lines.append("|---|-------|--------|-------------|-----|----------|")
    for i, row in enumerate(top["queries_by_clicks"], 1):
        q = row["keys"][0]
        lines.append(f"| {i} | {q} | {row['clicks']} | {row['impressions']} | "
                     f"{fmt_pct(row['ctr'])} | {fmt_pos(row['position'])} |")
    lines.append("")

    lines.append("## 2. Top Pages (by clicks)")
    lines.append("")
    lines.append("| # | Page | Clicks | Impressions | CTR | Position |")
    lines.append("|---|------|--------|-------------|-----|----------|")
    for i, row in enumerate(top["pages_by_clicks"], 1):
        path = url_to_path(row["keys"][0])
        lines.append(f"| {i} | {path} | {row['clicks']} | {row['impressions']} | "
                     f"{fmt_pct(row['ctr'])} | {fmt_pos(row['position'])} |")
    lines.append("")

    # --- Section 2: Low-Hanging Fruit ---
    lines.append("## 3. Low-Hanging Fruit (Position 5-20)")
    lines.append("")
    if lhf:
        lines.append("Queries close to page 1 that could move up with optimization.")
        lines.append("")
        lines.append("| # | Query | Page | Position | Impressions | CTR | Expected | Opportunity |")
        lines.append("|---|-------|------|----------|-------------|-----|----------|-------------|")
        for i, r in enumerate(lhf, 1):
            opp = "HIGH" if r["opportunity"] > 5 else "MED" if r["opportunity"] > 1 else "LOW"
            lines.append(f"| {i} | {r['query']} | {r['page']} | {fmt_pos(r['position'])} | "
                         f"{r['impressions']} | {fmt_pct(r['ctr'])} | {fmt_pct(r['expected_ctr'])} | {opp} |")
        lines.append("")
    else:
        lines.append("No low-hanging fruit found yet. This is normal for new sites.")
        lines.append("")

    # --- Section 3: CTR Optimization ---
    lines.append("## 4. CTR Optimization Candidates")
    lines.append("")
    if ctr_opt:
        lines.append("Pages with CTR below expected for their position (title/meta description candidates).")
        lines.append("")
        lines.append("| # | Page | Position | CTR | Expected | Gap |")
        lines.append("|---|------|----------|-----|----------|-----|")
        for i, r in enumerate(ctr_opt, 1):
            lines.append(f"| {i} | {r['page']} | {fmt_pos(r['position'])} | "
                         f"{fmt_pct(r['ctr'])} | {fmt_pct(r['expected_ctr'])} | {r['gap_pct']:.0%} |")
        lines.append("")
    else:
        lines.append("No underperforming pages detected yet.")
        lines.append("")

    # --- Section 4: Content Gaps ---
    lines.append("## 5. Content Gaps")
    lines.append("")
    if gaps:
        lines.append("Queries with impressions that no existing page targets well.")
        lines.append("")
        lines.append("| # | Query | Impressions | Position | Closest Page | Match |")
        lines.append("|---|-------|-------------|----------|-------------|-------|")
        for i, r in enumerate(gaps, 1):
            lines.append(f"| {i} | {r['query']} | {r['impressions']} | "
                         f"{fmt_pos(r['position'])} | {r['best_page']} | {r['match_score']:.0%} |")
        lines.append("")
    else:
        lines.append("No significant content gaps detected yet.")
        lines.append("")

    # --- Section 5: New Article Suggestions ---
    lines.append("## 6. Suggested New Articles")
    lines.append("")
    if clusters:
        lines.append("Query clusters suggesting new content opportunities.")
        lines.append("")
        for i, c in enumerate(clusters, 1):
            lines.append(f"### {i}. Theme: \"{c['theme']}\"")
            lines.append(f"- **Total impressions:** {c['total_impressions']}")
            lines.append(f"- **Avg position:** {fmt_pos(c['avg_position'])}")
            lines.append(f"- **Queries ({c['query_count']}):**")
            for q in c["queries"][:5]:
                lines.append(f"  - \"{q['query']}\" ({q['impressions']} impr, pos {fmt_pos(q['position'])})")
            lines.append("")
    else:
        lines.append("Not enough gap data to suggest new articles yet.")
        lines.append("")

    # --- Summary ---
    lines.append("---")
    lines.append("")
    lines.append("## Priority Actions")
    lines.append("")

    actions = []
    if lhf:
        top_lhf = lhf[0]
        actions.append(f"1. **Quick win:** Optimize \"{top_lhf['query']}\" on `{top_lhf['page']}` "
                       f"(position {fmt_pos(top_lhf['position'])}, {top_lhf['impressions']} impressions)")
    if ctr_opt:
        top_ctr = ctr_opt[0]
        actions.append(f"{len(actions)+1}. **Rewrite title/meta:** `{top_ctr['page']}` "
                       f"(CTR {top_ctr['gap_pct']:.0%} below expected)")
    if clusters:
        top_cluster = clusters[0]
        actions.append(f"{len(actions)+1}. **New article:** Create content targeting "
                       f"\"{top_cluster['theme']}\" theme ({top_cluster['total_impressions']} impressions)")

    if actions:
        lines.extend(actions)
    else:
        lines.append("Site is too new for specific action items. Run again in 1-2 weeks.")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Analyze Google Search Console data for highlevel.ai"
    )
    parser.add_argument("--days", type=int, default=28,
                        help="Number of days to analyze (default: 28)")
    parser.add_argument("--min-impressions", type=int, default=10,
                        help="Minimum impressions threshold (default: 10)")
    parser.add_argument("--output", type=str, default=None,
                        help="Save report to file (e.g., reports/report.md)")
    parser.add_argument("--site-url", type=str, default=SITE_URL,
                        help=f"GSC property URL (default: {SITE_URL})")
    args = parser.parse_args()

    print("=" * 60)
    print("  GSC Performance Analyzer — highlevel.ai")
    print("=" * 60)
    print()

    # Authenticate
    service = authenticate(args.site_url)
    print()

    # Fetch data
    qp_rows, page_rows, query_rows, start_date, end_date = fetch_data(
        service, args.site_url, args.days
    )
    print()

    # Run analyses
    print("Running analysis...")
    top = analyze_top_performers(query_rows, page_rows)
    lhf = analyze_low_hanging_fruit(qp_rows, args.min_impressions)
    ctr_opt = analyze_ctr_optimization(page_rows)
    gaps = analyze_content_gaps(qp_rows, args.min_impressions)
    clusters = analyze_query_clusters(gaps)
    print("  Done.")
    print()

    # Generate report
    report = generate_report(top, lhf, ctr_opt, gaps, clusters,
                             query_rows, start_date, end_date)

    # Output
    print(report)

    if args.output:
        output_path = os.path.join(SCRIPT_DIR, args.output)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w") as f:
            f.write(report)
        print(f"\nReport saved to: {output_path}")


if __name__ == "__main__":
    main()
