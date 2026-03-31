#!/usr/bin/env python3
"""
Performance Analyzer — analyzes what's working across published videos
and generates a dynamic "performance context" injected alongside the
master prompt at generation time.

The master prompt (the playbook) never changes. This module builds a
fresh "scouting report" from real YouTube data so the LLM can weight
its decisions toward proven patterns.

Usage:
    python3 scripts/seo/performance_analyzer.py          # print insights
    python3 scripts/seo/performance_analyzer.py --json    # JSON output
"""

import sys
import json
import argparse
from pathlib import Path
from statistics import mean
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (
    VIDEO_SHEET_ID, GA4_SERVICE_ACCOUNT_FILE,
    setup_logging, today
)

logger = setup_logging("performance_analyzer")

MIN_VIDEOS_FOR_INSIGHTS = 5


# ──────────────────────────────────────────────
# Data loading
# ──────────────────────────────────────────────

def get_all_videos_from_sheets():
    """Read all video rows from the HighLevel tab."""
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(GA4_SERVICE_ACCOUNT_FILE, scopes=scopes)
    gc = gspread.authorize(creds)
    ws = gc.open_by_key(VIDEO_SHEET_ID).worksheet("HighLevel")
    rows = ws.get_all_records()

    videos = []
    for row in rows:
        # Only include videos that have YouTube stats
        views = _num(row.get("views", 0))
        video = {
            "title": row.get("title", ""),
            "page_url": row.get("page_url", ""),
            "youtube_video_id": row.get("youtube_video_id", ""),
            "created_date": row.get("created_date", ""),
            "views": views,
            "likes": _num(row.get("likes", 0)),
            "comments": _num(row.get("comments", 0)),
            "ctr": _float(row.get("ctr", 0)),
            "avg_watch_pct": _float(row.get("avg_watch_pct", 0)),
            "hook_type": row.get("hook_type", ""),
            "content_format": row.get("content_format", ""),
            "content_category": row.get("content_category", ""),
            "ghl_feature": row.get("ghl_feature", ""),
            "template_used": row.get("template_used", ""),
            "thumbnail_variation": row.get("thumbnail_variation", ""),
            "accent_color": row.get("accent_color", ""),
            "thumbnail_text": row.get("thumbnail_text", ""),
            "thumbnail_text_word_count": _num(row.get("thumbnail_text_word_count", 0)),
            "video_length_minutes": _float(row.get("video_length_minutes", 0)),
        }
        videos.append(video)

    return videos


def _num(val):
    try:
        return int(str(val).replace(",", "").strip() or 0)
    except (ValueError, TypeError):
        return 0


def _float(val):
    try:
        return float(str(val).replace(",", "").replace("%", "").strip() or 0)
    except (ValueError, TypeError):
        return 0.0


# ──────────────────────────────────────────────
# Analysis
# ──────────────────────────────────────────────

def _group_by(videos, field):
    """Group videos by a field value, ignoring empty values."""
    groups = defaultdict(list)
    for v in videos:
        val = v.get(field, "").strip()
        if val:
            groups[val].append(v)
    return dict(groups)


def _avg(videos, field):
    """Average a numeric field across videos, ignoring zeros."""
    vals = [v[field] for v in videos if v.get(field, 0) > 0]
    return round(mean(vals), 1) if vals else 0


def _composite_score(group, weights=None):
    """Score a group by weighted combination of CTR, retention, and views."""
    if weights is None:
        weights = {"avg_watch_pct": 0.4, "ctr": 0.4, "views": 0.2}
    score = 0
    for metric, weight in weights.items():
        avg_val = _avg(group, metric)
        score += avg_val * weight
    return round(score, 2)


