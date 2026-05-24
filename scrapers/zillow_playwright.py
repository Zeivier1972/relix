"""
Zillow scraper — finds FSBO, price drops, high-DOM, and expired listings.
Uses Playwright with stealth headers. Zillow has strong bot-detection;
results are best-effort. Runs per ZIP code to minimize footprint.
"""
import asyncio
import json
import random
import re
from datetime import datetime
from typing import List, Dict, Any, Optional

import httpx

from scrapers.target_zips import ALL_ZIPS, score_property_lead

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.zillow.com/",
}

# Zillow internal search API
_SEARCH_URL = "https://www.zillow.com/async-create-search-page-state"

PRICE_DROP_DAYS = 7
MIN_DOM_HOT = 60
MIN_PRICE_DROP_PCT = 5.0


def _build_search_body(zip_code: str, filter_state: dict, page: int = 1) -> dict:
    return {
        "searchQueryState": {
            "pagination": {"currentPage": page},
            "isMapVisible": False,
            "mapBounds": {},
            "regionSelection": [{"regionId": zip_code, "regionType": 7}],
            "filterState": filter_state,
            "isListVisible": True,
        },
        "wants": {"cat1": ["listResults"], "cat2": ["total"]},
        "requestId": random.randint(1, 9999),
        "isDebugRequest": False,
    }


def _parse_listing(item: dict, lead_type: str, source_zip: str) -> Optional[Dict[str, Any]]:
    try:
        address = item.get("address", "")
        price = item.get("price", 0)
        dom = item.get("daysOnZillow") or item.get("daysOnMarket") or 0
        url = "https://www.zillow.com" + (item.get("detailUrl") or "")

        # Price history for reduction calculation
        price_reduction_pct = 0.0
        price_reduction_amt = 0.0
        prev_price = item.get("priceChange") or item.get("reducedPrice")
        if prev_price and price:
            price_reduction_amt = abs(prev_price - price) if isinstance(prev_price, (int, float)) else 0
            if price > 0 and price_reduction_amt:
                price_reduction_pct = round(price_reduction_amt / price * 100, 1)

        score = score_property_lead(lead_type, dom, price_reduction_pct)

        return {
            "address": address,
            "city": item.get("addressCity", ""),
            "zip_code": item.get("addressZipcode", source_zip),
            "owner_name": None,
            "listing_price": price,
            "days_on_market": dom,
            "price_reduction_pct": price_reduction_pct,
            "price_reduction_amt": price_reduction_amt,
            "phone": None,
            "email": None,
            "listing_url": url,
            "lead_type": lead_type,
            "score": score,
            "source": "zillow",
            "raw_data": {
                "zpid": item.get("zpid"),
                "bedrooms": item.get("beds"),
                "bathrooms": item.get("baths"),
                "living_area": item.get("area"),
                "home_type": item.get("homeType"),
                "image_url": item.get("imgSrc"),
            },
        }
    except Exception:
        return None


class ZillowScraper:
    """Scrape Zillow for motivated-seller and FSBO listings."""

    def __init__(self):
        self.client = httpx.AsyncClient(
            headers=HEADERS, timeout=30.0, follow_redirects=True
        )

    async def _search(self, zip_code: str, filter_state: dict) -> List[dict]:
        """Hit Zillow search API and return raw listing items."""
        try:
            body = _build_search_body(zip_code, filter_state)
            resp = await self.client.post(
                _SEARCH_URL,
                json=body,
                headers={**HEADERS, "Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
            results = (
                data.get("cat1", {})
                    .get("searchResults", {})
                    .get("listResults", [])
            )
            return results
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (403, 429):
                print(f"[Zillow] Blocked on {zip_code} (status {e.response.status_code}) — skipping")
            else:
                print(f"[Zillow] HTTP {e.response.status_code} on {zip_code}")
            return []
        except Exception as e:
            print(f"[Zillow] Error on {zip_code}: {e}")
            return []

    async def scrape_zip(self, zip_code: str) -> List[Dict[str, Any]]:
        leads = []

        # FSBO listings
        fsbo_items = await self._search(zip_code, {
            "isForSaleByOwner": {"value": True},
            "sortSelection": {"value": "days"},
        })
        for item in fsbo_items:
            lead = _parse_listing(item, "FSBO", zip_code)
            if lead:
                leads.append(lead)

        await asyncio.sleep(random.uniform(2, 4))

        # Price drops (sorted by price change)
        price_drop_items = await self._search(zip_code, {
            "isForSale": {"value": True},
            "sortSelection": {"value": "pricechange"},
            "doz": {"value": "7"},
        })
        for item in price_drop_items:
            price = item.get("price", 0)
            prev = item.get("reducedPrice") or item.get("priceChange")
            if prev and price and price > 0:
                pct = abs(prev - price) / price * 100
                if pct >= MIN_PRICE_DROP_PCT:
                    lead = _parse_listing(item, "PRICE_DROP", zip_code)
                    if lead:
                        leads.append(lead)

        await asyncio.sleep(random.uniform(2, 4))

        # Long days on market (motivated sellers)
        dom_items = await self._search(zip_code, {
            "isForSale": {"value": True},
            "doz": {"value": "90"},
            "sortSelection": {"value": "days"},
        })
        for item in dom_items:
            dom = item.get("daysOnZillow") or item.get("daysOnMarket") or 0
            if dom >= MIN_DOM_HOT:
                lead = _parse_listing(item, "FSBO" if item.get("isFeatured") is False else "PRICE_DROP", zip_code)
                if lead:
                    lead["lead_type"] = "FSBO"
                    lead["score"] = "HOT"
                    leads.append(lead)

        return leads

    async def scrape_all(self, zip_codes: List[str] = None) -> List[Dict[str, Any]]:
        zips = zip_codes or ALL_ZIPS
        all_leads = []
        seen_urls = set()

        for i, zip_code in enumerate(zips):
            try:
                leads = await self.scrape_zip(zip_code)
                for lead in leads:
                    url = lead.get("listing_url", "")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        all_leads.append(lead)
                print(f"[Zillow] {zip_code}: {len(leads)} leads")
            except Exception as e:
                print(f"[Zillow] {zip_code} failed: {e}")

            # Throttle between zips — Zillow rate-limits aggressively
            if i < len(zips) - 1:
                await asyncio.sleep(random.uniform(4, 8))

        return all_leads

    async def close(self):
        await self.client.aclose()
