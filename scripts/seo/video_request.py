"""
Video Pipeline — Research topics, generate thumbnails, build NotebookLM prompts,
and save to video_queue.json for manual video creation.

Usage:
    python scripts/seo/video_request.py --topic "GoHighLevel Pricing Explained"
    python scripts/seo/video_request.py --topic "GHL vs HubSpot" --page "/highlevel-vs-hubspot.html"
    python scripts/seo/video_request.py --topic "..." --dry-run
    python scripts/seo/video_request.py --auto
"""

import os
import re
import sys
import json
import shutil
import argparse
from pathlib import Path
from datetime import datetime

# Add scripts/seo/ to path for config import
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (
    ANTHROPIC_API_KEY, PERPLEXITY_API_KEY, SLACK_BOT_TOKEN, SLACK_CHANNEL_ID,
    VIDEO_SHEET_ID, GA4_SERVICE_ACCOUNT_FILE, SITE_URL, SITE_DIR,
    AFFILIATE_LINK, DATA_DIR, setup_logging, today
)

logger = setup_logging("video_request")


# ──────────────────────────────────────────────
# Prompt loader
# ──────────────────────────────────────────────

def load_prompt(prompt_name, variables=None):
    """Load a prompt template from data/{prompt_name}.txt and replace {{variables}}."""
    prompt_path = DATA_DIR / f"{prompt_name}.txt"
    template = prompt_path.read_text(encoding="utf-8")
    if variables:
        for key, value in variables.items():
            template = template.replace("{{" + key + "}}", str(value))
    return template


# ──────────────────────────────────────────────
# Step 0: Live research via Perplexity
# ──────────────────────────────────────────────

def research_topic(topic):
    """Call Perplexity API to get current, factual information about a topic.

    Returns:
        tuple: (research_text, citation_urls) — research content and list of source URLs.
    """
    if not PERPLEXITY_API_KEY:
        logger.warning("No Perplexity API key — skipping live research")
        return "", []

    import requests

    query = f"""Research the following topic and provide ONLY current, verified facts that a video scriptwriter needs. Include:

1. Current pricing (exact dollar amounts, plan names, what's included) as of 2026
2. Key features and recent updates (last 6 months)
3. Real competitor comparisons with specific pricing differences
4. Any known limitations or common complaints from real users
5. Specific statistics or data points with their sources

Topic: {topic}

Be specific — exact numbers, exact plan names, exact feature names. No vague statements. If you're not sure about something, say so rather than guessing."""

    try:
        resp = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "sonar",
                "messages": [{"role": "user", "content": query}],
                "max_tokens": 2000,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        research = data["choices"][0]["message"]["content"]
        citations = data.get("citations", [])
        logger.info(f"Perplexity research: {len(research.split())} words, {len(citations)} citations")
        return research, citations
    except Exception as e:
        logger.warning(f"Perplexity research failed: {e}")
        return "", []


# ──────────────────────────────────────────────
# Step 1: Generate video script
# ──────────────────────────────────────────────

def generate_script(topic, page_url, short=False, research_context=""):
    """Call Anthropic API to generate a video script."""
    from anthropic import Anthropic

    if short:
        prompt = f"Write exactly 1 sentence about: {topic}. Under 12 words. Return ONLY the sentence, nothing else."
    else:
        prompt = load_prompt("video_script_prompt", {"topic": topic, "page_url": page_url})

        # Inject live research if available
        if research_context:
            prompt += f"\n\nIMPORTANT — USE THESE VERIFIED FACTS (researched today, {today()}):\n\n{research_context}\n\nUse ONLY the facts above for any claims about pricing, features, comparisons, or statistics. Do not invent or assume any numbers — if the research doesn't cover something, don't include it in the script."

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500 if short else 4000,
        messages=[{"role": "user", "content": prompt}]
    )

    script = response.content[0].text
    logger.info(f"Generated script: {len(script.split())} words")
    return script


# ──────────────────────────────────────────────
# Step 2: Generate YouTube metadata
# ──────────────────────────────────────────────

def generate_metadata(script, topic, page_url):
    """Call Anthropic API to generate YouTube title, description, and tags."""
    from anthropic import Anthropic

    prompt = f"""Based on this video script, generate YouTube metadata. Return ONLY a JSON object with these fields:
- "title": YouTube video title (under 70 chars, include the main keyword, make it clickable)
- "description": YouTube description (200-300 words, include the page URL {SITE_URL}{page_url}, include affiliate link {AFFILIATE_LINK}, include chapter timestamps starting at 0:00)
- "tags": comma-separated tags string (8-12 relevant keywords)

Script:
{script}"""

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    text = response.content[0].text.strip()

    # Extract JSON from response (handle markdown code blocks)
    if "```" in text:
        start = text.find("{")
        end = text.rfind("}") + 1
        text = text[start:end]

    metadata = json.loads(text)
    logger.info(f"Generated metadata: {metadata.get('title', 'untitled')}")
    return metadata


