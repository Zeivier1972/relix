from dotenv import load_dotenv
load_dotenv()

import asyncio
import json
import random
from pathlib import Path
from typing import List, Dict, Any, Optional
from playwright.async_api import async_playwright, BrowserContext

SESSION_FILE = Path("./instagram_session.json")

BUYER_INTENT_KEYWORDS = [
    "quiero comprar", "busco casa", "how do i qualify", "cuanto necesito",
    "down payment", "me interesa", "want to buy", "looking to buy",
    "como aplico", "puedo comprar", "primera casa", "primer casa",
    "quiero una casa", "me mudo", "moving to miami", "moving to florida",
    "cuanto cuesta", "como hago", "first time buyer", "pre construction",
    "pre-construction", "necesito casa", "mudandome", "comprar casa",
    "comprar propiedad", "busco propiedad", "vivir en florida",
    "mudarse a florida", "mudarse a miami", "comprando casa",
]

HASHTAGS = [
    "quieromprarencasa",
    "buscandocasaenmiami",
    "mudanzaaflorida",
    "colombianosbuscandocasa",
    "comprarcasausa",
    "primeracasaenusa",
    "inversionistascolombianos",
    "vivireenflorida",
    "comprarcasaenflorida",
    "casapropia",
    "colombianosenmiami",
    "colombianosenusa",
]

COMMENT_ACCOUNTS = [
    "catherinegomez_realtor",
    "preconstruccionmiami",
    "colombianosenmiami",
    "miamirealestate",
    "luxurymiamirealestate",
]


def _has_buyer_intent(text: str) -> bool:
    tl = text.lower()
    return any(kw in tl for kw in BUYER_INTENT_KEYWORDS)


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
    if SESSION_FILE.exists():
        cookies = json.loads(SESSION_FILE.read_text())
        await ctx.add_cookies(cookies)
    return browser, ctx


