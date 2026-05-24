from dotenv import load_dotenv
load_dotenv()
import asyncio, json
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        captured_urls = []
        page = await ctx.new_page()

        async def on_response(r):
            captured_urls.append(r.url)

        page.on("response", on_response)

        keyword = "quiero comprar casa en miami"
        encoded = keyword.replace(" ", "%20")
        await page.goto(
            f"https://www.tiktok.com/search?q={encoded}&type=video",
            wait_until="networkidle",
            timeout=30000,
        )
        await asyncio.sleep(5)
        await page.screenshot(path="debug_tiktok.png")

        # Show all API calls made
        api_calls = [u for u in captured_urls if "tiktok.com/api" in u or "tiktok.com/search" in u]
        print(f"API calls intercepted: {len(api_calls)}")
        for url in api_calls[:20]:
            print(f"  {url[:120]}")

        # Show all video links in DOM
        links = await page.query_selector_all("a[href*='/video/']")
        print(f"\nVideo links in DOM: {len(links)}")
        for link in links[:10]:
            href = await link.get_attribute("href")
            print(f"  {href}")

        # Show page title and URL to confirm what loaded
        print(f"\nPage URL: {page.url}")
        print(f"Page title: {await page.title()}")

        await browser.close()

asyncio.run(main())