# ──────────────────────────────────────────────
# Step 3: Write to Google Sheets
# ──────────────────────────────────────────────

def write_to_sheets(page_url, metadata):
    """Append a row to the Video Pipeline Google Sheet."""
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_file(
        GA4_SERVICE_ACCOUNT_FILE, scopes=scopes
    )
    gc = gspread.authorize(creds)

    spreadsheet = gc.open_by_key(VIDEO_SHEET_ID)
    worksheet = spreadsheet.worksheet("HighLevel")

    row = [
        page_url,
        metadata.get("title", ""),
        metadata.get("description", ""),
        metadata.get("tags", ""),
        "pending",
        "",
        "",
        today()
    ]

    worksheet.append_row(row, value_input_option="USER_ENTERED")
    logger.info("Row written to Google Sheets")
    return True


# ──────────────────────────────────────────────
# Step 4: Post to Slack
# ──────────────────────────────────────────────

def post_to_slack(page_url, title, script, thumbnail_path=None):
    """Post the video script to Slack channel via incoming webhook."""
    import requests

    SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")

    message = f"""<@U0APRDW5DN1> REF: {page_url}
Title: {title}
Create a professional explainer video. Use a natural, conversational voice at a slightly faster pace than default. Stock footage style, clean and educational. Here is the script:
{script}"""

    response = requests.post(
        SLACK_WEBHOOK_URL,
        json={"text": message},
        headers={"Content-Type": "application/json"},
    )

    if response.status_code == 200 and response.text == "ok":
        logger.info("Slack webhook posted successfully")
    else:
        logger.warning(f"Slack webhook response: {response.status_code} {response.text}")

    return None


# ──────────────────────────────────────────────
# Step 5: Video queue helpers
# ──────────────────────────────────────────────

