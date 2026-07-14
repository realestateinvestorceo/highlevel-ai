# Workflows paused — July 13, 2026 pivot

These three workflows were moved out of `.github/workflows/` (which disables them) as part of the site's pivot from GoHighLevel affiliate content to an AI-education site referring to TownPicked:

- `daily-auto-improve.yml` — daily SEO auto-fixes. Its scripts are tuned to the old 99-page GHL site structure and would mangle or "fix" the new pages incorrectly.
- `daily-video.yml` — daily video pipeline (Perplexity research → Claude script → Ideogram thumbnail → Sheets → Slack → HeyGen). Its prompt files and 20 seed topics (in `prompts/` and `scripts/`) are all GoHighLevel topics and must be rewritten for AI-visibility topics before re-enabling.
- `weekly-seo-report.yml` — weekly GSC/GA4 report. Safe to re-enable after confirming it doesn't reference deleted pages.

## To re-enable one

1. Re-tune its scripts/prompts for the new site (12 pages, TownPicked CTAs, AI-education topics).
2. `git mv .github/workflows-paused/<name>.yml .github/workflows/`
3. Commit and push to main.
