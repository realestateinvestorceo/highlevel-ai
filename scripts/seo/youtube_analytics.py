#!/usr/bin/env python3
"""
YouTube Analytics — fetch video performance data from YouTube Data API v3.

Requires:
    1. YouTube Data API v3 enabled in Google Cloud Console
    2. OAuth2 Desktop App credentials at site/scripts/.credentials/youtube_client_secret.json
    3. First run: interactive OAuth → saves token

Usage:
    python3 scripts/seo/youtube_analytics.py
    python3 scripts/seo/youtube_analytics.py --video-id abc123
"""

import os
import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import CREDENTIALS_DIR, setup_logging

logger = setup_logging("youtube_analytics")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

YOUTUBE_CLIENT_SECRET = str(CREDENTIALS_DIR / "youtube_client_secret.json")
YOUTUBE_TOKEN_FILE = str(CREDENTIALS_DIR / "youtube_token.json")


def authenticate():
    """Authenticate with YouTube API via OAuth2 (same pattern as GSC)."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None

    if os.path.exists(YOUTUBE_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(YOUTUBE_TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing YouTube access token...")
            creds.refresh(Request())
        else:
            if not os.path.exists(YOUTUBE_CLIENT_SECRET):
                logger.error(f"youtube_client_secret.json not found at {YOUTUBE_CLIENT_SECRET}")
                logger.error("Enable YouTube Data API v3 in Google Cloud Console and create OAuth2 Desktop App credentials.")
                sys.exit(1)

            logger.info("Opening browser for YouTube OAuth consent...")
            flow = InstalledAppFlow.from_client_secrets_file(YOUTUBE_CLIENT_SECRET, SCOPES)
            creds = flow.run_local_server(port=0)

        os.makedirs(os.path.dirname(YOUTUBE_TOKEN_FILE), exist_ok=True)
        with open(YOUTUBE_TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        logger.info("YouTube token saved.")

    return creds


def get_youtube_service(creds):
    """Build YouTube Data API v3 service."""
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=creds)


def get_analytics_service(creds):
    """Build YouTube Analytics API service."""
    from googleapiclient.discovery import build
    return build("youtubeAnalytics", "v2", credentials=creds)


def get_channel_stats(service):
    """Get channel-level stats: subscribers, total views, total videos."""
    resp = service.channels().list(part="statistics,snippet", mine=True).execute()
    if not resp.get("items"):
        return {}
    ch = resp["items"][0]
    stats = ch.get("statistics", {})
    return {
        "channel_id": ch["id"],
        "title": ch.get("snippet", {}).get("title", ""),
        "subscribers": int(stats.get("subscriberCount", 0)),
        "total_views": int(stats.get("viewCount", 0)),
        "total_videos": int(stats.get("videoCount", 0)),
    }


def get_video_stats(service, video_id):
    """Get stats for a single video: views, likes, comments."""
    resp = service.videos().list(
        part="statistics,contentDetails,snippet",
        id=video_id
    ).execute()
    if not resp.get("items"):
        return {}
    item = resp["items"][0]
    stats = item.get("statistics", {})
    snippet = item.get("snippet", {})
    return {
        "video_id": video_id,
        "title": snippet.get("title", ""),
        "published_at": snippet.get("publishedAt", ""),
        "views": int(stats.get("viewCount", 0)),
        "likes": int(stats.get("likeCount", 0)),
        "comments": int(stats.get("commentCount", 0)),
        "duration": item.get("contentDetails", {}).get("duration", ""),
    }


def get_video_analytics(analytics_service, video_id, channel_id):
    """Get CTR, impressions, and avg watch % from YouTube Analytics API."""
    from datetime import datetime, timedelta
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

    try:
        resp = analytics_service.reports().query(
            ids=f"channel=={channel_id}",
            startDate=start_date,
            endDate=end_date,
            metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage",
            dimensions="video",
            filters=f"video=={video_id}",
        ).execute()

        if resp.get("rows") and len(resp["rows"]) > 0:
            row = resp["rows"][0]
            headers = [h["name"] for h in resp.get("columnHeaders", [])]
            data = dict(zip(headers, row))
            return {
                "views": int(data.get("views", 0)),
                "estimated_minutes_watched": round(float(data.get("estimatedMinutesWatched", 0)), 1),
                "avg_view_duration": round(float(data.get("averageViewDuration", 0)), 1),
                "avg_view_percentage": round(float(data.get("averageViewPercentage", 0)), 1),
            }
    except Exception as e:
        logger.warning(f"Analytics API error for {video_id}: {e}")

    return {}


def get_all_channel_videos(service, max_results=50):
    """Get all videos from the authenticated channel."""
    # First get uploads playlist
    ch_resp = service.channels().list(part="contentDetails", mine=True).execute()
    if not ch_resp.get("items"):
        return []
    uploads_id = ch_resp["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

    # Fetch all upload items
    videos = []
    page_token = None
    while True:
        pl_resp = service.playlistItems().list(
            part="snippet,contentDetails",
            playlistId=uploads_id,
            maxResults=min(max_results - len(videos), 50),
            pageToken=page_token,
        ).execute()

        for item in pl_resp.get("items", []):
            vid_id = item["contentDetails"]["videoId"]
            snippet = item.get("snippet", {})
            videos.append({
                "video_id": vid_id,
                "title": snippet.get("title", ""),
                "published_at": snippet.get("publishedAt", ""),
                "description": snippet.get("description", "")[:200],
            })

        page_token = pl_resp.get("nextPageToken")
        if not page_token or len(videos) >= max_results:
            break

    return videos


def get_all_video_performance(service, analytics_service=None):
    """Get all channel videos with their stats, sorted by date."""
    channel = get_channel_stats(service)
    channel_id = channel.get("channel_id", "")
    videos = get_all_channel_videos(service)

    # Batch fetch stats (up to 50 at a time)
    video_ids = [v["video_id"] for v in videos]
    stats_map = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i+50]
        resp = service.videos().list(
            part="statistics",
            id=",".join(batch),
        ).execute()
        for item in resp.get("items", []):
            s = item.get("statistics", {})
            stats_map[item["id"]] = {
                "views": int(s.get("viewCount", 0)),
                "likes": int(s.get("likeCount", 0)),
                "comments": int(s.get("commentCount", 0)),
            }

    # Merge stats into video list
    for v in videos:
        vid_stats = stats_map.get(v["video_id"], {})
        v.update(vid_stats)

        # Optionally get analytics (CTR, retention) — slower, one API call per video
        if analytics_service and channel_id:
            analytics = get_video_analytics(analytics_service, v["video_id"], channel_id)
            v.update(analytics)

    # Sort by published date descending
    videos.sort(key=lambda v: v.get("published_at", ""), reverse=True)
    return {"channel": channel, "videos": videos}


def main():
    parser = argparse.ArgumentParser(description="Fetch YouTube video performance data.")
    parser.add_argument("--video-id", help="Get stats for a specific video ID")
    parser.add_argument("--all", action="store_true", help="Get all video performance data")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    creds = authenticate()
    service = get_youtube_service(creds)

    if args.video_id:
        stats = get_video_stats(service, args.video_id)
        if args.json:
            print(json.dumps(stats, indent=2))
        else:
            for k, v in stats.items():
                print(f"  {k}: {v}")
    elif args.all:
        try:
            analytics_service = get_analytics_service(creds)
        except Exception:
            analytics_service = None
            logger.warning("YouTube Analytics API not available — skipping CTR/retention data")

        data = get_all_video_performance(service, analytics_service)
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            ch = data["channel"]
            print(f"\nChannel: {ch.get('title', 'N/A')}")
            print(f"Subscribers: {ch.get('subscribers', 0):,}")
            print(f"Total views: {ch.get('total_views', 0):,}")
            print(f"Videos: {ch.get('total_videos', 0)}\n")
            for v in data["videos"]:
                print(f"  {v.get('title', 'untitled')} — {v.get('views', 0):,} views, {v.get('likes', 0)} likes")
    else:
        ch = get_channel_stats(service)
        if args.json:
            print(json.dumps(ch, indent=2))
        else:
            print(f"\nChannel: {ch.get('title', 'N/A')}")
            print(f"Subscribers: {ch.get('subscribers', 0):,}")
            print(f"Total views: {ch.get('total_views', 0):,}")
            print(f"Videos: {ch.get('total_videos', 0)}")


if __name__ == "__main__":
    main()
