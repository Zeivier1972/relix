"""
Redfin scraper — most reliable of the three listing sites.
Uses Redfin's internal stingray API which returns JSON/CSV data.
"""
import asyncio
import csv
import io
import json
import random
import re
from typing import List, Dict, Any, Optional

import httpx

from scrapers.target_zips import ALL_ZIPS, score_property_lead

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.redfin.com/",
}

# Redfin region lookup by zip code
_REGION_URL = "https://www.redfin.com/stingray/do/query-location"
# Redfin download CSV endpoint
_CSV_URL = "https://www.redfin.com/stingray/api/gis-csv"
# Redfin JSON search
_SEARCH_URL = "https://www.redfin.com/stingray/api/gis"


def _parse_csv_row(row: dict, lead_type: str) -> Optional[Dict[str, Any]]:
    """Parse a row from Redfin's download CSV."""
    try:
        address = row.get("ADDRESS", "")
        city    = row.get("CITY", "")
        zip_code = row.get("ZIP OR POSTAL CODE", "")
        url_path = row.get("URL (SEE https://www.redfin.com/buy-a-home/comparative-market-analysis FOR INFO ON PRICING)", "")
        url = f"https://www.redfin.com{url_path}" if url_path and not url_path.startswith("http") else url_path

        price_str = row.get("PRICE", "0").replace("$", "").replace(",", "").strip()
        price = int(float(price_str)) if price_str and price_str not in ("", "—") else 0

        dom_str = row.get("DAYS ON MARKET", "0").strip()
        dom = int(dom_str) if dom_str.isdigit() else 0

        # Price drop detection from HOA/Status column
        status = row.get("STATUS", "").strip()
        sale_type = row.get("SALE TYPE", "").lower()

        actual_type = lead_type
        if "back on market" in status.lower():
            actual_type = "BACK_ON_MARKET"
        elif "for sale by owner" in sale_type or "fsbo" in sale_type:
            actual_type = "FSBO"

        # Price reduction — Redfin CSV doesn't always have this; infer from type
        price_red_pct = 0.0
        if actual_type == "PRICE_DROP":
            price_red_pct = 5.0  # Default when we searched by price-reduced filter

        score = score_property_lead(actual_type, dom, price_red_pct)

        return {
            "address": f"{address}, {city}, FL {zip_code}".strip(", "),
            "city": city,
            "zip_code": zip_code,
            "owner_name": None,
            "listing_price": price,
            "days_on_market": dom,
            "price_reduction_pct": price_red_pct,
            "price_reduction_amt": 0.0,
            "phone": None,
            "email": None,
            "listing_url": url,
            "lead_type": actual_type,
            "score": score,
            "source": "redfin",
            "raw_data": {
                "beds": row.get("BEDS"),
                "baths": row.get("BATHS"),
                "sqft": row.get("SQUARE FEET"),
                "lot_size": row.get("LOT SIZE"),
                "year_built": row.get("YEAR BUILT"),
                "hoa_month": row.get("HOA/MONTH"),
                "status": status,
                "sale_type": sale_type,
            },
        }
    except Exception:
        return None


class RedfinScraper:
    """Scrape Redfin listing data via their download CSV endpoint."""

    def __init__(self):
        self.client = httpx.AsyncClient(headers=HEADERS, timeout=45.0, follow_redirects=True)
        self._region_cache: Dict[str, str] = {}

    async def _get_region_id(self, zip_code: str) -> Optional[str]:
        """Look up Redfin region ID for a zip code."""
        if zip_code in self._region_cache:
            return self._region_cache[zip_code]
        try:
            resp = await self.client.get(
                _REGION_URL,
                params={"location": zip_code, "v": "2"},
            )
            resp.raise_for_status()
            # Response contains JS; extract region ID
            text = resp.text
            match = re.search(r'"id":(\d+),"type":(\d+)', text)
            if match:
                region_id = match.group(1)
                self._region_cache[zip_code] = region_id
                return region_id
        except Exception:
            pass
        return None

    async def _download_csv(self, zip_code: str, extra_params: dict) -> List[dict]:
        """Download Redfin CSV for a zip code with given filters."""
        params = {
            "al": 1,
            "market": "miami",
            "num_beds": 0,
            "num_baths": 0,
            "status": 9,      # Active
            "uipt": "1,2,3,4,5,6,7",
            "v": 8,
            "postal_code": zip_code,
            **extra_params,
        }
        try:
            resp = await self.client.get(_CSV_URL, params=params)
            resp.raise_for_status()
            reader = csv.DictReader(io.StringIO(resp.text))
            return list(reader)
        except Exception as e:
            print(f"[Redfin] CSV error on {zip_code}: {e}")
            return []

    async def scrape_zip(self, zip_code: str) -> List[Dict[str, Any]]:
        leads = []

        # FSBO listings
        for row in await self._download_csv(zip_code, {"listing_type": "3"}):  # 3 = FSBO
            lead = _parse_csv_row(row, "FSBO")
            if lead:
                leads.append(lead)
        await asyncio.sleep(random.uniform(1.5, 3))

        # Price-reduced in last 7 days
        for row in await self._download_csv(zip_code, {"price_reduced_within": 7}):
            lead = _parse_csv_row(row, "PRICE_DROP")
            if lead:
                leads.append(lead)
        await asyncio.sleep(random.uniform(1.5, 3))

        # High days on market (60+)
        for row in await self._download_csv(zip_code, {"time_on_market_range": "60-"}):
            dom_str = row.get("DAYS ON MARKET", "0").strip()
            dom = int(dom_str) if dom_str.isdigit() else 0
            if dom >= 60:
                lead = _parse_csv_row(row, "FSBO")
                if lead:
                    lead["lead_type"] = "FSBO"
                    lead["score"] = "HOT"
                    lead["days_on_market"] = dom
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
                print(f"[Redfin] {zip_code}: {len(leads)} leads")
            except Exception as e:
                print(f"[Redfin] {zip_code} failed: {e}")
            if i < len(zips) - 1:
                await asyncio.sleep(random.uniform(2, 4))

        return all_leads

    async def close(self):
        await self.client.aclose()
