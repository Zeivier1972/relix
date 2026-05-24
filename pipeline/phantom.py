from dotenv import load_dotenv
load_dotenv()

import asyncio
import json
import os
import random
import sqlite3
from datetime import datetime, date
from pathlib import Path
from typing import List, Dict, Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME", "")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD", "")
DB_PATH = os.getenv("DB_PATH", "./leads.db")

# ---------------------------------------------------------------------------
# Safety limits — conservative to protect the account
# Start at 8/day for the first week, raise to 15 after two weeks of clean runs
# ---------------------------------------------------------------------------
MAX_DMS_PER_DAY = 8
START_HOUR = 9    # 9 am — avoid very early sends
END_HOUR = 20     # 8 pm — stop earlier to look natural
MIN_DELAY_SEC = 180   # 3 min minimum between DMs
MAX_DELAY_SEC = 480   # 8 min maximum between DMs

SESSION_FILE = Path("./instagram_session.json")

# ---------------------------------------------------------------------------
# Message templates — multiple variations to avoid spam fingerprinting.
# Instagram flags identical messages sent repeatedly.
# ---------------------------------------------------------------------------
_DM_ES_VARIANTS = [
    (
        "Hola {name}! Soy Catherine Gomez, agente inmobiliaria en Miami especializada "
        "en compradores colombianos y latinos. Vi tu publicación y me gustaría ayudarte "
        "con tu búsqueda de casa en Florida. ¿Tienes un momento para hablar?"
    ),
    (
        "Hola {name}! Te escribo porque vi tu publicación sobre propiedades en Florida. "
        "Soy Catherine Gomez P.A., especialista en pre-construcción para compradores "
        "latinos en Miami. ¿Tienes un momento para que te cuente las opciones disponibles?"
    ),
    (
        "Hola {name}, que tal! Vi que estás buscando casa en Florida. Soy Catherine Gomez, "
        "agente inmobiliaria en South Florida especializada en ayudar a familias colombianas "
        "y latinas a comprar su primer hogar. ¿Cuándo podemos hablar?"
    ),
    (
        "Hola {name}! Soy Catherine Gomez, agente en Miami. Vi tu publicación y trabajo "
        "con muchos compradores colombianos y latinos — te puedo guiar en todo el proceso "
        "de compra de pre-construcción sin costo. ¿Te interesa?"
    ),
    (
        "Que tal {name}! Vi que buscas propiedad en Florida. Soy Catherine Gomez P.A., "
        "especialista en pre-construcción para compradores latinos en South Florida. "
        "Con gusto te explico el proceso. ¿Tienes un momento?"
    ),
]

_DM_EN_VARIANTS = [
    (
        "Hi {name}! I'm Catherine Gomez, a real estate agent in South Florida specializing "
        "in pre-construction homes for Latino and Colombian buyers. I saw your post and would "
        "love to help you find your dream home in Florida. Would you like to chat?"
    ),
    (
        "Hey {name}! I saw your post and wanted to reach out — I'm Catherine Gomez, a real "
        "estate specialist in Miami focusing on pre-construction homes for Latin American "
        "buyers. Happy to walk you through the process at no cost. Interested?"
    ),
    (
        "Hi {name}, hope you're doing well! I'm Catherine Gomez P.A., a South Florida real "
        "estate agent specializing in helping Colombian and Latino buyers find pre-construction "
        "homes in Miami. Would you like to know more?"
    ),
]


def _build_message(ig_username: str, source: str) -> str:
    # Extract a natural first name from the username
    raw = ig_username.split(".")[0].split("_")[0]
    name = "".join(c for c in raw if c.isalpha()).capitalize()
    if not name or len(name) < 2:
        name = "amigo" if source != "reddit" else "there"

    # Pick a random variant so no two leads get the identical message
    variants = _DM_EN_VARIANTS if source == "reddit" else _DM_ES_VARIANTS
    return random.choice(variants).format(name=name)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _init_dm_table():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dm_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            instagram_username TEXT NOT NULL,
            source TEXT,
            message_preview TEXT,
            status TEXT DEFAULT 'sent',
            error_message TEXT,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def _count_dms_today() -> int:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT COUNT(*) FROM dm_log WHERE date(sent_at) = ? AND status = 'sent'",
        (date.today().isoformat(),),
    ).fetchone()
    conn.close()
    return row[0] if row else 0


