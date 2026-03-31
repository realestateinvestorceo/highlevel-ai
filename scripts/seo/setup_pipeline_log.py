"""
One-time setup: Create "PipelineLog" tab in the VideoCreation Google Sheet.

Usage:
    python scripts/seo/setup_pipeline_log.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import VIDEO_SHEET_ID, GA4_SERVICE_ACCOUNT_FILE

COLUMNS = [
    "timestamp",
    "pipeline_run_id",
    "step_name",
    "status",
    "details",
    "video_topic",
    "duration_seconds",
]

TAB_NAME = "PipelineLog"


def main():
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(GA4_SERVICE_ACCOUNT_FILE, scopes=scopes)
    gc = gspread.authorize(creds)
    spreadsheet = gc.open_by_key(VIDEO_SHEET_ID)

    # Check if tab already exists
    existing = [ws.title for ws in spreadsheet.worksheets()]
    if TAB_NAME in existing:
        print(f"Tab '{TAB_NAME}' already exists — nothing to do.")
        return

    # Create new worksheet
    ws = spreadsheet.add_worksheet(title=TAB_NAME, rows=1000, cols=len(COLUMNS))
    ws.update("A1", [COLUMNS])

    # Bold + freeze header row
    ws.format("A1:G1", {"textFormat": {"bold": True}})
    ws.freeze(rows=1)

    print(f"Created '{TAB_NAME}' tab with columns: {', '.join(COLUMNS)}")


if __name__ == "__main__":
    main()
