import asyncio
import time
import httpx
from typing import List, Dict, Any

MAX_POST_AGE_HOURS = 48

SUBREDDITS = [
    # ── Florida markets ───────────────────────────────────────────────────
    "Miami",
    "orlando",
    "WestPalmBeach",
    "Broward",

    # ── Latin American community ──────────────────────────────────────────
    "Colombia",

    # ── Finance / first-time buyers ───────────────────────────────────────
    "personalfinance",
    "FirstTimeHomeBuyer",
    "realestateinvesting",
    "realestate",

    # ── Investment & lifestyle ────────────────────────────────────────────
    "AirBnB",
    "fatFIRE",
    "legaladvice",
]

SEARCH_TERMS = [
    # ── English buyer intent ──────────────────────────────────────────────
    "buying home Miami",
    "buy house Miami",
    "first home Florida",
    "real estate Miami",
    "mortgage Florida",
    "down payment Miami",
    "moving to Florida",
    "pre-construction Miami",
    "new construction Florida",
    "pre-construction condo Miami",
    "Colombian investing USA",
    "invest Miami real estate",
    "relocating to Miami",
    "relocating to Florida",
    "airbnb investment Miami",
    "short term rental Florida",

    # ── Spanish buyer intent ──────────────────────────────────────────────
    "comprar casa florida",
    "comprar casa miami",
    "primera casa florida",
    "pre construccion miami",
    "invertir en florida",
    "colombianos comprando casa usa",
]

BUYER_INTENT_KEYWORDS = [
    "want to buy", "looking to buy", "first home", "first house",
    "comprar casa", "quiero comprar", "down payment", "mortgage",
    "pre-construction", "preconstruccion", "moving to miami",
    "moving to florida", "mudandome", "relocating", "reubicandome",
    "invest in", "invertir en", "primera casa",
]


class RedditScraper:
    """Scrape buyer-intent posts from Reddit for Catherine Gomez P.A. — South Florida pre-construction specialist."""

    BASE_URL = "https://www.reddit.com"
    HEADERS = {"User-Agent": "CatherineGomezPA-RELIX/1.0 (South Florida real estate)"}

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0, headers=self.HEADERS)

    def _has_buyer_intent(self, text: str) -> bool:
        text_lower = text.lower()
        return any(kw in text_lower for kw in BUYER_INTENT_KEYWORDS)

    async def search_subreddit(self, subreddit: str, query: str, limit: int = 25) -> List[Dict[str, Any]]:
        url = f"{self.BASE_URL}/r/{subreddit}/search.json"
        params = {"q": query, "restrict_sr": "true", "sort": "new", "limit": limit}

        try:
            resp = await self.client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            posts = data.get("data", {}).get("children", [])
            cutoff_ts = time.time() - MAX_POST_AGE_HOURS * 3600
            leads = []
            for post in posts:
                p = post.get("data", {})
                # Skip posts older than 48 hours
                created_utc = p.get("created_utc") or 0
                if created_utc < cutoff_ts:
                    continue
                title = p.get("title", "")
                selftext = p.get("selftext", "")
                combined = f"{title} {selftext}"
                if not self._has_buyer_intent(combined):
                    continue
                author = p.get("author", "Unknown")
                if author in ("Unknown", "[deleted]", "AutoModerator"):
                    continue
                leads.append({
                    "name": author,
                    "property_url": f"https://reddit.com{p.get('permalink', '')}",
                    "source": "reddit",
                    "raw_data": {
                        "title": title,
                        "selftext": selftext[:500],
                        "subreddit": subreddit,
                        "query": query,
                        "score": p.get("score"),
                        "num_comments": p.get("num_comments"),
                        "url": p.get("url"),
                        "permalink": p.get("permalink"),
                        "created_utc": int(created_utc),
                    },
                })
            return leads
        except Exception as e:
            print(f"[Reddit] Error searching r/{subreddit} for '{query}': {e}")
            return []

    async def scrape_all(self, subreddits: List[str] = None, search_terms: List[str] = None) -> List[Dict[str, Any]]:
        subreddits = subreddits or SUBREDDITS
        search_terms = search_terms or SEARCH_TERMS
        all_leads = []
        seen_urls = set()

        for subreddit in subreddits:
            sub_count = 0
            for term in search_terms:
                try:
                    leads = await self.search_subreddit(subreddit, term)
                    for lead in leads:
                        url = lead.get("property_url", "")
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            all_leads.append(lead)
                            sub_count += 1
                    # Respect Reddit rate limit: ~1 req/sec
                    await asyncio.sleep(1.1)
                except Exception as e:
                    print(f"[Reddit] Skipping r/{subreddit} '{term}': {e}")
            print(f"[Reddit] r/{subreddit}: {sub_count} buyer-intent posts")

        return all_leads

    async def close(self):
        await self.client.aclose()
