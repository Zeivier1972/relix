"""
New construction scraper — active communities and available homes from major builders.
Targets: Lennar, KB Home, Pulte, DR Horton, Taylor Morrison, Mattamy, Meritage, Century.
Scrapes community pages for target zip codes. All new construction leads are HOT.
Note: builder inquiry/contact forms are not publicly scrape-able; this scrapes
public community listing pages for available homes and pricing.
"""
import asyncio
import json
import random
import re
from typing import List, Dict, Any, Optional
from urllib.parse import urlencode, urljoin

import httpx
from bs4 import BeautifulSoup

from scrapers.target_zips import ALL_ZIPS

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_TARGET_ZIPS_SET = set(ALL_ZIPS)

# Florida cities for filtering
_FL_CITIES = {
    "miami", "hialeah", "homestead", "coral gables", "miami beach", "doral",
    "kendall", "cutler bay", "pembroke pines", "miramar", "hollywood", "sunrise",
    "plantation", "davie", "fort lauderdale", "pompano beach", "boca raton",
    "delray beach", "boynton beach", "west palm beach", "palm beach gardens",
    "lake worth", "wellington", "jupiter", "orlando", "kissimmee", "sanford",
    "apopka", "ocoee", "winter garden", "clermont", "saint cloud",
}


def _in_target_area(city: str, zip_code: str) -> bool:
    if zip_code and zip_code in _TARGET_ZIPS_SET:
        return True
    if city and city.lower().strip() in _FL_CITIES:
        return True
    return False


def _make_lead(builder: str, community: str, address: str, city: str,
               zip_code: str, price_from: float, url: str,
               beds: str = "", baths: str = "",
               sqft: str = "") -> Dict[str, Any]:
    return {
        "address": address or community,
        "city": city,
        "zip_code": zip_code,
        "owner_name": None,
        "listing_price": price_from,
        "days_on_market": 0,
        "price_reduction_pct": 0.0,
        "price_reduction_amt": 0.0,
        "phone": None,
        "email": None,
        "listing_url": url,
        "lead_type": "NEW_CONSTRUCTION",
        "score": "HOT",
        "source": "new_construction",
        "raw_data": {
            "builder": builder,
            "community": community,
            "beds": beds,
            "baths": baths,
            "sqft": sqft,
        },
    }


def _parse_price(text: str) -> float:
    text = re.sub(r"[^\d.]", "", text.replace(",", ""))
    try:
        return float(text)
    except (ValueError, TypeError):
        return 0.0


class LennarScraper:
    """Lennar — scrapes /homes/fl/ community search API."""
    _API = "https://www.lennar.com/api/homes/search"

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def scrape(self) -> List[Dict[str, Any]]:
        leads = []
        try:
            params = {
                "state": "FL",
                "limit": 200,
                "offset": 0,
                "status": "active",
            }
            resp = await self.client.get(self._API, params=params, headers={
                **HEADERS, "Accept": "application/json",
            })
            if resp.status_code != 200:
                print(f"[NewConstruction-Lennar] Status {resp.status_code}")
                return leads

            data = resp.json()
            communities = data.get("communities") or data.get("results") or data.get("data") or []

            for c in communities:
                city = c.get("city", "")
                zip_code = str(c.get("zip") or c.get("postalCode") or "")
                if not _in_target_area(city, zip_code):
                    continue

                community = c.get("name") or c.get("communityName", "")
                address = c.get("address") or c.get("streetAddress", "")
                price_from = _parse_price(str(c.get("priceFrom") or c.get("price") or "0"))
                url = c.get("url") or c.get("href") or ""
                if url and not url.startswith("http"):
                    url = f"https://www.lennar.com{url}"

                leads.append(_make_lead("Lennar", community, address, city, zip_code, price_from, url))

        except Exception as e:
            print(f"[NewConstruction-Lennar] Error: {e}")

        print(f"[NewConstruction-Lennar] {len(leads)} communities")
        return leads


