#!/usr/bin/env python3
"""
GA4 Performance Analyzer for highlevel.ai

Pulls analytics data from Google Analytics 4 via the Data API
and generates an actionable performance report.

Usage:
    python3 ga4_analyze.py
    python3 ga4_analyze.py --days 14
    python3 ga4_analyze.py --output reports/ga4-report.md
"""

import argparse
import datetime
import os
import sys

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
    OrderBy,
    FilterExpression,
    Filter,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROPERTY_ID = "526181718"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT_FILE = os.path.join(
    os.path.expanduser("~"),
    "Downloads",
    "ai-projects-487616-878fcd9f633c.json",
)
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "reports")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def get_client():
    """Create an authenticated GA4 Data API client."""
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        print(f"ERROR: Service account file not found at {SERVICE_ACCOUNT_FILE}")
        sys.exit(1)

    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = SERVICE_ACCOUNT_FILE
    client = BetaAnalyticsDataClient()
    print(f"Authenticated via service account.")
    return client


def run_report(client, dimensions, metrics, date_range, order_by=None, limit=10, dim_filter=None):
    """Run a GA4 report and return rows."""
    request = RunReportRequest(
        property=f"properties/{PROPERTY_ID}",
        dimensions=[Dimension(name=d) for d in dimensions],
        metrics=[Metric(name=m) for m in metrics],
        date_ranges=[date_range],
        limit=limit,
    )
    if order_by:
        request.order_bys = order_by
    if dim_filter:
        request.dimension_filter = dim_filter

    response = client.run_report(request)
    return response


# ---------------------------------------------------------------------------
# Report Sections
# ---------------------------------------------------------------------------

def get_overview(client, date_range):
    """Site-wide overview metrics."""
    response = run_report(
        client,
        dimensions=["date"],
        metrics=[
            "sessions",
            "totalUsers",
            "screenPageViews",
            "bounceRate",
            "averageSessionDuration",
            "engagedSessions",
            "newUsers",
        ],
        date_range=date_range,
        order_by=[OrderBy(dimension=OrderBy.DimensionOrderBy(dimension_name="date"))],
        limit=100,
    )

    totals = {
        "sessions": 0,
        "users": 0,
        "pageviews": 0,
        "bounce_rate": 0,
        "avg_duration": 0,
        "engaged": 0,
        "new_users": 0,
    }
    daily = []

    for row in response.rows:
        date = row.dimension_values[0].value
        sessions = int(row.metric_values[0].value)
        users = int(row.metric_values[1].value)
        pageviews = int(row.metric_values[2].value)
        bounce = float(row.metric_values[3].value)
        duration = float(row.metric_values[4].value)
        engaged = int(row.metric_values[5].value)
        new_u = int(row.metric_values[6].value)

        totals["sessions"] += sessions
        totals["users"] += users
        totals["pageviews"] += pageviews
        totals["engaged"] += engaged
        totals["new_users"] += new_u

        daily.append({
            "date": date,
            "sessions": sessions,
            "users": users,
            "pageviews": pageviews,
            "bounce_rate": bounce,
            "duration": duration,
        })

    if daily:
        totals["bounce_rate"] = sum(d["bounce_rate"] for d in daily) / len(daily)
        totals["avg_duration"] = sum(d["duration"] for d in daily) / len(daily)

    return totals, daily


def get_top_pages(client, date_range, limit=20):
    """Top pages by pageviews."""
    response = run_report(
        client,
        dimensions=["pagePath"],
        metrics=["screenPageViews", "sessions", "averageSessionDuration", "bounceRate"],
        date_range=date_range,
        order_by=[OrderBy(
            metric=OrderBy.MetricOrderBy(metric_name="screenPageViews"),
            desc=True,
        )],
        limit=limit,
    )

    pages = []
    for row in response.rows:
        pages.append({
            "path": row.dimension_values[0].value,
            "pageviews": int(row.metric_values[0].value),
            "sessions": int(row.metric_values[1].value),
            "avg_duration": float(row.metric_values[2].value),
            "bounce_rate": float(row.metric_values[3].value),
        })
    return pages


