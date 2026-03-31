"""
PipelineLogger — structured logging for the video pipeline.

Writes every step's status to Google Sheets "PipelineLog" tab and
falls back to local log files if Sheets is unreachable. Alerts Slack
on errors so failures are never silent.

Usage:
    from pipeline_logger import PipelineLogger
    plog = PipelineLogger("2026-03-31", video_topic="GHL Pricing")
    plog.start("script_gen")
    try:
        result = generate_script(...)
        plog.log_success("script_gen", f"{len(result)} words")
    except Exception as e:
        plog.log_error("script_gen", e)
"""

import time
import json
import logging
from datetime import datetime
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (
    SLACK_BOT_TOKEN, SLACK_CHANNEL_ID, VIDEO_SHEET_ID,
    GA4_SERVICE_ACCOUNT_FILE, LOGS_DIR, setup_logging, now
)

logger = setup_logging("pipeline_logger")

PIPELINE_LOG_TAB = "PipelineLog"


class PipelineLogger:
    def __init__(self, run_id, video_topic=""):
        self.run_id = run_id
        self.video_topic = video_topic
        self.start_times = {}
        self._sheets_client = None

    def start(self, step_name):
        """Mark a step as running."""
        self.start_times[step_name] = time.time()
        self._log(step_name, "running")

    def log_success(self, step_name, details=""):
        """Mark a step as successfully completed."""
        duration = self._duration(step_name)
        self._log(step_name, "success", details, duration)

    def log_error(self, step_name, error):
        """Mark a step as failed and alert Slack."""
        duration = self._duration(step_name)
        self._log(step_name, "error", str(error), duration)
        self._alert_slack(step_name, error)

    def log_skipped(self, step_name, reason=""):
        """Mark a step as intentionally skipped."""
        self._log(step_name, "skipped", reason)

    # ── Internal ──

    def _duration(self, step_name):
        start = self.start_times.get(step_name)
        if start is None:
            return 0
        return round(time.time() - start, 1)

    def _log(self, step_name, status, details="", duration=0):
        """Write to Sheets, fall back to local log."""
        row = [
            now(),
            self.run_id,
            step_name,
            status,
            str(details)[:500],  # truncate long error messages
            self.video_topic,
            str(duration),
        ]

        # Try Sheets first
        try:
            self._write_to_sheets(row)
        except Exception as e:
            logger.warning(f"Sheets write failed ({e}), logging locally")
            self._write_to_local_log(row)

        # Always log to Python logger
        level = logging.ERROR if status == "error" else logging.INFO
        logger.log(level, f"[{self.run_id}] {step_name}: {status} ({duration}s) {details}")

    def _write_to_sheets(self, row):
        """Append a row to the PipelineLog tab."""
        if not VIDEO_SHEET_ID or not GA4_SERVICE_ACCOUNT_FILE:
            raise ValueError("Missing VIDEO_SHEET_ID or GA4_SERVICE_ACCOUNT_FILE")

        if self._sheets_client is None:
            import gspread
            from google.oauth2.service_account import Credentials

            scopes = [
                "https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive",
            ]
            creds = Credentials.from_service_account_file(
                GA4_SERVICE_ACCOUNT_FILE, scopes=scopes
            )
            self._sheets_client = gspread.authorize(creds)

        spreadsheet = self._sheets_client.open_by_key(VIDEO_SHEET_ID)
        worksheet = spreadsheet.worksheet(PIPELINE_LOG_TAB)
        worksheet.append_row(row, value_input_option="USER_ENTERED")

    def _write_to_local_log(self, row):
        """Fallback: append to a local JSON-lines log file."""
        log_file = LOGS_DIR / "pipeline_log.jsonl"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": row[0],
            "run_id": row[1],
            "step": row[2],
            "status": row[3],
            "details": row[4],
            "topic": row[5],
            "duration": row[6],
        }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def _alert_slack(self, step_name, error):
        """Post a warning to Slack when a pipeline step fails."""
        if not SLACK_BOT_TOKEN or not SLACK_CHANNEL_ID:
            logger.warning("Slack credentials missing — skipping error alert")
            return

        try:
            from slack_sdk import WebClient

            client = WebClient(token=SLACK_BOT_TOKEN)
            msg = (
                f":warning: *Pipeline failed* at `{step_name}`\n"
                f"*Topic:* {self.video_topic}\n"
                f"*Error:* {str(error)[:300]}\n"
                f"*Run ID:* {self.run_id}"
            )
            client.chat_postMessage(channel=SLACK_CHANNEL_ID, text=msg)
        except Exception as e:
            logger.error(f"Slack alert failed: {e}")
