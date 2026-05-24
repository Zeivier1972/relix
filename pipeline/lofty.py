import httpx
import json
import os
from typing import Dict, Optional, Any
from dotenv import load_dotenv

load_dotenv()

LOFTY_API_KEY = os.getenv("LOFTY_API_KEY")
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
