"""
Sunbiz.org LLC scraper — new Florida LLC registrations for real estate keywords.
Searches Division of Corporations for new entities with real estate-related names
filed in the last 30 days. New real estate LLCs are often cash buyers.
All leads are scored HOT (LLC_PURCHASE type).
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
    "Referer": "https://search.sunbiz.org/",
}

_SEARCH_URL = "https://search.sunbiz.org/Inquiry/CorporationSearch/ByName"

# Keywords indicating real estate investment activity
_RE_KEYWORDS = [
    "REALTY", "REAL ESTATE", "PROPERTIES", "HOLDINGS", "INVESTMENTS",
    "CAPITAL", "HOMES", "HOUSING", "PROPERTY", "ASSETS", "ACQUISITIONS",
    "VENTURES", "LAND", "DEVELOPMENT", "RESIDENTIAL", "RENTAL",
]

_TODAY = datetime.now().strftime("%m/%d/%Y")
_30_DAYS_AGO = (datetime.now() - timedelta(days=30)).strftime("%m/%d/%Y")


def _matches_re_keyword(name: str) -> bool:
    name_upper = name.upper()
    return any(kw in name_upper for kw in _RE_KEYWORDS)


def _parse_filing_date(text: str) -> Optional[str]:
    match = re.search(r"\d{2}/\d{2}/\d{4}", text)
    return match.group(0) if match else None


class SunbizScraper:
    """
    Scrapes Sunbiz.org for newly formed Florida LLCs with real estate keywords.
    Each keyword search returns a paginated list of company names with filing dates.
    """

    def __init__(self):
        self.client = httpx.AsyncClient(headers=HEADERS, timeout=45.0, follow_redirects=True)
        self._cutoff = datetime.now() - timedelta(days=30)

    async def _search_keyword(self, keyword: str) -> List[Dict[str, Any]]:
        leads = []
        try:
            params = {
                "SearchNameOrder": keyword,
                "ActiveInactiveSearchOption": "A",  # Active only
                "SearchTerm": keyword,
                "EntityType": "LLC",
                "SearchType": "Contains",
            }
            resp = await self.client.get(_SEARCH_URL, params=params, headers=HEADERS)
            if resp.status_code != 200:
                return leads

            soup = BeautifulSoup(resp.text, "lxml")

            # Results table on sunbiz search page
            table = soup.find("table", {"class": re.compile("search-results|results", re.I)})
            if not table:
                # Try any table with LLC data
                tables = soup.find_all("table")
                for t in tables:
                    headers_row = t.find("tr")
                    if headers_row and "entity name" in headers_row.get_text().lower():
                        table = t
                        break

            if not table:
                return leads

            rows = table.find_all("tr")[1:]  # skip header
            for row in rows:
                cells = row.find_all("td")
                if len(cells) < 3:
                    continue

                entity_name = cells[0].get_text(strip=True)
                doc_number = cells[1].get_text(strip=True) if len(cells) > 1 else ""
                status = cells[2].get_text(strip=True) if len(cells) > 2 else ""
                filing_date_text = cells[3].get_text(strip=True) if len(cells) > 3 else ""
                state = cells[4].get_text(strip=True) if len(cells) > 4 else ""

                # Skip non-active, non-Florida, non-real-estate entities
                if status.upper() not in ("ACTIVE", ""):
                    continue
                if state and state.upper() not in ("FL", "FLORIDA", ""):
                    continue
                if not _matches_re_keyword(entity_name):
                    continue

                # Parse and filter by filing date
                filing_date = _parse_filing_date(filing_date_text)
                if filing_date:
                    try:
                        filed_dt = datetime.strptime(filing_date, "%m/%d/%Y")
                        if filed_dt < self._cutoff:
                            continue
                    except ValueError:
                        pass

                # Build detail URL for this entity
                detail_link = cells[0].find("a")
                detail_url = ""
                if detail_link and detail_link.get("href"):
                    href = detail_link["href"]
                    detail_url = f"https://search.sunbiz.org{href}" if href.startswith("/") else href

                leads.append({
                    "address": "",
                    "city": "Florida",
                    "zip_code": "",
                    "owner_name": entity_name,
                    "listing_price": 0.0,
                    "days_on_market": 0,
                    "price_reduction_pct": 0.0,
                    "price_reduction_amt": 0.0,
                    "phone": None,
                    "email": None,
                    "listing_url": detail_url,
                    "lead_type": "LLC_PURCHASE",
                    "score": "HOT",
                    "source": "sunbiz",
                    "raw_data": {
                        "entity_name": entity_name,
                        "doc_number": doc_number,
                        "status": status,
                        "filing_date": filing_date or filing_date_text,
                        "keyword_matched": keyword,
                        "state": state,
                    },
                })

        except Exception as e:
            print(f"[Sunbiz] Keyword '{keyword}' error: {e}")

        return leads

    async def _enrich_with_agent(self, lead: Dict[str, Any]) -> Dict[str, Any]:
        """
        Optionally fetch the entity detail page to get registered agent address.
        This gives us a physical address to associate with the LLC.
        """
        url = lead.get("listing_url", "")
        if not url:
            return lead
        try:
            resp = await self.client.get(url, headers=HEADERS)
            if resp.status_code != 200:
                return lead

            soup = BeautifulSoup(resp.text, "lxml")

            # Registered Agent section
            agent_section = soup.find(string=re.compile("Registered Agent", re.I))
            if agent_section:
                parent = agent_section.find_parent()
                if parent:
                    # Next siblings often contain the address
                    address_parts = []
                    for sibling in parent.find_next_siblings(limit=4):
                        text = sibling.get_text(strip=True)
                        if text and not re.match(r"registered agent", text, re.I):
                            address_parts.append(text)
                    if address_parts:
                        lead["address"] = address_parts[0]
                        if len(address_parts) > 1:
                            city_state = address_parts[1]
                            parts = city_state.split(",")
                            lead["city"] = parts[0].strip() if parts else ""

            # Principal address
            principal = soup.find(string=re.compile("Principal Address", re.I))
            if principal and not lead.get("address"):
                parent = principal.find_parent()
                if parent:
                    address_parts = []
                    for sibling in parent.find_next_siblings(limit=3):
                        text = sibling.get_text(strip=True)
                        if text:
                            address_parts.append(text)
                    if address_parts:
                        lead["address"] = address_parts[0]

        except Exception:
            pass

        return lead

    async def scrape_all(self) -> List[Dict[str, Any]]:
        all_leads = []
        seen_entities = set()

        # Stagger keyword searches with delays
        for keyword in _RE_KEYWORDS:
            results = await self._search_keyword(keyword)
            for lead in results:
                entity = lead.get("raw_data", {}).get("entity_name", "")
                if entity and entity not in seen_entities:
                    seen_entities.add(entity)
                    all_leads.append(lead)
            print(f"[Sunbiz] '{keyword}': {len(results)} results")
            await asyncio.sleep(random.uniform(2, 4))

        # Enrich a sample with registered agent addresses (throttled)
        enriched = []
        for i, lead in enumerate(all_leads[:50]):  # cap at 50 detail lookups
            enriched_lead = await self._enrich_with_agent(lead)
            enriched.append(enriched_lead)
            if i < len(all_leads) - 1:
                await asyncio.sleep(random.uniform(1, 2))

        # Remaining leads without enrichment
        if len(all_leads) > 50:
            enriched.extend(all_leads[50:])

        print(f"[Sunbiz] Total: {len(enriched)} unique LLC leads")
        return enriched

    async def close(self):
        await self.client.aclose()
