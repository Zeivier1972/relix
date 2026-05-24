"""
Pre-foreclosure scraper — county clerk Lis Pendens filings.
Covers Miami-Dade, Broward, Palm Beach, and Orange County.
All pre-foreclosure leads are scored HOT.
"""
import asyncio
import random
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

import httpx
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_TODAY = datetime.now().strftime("%m/%d/%Y")
_YESTERDAY = (datetime.now() - timedelta(days=1)).strftime("%m/%d/%Y")
_7_DAYS_AGO = (datetime.now() - timedelta(days=7)).strftime("%m/%d/%Y")


def _clean(text: str) -> str:
    return " ".join(text.split()).strip() if text else ""


def _make_lead(owner: str, address: str, city: str, zip_code: str,
               county: str, loan_amount: float, filing_date: str,
               case_number: str, source_url: str) -> Dict[str, Any]:
    return {
        "address": address,
        "city": city,
        "zip_code": zip_code,
        "owner_name": owner,
        "listing_price": loan_amount,
        "days_on_market": 0,
        "price_reduction_pct": 0.0,
        "price_reduction_amt": 0.0,
        "phone": None,
        "email": None,
        "listing_url": source_url,
        "lead_type": "PRE_FORECLOSURE",
        "score": "HOT",
        "source": "preforeclosure",
        "raw_data": {
            "county": county,
            "case_number": case_number,
            "filing_date": filing_date,
            "loan_amount": loan_amount,
        },
    }


class MiamiDadeForeclosureScraper:
    """
    Miami-Dade Clerk of Courts — Lis Pendens search.
    URL: https://www.miami-dadeclerk.com/clerkcourt/CaseSearch.aspx
    The clerk site uses a public case search form; we query by document type.
    """
    _BASE = "https://www.miami-dadeclerk.com"
    _SEARCH = "https://www.miami-dadeclerk.com/clerkcourt/CaseSearch.aspx"

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def scrape(self) -> List[Dict[str, Any]]:
        leads = []
        try:
            # Get the form to extract viewstate tokens
            resp = await self.client.get(self._SEARCH, headers=HEADERS)
            if resp.status_code != 200:
                print(f"[PreForeclosure-MiamiDade] Status {resp.status_code}")
                return leads

            soup = BeautifulSoup(resp.text, "lxml")
            viewstate = soup.find("input", {"id": "__VIEWSTATE"})
            eventval = soup.find("input", {"id": "__EVENTVALIDATION"})

            if not viewstate or not eventval:
                print("[PreForeclosure-MiamiDade] Could not find form tokens — site structure changed")
                return leads

            # Submit search for Lis Pendens filed in last 7 days
            payload = {
                "__VIEWSTATE": viewstate["value"],
                "__EVENTVALIDATION": eventval["value"],
                "ctl00$ContentPlaceHolder1$txtDocType": "LIS PENDENS",
                "ctl00$ContentPlaceHolder1$txtFromDate": _7_DAYS_AGO,
                "ctl00$ContentPlaceHolder1$txtToDate": _TODAY,
                "ctl00$ContentPlaceHolder1$btnSearch": "Search",
            }

            resp2 = await self.client.post(self._SEARCH, data=payload, headers={
                **HEADERS,
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": self._SEARCH,
            })
            soup2 = BeautifulSoup(resp2.text, "lxml")

            table = soup2.find("table", {"id": re.compile("GridView", re.I)})
            if not table:
                print("[PreForeclosure-MiamiDade] No results table found")
                return leads

            rows = table.find_all("tr")[1:]  # skip header
            for row in rows:
                cells = [_clean(td.get_text()) for td in row.find_all("td")]
                if len(cells) < 4:
                    continue
                case_number = cells[0]
                filing_date = cells[1]
                owner = cells[2] if len(cells) > 2 else ""
                address_raw = cells[3] if len(cells) > 3 else ""

                # Parse address — format varies
                addr_parts = address_raw.split(",")
                address = addr_parts[0].strip() if addr_parts else address_raw
                city = addr_parts[1].strip() if len(addr_parts) > 1 else "Miami"

                leads.append(_make_lead(
                    owner=owner,
                    address=address,
                    city=city,
                    zip_code="",
                    county="Miami-Dade",
                    loan_amount=0.0,
                    filing_date=filing_date,
                    case_number=case_number,
                    source_url=self._SEARCH,
                ))

        except Exception as e:
            print(f"[PreForeclosure-MiamiDade] Error: {e}")

        print(f"[PreForeclosure-MiamiDade] {len(leads)} leads")
        return leads


class BrowardForeclosureScraper:
    """
    Broward County Clerk — Official Records search for Lis Pendens.
    URL: https://officialrecords.broward.org/AcclaimWeb/search/SearchTypeDocType
    """
    _SEARCH = "https://officialrecords.broward.org/AcclaimWeb/search/SearchTypeDocType"

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def scrape(self) -> List[Dict[str, Any]]:
        leads = []
        try:
            params = {
                "DocTypes": "LIS PENDENS",
                "DateFrom": _7_DAYS_AGO,
                "DateTo": _TODAY,
                "SearchType": "DocType",
            }
            resp = await self.client.get(self._SEARCH, params=params, headers=HEADERS)
            if resp.status_code not in (200, 302):
                print(f"[PreForeclosure-Broward] Status {resp.status_code}")
                return leads

            soup = BeautifulSoup(resp.text, "lxml")
            rows = soup.select("table.search-results tr")[1:]

            for row in rows:
                cells = [_clean(td.get_text()) for td in row.find_all("td")]
                if len(cells) < 5:
                    continue
                filing_date = cells[0]
                doc_type = cells[1]
                if "lis pendens" not in doc_type.lower():
                    continue
                case_number = cells[2]
                grantor = cells[3]  # Owner (defendant)
                address_raw = cells[4] if len(cells) > 4 else ""

                addr_parts = address_raw.split(",")
                address = addr_parts[0].strip()
                city = addr_parts[1].strip() if len(addr_parts) > 1 else "Fort Lauderdale"

                leads.append(_make_lead(
                    owner=grantor,
                    address=address,
                    city=city,
                    zip_code="",
                    county="Broward",
                    loan_amount=0.0,
                    filing_date=filing_date,
                    case_number=case_number,
                    source_url=self._SEARCH,
                ))

        except Exception as e:
            print(f"[PreForeclosure-Broward] Error: {e}")

        print(f"[PreForeclosure-Broward] {len(leads)} leads")
        return leads