class InstagramPlaywrightScraper:
    """
    Scrapes buyer-intent posts from Instagram hashtags and comments for Catherine Gomez P.A.
    Targets Colombian and Latino buyers searching for pre-construction homes in South Florida.
    Primary: intercepts Instagram's internal JSON API responses.
    Fallback: extracts post links from the hashtag grid DOM.
    """

    # ------------------------------------------------------------------
    # API response parsers
    # ------------------------------------------------------------------

    def _parse_response(self, data: Any, hashtag: str) -> List[Dict]:
        leads = []
        if not isinstance(data, dict):
            return leads

        # /api/v1/tags/{tag}/sections/ shape
        for section in data.get("sections") or []:
            for item in (section.get("layout_content") or {}).get("medias") or []:
                media = item.get("media") or {}
                lead = self._media_to_lead(media, hashtag)
                if lead:
                    leads.append(lead)

        # GraphQL shape
        hashtag_node = (
            (data.get("data") or {}).get("hashtag")
            or (data.get("graphql") or {}).get("hashtag")
            or {}
        )
        for key in ("edge_hashtag_to_media", "edge_hashtag_to_top_posts"):
            for edge in (hashtag_node.get(key) or {}).get("edges") or []:
                lead = self._gql_node_to_lead(edge.get("node") or {}, hashtag)
                if lead:
                    leads.append(lead)

        return leads

    def _media_to_lead(self, media: Dict, hashtag: str) -> Optional[Dict]:
        cap = media.get("caption") or {}
        caption = cap.get("text", "") if isinstance(cap, dict) else str(cap)
        if not _has_buyer_intent(caption):
            return None
        username = (media.get("user") or {}).get("username") or ""
        if not username:
            return None
        shortcode = media.get("code") or media.get("pk") or ""
        post_url = f"https://www.instagram.com/p/{shortcode}/" if shortcode else f"https://www.instagram.com/{username}/"
        return {
            "name": username,
            "property_url": post_url,
            "source": "instagram_hashtags",
            "raw_data": {"caption": caption[:500], "ownerUsername": username,
                         "hashtag": hashtag, "url": post_url},
        }

    def _gql_node_to_lead(self, node: Dict, hashtag: str) -> Optional[Dict]:
        shortcode = node.get("shortcode") or ""
        edges = (node.get("edge_media_to_caption") or {}).get("edges") or []
        caption = edges[0].get("node", {}).get("text", "") if edges else ""
        if not _has_buyer_intent(caption):
            return None
        username = (node.get("owner") or {}).get("username") or ""
        if not username:
            return None
        post_url = f"https://www.instagram.com/p/{shortcode}/"
        return {
            "name": username,
            "property_url": post_url,
            "source": "instagram_hashtags",
            "raw_data": {"caption": caption[:500], "ownerUsername": username,
                         "hashtag": hashtag, "url": post_url},
        }

    # ------------------------------------------------------------------
    # Hashtag scraper
    # ------------------------------------------------------------------

    async def _scrape_hashtag(self, ctx: BrowserContext, hashtag: str,
                               max_posts: int = 12) -> List[Dict]:
        leads: List[Dict] = []
        captured: List[Any] = []
        page = await ctx.new_page()

        async def on_response(r):
            if any(kw in r.url for kw in ("api/v1/tags", "graphql/query", "tag_feed")):
                try:
                    captured.append(await r.json())
                except Exception:
                    pass

        page.on("response", on_response)
        try:
            await page.goto(
                f"https://www.instagram.com/explore/tags/{hashtag}/",
                wait_until="domcontentloaded",
                timeout=20000,
            )
            await asyncio.sleep(random.uniform(3, 5))
            for _ in range(2):
                await page.mouse.wheel(0, random.randint(400, 700))
                await asyncio.sleep(random.uniform(1.5, 2.5))

            for resp_data in captured:
                leads.extend(self._parse_response(resp_data, hashtag))
                if len(leads) >= max_posts:
                    break

            # DOM fallback: get post links from the grid
            if len(leads) < 3:
                link_els = await page.query_selector_all("a[href*='/p/']")
                seen_urls = {l["property_url"] for l in leads}
                for el in link_els:
                    if len(leads) >= max_posts:
                        break
                    href = await el.get_attribute("href")
                    if not href:
                        continue
                    post_url = f"https://www.instagram.com{href}"
                    if post_url in seen_urls:
                        continue
                    seen_urls.add(post_url)
                    img = await el.query_selector("img")
                    alt = (await img.get_attribute("alt") or "") if img else ""
                    if _has_buyer_intent(alt):
                        leads.append({
                            "name": f"ig_user_{hashtag}",
                            "property_url": post_url,
                            "source": "instagram_hashtags",
                            "raw_data": {"hashtag": hashtag, "alt": alt[:200], "url": href},
                        })
        except Exception as e:
            print(f"[IGPlay] #{hashtag}: {e}")
        finally:
            await page.close()

        return leads[:max_posts]

    # ------------------------------------------------------------------
    # Comment scraper (target RE accounts)
    # ------------------------------------------------------------------

    async def _scrape_account_comments(self, ctx: BrowserContext, username: str,
                                        max_posts: int = 3) -> List[Dict]:
        leads: List[Dict] = []
        page = await ctx.new_page()
        try:
            await page.goto(f"https://www.instagram.com/{username}/",
                            wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(random.uniform(2, 4))

            link_els = await page.query_selector_all("a[href*='/p/']")
            post_urls: List[str] = []
            seen: set = set()
            for el in link_els:
                href = await el.get_attribute("href")
                if href and href not in seen and "/p/" in href:
                    seen.add(href)
                    post_urls.append(f"https://www.instagram.com{href}")
                if len(post_urls) >= max_posts:
                    break
        except Exception as e:
            print(f"[IGPlay] Comments profile @{username}: {e}")
            await page.close()
            return []
        finally:
            await page.close()

        for post_url in post_urls:
            post_page = await ctx.new_page()
            try:
                await post_page.goto(post_url, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(random.uniform(2, 3))

                # Each comment is a <li> — grab text and try to pull the @-handle from the first <a>
                comment_items = await post_page.query_selector_all("ul li")
                for li in comment_items[:50]:
                    try:
                        text = await li.inner_text()
                        if not text or not _has_buyer_intent(text):
                            continue
                        handle = ""
                        handle_el = await li.query_selector("a")
                        if handle_el:
                            href = await handle_el.get_attribute("href") or ""
                            handle = href.strip("/").split("/")[-1]
                        leads.append({
                            "name": handle or f"commenter_{username}",
                            "property_url": post_url,
                            "source": "instagram_comments",
                            "raw_data": {"text": text[:300],
                                         "_matched_account": username,
                                         "postUrl": post_url},
                        })
                    except Exception:
                        continue
            except Exception as e:
                print(f"[IGPlay] Comments post {post_url}: {e}")
            finally:
                await post_page.close()

            await asyncio.sleep(random.uniform(1.5, 3.0))

        return leads

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def scrape_all(self, hashtags: List[str] = None,
                          comment_accounts: List[str] = None,
                          max_posts_per_hashtag: int = 12) -> List[Dict]:
        hashtags = hashtags or HASHTAGS
        comment_accounts = comment_accounts or COMMENT_ACCOUNTS

        if not SESSION_FILE.exists():
            print("[IGPlay] No instagram_session.json — run import_ig_cookies.py first")
            return []

        all_leads: List[Dict] = []

        async with async_playwright() as p:
            browser, ctx = await _build_context(p)
            try:
                # Verify session
                check = await ctx.new_page()
                await check.goto("https://www.instagram.com/", wait_until="domcontentloaded",
                                 timeout=15000)
                await asyncio.sleep(2)
                if "accounts/login" in check.url:
                    print("[IGPlay] Session expired — re-run import_ig_cookies.py")
                    await check.close()
                    return []
                await check.close()
                print("[IGPlay] Session valid")

                for hashtag in hashtags:
                    try:
                        leads = await self._scrape_hashtag(ctx, hashtag, max_posts_per_hashtag)
                        all_leads.extend(leads)
                        print(f"[IGPlay] #{hashtag}: {len(leads)} leads")
                        await asyncio.sleep(random.uniform(3, 7))
                    except Exception as e:
                        print(f"[IGPlay] #{hashtag} failed: {e}")

                for account in comment_accounts:
                    try:
                        leads = await self._scrape_account_comments(ctx, account)
                        all_leads.extend(leads)
                        print(f"[IGPlay] @{account} comments: {len(leads)} leads")
                        await asyncio.sleep(random.uniform(4, 8))
                    except Exception as e:
                        print(f"[IGPlay] @{account} comments failed: {e}")

            finally:
                await browser.close()

        print(f"[IGPlay] Total: {len(all_leads)} leads")
        return all_leads
