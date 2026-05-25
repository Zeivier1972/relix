"""
Facebook Ad Library scraper — competitor intelligence + buyer lead detection.

PRIMARY PATH  (requires FACEBOOK_ACCESS_TOKEN in .env)
  Uses the official, completely public Facebook Ad Library API.
  No login needed — only a free User Access Token with ads_read permission.
  Setup:
    1. developers.facebook.com → Create App → Get User Access Token
    2. Add to .env: FACEBOOK_ACCESS_TOKEN=your_token

FALLBACK PATH (no token)
  Launches a headless Chromium browser (Playwright) to load the Ad Library,
  waits for ads to render, then infinitely scrolls to load ALL available ads.
  Passes Facebook's JS bot-detection challenge automatically.
  No login, no credentials needed.

COMMENT LEADS
  Facebook ad comments require login — not accessible without a session.
  Buyer intent is detected from:
    1. Ad copy itself (advertiser targeting signals)
    2. Instagram handles found in ad copy → queued for DM
    3. Public Facebook page posts (best-effort, some pages visible without login)
"""

import asyncio
import json
import os
import random
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple

import httpx
from dotenv import load_dotenv

load_dotenv()

FACEBOOK_ACCESS_TOKEN = os.getenv("FACEBOOK_ACCESS_TOKEN", "")

_API_BASE = "https://graph.facebook.com/v20.0"
_AD_ARCHIVE = f"{_API_BASE}/ads_archive"
_PAGE_BASE  = "https://www.facebook.com"
_LIB_URL    = "https://www.facebook.com/ads/library/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.facebook.com/",
}

# ── Search config ─────────────────────────────────────────────────────────────

SEARCH_KEYWORDS = [
    # English — Miami metro
    "real estate miami",
    "homes for sale miami",
    "condos miami",
    "condos brickell miami",
    "homes homestead florida",
    "investment property miami",
    "pre-construction florida",
    "new construction miami",
    "miami beach condos for sale",
    "aventura condos",
    "doral homes for sale",
    "coral gables real estate",
    "south florida real estate",
    "buy home miami florida",
    "luxury condos miami",
    # Spanish — targeting Latino/Colombian buyers
    "casas en miami",
    "casas en venta miami",
    "preconstruccion miami",
    "casas florida",
    "invertir en miami",
    "nueva construccion miami",
    "bienes raices miami",
    "apartamentos miami venta",
    "casas en homestead florida",
    "propiedades miami florida",
]

LOCATIONS = [
    "Miami, Florida",
    "Miami Beach, Florida",
    "Brickell, Florida",
    "Coral Gables, Florida",
    "Doral, Florida",
    "Hialeah, Florida",
    "Homestead, Florida",
    "Kendall, Florida",
    "Aventura, Florida",
    "Hollywood, Florida",
    "Pembroke Pines, Florida",
    "Fort Lauderdale, Florida",
    "Broward County, Florida",
    "West Palm Beach, Florida",
    "Boca Raton, Florida",
]

# Buyer intent phrases — English and Spanish
_BUYER_INTENT_EN = [
    "interested", "how much", "i want to buy", "contact me",
    "call me", "dm me", "price?", "available?", "where is this?",
    "send info", "more info", "how do i", "what's the price",
    "is it available", "i'm looking", "i am looking",
    "how can i get", "what is the process", "ready to buy",
    "looking for a home", "first time buyer", "i need an agent",
]
_BUYER_INTENT_ES = [
    "me interesa", "cuánto cuesta", "cuanto cuesta", "quiero comprar",
    "información", "informacion", "contacto", "llámame", "llamame",
    "precio", "disponible", "cómo aplico", "como aplico",
    "me pueden contactar", "quiero información", "quiero informacion",
    "donde queda", "qué precio", "que precio", "busco casa",
    "necesito agente", "primera vez comprando", "cómo funciona",
]
_ALL_INTENT = _BUYER_INTENT_EN + _BUYER_INTENT_ES

_IG_HANDLE_RE = re.compile(r"(?:instagram\.com/|@)([A-Za-z0-9_.]{3,30})")


def _has_buyer_intent(text: str) -> Tuple[bool, str]:
    t = text.lower()
    for kw in _BUYER_INTENT_ES:
        if kw in t:
            return True, "spanish"
    for kw in _BUYER_INTENT_EN:
        if kw in t:
            return True, "english"
    return False, "unknown"