def analyze_video_performance(videos):
    """Analyze video script performance patterns."""
    has_stats = [v for v in videos if v["views"] > 0]
    insights = {"total_videos": len(videos), "videos_with_stats": len(has_stats)}

    if len(has_stats) < MIN_VIDEOS_FOR_INSIGHTS:
        return insights

    # Hook type performance
    by_hook = _group_by(has_stats, "hook_type")
    insights["hooks"] = {}
    for hook, vids in by_hook.items():
        insights["hooks"][hook] = {
            "count": len(vids),
            "avg_ctr": _avg(vids, "ctr"),
            "avg_retention": _avg(vids, "avg_watch_pct"),
            "avg_views": _avg(vids, "views"),
            "score": _composite_score(vids),
        }

    # Content format performance
    by_format = _group_by(has_stats, "content_format")
    insights["formats"] = {}
    for fmt, vids in by_format.items():
        insights["formats"][fmt] = {
            "count": len(vids),
            "avg_ctr": _avg(vids, "ctr"),
            "avg_views": _avg(vids, "views"),
            "score": _composite_score(vids),
        }

    # Content category performance
    by_category = _group_by(has_stats, "content_category")
    insights["categories"] = {}
    for cat, vids in by_category.items():
        insights["categories"][cat] = {
            "count": len(vids),
            "avg_ctr": _avg(vids, "ctr"),
            "avg_views": _avg(vids, "views"),
            "score": _composite_score(vids),
        }

    # GHL feature performance
    by_feature = _group_by(has_stats, "ghl_feature")
    insights["features"] = {}
    for feat, vids in by_feature.items():
        insights["features"][feat] = {
            "count": len(vids),
            "avg_views": _avg(vids, "views"),
        }

    # Video length buckets
    length_buckets = {"0-4min": [], "4-6min": [], "6-8min": [], "8-12min": [], "12+min": []}
    for v in has_stats:
        length = v.get("video_length_minutes", 0)
        if length <= 0:
            continue
        elif length <= 4:
            length_buckets["0-4min"].append(v)
        elif length <= 6:
            length_buckets["4-6min"].append(v)
        elif length <= 8:
            length_buckets["6-8min"].append(v)
        elif length <= 12:
            length_buckets["8-12min"].append(v)
        else:
            length_buckets["12+min"].append(v)

    insights["length_buckets"] = {}
    for bucket, vids in length_buckets.items():
        if vids:
            insights["length_buckets"][bucket] = {
                "count": len(vids),
                "avg_views": _avg(vids, "views"),
                "avg_retention": _avg(vids, "avg_watch_pct"),
            }

    # Top and bottom performers
    sorted_by_views = sorted(has_stats, key=lambda v: v["views"], reverse=True)
    insights["top_3"] = [{"title": v["title"], "views": v["views"], "ctr": v["ctr"], "category": v["content_category"]} for v in sorted_by_views[:3]]
    insights["bottom_3"] = [{"title": v["title"], "views": v["views"], "ctr": v["ctr"], "category": v["content_category"]} for v in sorted_by_views[-3:]]

    return insights


def analyze_thumbnail_performance(videos):
    """Analyze thumbnail performance patterns."""
    has_stats = [v for v in videos if v["views"] > 0]
    insights = {}

    if len(has_stats) < MIN_VIDEOS_FOR_INSIGHTS:
        return insights

    # Template performance
    by_template = _group_by(has_stats, "template_used")
    insights["templates"] = {}
    for tmpl, vids in by_template.items():
        insights["templates"][tmpl] = {
            "count": len(vids),
            "avg_ctr": _avg(vids, "ctr"),
        }

    # Accent color performance
    by_color = _group_by(has_stats, "accent_color")
    insights["colors"] = {}
    for color, vids in by_color.items():
        insights["colors"][color] = {
            "count": len(vids),
            "avg_ctr": _avg(vids, "ctr"),
        }

    # Text word count vs CTR
    by_words = _group_by(has_stats, "thumbnail_text_word_count")
    insights["word_counts"] = {}
    for wc, vids in by_words.items():
        insights["word_counts"][wc] = {
            "count": len(vids),
            "avg_ctr": _avg(vids, "ctr"),
        }

    # A/B variation win rate
    by_var = _group_by(has_stats, "thumbnail_variation")
    insights["variations"] = {}
    for var, vids in by_var.items():
        insights["variations"][var] = {
            "count": len(vids),
            "avg_ctr": _avg(vids, "ctr"),
        }

    return insights


def analyze_all(videos):
    """Run all analyses and return combined insights."""
    return {
        "video": analyze_video_performance(videos),
        "thumbnail": analyze_thumbnail_performance(videos),
    }


# ──────────────────────────────────────────────
# Context generation
# ──────────────────────────────────────────────

