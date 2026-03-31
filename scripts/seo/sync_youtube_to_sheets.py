#!/usr/bin/env python3
"""
Sync YouTube video stats to the VideoCreation Google Sheet.

Reads rows where youtube_video_id is populated, fetches stats from YouTube,
and writes back: views, impressions, ctr, avg_watch_pct, likes, comments, last_synced.

Usage:
    python3 scripts/seo/sync_youtube_to_sheets.py
"""

import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (
    VIDEO_SHEET_ID, GA4_SERVICE_ACCOUNT_FILE,
    setup_logging, now
)

logger = setup_logging("sync_youtube_to_sheets")

# Columns in the HighLevel tab that we read/write
# Base columns (0-indexed): page_url(0), title(1), description(2), tags(3),
# status(4), youtube_url(5), youtube_video_id(6), created_date(7)
# Extended columns we add/update:
STATS_COLUMNS = {
    "views": 8,           # column I
    "impressions": 9,     # column J
    "ctr": 10,            # column K
    "avg_watch_pct": 11,  # column L
    "likes": 12,          # column M
    "comments": 13,       # column N
    "last_synced": 14,    # column O
}

METADATA_COLUMNS = {
    "hook_type": 15,              # column P
    "content_format": 16,         # column Q
    "content_category": 17,       # column R
    "ghl_feature": 18,            # column S
    "template_used": 19,          # column T
    "thumbnail_variation": 20,    # column U
    "accent_color": 21,           # column V
    "thumbnail_text": 22,         # column W
    "thumbnail_text_word_count": 23,  # column X
    "video_length_minutes": 24,   # column Y
}


def ensure_headers(ws):
    """Ensure the extended columns have headers."""
    headers = ws.row_values(1)
    all_cols = {**STATS_COLUMNS, **METADATA_COLUMNS}
    max_col = max(all_cols.values()) + 1

    if len(headers) < max_col:
        # Extend headers
        needed = list(all_cols.keys())
        for name, col_idx in all_cols.items():
            if col_idx < len(headers):
                if not headers[col_idx]:
                    headers[col_idx] = name
            else:
                while len(headers) <= col_idx:
                    headers.append("")
                headers[col_idx] = name

        # Write extended header row
        from gspread.utils import rowcol_to_a1
        end_col = rowcol_to_a1(1, max_col)
        ws.update(f"A1:{end_col}", [headers])
        logger.info(f"Extended headers to {max_col} columns")


def main():
    import gspread
    from google.oauth2.service_account import Credentials

    # Connect to Sheets
    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(GA4_SERVICE_ACCOUNT_FILE, scopes=scopes)
    gc = gspread.authorize(creds)
    spreadsheet = gc.open_by_key(VIDEO_SHEET_ID)
    ws = spreadsheet.worksheet("HighLevel")

    ensure_headers(ws)

    # Get all rows
    all_rows = ws.get_all_values()
    if len(all_rows) <= 1:
        logger.info("No data rows in HighLevel tab")
        return

    header = all_rows[0]
    vid_col = 6  # youtube_video_id column index

    # Find rows with youtube_video_id
    videos_to_sync = []
    for row_idx, row in enumerate(all_rows[1:], start=2):  # 1-indexed, skip header
        if len(row) > vid_col and row[vid_col].strip():
            videos_to_sync.append({
                "row_num": row_idx,
                "video_id": row[vid_col].strip(),
                "title": row[1] if len(row) > 1 else "",
            })

    if not videos_to_sync:
        logger.info("No videos with youtube_video_id found — nothing to sync")
        return

    logger.info(f"Found {len(videos_to_sync)} videos to sync")

    # Authenticate with YouTube
    try:
        from youtube_analytics import authenticate, get_youtube_service, get_video_stats
        yt_creds = authenticate()
        yt_service = get_youtube_service(yt_creds)
    except Exception as e:
        logger.error(f"YouTube auth failed: {e}")
        logger.error("Run youtube_analytics.py locally first to complete OAuth")
        return

    # Fetch stats and update Sheets
    synced = 0
    for video in videos_to_sync:
        try:
            stats = get_video_stats(yt_service, video["video_id"])
            if not stats:
                logger.warning(f"No stats for {video['video_id']}")
                continue

            # Build cell updates
            from gspread.utils import rowcol_to_a1
            updates = []
            row = video["row_num"]

            updates.append({
                "range": rowcol_to_a1(row, STATS_COLUMNS["views"] + 1),
                "values": [[stats.get("views", 0)]],
            })
            updates.append({
                "range": rowcol_to_a1(row, STATS_COLUMNS["likes"] + 1),
                "values": [[stats.get("likes", 0)]],
            })
            updates.append({
                "range": rowcol_to_a1(row, STATS_COLUMNS["comments"] + 1),
                "values": [[stats.get("comments", 0)]],
            })
            updates.append({
                "range": rowcol_to_a1(row, STATS_COLUMNS["last_synced"] + 1),
                "values": [[now()]],
            })

            for update in updates:
                ws.update(update["range"], update["values"])

            synced += 1
            logger.info(f"Synced {video['title']}: {stats.get('views', 0)} views")

        except Exception as e:
            logger.warning(f"Error syncing {video['video_id']}: {e}")

    logger.info(f"Sync complete: {synced}/{len(videos_to_sync)} videos updated")


if __name__ == "__main__":
    main()
