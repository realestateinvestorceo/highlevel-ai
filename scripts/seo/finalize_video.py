#!/usr/bin/env python3
"""
Finalize Video — upload thumbnails for videos that have youtube_video_id
but haven't had their thumbnail uploaded yet.

Reads Sheets, finds rows ready for thumbnail upload, uploads, and marks complete.

Usage:
    python3 scripts/seo/finalize_video.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (
    VIDEO_SHEET_ID, GA4_SERVICE_ACCOUNT_FILE,
    setup_logging, now
)

logger = setup_logging("finalize_video")

THUMBNAILS_DIR = Path(__file__).resolve().parent / "thumbnails"


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

    rows = ws.get_all_values()
    if len(rows) <= 1:
        print("No data rows")
        return

    header = rows[0]

    # Find column indices
    def col_idx(name):
        try:
            return header.index(name)
        except ValueError:
            return -1

    vid_col = col_idx("youtube_video_id")
    status_col = col_idx("status")

    if vid_col < 0:
        print("youtube_video_id column not found")
        return

    finalized = 0
    for row_num, row in enumerate(rows[1:], start=2):
        video_id = row[vid_col].strip() if len(row) > vid_col else ""
        status = row[status_col].strip().lower() if len(row) > status_col and status_col >= 0 else ""

        if not video_id or status == "thumbnail_uploaded":
            continue

        # Find matching thumbnail file
        thumb_files = list(THUMBNAILS_DIR.glob(f"*_{video_id}*")) or list(THUMBNAILS_DIR.glob("*.jpg"))
        if not thumb_files:
            logger.info(f"No thumbnail found for {video_id}")
            continue

        thumb_path = thumb_files[0]

        try:
            from upload_thumbnail import upload_thumbnail
            upload_thumbnail(video_id, thumb_path)

            # Update status in Sheets
            if status_col >= 0:
                from gspread.utils import rowcol_to_a1
                cell = rowcol_to_a1(row_num, status_col + 1)
                ws.update(cell, [["thumbnail_uploaded"]])

            finalized += 1
            logger.info(f"Finalized {video_id} with {thumb_path.name}")
        except Exception as e:
            logger.error(f"Failed to finalize {video_id}: {e}")

    print(f"Finalized {finalized} videos")


if __name__ == "__main__":
    main()
