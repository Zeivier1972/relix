import httpx
import os
from typing import Dict, Optional, Any
from dotenv import load_dotenv

load_dotenv()

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_SMS_FROM = os.getenv("TWILIO_SMS_FROM", "+17866611717")

_DM_ES = (
    "Hola {name}! Vi que estas buscando casa en Miami o Florida. "
    "Soy agente inmobiliario especializado en ayudar a colombianos a comprar "
    "su primera propiedad en USA. Te puedo explicar el proceso, requisitos y "
    "opciones de financiamiento sin costo. Te interesa?"
)
_DM_EN = (
    "Hey {name}! I noticed you're looking to buy a home in Miami or Florida. "
    "I'm a real estate agent who specializes in helping Colombian buyers navigate "
    "the US market. Happy to walk you through the process and financing options "
    "at no cost. Interested?"
)


def _build_dm(lead_data: Dict) -> str:
    name = (lead_data.get("name") or "").split()[0] or "there"
    source = lead_data.get("source", "")
    template = _DM_EN if source == "reddit" else _DM_ES
    return template.format(name=name)


class TwilioWhatsAppAlerts:
    """Send the pre-written first DM directly to the lead's phone number."""

    def __init__(self, account_sid: str = TWILIO_ACCOUNT_SID,
                 auth_token: str = TWILIO_AUTH_TOKEN):
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.client = httpx.AsyncClient(
            auth=(account_sid, auth_token),
            timeout=30.0,
        )

    async def send_hot_lead_alert(self, lead_data: Dict[str, Any],
                                  whatsapp_from: str = None,
                                  whatsapp_to: str = None) -> bool:
        """
        Send the pre-written DM to the lead's own phone number.
        Skips entirely if the lead has no phone number.
        """
        lead_phone = (lead_data.get("phone") or "").strip().replace("whatsapp:", "")
        if not lead_phone:
            print(f"[SMS] No phone for {lead_data.get('name')} — skipping")
            return False

        message_body = _build_dm(lead_data)
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"

        try:
            response = await self.client.post(url, data={
                "From": TWILIO_SMS_FROM,
                "To": lead_phone,
                "Body": message_body,
            })
            response.raise_for_status()
            result = response.json()
            print(f"[SMS] DM sent to lead {lead_data.get('name')} ({lead_phone}): {result.get('sid')}")
            return True
        except httpx.HTTPError as e:
            print(f"[SMS] Twilio error for {lead_data.get('name')}: {e}")
            return False

    async def send_bulk_alerts(self, leads: list,
                               whatsapp_from: str = None,
                               whatsapp_to: str = None) -> list:
        results = []
        for lead in leads:
            success = await self.send_hot_lead_alert(lead)
            results.append({"lead_id": lead.get("id"), "success": success})
        return results

    async def close(self):
        await self.client.aclose()
