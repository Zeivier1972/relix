"""
Run this ONCE on your local machine to capture your Instagram session.

  python save_ig_session.py

It opens a real Chrome window. Log in to Instagram normally (including
any 2-factor code). Once you're on the home feed, press Enter in this
terminal. The script prints a base64 string — copy it and add it to
Railway as the environment variable INSTAGRAM_SESSION_B64.

The session typically lasts 30-90 days. When it expires just re-run
this script and update the Railway variable.
"""
import asyncio
import base64
import json
import sys

async def main():
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("ERROR: playwright not installed. Run: pip install playwright && playwright install chromium")
        sys.exit(1)

    print("Opening Instagram in a real browser window...")
    print("Log in normally (enter password, complete any 2-FA).")
    print("Once you see the Instagram home feed, come back here and press Enter.\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--start-maximized"],
        )
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await ctx.new_page()
        await page.goto("https://www.instagram.com/accounts/login/")

        input("Press Enter AFTER you are fully logged in and see the Instagram home feed: ")

        cookies = await ctx.cookies()
        session_json  = json.dumps(cookies)
        session_b64   = base64.b64encode(session_json.encode()).decode()

        # Also save locally for immediate use
        with open("instagram_session.json", "w") as f:
            f.write(session_json)
        print("\n[OK] instagram_session.json saved locally.")

        print("\n" + "=" * 70)
        print("Copy the entire string below and add it to Railway as:")
        print("  Variable name:  INSTAGRAM_SESSION_B64")
        print("  Variable value: (the string below)")
        print("=" * 70)
        print(session_b64)
        print("=" * 70)
        print("\nDone. Railway will restore this session automatically on every deploy.")

        await browser.close()

asyncio.run(main())
