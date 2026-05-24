from dotenv import load_dotenv
load_dotenv()

import asyncio
import os
import random
from typing import List, Dict, Any, Optional
from playwright.async_api import async_playwright, BrowserContext

FACEBOOK_GROUPS = [
    # ── Latino / Colombian focused ────────────────────────────────────────
    "https://www.facebook.com/groups/colombianosenflorida",
    "https://www.facebook.com/groups/colombianosenmiami",
    "https://www.facebook.com/groups/colombiansinusa",
    "https://www.facebook.com/groups/colombianosenorlando",
    "https://www.facebook.com/groups/colombianosinvertendousa",
    "https://www.facebook.com/groups/latinosenflorida",
    "https://www.facebook.com/groups/latinosinmiami",
    "https://www.facebook.com/groups/hispanosenmiami",
    "https://www.facebook.com/groups/venezolanosenflorida",
    "https://www.facebook.com/groups/venezolanosenmiami",
    "https://www.facebook.com/groups/argentinosenflorida",
    "https://www.facebook.com/groups/mexicanosenflorida",
    "https://www.facebook.com/groups/puertorriquenosenflorida",

    # ── South Florida cities ──────────────────────────────────────────────
    "https://www.facebook.com/groups/miamirealestate",
    "https://www.facebook.com/groups/miamihomebuyers",
    "https://www.facebook.com/groups/miamiinvestors",
    "https://www.facebook.com/groups/homesteadfloridarealstate",
    "https://www.facebook.com/groups/homesteadfloridahomes",
    "https://www.facebook.com/groups/brickellmiamirealestate",
    "https://www.facebook.com/groups/wynwoodhomes",
    "https://www.facebook.com/groups/miamibeachhomes",
    "https://www.facebook.com/groups/coralgablesproperties",
    "https://www.facebook.com/groups/doralfloridarealstate",
    "https://www.facebook.com/groups/hialeahrealestate",
    "https://www.facebook.com/groups/kendallfloridarealstate",
    "https://www.facebook.com/groups/sunriseflrealestate",
    "https://www.facebook.com/groups/pembrokepiresrealestate",
    "https://www.facebook.com/groups/fortlauderdalerealestate",
    "https://www.facebook.com/groups/bocaratonhomes",
    "https://www.facebook.com/groups/westpalmbeachrealestate",
    "https://www.facebook.com/groups/wellingtonflhomes",
    "https://www.facebook.com/groups/orlandorealestate",
    "https://www.facebook.com/groups/orlandohomebuyers",
    "https://www.facebook.com/groups/tamparealestate",
    "https://www.facebook.com/groups/tampahomebuyers",

    # ── Pre-construction specific ─────────────────────────────────────────
    "https://www.facebook.com/groups/preconstruccionflorida",
    "https://www.facebook.com/groups/preconstruccionmiami",
    "https://www.facebook.com/groups/floridanewconstruction",
    "https://www.facebook.com/groups/miamiNewConstruction",
    "https://www.facebook.com/groups/preconstruccionusa",
    "https://www.facebook.com/groups/newhomesflorida",
    "https://www.facebook.com/groups/nuevascasasenmiami",
    "https://www.facebook.com/groups/nuevaconstruccionmiami",

    # ── Real estate investing ─────────────────────────────────────────────
    "https://www.facebook.com/groups/floridainvestors",
    "https://www.facebook.com/groups/miamiinvestmentproperties",
    "https://www.facebook.com/groups/southfloridainvestors",
    "https://www.facebook.com/groups/realestateinvestorsflorida",
    "https://www.facebook.com/groups/inversionistasflorida",
    "https://www.facebook.com/groups/inversionistasmiami",
    "https://www.facebook.com/groups/airbnbmiami",
    "https://www.facebook.com/groups/shorttermrentalsflorida",
]

BUYER_INTENT_KEYWORDS = [
    "quiero comprar", "busco casa", "cuanto necesito", "down payment",
    "me interesa comprar", "primera casa", "primer casa", "como aplico",
    "puedo comprar", "want to buy", "looking to buy", "how do i qualify",
    "comprar casa", "comprar propiedad", "busco propiedad", "busco apartamento",
    "me mudo", "mudanza", "mudandome", "vivir en florida", "vivir en miami",
    "cuanto cuesta", "que necesito para comprar", "como hago para comprar",
    "first time buyer", "pre construction", "pre-construction",
    "requisitos para comprar", "financiamiento para casa", "prestamo hipotecario",
]


def _has_buyer_intent(text: str) -> bool:
    tl = text.lower()
    return any(kw in tl for kw in BUYER_INTENT_KEYWORDS)