def generate_performance_context(insights, context_type="video"):
    """Generate a human-readable performance context block for prompt injection."""
    vi = insights.get("video", {})
    ti = insights.get("thumbnail", {})

    total = vi.get("videos_with_stats", 0)
    if total < MIN_VIDEOS_FOR_INSIGHTS:
        return (
            "PERFORMANCE CONTEXT: Insufficient data (fewer than "
            f"{MIN_VIDEOS_FOR_INSIGHTS} published videos with analytics). "
            "Use master prompt defaults for all decisions."
        )

    lines = [f"PERFORMANCE CONTEXT (auto-generated from {total} videos, updated {today()}):"]
    lines.append("")

    if context_type == "video":
        # Hook type ranking
        hooks = vi.get("hooks", {})
        if hooks:
            ranked = sorted(hooks.items(), key=lambda x: x[1]["score"], reverse=True)
            best = ranked[0]
            worst = ranked[-1]
            lines.append(f"Best hook type: \"{best[0]}\" — {best[1]['avg_ctr']}% CTR, {best[1]['avg_retention']}% retention ({best[1]['count']} videos)")
            lines.append(f"Weakest hook type: \"{worst[0]}\" — {worst[1]['avg_ctr']}% CTR, {worst[1]['avg_retention']}% retention ({worst[1]['count']} videos)")
            lines.append(f"-> Weight toward {ranked[0][0]}" + (f" and {ranked[1][0]}" if len(ranked) > 1 else "") + " hooks.")
            lines.append("")

        # Content format ranking
        formats = vi.get("formats", {})
        if formats:
            ranked = sorted(formats.items(), key=lambda x: x[1]["score"], reverse=True)
            lines.append(f"Best content format: \"{ranked[0][0]}\" ({ranked[0][1]['avg_views']} avg views)")
            lines.append("")

        # Category ranking
        categories = vi.get("categories", {})
        if categories:
            ranked = sorted(categories.items(), key=lambda x: x[1]["score"], reverse=True)
            lines.append(f"Top category: \"{ranked[0][0]}\" ({ranked[0][1]['avg_views']} avg views, {ranked[0][1]['avg_ctr']}% CTR)")
            if len(ranked) > 1:
                lines.append(f"Weakest category: \"{ranked[-1][0]}\" ({ranked[-1][1]['avg_views']} avg views)")
            lines.append("")

        # Length sweet spot
        buckets = vi.get("length_buckets", {})
        if buckets:
            best_bucket = max(buckets.items(), key=lambda x: x[1].get("avg_retention", 0))
            lines.append(f"Best video length: {best_bucket[0]} ({best_bucket[1]['avg_retention']}% avg retention)")
            lines.append("")

        # Top 3
        top = vi.get("top_3", [])
        if top:
            lines.append("Top 3 videos by views:")
            for v in top:
                lines.append(f'  - "{v["title"]}" ({v["views"]} views, {v["ctr"]}% CTR, {v["category"]})')
            lines.append("")

        # Bottom 3
        bottom = vi.get("bottom_3", [])
        if bottom:
            lines.append("Bottom 3 videos (learn from these):")
            for v in bottom:
                lines.append(f'  - "{v["title"]}" ({v["views"]} views, {v["ctr"]}% CTR, {v["category"]})')
            lines.append("")

    elif context_type == "thumbnail":
        templates = ti.get("templates", {})
        if templates:
            ranked = sorted(templates.items(), key=lambda x: x[1]["avg_ctr"], reverse=True)
            lines.append(f"Best thumbnail template: \"{ranked[0][0]}\" ({ranked[0][1]['avg_ctr']}% CTR)")
            if len(ranked) > 1:
                lines.append(f"Weakest template: \"{ranked[-1][0]}\" ({ranked[-1][1]['avg_ctr']}% CTR)")
            lines.append("")

        colors = ti.get("colors", {})
        if colors:
            ranked = sorted(colors.items(), key=lambda x: x[1]["avg_ctr"], reverse=True)
            lines.append(f"Best accent color: \"{ranked[0][0]}\" ({ranked[0][1]['avg_ctr']}% CTR)")
            lines.append("")

        word_counts = ti.get("word_counts", {})
        if word_counts:
            ranked = sorted(word_counts.items(), key=lambda x: x[1]["avg_ctr"], reverse=True)
            lines.append(f"Best text word count: {ranked[0][0]} words ({ranked[0][1]['avg_ctr']}% CTR)")
            lines.append("")

    lines.append("Use these insights to inform your choices. Do not mention these analytics in the output — they are internal guidance only.")

    return "\n".join(lines)


def generate_weekly_summary(insights):
    """Generate a Slack-friendly weekly performance summary."""
    vi = insights.get("video", {})
    total = vi.get("videos_with_stats", 0)

    if total < MIN_VIDEOS_FOR_INSIGHTS:
        return f"Video Performance: {vi.get('total_videos', 0)} videos published, waiting for {MIN_VIDEOS_FOR_INSIGHTS}+ with stats for insights."

    top = vi.get("top_3", [])
    best_title = top[0]["title"] if top else "N/A"
    best_views = top[0]["views"] if top else 0

    hooks = vi.get("hooks", {})
    best_hook = max(hooks.items(), key=lambda x: x[1]["score"])[0] if hooks else "N/A"

    categories = vi.get("categories", {})
    best_cat = max(categories.items(), key=lambda x: x[1]["score"])[0] if categories else "N/A"

    return (
        f"Weekly Video Performance ({total} videos with data):\n"
        f"Best performer: \"{best_title}\" ({best_views:,} views)\n"
        f"Top hook type: {best_hook} | Top category: {best_cat}\n"
    )


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Analyze video performance and generate context.")
    parser.add_argument("--json", action="store_true", help="Output raw JSON insights")
    parser.add_argument("--context", choices=["video", "thumbnail"], default="video", help="Which context to generate")
    args = parser.parse_args()

    videos = get_all_videos_from_sheets()
    logger.info(f"Loaded {len(videos)} videos from Sheets")

    insights = analyze_all(videos)

    if args.json:
        print(json.dumps(insights, indent=2))
    else:
        context = generate_performance_context(insights, context_type=args.context)
        print(context)
        print("\n---\n")
        print(generate_weekly_summary(insights))


if __name__ == "__main__":
    main()
