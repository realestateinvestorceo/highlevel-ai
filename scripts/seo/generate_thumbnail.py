#!/usr/bin/env python3
"""
Thumbnail Generator — auto-generate branded YouTube thumbnails using
the master prompt, Ideogram/GPT Image API, and Pillow post-processing.

Usage:
    python3 scripts/seo/generate_thumbnail.py --title "GHL Review" --keyword "gohighlevel review" --format tutorial --hook proof
    python3 scripts/seo/generate_thumbnail.py --from-json '{"video_title": "...", ...}'
"""

import sys
import json
import argparse
import re
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (
    IDEOGRAM_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY,
    DATA_DIR, setup_logging, today
)
from video_request import load_prompt

logger = setup_logging("generate_thumbnail")

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
THUMBNAILS_DIR = Path(__file__).resolve().parent / "thumbnails"

# Template selection logic (from master prompt)
TEMPLATE_MAP = {
    "tutorial": "tutorial",
    "FAQ": "tutorial",
    "quick-win": "result",
    "review": "comparison",
    "comparison": "comparison",
    "listicle": "step_by_step",
    "problem-solution": "step_by_step",
}

HOOK_TEMPLATE_MAP = {
    "proof": "result",
    "contrarian": "hidden",
    "pattern-interrupt": "hidden",
}

TEMPLATE_NAMES = {
    "tutorial": "The Tutorial",
    "result": "The Result",
    "comparison": "The Comparison",
    "hidden": "The Hidden Feature",
    "step_by_step": "The Step-by-Step",
}


def select_template(content_format, hook_type, perf_data=None):
    """Select the best thumbnail template based on content + hook + performance data."""
    # Start with content format mapping
    template = TEMPLATE_MAP.get(content_format, "tutorial")

    # Hook type can override
    if hook_type in HOOK_TEMPLATE_MAP:
        template = HOOK_TEMPLATE_MAP[hook_type]

    # Performance override: if the selected template is a bottom performer, switch
    if perf_data:
        templates = perf_data.get("templates", {})
        if templates and len(templates) >= 3:
            ranked = sorted(templates.items(), key=lambda x: x[1].get("avg_ctr", 0), reverse=True)
            bottom_2 = [r[0] for r in ranked[-2:]]
            if template in bottom_2:
                # Switch to best performer that still makes sense
                best = ranked[0][0]
                logger.info(f"Performance override: {template} -> {best} (CTR-based)")
                template = best

    return template


def select_accent_color(content_format, content_category, perf_data=None):
    """Select accent color based on content + performance."""
    # Defaults from master prompt
    if content_category == "troubleshooting" or content_format == "problem-solution":
        color = "red"
    elif content_category in ("cost-savings", "business-growth") or content_format in ("quick-win", "review"):
        color = "green"
    else:
        color = "yellow"

    # Performance override
    if perf_data:
        colors = perf_data.get("colors", {})
        if colors and len(colors) >= 2:
            best_color = max(colors.items(), key=lambda x: x[1].get("avg_ctr", 0))
            if best_color[1].get("avg_ctr", 0) > 0:
                color = best_color[0]

    return color


