"""
Redfin scraper — most reliable of the listing sites.
Uses Redfin's stingray API which returns CSV/JSON data.
Redfin is far less aggressive than Zillow with bot detection.
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
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.redfin.com/",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124"',
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

# Redfin location lookup
_LOCATION_URL = "https://www.redfin.com/stingray/do/location-autocomplete"
# Redfin CSV download
_CSV_URL = "https://www.redfin.com/stingray/api/gis-csv"
# Redfin GIS search (JSON)
_GIS_URL = "https://www.redfin.com/stingray/api/gis"


def _parse_csv_row(row: dict, lead_type: str) -> Optional[Dict[str, Any]]:
    try:
        address  = row.get("ADDRESS", "").strip()
        city     = row.get("CITY", "").strip()
        zip_code = row.get("ZIP OR POSTAL CODE", "").strip()
        if not address:
            return None

        # URL column header varies by export version
        url_path = ""
        for k in row:
            if "URL" in k.upper():
                url_path = row[k].strip()
                break
        url = ("https://www.redfin.com" + url_path
               if url_path and not url_path.startswith("http") else url_path)

        price_str = row.get("PRICE", "0").replace("$", "").replace(",", "").strip()
        try:
            price = int(float(price_str)) if price_str and price_str not in ("", "—", "N/A") else 0
        except ValueError:
            price = 0

        dom_str = row.get("DAYS ON MARKET", "0").strip()
        try:
            dom = int(dom_str) if dom_str and dom_str.isdigit() else 0
        except ValueError:
            dom = 0

        status    = row.get("STATUS", "").strip()
        sale_type = row.get("SALE TYPE", "").lower()

        actual_type = lead_type
        if "back on market" in status.lower():
            actual_type = "BACK_ON_MARKET"
        elif "for sale by owner" in sale_type or "fsbo" in sale_type:
            actual_type = "FSBO"

        price_red_pct = 5.0 if actual_type == "PRICE_DROP" else 0.0
        score = score_property_lead(actual_type, dom, price_red_pct)

        return {
            "address":             f"{address}, {city}, FL {zip_code}".strip(", "),
            "city":                city,
            "zip_code":            zip_code,
            "owner_name":          None,
            "listing_price":       price,
            "days_on_market":      dom,
            "price_reduction_pct": price_red_pct,
            "price_reduction_amt": 0.0,
            "phone":               None,
            "email":               None,
            "listing_url":         url,
            "lead_type":           actual_type,
            "score":               score,
            "source":              "redfin",
            "raw_data": {
                "beds":        row.get("BEDS"),
                "baths":       row.get("BATHS"),
                "sqft":        row.get("SQUARE FEET"),
                "lot_size":    row.get("LOT SIZE"),
                "year_built":  row.get("YEAR BUILT"),
                "hoa_month":   row.get("HOA/MONTH"),
                "status":      status,
                "sale_type":   sale_type,
            },
        }
    except Exception:
        return None


class RedfinScraper:
    """Scrape Redfin listing data via their download CSV endpoint."""

    def __init__(self):
        self.client = httpx.AsyncClient(
            headers=HEADERS, timeout=45.0, follow_redirects=True
        )
        self._region_cache: Dict[str, tuple] = {}   # zip → (region_id, region_type)

    async def _get_region(self, zip_code: str) -> Optional[tuple]:
        """Return (region_id, region_type) for a ZIP code via Redfin's autocomplete."""
        if zip_code in self._region_cache:
            return self._region_cache[zip_code]
        try:
            resp = await self.client.get(
                _LOCATION_URL,
                params={"location": zip_code, "v": "2", "al": "1", "market": "miami"},
            )
            resp.raise_for_status()
            text = resp.text
            # Strip Redfin's JS wrapper "{}&&{...}"
            if text.startswith("{}&&"):
                text = text[4:]
            data = json.loads(text)
            rows = (data.get("payload") or {}).get("sections", [])
            for section in rows:
                for item in section.get("rows", []):
                    if item.get("type") == "2":  # ZIP code result
                        rid = item.get("id", {}).get("tableId") or item.get("id", {}).get("regionId")
                        if rid:
                            result = (str(rid), "2")
                            self._region_cache[zip_code] = result
                            return result
            # Fallback: grab first result regardless of type
            for section in rows:
                for item in section.get("rows", []):
                    rid = (item.get("id") or {}).get("tableId") or (item.get("id") or {}).get("regionId")
                    rtype = str((item.get("id") or {}).get("type") or item.get("type") or "6")
                    if rid:
                        result = (str(rid), rtype)
                        self._region_cache[zip_code] = result
                        return result
        except Exception as e:
            print(f"[Redfin] Region lookup failed for {zip_code}: {e}")
        return None

    async def _download_csv(self, zip_code: str, extra_params: dict,
                            region_id: str = None, region_type: str = "2") -> List[dict]:
        """Download Redfin CSV.  Uses region_id when available, postal_code fallback."""
        if region_id:
            params = {
                "al": 1,
                "market": "miami",
                "num_beds": 0,
                "num_baths": 0,
                "status": 9,        # Active
                "uipt": "1,2,3,4,5,6,7",
                "v": 8,
                "region_id": region_id,
                "region_type": region_type,
                **extra_params,
            }
        else:
            # Fallback without region ID — works for some markets
            params = {
                "al": 1,
                "market": "miami",
                "num_beds": 0,
                "num_baths": 0,
                "status": 9,
                "uipt": "1,2,3,4,5,6,7",
                "v": 8,
                "postal_code": zip_code,
                **extra_params,
            }
        try:
            resp = await self.client.get(_CSV_URL, params=params)
            if resp.status_code == 403:
                print(f"[Redfin] 403 blocked on {zip_code} — skipping")
                return []
            resp.raise_for_status()
            content = resp.text
            if not content.strip() or "DOCTYPE" in content:
                return []
            reader = csv.DictReader(io.StringIO(content))
            return list(reader)
        except Exception as e:
            print(f"[Redfin] CSV error on {zip_code}: {e}")
            return []

    async def scrape_zip(self, zip_code: str) -> List[Dict[str, Any]]:
        leads = []

        # Get Redfin region ID for this ZIP
        region = await self._get_region(zip_code)
        rid    = region[0] if region else None
        rtype  = region[1] if region else "2"

        # FSBO listings (listing_type=3 in Redfin = FSBO)
        for row in await self._download_csv(zip_code, {"sf": "1,2,3,5,6,7", "listing_type": "3"},
                                             rid, rtype):
            lead = _parse_csv_row(row, "FSBO")
            if lead:
                leads.append(lead)
        await asyncio.sleep(random.uniform(1.5, 3))

        # Price-reduced listings (last 7 days)
        for row in await self._download_csv(zip_code, {"price_reduced_within": 7},
                                             rid, rtype):
            lead = _parse_csv_row(row, "PRICE_DROP")
            if lead:
                leads.append(lead)
        await asyncio.sleep(random.uniform(1.5, 3))

        # High days on market (60+ days)
        for row in await self._download_csv(zip_code, {"time_on_market_range": "60-"},
                                             rid, rtype):
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
        seen_urls: set = set()
        blocked = 0

        for i, zip_code in enumerate(zips):
            try:
                leads = await self.scrape_zip(zip_code)
                for lead in leads:
                    url = lead.get("listing_url", "")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        all_leads.append(lead)
                if leads:
                    print(f"[Redfin] {zip_code}: {len(leads)} leads")
            except Exception as e:
                print(f"[Redfin] {zip_code} failed: {e}")
                blocked += 1
                if blocked >= 5:
                    print("[Redfin] Too many failures — stopping early")
                    break

            if i < len(zips) - 1:
                await asyncio.sleep(random.uniform(2, 4))

        print(f"[Redfin] Total: {len(all_leads)} leads across {i+1} ZIPs")
        return all_leads

    async def close(self):
        await self.client.aclose()
