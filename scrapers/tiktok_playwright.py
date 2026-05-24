from dotenv import load_dotenv
load_dotenv()

import asyncio
import random
from typing import List, Dict, Any, Optional
from playwright.async_api import async_playwright, BrowserContext, Page

# Spanish buyer-intent keywords to search TikTok for
SEARCH_KEYWORDS = [
    "quiero comprar casa en miami",
    "como comprar casa en florida",
    "colombianos comprando casa usa",
    "primera casa en estados unidos",
]

BUYER_INTENT_KEYWORDS = [
    "quiero comprar", "busco casa", "cuanto necesito", "down payment",
    "como aplico", "puedo comprar", "primera casa", "primer casa",
    "me interesa", "quiero una casa", "me mudo", "moving to miami",
    "cuanto cuesta", "como hago", "first time buyer", "pre construction",
    "comprar casa", "comprar propiedad", "busco propiedad", "mudandome",
    "que necesito para comprar", "como financio", "financiamiento",
    "hipoteca", "mortgage", "requisitos para comprar", "cuanto de inicial",
    "enganche", "prestamo", "where do i start", "how do i qualify",
    "want to buy", "looking to buy", "moving to florida",
]

MAX_VIDEOS_PER_KEYWORD = 5
MAX_COMMENTS_PER_VIDEO = 50


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
    return browser, ctx