def slugify(text):
    """Convert text to URL-friendly slug."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    text = re.sub(r'-+', '-', text)
    return text[:60].rstrip('-')


def collect_source_urls(topic, page_url, citation_urls):
    """Combine Perplexity citations with matching highlevel.ai pages."""
    urls = list(citation_urls) if citation_urls else []

    # Add the video's own page URL
    if page_url:
        urls.append(f"{SITE_URL}{page_url}")

    # Find matching internal pages from internal_links_map.json
    links_map_path = DATA_DIR / "internal_links_map.json"
    if links_map_path.exists():
        try:
            links_map = json.loads(links_map_path.read_text(encoding="utf-8"))
            topic_words = set(topic.lower().split())
            matches = []
            for keyword, path in links_map.items():
                kw_words = set(keyword.lower().split())
                overlap = len(topic_words & kw_words)
                if overlap >= 1:
                    matches.append((overlap, f"{SITE_URL}{path}"))
            # Sort by overlap descending, take top 5
            matches.sort(key=lambda x: x[0], reverse=True)
            for _, url in matches[:5]:
                if url not in urls:
                    urls.append(url)
        except Exception as e:
            logger.warning(f"Could not load internal_links_map.json: {e}")

    # Dedupe while preserving order
    seen = set()
    deduped = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    return deduped


def generate_notebooklm_prompt(topic, research_summary):
    """Load the NotebookLM prompt template and fill in placeholders."""
    # Try site/data/ first (deploy root), fall back to scripts/seo/data/
    template_path = SITE_DIR / "data" / "notebooklm_prompt.txt"
    if not template_path.exists():
        template_path = DATA_DIR / "notebooklm_prompt.txt"
    if not template_path.exists():
        logger.warning("NotebookLM prompt template not found")
        return ""

    template = template_path.read_text(encoding="utf-8")
    prompt = template.replace("{{topic}}", topic)
    prompt = prompt.replace("{{research_summary}}", research_summary or "(No research available)")
    return prompt


def save_to_video_queue(video_id, topic, page_url, thumbnail_rel_path,
                        source_urls, research_summary, notebooklm_prompt):
    """Append a new video entry to site/video_queue.json."""
    queue_path = SITE_DIR / "video_queue.json"

    if queue_path.exists():
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
    else:
        queue = {"last_updated": "", "videos": []}

    # Don't add duplicates
    existing_ids = {v["id"] for v in queue["videos"]}
    if video_id in existing_ids:
        logger.info(f"Video {video_id} already in queue, skipping")
        return None

    entry = {
        "id": video_id,
        "topic": topic,
        "status": "pending",
        "created_date": today(),
        "page_url": page_url,
        "thumbnail_path": thumbnail_rel_path,
        "source_urls": source_urls,
        "research_summary": research_summary or "",
        "notebooklm_prompt": notebooklm_prompt or "",
        "youtube_url": None,
        "youtube_video_id": None,
        "completed_date": None,
    }

    queue["videos"].append(entry)
    queue["last_updated"] = datetime.utcnow().isoformat() + "Z"

    queue_path.write_text(json.dumps(queue, indent=2) + "\n", encoding="utf-8")
    logger.info(f"Added video {video_id} to queue ({len(queue['videos'])} total)")
    return entry


def copy_thumbnail_to_site(thumbnail_path, video_id):
    """Copy and compress thumbnail to site/thumbnails/ for web serving."""
    if not thumbnail_path or not Path(thumbnail_path).exists():
        return None

    dest_dir = SITE_DIR / "thumbnails"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"{video_id}.jpg"

    try:
        from PIL import Image
        img = Image.open(thumbnail_path)
        img = img.convert("RGB")
        # Resize to 1280x720 if larger
        if img.width > 1280 or img.height > 720:
            img = img.resize((1280, 720), Image.LANCZOS)
        img.save(str(dest_path), "JPEG", quality=80, optimize=True)
        # Check size — compress more if over 200KB
        if dest_path.stat().st_size > 200_000:
            img.save(str(dest_path), "JPEG", quality=60, optimize=True)
        logger.info(f"Thumbnail saved: {dest_path} ({dest_path.stat().st_size // 1024}KB)")
    except ImportError:
        # No Pillow — just copy the file
        shutil.copy2(thumbnail_path, dest_path)
        logger.info(f"Thumbnail copied (no PIL): {dest_path}")

    return f"/thumbnails/{video_id}.jpg"


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Research video topics, generate thumbnails, and save to video queue."
    )
    parser.add_argument("--topic", default="", help="Video topic (e.g. 'GoHighLevel Pricing Explained')")
    parser.add_argument("--auto", action="store_true", help="Auto-research topic, load master prompt + performance context")
    parser.add_argument("--page", default="", help="Target page path (e.g. '/pricing-explained.html')")
    parser.add_argument("--dry-run", action="store_true", help="Generate outputs only, don't save to queue")
    parser.add_argument("--short", action="store_true", help="Generate a short test script (~100 words)")
    parser.add_argument("--no-slack", action="store_true", default=True, help="Skip Slack posting (default: skip)")
    parser.add_argument("--no-sheets", action="store_true", help="Skip Google Sheets logging")
    args = parser.parse_args()

    page_url = args.page or ""

    # Auto-research mode: pick topic automatically
    auto_variables = None
    if args.auto:
        from research_video_topic import load_topic_pool, get_covered_topics, get_performance_multipliers, score_topics, build_prompt_variables
        pool = load_topic_pool()
        covered = get_covered_topics()
        multipliers = get_performance_multipliers()
        scored = score_topics(pool, covered, multipliers)
        if not scored:
            print("No uncovered topics available. Add more to video_topics.json.")
            return
        selected = scored[0]
        args.topic = selected["keyword"]
        page_url = selected.get("page_url", "")
        auto_variables = build_prompt_variables(selected)
        print(f"Auto-selected topic: {args.topic} (score: {selected['_score']})")

    if not args.topic:
        parser.error("--topic is required (or use --auto)")

    # Pipeline logger (logs each step to Sheets + local file)
    from pipeline_logger import PipelineLogger
    plog = PipelineLogger(today(), video_topic=args.topic)

    print(f"\n{'='*60}")
    print(f"Video Pipeline — {args.topic}")
    print(f"{'='*60}\n")

    # Step 0: Live research via Perplexity
    research_context = ""
    citation_urls = []
    if not args.short:
        print("Researching topic (Perplexity)...")
        plog.start("research")
        try:
            research_context, citation_urls = research_topic(args.topic)
            if research_context:
                plog.log_success("research", f"{len(research_context.split())} words, {len(citation_urls)} citations")
                print(f"Research complete ({len(research_context.split())} words, {len(citation_urls)} citations)\n")
            else:
                plog.log_skipped("research", "no API key or empty response")
                print("Research skipped (no Perplexity key or empty response)\n")
        except Exception as e:
            plog.log_error("research", e)
            print(f"Research failed: {e} (continuing without research)\n")

    # Step 1: Generate script (still used for metadata generation)
    print("Generating video script...")
    plog.start("script_gen")
    try:
        script = generate_script(args.topic, page_url, short=args.short, research_context=research_context)
        plog.log_success("script_gen", f"{len(script.split())} words")
        print(f"Script generated ({len(script.split())} words)\n")
    except Exception as e:
        plog.log_error("script_gen", e)
        print(f"Script generation failed: {e}")
        return

    # Step 2: Generate metadata
    print("Generating YouTube metadata...")
    plog.start("metadata_gen")
    try:
        metadata = generate_metadata(script, args.topic, page_url)
        plog.log_success("metadata_gen", metadata.get("title", ""))
        print(f"Title: {metadata.get('title', 'N/A')}")
        print(f"Tags: {metadata.get('tags', 'N/A')}\n")
    except Exception as e:
        plog.log_error("metadata_gen", e)
        print(f"Metadata generation failed: {e}")
        return

    # Step 2b: Generate thumbnail (if image API key available)
    thumbnail_path = None
    plog.start("thumbnail_gen")
    try:
        from generate_thumbnail import generate_thumbnail
        thumb_metadata = {
            "video_title": metadata.get("title", ""),
            "target_keyword": args.topic,
            "content_format": auto_variables.get("content_format", "tutorial") if auto_variables else "tutorial",
            "hook_type": auto_variables.get("hook_type", "") if auto_variables else "",
            "ghl_feature": auto_variables.get("ghl_feature", "") if auto_variables else "",
            "thumbnail_text": args.topic.split()[-2:] if args.topic else ["GHL"],
            "content_category": auto_variables.get("content_category", "") if auto_variables else "",
        }
        paths = generate_thumbnail(thumb_metadata)
        if paths:
            thumbnail_path = paths[0]
            plog.log_success("thumbnail_gen", thumbnail_path)
            print(f"Thumbnail generated: {thumbnail_path}")
        else:
            plog.log_skipped("thumbnail_gen", "no image API key configured")
            print("Thumbnail skipped (no image API key)")
    except Exception as e:
        plog.log_error("thumbnail_gen", e)
        print(f"Thumbnail generation failed: {e} (continuing without thumbnail)")

    # Step 3: Build video queue entry
    video_id = f"{today()}-{slugify(args.topic)}"

    # Copy thumbnail to site/thumbnails/
    thumbnail_rel_path = copy_thumbnail_to_site(thumbnail_path, video_id)

    # Collect source URLs (Perplexity citations + matching internal pages)
    source_urls = collect_source_urls(args.topic, page_url, citation_urls)
    print(f"Source URLs collected: {len(source_urls)}")

    # Generate NotebookLM prompt from template
    notebooklm_prompt = generate_notebooklm_prompt(args.topic, research_context)
    if notebooklm_prompt:
        print(f"NotebookLM prompt generated ({len(notebooklm_prompt)} chars)")

    if args.dry_run:
        plog.log_skipped("queue_write", "dry-run")
        plog.log_skipped("sheets_write", "dry-run")
        print("\n─── DRY RUN ─── Preview:\n")
        print(f"Video ID: {video_id}")
        print(f"Thumbnail: {thumbnail_rel_path or '(none)'}")
        print(f"Source URLs: {source_urls[:3]}...")
        print(f"NotebookLM prompt: {notebooklm_prompt[:200]}...")
        print(f"\n{'='*60}")
        print("Dry run complete. Nothing saved.")
        return

    # Save to video_queue.json
    plog.start("queue_write")
    try:
        entry = save_to_video_queue(
            video_id=video_id,
            topic=args.topic,
            page_url=page_url,
            thumbnail_rel_path=thumbnail_rel_path,
            source_urls=source_urls,
            research_summary=research_context,
            notebooklm_prompt=notebooklm_prompt,
        )
        if entry:
            plog.log_success("queue_write", f"video_id={video_id}")
            print(f"Video queue updated: {video_id}")
        else:
            plog.log_skipped("queue_write", "duplicate video_id")
            print(f"Video {video_id} already in queue")
    except Exception as e:
        plog.log_error("queue_write", e)
        print(f"Queue write error: {e}")

    # Step 4: Google Sheets (backward compat — basic metadata only)
    if args.no_sheets:
        plog.log_skipped("sheets_write", "--no-sheets")
        print("Skipping Google Sheets (--no-sheets)")
    else:
        plog.start("sheets_write")
        try:
            write_to_sheets(page_url, metadata)
            plog.log_success("sheets_write")
            print("Google Sheet row written successfully")
        except Exception as e:
            plog.log_error("sheets_write", e)
            print(f"Google Sheets error: {e}")

    # Slack posting is disabled by default (manual video workflow)
    plog.log_skipped("slack_post", "manual workflow — no Slack posting")

    # Summary
    print(f"\n{'='*60}")
    print(f"Video ID: {video_id}")
    print(f"Title: {metadata.get('title', 'N/A')}")
    print(f"Thumbnail: {thumbnail_rel_path or 'none'}")
    print(f"Source URLs: {len(source_urls)}")
    print(f"Queue: saved")
    print(f"Sheets: {'written' if not args.no_sheets else 'skipped'}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