def _already_dmed(username: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT id FROM dm_log WHERE instagram_username = ? AND status = 'sent'",
        (username,),
    ).fetchone()
    conn.close()
    return row is not None


def _log_dm(lead_id: Optional[int], username: str, source: str,
            message: str, status: str = "sent", error: str = None):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """INSERT INTO dm_log
               (lead_id, instagram_username, source, message_preview, status, error_message)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (lead_id, username, source, message[:120], status, error),
    )
    conn.commit()
    conn.close()


def get_pending_dm_leads() -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT l.id, l.name, l.source, l.property_url, q.score
        FROM leads l
        JOIN qualifications q ON l.id = q.lead_id
        WHERE l.source IN ('instagram_hashtags', 'instagram_comments')
          AND q.score IN ('HOT', 'WARM')
          AND l.name NOT IN (
              SELECT instagram_username FROM dm_log WHERE status = 'sent'
          )
        ORDER BY q.score DESC, l.created_at DESC
        LIMIT 50
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_dm_log(limit: int = 100) -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM dm_log ORDER BY sent_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_within_hours() -> bool:
    return START_HOUR <= datetime.now().hour < END_HOUR


async def _human_pause(min_s: float = 1.0, max_s: float = 3.0):
    """Short random pause to simulate reading/thinking."""
    await asyncio.sleep(random.uniform(min_s, max_s))


async def _human_scroll(page: Page, times: int = None):
    """Scroll the page a random amount to simulate browsing."""
    scrolls = times or random.randint(1, 3)
    for _ in range(scrolls):
        await page.mouse.wheel(0, random.randint(200, 600))
        await asyncio.sleep(random.uniform(0.4, 1.2))


# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------

class InstagramDMBot:
    """
    Hidden Chromium bot that sends pre-written DMs to Instagram leads.

    Suspension-prevention measures:
    - Max 8 DMs/day (conservative for account safety)
    - 3–8 minute random delay between each DM
    - Only runs 9am–8pm
    - 5 message variants — no two leads get identical text
    - Simulates human behavior: scrolls feed, pauses on profile before messaging
    - Saves and reuses session cookies to avoid repeated logins
    - Skips private profiles, accounts with no Message button, and already-DMed users
    """

    def __init__(self):
        self.ig_user = INSTAGRAM_USERNAME
        self.ig_pass = INSTAGRAM_PASSWORD
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        _init_dm_table()

    # ------------------------------------------------------------------
    # Browser lifecycle
    # ------------------------------------------------------------------

    async def setup(self, playwright):
        self.browser = await playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1280,800",
            ],
        )
        self.context = await self.browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        await self.context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        if SESSION_FILE.exists():
            try:
                cookies = json.loads(SESSION_FILE.read_text())
                await self.context.add_cookies(cookies)
                print("[Phantom] Restored saved session")
            except Exception as e:
                print(f"[Phantom] Session load failed: {e}")

        self.page = await self.context.new_page()

    async def teardown(self):
        if self.context:
            try:
                cookies = await self.context.cookies()
                SESSION_FILE.write_text(json.dumps(cookies))
            except Exception:
                pass
        if self.browser:
            await self.browser.close()
        print("[Phantom] Browser closed, session saved")

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    async def _dismiss_popup(self):
        for label in ["Not Now", "Not now", "Ahora no", "Dismiss"]:
            try:
                btn = self.page.get_by_role("button", name=label)
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    await _human_pause(0.8, 1.5)
            except Exception:
                pass

    async def login(self) -> bool:
        print("[Phantom] Checking session...")
        await self.page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
        await _human_pause(2, 4)

        if "accounts/login" not in self.page.url:
            home = await self.page.query_selector("svg[aria-label='Home']")
            if home:
                print("[Phantom] Authenticated via saved cookies")
                return True

        print("[Phantom] Session missing — attempting credential login...")
        await self.page.goto(
            "https://www.instagram.com/accounts/login/", wait_until="domcontentloaded"
        )
        await _human_pause(2, 3)

        await self.page.fill("input[name='username'], input[name='email']", self.ig_user)
        await _human_pause(0.6, 1.2)
        await self.page.fill("input[name='password'], input[name='pass']", self.ig_pass)
        await _human_pause(0.5, 1.0)
        try:
            await self.page.get_by_role("button", name="Log in").click(timeout=5000)
        except Exception:
            await self.page.keyboard.press("Enter")

        try:
            await self.page.wait_for_url(
                lambda url: "accounts/login" not in url, timeout=20000
            )
        except Exception:
            print("[Phantom] Login failed — run: python import_ig_cookies.py")
            return False

        await _human_pause(2, 4)
        await self._dismiss_popup()
        await _human_pause(1, 2)
        await self._dismiss_popup()

        cookies = await self.context.cookies()
        SESSION_FILE.write_text(json.dumps(cookies))
        print("[Phantom] Login successful")
        return True

    # ------------------------------------------------------------------
    # Warm-up: browse feed briefly before starting DMs
    # ------------------------------------------------------------------

    async def _warmup_browse(self):
        """Scroll the home feed for 20–40 seconds to look like a real session."""
        print("[Phantom] Warming up — browsing feed...")
        await self.page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
        await _human_pause(2, 4)
        for _ in range(random.randint(3, 6)):
            await _human_scroll(self.page)
            await _human_pause(2, 5)

    # ------------------------------------------------------------------
    # DM
    # ------------------------------------------------------------------

    async def send_dm(self, ig_username: str, message: str,
                      lead_id: Optional[int] = None, source: str = "") -> bool:
        print(f"[Phantom] Visiting @{ig_username}...")
        try:
            await self.page.goto(
                f"https://www.instagram.com/{ig_username}/",
                wait_until="domcontentloaded",
                timeout=20000,
            )
            await _human_pause(2, 4)

            # Check profile not found
            if await self.page.query_selector("h2"):
                text = await self.page.locator("h2").first.inner_text()
                if "isn" in text.lower() or "available" in text.lower():
                    print(f"[Phantom] @{ig_username} not found")
                    _log_dm(lead_id, ig_username, source, message,
                            status="skipped", error="profile not found")
                    return False

            # Simulate reading the profile before messaging (human behavior)
            await _human_scroll(self.page, times=random.randint(1, 2))
            await _human_pause(2, 5)

            # Find Message button
            message_btn = None
            for selector in [
                "div[role='button']:has-text('Message')",
                "button:has-text('Message')",
                "[aria-label='Send message']",
                "a:has-text('Message')",
            ]:
                try:
                    loc = self.page.locator(selector).first
                    if await loc.is_visible(timeout=3000):
                        message_btn = loc
                        break
                except Exception:
                    continue

            if not message_btn:
                print(f"[Phantom] No Message button for @{ig_username} (private/blocked)")
                _log_dm(lead_id, ig_username, source, message,
                        status="skipped", error="no message button")
                return False

            await message_btn.click()
            await _human_pause(2, 4)
            await self._dismiss_popup()

            # Find message input
            input_box = None
            for selector in [
                "div[aria-label='Message']",
                "div[contenteditable='true']",
                "textarea",
            ]:
                try:
                    loc = self.page.locator(selector).last
                    if await loc.is_visible(timeout=5000):
                        input_box = loc
                        break
                except Exception:
                    continue

            if not input_box:
                print(f"[Phantom] No message input for @{ig_username}")
                _log_dm(lead_id, ig_username, source, message,
                        status="error", error="input not found")
                return False

            await input_box.click()
            await _human_pause(0.5, 1.2)

            # Type with human-like cadence — vary speed mid-message
            for char in message:
                delay = random.randint(28, 90)
                if char == " ":
                    delay = random.randint(60, 150)  # slightly longer on spaces
                await input_box.type(char, delay=delay)

            await _human_pause(1.0, 2.5)
            await self.page.keyboard.press("Enter")
            await _human_pause(1.5, 3.0)

            _log_dm(lead_id, ig_username, source, message, status="sent")
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[Phantom] [{ts}] Sent to @{ig_username}: {message[:60]}...")
            return True

        except Exception as e:
            err = str(e)[:200]
            print(f"[Phantom] Error DMing @{ig_username}: {err}")
            _log_dm(lead_id, ig_username, source, message, status="error", error=err)
            return False

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    async def run(self):
        if not _is_within_hours():
            print(f"[Phantom] Outside hours ({START_HOUR}am–{END_HOUR}pm). Aborting.")
            return

        dms_today = _count_dms_today()
        if dms_today >= MAX_DMS_PER_DAY:
            print(f"[Phantom] Daily limit reached ({dms_today}/{MAX_DMS_PER_DAY}). Aborting.")
            return

        leads = get_pending_dm_leads()
        if not leads:
            print("[Phantom] No pending Instagram leads to DM.")
            return

        remaining = MAX_DMS_PER_DAY - dms_today
        batch = leads[:remaining]
        print(
            f"[Phantom] Batch: {len(batch)} leads "
            f"({dms_today} sent today, limit {MAX_DMS_PER_DAY})"
        )

        async with async_playwright() as p:
            await self.setup(p)

            if not await self.login():
                print("[Phantom] Login failed — aborting")
                await self.teardown()
                return

            # Browse feed briefly before starting DMs
            await self._warmup_browse()

            for i, lead in enumerate(batch):
                if not _is_within_hours():
                    print("[Phantom] Reached cutoff hour, stopping")
                    break

                ig_username = lead["name"]
                source = lead.get("source", "")

                if _already_dmed(ig_username):
                    print(f"[Phantom] Already DMed @{ig_username}, skipping")
                    continue

                message = _build_message(ig_username, source)
                await self.send_dm(ig_username, message,
                                   lead_id=lead["id"], source=source)

                if i < len(batch) - 1:
                    # Longer, more varied delay between DMs
                    delay = random.randint(MIN_DELAY_SEC, MAX_DELAY_SEC)
                    # Occasionally take an extra-long break (looks more human)
                    if random.random() < 0.2:
                        extra = random.randint(60, 180)
                        delay += extra
                        print(f"[Phantom] Taking extended break (+{extra}s)")
                    print(
                        f"[Phantom] Waiting {delay // 60}m {delay % 60}s "
                        f"({i + 1}/{len(batch)} done)..."
                    )
                    await asyncio.sleep(delay)

            await self.teardown()

        total = _count_dms_today()
        print(f"[Phantom] Run complete. DMs sent today: {total}/{MAX_DMS_PER_DAY}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def run_dm_bot():
    bot = InstagramDMBot()
    await bot.run()


async def send_dm_to_lead(lead_id: int, ig_username: str, source: str) -> dict:
    """Send a single DM to one specific lead. Used by the dashboard send button."""
    if _already_dmed(ig_username):
        return {"status": "already_sent", "username": ig_username}

    message = _build_message(ig_username, source)
    result = {"username": ig_username, "message_preview": message[:100], "status": "unknown"}

    bot = InstagramDMBot()
    async with async_playwright() as p:
        await bot.setup(p)
        if not await bot.login():
            await bot.teardown()
            result["status"] = "login_failed"
            return result
        success = await bot.send_dm(ig_username, message, lead_id=lead_id, source=source)
        await bot.teardown()
        result["status"] = "sent" if success else "failed"

    return result


def build_dm_preview(ig_username: str, source: str) -> str:
    """Return the pre-written DM that would be sent to this lead."""
    return _build_message(ig_username, source)


if __name__ == "__main__":
    asyncio.run(run_dm_bot())
