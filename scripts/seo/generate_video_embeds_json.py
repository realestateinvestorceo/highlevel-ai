#!/usr/bin/env python3
"""
Generate video_embeds.json from the VideoCreation Google Sheet.

Reads all rows where youtube_video_id is populated and outputs a JSON
mapping page URLs to their video embed data.

Usage:
    python3 scripts/seo/generate_video_embeds_json.py
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (
    VIDEO_SHEET_ID, GA4_SERVICE_ACCOUNT_FILE,
    DATA_DIR, setup_logging
)

logger = setup_logging("generate_video_embeds_json")


def main():
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

    embeds = {}
    for row in rows:
        video_id = (row.get("youtube_video_id", "") or "").strip()
        page_url = (row.get("page_url", "") or "").strip()

        if not video_id or not page_url:
            continue

        embeds[page_url] = {
            "video_id": video_id,
            "title": row.get("title", ""),
            "description": (row.get("description", "") or "")[:200],
            "upload_date": row.get("created_date", ""),
            "thumbnail_url": f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
            "youtube_url": row.get("youtube_url", "") or f"https://www.youtube.com/watch?v={video_id}",
        }

    output_path = DATA_DIR / "video_embeds.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(embeds, f, indent=2, ensure_ascii=False)

    logger.info(f"Wrote {len(embeds)} video embeds to {output_path}")
    print(f"Generated video_embeds.json with {len(embeds)} entries")


if __name__ == "__main__":
    main()