def get_traffic_sources(client, date_range, limit=15):
    """Traffic sources breakdown."""
    response = run_report(
        client,
        dimensions=["sessionSource", "sessionMedium"],
        metrics=["sessions", "totalUsers", "bounceRate", "averageSessionDuration"],
        date_range=date_range,
        order_by=[OrderBy(
            metric=OrderBy.MetricOrderBy(metric_name="sessions"),
            desc=True,
        )],
        limit=limit,
    )

    sources = []
    for row in response.rows:
        sources.append({
            "source": row.dimension_values[0].value,
            "medium": row.dimension_values[1].value,
            "sessions": int(row.metric_values[0].value),
            "users": int(row.metric_values[1].value),
            "bounce_rate": float(row.metric_values[2].value),
            "avg_duration": float(row.metric_values[3].value),
        })
    return sources


def get_device_breakdown(client, date_range):
    """Device category breakdown."""
    response = run_report(
        client,
        dimensions=["deviceCategory"],
        metrics=["sessions", "totalUsers", "bounceRate", "averageSessionDuration"],
        date_range=date_range,
        order_by=[OrderBy(
            metric=OrderBy.MetricOrderBy(metric_name="sessions"),
            desc=True,
        )],
        limit=10,
    )

    devices = []
    for row in response.rows:
        devices.append({
            "device": row.dimension_values[0].value,
            "sessions": int(row.metric_values[0].value),
            "users": int(row.metric_values[1].value),
            "bounce_rate": float(row.metric_values[2].value),
            "avg_duration": float(row.metric_values[3].value),
        })
    return devices


def get_countries(client, date_range, limit=10):
    """Top countries."""
    response = run_report(
        client,
        dimensions=["country"],
        metrics=["sessions", "totalUsers"],
        date_range=date_range,
        order_by=[OrderBy(
            metric=OrderBy.MetricOrderBy(metric_name="sessions"),
            desc=True,
        )],
        limit=limit,
    )

    countries = []
    for row in response.rows:
        countries.append({
            "country": row.dimension_values[0].value,
            "sessions": int(row.metric_values[0].value),
            "users": int(row.metric_values[1].value),
        })
    return countries


def get_landing_pages(client, date_range, limit=15):
    """Top landing pages (first page of session)."""
    response = run_report(
        client,
        dimensions=["landingPage"],
        metrics=["sessions", "totalUsers", "bounceRate", "averageSessionDuration"],
        date_range=date_range,
        order_by=[OrderBy(
            metric=OrderBy.MetricOrderBy(metric_name="sessions"),
            desc=True,
        )],
        limit=limit,
    )

    pages = []
    for row in response.rows:
        pages.append({
            "path": row.dimension_values[0].value,
            "sessions": int(row.metric_values[0].value),
            "users": int(row.metric_values[1].value),
            "bounce_rate": float(row.metric_values[2].value),
            "avg_duration": float(row.metric_values[3].value),
        })
    return pages


def get_cta_clicks(client, date_range):
    """CTA click events (if tracking is deployed)."""
    try:
        response = run_report(
            client,
            dimensions=["pagePath", "customEvent:page_section", "customEvent:event_label"],
            metrics=["eventCount"],
            date_range=date_range,
            order_by=[OrderBy(
                metric=OrderBy.MetricOrderBy(metric_name="eventCount"),
                desc=True,
            )],
            limit=50,
            dim_filter=FilterExpression(
                filter=Filter(
                    field_name="eventName",
                    string_filter=Filter.StringFilter(value="cta_click"),
                )
            ),
        )
        clicks = []
        for row in response.rows:
            clicks.append({
                "page": row.dimension_values[0].value,
                "section": row.dimension_values[1].value,
                "label": row.dimension_values[2].value,
                "count": int(row.metric_values[0].value),
            })
        return clicks
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Report Generator
# ---------------------------------------------------------------------------

def fmt_duration(seconds):
    """Format seconds as m:ss."""
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def fmt_pct(value):
    """Format decimal as percentage."""
    return f"{value * 100:.1f}%" if value < 1 else f"{value:.1f}%"


