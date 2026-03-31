"""
Video Pipeline — Generate video scripts, post to Slack, log to Google Sheets.

Usage:
    python scripts/seo/video_request.py --topic "GoHighLevel Pricing Explained"
    python scripts/seo/video_request.py --topic "GHL vs HubSpot" --page "/highlevel-vs-hubspot.html"
    python scripts/seo/video_request.py --topic "..." --dry-run
    python scripts/seo/video_request.py --topic "..." --no-slack --no-sheets
"""

import sys
import json
import argparse
from pathlib import Path

# Add scripts/seo/ to path for config import
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (
    ANTHROPIC_API_KEY, PERPLEXITY_API_KEY, SLACK_BOT_TOKEN, SLACK_CHANNEL_ID,
    VIDEO_SHEET_ID, GA4_SERVICE_ACCOUNT_FILE, SITE_URL,
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
    """Call Perplexity API to get current, factual information about a topic."""
    if not PERPLEXITY_API_KEY:
        logger.warning("No Perplexity API key — skipping live research")
        return ""

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
        logger.info(f"Perplexity research: {len(research.split())} words")
        return research
    except Exception as e:
        logger.warning(f"Perplexity research failed: {e}")
        return ""


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
    """Post the video script to Slack channel, optionally attach thumbnail."""
    from slack_sdk import WebClient

    client = WebClient(token=SLACK_BOT_TOKEN)

    message = f"""REF: {page_url}
Title: {title}
Create a professional explainer video. Use a natural, conversational voice at a slightly faster pace than default. Stock footage style, clean and educational. Here is the script:
{script}"""

    response = client.chat_postMessage(
        channel=SLACK_CHANNEL_ID,
        text=message
    )

    ts = response["ts"]
    logger.info(f"Slack message posted (ts: {ts})")

    # Add movie_camera reaction to trigger HeyGen
    client.reactions_add(
        channel=SLACK_CHANNEL_ID,
        timestamp=ts,
        name="movie_camera"
    )
    logger.info("Added 🎥 reaction to trigger HeyGen")

    # Attach thumbnail image to the thread if available
    if thumbnail_path:
        try:
            from pathlib import Path
            thumb = Path(thumbnail_path)
            if thumb.exists():
                client.files_upload_v2(
                    channel=SLACK_CHANNEL_ID,
                    file=str(thumb),
                    filename=thumb.name,
                    title=f"Thumbnail: {title}",
                    thread_ts=ts,
                    initial_comment="Generated thumbnail for this video:",
                )
                logger.info(f"Thumbnail attached to Slack thread: {thumb.name}")
        except Exception as e:
            logger.warning(f"Could not attach thumbnail to Slack: {e}")

    return ts


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate a video script, post to Slack, and log to Google Sheets."
    )
    parser.add_argument("--topic", default="", help="Video topic (e.g. 'GoHighLevel Pricing Explained')")
    parser.add_argument("--auto", action="store_true", help="Auto-research topic, load master prompt + performance context")
    parser.add_argument("--page", default="", help="Target page path (e.g. '/pricing-explained.html')")
    parser.add_argument("--dry-run", action="store_true", help="Generate script only, don't post anywhere")
    parser.add_argument("--short", action="store_true", help="Generate a short test script (~100 words)")
    parser.add_argument("--no-slack", action="store_true", help="Skip Slack posting")
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
    if not args.short:
        print("Researching topic (Perplexity)...")
        plog.start("research")
        try:
            research_context = research_topic(args.topic)
            if research_context:
                plog.log_success("research", f"{len(research_context.split())} words")
                print(f"Research complete ({len(research_context.split())} words)\n")
            else:
                plog.log_skipped("research", "no API key or empty response")
                print("Research skipped (no Perplexity key or empty response)\n")
        except Exception as e:
            plog.log_error("research", e)
            print(f"Research failed: {e} (continuing without research)\n")

    # Step 1: Generate script
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

    if args.dry_run:
        plog.log_skipped("sheets_write", "dry-run")
        plog.log_skipped("slack_post", "dry-run")
        print("─── DRY RUN ─── Script preview:\n")
        print(script[:1000])
        print("\n... (truncated)" if len(script) > 1000 else "")
        print(f"\n{'='*60}")
        print("Dry run complete. No messages posted.")
        return

    # Step 3: Google Sheets
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

    # Step 4: Slack
    slack_ts = None
    if args.no_slack:
        plog.log_skipped("slack_post", "--no-slack")
        print("Skipping Slack (--no-slack)")
    else:
        plog.start("slack_post")
        try:
            slack_ts = post_to_slack(page_url, metadata.get("title", ""), script, thumbnail_path=thumbnail_path)
            plog.log_success("slack_post", f"ts={slack_ts}")
            print(f"Slack message posted successfully")
            print(f"Slack message timestamp: {slack_ts}")
        except Exception as e:
            plog.log_error("slack_post", e)
            print(f"Slack error: {e}")

    # Summary
    print(f"\n{'='*60}")
    print(f"Video Title: {metadata.get('title', 'N/A')}")
    print(f"Google Sheets: {'written' if not args.no_sheets else 'skipped'}")
    print(f"Slack: {'posted' if slack_ts else 'skipped'}")
    if slack_ts:
        print(f"Slack TS: {slack_ts}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
