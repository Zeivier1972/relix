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


# ── Property lead SMS outreach ────────────────────────────────────────────────

_SMS_DAILY_LIMIT = 50
_CATHERINE_PHONE = "(305) 283-0872"

_FSBO_EN = (
    "Hi {name}! I'm Catherine Gomez, real estate agent in Miami. I'd love to help "
    "you sell your property at {address}. I have qualified buyers ready. "
    f"Call or text me at {_CATHERINE_PHONE}. Reply STOP to opt out."
)
_FSBO_ES = (
    "Hola {name}! Soy Catherine Gomez, agente de bienes raíces en Miami. Me gustaría "
    "ayudarte a vender tu propiedad en {address}. Tengo compradores listos. "
    f"Llámame al {_CATHERINE_PHONE}. Responde STOP para cancelar."
)
_FORECLOSURE_EN = (
    "Hi {name}, I'm Catherine Gomez, real estate agent in Miami. I help homeowners "
    "sell quickly before foreclosure to protect their credit. "
    f"Call me at {_CATHERINE_PHONE}. Reply STOP to opt out."
)
_FORECLOSURE_ES = (
    "Hola {name}, soy Catherine Gomez, agente en Miami. Ayudo a propietarios a vender "
    "rápido antes del foreclosure para proteger su crédito. "
    f"Llámame al {_CATHERINE_PHONE}. Responde STOP para cancelar."
)
_INVESTOR_EN = (
    "Hi {name}! I'm Catherine Gomez, real estate agent in Miami specializing in "
    "pre-construction investments. I have exclusive opportunities with strong ROI. "
    f"Call me at {_CATHERINE_PHONE}. Reply STOP to opt out."
)
_INVESTOR_ES = (
    "Hola {name}! Soy Catherine Gomez, especialista en inversiones de preconstrucción "
    "en Miami. Tengo oportunidades exclusivas con excelente ROI. "
    f"Llámame al {_CATHERINE_PHONE}. Responde STOP para cancelar."
)

_INVESTOR_TYPES = {"LLC_PURCHASE", "CASH_BUYER", "NEW_CONSTRUCTION"}
_FORECLOSURE_TYPES = {"PRE_FORECLOSURE"}


def _build_property_sms(lead: dict, language: str = "spanish") -> str:
    name = (lead.get("owner_name") or lead.get("name") or "there").split()[0]
    address = lead.get("address", "your property")
    lead_type = lead.get("lead_type", "")
    es = (language == "spanish")

    if lead_type in _FORECLOSURE_TYPES:
        tpl = _FORECLOSURE_ES if es else _FORECLOSURE_EN
    elif lead_type in _INVESTOR_TYPES:
        tpl = _INVESTOR_ES if es else _INVESTOR_EN
    else:
        tpl = _FSBO_ES if es else _FSBO_EN

    return tpl.format(name=name, address=address)


async def send_property_sms(lead: dict, db=None, language: str = "spanish") -> bool:
    """
    Send an SMS to a HOT property lead. Respects:
    - 9am–7pm EST window
    - 50/day limit
    - 30-day per-phone cooldown (checked via db.check_sms_cooldown)
    Always includes STOP opt-out per CAN-SPAM/TCPA.
    """
    from datetime import datetime
    import pytz

    phone = (lead.get("phone") or "").strip()
    if not phone:
        return False

    # Time window check (EST)
    est = pytz.timezone("America/New_York")
    now_est = datetime.now(est)
    if not (9 <= now_est.hour < 19):
        print(f"[SMS-Property] Outside hours ({now_est.hour}:00 EST) — skipping")
        return False

    # Cooldown check
    if db and db.check_sms_cooldown(phone, days=30):
        print(f"[SMS-Property] {phone} texted within 30 days — skipping")
        return False

    # Daily limit check
    if db and db.count_sms_today() >= _SMS_DAILY_LIMIT:
        print(f"[SMS-Property] Daily SMS limit ({_SMS_DAILY_LIMIT}) reached")
        return False

    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        print("[SMS-Property] Twilio credentials not set — skipping")
        return False

    message = _build_property_sms(lead, language)
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"

    try:
        async with httpx.AsyncClient(auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN), timeout=30.0) as client:
            resp = await client.post(url, data={
                "From": TWILIO_SMS_FROM,
                "To": phone,
                "Body": message,
            })
            resp.raise_for_status()
            sid = resp.json().get("sid")
            print(f"[SMS-Property] Sent to {phone} ({lead.get('lead_type')}): {sid}")
            if db:
                db.log_sms_sent(phone, lead.get("lead_type", ""), lead.get("source", ""), message)
                if lead.get("id"):
                    db.mark_property_sms_sent(lead["id"])
            return True
    except Exception as e:
        print(f"[SMS-Property] Error for {phone}: {e}")
        return False
