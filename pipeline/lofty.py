import httpx
import json
import os
from typing import Dict, Optional, Any
from dotenv import load_dotenv

load_dotenv()

LOFTY_API_KEY = os.getenv("LOFTY_API_KEY")
# Direct Lofty API (not Zapier) — used for property leads with real contact info
_LOFTY_API_BASE = os.getenv("LOFTY_API_BASE_URL", "https://api.lofty.ai")
ZAPIER_LOFTY_WEBHOOK = os.getenv("ZAPIER_LOFTY_WEBHOOK", "")

ELIGIBLE_SCORES = {"HOT", "WARM"}


def _detect_language(lead_data: Dict) -> str:
    source = lead_data.get("source", "")
    if source == "reddit":
        return "english"
    return "spanish"


def _detect_platform(lead_data: Dict) -> str:
    source = lead_data.get("source", "")
    mapping = {
        "instagram_hashtags": "instagram",
        "instagram_comments": "instagram",
        "facebook_groups": "facebook",
        "tiktok_comments": "tiktok",
        "reddit": "reddit",
    }
    return mapping.get(source, source)


def _build_tags(lead_data: Dict) -> str:
    tags = ["RELIX"]
    score = lead_data.get("qualification_score", "")
    if score:
        tags.append(score)
    platform = _detect_platform(lead_data)
    if platform:
        tags.append(platform)
    return ", ".join(tags)


class LoftyCRMClient:
    """
    Push HOT and WARM leads to Lofty via Zapier webhook.
    Leads without a phone or email still get pushed — contact info
    will be enriched later by ManyChat.
    Only skips if the webhook URL is not configured.
    """

    def __init__(self):
        self.webhook_url = ZAPIER_LOFTY_WEBHOOK
        self.client = httpx.AsyncClient(timeout=15.0)

    async def create_lead(self, lead_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        score = (lead_data.get("qualification_score") or "").upper()

        if score not in ELIGIBLE_SCORES:
            return None

        if not self.webhook_url:
            print("[Lofty] ZAPIER_LOFTY_WEBHOOK not set — skipping CRM push")
            return None

        name_parts = (lead_data.get("name") or "Unknown").split()
        first_name = name_parts[0]
        last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

        raw = lead_data.get("raw_data") or {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = {}
        content = (
            raw.get("caption")
            or raw.get("postText")
            or raw.get("text")
            or raw.get("title")
            or raw.get("selftext")
            or ""
        )

        payload = {
            "firstName": first_name,
            "lastName": last_name,
            "email": lead_data.get("email") or "",
            "phone": lead_data.get("phone") or "",
            "source": lead_data.get("source", "RELIX"),
            "score": score,
            "profileUrl": lead_data.get("property_url") or "",
            "content": str(content)[:500],
            "notes": lead_data.get("qualification_reasoning") or "",
            "tags": _build_tags(lead_data),
            "leadType": "buyer",
            "language": _detect_language(lead_data),
            "platform": _detect_platform(lead_data),
        }

        try:
            response = await self.client.post(self.webhook_url, json=payload)
            response.raise_for_status()
            print(f"[Lofty] Zapier webhook sent: {first_name} {last_name} ({score})")
            return {"status": "sent", "lead": lead_data.get("name"), "score": score}
        except httpx.HTTPError as e:
            print(f"[Lofty] Zapier webhook error: {e}")
            return None

    async def close(self):
        await self.client.aclose()


async def push_directly_to_lofty(lead: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Push a property lead directly to Lofty CRM via API key.
    Only fires when the lead has a name AND (phone OR email).
    Does NOT use or modify the Zapier webhook.
    """
    if not LOFTY_API_KEY:
        print("[Lofty-Direct] LOFTY_API_KEY not set — skipping")
        return None

    name = (lead.get("owner_name") or lead.get("name") or "").strip()
    phone = (lead.get("phone") or "").strip()
    email = (lead.get("email") or "").strip()

    if not name or (not phone and not email):
        return None  # Insufficient contact info

    name_parts = name.split()
    first = name_parts[0]
    last = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

    lead_type = lead.get("lead_type", "")
    score = lead.get("score", "")
    source = lead.get("source", "relix")
    address = lead.get("address", "")
    price = lead.get("listing_price", 0)
    dom = lead.get("days_on_market", 0)
    listing_url = lead.get("listing_url", "")

    # Map lead type to Lofty contact type
    lofty_lead_type = "seller" if lead_type in (
        "FSBO", "PRE_FORECLOSURE", "PRICE_DROP", "BACK_ON_MARKET", "EXPIRED"
    ) else "buyer" if lead_type in (
        "NEW_CONSTRUCTION", "CASH_BUYER"
    ) else "investor"

    tags = ["RELIX", score, lead_type, source]
    notes = (
        f"Source: {source} | Type: {lead_type} | Score: {score}\n"
        f"Property: {address}\n"
        f"Price: ${price:,.0f} | DOM: {dom}\n"
        f"Listing: {listing_url}"
    )

    payload = {
        "firstName": first,
        "lastName": last,
        "phone": phone,
        "email": email,
        "tags": ", ".join(t for t in tags if t),
        "leadType": lofty_lead_type,
        "source": f"RELIX-{lead_type}",
        "notes": notes,
        "propertyAddress": address,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{_LOFTY_API_BASE}/api/v1/leads",
                json=payload,
                headers={
                    "Authorization": f"Bearer {LOFTY_API_KEY}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            resp.raise_for_status()
            print(f"[Lofty-Direct] Pushed {name} ({lead_type} / {score})")
            return resp.json()
    except httpx.HTTPStatusError as e:
        print(f"[Lofty-Direct] HTTP {e.response.status_code} for {name}: {e.response.text[:200]}")
        return None
    except Exception as e:
        print(f"[Lofty-Direct] Error for {name}: {e}")
        return None
