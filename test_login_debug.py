from dotenv import load_dotenv
load_dotenv()

import asyncio
import os
from playwright.async_api import async_playwright

IG_USER = os.getenv("INSTAGRAM_USERNAME")
IG_PASS = os.getenv("INSTAGRAM_PASSWORD")

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = await ctx.new_page()

        print("Loading login page...")
        await page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded")
        await asyncio.sleep(4)

        # Handle cookie consent if present
        for label in ["Allow all cookies", "Accept All", "Allow essential and optional cookies"]:
            try:
                btn = page.get_by_role("button", name=label)
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    print(f"Dismissed cookie dialog: {label}")
                    await asyncio.sleep(2)
                    break
            except Exception:
                pass

        await page.screenshot(path="ig_login_before.png", full_page=True)
        print("Screenshot: ig_login_before.png")

        print(f"Filling credentials for @{IG_USER}...")
        await page.fill("input[name='email']", IG_USER)
        await asyncio.sleep(0.8)
        await page.fill("input[name='pass']", IG_PASS)
        await asyncio.sleep(0.6)

        await page.screenshot(path="ig_login_filled.png")
        print("Screenshot: ig_login_filled.png")

        # Click the visible "Log in" button
        try:
            await page.get_by_role("button", name="Log in").click(timeout=5000)
        except Exception:
            await page.keyboard.press("Enter")
        print("Submitted, waiting for redirect...")

        try:
            await page.wait_for_url(
                lambda url: "accounts/login" not in url, timeout=20000
            )
            print(f"Redirected to: {page.url}")
            await asyncio.sleep(3)
            await page.screenshot(path="ig_after_login.png", full_page=True)
            print("Screenshot: ig_after_login.png — LOGIN SUCCESS")
        except Exception:
            await page.screenshot(path="ig_login_failed.png", full_page=True)
            print(f"URL still: {page.url}")
            print("Screenshot: ig_login_failed.png — check for 2FA or challenge")

        await browser.close()

asyncio.run(main())
