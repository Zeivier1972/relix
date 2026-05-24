"""
Realtor.com scraper — FSBO, price drops, high-DOM, back-on-market listings.
Uses Realtor.com's internal GraphQL/JSON API via httpx.
"""
import asyncio
import json
import random
from typing import List, Dict, Any, Optional

import httpx

from scrapers.target_zips import ALL_ZIPS, score_property_lead

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.realtor.com/",
    "Origin": "https://www.realtor.com",
}

_API_URL = "https://www.realtor.com/api/v2/search"


def _parse_property(prop: dict, lead_type: str, source_zip: str) -> Optional[Dict[str, Any]]:
    try:
        loc = prop.get("location", {})
        addr = loc.get("address", {})
        address_str = ", ".join(filter(None, [
            addr.get("line"), addr.get("city"),
            addr.get("state_code"), addr.get("postal_code"),
        ]))

        listing = prop.get("list_price", 0) or 0
        desc = prop.get("description", {})
        dom = desc.get("list_date_delta") or 0
        if isinstance(dom, str):
            dom = int(dom) if dom.isdigit() else 0

        # Price reduction
        price_red_pct = 0.0
        price_red_amt = 0.0
        tags = prop.get("tags", [])
        price_reduced = "price_reduced" in tags or "reduced_price" in tags
        if price_reduced:
            orig = prop.get("list_price_max", listing)
            if orig and orig > listing and listing > 0:
                price_red_amt = orig - listing
                price_red_pct = round(price_red_amt / orig * 100, 1)

        slug = prop.get("permalink", "")
        url = f"https://www.realtor.com/realestateandhomes-detail/{slug}" if slug else ""

        # Detect lead type from tags
        actual_type = lead_type
        if "back_on_market" in tags:
            actual_type = "BACK_ON_MARKET"
        elif "foreclosure" in tags:
            actual_type = "PRE_FORECLOSURE"
        elif "for_sale_by_owner" in tags:
            actual_type = "FSBO"

        score = score_property_lead(actual_type, dom, price_red_pct)

        # Contact info (often present on FSBO listings)
        advertisers = prop.get("advertisers", [{}])
        phone = None
        if advertisers:
            phones = advertisers[0].get("phones", [])
            if phones:
                phone = phones[0].get("number")

        return {
            "address": address_str,
            "city": addr.get("city", ""),
            "zip_code": addr.get("postal_code", source_zip),
            "owner_name": advertisers[0].get("name") if advertisers else None,
            "listing_price": listing,
            "days_on_market": dom,
            "price_reduction_pct": price_red_pct,
            "price_reduction_amt": price_red_amt,
            "phone": phone,
            "email": None,
            "listing_url": url,
            "lead_type": actual_type,
            "score": score,
            "source": "realtor",
            "raw_data": {
                "property_id": prop.get("property_id"),
                "beds": desc.get("beds"),
                "baths": desc.get("baths"),
                "sqft": desc.get("sqft"),
                "type": desc.get("type"),
                "tags": tags,
            },
        }
    except Exception:
        return None


class RealtorScraper:
    """Scrape Realtor.com for motivated-seller listings."""

    def __init__(self):
        self.client = httpx.AsyncClient(headers=HEADERS, timeout=30.0, follow_redirects=True)

    async def _search(self, zip_code: str, extra_params: dict) -> List[dict]:
        params = {
            "postal_code": zip_code,
            "limit": 42,
            "offset": 0,
            "status": "for_sale",
            "sort": "list_date",
            **extra_params,
        }
        try:
            resp = await self.client.get(_API_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", {}).get("home_search", {}).get("results", [])
        except httpx.HTTPStatusError as e:
            print(f"[Realtor] HTTP {e.response.status_code} on {zip_code}")
            return []
        except Exception as e:
            print(f"[Realtor] Error on {zip_code}: {e}")
            return []

    async def scrape_zip(self, zip_code: str) -> List[Dict[str, Any]]:
        leads = []

        # FSBO listings
        for prop in await self._search(zip_code, {"listing_type": "by_owner"}):
            lead = _parse_property(prop, "FSBO", zip_code)
            if lead:
                leads.append(lead)
        await asyncio.sleep(random.uniform(2, 4))

        # Price-reduced listings
        for prop in await self._search(zip_code, {"price_reduced_within": "7"}):
            lead = _parse_property(prop, "PRICE_DROP", zip_code)
            if lead and lead.get("price_reduction_pct", 0) >= 3:
                leads.append(lead)
        await asyncio.sleep(random.uniform(2, 4))

        # Back on market
        for prop in await self._search(zip_code, {"status": "back_on_market"}):
            lead = _parse_property(prop, "BACK_ON_MARKET", zip_code)
            if lead:
                leads.append(lead)
        await asyncio.sleep(random.uniform(2, 4))

        # Long DOM
        for prop in await self._search(zip_code, {"age_min": 60, "sort": "list_date"}):
            lead = _parse_property(prop, "FSBO", zip_code)
            if lead and lead.get("days_on_market", 0) >= 60:
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
                print(f"[Realtor] {zip_code}: {len(leads)} leads")
            except Exception as e:
                print(f"[Realtor] {zip_code} failed: {e}")
            if i < len(zips) - 1:
                await asyncio.sleep(random.uniform(3, 6))

        return all_leads

    async def close(self):
        await self.client.aclose()