class DRHortonScraper:
    """DR Horton — uses their JSON search endpoint."""
    _API = "https://www.drhorton.com/api/communities/search"

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def scrape(self) -> List[Dict[str, Any]]:
        leads = []
        try:
            payload = {"state": "FL", "pageSize": 500, "pageNumber": 1}
            resp = await self.client.post(self._API, json=payload, headers={
                **HEADERS, "Content-Type": "application/json",
            })
            if resp.status_code != 200:
                print(f"[NewConstruction-DRHorton] Status {resp.status_code}")
                return leads

            data = resp.json()
            items = data.get("communities") or data.get("items") or data.get("results") or []

            for c in items:
                city = c.get("city", "")
                zip_code = str(c.get("zip") or c.get("zipCode") or "")
                if not _in_target_area(city, zip_code):
                    continue

                community = c.get("communityName") or c.get("name", "")
                address = c.get("address", "")
                price_from = _parse_price(str(c.get("priceFrom") or c.get("basePrice") or "0"))
                url = c.get("url") or c.get("communityUrl") or ""
                if url and not url.startswith("http"):
                    url = f"https://www.drhorton.com{url}"

                leads.append(_make_lead("DR Horton", community, address, city, zip_code, price_from, url))

        except Exception as e:
            print(f"[NewConstruction-DRHorton] Error: {e}")

        print(f"[NewConstruction-DRHorton] {len(leads)} communities")
        return leads


class PulteScraper:
    """Pulte Homes — scrapes the /homes/florida/ listing page."""
    _URL = "https://www.pulte.com/homes/florida"

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def scrape(self) -> List[Dict[str, Any]]:
        leads = []
        try:
            resp = await self.client.get(self._URL, headers=HEADERS)
            if resp.status_code != 200:
                print(f"[NewConstruction-Pulte] Status {resp.status_code}")
                return leads

            soup = BeautifulSoup(resp.text, "lxml")

            # Look for JSON-LD or embedded JSON data
            for script in soup.find_all("script", type="application/json"):
                try:
                    data = json.loads(script.string or "")
                    communities = (
                        data.get("communities") or data.get("props", {})
                        .get("pageProps", {}).get("communities") or []
                    )
                    for c in communities:
                        city = c.get("city", "")
                        zip_code = str(c.get("zip") or c.get("postalCode") or "")
                        if not _in_target_area(city, zip_code):
                            continue
                        community = c.get("name", "")
                        price_from = _parse_price(str(c.get("priceFrom") or "0"))
                        url = c.get("url") or c.get("href") or self._URL
                        leads.append(_make_lead("Pulte", community, "", city, zip_code, price_from, url))
                except Exception:
                    continue

            if not leads:
                # Fallback: parse HTML cards
                cards = soup.select("[class*='community-card'], [class*='home-card'], article")
                for card in cards:
                    city_el = card.select_one("[class*='city'], .location, [class*='location']")
                    price_el = card.select_one("[class*='price'], .price")
                    name_el = card.select_one("h2, h3, [class*='name']")
                    link_el = card.select_one("a[href]")

                    city = city_el.get_text(strip=True) if city_el else ""
                    price_from = _parse_price(price_el.get_text(strip=True)) if price_el else 0.0
                    community = name_el.get_text(strip=True) if name_el else ""
                    url = link_el["href"] if link_el else ""
                    if url and not url.startswith("http"):
                        url = f"https://www.pulte.com{url}"

                    if city and _in_target_area(city, ""):
                        leads.append(_make_lead("Pulte", community, "", city, "", price_from, url))

        except Exception as e:
            print(f"[NewConstruction-Pulte] Error: {e}")

        print(f"[NewConstruction-Pulte] {len(leads)} communities")
        return leads


class TaylorMorrisonScraper:
    """Taylor Morrison — JSON API for FL communities."""
    _API = "https://www.taylormorrison.com/api/2.0/communities"

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def scrape(self) -> List[Dict[str, Any]]:
        leads = []
        try:
            params = {"state": "FL", "take": 300}
            resp = await self.client.get(self._API, params=params, headers={
                **HEADERS, "Accept": "application/json",
            })
            if resp.status_code != 200:
                print(f"[NewConstruction-TaylorMorrison] Status {resp.status_code}")
                return leads

            data = resp.json()
            items = data if isinstance(data, list) else (data.get("communities") or data.get("items") or [])

            for c in items:
                city = c.get("city", "")
                zip_code = str(c.get("zipCode") or c.get("zip") or "")
                if not _in_target_area(city, zip_code):
                    continue

                community = c.get("communityName") or c.get("name", "")
                address = c.get("address1") or c.get("address", "")
                price_from = _parse_price(str(c.get("priceFrom") or "0"))
                url = c.get("url") or c.get("communityPageUrl") or ""
                if url and not url.startswith("http"):
                    url = f"https://www.taylormorrison.com{url}"

                leads.append(_make_lead("Taylor Morrison", community, address, city, zip_code, price_from, url))

        except Exception as e:
            print(f"[NewConstruction-TaylorMorrison] Error: {e}")

        print(f"[NewConstruction-TaylorMorrison] {len(leads)} communities")
        return leads


