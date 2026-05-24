from dotenv import load_dotenv
load_dotenv()

import asyncio
import httpx
import os

LOFTY_API_KEY = os.getenv("LOFTY_API_KEY")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_SMS_FROM = os.getenv("TWILIO_SMS_FROM", "+17866611717")
YOUR_PHONE_NUMBER = os.getenv("YOUR_PHONE_NUMBER", "").replace("whatsapp:", "")


async def test_lofty():
    print("\n" + "="*50)
    print("LOFTY CRM TEST")
    print("="*50)
    print(f"URL: https://crm.lofty.com/api/v1/leads")

    headers = {"Authorization": f"Bearer {LOFTY_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "firstName": "RELIX",
        "lastName": "Test",
        "email": "test@relix.ai",
        "source": "RELIX_TEST",
        "notes": "Pipeline connection test",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Test create lead
        r = await client.post("https://crm.lofty.com/api/v1/leads", json=payload, headers=headers)
        print(f"\n  [POST /leads] Status: {r.status_code}")
        print(f"  Response: {r.text[:500]}")
        if r.status_code < 400:
            print("  >>> SUCCESS <<<")
        else:
            print("  >>> FAILED — refresh LOFTY_API_KEY in .env from your Lofty account settings <<<")


async def test_twilio():
    print("\n" + "="*50)
    print("TWILIO SMS TEST")
    print("="*50)
    print(f"From: {TWILIO_SMS_FROM}")
    print(f"To:   {YOUR_PHONE_NUMBER}")

    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    async with httpx.AsyncClient(auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN), timeout=15.0) as client:
        r = await client.post(url, data={
            "From": TWILIO_SMS_FROM,
            "To": YOUR_PHONE_NUMBER,
            "Body": "RELIX pipeline test — SMS connection verified.",
        })
        print(f"\n  Status: {r.status_code}")
        print(f"  Response: {r.text[:600]}")
        if r.status_code in (200, 201):
            print(f"\n  >>> SUCCESS <<< SID: {r.json().get('sid')}")
        else:
            print("\n  >>> FAILED <<<")


async def main():
    await test_lofty()
    await test_twilio()
    print("\n" + "="*50)
    print("TEST COMPLETE")
    print("="*50)

asyncio.run(main())
