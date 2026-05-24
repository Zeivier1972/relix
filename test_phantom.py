from dotenv import load_dotenv
load_dotenv()

import asyncio
import json
import random
from pathlib import Path
from playwright.async_api import async_playwright

SESSION_FILE = Path("./instagram_session.json")
TEST_MESSAGE = (
    "RELIX TEST - Please ignore this message. "
    "Automated pipeline verification."
)


async def test_dm(recipient: str):
    print(f"\n[Test] Sending DM to @{recipient} via profile page...")

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

        # Load session cookies
        if not SESSION_FILE.exists():
            print("[Test] No session file — run import_ig_cookies.py first")
            await browser.close()
            return
        cookies = json.loads(SESSION_FILE.read_text())
        await ctx.add_cookies(cookies)
        print(f"[Test] Loaded {len(cookies)} cookies")

        page = await ctx.new_page()

        # Verify session
        await page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
        await asyncio.sleep(3)
        if "accounts/login" in page.url:
            print("[Test] Session expired — re-run import_ig_cookies.py")
            await browser.close()
            return
        print("[Test] Session valid")

        # Go to the recipient's profile
        print(f"[Test] Loading profile https://www.instagram.com/{recipient}/")
        await page.goto(
            f"https://www.instagram.com/{recipient}/",
            wait_until="domcontentloaded",
            timeout=20000,
        )
        await asyncio.sleep(random.uniform(2, 4))
        await page.screenshot(path="test_profile.png")
        print("[Test] Screenshot: test_profile.png")

        # Find and click the Message button
        message_btn = None
        for selector in [
            "div[role='button']:has-text('Message')",
            "button:has-text('Message')",
            "[aria-label='Send message']",
            "a:has-text('Message')",
        ]:
            try:
                loc = page.locator(selector).first
                if await loc.is_visible(timeout=3000):
                    message_btn = loc
                    print(f"[Test] Found Message button: {selector}")
                    break
            except Exception:
                continue

        if not message_btn:
            print("[Test] No Message button found (private account or doesn't follow back)")
            await page.screenshot(path="test_phantom_debug.png")
            await browser.close()
            return

        await message_btn.click()
        await asyncio.sleep(random.uniform(2, 4))
        await page.screenshot(path="test_after_message_click.png")
        print("[Test] Screenshot: test_after_message_click.png")

        # Dismiss any pop-up
        for label in ["Not Now", "Not now"]:
            try:
                btn = page.get_by_role("button", name=label)
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    await asyncio.sleep(1)
            except Exception:
                pass

        # Find message input
        input_box = None
        for selector in [
            "div[aria-label='Message']",
            "div[contenteditable='true']",
            "textarea",
        ]:
            try:
                loc = page.locator(selector).last
                if await loc.is_visible(timeout=5000):
                    input_box = loc
                    print(f"[Test] Found input: {selector}")
                    break
            except Exception:
                continue

        if not input_box:
            print("[Test] Could not find message input")
            await page.screenshot(path="test_phantom_debug.png")
            await browser.close()
            return

        await input_box.click()
        await asyncio.sleep(0.5)
        for char in TEST_MESSAGE:
            await input_box.type(char, delay=random.randint(30, 80))
        await asyncio.sleep(1)
        await page.keyboard.press("Enter")
        await asyncio.sleep(2)

        await page.screenshot(path="test_dm_sent.png")
        print(f"\n[Test] SUCCESS — DM sent to @{recipient}")
        print(f"[Test] Message: {TEST_MESSAGE}")
        print("[Test] Screenshot: test_dm_sent.png")

        cookies = await ctx.cookies()
        SESSION_FILE.write_text(json.dumps(cookies))
        await browser.close()


asyncio.run(test_dm("optionsscanner.io"))