class PalmBeachForeclosureScraper:
    """
    Palm Beach County Clerk — Official Records for Lis Pendens.
    URL: https://www.mypalmbeachclerk.com/official-records
    Uses the AcclaimWeb portal like Broward.
    """
    _SEARCH = "https://or.mypalmbeachclerk.com/AcclaimWeb/search/SearchTypeDocType"

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def scrape(self) -> List[Dict[str, Any]]:
        leads = []
        try:
            params = {
                "DocTypes": "LIS PENDENS",
                "DateFrom": _7_DAYS_AGO,
                "DateTo": _TODAY,
                "SearchType": "DocType",
            }
            resp = await self.client.get(self._SEARCH, params=params, headers=HEADERS)
            if resp.status_code not in (200, 302):
                print(f"[PreForeclosure-PalmBeach] Status {resp.status_code}")
                return leads

            soup = BeautifulSoup(resp.text, "lxml")
            rows = soup.select("table.search-results tr")[1:]

            for row in rows:
                cells = [_clean(td.get_text()) for td in row.find_all("td")]
                if len(cells) < 4:
                    continue
                filing_date = cells[0]
                case_number = cells[2] if len(cells) > 2 else ""
                grantor = cells[3] if len(cells) > 3 else ""
                address_raw = cells[4] if len(cells) > 4 else ""

                addr_parts = address_raw.split(",")
                address = addr_parts[0].strip()
                city = addr_parts[1].strip() if len(addr_parts) > 1 else "West Palm Beach"

                leads.append(_make_lead(
                    owner=grantor,
                    address=address,
                    city=city,
                    zip_code="",
                    county="Palm Beach",
                    loan_amount=0.0,
                    filing_date=filing_date,
                    case_number=case_number,
                    source_url=self._SEARCH,
                ))

        except Exception as e:
            print(f"[PreForeclosure-PalmBeach] Error: {e}")

        print(f"[PreForeclosure-PalmBeach] {len(leads)} leads")
        return leads


class OrangeCountyForeclosureScraper:
    """
    Orange County Clerk — Comptroller Official Records for Lis Pendens.
    URL: https://myorangeclerk.com/official-records
    """
    _SEARCH = "https://or.myorangeclerk.com/AcclaimWeb/search/SearchTypeDocType"

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def scrape(self) -> List[Dict[str, Any]]:
        leads = []
        try:
            params = {
                "DocTypes": "LIS PENDENS",
                "DateFrom": _7_DAYS_AGO,
                "DateTo": _TODAY,
                "SearchType": "DocType",
            }
            resp = await self.client.get(self._SEARCH, params=params, headers=HEADERS)
            if resp.status_code not in (200, 302):
                print(f"[PreForeclosure-Orange] Status {resp.status_code}")
                return leads

            soup = BeautifulSoup(resp.text, "lxml")
            rows = soup.select("table.search-results tr")[1:]

            for row in rows:
                cells = [_clean(td.get_text()) for td in row.find_all("td")]
                if len(cells) < 4:
                    continue
                filing_date = cells[0]
                case_number = cells[2] if len(cells) > 2 else ""
                grantor = cells[3] if len(cells) > 3 else ""
                address_raw = cells[4] if len(cells) > 4 else ""

                addr_parts = address_raw.split(",")
                address = addr_parts[0].strip()
                city = addr_parts[1].strip() if len(addr_parts) > 1 else "Orlando"

                leads.append(_make_lead(
                    owner=grantor,
                    address=address,
                    city=city,
                    zip_code="",
                    county="Orange",
                    loan_amount=0.0,
                    filing_date=filing_date,
                    case_number=case_number,
                    source_url=self._SEARCH,
                ))

        except Exception as e:
            print(f"[PreForeclosure-Orange] Error: {e}")

        print(f"[PreForeclosure-Orange] {len(leads)} leads")
        return leads


class PreForeclosureScraper:
    """Aggregate pre-foreclosure scraper across all four counties."""

    def __init__(self):
        self.client = httpx.AsyncClient(headers=HEADERS, timeout=45.0, follow_redirects=True)

    async def scrape_all(self) -> List[Dict[str, Any]]:
        scrapers = [
            MiamiDadeForeclosureScraper(self.client),
            BrowardForeclosureScraper(self.client),
            PalmBeachForeclosureScraper(self.client),
            OrangeCountyForeclosureScraper(self.client),
        ]

        all_leads = []
        seen = set()

        for scraper in scrapers:
            try:
                leads = await scraper.scrape()
                for lead in leads:
                    key = (lead["address"], lead.get("raw_data", {}).get("case_number", ""))
                    if key not in seen:
                        seen.add(key)
                        all_leads.append(lead)
            except Exception as e:
                print(f"[PreForeclosure] Scraper error: {e}")
            await asyncio.sleep(random.uniform(3, 6))

        print(f"[PreForeclosure] Total: {len(all_leads)} unique leads")
        return all_leads

    async def close(self):
        await self.client.aclose()
