#!/usr/bin/env python3
"""Check indexing status for all highlevel.ai pages via GSC URL Inspection API."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.gsc_analyze import authenticate

service = authenticate("sc-domain:highlevel.ai")

urls = [
    "https://www.highlevel.ai/",
    "https://www.highlevel.ai/pricing-explained.html",
    "https://www.highlevel.ai/highlevel-vs-hubspot.html",
    "https://www.highlevel.ai/mistakes-to-avoid.html",
    "https://www.highlevel.ai/voice-agent-setup.html",
    "https://www.highlevel.ai/workflows-for-agencies.html",
    "https://www.highlevel.ai/best-prompts-sales-support.html",
    "https://www.highlevel.ai/highlevel-for-med-spas.html",
    "https://www.highlevel.ai/highlevel-for-real-estate.html",
    "https://www.highlevel.ai/highlevel-vs-zapier-make.html",
    "https://www.highlevel.ai/highlevel-vs-chatgpt.html",
    "https://www.highlevel.ai/gohighlevel-reviews.html",
    "https://www.highlevel.ai/about.html",
    "https://www.highlevel.ai/editorial-policy.html",
    "https://www.highlevel.ai/contact.html",
    "https://www.highlevel.ai/privacy.html",
    "https://www.highlevel.ai/terms.html",
]

print(f"{'Page':<45} | {'Verdict':<15} | {'Coverage':<30} | Last Crawl")
print("-" * 120)

for url in urls:
    try:
        result = service.urlInspection().index().inspect(body={
            "inspectionUrl": url,
            "siteUrl": "sc-domain:highlevel.ai",
        }).execute()
        status = result.get("inspectionResult", {}).get("indexStatusResult", {})
        verdict = status.get("verdict", "UNKNOWN")
        coverage = status.get("coverageState", "UNKNOWN")
        crawled = status.get("lastCrawlTime", "Never")
        if crawled != "Never":
            crawled = crawled[:10]
        page = url.replace("https://www.highlevel.ai", "") or "/"
        print(f"{page:<45} | {verdict:<15} | {coverage:<30} | {crawled}")
    except Exception as e:
        page = url.replace("https://www.highlevel.ai", "") or "/"
        err = str(e)[:60]
        print(f"{page:<45} | ERROR: {err}")
