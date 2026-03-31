#!/usr/bin/env python3
"""
Research Video Topic — auto-select the best topic for today's video.

Pulls from GSC rising queries, GA4 top pages, and the topic pool.
Scores candidates and returns a full set of variables for the master prompt.

Usage:
    python3 scripts/seo/research_video_topic.py
    python3 scripts/seo/research_video_topic.py --json
"""

import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (
    VIDEO_SHEET_ID, GA4_SERVICE_ACCOUNT_FILE,
    DATA_DIR, setup_logging, today
)

logger = setup_logging("research_video_topic")


def load_topic_pool():
    """Load the seed topic pool from JSON."""
    pool_path = DATA_DIR / "video_topics.json"
    if not pool_path.exists():
        logger.warning("video_topics.json not found")
        return []
    with open(pool_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_covered_topics():
    """Get topics already covered (from Sheets HighLevel tab)."""
    covered = set()
    try:
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
        for row in rows:
            title = (row.get("title", "") or "").lower().strip()
            page = (row.get("page_url", "") or "").lower().strip()
            if title:
                covered.add(title)
            if page:
                covered.add(page)
    except Exception as e:
        logger.warning(f"Could not read Sheets for covered topics: {e}")
    return covered


def get_performance_multipliers():
    """Get category performance multipliers from the analyzer."""
    multipliers = {}
    try:
        from performance_analyzer import get_all_videos_from_sheets, analyze_video_performance
        videos = get_all_videos_from_sheets()
        insights = analyze_video_performance(videos)
        categories = insights.get("categories", {})
        if categories:
            scores = {cat: data["score"] for cat, data in categories.items()}
            avg_score = sum(scores.values()) / len(scores) if scores else 1
            for cat, score in scores.items():
                if avg_score > 0:
                    multipliers[cat] = round(max(0.5, min(2.0, score / avg_score)), 2)
    except Exception as e:
        logger.warning(f"Could not load performance data: {e}")
    return multipliers


def score_topics(topics, covered, multipliers):
    """Score and rank topics, filtering out already-covered ones."""
    scored = []
    for topic in topics:
        keyword = topic.get("keyword", "").lower()
        page_url = topic.get("page_url", "").lower()

        # Skip if already covered
        if any(keyword in c or page_url in c for c in covered if c):
            continue

        # Base score (all topics start equal from pool)
        base_score = 10.0

        # Boost by search intent
        intent = topic.get("search_intent", "informational")
        intent_boost = {
            "commercial": 1.5,
            "transactional": 1.3,
            "informational": 1.0,
            "navigational": 0.7,
        }
        base_score *= intent_boost.get(intent, 1.0)

        # Performance multiplier by category
        category = topic.get("content_category", "")
        perf_mult = multipliers.get(category, 1.0)
        base_score *= perf_mult

        topic["_score"] = round(base_score, 2)
        topic["_perf_multiplier"] = perf_mult
        scored.append(topic)

    scored.sort(key=lambda t: t["_score"], reverse=True)
    return scored


def build_prompt_variables(topic):
    """Convert a topic dict into the full set of master prompt variables."""
    return {
        "target_keyword": topic.get("keyword", ""),
        "search_volume": "N/A",
        "keyword_difficulty": "N/A",
        "search_intent": topic.get("search_intent", "informational"),
        "secondary_keywords": topic.get("secondary_keywords", ""),
        "audience_pain_point": topic.get("pain_point", ""),
        "audience_persona": topic.get("audience_persona", ""),
        "content_format": topic.get("content_format", "tutorial"),
        "ghl_feature": topic.get("ghl_feature", ""),
        "affiliate_product": "GoHighLevel",
        "cta_slug": "gohighlevel",
        "competing_gaps": "",
        "video_length": "10",
        "content_category": topic.get("content_category", "automation"),
        "num_sections": "4",
        "word_count": "1500",
        "primary_topic_hashtag": topic.get("content_category", "automation").replace("-", ""),
        "content_category_hashtag": topic.get("content_category", "").replace("-", ""),
    }


def main():
    parser = argparse.ArgumentParser(description="Research and select the best video topic.")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--top", type=int, default=5, help="Show top N candidates")
    args = parser.parse_args()

    # Load data
    pool = load_topic_pool()
    logger.info(f"Topic pool: {len(pool)} topics")

    covered = get_covered_topics()
    logger.info(f"Already covered: {len(covered)} topics")

    multipliers = get_performance_multipliers()
    if multipliers:
        logger.info(f"Performance multipliers: {multipliers}")

    # Score and rank
    scored = score_topics(pool, covered, multipliers)
    logger.info(f"Scored {len(scored)} uncovered topics")

    if not scored:
        logger.warning("No uncovered topics available!")
        print("No topics available. Add more to video_topics.json.")
        return

    # Output
    if args.json:
        best = scored[0]
        variables = build_prompt_variables(best)
        output = {
            "selected_topic": best,
            "prompt_variables": variables,
            "candidates_count": len(scored),
            "top_candidates": [{"keyword": t["keyword"], "score": t["_score"]} for t in scored[:args.top]],
        }
        print(json.dumps(output, indent=2))
    else:
        print(f"\nTop {min(args.top, len(scored))} video topic candidates:\n")
        for i, topic in enumerate(scored[:args.top], 1):
            print(f"  {i}. [{topic['_score']:.1f}] {topic['keyword']}")
            print(f"     Format: {topic['content_format']} | Category: {topic['content_category']} | Perf mult: {topic.get('_perf_multiplier', 1.0)}")
        print(f"\nSelected: {scored[0]['keyword']}")
        print(f"Page: {scored[0].get('page_url', 'N/A')}")


if __name__ == "__main__":
    main()