def generate_image_prompt(template, variables, ghl_feature=""):
    """Build the image generation API prompt from template + variables."""
    # Feature-specific visual descriptions
    feature_visuals = {
        "Workflow Builder": "an automation flow diagram with connected nodes and trigger icons",
        "SaaS Configurator": "a pricing configuration panel with toggle switches and plan tiers",
        "Reputation Management": "a star rating display and review cards with ratings",
        "Chat Widget": "a chat conversation interface with message bubbles",
        "CRM + Marketing Automation": "a CRM dashboard with contact cards and pipeline stages",
        "Calendar + Booking": "a booking calendar interface with time slots and appointments",
        "Funnel Builder": "a funnel visualization with stages and conversion indicators",
        "Email Marketing": "an email campaign builder with template blocks and metrics",
        "Pricing Plans": "a pricing comparison table with plan feature checkmarks",
        "Pipeline Management + SMS": "a sales pipeline with SMS conversation threads",
    }
    feature_desc = feature_visuals.get(ghl_feature, "a modern SaaS dashboard interface panel with metrics and controls")

    # Template-specific base prompts
    prompts = {
        "tutorial": f"""A bold, high-contrast YouTube thumbnail scene in professional graphic design style.
Dark navy gradient background (#0B223F to #152E50). Left 55% of frame: a clean,
slightly angled modern SaaS dashboard interface panel showing {feature_desc} —
rendered as a stylized graphic with glowing bright blue (#188AF5) accent lines on
a dark surface. A large glowing golden yellow (#FEB902) arrow directs attention to
the key feature area. Right 45%: clean empty dark navy space for text overlay.
No text anywhere. No words. No faces. Maximum 3 visual elements. 16:9 aspect ratio.""",

        "result": f"""A bold, high-contrast YouTube thumbnail scene in professional graphic design style.
Dark navy gradient background (#0B223F to #152E50). Center-left: a stylized glowing
analytics chart showing a dramatic upward trend — bright green (#00E676) glowing
trend line against dark background. Small golden yellow (#FEB902) sparkle effects
near the peak. A simplified SaaS dashboard frame in cool blue (#188AF5). Right 40%:
clean empty dark navy space for text. No text. No numbers. No faces. Maximum 3
visual elements. 16:9 aspect ratio.""",

        "comparison": f"""A bold, high-contrast YouTube thumbnail scene in professional graphic design style.
Split composition divided vertically. Left 45%: dark desaturated background (#1A1A2E)
with a dim, cramped tool interface in muted colors. Subtle red (#FF1744) tint. Right
45%: brighter background (#0B223F with blue #188AF5 glow) showing a clean modern
dashboard. Center 10%: bright yellow (#FEB902) dividing line. Left = cramped/old,
right = clean/powerful. No text. No logos. No faces. 16:9 aspect ratio.""",

        "hidden": f"""A bold, high-contrast YouTube thumbnail scene in professional graphic design style.
Dark navy background (#0B223F). A large tilted SaaS dashboard — mostly darkened and
slightly blurred, except ONE area brightly illuminated with golden yellow (#FEB902)
circular spotlight showing {feature_desc} in sharp detail. A bold red (#FF1744) arrow
points to the illuminated area. Blue (#188AF5) lens flare effects. Right 35% clear
dark space for text. No text. No faces. Maximum 3 visual elements. 16:9 aspect ratio.""",

        "step_by_step": f"""A bold, high-contrast YouTube thumbnail scene in professional graphic design style.
Dark navy gradient background (#0B223F to #152E50). Left 30%: a large bold glowing
circle in golden yellow (#FEB902) with dramatic glow — container for a step number.
Center-right: 2-3 small SaaS interface icons arranged in a horizontal flow connected
by thin bright blue (#188AF5) lines. Right 35%: clean dark space for text. No text.
No numbers. No faces. Maximum 4 visual elements. 16:9 aspect ratio.""",
    }

    base = prompts.get(template, prompts["tutorial"])

    # Append negative prompt
    negative = """
NEGATIVE: Do not include any text, words, letters, numbers, titles, subtitles,
watermarks, logos, brand names, URLs, or written content of any kind. Do not include
human faces, hands, or body parts. Keep composition clean with maximum 3-4 visual
elements. Do not place important elements in the bottom-right 15%."""

    return base.strip() + "\n\n" + negative.strip()


def call_image_api(prompt, output_path):
    """Call image generation API (Ideogram primary, GPT Image fallback)."""
    if IDEOGRAM_API_KEY:
        return _call_ideogram(prompt, output_path)
    elif OPENAI_API_KEY:
        return _call_gpt_image(prompt, output_path)
    else:
        logger.error("No image API key available (need IDEOGRAM_API_KEY or OPENAI_API_KEY)")
        return None