class KBHomeScraper:
    """KB Home — scrapes FL search page for community listings."""
    _URL = "https://www.kbhome.com/new-homes-florida"
    _API = "https://www.kbhome.com/api/communities/search"

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def scrape(self) -> List[Dict[str, Any]]:
        leads = []
        try:
            resp = await self.client.get(self._URL, headers=HEADERS)
            if resp.status_code != 200:
                print(f"[NewConstruction-KBHome] Status {resp.status_code}")
                return leads

            soup = BeautifulSoup(resp.text, "lxml")

            # Extract embedded JSON
            for script in soup.find_all("script"):
                if script.string and "communities" in (script.string or ""):
                    try:
                        # Look for JSON array
                        match = re.search(r'"communities"\s*:\s*(\[.*?\])', script.string, re.DOTALL)
                        if match:
                            communities = json.loads(match.group(1))
                            for c in communities:
                                city = c.get("city", "")
                                zip_code = str(c.get("zipCode") or c.get("zip") or "")
                                if not _in_target_area(city, zip_code):
                                    continue
                                community = c.get("name") or c.get("communityName", "")
                                price_from = _parse_price(str(c.get("priceFrom") or "0"))
                                url = c.get("url") or c.get("href") or ""
                                if url and not url.startswith("http"):
                                    url = f"https://www.kbhome.com{url}"
                                leads.append(_make_lead("KB Home", community, "", city, zip_code, price_from, url))
                    except Exception:
                        continue

            if not leads:
                # HTML fallback
                cards = soup.select(".community-card, .community-result, [data-community]")
                for card in cards:
                    city_el = card.select_one(".city, .location, [class*='city']")
                    price_el = card.select_one(".price, [class*='price']")
                    name_el = card.select_one("h2, h3, .community-name")
                    link_el = card.select_one("a[href]")

                    city = city_el.get_text(strip=True) if city_el else ""
                    if not _in_target_area(city, ""):
                        continue
                    price_from = _parse_price(price_el.get_text(strip=True)) if price_el else 0.0
                    community = name_el.get_text(strip=True) if name_el else ""
                    url = link_el["href"] if link_el else ""
                    if url and not url.startswith("http"):
                        url = f"https://www.kbhome.com{url}"
                    leads.append(_make_lead("KB Home", community, "", city, "", price_from, url))

        except Exception as e:
            print(f"[NewConstruction-KBHome] Error: {e}")

        print(f"[NewConstruction-KBHome] {len(leads)} communities")
        return leads


class MeritageHomesScraper:
    """Meritage Homes — JSON community search."""
    _API = "https://www.meritagehomes.com/api/community/search"

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def scrape(self) -> List[Dict[str, Any]]:
        leads = []
        try:
            params = {"state": "FL", "pageSize": 200}
            resp = await self.client.get(self._API, params=params, headers={
                **HEADERS, "Accept": "application/json",
            })
            if resp.status_code != 200:
                print(f"[NewConstruction-Meritage] Status {resp.status_code}")
                return leads

            data = resp.json()
            items = data if isinstance(data, list) else (data.get("communities") or data.get("data") or [])

            for c in items:
                city = c.get("city", "")
                zip_code = str(c.get("zip") or c.get("postalCode") or "")
                if not _in_target_area(city, zip_code):
                    continue
                community = c.get("name") or c.get("communityName", "")
                price_from = _parse_price(str(c.get("priceFrom") or c.get("price") or "0"))
                url = c.get("url") or c.get("detailUrl") or ""
                if url and not url.startswith("http"):
                    url = f"https://www.meritagehomes.com{url}"
                leads.append(_make_lead("Meritage", community, "", city, zip_code, price_from, url))

        except Exception as e:
            print(f"[NewConstruction-Meritage] Error: {e}")

        print(f"[NewConstruction-Meritage] {len(leads)} communities")
        return leads


