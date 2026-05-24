"""
Facebook Ad Library scraper — competitor intelligence + buyer lead detection.

PRIMARY PATH  (requires FACEBOOK_ACCESS_TOKEN in .env)
  Uses the official, completely public Facebook Ad Library API.
  No login needed — only a free User Access Token with ads_read permission.
  Setup:
    1. developers.facebook.com → Create App → Get User Access Token
    2. Add to .env: FACEBOOK_ACCESS_TOKEN=your_token

FALLBACK PATH (no token)
  Hits the public Ad Library search page via httpx and parses embedded JSON.
  Returns fewer fields but requires zero credentials.

BUYER LEAD DETECTION
  Ad comments are NOT exposed by the Ad Library API and require Facebook login
  to scrape at scale.  We detect buyer intent from:
    - Ad engagement text visible in the snapshot pages (when public)
    - Cross-referenced names/handles from search (best-effort, no login needed)
  When an Instagram handle is found it is queued for auto-DM.
  When only a Facebook name is found it is saved as a WARM lead.
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
    "real estate miami",
    "casas en miami",
    "preconstruccion miami",
    "homes for sale florida",
    "condos miami",
    "investment property florida",
    "colombianos miami",
    "casas florida",
    "invertir en miami",
    "pre-construction florida",
    "nueva construccion miami",
]

LOCATIONS = [
    "Miami, Florida",
    "Homestead, Florida",
    "Broward County, Florida",
    "West Palm Beach, Florida",
    "Orlando, Florida",
]

# Keywords that signal buyer intent in comment text
_BUYER_INTENT_EN = [
    "interested", "how much", "i want to buy", "contact me",
    "call me", "dm me", "price?", "available?", "where is this?",
    "send info", "more info", "how do i", "what's the price",
    "is it available", "i'm looking", "i am looking",
]
_BUYER_INTENT_ES = [
    "me interesa", "cuánto cuesta", "cuanto cuesta", "quiero comprar",
    "información", "informacion", "contacto", "llámame", "llamame",
    "precio", "disponible", "cómo aplico", "como aplico",
    "me pueden contactar", "quiero información", "quiero informacion",
    "donde queda", "qué precio", "que precio",
]
_ALL_INTENT = _BUYER_INTENT_EN + _BUYER_INTENT_ES

_IG_HANDLE_RE = re.compile(r"(?:instagram\.com/|@)([A-Za-z0-9_.]{3,30})")


def _has_buyer_intent(text: str) -> Tuple[bool, str]:
    """Return (has_intent, language)."""
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
    """Return (lower_bound, upper_bound) in USD."""
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
    Primary: official API  |  Fallback: public HTML parsing.
    """

    def __init__(self):
        self.client = httpx.AsyncClient(
            headers=HEADERS, timeout=45.0, follow_redirects=True
        )
        self._use_api = bool(FACEBOOK_ACCESS_TOKEN)

    # ── API path ──────────────────────────────────────────────────────────────

    async def _api_search(self, keyword: str,
                          after: Optional[str] = None) -> Tuple[List[dict], Optional[str]]:
        """
        Call the Ad Library API for one keyword.
        Returns (ads_list, next_cursor).
        """
        fields = ",".join([
            "id", "ad_snapshot_url", "ad_creative_bodies",
            "ad_creative_link_captions", "ad_creative_link_descriptions",
            "ad_creative_link_titles", "ad_delivery_start_time",
            "ad_delivery_stop_time", "currency", "page_id", "page_name",
            "spend", "impressions", "target_locations",
            "demographic_distribution",
        ])
        params = {
            "access_token":        FACEBOOK_ACCESS_TOKEN,
            "ad_type":             "ALL",
            "ad_reached_countries": '["US"]',
            "search_terms":        keyword,
            "ad_active_status":    "ACTIVE",
            "fields":              fields,
            "limit":               50,
        }
        if after:
            params["after"] = after

        try:
            resp = await self.client.get(_AD_ARCHIVE, params=params)
            if resp.status_code == 401:
                print("[FBAds-API] Token expired or invalid — switch to web fallback")
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
        bodies      = ad.get("ad_creative_bodies") or []
        titles      = ad.get("ad_creative_link_titles") or []
        captions    = ad.get("ad_creative_link_captions") or []
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
            "ad_id":           ad.get("id", ""),
            "page_name":       page_name,
            "page_id":         page_id,
            "ad_body":         body,
            "ad_headline":     headline,
            "ad_caption":      caption,
            "ad_description":  desc,
            "ad_snapshot_url": snapshot,
            "page_url":        page_url,
            "start_date":      start_date,
            "days_running":    days,
            "spend_lower":     spend_lo,
            "spend_upper":     spend_hi,
            "impressions_lower": imp_lo,
            "impressions_upper": imp_hi,
            "keyword_matched": keyword,
            "source":          "facebook_ads_library",
        }

    # ── Web fallback path ─────────────────────────────────────────────────────

    async def _web_search(self, keyword: str) -> List[Dict[str, Any]]:
        """
        Fallback: load the public Ad Library search page and parse any
        embedded JSON from script tags. Returns partial ad data.
        """
        ads = []
        try:
            params = {
                "active_status": "active",
                "ad_type":       "all",
                "country":       "US",
                "q":             keyword,
                "search_type":   "keyword_unordered",
                "media_type":    "all",
            }
            resp = await self.client.get(_LIB_URL, params=params)
            if resp.status_code != 200:
                return ads

            html = resp.text

            # Facebook embeds initial data in several patterns
            for pattern in [
                r'"ads"\s*:\s*(\[.*?\])\s*,\s*"',
                r'window\.__initialData\s*=\s*(\{.*?\})\s*;',
                r'"adArchiveID"\s*:\s*"(\d+)"',
            ]:
                matches = re.findall(pattern, html, re.DOTALL)
                for m in matches:
                    try:
                        parsed = json.loads(m if m.startswith('{') or m.startswith('[') else f'"{m}"')
                        if isinstance(parsed, list):
                            for item in parsed[:20]:
                                if isinstance(item, dict) and item.get("adArchiveID"):
                                    ads.append({
                                        "ad_id":       str(item.get("adArchiveID", "")),
                                        "page_name":   item.get("pageName", ""),
                                        "ad_body":     str(item.get("snapshot", {}).get("body", {}).get("markup", ""))[:500],
                                        "ad_headline": str(item.get("snapshot", {}).get("title", ""))[:200],
                                        "start_date":  str(item.get("startDate", "")),
                                        "days_running": _days_running(str(item.get("startDate", ""))),
                                        "spend_lower": 0,
                                        "spend_upper": 0,
                                        "keyword_matched": keyword,
                                        "source": "facebook_ads_web",
                                    })
                    except Exception:
                        continue

        except Exception as e:
            print(f"[FBAds-Web] Error for '{keyword}': {e}")

        return ads

    # ── Comment / buyer lead extraction ──────────────────────────────────────

    async def _scrape_snapshot_for_leads(self, ad: dict) -> List[Dict[str, Any]]:
        """
        Load the ad snapshot page and look for:
        - Visible comments with buyer intent keywords
        - Any Instagram handle mentions in the ad copy itself
        Returns a list of raw lead dicts.
        """
        leads = []
        snapshot_url = ad.get("ad_snapshot_url", "")
        ad_body = (ad.get("ad_body", "") + " " + ad.get("ad_description", "")).lower()

        # Check if the ad copy itself mentions an Instagram handle (competitor's IG)
        ig_in_copy = _IG_HANDLE_RE.findall(ad_body)

        # Scan ad copy for buyer-intent language (e.g. "DM us" or "call us")
        # These indicate the advertiser is reaching buyers — useful intel
        body_intent, lang = _has_buyer_intent(ad_body)

        if not snapshot_url:
            return leads

        try:
            resp = await self.client.get(snapshot_url, timeout=20.0)
            if resp.status_code != 200:
                return leads

            html  = resp.text
            text  = re.sub(r"<[^>]+>", " ", html)   # strip tags
            paras = [p.strip() for p in text.split("\n") if len(p.strip()) > 10]

            for para in paras:
                has_intent, language = _has_buyer_intent(para)
                if not has_intent:
                    continue
                # Try to find a name/handle near this text
                ig_handles = _IG_HANDLE_RE.findall(para)
                leads.append({
                    "ad_id":          ad.get("ad_id", ""),
                    "profile_name":   "Unknown (from ad snapshot)",
                    "profile_url":    snapshot_url,
                    "comment_text":   para[:500],
                    "language":       language,
                    "instagram_handle": ig_handles[0] if ig_handles else None,
                    "score":          "HOT",
                    "source":         "facebook_ads_comment",
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
            "ads":   [ ... ]   competitor ad records
            "leads": [ ... ]   buyer-intent lead records
          }
        """
        all_ads: List[Dict[str, Any]] = []
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
                kw_ads = await self._web_search(keyword)

            # Deduplicate by ad_id
            new_ads = []
            for ad in kw_ads:
                if ad.get("ad_id") and ad["ad_id"] not in seen_ad_ids:
                    seen_ad_ids.add(ad["ad_id"])
                    new_ads.append(ad)
                elif not ad.get("ad_id"):
                    new_ads.append(ad)
            all_ads.extend(new_ads)

            print(f"[FBAds] '{keyword}': {len(new_ads)} ads")
            await asyncio.sleep(random.uniform(2, 4))

        # Check snapshot pages for buyer leads (sample — avoid rate limiting)
        hot_ads = sorted(all_ads, key=lambda a: a.get("days_running", 0), reverse=True)[:20]
        for ad in hot_ads:
            try:
                leads = await self._scrape_snapshot_for_leads(ad)
                all_leads.extend(leads)
            except Exception:
                pass
            await asyncio.sleep(random.uniform(1.5, 3))

        print(f"[FBAds] Total: {len(all_ads)} competitor ads | {len(all_leads)} buyer leads")
        return {"ads": all_ads, "leads": all_leads}

    async def close(self):
        await self.client.aclose()


# ── Competitor report helpers ─────────────────────────────────────────────────

def top_advertisers(ads: List[Dict[str, Any]], n: int = 10) -> List[Dict[str, Any]]:
    """
    Aggregate ads by page, return top N by longest-running ad (proxy for ROI).
    """
    pages: Dict[str, Dict] = {}
    for ad in ads:
        name = ad.get("page_name") or "Unknown"
        pid  = ad.get("page_id", "")
        key  = pid or name
        if key not in pages:
            pages[key] = {
                "page_name":    name,
                "page_url":     ad.get("page_url", ""),
                "ad_count":     0,
                "max_days":     0,
                "total_spend_lo": 0,
                "top_ad_body":  "",
                "top_ad_headline": "",
            }
        pages[key]["ad_count"] += 1
        days = ad.get("days_running", 0)
        if days > pages[key]["max_days"]:
            pages[key]["max_days"]        = days
            pages[key]["top_ad_body"]     = ad.get("ad_body", "")[:300]
            pages[key]["top_ad_headline"] = ad.get("ad_headline", "")[:150]
        pages[key]["total_spend_lo"] += ad.get("spend_lower", 0)

    ranked = sorted(pages.values(), key=lambda p: p["max_days"], reverse=True)
    return ranked[:n]
