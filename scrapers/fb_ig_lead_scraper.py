"""
Competitor Instagram Comment Harvester.

Takes the top advertisers found in the Facebook Ads Library scrape,
finds their Instagram profiles, then scrapes recent post comments
for buyer-intent phrases (EN + ES).

Those commenters become HOT leads — they are actively engaging with
real estate ads in Miami, which is the strongest buying signal short
of a direct inquiry.

Flow:
  1. FB Ads scrape → top advertisers with IG handles or page names
  2. This module maps page names → IG handles (explicit + derived guess)
  3. Playwright + instagram_session.json loads each profile
  4. Scrapes the last N posts, reads visible comments
  5. Returns buyer lead records ready for db.save_fb_ad_lead()
"""

import asyncio
import json
import random
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Set

SESSION_FILE = Path("./instagram_session.json")

# ── Buyer intent phrases (EN + ES) ────────────────────────────────────────────

_INTENT_EN = [
    "interested", "how much", "price?", "available?", "i want to buy",
    "looking to buy", "i'm looking", "i am looking", "where is this",
    "send info", "more info", "contact me", "call me", "dm me",
    "how do i", "what's the price", "is it available", "how can i",
    "first time buyer", "need a realtor", "looking for agent",
    "ready to buy", "how to qualify", "down payment",
]
_INTENT_ES = [
    "me interesa", "cuánto cuesta", "cuanto cuesta", "quiero comprar",
    "información", "informacion", "contacto", "llámame", "llamame",
    "precio", "disponible", "cómo aplico", "como aplico",
    "quiero información", "quiero informacion", "donde queda",
    "qué precio", "que precio", "busco casa", "necesito agente",
    "primera vez", "como califica", "enganche", "prima", "cuanto necesito",
    "me mudo", "mudandome", "interesado", "interesada",
]
_ALL_INTENT = _INTENT_EN + _INTENT_ES

_IG_HANDLE_RE = re.compile(r"(?:instagram\.com/|@)([A-Za-z0-9_.]{3,30})")


def _has_buyer_intent(text: str):
    """Return (has_intent: bool, language: str)."""
    t = text.lower()
    for kw in _INTENT_ES:
        if kw in t:
            return True, "spanish"
    for kw in _INTENT_EN:
        if kw in t:
            return True, "english"
    return False, "unknown"


def _page_name_to_ig_guess(name: str) -> str:
    """
    Derive a probable Instagram handle from a Facebook page name.
    e.g. "Jenny Amaya Realtor" → "jennyamayarealtor"
    """
    if not name:
        return ""
    clean = name.lower()
    clean = re.sub(r"[^a-z0-9_.]", "", clean.replace(" ", "").replace("-", ""))
    return clean[:30] if len(clean) >= 3 else ""


def extract_ig_handles(ads: List[Dict[str, Any]], top_n: int = 30) -> List[str]:
    """
    From a list of FB ad records, extract IG handles to scan.

    Priority:
      1. Explicit @handle or instagram.com/handle found in the ad body
      2. Page name normalized to a probable IG handle
    Returns deduplicated list, capped at top_n most-days-running advertisers.
    """
    # Sort by days_running so we target the most active advertisers first
    sorted_ads = sorted(ads, key=lambda a: a.get("days_running", 0), reverse=True)

    handles: list = []
    seen: Set[str] = set()
    seen_pages: Set[str] = set()

    for ad in sorted_ads:
        page = (ad.get("page_name") or "").strip()
        if page in seen_pages:
            continue
        seen_pages.add(page)
        if len(seen_pages) > top_n:
            break

        # Explicit handle from ad body
        body = ad.get("ad_body", "") or ""
        explicit = _IG_HANDLE_RE.findall(body)
        for h in explicit:
            h = h.lower().strip(".")
            if h and h not in seen and len(h) >= 3:
                seen.add(h)
                handles.append(h)

        # Also use the stored ig_handle field if present
        stored = (ad.get("ig_handle") or "").lower().strip(".")
        if stored and stored not in seen and len(stored) >= 3:
            seen.add(stored)
            handles.append(stored)

        # Derived guess from page name
        guess = _page_name_to_ig_guess(page)
        if guess and guess not in seen:
            seen.add(guess)
            handles.append(guess)

    return handles


# ── Playwright comment scraper ────────────────────────────────────────────────