class MattamyScraper:
    """Mattamy Homes — scrapes FL communities page."""
    _URL = "https://www.mattamyhomes.com/florida"

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def scrape(self) -> List[Dict[str, Any]]:
        leads = []
        try:
            resp = await self.client.get(self._URL, headers=HEADERS)
            if resp.status_code != 200:
                print(f"[NewConstruction-Mattamy] Status {resp.status_code}")
                return leads

            soup = BeautifulSoup(resp.text, "lxml")

            # Embedded JSON-LD or __NEXT_DATA__
            next_data = soup.find("script", {"id": "__NEXT_DATA__"})
            if next_data and next_data.string:
                try:
                    data = json.loads(next_data.string)
                    communities = (
                        data.get("props", {}).get("pageProps", {}).get("communities") or
                        data.get("props", {}).get("pageProps", {}).get("data", {}).get("communities") or []
                    )
                    for c in communities:
                        city = c.get("city", "")
                        zip_code = str(c.get("postalCode") or c.get("zip") or "")
                        if not _in_target_area(city, zip_code):
                            continue
                        community = c.get("name", "")
                        price_from = _parse_price(str(c.get("priceFrom") or "0"))
                        url = c.get("slug") or c.get("url") or ""
                        if url and not url.startswith("http"):
                            url = f"https://www.mattamyhomes.com{url}"
                        leads.append(_make_lead("Mattamy", community, "", city, zip_code, price_from, url))
                except Exception:
                    pass

            if not leads:
                cards = soup.select("[class*='community'], [class*='Community']")
                for card in cards:
                    city_el = card.select_one("[class*='city'], [class*='location']")
                    price_el = card.select_one("[class*='price'], [class*='Price']")
                    name_el = card.select_one("h2, h3, [class*='name']")
                    link_el = card.select_one("a[href]")

                    city = city_el.get_text(strip=True) if city_el else ""
                    if not _in_target_area(city, ""):
                        continue
                    price_from = _parse_price(price_el.get_text(strip=True)) if price_el else 0.0
                    community = name_el.get_text(strip=True) if name_el else ""
                    url = link_el["href"] if link_el else ""
                    if url and not url.startswith("http"):
                        url = f"https://www.mattamyhomes.com{url}"
                    leads.append(_make_lead("Mattamy", community, "", city, "", price_from, url))

        except Exception as e:
            print(f"[NewConstruction-Mattamy] Error: {e}")

        print(f"[NewConstruction-Mattamy] {len(leads)} communities")
        return leads


class NewConstructionScraper:
    """Aggregate scraper across all major FL builders."""

    def __init__(self):
        self.client = httpx.AsyncClient(headers=HEADERS, timeout=45.0, follow_redirects=True)

    async def scrape_all(self) -> List[Dict[str, Any]]:
        scrapers = [
            ("Lennar", LennarScraper(self.client)),
            ("DR Horton", DRHortonScraper(self.client)),
            ("Pulte", PulteScraper(self.client)),
            ("Taylor Morrison", TaylorMorrisonScraper(self.client)),
            ("KB Home", KBHomeScraper(self.client)),
            ("Meritage", MeritageHomesScraper(self.client)),
            ("Mattamy", MattamyScraper(self.client)),
        ]

        all_leads = []
        seen_urls = set()

        for name, scraper in scrapers:
            try:
                leads = await scraper.scrape()
                for lead in leads:
                    url = lead.get("listing_url", "")
                    key = url or lead.get("address", "")
                    if key and key not in seen_urls:
                        seen_urls.add(key)
                        all_leads.append(lead)
            except Exception as e:
                print(f"[NewConstruction] {name} error: {e}")
            await asyncio.sleep(random.uniform(2, 5))

        print(f"[NewConstruction] Total: {len(all_leads)} communities in target areas")
        return all_leads

    async def close(self):
        await self.client.aclose()
