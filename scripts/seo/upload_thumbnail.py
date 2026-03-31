#!/usr/bin/env python3
"""
Upload Thumbnail — set a custom thumbnail on a YouTube video.

Requires youtube.upload scope (already in youtube_analytics.py scopes).

Usage:
    python3 scripts/seo/upload_thumbnail.py --video-id abc123 --image path/to/thumb.jpg
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import setup_logging

logger = setup_logging("upload_thumbnail")


def upload_thumbnail(video_id, image_path):
    """Upload a custom thumbnail to YouTube."""
    from youtube_analytics import authenticate, get_youtube_service
    from googleapiclient.http import MediaFileUpload

    creds = authenticate()
    service = get_youtube_service(creds)

    media = MediaFileUpload(str(image_path), mimetype="image/jpeg")
    response = service.thumbnails().set(
        videoId=video_id,
        media_body=media,
    ).execute()

    logger.info(f"Thumbnail uploaded for {video_id}: {response}")
    return response


def main():
    parser = argparse.ArgumentParser(description="Upload a thumbnail to YouTube.")
    parser.add_argument("--video-id", required=True, help="YouTube video ID")
    parser.add_argument("--image", required=True, help="Path to thumbnail image")
    args = parser.parse_args()

    image_path = Path(args.image)
    if not image_path.exists():
        print(f"Image not found: {image_path}")
        return

    result = upload_thumbnail(args.video_id, image_path)
    print(f"Thumbnail uploaded: {result}")


if __name__ == "__main__":
    main()
