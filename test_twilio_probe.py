from dotenv import load_dotenv
load_dotenv()

import asyncio
import httpx
import os

SID = os.getenv("TWILIO_ACCOUNT_SID")
TOK = os.getenv("TWILIO_AUTH_TOKEN")

async def main():
    async with httpx.AsyncClient(auth=(SID, TOK), timeout=10.0) as c:
        # Account info
        r = await c.get(f"https://api.twilio.com/2010-04-01/Accounts/{SID}.json")
        print(f"Account status code: {r.status_code}")
        if r.status_code == 200:
            d = r.json()
            print(f"  Name:   {d.get('friendly_name')}")
            print(f"  Status: {d.get('status')}")

        # Phone numbers on account
        r2 = await c.get(f"https://api.twilio.com/2010-04-01/Accounts/{SID}/IncomingPhoneNumbers.json")
        print(f"\nPhone numbers status: {r2.status_code}")
        if r2.status_code == 200:
            nums = r2.json().get("incoming_phone_numbers", [])
            print(f"  Numbers provisioned: {len(nums)}")
            for n in nums:
                print(f"    {n.get('phone_number')} caps={n.get('capabilities')}")

        # Check sandbox config via messaging services
        r3 = await c.get("https://messaging.twilio.com/v1/Services")
        print(f"\nMessaging Services status: {r3.status_code}")
        if r3.status_code == 200:
            svcs = r3.json().get("services", [])
            print(f"  Services: {len(svcs)}")
            for s in svcs:
                print(f"    {s.get('sid')} - {s.get('friendly_name')}")

        # Try sending via content template (sandbox workaround)
        print("\nRetrying message with no ContentSid...")
        r4 = await c.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{SID}/Messages.json",
            data={
                "From": os.getenv("TWILIO_WHATSAPP_FROM"),
                "To": os.getenv("YOUR_WHATSAPP_NUMBER"),
                "Body": "RELIX test ping",
            }
        )
        print(f"  Status: {r4.status_code}")
        print(f"  Response: {r4.text[:600]}")

asyncio.run(main())