def generate_report(totals, daily, pages, sources, devices, countries,
                    landing_pages, cta_clicks, start_date, end_date):
    """Generate markdown report."""
    lines = []
    lines.append("# GA4 Performance Report: www.highlevel.ai")
    lines.append(f"**Period:** {start_date} to {end_date}")
    lines.append(f"**Generated:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    # Overview
    lines.append("## Overview")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Sessions | {totals['sessions']} |")
    lines.append(f"| Users | {totals['users']} |")
    lines.append(f"| New Users | {totals['new_users']} |")
    lines.append(f"| Pageviews | {totals['pageviews']} |")
    lines.append(f"| Engaged Sessions | {totals['engaged']} |")
    lines.append(f"| Bounce Rate | {fmt_pct(totals['bounce_rate'])} |")
    lines.append(f"| Avg Session Duration | {fmt_duration(totals['avg_duration'])} |")
    lines.append("")

    # Daily trend
    if daily:
        lines.append("## Daily Trend")
        lines.append("")
        lines.append("| Date | Sessions | Users | Pageviews | Bounce Rate | Avg Duration |")
        lines.append("|------|----------|-------|-----------|-------------|--------------|")
        for d in daily:
            lines.append(f"| {d['date']} | {d['sessions']} | {d['users']} | "
                         f"{d['pageviews']} | {fmt_pct(d['bounce_rate'])} | "
                         f"{fmt_duration(d['duration'])} |")
        lines.append("")

    # Top pages
    if pages:
        lines.append("## Top Pages (by pageviews)")
        lines.append("")
        lines.append("| # | Page | Pageviews | Sessions | Bounce Rate | Avg Duration |")
        lines.append("|---|------|-----------|----------|-------------|--------------|")
        for i, p in enumerate(pages, 1):
            lines.append(f"| {i} | {p['path']} | {p['pageviews']} | {p['sessions']} | "
                         f"{fmt_pct(p['bounce_rate'])} | {fmt_duration(p['avg_duration'])} |")
        lines.append("")

    # Landing pages
    if landing_pages:
        lines.append("## Top Landing Pages")
        lines.append("")
        lines.append("| # | Page | Sessions | Users | Bounce Rate | Avg Duration |")
        lines.append("|---|------|----------|-------|-------------|--------------|")
        for i, p in enumerate(landing_pages, 1):
            lines.append(f"| {i} | {p['path']} | {p['sessions']} | {p['users']} | "
                         f"{fmt_pct(p['bounce_rate'])} | {fmt_duration(p['avg_duration'])} |")
        lines.append("")

    # Traffic sources
    if sources:
        lines.append("## Traffic Sources")
        lines.append("")
        lines.append("| # | Source | Medium | Sessions | Users | Bounce Rate | Avg Duration |")
        lines.append("|---|--------|--------|----------|-------|-------------|--------------|")
        for i, s in enumerate(sources, 1):
            lines.append(f"| {i} | {s['source']} | {s['medium']} | {s['sessions']} | "
                         f"{s['users']} | {fmt_pct(s['bounce_rate'])} | "
                         f"{fmt_duration(s['avg_duration'])} |")
        lines.append("")

    # Devices
    if devices:
        total_sessions = sum(d["sessions"] for d in devices)
        lines.append("## Device Breakdown")
        lines.append("")
        lines.append("| Device | Sessions | % | Users | Bounce Rate | Avg Duration |")
        lines.append("|--------|----------|---|-------|-------------|--------------|")
        for d in devices:
            pct = f"{d['sessions']/total_sessions*100:.1f}%" if total_sessions else "0%"
            lines.append(f"| {d['device']} | {d['sessions']} | {pct} | {d['users']} | "
                         f"{fmt_pct(d['bounce_rate'])} | {fmt_duration(d['avg_duration'])} |")
        lines.append("")

    # Countries
    if countries:
        lines.append("## Top Countries")
        lines.append("")
        lines.append("| # | Country | Sessions | Users |")
        lines.append("|---|---------|----------|-------|")
        for i, c in enumerate(countries, 1):
            lines.append(f"| {i} | {c['country']} | {c['sessions']} | {c['users']} |")
        lines.append("")

    # CTA Clicks
    if cta_clicks:
        lines.append("## CTA Clicks (Affiliate Link Events)")
        lines.append("")
        lines.append("| # | Page | Section | CTA Text | Clicks |")
        lines.append("|---|------|---------|----------|--------|")
        for i, c in enumerate(cta_clicks, 1):
            label = c["label"][:60] if c["label"] else "(unknown)"
            lines.append(f"| {i} | {c['page']} | {c['section']} | {label} | {c['count']} |")
        lines.append("")
    else:
        lines.append("## CTA Clicks")
        lines.append("")
        lines.append("No `cta_click` events found. Deploy the CTA tracking snippet to start collecting data.")
        lines.append("")

    # Analysis
    lines.append("---")
    lines.append("")
    lines.append("## Key Takeaways")
    lines.append("")

    if totals["sessions"] == 0:
        lines.append("- **No sessions recorded.** Verify GA4 tag is firing correctly.")
    else:
        if totals["bounce_rate"] > 0.7:
            lines.append(f"- **High bounce rate ({fmt_pct(totals['bounce_rate'])}).** "
                         "Visitors are leaving without engaging. Consider improving above-the-fold content and CTAs.")
        if totals["avg_duration"] < 30:
            lines.append(f"- **Low avg session duration ({fmt_duration(totals['avg_duration'])}).** "
                         "Visitors aren't reading the content. Check page load speed and content relevance.")
        if totals["sessions"] < 50:
            lines.append(f"- **Very low traffic ({totals['sessions']} sessions).** "
                         "Focus on SEO, content marketing, and building backlinks before optimizing conversions.")

        if pages:
            homepage = next((p for p in pages if p["path"] in ["/", "/index.html"]), None)
            if homepage and homepage["pageviews"] > totals["pageviews"] * 0.8:
                lines.append("- **Traffic concentrated on homepage.** Subpages aren't attracting visitors. "
                             "Improve internal linking and target long-tail keywords.")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Analyze Google Analytics 4 data for highlevel.ai"
    )
    parser.add_argument("--days", type=int, default=28,
                        help="Number of days to analyze (default: 28)")
    parser.add_argument("--output", type=str, default=None,
                        help="Save report to file (e.g., reports/ga4-report.md)")
    args = parser.parse_args()

    print("=" * 60)
    print("  GA4 Performance Analyzer — highlevel.ai")
    print("=" * 60)
    print()

    client = get_client()

    end_date = datetime.date.today().isoformat()
    start_date = (datetime.date.today() - datetime.timedelta(days=args.days)).isoformat()
    date_range = DateRange(start_date=start_date, end_date=end_date)

    print(f"Fetching data: {start_date} to {end_date} ({args.days} days)")
    print()

    print("  Fetching overview...")
    totals, daily = get_overview(client, date_range)
    print(f"    {totals['sessions']} sessions, {totals['users']} users, {totals['pageviews']} pageviews")

    print("  Fetching top pages...")
    pages = get_top_pages(client, date_range)
    print(f"    {len(pages)} pages")

    print("  Fetching traffic sources...")
    sources = get_traffic_sources(client, date_range)
    print(f"    {len(sources)} sources")

    print("  Fetching device breakdown...")
    devices = get_device_breakdown(client, date_range)

    print("  Fetching countries...")
    countries = get_countries(client, date_range)

    print("  Fetching landing pages...")
    landing_pages = get_landing_pages(client, date_range)

    print("  Fetching CTA clicks...")
    cta_clicks = get_cta_clicks(client, date_range)
    print(f"    {len(cta_clicks)} CTA click records")

    print()

    report = generate_report(totals, daily, pages, sources, devices, countries,
                             landing_pages, cta_clicks, start_date, end_date)

    print(report)

    if args.output:
        output_path = os.path.join(SCRIPT_DIR, args.output)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w") as f:
            f.write(report)
        print(f"\nReport saved to: {output_path}")
    else:
        # Auto-save with date
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        filename = f"ga4-{datetime.date.today().isoformat()}.md"
        output_path = os.path.join(OUTPUT_DIR, filename)
        with open(output_path, "w") as f:
            f.write(report)
        print(f"\nReport auto-saved to: {output_path}")


if __name__ == "__main__":
    main()