def _call_ideogram(prompt, output_path):
    """Call Ideogram v3 API."""
    import requests

    resp = requests.post(
        "https://api.ideogram.ai/generate",
        headers={
            "Api-Key": IDEOGRAM_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "image_request": {
                "prompt": prompt,
                "aspect_ratio": "ASPECT_16_9",
                "model": "V_2",
                "style_type": "DESIGN",
                "magic_prompt_option": "OFF",
                "negative_prompt": "text, words, letters, numbers, faces, hands",
            }
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()

    # Download the image
    image_url = data.get("data", [{}])[0].get("url", "")
    if not image_url:
        logger.error("No image URL in Ideogram response")
        return None

    img_resp = requests.get(image_url, timeout=30)
    img_resp.raise_for_status()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(img_resp.content)

    logger.info(f"Ideogram image saved to {output_path}")
    return output_path


def _call_gpt_image(prompt, output_path):
    """Call OpenAI GPT Image API (fallback)."""
    from openai import OpenAI

    client = OpenAI(api_key=OPENAI_API_KEY)
    resp = client.images.generate(
        model="dall-e-3",
        prompt=prompt,
        size="1792x1024",
        quality="standard",
        n=1,
    )

    image_url = resp.data[0].url
    import requests
    img_resp = requests.get(image_url, timeout=30)
    img_resp.raise_for_status()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(img_resp.content)

    logger.info(f"GPT Image saved to {output_path}")
    return output_path


def post_process(image_path, template, thumbnail_text, accent_color="yellow"):
    """Add text overlay, badge, and logo using Pillow."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        logger.warning("Pillow not installed — skipping post-processing")
        return image_path

    img = Image.open(image_path).convert("RGB")
    img = img.resize((1280, 720), Image.LANCZOS)
    draw = ImageDraw.Draw(img)

    # Color map
    colors = {
        "yellow": "#FEB902",
        "red": "#FF1744",
        "green": "#00E676",
    }
    accent_hex = colors.get(accent_color, "#FEB902")

    # Try to load Poppins font, fall back to default
    font_path = ASSETS_DIR / "fonts" / "Poppins-Black.ttf"
    try:
        words = thumbnail_text.split()
        font_size = 120 if len(words) <= 3 else 100 if len(words) <= 4 else 90
        font = ImageFont.truetype(str(font_path), font_size)
    except (OSError, IOError):
        logger.warning("Poppins-Black.ttf not found, using default font")
        font = ImageFont.load_default()

    # Draw text with stroke in the right third
    text_upper = thumbnail_text.upper()
    text_x = 850
    text_y = 300

    # Black stroke
    for dx in range(-4, 5):
        for dy in range(-4, 5):
            draw.text((text_x + dx, text_y + dy), text_upper, font=font, fill="#000000")
    # White fill
    draw.text((text_x, text_y), text_upper, font=font, fill="#FFFFFF")

    # Save
    img.save(image_path, "JPEG", quality=90)
    logger.info(f"Post-processed: {image_path}")
    return image_path


def generate_thumbnail(video_metadata):
    """Main entry point: generate a thumbnail from video metadata.

    Args:
        video_metadata: dict with keys like video_title, target_keyword,
                       content_format, hook_type, ghl_feature, thumbnail_text, etc.
    Returns:
        list of output file paths (up to 3 variations)
    """
    title = video_metadata.get("video_title", video_metadata.get("title", ""))
    keyword = video_metadata.get("target_keyword", "")
    content_format = video_metadata.get("content_format", "tutorial")
    hook_type = video_metadata.get("hook_type", "")
    ghl_feature = video_metadata.get("ghl_feature", "")
    thumbnail_text = video_metadata.get("thumbnail_text", keyword.split()[-2:] if keyword else ["GHL"])
    content_category = video_metadata.get("content_category", "")

    if isinstance(thumbnail_text, list):
        thumbnail_text = " ".join(thumbnail_text)

    # Load performance data for informed selection
    perf_data = {}
    try:
        from performance_analyzer import get_all_videos_from_sheets, analyze_thumbnail_performance
        videos = get_all_videos_from_sheets()
        perf_data = analyze_thumbnail_performance(videos)
    except Exception as e:
        logger.warning(f"Could not load performance data: {e}")

    # Select template and color
    template = select_template(content_format, hook_type, perf_data)
    accent_color = select_accent_color(content_format, content_category, perf_data)
    logger.info(f"Template: {TEMPLATE_NAMES.get(template, template)} | Color: {accent_color}")

    # Generate image prompt
    prompt = generate_image_prompt(template, video_metadata, ghl_feature)

    # Generate base image
    slug = re.sub(r'[^a-z0-9]+', '-', keyword.lower())[:40]
    date_str = today().replace("-", "")
    base_path = THUMBNAILS_DIR / f"{date_str}_{slug}_vA.jpg"

    result_path = call_image_api(prompt, base_path)
    if not result_path:
        logger.error("Image generation failed")
        return []

    # Post-process
    post_process(result_path, template, thumbnail_text, accent_color)

    outputs = [str(result_path)]

    # Return metadata for Sheets tracking
    video_metadata["_thumbnail_data"] = {
        "template_used": template,
        "accent_color": accent_color,
        "thumbnail_text": thumbnail_text,
        "thumbnail_text_word_count": len(thumbnail_text.split()),
        "paths": outputs,
    }

    return outputs


def main():
    parser = argparse.ArgumentParser(description="Generate a YouTube thumbnail.")
    parser.add_argument("--title", default="", help="Video title")
    parser.add_argument("--keyword", default="", help="Target keyword")
    parser.add_argument("--format", default="tutorial", help="Content format")
    parser.add_argument("--hook", default="", help="Hook type used")
    parser.add_argument("--feature", default="", help="GHL feature focus")
    parser.add_argument("--text", default="", help="Thumbnail text (2-4 words)")
    parser.add_argument("--from-json", default="", help="JSON string with all metadata")
    args = parser.parse_args()

    if args.from_json:
        metadata = json.loads(args.from_json)
    else:
        metadata = {
            "video_title": args.title,
            "target_keyword": args.keyword,
            "content_format": args.format,
            "hook_type": args.hook,
            "ghl_feature": args.feature,
            "thumbnail_text": args.text or args.keyword,
        }

    paths = generate_thumbnail(metadata)
    if paths:
        print(f"Generated {len(paths)} thumbnail(s):")
        for p in paths:
            print(f"  {p}")
    else:
        print("Thumbnail generation failed. Check API keys.")


if __name__ == "__main__":
    main()