class TikTokPlaywrightScraper:
    """
    Search TikTok for Spanish buyer-intent keywords, find the top N videos,
    then scrape comments on those videos for people showing purchase intent.
    Uses API response interception as primary method; DOM as fallback.
    No login required — all public content.
    """

    # ------------------------------------------------------------------
    # Search: get top video URLs for a keyword
    # ------------------------------------------------------------------

    async def _search_videos(self, ctx: BrowserContext, keyword: str,
                              max_videos: int = MAX_VIDEOS_PER_KEYWORD) -> List[Dict]:
        """
        Navigate to TikTok search, intercept the JSON API response,
        and return up to max_videos video entries (id + author).
        """
        videos: List[Dict] = []
        captured: List[Any] = []
        page = await ctx.new_page()

        async def on_response(r):
            url = r.url
            if any(kw in url for kw in ("search/general", "search/item", "search/full",
                                         "api/search", "aweme/v1/search", "item_list")):
                try:
                    captured.append(await r.json())
                except Exception:
                    pass

        page.on("response", on_response)
        try:
            encoded = keyword.replace(" ", "%20")
            await page.goto(
                f"https://www.tiktok.com/search?q={encoded}",
                wait_until="domcontentloaded",
                timeout=25000,
            )
            await asyncio.sleep(random.uniform(3, 5))

            # Click the "Videos" tab — this triggers the actual video search API call
            for selector in (
                "a[href*='type=video']",
                "span:text('Videos')",
                "[data-e2e='search-tab-video']",
                "div[role='tab']:has-text('Videos')",
            ):
                try:
                    el = page.locator(selector).first
                    if await el.is_visible(timeout=3000):
                        await el.click()
                        print(f"[TTPlay]   Clicked Videos tab")
                        break
                except Exception:
                    pass

            await asyncio.sleep(random.uniform(4, 6))

            # Scroll to trigger lazy-loading of remaining results
            await page.mouse.wheel(0, random.randint(400, 700))
            await asyncio.sleep(random.uniform(2, 3))

            # Parse API responses first
            for data in captured:
                videos.extend(self._parse_search_response(data))
                if len(videos) >= max_videos:
                    break

            # DOM fallback: extract video links directly from the search results page
            if len(videos) < max_videos:
                link_els = await page.query_selector_all("a[href*='/video/']")
                seen = {v.get("url") for v in videos}
                for el in link_els:
                    if len(videos) >= max_videos:
                        break
                    href = await el.get_attribute("href")
                    if not href or href in seen:
                        continue
                    seen.add(href)
                    # Extract username and video ID from URL pattern /@user/video/id
                    parts = href.split("/")
                    try:
                        vid_idx = parts.index("video")
                        username = parts[vid_idx - 1].lstrip("@")
                        video_id = parts[vid_idx + 1].split("?")[0]
                        url = f"https://www.tiktok.com/@{username}/video/{video_id}"
                        videos.append({"id": video_id, "username": username, "url": url})
                    except (ValueError, IndexError):
                        videos.append({"id": "", "username": "", "url": href})

        except Exception as e:
            print(f"[TTPlay] Search '{keyword}': {e}")
        finally:
            await page.close()

        return videos[:max_videos]

    def _parse_search_response(self, data: Any) -> List[Dict]:
        videos: List[Dict] = []
        if not isinstance(data, dict):
            return videos
        # Shape: {"data": [{"item": {...}}]} or {"item_list": [...]}
        items = data.get("item_list") or []
        if not items:
            for entry in data.get("data") or []:
                item = entry.get("item") if isinstance(entry, dict) else entry
                if isinstance(item, dict):
                    items.append(item)
        for item in items:
            try:
                author = item.get("author") or {}
                username = author.get("uniqueId") or author.get("unique_id") or ""
                video_id = item.get("id") or item.get("aweme_id") or ""
                if not username or not video_id:
                    continue
                videos.append({
                    "id": video_id,
                    "username": username,
                    "url": f"https://www.tiktok.com/@{username}/video/{video_id}",
                })
            except Exception:
                continue
        return videos

    # ------------------------------------------------------------------
    # Comments: scrape comments on a specific video
    # ------------------------------------------------------------------

    async def _scrape_video_comments(self, ctx: BrowserContext, video: Dict,
                                      keyword: str) -> List[Dict]:
        """
        Navigate to a TikTok video, intercept the comment API,
        and return comments with buyer-intent text.
        """
        leads: List[Dict] = []
        captured: List[Any] = []
        page = await ctx.new_page()

        async def on_response(r):
            if "comment/list" in r.url or "aweme/v1/comment" in r.url:
                try:
                    captured.append(await r.json())
                except Exception:
                    pass

        page.on("response", on_response)
        try:
            video_url = video.get("url") or ""
            if not video_url:
                return []

            await page.goto(video_url, wait_until="domcontentloaded", timeout=25000)
            await asyncio.sleep(random.uniform(3, 5))

            # Scroll toward comment section
            await page.mouse.wheel(0, random.randint(300, 600))
            await asyncio.sleep(random.uniform(2, 3))

            # Parse intercepted comment API responses
            for data in captured:
                leads.extend(self._parse_comment_response(data, video, keyword))
                if len(leads) >= MAX_COMMENTS_PER_VIDEO:
                    break

            # DOM fallback: grab comment text elements
            if not leads:
                for sel in (
                    "p[data-e2e='comment-level-1']",
                    "span[data-e2e='comment-text']",
                    "div[class*='CommentText']",
                    "p[class*='comment']",
                ):
                    comment_els = await page.query_selector_all(sel)
                    for el in comment_els[:MAX_COMMENTS_PER_VIDEO]:
                        try:
                            text = await el.inner_text()
                            if not text or not _has_buyer_intent(text):
                                continue
                            # Try to find the commenter's @-handle nearby
                            parent = await el.evaluate_handle(
                                "el => el.closest('[data-e2e=\"comment-item\"]') || el.parentElement"
                            )
                            username = ""
                            try:
                                handle_el = await parent.query_selector("a[href*='/@']")
                                if handle_el:
                                    href = await handle_el.get_attribute("href") or ""
                                    username = href.split("/@")[-1].split("?")[0]
                            except Exception:
                                pass
                            leads.append({
                                "name": username or f"tt_commenter_{video.get('username', '')}",
                                "property_url": video_url,
                                "source": "tiktok_comments",
                                "raw_data": {
                                    "text": text[:300],
                                    "videoUrl": video_url,
                                    "videoAuthor": video.get("username", ""),
                                    "_search_keyword": keyword,
                                },
                            })
                        except Exception:
                            continue
                    if leads:
                        break

        except Exception as e:
            print(f"[TTPlay] Comments on {video.get('url', '')}: {e}")
        finally:
            await page.close()

        return leads

    def _parse_comment_response(self, data: Any, video: Dict, keyword: str) -> List[Dict]:
        leads: List[Dict] = []
        if not isinstance(data, dict):
            return leads

        comments = data.get("comments") or []
        for comment in comments:
            try:
                text = (comment.get("text") or comment.get("share_info", {}).get("desc") or "")
                if not text or not _has_buyer_intent(text):
                    continue
                user = comment.get("user") or {}
                username = (user.get("unique_id") or user.get("uniqueId")
                            or user.get("nickname") or "")
                video_url = video.get("url", "")
                leads.append({
                    "name": username or f"tt_commenter_{video.get('username', '')}",
                    "property_url": video_url,
                    "source": "tiktok_comments",
                    "raw_data": {
                        "text": text[:300],
                        "videoUrl": video_url,
                        "videoAuthor": video.get("username", ""),
                        "_search_keyword": keyword,
                    },
                })
            except Exception:
                continue
        return leads

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def scrape_all(self, search_keywords: List[str] = None,
                          max_videos_per_keyword: int = MAX_VIDEOS_PER_KEYWORD) -> List[Dict]:
        search_keywords = search_keywords or SEARCH_KEYWORDS
        all_leads: List[Dict] = []

        async with async_playwright() as p:
            browser, ctx = await _build_context(p)
            try:
                for keyword in search_keywords:
                    print(f"[TTPlay] Searching: '{keyword}'")
                    try:
                        videos = await self._search_videos(ctx, keyword, max_videos_per_keyword)
                        print(f"[TTPlay]   Found {len(videos)} videos")

                        for video in videos:
                            try:
                                comments = await self._scrape_video_comments(ctx, video, keyword)
                                all_leads.extend(comments)
                                if comments:
                                    print(f"[TTPlay]   @{video.get('username')}: {len(comments)} buyer-intent comments")
                                await asyncio.sleep(random.uniform(3, 5))
                            except Exception as e:
                                print(f"[TTPlay]   Video {video.get('url', '')}: {e}")

                        await asyncio.sleep(random.uniform(4, 7))

                    except Exception as e:
                        print(f"[TTPlay] Keyword '{keyword}' failed: {e}")

            finally:
                await browser.close()

        print(f"[TTPlay] Total: {len(all_leads)} buyer-intent comments")
        return all_leads