def _days_running(start_date_str: str) -> int:
    if not start_date_str:
        return 0
    try:
        start = datetime.fromisoformat(start_date_str.replace("Z", "+00:00"))
        return max(0, (datetime.now(start.tzinfo) - start).days)
    except Exception:
        return 0


def _parse_spend(spend: dict) -> Tuple[int, int]:
    if not spend:
        return 0, 0
    try:
        lo = int(spend.get("lower_bound", 0) or 0)
        hi = int(spend.get("upper_bound", lo) or lo)
        return lo, hi
    except Exception:
        return 0, 0


def _parse_impressions(imp: dict) -> Tuple[int, int]:
    if not imp:
        return 0, 0
    try:
        lo = int(imp.get("lower_bound", 0) or 0)
        hi = int(imp.get("upper_bound", lo) or lo)
        return lo, hi
    except Exception:
        return 0, 0


# ── Main scraper ──────────────────────────────────────────────────────────────

class FacebookAdsLibraryScraper:
    """
    Competitor intelligence via Facebook Ad Library.
    Primary: official API  |  Fallback: Playwright infinite-scroll DOM parsing.
    """

    def __init__(self):
        self.client = httpx.AsyncClient(
            headers=HEADERS, timeout=45.0, follow_redirects=True
        )
        self._use_api = bool(FACEBOOK_ACCESS_TOKEN)

    # ── API path ──────────────────────────────────────────────────────────────

    async def _api_search(self, keyword: str,
                          after: Optional[str] = None) -> Tuple[List[dict], Optional[str]]:
        fields = ",".join([
            "id", "ad_snapshot_url", "ad_creative_bodies",
            "ad_creative_link_captions", "ad_creative_link_descriptions",
            "ad_creative_link_titles", "ad_delivery_start_time",
            "ad_delivery_stop_time", "currency", "page_id", "page_name",
            "spend", "impressions", "target_locations",
            "demographic_distribution",
        ])
        params = {
            "access_token":         FACEBOOK_ACCESS_TOKEN,
            "ad_type":              "ALL",
            "ad_reached_countries": '["US"]',
            "search_terms":         keyword,
            "ad_active_status":     "ACTIVE",
            "fields":               fields,
            "limit":                50,
        }
        if after:
            params["after"] = after
        try:
            resp = await self.client.get(_AD_ARCHIVE, params=params)
            if resp.status_code == 401:
                print("[FBAds-API] Token expired — switching to web fallback")
                self._use_api = False
                return [], None
            if resp.status_code == 200:
                data = resp.json()
                ads  = data.get("data", [])
                next_cursor = data.get("paging", {}).get("cursors", {}).get("after")
                return ads, next_cursor
            print(f"[FBAds-API] HTTP {resp.status_code} for '{keyword}'")
        except Exception as e:
            print(f"[FBAds-API] Error for '{keyword}': {e}")
        return [], None

    def _parse_api_ad(self, ad: dict, keyword: str) -> Optional[Dict[str, Any]]:
        bodies       = ad.get("ad_creative_bodies") or []
        titles       = ad.get("ad_creative_link_titles") or []
        captions     = ad.get("ad_creative_link_captions") or []
        descriptions = ad.get("ad_creative_link_descriptions") or []

        body     = " | ".join(bodies)[:1000]
        headline = " | ".join(titles)[:300]
        caption  = " | ".join(captions)[:300]
        desc     = " | ".join(descriptions)[:500]

        start_date  = ad.get("ad_delivery_start_time", "")
        days        = _days_running(start_date)
        spend_lo, spend_hi = _parse_spend(ad.get("spend"))
        imp_lo, imp_hi     = _parse_impressions(ad.get("impressions"))

        page_name = ad.get("page_name", "")
        page_id   = ad.get("page_id", "")
        snapshot  = ad.get("ad_snapshot_url", "")
        page_url  = f"https://www.facebook.com/{page_id}" if page_id else ""

        return {
            "ad_id":             ad.get("id", ""),
            "page_name":         page_name,
            "page_id":           page_id,
            "ad_body":           body,
            "ad_headline":       headline,
            "ad_caption":        caption,
            "ad_description":    desc,
            "ad_snapshot_url":   snapshot,
            "page_url":          page_url,
            "start_date":        start_date,
            "days_running":      days,
            "spend_lower":       spend_lo,
            "spend_upper":       spend_hi,
            "impressions_lower": imp_lo,
            "impressions_upper": imp_hi,
            "keyword_matched":   keyword,
            "source":            "facebook_ads_library",
        }

    # ── Playwright fallback path ──────────────────────────────────────────────

    async def _playwright_search(self, keyword: str,
                                 max_ads: int = 200) -> List[Dict[str, Any]]:
        """
        Headless Chromium renders the Ad Library, then infinitely scrolls
        to load ALL ads (up to max_ads). Parses rendered DOM text directly.

        DOM structure (confirmed from live run):
          Library ID: [numeric_id]
          Started running on [Month D, YYYY]
          Active | Platforms | N ads use this creative
          [Advertiser Name]
          Sponsored
          [Ad copy text]
        """
        from playwright.async_api import async_playwright

        url = (
            f"{_LIB_URL}?active_status=active&ad_type=all&country=US"
            f"&q={keyword.replace(' ', '+')}&search_type=keyword_unordered&media_type=all"
        )
        page_text = ""

        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage",
                          "--disable-blink-features=AutomationControlled"],
                )
                ctx = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    locale="en-US",
                    viewport={"width": 1280, "height": 900},
                )
                page = await ctx.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=40000)

                # Wait for first ads to render
                for _ in range(15):
                    await page.wait_for_timeout(1000)
                    txt = await page.inner_text("body")
                    if "Library ID:" in txt:
                        page_text = txt
                        break
                else:
                    page_text = await page.inner_text("body")

                # Infinite scroll — keep scrolling until no new ads appear or limit reached
                prev_count = page_text.count("Library ID:")
                for _scroll in range(12):
                    if prev_count >= max_ads:
                        print(f"[FBAds] '{keyword}': reached {prev_count} ads — stopping scroll")
                        break
                    # Scroll to bottom to trigger lazy loading
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(2500)
                    txt = await page.inner_text("body")
                    new_count = txt.count("Library ID:")
                    if new_count == prev_count:
                        break  # No more ads loading
                    prev_count = new_count
                    page_text = txt
                    print(f"[FBAds] '{keyword}': scrolled → {new_count} ads loaded")

                await browser.close()

        except Exception as e:
            print(f"[FBAds-Playwright] Error for '{keyword}': {e}")
            return []

        return self._parse_dom_text(page_text, keyword)

    def _parse_dom_text(self, text: str, keyword: str) -> List[Dict[str, Any]]:
        ads = []
        if not text or "Library ID:" not in text:
            return ads
        blocks = re.split(r"Library ID:\s*", text)
        for block in blocks[1:]:
            try:
                ad = self._parse_ad_block(block, keyword)
                if ad:
                    ads.append(ad)
            except Exception:
                continue
        return ads

    _MONTHS = {
        "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
        "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
    }

    def _parse_ad_block(self, block: str, keyword: str) -> Optional[Dict[str, Any]]:
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        if not lines:
            return None

        ad_id = lines[0].strip()
        if not re.match(r"^\d{10,}", ad_id):
            return None

        # Start date
        start_date_raw = ""
        days_running   = 0
        date_re = re.compile(
            r"Started running on\s+([A-Za-z]+)\s+(\d{1,2}),?\s*(\d{4})"
        )
        for line in lines[:20]:
            m = date_re.search(line)
            if m:
                month_str, day, year = m.group(1)[:3], int(m.group(2)), int(m.group(3))
                month = self._MONTHS.get(month_str.capitalize(), 1)
                try:
                    start_dt     = datetime(year, month, day)
                    days_running = max(0, (datetime.now() - start_dt).days)
                    start_date_raw = start_dt.strftime("%Y-%m-%d")
                except ValueError:
                    pass
                break

        # Advertiser: line immediately before "Sponsored"
        page_name = ""
        for i, line in enumerate(lines):
            if line == "Sponsored" and i > 0:
                page_name = lines[i - 1]
                break
        if not page_name:
            noise = {"Active", "Inactive", "Platforms", "Open Dropdown",
                     "See summary details", "Remove", "Filters", "Sort"}
            for line in lines[1:15]:
                if (line and line not in noise and
                        not line.startswith("Library ID") and
                        not re.match(r"^\d+ ad", line) and
                        "Started running" not in line and
                        len(line) > 2):
                    page_name = line
                    break

        # Ad copy: lines after "Sponsored"
        ad_body = ""
        sponsored_idx = None
        for i, line in enumerate(lines):
            if line == "Sponsored":
                sponsored_idx = i
                break
        if sponsored_idx is not None:
            stop_words = {"See more", "Like", "Comment", "Share", "Active",
                          "Library ID", "Started running", "Platforms", "Filters"}
            body_parts = []
            for line in lines[sponsored_idx + 1:sponsored_idx + 40]:
                if any(sw in line for sw in stop_words) or re.match(r"Library ID:", line):
                    break
                body_parts.append(line)
            ad_body = " ".join(body_parts)[:800]

        # Detect IG handle in body
        ig_handles = _IG_HANDLE_RE.findall(ad_body)
        ig_handle  = ig_handles[0] if ig_handles else None

        snapshot_url = f"https://www.facebook.com/ads/library/?id={ad_id}"

        return {
            "ad_id":             ad_id,
            "page_name":         page_name,
            "page_id":           "",
            "ad_body":           ad_body,
            "ad_headline":       "",
            "ad_caption":        "",
            "ad_description":    "",
            "ad_snapshot_url":   snapshot_url,
            "page_url":          "",
            "start_date":        start_date_raw,
            "days_running":      days_running,
            "spend_lower":       0,
            "spend_upper":       0,
            "impressions_lower": 0,
            "impressions_upper": 0,
            "keyword_matched":   keyword,
            "ig_handle":         ig_handle,
            "source":            "facebook_ads_playwright",
        }

    # ── Comment / buyer lead extraction ──────────────────────────────────────

    async def _scrape_page_public_posts(self, page_url: str,
                                        page_name: str) -> List[Dict[str, Any]]:
        """
        Navigate to a public Facebook page and look for buyer-intent text
        in visible posts/comments (no login required for public pages).
        """
        leads = []
        if not page_url:
            return leads
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                ctx = await browser.new_context(
                    user_agent=HEADERS["User-Agent"],
                    locale="en-US",
                    viewport={"width": 1280, "height": 900},
                )
                page = await ctx.new_page()
                await page.goto(page_url, wait_until="domcontentloaded", timeout=25000)
                await page.wait_for_timeout(2000)

                # Accept cookie consent if present
                for sel in ['button[title="Allow all cookies"]',
                            'button:text("Accept All")', 'button:text("OK")']:
                    try:
                        await page.click(sel, timeout=1500)
                        await page.wait_for_timeout(1000)
                        break
                    except Exception:
                        pass

                # Scroll a little to reveal posts
                await page.evaluate("window.scrollTo(0, 600)")
                await page.wait_for_timeout(1500)

                txt = await page.inner_text("body")
                await browser.close()

            paras = [p.strip() for p in txt.split("\n") if len(p.strip()) > 15]
            for para in paras:
                has_intent, lang = _has_buyer_intent(para)
                if not has_intent:
                    continue
                ig_handles = _IG_HANDLE_RE.findall(para)
                leads.append({
                    "ad_id":            "",
                    "profile_name":     f"Visitor on {page_name}",
                    "profile_url":      page_url,
                    "comment_text":     para[:500],
                    "language":         lang,
                    "instagram_handle": ig_handles[0] if ig_handles else None,
                    "score":            "WARM",
                    "source":           "facebook_page_post",
                })
                if len(leads) >= 8:
                    break
        except Exception as e:
            print(f"[FBAds-PageScrape] {page_url[:60]} error: {e}")
        return leads

    async def _scrape_snapshot_for_leads(self, ad: dict) -> List[Dict[str, Any]]:
        leads = []
        snapshot_url = ad.get("ad_snapshot_url", "")
        ad_body = (ad.get("ad_body", "") + " " + ad.get("ad_description", "")).lower()

        ig_in_copy = _IG_HANDLE_RE.findall(ad_body)
        body_intent, lang = _has_buyer_intent(ad_body)

        if not snapshot_url:
            return leads
        try:
            resp = await self.client.get(snapshot_url, timeout=20.0)
            if resp.status_code != 200:
                return leads

            html  = resp.text
            text  = re.sub(r"<[^>]+>", " ", html)
            paras = [p.strip() for p in text.split("\n") if len(p.strip()) > 10]

            for para in paras:
                has_intent, language = _has_buyer_intent(para)
                if not has_intent:
                    continue
                ig_handles = _IG_HANDLE_RE.findall(para)
                leads.append({
                    "ad_id":            ad.get("ad_id", ""),
                    "profile_name":     "Unknown (from ad snapshot)",
                    "profile_url":      snapshot_url,
                    "comment_text":     para[:500],
                    "language":         language,
                    "instagram_handle": ig_handles[0] if ig_handles else None,
                    "score":            "HOT",
                    "source":           "facebook_ads_comment",
                })
                if len(leads) >= 10:
                    break
        except Exception as e:
            print(f"[FBAds-Snapshot] {snapshot_url[:60]} error: {e}")
        return leads

    # ── Aggregate ────────────────────────────────────────────────────────────

    async def scrape_all(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Returns:
          {
            "ads":   [ ... ]   all competitor ad records
            "leads": [ ... ]   buyer-intent lead records
          }
        """
        all_ads:   List[Dict[str, Any]] = []
        all_leads: List[Dict[str, Any]] = []
        seen_ad_ids: set = set()

        for keyword in SEARCH_KEYWORDS:
            kw_ads: List[Dict[str, Any]] = []

            if self._use_api:
                raw_ads, _cursor = await self._api_search(keyword)
                for raw in raw_ads:
                    parsed = self._parse_api_ad(raw, keyword)
                    if parsed:
                        kw_ads.append(parsed)
            else:
                kw_ads = await self._playwright_search(keyword, max_ads=200)

            new_ads = []
            for ad in kw_ads:
                if ad.get("ad_id") and ad["ad_id"] not in seen_ad_ids:
                    seen_ad_ids.add(ad["ad_id"])
                    new_ads.append(ad)
                elif not ad.get("ad_id"):
                    new_ads.append(ad)
            all_ads.extend(new_ads)

            print(f"[FBAds] '{keyword}': {len(new_ads)} new ads (total {len(all_ads)})")
            await asyncio.sleep(random.uniform(3, 5))

        # Snapshot lead scan — check top 30 longest-running ads
        hot_ads = sorted(all_ads, key=lambda a: a.get("days_running", 0), reverse=True)[:30]
        for ad in hot_ads:
            try:
                leads = await self._scrape_snapshot_for_leads(ad)
                all_leads.extend(leads)
            except Exception:
                pass
            await asyncio.sleep(random.uniform(1.5, 3))

        # Public page post scan — top 10 advertisers by days running
        seen_pages: set = set()
        top_by_days = sorted(all_ads, key=lambda a: a.get("days_running", 0), reverse=True)
        for ad in top_by_days:
            page_url = ad.get("page_url", "")
            page_name = ad.get("page_name", "")
            if not page_url or page_url in seen_pages:
                continue
            seen_pages.add(page_url)
            if len(seen_pages) > 10:
                break
            try:
                page_leads = await self._scrape_page_public_posts(page_url, page_name)
                all_leads.extend(page_leads)
            except Exception:
                pass
            await asyncio.sleep(random.uniform(2, 4))

        print(f"[FBAds] Total: {len(all_ads)} ads | {len(all_leads)} buyer leads")
        return {"ads": all_ads, "leads": all_leads}

    async def close(self):
        await self.client.aclose()


# ── Competitor report helpers ─────────────────────────────────────────────────

def top_advertisers(ads: List[Dict[str, Any]],
                    n: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Aggregate ads by page, return sorted by longest-running ad (proxy for ROI).
    n=None returns all advertisers.
    """
    pages: Dict[str, Dict] = {}
    for ad in ads:
        name = ad.get("page_name") or "Unknown"
        pid  = ad.get("page_id", "")
        key  = pid or name
        if key not in pages:
            pages[key] = {
                "page_name":       name,
                "page_url":        ad.get("page_url", ""),
                "ad_count":        0,
                "max_days":        0,
                "total_spend_lo":  0,
                "top_ad_body":     "",
                "top_ad_headline": "",
                "keywords":        set(),
            }
        pages[key]["ad_count"] += 1
        pages[key]["keywords"].add(ad.get("keyword_matched", ""))
        days = ad.get("days_running", 0)
        if days > pages[key]["max_days"]:
            pages[key]["max_days"]        = days
            pages[key]["top_ad_body"]     = ad.get("ad_body", "")[:400]
            pages[key]["top_ad_headline"] = ad.get("ad_headline", "")[:150]
        pages[key]["total_spend_lo"] += ad.get("spend_lower", 0)

    ranked = sorted(pages.values(), key=lambda p: p["max_days"], reverse=True)
    # Convert set to list for JSON serialization
    for p in ranked:
        p["keywords"] = sorted(p["keywords"])
    return ranked[:n] if n else ranked
