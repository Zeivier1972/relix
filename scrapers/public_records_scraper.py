"""
Florida public records scraper — deed records for cash buyers and LLC purchases.
Searches Miami-Dade, Broward, Palm Beach, and Orange County official records
for warranty deeds and quit-claim deeds filed by LLCs (no mortgage = cash buyer).
Cash buyer and LLC purchase leads are scored HOT.
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
_7_DAYS_AGO = (datetime.now() - timedelta(days=7)).strftime("%m/%d/%Y")

_LLC_PATTERN = re.compile(
    r"\b(LLC|L\.L\.C|L\.C|CORP|INC|HOLDINGS|INVESTMENTS|PROPERTIES|REALTY|TRUST|GROUP)\b",
    re.IGNORECASE,
)


def _is_llc_buyer(grantee: str) -> bool:
    return bool(_LLC_PATTERN.search(grantee))


def _detect_lead_type(grantee: str, has_mortgage: bool = False) -> str:
    if _is_llc_buyer(grantee):
        return "LLC_PURCHASE"
    if not has_mortgage:
        return "CASH_BUYER"
    return "CASH_BUYER"


def _clean(text: str) -> str:
    return " ".join(text.split()).strip() if text else ""


def _make_lead(owner: str, grantee: str, address: str, city: str,
               zip_code: str, county: str, sale_price: float,
               filing_date: str, doc_type: str, case_number: str,
               source_url: str, lead_type: str) -> Dict[str, Any]:
    return {
        "address": address,
        "city": city,
        "zip_code": zip_code,
        "owner_name": owner,
        "listing_price": sale_price,
        "days_on_market": 0,
        "price_reduction_pct": 0.0,
        "price_reduction_amt": 0.0,
        "phone": None,
        "email": None,
        "listing_url": source_url,
        "lead_type": lead_type,
        "score": "HOT",
        "source": "public_records",
        "raw_data": {
            "county": county,
            "doc_type": doc_type,
            "case_number": case_number,
            "filing_date": filing_date,
            "grantee": grantee,
            "sale_price": sale_price,
        },
    }


class MiamiDadePublicRecords:
    """
    Miami-Dade Official Records — warranty deeds filed by LLCs (cash buyers).
    Endpoint: https://www.miami-dadeclerk.com/officialrecords/Search.aspx
    """
    _BASE = "https://www.miami-dadeclerk.com"
    _SEARCH = "https://www.miami-dadeclerk.com/officialrecords/Search.aspx"

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def scrape(self) -> List[Dict[str, Any]]:
        leads = []
        for doc_type in ("WARRANTY DEED", "QUIT CLAIM DEED"):
            try:
                resp = await self.client.get(self._SEARCH, headers=HEADERS)
                if resp.status_code != 200:
                    continue

                soup = BeautifulSoup(resp.text, "lxml")
                vs = soup.find("input", {"id": "__VIEWSTATE"})
                ev = soup.find("input", {"id": "__EVENTVALIDATION"})
                if not vs or not ev:
                    continue

                payload = {
                    "__VIEWSTATE": vs["value"],
                    "__EVENTVALIDATION": ev["value"],
                    "ctl00$ContentPlaceHolder1$txtDocType": doc_type,
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
                    continue

                for row in table.find_all("tr")[1:]:
                    cells = [_clean(td.get_text()) for td in row.find_all("td")]
                    if len(cells) < 5:
                        continue
                    filing_date = cells[0]
                    doc_t = cells[1]
                    case_num = cells[2]
                    grantor = cells[3]   # seller (previous owner)
                    grantee = cells[4]   # buyer
                    address_raw = cells[5] if len(cells) > 5 else ""

                    # Only collect LLC or cash-buyer-flagged deeds
                    if not _is_llc_buyer(grantee) and not _is_llc_buyer(grantor):
                        continue

                    lead_type = _detect_lead_type(grantee)
                    addr_parts = address_raw.split(",")
                    address = addr_parts[0].strip()
                    city = addr_parts[1].strip() if len(addr_parts) > 1 else "Miami"

                    leads.append(_make_lead(
                        owner=grantor,
                        grantee=grantee,
                        address=address,
                        city=city,
                        zip_code="",
                        county="Miami-Dade",
                        sale_price=0.0,
                        filing_date=filing_date,
                        doc_type=doc_t,
                        case_number=case_num,
                        source_url=self._SEARCH,
                        lead_type=lead_type,
                    ))

                await asyncio.sleep(random.uniform(2, 4))

            except Exception as e:
                print(f"[PublicRecords-MiamiDade] {doc_type} error: {e}")

        print(f"[PublicRecords-MiamiDade] {len(leads)} leads")
        return leads


class BrowardPublicRecords:
    """Broward County Official Records — AcclaimWeb portal."""
    _SEARCH = "https://officialrecords.broward.org/AcclaimWeb/search/SearchTypeDocType"

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def scrape(self) -> List[Dict[str, Any]]:
        leads = []
        for doc_type in ("WARRANTY DEED", "QUIT CLAIM DEED"):
            try:
                params = {
                    "DocTypes": doc_type,
                    "DateFrom": _7_DAYS_AGO,
                    "DateTo": _TODAY,
                    "SearchType": "DocType",
                }
                resp = await self.client.get(self._SEARCH, params=params, headers=HEADERS)
                if resp.status_code not in (200, 302):
                    continue

                soup = BeautifulSoup(resp.text, "lxml")
                rows = soup.select("table.search-results tr")[1:]

                for row in rows:
                    cells = [_clean(td.get_text()) for td in row.find_all("td")]
                    if len(cells) < 5:
                        continue
                    filing_date = cells[0]
                    case_num = cells[2] if len(cells) > 2 else ""
                    grantor = cells[3] if len(cells) > 3 else ""
                    grantee = cells[4] if len(cells) > 4 else ""
                    address_raw = cells[5] if len(cells) > 5 else ""

                    if not _is_llc_buyer(grantee) and not _is_llc_buyer(grantor):
                        continue

                    lead_type = _detect_lead_type(grantee)
                    addr_parts = address_raw.split(",")
                    address = addr_parts[0].strip()
                    city = addr_parts[1].strip() if len(addr_parts) > 1 else "Fort Lauderdale"

                    leads.append(_make_lead(
                        owner=grantor,
                        grantee=grantee,
                        address=address,
                        city=city,
                        zip_code="",
                        county="Broward",
                        sale_price=0.0,
                        filing_date=filing_date,
                        doc_type=doc_type,
                        case_number=case_num,
                        source_url=self._SEARCH,
                        lead_type=lead_type,
                    ))

                await asyncio.sleep(random.uniform(2, 4))

            except Exception as e:
                print(f"[PublicRecords-Broward] {doc_type} error: {e}")

        print(f"[PublicRecords-Broward] {len(leads)} leads")
        return leads


class PalmBeachPublicRecords:
    """Palm Beach County Official Records."""
    _SEARCH = "https://or.mypalmbeachclerk.com/AcclaimWeb/search/SearchTypeDocType"

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def scrape(self) -> List[Dict[str, Any]]:
        leads = []
        for doc_type in ("WARRANTY DEED", "QUIT CLAIM DEED"):
            try:
                params = {
                    "DocTypes": doc_type,
                    "DateFrom": _7_DAYS_AGO,
                    "DateTo": _TODAY,
                    "SearchType": "DocType",
                }
                resp = await self.client.get(self._SEARCH, params=params, headers=HEADERS)
                if resp.status_code not in (200, 302):
                    continue

                soup = BeautifulSoup(resp.text, "lxml")
                rows = soup.select("table.search-results tr")[1:]

                for row in rows:
                    cells = [_clean(td.get_text()) for td in row.find_all("td")]
                    if len(cells) < 4:
                        continue
                    filing_date = cells[0]
                    case_num = cells[2] if len(cells) > 2 else ""
                    grantor = cells[3] if len(cells) > 3 else ""
                    grantee = cells[4] if len(cells) > 4 else ""
                    address_raw = cells[5] if len(cells) > 5 else ""

                    if not _is_llc_buyer(grantee) and not _is_llc_buyer(grantor):
                        continue

                    lead_type = _detect_lead_type(grantee)
                    addr_parts = address_raw.split(",")
                    address = addr_parts[0].strip()
                    city = addr_parts[1].strip() if len(addr_parts) > 1 else "West Palm Beach"

                    leads.append(_make_lead(
                        owner=grantor,
                        grantee=grantee,
                        address=address,
                        city=city,
                        zip_code="",
                        county="Palm Beach",
                        sale_price=0.0,
                        filing_date=filing_date,
                        doc_type=doc_type,
                        case_number=case_num,
                        source_url=self._SEARCH,
                        lead_type=lead_type,
                    ))

                await asyncio.sleep(random.uniform(2, 4))

            except Exception as e:
                print(f"[PublicRecords-PalmBeach] {doc_type} error: {e}")

        print(f"[PublicRecords-PalmBeach] {len(leads)} leads")
        return leads


class OrangeCountyPublicRecords:
    """Orange County Comptroller Official Records."""
    _SEARCH = "https://or.myorangeclerk.com/AcclaimWeb/search/SearchTypeDocType"

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def scrape(self) -> List[Dict[str, Any]]:
        leads = []
        for doc_type in ("WARRANTY DEED", "QUIT CLAIM DEED"):
            try:
                params = {
                    "DocTypes": doc_type,
                    "DateFrom": _7_DAYS_AGO,
                    "DateTo": _TODAY,
                    "SearchType": "DocType",
                }
                resp = await self.client.get(self._SEARCH, params=params, headers=HEADERS)
                if resp.status_code not in (200, 302):
                    continue

                soup = BeautifulSoup(resp.text, "lxml")
                rows = soup.select("table.search-results tr")[1:]

                for row in rows:
                    cells = [_clean(td.get_text()) for td in row.find_all("td")]
                    if len(cells) < 4:
                        continue
                    filing_date = cells[0]
                    case_num = cells[2] if len(cells) > 2 else ""
                    grantor = cells[3] if len(cells) > 3 else ""
                    grantee = cells[4] if len(cells) > 4 else ""
                    address_raw = cells[5] if len(cells) > 5 else ""

                    if not _is_llc_buyer(grantee) and not _is_llc_buyer(grantor):
                        continue

                    lead_type = _detect_lead_type(grantee)
                    addr_parts = address_raw.split(",")
                    address = addr_parts[0].strip()
                    city = addr_parts[1].strip() if len(addr_parts) > 1 else "Orlando"

                    leads.append(_make_lead(
                        owner=grantor,
                        grantee=grantee,
                        address=address,
                        city=city,
                        zip_code="",
                        county="Orange",
                        sale_price=0.0,
                        filing_date=filing_date,
                        doc_type=doc_type,
                        case_number=case_num,
                        source_url=self._SEARCH,
                        lead_type=lead_type,
                    ))

                await asyncio.sleep(random.uniform(2, 4))

            except Exception as e:
                print(f"[PublicRecords-Orange] {doc_type} error: {e}")

        print(f"[PublicRecords-Orange] {len(leads)} leads")
        return leads


class PublicRecordsScraper:
    """Aggregate public records scraper — cash buyers and LLC purchases."""

    def __init__(self):
        self.client = httpx.AsyncClient(headers=HEADERS, timeout=45.0, follow_redirects=True)

    async def scrape_all(self) -> List[Dict[str, Any]]:
        scrapers = [
            MiamiDadePublicRecords(self.client),
            BrowardPublicRecords(self.client),
            PalmBeachPublicRecords(self.client),
            OrangeCountyPublicRecords(self.client),
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
                print(f"[PublicRecords] Scraper error: {e}")
            await asyncio.sleep(random.uniform(3, 6))

        print(f"[PublicRecords] Total: {len(all_leads)} unique leads")
        return all_leads

    async def close(self):
        await self.client.aclose()