def _build_fb_cookies() -> Optional[List[Dict]]:
    c_user = os.getenv("FACEBOOK_C_USER", "").strip()
    xs = os.getenv("FACEBOOK_XS", "").strip()
    if not c_user or not xs:
        return None
    return [
        {
            "name": "c_user",
            "value": c_user,
            "domain": ".facebook.com",
            "path": "/",
            "httpOnly": False,
            "secure": True,
            "sameSite": "None",
        },
        {
            "name": "xs",
            "value": xs,
            "domain": ".facebook.com",
            "path": "/",
            "httpOnly": True,
            "secure": True,
            "sameSite": "None",
        },
    ]


async def _build_context(playwright):
    browser = await playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ],
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
    cookies = _build_fb_cookies()
    if cookies:
        await ctx.add_cookies(cookies)
    return browser, ctx


class FacebookPlaywrightScraper:
    """
    Scrapes buyer-intent posts from Colombian and Latino Facebook groups for Catherine Gomez P.A.
    Targets buyers searching for pre-construction homes in South Florida.
    Authenticates via FACEBOOK_C_USER + FACEBOOK_XS session cookies.
    """

    async def _scrape_group(self, ctx: BrowserContext, group_url: str,
                             max_posts: int = 30) -> List[Dict]:
        leads: List[Dict] = []
        page = await ctx.new_page()

        try:
            await page.goto(group_url, wait_until="domcontentloaded", timeout=25000)
            await asyncio.sleep(random.uniform(3, 5))

            if "login" in page.url or await page.query_selector("input[name='email']"):
                print(f"[FBPlay] Not authenticated — cookies may be expired for {group_url}")
                return []

            # Scroll to load more posts
            for _ in range(4):
                await page.mouse.wheel(0, random.randint(600, 1000))
                await asyncio.sleep(random.uniform(2, 3))

            articles = await page.query_selector_all("[role='article']")
            print(f"[FBPlay] {group_url.split('/')[-1]}: {len(articles)} article elements")

            seen_snippets: set = set()
            for article in articles[:max_posts]:
                try:
                    text = await article.inner_text()
                    if not text or not _has_buyer_intent(text):
                        continue

                    snippet = text[:100].strip()
                    if snippet in seen_snippets:
                        continue
                    seen_snippets.add(snippet)

                    # Author: try semantic selectors first, then first short link text
                    author = ""
                    for sel in ("h2 a", "h3 a", "strong a", "[data-ad-preview='message'] + div a"):
                        try:
                            el = article.locator(sel).first
                            if await el.is_visible(timeout=800):
                                author = (await el.inner_text()).strip()
                                if author:
                                    break
                        except Exception:
                            pass

                    if not author:
                        link_els = await article.query_selector_all("a[role='link']")
                        for link in link_els[:6]:
                            lt = (await link.inner_text()).strip()
                            if lt and 2 < len(lt) < 60:
                                author = lt
                                break

                    if not author:
                        continue

                    # Post URL from timestamp or "see more" link
                    post_url = group_url
                    for sel in ("a[href*='/posts/']", "a[href*='story_fbid']",
                                "a[href*='permalink']"):
                        try:
                            el = article.locator(sel).first
                            href = await el.get_attribute("href")
                            if href:
                                post_url = href if href.startswith("http") else f"https://www.facebook.com{href}"
                                break
                        except Exception:
                            pass

                    leads.append({
                        "name": author,
                        "property_url": post_url,
                        "source": "facebook_groups",
                        "raw_data": {
                            "postText": text[:500],
                            "postAuthor": author,
                            "groupUrl": group_url,
                            "postUrl": post_url,
                        },
                    })

                except Exception as e:
                    print(f"[FBPlay] Article parse error: {e}")

        except Exception as e:
            print(f"[FBPlay] {group_url}: {e}")
        finally:
            await page.close()

        return leads

    async def scrape_all(self, group_urls: List[str] = None,
                          max_posts_per_group: int = 30) -> List[Dict]:
        group_urls = group_urls or FACEBOOK_GROUPS
        all_leads: List[Dict] = []

        if not _build_fb_cookies():
            print("[FBPlay] No Facebook cookies — set FACEBOOK_C_USER and FACEBOOK_XS in .env")
            return []

        async with async_playwright() as p:
            browser, ctx = await _build_context(p)
            try:
                for group_url in group_urls:
                    try:
                        leads = await self._scrape_group(ctx, group_url, max_posts_per_group)
                        all_leads.extend(leads)
                        print(f"[FBPlay] {group_url.split('/')[-1]}: {len(leads)} buyer-intent posts")
                        await asyncio.sleep(random.uniform(4, 8))
                    except Exception as e:
                        print(f"[FBPlay] {group_url} failed: {e}")
            finally:
                await browser.close()

        print(f"[FBPlay] Total: {len(all_leads)} leads")
        return all_leads
