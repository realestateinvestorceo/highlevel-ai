#!/usr/bin/env python3
"""
SEO Dashboard — Unified Google Search Console + GA4 reporting.

Combines GSC search performance data with GA4 engagement and conversion
metrics to produce a single actionable Markdown report.

Usage:
    python seo_dashboard.py
    python seo_dashboard.py --days 90 --ga-days 30
    python seo_dashboard.py --striking-distance
    python seo_dashboard.py --output /path/to/report.md
"""

import sys
import os
import argparse
import json
import pickle
from pathlib import Path
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Config bootstrap
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import *

add_existing_scripts_to_path()

logger = setup_logging("seo_dashboard")

# ---------------------------------------------------------------------------
# Expected CTR by position (for CTR gap analysis)
# ---------------------------------------------------------------------------
EXPECTED_CTR = {
    1: 0.28,
    2: 0.15,
    3: 0.11,
    4: 0.08,
    5: 0.06,
    6: 0.045,
    7: 0.035,
    8: 0.025,
    9: 0.02,
    10: 0.015,
}

HISTORY_FILE = DATA_DIR / "dashboard_history.json"


# ===================================================================
# Google Search Console
# ===================================================================

def _gsc_auth():
    """Authenticate with Google Search Console using stored OAuth2 token."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    creds = None
    token_path = Path(GSC_TOKEN_FILE)

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path))

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            # Persist refreshed token
            with open(token_path, "w") as f:
                f.write(creds.to_json())
            logger.info("GSC token refreshed successfully.")
        except Exception as e:
            logger.warning(f"GSC token refresh failed: {e}")
            creds = None

    if not creds or not creds.valid:
        # Attempt interactive flow as fallback
        creds_file = Path(GSC_CREDENTIALS_FILE)
        if not creds_file.exists():
            raise FileNotFoundError(
                f"GSC credentials file not found at {GSC_CREDENTIALS_FILE}. "
                "Cannot authenticate."
            )
        from google_auth_oauthlib.flow import InstalledAppFlow
        scopes = ["https://www.googleapis.com/auth/webmasters.readonly"]
        flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), scopes)
        creds = flow.run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())
        logger.info("GSC token created via interactive flow.")

    from googleapiclient.discovery import build
    service = build("searchconsole", "v1", credentials=creds)
    return service


def _gsc_query(service, start_date: str, end_date: str, dimensions: list,
               row_limit: int = 1000, start_row: int = 0):
    """Execute a single GSC searchAnalytics query and return rows."""
    body = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": dimensions,
        "rowLimit": row_limit,
        "startRow": start_row,
    }
    response = (
        service.searchanalytics()
        .query(siteUrl=GSC_SITE_URL, body=body)
        .execute()
    )
    return response.get("rows", [])


def fetch_gsc_data(days: int = 90) -> dict | None:
    """
    Fetch all GSC data for the dashboard.

    Returns dict with keys: top_queries, top_pages, query_page, overview,
    or None on failure.
    """
    try:
        service = _gsc_auth()
    except Exception as e:
        logger.error(f"GSC authentication failed: {e}")
        return None

    end_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days + 3)).strftime("%Y-%m-%d")

    logger.info(f"Fetching GSC data: {start_date} to {end_date}")

    try:
        top_queries = _gsc_query(
            service, start_date, end_date,
            dimensions=["query"],
            row_limit=1000,
        )
        logger.info(f"  Top queries: {len(top_queries)} rows")

        top_pages = _gsc_query(
            service, start_date, end_date,
            dimensions=["page"],
            row_limit=500,
        )
        logger.info(f"  Top pages: {len(top_pages)} rows")

        query_page = _gsc_query(
            service, start_date, end_date,
            dimensions=["query", "page"],
            row_limit=5000,
        )
        logger.info(f"  Query+Page: {len(query_page)} rows")

        # Compute aggregate overview
        total_clicks = sum(r.get("clicks", 0) for r in top_queries)
        total_impressions = sum(r.get("impressions", 0) for r in top_queries)
        avg_ctr = total_clicks / total_impressions if total_impressions else 0
        positions = [r.get("position", 0) for r in top_queries if r.get("impressions", 0) > 0]
        avg_position = sum(positions) / len(positions) if positions else 0

        return {
            "top_queries": top_queries,
            "top_pages": top_pages,
            "query_page": query_page,
            "overview": {
                "clicks": total_clicks,
                "impressions": total_impressions,
                "ctr": avg_ctr,
                "position": round(avg_position, 1),
            },
            "start_date": start_date,
            "end_date": end_date,
        }

    except Exception as e:
        logger.error(f"GSC data fetch failed: {e}")
        return None


# ===================================================================
# Google Analytics 4
# ===================================================================

def _ga4_client():
    """Create an authenticated GA4 BetaAnalyticsDataClient."""
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = GA4_SERVICE_ACCOUNT_FILE
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    return BetaAnalyticsDataClient()


def _ga4_run_report(client, start_date: str, end_date: str,
                    dimensions: list[str], metrics: list[str],
                    dimension_filter=None, limit: int = 10000):
    """Run a single GA4 report and return parsed rows."""
    from google.analytics.data_v1beta.types import (
        RunReportRequest, DateRange, Dimension, Metric,
    )

    request = RunReportRequest(
        property=f"properties/{GA4_PROPERTY_ID}",
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        dimensions=[Dimension(name=d) for d in dimensions],
        metrics=[Metric(name=m) for m in metrics],
        limit=limit,
    )
    if dimension_filter:
        request.dimension_filter = dimension_filter

    response = client.run_report(request)

    rows = []
    for row in response.rows:
        entry = {}
        for i, dim in enumerate(dimensions):
            entry[dim] = row.dimension_values[i].value
        for i, met in enumerate(metrics):
            val = row.metric_values[i].value
            # Try to parse as number
            try:
                entry[met] = int(val)
            except ValueError:
                try:
                    entry[met] = float(val)
                except ValueError:
                    entry[met] = val
        rows.append(entry)
    return rows


def fetch_ga4_data(days: int = 30) -> dict | None:
    """
    Fetch all GA4 data for the dashboard.

    Returns dict with keys: overview, pages, traffic_sources, cta_clicks,
    scroll_depth, organic_sessions, or None on failure.
    """
    try:
        client = _ga4_client()
    except Exception as e:
        logger.error(f"GA4 authentication failed: {e}")
        return None

    end_date = "today"
    start_date = f"{days}daysAgo"

    logger.info(f"Fetching GA4 data: {start_date} to {end_date}")

    from google.analytics.data_v1beta.types import (
        FilterExpression, Filter,
    )

    try:
        # --- Overview ---
        overview_rows = _ga4_run_report(
            client, start_date, end_date,
            dimensions=[],
            metrics=[
                "sessions", "totalUsers", "screenPageViews",
                "bounceRate", "averageSessionDuration",
            ],
        )
        overview = overview_rows[0] if overview_rows else {}
        logger.info("  GA4 overview fetched.")

        # --- Pages ---
        pages = _ga4_run_report(
            client, start_date, end_date,
            dimensions=["pagePath"],
            metrics=[
                "sessions", "screenPageViews",
                "averageSessionDuration", "bounceRate",
            ],
            limit=500,
        )
        logger.info(f"  GA4 pages: {len(pages)} rows")

        # --- Traffic sources ---
        traffic_sources = _ga4_run_report(
            client, start_date, end_date,
            dimensions=["sessionSource", "sessionMedium"],
            metrics=["sessions"],
            limit=100,
        )
        logger.info(f"  GA4 traffic sources: {len(traffic_sources)} rows")

        # --- CTA clicks (event: cta_click) ---
        cta_filter = FilterExpression(
            filter=Filter(
                field_name="eventName",
                string_filter=Filter.StringFilter(
                    value="cta_click",
                    match_type=Filter.StringFilter.MatchType.EXACT,
                ),
            )
        )
        cta_clicks = _ga4_run_report(
            client, start_date, end_date,
            dimensions=["pagePath", "customEvent:event_label"],
            metrics=["eventCount"],
            dimension_filter=cta_filter,
            limit=500,
        )
        logger.info(f"  GA4 CTA clicks: {len(cta_clicks)} rows")

        # --- Scroll depth (event: scroll_depth) ---
        scroll_filter = FilterExpression(
            filter=Filter(
                field_name="eventName",
                string_filter=Filter.StringFilter(
                    value="scroll_depth",
                    match_type=Filter.StringFilter.MatchType.EXACT,
                ),
            )
        )
        scroll_depth = _ga4_run_report(
            client, start_date, end_date,
            dimensions=["pagePath", "customEvent:scroll_percentage"],
            metrics=["eventCount"],
            dimension_filter=scroll_filter,
            limit=1000,
        )
        logger.info(f"  GA4 scroll depth: {len(scroll_depth)} rows")

        # --- Organic sessions (source = google) ---
        organic_filter = FilterExpression(
            filter=Filter(
                field_name="sessionSource",
                string_filter=Filter.StringFilter(
                    value="google",
                    match_type=Filter.StringFilter.MatchType.EXACT,
                ),
            )
        )
        organic_sessions = _ga4_run_report(
            client, start_date, end_date,
            dimensions=["pagePath"],
            metrics=["sessions"],
            dimension_filter=organic_filter,
            limit=500,
        )
        logger.info(f"  GA4 organic sessions: {len(organic_sessions)} rows")

        return {
            "overview": overview,
            "pages": pages,
            "traffic_sources": traffic_sources,
            "cta_clicks": cta_clicks,
            "scroll_depth": scroll_depth,
            "organic_sessions": organic_sessions,
        }

    except Exception as e:
        logger.error(f"GA4 data fetch failed: {e}")
        return None


# ===================================================================
# Analysis Functions
# ===================================================================

def _normalize_url(url: str) -> str:
    """Strip the domain from a full URL to get just the path."""
    if url.startswith("http"):
        from urllib.parse import urlparse
        return urlparse(url).path
    return url


def analyze_striking_distance(gsc_data: dict) -> list[dict]:
    """
    Find queries with average position 5-20 and impressions > 50.
    These are 'striking distance' keywords ripe for optimization.
    """
    results = []
    for row in gsc_data.get("query_page", []):
        keys = row.get("keys", [])
        if len(keys) < 2:
            continue
        query, page = keys[0], keys[1]
        pos = row.get("position", 0)
        impr = row.get("impressions", 0)
        clicks = row.get("clicks", 0)
        ctr = row.get("ctr", 0)

        if 5 <= pos <= 20 and impr > 50:
            results.append({
                "query": query,
                "page": _normalize_url(page),
                "position": round(pos, 1),
                "impressions": impr,
                "clicks": clicks,
                "ctr": round(ctr * 100, 2),
            })

    results.sort(key=lambda x: x["impressions"], reverse=True)
    return results


def analyze_ctr_gaps(gsc_data: dict) -> list[dict]:
    """
    Find pages where actual CTR is >30% below expected for their position.
    """
    gaps = []
    for row in gsc_data.get("top_pages", []):
        keys = row.get("keys", [])
        if not keys:
            continue
        page = keys[0]
        pos = row.get("position", 0)
        actual_ctr = row.get("ctr", 0)
        impr = row.get("impressions", 0)

        if impr < 20:
            continue

        rounded_pos = max(1, min(10, round(pos)))
        expected = EXPECTED_CTR.get(rounded_pos, 0.01)

        if expected > 0 and actual_ctr < expected * 0.7:
            gap = expected - actual_ctr
            gaps.append({
                "page": _normalize_url(page),
                "position": round(pos, 1),
                "expected_ctr": round(expected * 100, 1),
                "actual_ctr": round(actual_ctr * 100, 2),
                "gap": round(gap * 100, 1),
                "impressions": impr,
            })

    gaps.sort(key=lambda x: x["impressions"], reverse=True)
    return gaps


def analyze_engagement(gsc_data: dict, ga4_data: dict) -> dict:
    """
    Cross-reference GSC pages with GA4 engagement data.

    Returns dict with:
        low_engagement: pages with high impressions but low session duration
        low_scroll: pages with high traffic but most users don't reach 50%
    """
    # Build GA4 lookup by path
    ga4_page_map = {}
    for p in ga4_data.get("pages", []):
        path = p.get("pagePath", "")
        ga4_page_map[path] = p

    # Build scroll depth lookup — aggregate by page
    scroll_map = {}  # path -> {threshold: count}
    for s in ga4_data.get("scroll_depth", []):
        path = s.get("pagePath", "")
        pct = s.get("customEvent:scroll_percentage", "0")
        count = s.get("eventCount", 0)
        if path not in scroll_map:
            scroll_map[path] = {}
        scroll_map[path][pct] = count

    low_engagement = []
    low_scroll = []

    for row in gsc_data.get("top_pages", []):
        keys = row.get("keys", [])
        if not keys:
            continue
        full_url = keys[0]
        path = _normalize_url(full_url)
        impr = row.get("impressions", 0)

        if impr < 50:
            continue

        ga4_info = ga4_page_map.get(path, {})
        avg_duration = ga4_info.get("averageSessionDuration", None)
        sessions = ga4_info.get("sessions", 0)

        if avg_duration is not None and avg_duration < 30 and sessions > 10:
            low_engagement.append({
                "page": path,
                "impressions": impr,
                "sessions": sessions,
                "avg_duration": round(avg_duration, 1),
            })

        # Scroll analysis
        if path in scroll_map:
            scroll_data = scroll_map[path]
            total_events = sum(scroll_data.values())
            deep_events = sum(
                v for k, v in scroll_data.items()
                if _parse_int(k, 0) >= 50
            )
            if total_events > 10:
                deep_pct = deep_events / total_events
                if deep_pct < 0.5:
                    low_scroll.append({
                        "page": path,
                        "impressions": impr,
                        "scroll_events": total_events,
                        "reached_50pct": round(deep_pct * 100, 1),
                    })

    low_engagement.sort(key=lambda x: x["impressions"], reverse=True)
    low_scroll.sort(key=lambda x: x["impressions"], reverse=True)

    return {"low_engagement": low_engagement, "low_scroll": low_scroll}


def _parse_int(val, default=0):
    """Safely parse an int from a string."""
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def analyze_conversion_funnel(ga4_data: dict) -> dict:
    """
    Build the organic conversion funnel:
      organic sessions -> CTA clicks -> rate
    """
    total_organic = sum(
        r.get("sessions", 0) for r in ga4_data.get("organic_sessions", [])
    )

    # CTA clicks per page
    cta_by_page = {}
    total_cta = 0
    for row in ga4_data.get("cta_clicks", []):
        path = row.get("pagePath", "")
        count = row.get("eventCount", 0)
        total_cta += count
        cta_by_page[path] = cta_by_page.get(path, 0) + count

    cta_rate = (total_cta / total_organic * 100) if total_organic else 0

    # Per-page funnel
    organic_by_page = {}
    for row in ga4_data.get("organic_sessions", []):
        path = row.get("pagePath", "")
        organic_by_page[path] = row.get("sessions", 0)

    page_funnels = []
    for path, cta_count in sorted(cta_by_page.items(), key=lambda x: x[1], reverse=True):
        org = organic_by_page.get(path, 0)
        rate = (cta_count / org * 100) if org else 0
        page_funnels.append({
            "page": path,
            "organic_sessions": org,
            "cta_clicks": cta_count,
            "cta_rate": round(rate, 2),
        })

    return {
        "total_organic_sessions": total_organic,
        "total_cta_clicks": total_cta,
        "overall_cta_rate": round(cta_rate, 2),
        "by_page": page_funnels[:20],
    }


def analyze_trends(gsc_data: dict, ga4_data: dict) -> dict:
    """
    Compare current data with the previous snapshot stored in
    data/dashboard_history.json. Return week-over-week deltas.
    """
    current = {
        "date": today(),
        "gsc_clicks": gsc_data["overview"]["clicks"] if gsc_data else 0,
        "gsc_impressions": gsc_data["overview"]["impressions"] if gsc_data else 0,
        "ga4_sessions": ga4_data["overview"].get("sessions", 0) if ga4_data else 0,
        "ga4_cta_clicks": sum(
            r.get("eventCount", 0) for r in ga4_data.get("cta_clicks", [])
        ) if ga4_data else 0,
    }

    history = load_json(HISTORY_FILE, default={"snapshots": []})
    previous = history["snapshots"][-1] if history["snapshots"] else None

    deltas = {}
    alerts = []
    if previous:
        for key in ["gsc_clicks", "gsc_impressions", "ga4_sessions", "ga4_cta_clicks"]:
            old_val = previous.get(key, 0)
            new_val = current[key]
            if old_val > 0:
                pct_change = (new_val - old_val) / old_val * 100
            else:
                pct_change = 0 if new_val == 0 else 100
            deltas[key] = {
                "previous": old_val,
                "current": new_val,
                "change_pct": round(pct_change, 1),
            }
            if pct_change < -20:
                alerts.append(f"{key} dropped {abs(round(pct_change, 1))}% vs previous snapshot")

    # Save current snapshot
    history["snapshots"].append(current)
    # Keep only last 52 snapshots (approx 1 year of weekly)
    history["snapshots"] = history["snapshots"][-52:]
    save_json(HISTORY_FILE, history)

    return {"deltas": deltas, "alerts": alerts, "previous_date": previous.get("date") if previous else None}


# ===================================================================
# Report Generation
# ===================================================================

def _fmt_pct(val, already_percent=False):
    """Format a percentage value."""
    if already_percent:
        return f"{val:.1f}%"
    return f"{val * 100:.1f}%"


def _fmt_num(val):
    """Format a number with commas."""
    if isinstance(val, float):
        return f"{val:,.1f}"
    return f"{val:,}"


def _trend_arrow(change_pct):
    """Return a directional indicator for the trend."""
    if change_pct > 5:
        return f"+{change_pct:.1f}%"
    elif change_pct < -5:
        return f"{change_pct:.1f}%"
    else:
        return f"{change_pct:+.1f}%"


def generate_report(gsc_data, ga4_data, striking, ctr_gaps, engagement,
                    funnel, trends, gsc_days, ga4_days) -> str:
    """Generate the full Markdown dashboard report."""
    lines = []

    report_date = today()
    timestamp = now()

    gsc_start = gsc_data["start_date"] if gsc_data else "N/A"
    gsc_end = gsc_data["end_date"] if gsc_data else "N/A"

    lines.append(f"# SEO Dashboard -- {report_date}")
    lines.append(f"Generated: {timestamp}")
    lines.append(f"GSC Period: {gsc_start} to {gsc_end} ({gsc_days} days)")
    lines.append(f"GA4 Period: last {ga4_days} days")
    lines.append("")

    # ---- Priority Actions ----
    lines.append("## Priority Actions")
    actions = _generate_priority_actions(
        gsc_data, ga4_data, striking, ctr_gaps, engagement, funnel, trends
    )
    for i, action in enumerate(actions[:5], 1):
        lines.append(f"{i}. {action}")
    lines.append("")

    # ---- Trend Alerts ----
    if trends.get("alerts"):
        lines.append("## Trend Alerts")
        for alert in trends["alerts"]:
            lines.append(f"- WARNING: {alert}")
        lines.append("")

    # ---- Search Performance (GSC) ----
    lines.append("## Search Performance (GSC)")

    if gsc_data:
        ov = gsc_data["overview"]
        lines.append("### Overview")
        lines.append("| Metric | Value | vs Previous |")
        lines.append("|--------|------:|-------------|")

        deltas = trends.get("deltas", {})
        for label, key, fmt_fn in [
            ("Total Clicks", "gsc_clicks", _fmt_num),
            ("Total Impressions", "gsc_impressions", _fmt_num),
        ]:
            val = ov.get(key.replace("gsc_", ""), 0)
            d = deltas.get(key, {})
            vs = _trend_arrow(d["change_pct"]) if d else "--"
            lines.append(f"| {label} | {fmt_fn(val)} | {vs} |")

        lines.append(f"| Avg CTR | {_fmt_pct(ov['ctr'])} | -- |")
        lines.append(f"| Avg Position | {ov['position']} | -- |")
        lines.append("")

        # Top queries
        lines.append("### Top Queries (by Impressions)")
        lines.append("| Query | Clicks | Impressions | CTR | Position |")
        lines.append("|-------|-------:|------------:|----:|---------:|")
        for row in gsc_data["top_queries"][:20]:
            q = row.get("keys", [""])[0]
            lines.append(
                f"| {q} | {row.get('clicks', 0):,} | {row.get('impressions', 0):,} "
                f"| {_fmt_pct(row.get('ctr', 0))} | {row.get('position', 0):.1f} |"
            )
        lines.append("")

        # Striking distance
        lines.append("### Striking Distance Keywords (Quick Wins)")
        if striking:
            lines.append("| Query | Position | Impressions | Clicks | CTR | Page |")
            lines.append("|-------|--------:|-----------:|------:|----:|------|")
            for kw in striking[:25]:
                lines.append(
                    f"| {kw['query']} | {kw['position']} | {kw['impressions']:,} "
                    f"| {kw['clicks']:,} | {kw['ctr']}% | {kw['page']} |"
                )
        else:
            lines.append("_No striking distance keywords found._")
        lines.append("")

        # CTR gaps
        lines.append("### CTR Below Expected")
        if ctr_gaps:
            lines.append("| Page | Position | Expected CTR | Actual CTR | Gap | Impressions |")
            lines.append("|------|--------:|------------:|-----------:|----:|-----------:|")
            for g in ctr_gaps[:15]:
                lines.append(
                    f"| {g['page']} | {g['position']} | {g['expected_ctr']}% "
                    f"| {g['actual_ctr']}% | -{g['gap']}% | {g['impressions']:,} |"
                )
        else:
            lines.append("_No significant CTR gaps detected._")
        lines.append("")

        # Top pages
        lines.append("### Top Pages (by Clicks)")
        lines.append("| Page | Clicks | Impressions | CTR | Position |")
        lines.append("|------|-------:|------------:|----:|---------:|")
        sorted_pages = sorted(
            gsc_data["top_pages"], key=lambda r: r.get("clicks", 0), reverse=True
        )
        for row in sorted_pages[:20]:
            p = _normalize_url(row.get("keys", [""])[0])
            lines.append(
                f"| {p} | {row.get('clicks', 0):,} | {row.get('impressions', 0):,} "
                f"| {_fmt_pct(row.get('ctr', 0))} | {row.get('position', 0):.1f} |"
            )
        lines.append("")
    else:
        lines.append("_GSC data unavailable. Check authentication._")
        lines.append("")

    # ---- Site Engagement (GA4) ----
    lines.append("## Site Engagement (GA4)")

    if ga4_data:
        ov = ga4_data["overview"]
        lines.append("### Overview")
        lines.append("| Metric | Value |")
        lines.append("|--------|------:|")

        deltas = trends.get("deltas", {})
        sessions = ov.get("sessions", 0)
        d = deltas.get("ga4_sessions", {})
        vs = f" ({_trend_arrow(d['change_pct'])})" if d else ""
        lines.append(f"| Sessions | {_fmt_num(sessions)}{vs} |")
        lines.append(f"| Total Users | {_fmt_num(ov.get('totalUsers', 0))} |")
        lines.append(f"| Page Views | {_fmt_num(ov.get('screenPageViews', 0))} |")
        bounce = ov.get("bounceRate", 0)
        lines.append(f"| Bounce Rate | {_fmt_pct(bounce, already_percent=isinstance(bounce, str))} |")
        dur = ov.get("averageSessionDuration", 0)
        lines.append(f"| Avg Session Duration | {dur:.0f}s |")
        lines.append("")

        # Top pages by traffic
        lines.append("### Top Pages by Traffic")
        lines.append("| Page | Sessions | Page Views | Avg Duration | Bounce Rate |")
        lines.append("|------|--------:|----------:|-----------:|----------:|")
        sorted_ga_pages = sorted(
            ga4_data["pages"], key=lambda r: r.get("sessions", 0), reverse=True
        )
        for p in sorted_ga_pages[:20]:
            path = p.get("pagePath", "")
            dur_val = p.get("averageSessionDuration", 0)
            br = p.get("bounceRate", 0)
            lines.append(
                f"| {path} | {p.get('sessions', 0):,} | {p.get('screenPageViews', 0):,} "
                f"| {dur_val:.0f}s | {_fmt_pct(br, already_percent=isinstance(br, str))} |"
            )
        lines.append("")

        # Low engagement pages
        lines.append("### Low Engagement Pages")
        low_eng = engagement.get("low_engagement", [])
        if low_eng:
            lines.append("_Pages with high impressions but <30s avg session duration._")
            lines.append("| Page | Impressions | Sessions | Avg Duration |")
            lines.append("|------|-----------:|--------:|-----------:|")
            for p in low_eng[:15]:
                lines.append(
                    f"| {p['page']} | {p['impressions']:,} | {p['sessions']:,} "
                    f"| {p['avg_duration']}s |"
                )
        else:
            lines.append("_No low-engagement pages detected._")
        lines.append("")

        # Low scroll pages
        low_scroll = engagement.get("low_scroll", [])
        if low_scroll:
            lines.append("### Low Scroll Depth Pages")
            lines.append("_Pages where most users don't reach 50% scroll._")
            lines.append("| Page | Impressions | Scroll Events | Reached 50% |")
            lines.append("|------|-----------:|-------------:|----------:|")
            for p in low_scroll[:15]:
                lines.append(
                    f"| {p['page']} | {p['impressions']:,} | {p['scroll_events']:,} "
                    f"| {p['reached_50pct']}% |"
                )
        lines.append("")
    else:
        lines.append("_GA4 data unavailable. Check authentication._")
        lines.append("")

    # ---- Conversion Funnel ----
    lines.append("## Conversion Funnel")
    if ga4_data and funnel:
        lines.append("| Stage | Count | Rate |")
        lines.append("|-------|------:|-----:|")
        lines.append(f"| Organic Sessions | {funnel['total_organic_sessions']:,} | -- |")
        lines.append(
            f"| CTA Clicks | {funnel['total_cta_clicks']:,} "
            f"| {funnel['overall_cta_rate']}% |"
        )
        lines.append("")

        if funnel["by_page"]:
            lines.append("### CTA Clicks by Page")
            lines.append("| Page | Organic Sessions | CTA Clicks | Rate |")
            lines.append("|------|----------------:|----------:|-----:|")
            for p in funnel["by_page"][:15]:
                lines.append(
                    f"| {p['page']} | {p['organic_sessions']:,} "
                    f"| {p['cta_clicks']:,} | {p['cta_rate']}% |"
                )
            lines.append("")
    else:
        lines.append("_Conversion funnel data unavailable._")
        lines.append("")

    # ---- Traffic Sources ----
    lines.append("## Traffic Sources")
    if ga4_data:
        lines.append("| Source | Medium | Sessions |")
        lines.append("|--------|--------|--------:|")
        sorted_sources = sorted(
            ga4_data["traffic_sources"],
            key=lambda r: r.get("sessions", 0),
            reverse=True,
        )
        for s in sorted_sources[:20]:
            lines.append(
                f"| {s.get('sessionSource', '')} | {s.get('sessionMedium', '')} "
                f"| {s.get('sessions', 0):,} |"
            )
        lines.append("")
    else:
        lines.append("_Traffic source data unavailable._")
        lines.append("")

    # ---- Week-over-Week ----
    if trends.get("deltas"):
        lines.append("## Week-over-Week Comparison")
        prev_date = trends.get("previous_date", "unknown")
        lines.append(f"_Compared to snapshot from {prev_date}_")
        lines.append("| Metric | Previous | Current | Change |")
        lines.append("|--------|--------:|-------:|-------:|")
        label_map = {
            "gsc_clicks": "GSC Clicks",
            "gsc_impressions": "GSC Impressions",
            "ga4_sessions": "GA4 Sessions",
            "ga4_cta_clicks": "CTA Clicks",
        }
        for key, d in trends["deltas"].items():
            label = label_map.get(key, key)
            lines.append(
                f"| {label} | {_fmt_num(d['previous'])} | {_fmt_num(d['current'])} "
                f"| {_trend_arrow(d['change_pct'])} |"
            )
        lines.append("")

    lines.append("---")
    lines.append(f"_Report generated by seo_dashboard.py | {timestamp}_")

    return "\n".join(lines)


def _generate_priority_actions(gsc_data, ga4_data, striking, ctr_gaps,
                               engagement, funnel, trends) -> list[str]:
    """Generate top-5 priority actions based on analysis."""
    actions = []

    # Trend alerts are highest priority
    for alert in trends.get("alerts", []):
        actions.append(f"INVESTIGATE: {alert}")

    # Striking distance quick wins
    if striking:
        top_kw = striking[0]
        actions.append(
            f"Optimize for \"{top_kw['query']}\" (pos {top_kw['position']}, "
            f"{top_kw['impressions']:,} impressions) on {top_kw['page']}"
        )

    # CTR gaps
    if ctr_gaps:
        top_gap = ctr_gaps[0]
        actions.append(
            f"Improve title/description for {top_gap['page']} "
            f"(CTR {top_gap['actual_ctr']}% vs {top_gap['expected_ctr']}% expected, "
            f"{top_gap['impressions']:,} impressions)"
        )

    # Low engagement
    low_eng = engagement.get("low_engagement", [])
    if low_eng:
        worst = low_eng[0]
        actions.append(
            f"Improve content quality on {worst['page']} "
            f"(only {worst['avg_duration']}s avg duration, "
            f"{worst['impressions']:,} impressions)"
        )

    # Low CTA rate
    if funnel and funnel.get("overall_cta_rate", 0) < 2:
        actions.append(
            f"Overall CTA rate is low ({funnel['overall_cta_rate']}%) -- "
            f"review CTA placement and copy across key pages"
        )

    # Low scroll
    low_scroll = engagement.get("low_scroll", [])
    if low_scroll:
        worst = low_scroll[0]
        actions.append(
            f"Fix scroll engagement on {worst['page']} "
            f"(only {worst['reached_50pct']}% reach 50% scroll)"
        )

    if not actions:
        actions.append("No urgent issues detected. Keep monitoring.")

    return actions[:5]


# ===================================================================
# Main
# ===================================================================

def main():
    parser = argparse.ArgumentParser(
        description="SEO Dashboard -- unified GSC + GA4 reporting"
    )
    parser.add_argument(
        "--days", type=int, default=90,
        help="Lookback period for GSC data (default: 90)"
    )
    parser.add_argument(
        "--ga-days", type=int, default=30,
        help="Lookback period for GA4 data (default: 30)"
    )
    parser.add_argument(
        "--striking-distance", action="store_true",
        help="Only show striking distance keywords"
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Custom output path for the report"
    )
    args = parser.parse_args()

    ensure_dirs()

    logger.info("=" * 60)
    logger.info("SEO Dashboard run started")
    logger.info("=" * 60)

    # ---- Fetch data ----
    gsc_data = fetch_gsc_data(days=args.days)
    ga4_data = fetch_ga4_data(days=args.ga_days)

    if not gsc_data and not ga4_data:
        logger.error("Both GSC and GA4 failed. Cannot generate report.")
        print("ERROR: Both data sources failed. Check logs for details.")
        sys.exit(1)

    # ---- Analyze ----
    striking = analyze_striking_distance(gsc_data) if gsc_data else []
    ctr_gaps = analyze_ctr_gaps(gsc_data) if gsc_data else []

    engagement = {}
    if gsc_data and ga4_data:
        engagement = analyze_engagement(gsc_data, ga4_data)

    funnel = analyze_conversion_funnel(ga4_data) if ga4_data else {}
    trends = analyze_trends(gsc_data, ga4_data)

    # ---- Striking-distance-only mode ----
    if args.striking_distance:
        print(f"\nStriking Distance Keywords ({len(striking)} found):\n")
        print(f"{'Query':<50} {'Pos':>5} {'Impr':>8} {'Clicks':>7} {'CTR':>6} Page")
        print("-" * 120)
        for kw in striking[:50]:
            print(
                f"{kw['query']:<50} {kw['position']:>5.1f} {kw['impressions']:>8,} "
                f"{kw['clicks']:>7,} {kw['ctr']:>5.1f}% {kw['page']}"
            )
        return

    # ---- Generate report ----
    report = generate_report(
        gsc_data, ga4_data, striking, ctr_gaps, engagement,
        funnel, trends, args.days, args.ga_days,
    )

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report)
    else:
        report_name = f"seo-dashboard-{today()}.md"
        output_path = save_report(report_name, report)

    logger.info(f"Report saved to {output_path}")
    print(f"\nDashboard report saved to:\n  {output_path}")

    # Print summary to terminal
    print("\n--- Quick Summary ---")
    if gsc_data:
        ov = gsc_data["overview"]
        print(f"  GSC: {ov['clicks']:,} clicks | {ov['impressions']:,} impressions | {_fmt_pct(ov['ctr'])} CTR")
    if ga4_data:
        ov = ga4_data["overview"]
        print(f"  GA4: {_fmt_num(ov.get('sessions', 0))} sessions | {_fmt_num(ov.get('totalUsers', 0))} users")
    print(f"  Striking distance keywords: {len(striking)}")
    print(f"  CTR gaps found: {len(ctr_gaps)}")
    if funnel:
        print(f"  CTA rate: {funnel.get('overall_cta_rate', 0)}%")
    for alert in trends.get("alerts", []):
        print(f"  ALERT: {alert}")


if __name__ == "__main__":
    main()
