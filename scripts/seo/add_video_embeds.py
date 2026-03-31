#!/usr/bin/env python3
"""
Add Video Embeds — inject lazy-loaded YouTube embeds + VideoObject schema
into site pages that have matching videos.

Idempotent: skips pages that already have class="video-section".
Creates backups before modifying any file.

Usage:
    python3 scripts/seo/add_video_embeds.py
    python3 scripts/seo/add_video_embeds.py --dry-run
"""

import sys
import re
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (
    SITE_DIR, SITE_URL, DATA_DIR, setup_logging, backup_file
)

logger = setup_logging("add_video_embeds")

# Lazy-loaded YouTube embed HTML (click-to-play pattern)
EMBED_TEMPLATE = """
<!-- Video Section (auto-embedded by add_video_embeds.py) -->
<section class="video-section" style="max-width:800px;margin:40px auto;padding:0 20px;">
  <h2 style="font-family:'Sora',sans-serif;font-size:1.4rem;color:#e2e8f0;margin-bottom:16px;">Watch the Video</h2>
  <div style="position:relative;padding-bottom:56.25%;height:0;overflow:hidden;border-radius:12px;background:#12121a;border:1px solid rgba(255,255,255,0.06);">
    <iframe
      style="position:absolute;top:0;left:0;width:100%;height:100%;border:0;"
      srcdoc="<style>*{{padding:0;margin:0;overflow:hidden}}html,body{{height:100%}}img,span{{position:absolute;width:100%;top:0;bottom:0;margin:auto}}span{{height:1.5em;text-align:center;font:60px/1.5 sans-serif;color:white;text-shadow:0 0 0.5em black}}</style><a href='https://www.youtube.com/embed/{video_id}?autoplay=1'><img src='https://img.youtube.com/vi/{video_id}/maxresdefault.jpg' alt='{title}'><span>&#x25B6;</span></a>"
      loading="lazy"
      allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
      allowfullscreen
      title="{title}"
    ></iframe>
  </div>
</section>
"""

VIDEO_SCHEMA_TEMPLATE = """<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@type": "VideoObject",
  "name": "{title}",
  "description": "{description}",
  "thumbnailUrl": "{thumbnail_url}",
  "uploadDate": "{upload_date}",
  "contentUrl": "{youtube_url}",
  "embedUrl": "https://www.youtube.com/embed/{video_id}"
}}
</script>"""


def load_embeds():
    """Load video_embeds.json."""
    path = DATA_DIR / "video_embeds.json"
    if not path.exists():
        logger.info("video_embeds.json not found — run generate_video_embeds_json.py first")
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def find_page_file(page_url):
    """Convert a page URL to a file path in the site directory."""
    # /index.html -> site/index.html
    # /pricing-explained.html -> site/pricing-explained.html
    # /blog/post.html -> site/blog/post.html
    rel = page_url.lstrip("/")
    if not rel or rel == "/":
        rel = "index.html"
    path = SITE_DIR / rel
    return path if path.exists() else None


def page_already_has_embed(html):
    """Check if page already has a video embed."""
    return 'class="video-section"' in html


def inject_embed(html, video_data):
    """Inject the video embed and schema into the HTML."""
    vid = video_data["video_id"]
    title = video_data["title"].replace('"', '&quot;').replace("'", "&#39;")
    desc = video_data.get("description", "").replace('"', '&quot;')[:200]

    # Build embed HTML
    embed_html = EMBED_TEMPLATE.format(
        video_id=vid,
        title=title,
    )

    # Build schema JSON-LD
    schema_html = VIDEO_SCHEMA_TEMPLATE.format(
        video_id=vid,
        title=title,
        description=desc,
        thumbnail_url=video_data.get("thumbnail_url", ""),
        upload_date=video_data.get("upload_date", ""),
        youtube_url=video_data.get("youtube_url", ""),
    )

    # Insert embed before </main> or before the last </section> before footer
    # Try </main> first
    if "</main>" in html:
        html = html.replace("</main>", embed_html + "\n</main>", 1)
    else:
        # Insert before </body>
        html = html.replace("</body>", embed_html + "\n</body>", 1)

    # Insert schema in <head>
    if "</head>" in html:
        html = html.replace("</head>", schema_html + "\n</head>", 1)

    return html


def main():
    parser = argparse.ArgumentParser(description="Add video embeds to site pages.")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without modifying files")
    args = parser.parse_args()

    embeds = load_embeds()
    if not embeds:
        print("No video embeds to add.")
        return

    modified = 0
    skipped = 0

    for page_url, video_data in embeds.items():
        page_path = find_page_file(page_url)
        if not page_path:
            logger.warning(f"Page not found: {page_url}")
            continue

        html = page_path.read_text(encoding="utf-8")

        if page_already_has_embed(html):
            skipped += 1
            continue

        if args.dry_run:
            print(f"  Would embed: {video_data['video_id']} -> {page_url}")
            modified += 1
            continue

        # Backup before modifying
        backup_file(page_path, "pre-video-embed")

        # Inject embed
        new_html = inject_embed(html, video_data)
        page_path.write_text(new_html, encoding="utf-8")
        modified += 1
        logger.info(f"Embedded {video_data['video_id']} on {page_url}")

    action = "Would modify" if args.dry_run else "Modified"
    print(f"\n{action} {modified} pages, skipped {skipped} (already embedded)")


if __name__ == "__main__":
    main()
