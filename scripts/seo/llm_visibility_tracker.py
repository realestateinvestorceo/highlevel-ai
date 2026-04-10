#!/usr/bin/env python3
"""
LLM Visibility Tracker for highlevel.ai

Queries ChatGPT, Claude, and Perplexity with tracking queries to measure
whether highlevel.ai is cited in LLM responses. Logs results to CSV,
generates trend analysis, and produces markdown reports.

Usage:
    python scripts/seo/llm_visibility_tracker.py              # Run all queries
    python scripts/seo/llm_visibility_tracker.py --summary     # Summary from CSV
    python scripts/seo/llm_visibility_tracker.py --trends      # Trend analysis only
    python scripts/seo/llm_visibility_tracker.py --query "..."  # Test single query

Requirements:
    pip install openai anthropic requests
    export OPENAI_API_KEY="sk-..."
    export ANTHROPIC_API_KEY="sk-ant-..."
    export PERPLEXITY_API_KEY="pplx-..."
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import *

import argparse
import csv
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta

# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────

QUERIES_FILE = DATA_DIR / "tracking_queries.json"
CSV_FILE = DATA_DIR / "llm_visibility_history.csv"
CSV_COLUMNS = [
    "date", "query", "provider", "cited", "brand_mention",
    "citations_urls", "citation_snippet",
    "competitors_cited", "response_length",
]

# `cited` is the union of grounded-URL citations (Perplexity's citations
# array contains highlevel.ai) and plain text mentions of the domain. These
# two signals answer different questions:
#   - URL citation: "Did the AI actually link to us as a source?"  (hard proof
#     that our content is being crawled and used)
#   - Brand mention: "Did the AI type 'highlevel.ai' anywhere in the answer?"
#     (weaker, but catches non-grounded ChatGPT/Claude recommending us by name)
TARGET_DOMAINS = ["highlevel.ai", "www.highlevel.ai"]
COMPETITOR_DOMAINS = [
    "gohighlevel.com",
    "hubspot.com",
    "clickfunnels.com",
    "activecampaign.com",
    "keap.com",
    "salesforce.com",
]

PROVIDERS = {
    "openai": {
        "name": "OpenAI",
        "model": "gpt-4o",
        "key_var": "OPENAI_API_KEY",
    },
    "anthropic": {
        "name": "Anthropic",
        "model": "claude-sonnet-4-5",
        "key_var": "ANTHROPIC_API_KEY",
    },
    "perplexity": {
        "name": "Perplexity",
        "model": "sonar",
        "key_var": "PERPLEXITY_API_KEY",
    },
}

logger = setup_logging("llm_visibility_tracker")


# ──────────────────────────────────────────────
# Query Functions
# ──────────────────────────────────────────────

# Each query function returns (response_text, citations_urls) where
# citations_urls is a list of source URLs the provider explicitly grounded
# its answer in. Only Perplexity returns real grounded citations; OpenAI
# and Anthropic non-grounded calls always return an empty list.

def query_openai(prompt: str) -> tuple[str | None, list[str]]:
    """Query ChatGPT via OpenAI API."""
    if not OPENAI_API_KEY:
        return None, []
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=PROVIDERS["openai"]["model"],
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000,
        )
        return response.choices[0].message.content, []
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        return None, []


def query_anthropic(prompt: str) -> tuple[str | None, list[str]]:
    """Query Claude via Anthropic API."""
    if not ANTHROPIC_API_KEY:
        return None, []
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=PROVIDERS["anthropic"]["model"],
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text, []
    except Exception as e:
        logger.error(f"Anthropic error: {e}")
        return None, []


def query_perplexity(prompt: str) -> tuple[str | None, list[str]]:
    """Query Perplexity with return_citations so we get the real grounded
    source URL list, not just the response text."""
    if not PERPLEXITY_API_KEY:
        return None, []
    try:
        import requests
        response = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": PROVIDERS["perplexity"]["model"],
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1000,
                "return_citations": True,
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        text = payload["choices"][0]["message"]["content"]
        # Perplexity returns citations at the top level of the response.
        # Different API versions have put them in different places over time,
        # so check both.
        citations = payload.get("citations") or []
        if not citations:
            # Older format — search_results with url fields
            for sr in payload.get("search_results") or []:
                if isinstance(sr, dict) and sr.get("url"):
                    citations.append(sr["url"])
        return text, citations
    except Exception as e:
        logger.error(f"Perplexity error: {e}")
        return None, []


QUERY_FUNCTIONS = {
    "openai": query_openai,
    "anthropic": query_anthropic,
    "perplexity": query_perplexity,
}


# ──────────────────────────────────────────────
# Citation Detection
# ──────────────────────────────────────────────

def check_brand_mention(text: str) -> bool:
    """True if highlevel.ai appears as a string anywhere in the response text."""
    if not text:
        return False
    text_lower = text.lower()
    return any(domain in text_lower for domain in TARGET_DOMAINS)


def check_url_citation(citations_urls: list[str]) -> bool:
    """True if any of Perplexity's grounded citation URLs point at highlevel.ai."""
    if not citations_urls:
        return False
    for url in citations_urls:
        url_lower = (url or "").lower()
        if any(domain in url_lower for domain in TARGET_DOMAINS):
            return True
    return False