async def _scrape_one_account(ctx, username: str,
                               max_posts: int = 4) -> List[Dict[str, Any]]:
    """
    Scrape buyer-intent comments from an Instagram account's recent posts.
    Returns lead records.
    """
    leads: List[Dict[str, Any]] = []

    # Step 1: get post URLs from profile grid
    page = await ctx.new_page()
    post_urls: List[str] = []
    try:
        await page.goto(
            f"https://www.instagram.com/{username}/",
            wait_until="domcontentloaded", timeout=20000,
        )
        await asyncio.sleep(random.uniform(2, 3))

        # Check for 404 / private / not found
        txt = await page.inner_text("body")
        if any(x in txt for x in ("Sorry, this page", "Page Not Found",
                                   "isn't available", "This account is private")):
            return []

        link_els = await page.query_selector_all("a[href*='/p/']")
        seen_hrefs: Set[str] = set()
        for el in link_els:
            href = await el.get_attribute("href") or ""
            if href and href not in seen_hrefs and "/p/" in href:
                seen_hrefs.add(href)
                post_urls.append(f"https://www.instagram.com{href}")
            if len(post_urls) >= max_posts:
                break
    except Exception as e:
        print(f"[FBIGLeads] @{username} profile error: {e}")
    finally:
        await page.close()

    if not post_urls:
        return []

    # Step 2: visit each post and read comments
    for post_url in post_urls:
        post_page = await ctx.new_page()
        try:
            await post_page.goto(post_url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(random.uniform(2, 3))

            # Comments are in <li> elements under the comment list
            comment_items = await post_page.query_selector_all("ul li")
            for li in comment_items[:60]:
                try:
                    text = (await li.inner_text() or "").strip()
                    if len(text) < 5:
                        continue
                    has_intent, lang = _has_buyer_intent(text)
                    if not has_intent:
                        continue

                    # Extract commenter handle from the first <a> in the <li>
                    handle_el = await li.query_selector("a")
                    commenter = ""
                    if handle_el:
                        href = (await handle_el.get_attribute("href") or "").strip("/")
                        parts = href.split("/")
                        commenter = parts[-1] if parts else ""

                    leads.append({
                        "ad_id":            "",
                        "profile_name":     commenter or f"commenter_on_{username}",
                        "profile_url":      (
                            f"https://www.instagram.com/{commenter}/"
                            if commenter else post_url
                        ),
                        "comment_text":     text[:500],
                        "language":         lang,
                        "instagram_handle": commenter or None,
                        "score":            "HOT",
                        "source":           "instagram_comments",
                        "matched_account":  username,
                        "post_url":         post_url,
                    })
                except Exception:
                    continue
        except Exception as e:
            print(f"[FBIGLeads] @{username} post error: {e}")
        finally:
            await post_page.close()

        await asyncio.sleep(random.uniform(1.5, 3.0))

    return leads


async def scrape_competitor_ig_comments(
    ig_handles: List[str],
    max_accounts: int = 20,
    max_posts_each: int = 4,
) -> List[Dict[str, Any]]:
    """
    Main entry point. Given a list of competitor IG handles,
    scrapes their recent post comments for buyer-intent leads.

    Returns list of lead dicts ready for db.save_fb_ad_lead().
    """
    if not SESSION_FILE.exists():
        print("[FBIGLeads] No instagram_session.json — skipping IG comment scrape")
        return []

    if not ig_handles:
        print("[FBIGLeads] No IG handles to scan")
        return []

    targets = ig_handles[:max_accounts]
    print(f"[FBIGLeads] Scanning {len(targets)} competitor IG accounts for buyer comments...")

    all_leads: List[Dict[str, Any]] = []

    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage",
                      "--disable-blink-features=AutomationControlled"],
            )
            ctx = await browser.new_context(
                viewport={"width": 1280, "height": 800},
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

            # Load Instagram session cookies
            cookies = json.loads(SESSION_FILE.read_text())
            await ctx.add_cookies(cookies)

            # Verify session is still valid
            check = await ctx.new_page()
            await check.goto("https://www.instagram.com/",
                             wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(2)
            if "accounts/login" in check.url:
                print("[FBIGLeads] Instagram session expired — skipping")
                await check.close()
                await browser.close()
                return []
            await check.close()
            print("[FBIGLeads] Instagram session valid")

            for handle in targets:
                try:
                    leads = await _scrape_one_account(ctx, handle, max_posts_each)
                    if leads:
                        print(f"[FBIGLeads] @{handle}: {len(leads)} buyer leads")
                        all_leads.extend(leads)
                    else:
                        print(f"[FBIGLeads] @{handle}: no buyer leads (or profile not found)")
                    await asyncio.sleep(random.uniform(3, 6))
                except Exception as e:
                    print(f"[FBIGLeads] @{handle} failed: {e}")

            await browser.close()

    except Exception as e:
        print(f"[FBIGLeads] Fatal error: {e}")

    print(f"[FBIGLeads] Total buyer leads from competitor IG: {len(all_leads)}")
    return all_leads
