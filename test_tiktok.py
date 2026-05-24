from dotenv import load_dotenv
load_dotenv()
import asyncio
from scrapers.tiktok_playwright import TikTokPlaywrightScraper

async def main():
    s = TikTokPlaywrightScraper()
    leads = await s.scrape_all(
        search_keywords=["quiero comprar casa en miami"],
        max_videos_per_keyword=3,
    )
    print(f"\nResult: {len(leads)} buyer-intent comments found")
    for l in leads:
        name = l.get("name", "")
        text = (l.get("raw_data") or {}).get("text", "")[:80]
        print(f"  @{name}: {text}")

asyncio.run(main())