def check_citation(text: str, citations_urls: list[str] | None = None) -> bool:
    """Combined citation signal — True if either grounded URL cite or text mention."""
    return check_url_citation(citations_urls or []) or check_brand_mention(text)


def extract_citation_snippet(text: str, max_length: int = 200) -> str:
    """Extract a snippet around the first highlevel.ai citation."""
    if not text:
        return ""
    text_lower = text.lower()
    for domain in TARGET_DOMAINS:
        idx = text_lower.find(domain)
        if idx != -1:
            start = max(0, idx - 80)
            end = min(len(text), idx + len(domain) + 80)
            snippet = text[start:end].strip()
            # Truncate to max_length
            if len(snippet) > max_length:
                snippet = snippet[:max_length]
            # Clean newlines for CSV safety
            snippet = snippet.replace("\n", " ").replace("\r", " ")
            return snippet
    return ""


def find_competitor_citations(text: str) -> list[str]:
    """Find which competitor domains appear in the response."""
    if not text:
        return []
    text_lower = text.lower()
    found = []
    for domain in COMPETITOR_DOMAINS:
        if domain in text_lower:
            found.append(domain)
    return found


# ──────────────────────────────────────────────
# CSV Logging
# ──────────────────────────────────────────────

def ensure_csv():
    """Create the CSV file with headers if it doesn't exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not CSV_FILE.exists():
        with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_COLUMNS)
        logger.info(f"Created CSV file: {CSV_FILE}")


def append_result(date_str: str, query: str, provider: str, cited: bool,
                  brand_mention: bool, citations_urls: list[str],
                  snippet: str, competitors: list[str], response_length: int):
    """Append a single result row to the CSV."""
    ensure_csv()
    # If an older CSV exists with the pre-brand_mention columns, migrate it
    # by appending the new columns lazily on first write.
    _ensure_csv_header_current()
    with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            date_str,
            query,
            provider,
            "true" if cited else "false",
            "true" if brand_mention else "false",
            " ".join(citations_urls),
            snippet,
            ",".join(competitors),
            response_length,
        ])


def _ensure_csv_header_current():
    """If the CSV exists with the old 7-column schema, rewrite it with the
    new 9-column header so appends don't misalign."""
    if not CSV_FILE.exists():
        return
    with open(CSV_FILE, "r", encoding="utf-8") as f:
        first_line = f.readline().strip()
    if first_line == ",".join(CSV_COLUMNS):
        return  # already current
    # Read everything, rewrite with new header + migrated rows
    with open(CSV_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        old_rows = list(reader)
    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        for r in old_rows:
            writer.writerow([
                r.get("date", ""),
                r.get("query", ""),
                r.get("provider", ""),
                r.get("cited", "false"),
                r.get("brand_mention", r.get("cited", "false")),
                r.get("citations_urls", ""),
                r.get("citation_snippet", ""),
                r.get("competitors_cited", ""),
                r.get("response_length", "0"),
            ])
    logger.info("Migrated CSV to new schema (%d rows)", len(old_rows))


def read_csv_history() -> list[dict]:
    """Read the full CSV history into a list of dicts."""
    if not CSV_FILE.exists():
        return []
    rows = []
    with open(CSV_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


# ──────────────────────────────────────────────
# Load Queries
# ──────────────────────────────────────────────

def load_queries() -> list[str]:
    """Load tracking queries from JSON file."""
    if not QUERIES_FILE.exists():
        logger.error(f"Queries file not found: {QUERIES_FILE}")
        sys.exit(1)
    return load_json(QUERIES_FILE, default=[])


# ──────────────────────────────────────────────
# Available Providers
# ──────────────────────────────────────────────

def get_available_providers() -> dict[str, dict]:
    """Return providers whose API keys are set."""
    available = {}
    for key, info in PROVIDERS.items():
        api_key = globals().get(info["key_var"], "") or os.getenv(info["key_var"], "")
        if api_key:
            available[key] = info
        else:
            logger.warning(f"Skipping {info['name']} -- {info['key_var']} not set")
    return available


# ──────────────────────────────────────────────
# Run Queries
# ──────────────────────────────────────────────

def run_all_queries():
    """Run all tracking queries against all available providers."""
    queries = load_queries()
    providers = get_available_providers()

    if not providers:
        logger.error("No API keys configured. Set at least one of: "
                     "OPENAI_API_KEY, ANTHROPIC_API_KEY, PERPLEXITY_API_KEY")
        sys.exit(1)

    date_str = today()
    total = len(queries) * len(providers)
    completed = 0
    cited_count = 0

    logger.info(f"Running {len(queries)} queries against {len(providers)} providers "
                f"({total} total API calls)")

    results = []  # For report generation

    for query in queries:
        logger.info(f'Query: "{query}"')
        query_results = {"query": query}

        for provider_key, provider_info in providers.items():
            query_fn = QUERY_FUNCTIONS[provider_key]
            provider_name = provider_info["name"]

            logger.info(f"  {provider_name}...", )
            response_text, citations_urls = query_fn(query)

            if response_text is None:
                logger.warning(f"  {provider_name}: no response (error or timeout)")
                query_results[provider_key] = None
                # Still log the failure
                append_result(date_str, query, provider_key,
                              False, False, [], "", [], 0)
                completed += 1
                time.sleep(1)
                continue

            url_cited = check_url_citation(citations_urls)
            brand_mention = check_brand_mention(response_text)
            cited = url_cited or brand_mention
            snippet = extract_citation_snippet(response_text) if brand_mention else ""
            competitors = find_competitor_citations(response_text)
            response_length = len(response_text)

            if cited:
                cited_count += 1
                tag = "URL CITE" if url_cited else "brand mention"
                logger.info(f"  {provider_name}: CITED ({tag})")
            else:
                logger.info(f"  {provider_name}: not cited")

            if competitors:
                logger.info(f"  {provider_name}: competitors mentioned: {', '.join(competitors)}")

            append_result(date_str, query, provider_key, cited, brand_mention,
                          citations_urls, snippet, competitors, response_length)

            query_results[provider_key] = {
                "cited": cited,
                "url_cited": url_cited,
                "brand_mention": brand_mention,
                "citations_urls": citations_urls,
                "snippet": snippet,
                "competitors": competitors,
                "response_length": response_length,
            }

            completed += 1
            # Rate limiting: 1 second between calls per provider
            time.sleep(1)

        results.append(query_results)

    logger.info(f"\nCompleted: {completed}/{total} calls, "
                f"{cited_count} citations found")

    # Generate report
    generate_report(results, providers, date_str)
    return results


def run_single_query(query: str):
    """Test a single query against all available providers."""
    providers = get_available_providers()

    if not providers:
        logger.error("No API keys configured.")
        sys.exit(1)

    date_str = today()
    print(f'\nQuery: "{query}"\n{"=" * 60}')

    for provider_key, provider_info in providers.items():
        query_fn = QUERY_FUNCTIONS[provider_key]
        provider_name = provider_info["name"]

        print(f"\n--- {provider_name} ({provider_info['model']}) ---")
        response_text, citations_urls = query_fn(query)

        if response_text is None:
            print("  ERROR: No response")
            continue

        url_cited = check_url_citation(citations_urls)
        brand_mention = check_brand_mention(response_text)
        cited = url_cited or brand_mention
        snippet = extract_citation_snippet(response_text) if brand_mention else ""
        competitors = find_competitor_citations(response_text)

        print(f"  URL cite: {'YES' if url_cited else 'no'}  "
              f"Brand mention: {'YES' if brand_mention else 'no'}")
        if citations_urls:
            print(f"  Grounded sources: {len(citations_urls)}")
            for u in citations_urls[:5]:
                print(f"    - {u}")
        if snippet:
            print(f"  Snippet: {snippet}")
        if competitors:
            print(f"  Competitors mentioned: {', '.join(competitors)}")
        print(f"  Response length: {len(response_text)} chars")

        # Log to CSV
        append_result(date_str, query, provider_key, cited, brand_mention,
                      citations_urls, snippet, competitors, len(response_text))

        time.sleep(1)


# ──────────────────────────────────────────────
# Trend Analysis
# ──────────────────────────────────────────────

def analyze_trends(rows: list[dict] | None = None) -> dict:
    """Analyze citation trends from CSV history."""
    if rows is None:
        rows = read_csv_history()

    if not rows:
        return {"error": "No history data available"}

    # Overall citation rate
    total = len(rows)
    cited = sum(1 for r in rows if r.get("cited") == "true")
    overall_rate = (cited / total * 100) if total > 0 else 0

    # Per-provider rates
    provider_stats = defaultdict(lambda: {"total": 0, "cited": 0})
    for r in rows:
        p = r.get("provider", "unknown")
        provider_stats[p]["total"] += 1
        if r.get("cited") == "true":
            provider_stats[p]["cited"] += 1

    provider_rates = {}
    for p, stats in provider_stats.items():
        rate = (stats["cited"] / stats["total"] * 100) if stats["total"] > 0 else 0
        provider_rates[p] = {
            "total": stats["total"],
            "cited": stats["cited"],
            "rate": round(rate, 1),
        }

    # Per-query rates
    query_stats = defaultdict(lambda: {"total": 0, "cited": 0})
    for r in rows:
        q = r.get("query", "unknown")
        query_stats[q]["total"] += 1
        if r.get("cited") == "true":
            query_stats[q]["cited"] += 1

    query_rates = {}
    for q, stats in query_stats.items():
        rate = (stats["cited"] / stats["total"] * 100) if stats["total"] > 0 else 0
        query_rates[q] = {
            "total": stats["total"],
            "cited": stats["cited"],
            "rate": round(rate, 1),
        }

    # Week-over-week trend
    today_dt = datetime.now().date()
    this_week_start = today_dt - timedelta(days=today_dt.weekday())
    last_week_start = this_week_start - timedelta(days=7)

    this_week_rows = []
    last_week_rows = []
    for r in rows:
        try:
            row_date = datetime.strptime(r["date"], "%Y-%m-%d").date()
        except (ValueError, KeyError):
            continue
        if row_date >= this_week_start:
            this_week_rows.append(r)
        elif row_date >= last_week_start:
            last_week_rows.append(r)

    this_week_rate = None
    last_week_rate = None
    trend = None

    if this_week_rows:
        tw_cited = sum(1 for r in this_week_rows if r.get("cited") == "true")
        this_week_rate = round(tw_cited / len(this_week_rows) * 100, 1)

    if last_week_rows:
        lw_cited = sum(1 for r in last_week_rows if r.get("cited") == "true")
        last_week_rate = round(lw_cited / len(last_week_rows) * 100, 1)

    if this_week_rate is not None and last_week_rate is not None:
        diff = this_week_rate - last_week_rate
        if diff > 2:
            trend = "up"
        elif diff < -2:
            trend = "down"
        else:
            trend = "stable"

    # Competitor analysis
    competitor_counts = defaultdict(int)
    total_responses = 0
    for r in rows:
        total_responses += 1
        comp_str = r.get("competitors_cited", "")
        if comp_str:
            for comp in comp_str.split(","):
                comp = comp.strip()
                if comp:
                    competitor_counts[comp] += 1

    competitor_rates = {}
    for comp, count in sorted(competitor_counts.items(), key=lambda x: -x[1]):
        rate = round(count / total_responses * 100, 1) if total_responses > 0 else 0
        competitor_rates[comp] = {"count": count, "rate": rate}

    return {
        "total_rows": total,
        "overall_rate": round(overall_rate, 1),
        "provider_rates": provider_rates,
        "query_rates": query_rates,
        "this_week_rate": this_week_rate,
        "last_week_rate": last_week_rate,
        "trend": trend,
        "competitor_rates": competitor_rates,
    }


def print_trends(trends: dict):
    """Print trend analysis to console."""
    if "error" in trends:
        print(f"  {trends['error']}")
        return

    print(f"\n{'=' * 60}")
    print(f"AI VISIBILITY TRENDS")
    print(f"{'=' * 60}")
    print(f"\nOverall citation rate: {trends['overall_rate']}% "
          f"({trends['total_rows']} total responses)")

    print(f"\n--- By Provider ---")
    for provider, stats in trends["provider_rates"].items():
        name = PROVIDERS.get(provider, {}).get("name", provider)
        print(f"  {name}: {stats['cited']}/{stats['total']} ({stats['rate']}%)")

    print(f"\n--- By Query ---")
    sorted_queries = sorted(trends["query_rates"].items(), key=lambda x: -x[1]["rate"])
    for query, stats in sorted_queries:
        print(f"  {query}: {stats['cited']}/{stats['total']} ({stats['rate']}%)")

    print(f"\n--- Week-over-Week ---")
    if trends["this_week_rate"] is not None:
        print(f"  This week: {trends['this_week_rate']}%")
    else:
        print(f"  This week: no data")
    if trends["last_week_rate"] is not None:
        print(f"  Last week: {trends['last_week_rate']}%")
    else:
        print(f"  Last week: no data")
    if trends["trend"]:
        arrow = {"up": "^", "down": "v", "stable": "->"}[trends["trend"]]
        print(f"  Trend: {arrow} ({trends['trend']})")
    else:
        print(f"  Trend: insufficient data")

    if trends["competitor_rates"]:
        print(f"\n--- Competitor Mentions ---")
        for comp, stats in trends["competitor_rates"].items():
            print(f"  {comp}: {stats['count']}x ({stats['rate']}%)")
    else:
        print(f"\n--- Competitor Mentions ---")
        print(f"  No competitors cited")


# ──────────────────────────────────────────────
# Report Generation
# ──────────────────────────────────────────────

def generate_report(results: list[dict] | None = None,
                    providers: dict | None = None,
                    date_str: str | None = None):
    """Generate a markdown visibility report."""
    if date_str is None:
        date_str = today()

    ensure_dirs()
    rows = read_csv_history()
    trends = analyze_trends(rows)

    # If we have fresh results, use them for the per-query table
    # Otherwise reconstruct from today's CSV rows
    if results is None:
        # Pull today's rows from CSV
        today_rows = [r for r in rows if r.get("date") == date_str]
        if not today_rows:
            logger.warning("No data for today to generate report from")
            return
    if providers is None:
        providers = get_available_providers()

    provider_keys = list(providers.keys())
    provider_names = [providers[k]["name"] for k in provider_keys]

    queries = load_queries()

    lines = []
    lines.append(f"# AI Visibility Report -- {date_str}")
    lines.append("")

    # Summary
    total_queries = len(queries)
    total_providers = len(providers)

    # Count today's citations
    today_rows = [r for r in rows if r.get("date") == date_str]
    today_cited = sum(1 for r in today_rows if r.get("cited") == "true")
    today_brand = sum(1 for r in today_rows if r.get("brand_mention") == "true")
    today_url = sum(
        1 for r in today_rows
        if r.get("cited") == "true" and r.get("brand_mention") != "true"
    )
    # A row is a URL-only cite if cited=true but brand_mention=false (meaning
    # the provider's returned citation URLs matched but the response text
    # didn't). It's a URL+brand cite if both are true. Simpler: count rows
    # whose citations_urls column contains a highlevel.ai substring.
    today_url = 0
    for r in today_rows:
        urls = (r.get("citations_urls") or "").lower()
        if any(d in urls for d in TARGET_DOMAINS):
            today_url += 1
    today_total = len(today_rows) if today_rows else total_queries * total_providers
    today_rate = round(today_cited / today_total * 100, 1) if today_total > 0 else 0
    today_brand_rate = round(today_brand / today_total * 100, 1) if today_total > 0 else 0
    today_url_rate = round(today_url / today_total * 100, 1) if today_total > 0 else 0

    lines.append("## Summary")
    lines.append(f"- Queries tested: {total_queries}")
    lines.append(f"- Providers checked: {total_providers}")
    lines.append(f"- Overall citation rate: {today_rate}%")
    lines.append(f"- URL citation rate: {today_url_rate}%")
    lines.append(f"- Brand mention rate: {today_brand_rate}%")
    lines.append("")

    # Results by Provider
    lines.append("## Results by Provider")
    lines.append("| Provider | Queries | Citations | URL Cites | Brand Mentions | Rate |")
    lines.append("|----------|---------|-----------|-----------|----------------|------|")

    for pk in provider_keys:
        pname = providers[pk]["name"]
        p_rows = [r for r in today_rows if r.get("provider") == pk]
        p_total = len(p_rows)
        p_cited = sum(1 for r in p_rows if r.get("cited") == "true")
        p_brand = sum(1 for r in p_rows if r.get("brand_mention") == "true")
        p_url = 0
        for r in p_rows:
            urls = (r.get("citations_urls") or "").lower()
            if any(d in urls for d in TARGET_DOMAINS):
                p_url += 1
        p_rate = round(p_cited / p_total * 100, 1) if p_total > 0 else 0
        lines.append(
            f"| {pname} | {p_total} | {p_cited} | {p_url} | {p_brand} | {p_rate}% |"
        )

    lines.append("")

    # Results by Query
    lines.append("## Results by Query")
    header_cells = ["Query"] + provider_names
    lines.append("| " + " | ".join(header_cells) + " |")
    lines.append("|" + "|".join(["---"] * len(header_cells)) + "|")

    for query in queries:
        cells = [query]
        for pk in provider_keys:
            matching = [
                r for r in today_rows
                if r.get("query") == query and r.get("provider") == pk
            ]
            if not matching:
                cells.append("--")
            elif matching[-1].get("cited") == "true":
                cells.append("V")
            else:
                cells.append("X")
        lines.append("| " + " | ".join(cells) + " |")

    lines.append("")

    # Trends section
    if trends and "error" not in trends and trends["total_rows"] > len(today_rows):
        lines.append("## Trends")
        if trends["this_week_rate"] is not None:
            lines.append(f"- This week: {trends['this_week_rate']}% citation rate")
        if trends["last_week_rate"] is not None:
            lines.append(f"- Last week: {trends['last_week_rate']}% citation rate")
        if trends["trend"]:
            arrow = {"up": "^", "down": "v", "stable": "->"}[trends["trend"]]
            lines.append(f"- Trend: {arrow}")
        lines.append("")

    # Competitor Mentions
    lines.append("## Competitor Mentions")

    # Use today's data for competitor table
    comp_counts_today = defaultdict(int)
    for r in today_rows:
        comp_str = r.get("competitors_cited", "")
        if comp_str:
            for comp in comp_str.split(","):
                comp = comp.strip()
                if comp:
                    comp_counts_today[comp] += 1

    if comp_counts_today:
        lines.append("| Domain | Times Cited | Rate |")
        lines.append("|--------|-------------|------|")
        for comp, count in sorted(comp_counts_today.items(), key=lambda x: -x[1]):
            rate = round(count / len(today_rows) * 100, 1) if today_rows else 0
            lines.append(f"| {comp} | {count} | {rate}% |")
    else:
        lines.append("No competitor domains cited today.")

    lines.append("")
    lines.append("---")
    lines.append(f"*Generated by llm_visibility_tracker.py on {date_str}*")

    report_content = "\n".join(lines)
    report_filename = f"llm-visibility-{date_str}.md"
    report_path = save_report(report_filename, report_content)
    logger.info(f"Report saved to: {report_path}")
    return report_path


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AI Visibility Tracker -- Check if highlevel.ai is cited in LLM responses",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python llm_visibility_tracker.py              Run all queries against all providers
  python llm_visibility_tracker.py --summary    Generate summary from existing CSV data
  python llm_visibility_tracker.py --trends     Show trend analysis only
  python llm_visibility_tracker.py --query "GoHighLevel review 2026"
        """,
    )
    parser.add_argument("--run", action="store_true", default=False,
                        help="Run all queries against all providers (default action)")
    parser.add_argument("--summary", action="store_true",
                        help="Generate summary from existing CSV data (no API calls)")
    parser.add_argument("--trends", action="store_true",
                        help="Show trend analysis only")
    parser.add_argument("--query", type=str, default=None,
                        help="Test a single query against all providers")

    args = parser.parse_args()

    ensure_dirs()

    # Determine action -- if no flags, default to --run
    if args.summary:
        logger.info("Generating summary from existing data...")
        report_path = generate_report()
        if report_path:
            print(f"\nReport saved to: {report_path}")
        trends = analyze_trends()
        print_trends(trends)

    elif args.trends:
        trends = analyze_trends()
        print_trends(trends)

    elif args.query:
        run_single_query(args.query)

    else:
        # Default: run all queries
        run_all_queries()


if __name__ == "__main__":
    main()
