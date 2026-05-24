from dotenv import load_dotenv
load_dotenv()

import asyncio
import httpx
import os

LOFTY_API_KEY = os.getenv("LOFTY_API_KEY")
BASE = "https://api.lofty.com/v1"

# Lofty uses X-API-Key or a custom header — try all common variants
AUTH_FORMATS = [
    ("Authorization: Bearer",  {"Authorization": f"Bearer {LOFTY_API_KEY}"}),
    ("Authorization: Token",   {"Authorization": f"Token {LOFTY_API_KEY}"}),
    ("Authorization: JWT",     {"Authorization": f"JWT {LOFTY_API_KEY}"}),
    ("X-API-Key header",       {"X-API-Key": LOFTY_API_KEY}),
    ("X-Auth-Token header",    {"X-Auth-Token": LOFTY_API_KEY}),
    ("api-key header",         {"api-key": LOFTY_API_KEY}),
    ("token query param",      {}),  # handled separately below
]

PROBE_URLS = [
    f"{BASE}/leads",
    f"{BASE}/contacts",
    f"{BASE}/me",
    f"{BASE}/users/me",
    f"{BASE}/agent",
]

async def main():
    async with httpx.AsyncClient(timeout=10.0) as client:
        # Try each auth format against the /leads endpoint
        print("=== AUTH FORMAT PROBE ===")
        for label, headers in AUTH_FORMATS:
            for url in PROBE_URLS:
                params = {}
                if label == "token query param":
                    params = {"token": LOFTY_API_KEY, "api_key": LOFTY_API_KEY}
                try:
                    r = await client.get(url, headers=headers, params=params)
                    if r.status_code != 401:
                        print(f"  [{r.status_code}] {label} -> {url}")
                        print(f"  Response: {r.text[:400]}")
                        print()
                    else:
                        print(f"  [401] {label} -> {url}")
                except Exception as e:
                    print(f"  [ERR] {label} -> {url}: {e}")

asyncio.run(main())
