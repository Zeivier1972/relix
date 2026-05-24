from dotenv import load_dotenv
load_dotenv()

import asyncio
import sqlite3
import json
import os
from datetime import datetime
from pathlib import Path

DB_PATH = os.getenv("DB_PATH", "./leads.db")

# ---------------------------------------------------------------------------
# Step 1 — Insert a test lead (optionsscanner.io) if not already present
# ---------------------------------------------------------------------------

def insert_test_lead() -> int:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Remove any previous test entry so we get a clean run
    conn.execute(
        "DELETE FROM qualifications WHERE lead_id IN "
        "(SELECT id FROM leads WHERE name = 'optionsscanner.io' AND source = 'instagram_hashtags')"
    )
    conn.execute(
        "DELETE FROM leads WHERE name = 'optionsscanner.io' AND source = 'instagram_hashtags'"
    )
    conn.commit()

    raw_data = json.dumps({
        "caption": "Hola, busco casa en Miami. Cuanto necesito para el down payment? Soy colombiano.",
        "ownerUsername": "optionsscanner.io",
        "url": "https://www.instagram.com/optionsscanner.io/",
    })

    cur = conn.execute(
        """INSERT INTO leads (name, email, phone, property_url, source, raw_data, lead_status, created_at, updated_at)
           VALUES (?, '', '', ?, ?, ?, 'new', ?, ?)""",
        (
            "optionsscanner.io",
            "https://www.instagram.com/optionsscanner.io/",
            "instagram_hashtags",
            raw_data,
            datetime.now().isoformat(),
            datetime.now().isoformat(),
        ),
    )
    lead_id = cur.lastrowid

    conn.execute(
        """INSERT INTO qualifications (lead_id, score, reasoning, ai_analysis, created_at)
           VALUES (?, 'HOT', ?, ?, ?)""",
        (
            lead_id,
            "Colombian buyer asking about down payment for Miami property — clear purchase intent.",
            json.dumps({"test": True, "trigger": "manual_pipeline_test"}),
            datetime.now().isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    print(f"[Test] Inserted HOT lead: optionsscanner.io (id={lead_id})")
    return lead_id


# ---------------------------------------------------------------------------
# Step 2 — Push to Lofty via Zapier
# ---------------------------------------------------------------------------

async def push_to_lofty(lead_id: int):
    from pipeline.lofty import LoftyCRMClient

    lead_data = {
        "id": lead_id,
        "name": "optionsscanner.io",
        "email": "",
        "phone": "",
        "source": "instagram_hashtags",
        "property_url": "https://www.instagram.com/optionsscanner.io/",
        "qualification_score": "HOT",
        "qualification_reasoning": "Colombian buyer asking about down payment for Miami property — clear purchase intent.",
        "raw_data": {
            "caption": "Hola, busco casa en Miami. Cuanto necesito para el down payment? Soy colombiano.",
        },
    }

    lofty = LoftyCRMClient()
    result = await lofty.create_lead(lead_data)
    await lofty.close()

    if result:
        print(f"[Test] Lofty/Zapier: OK — {result}")
    else:
        print("[Test] Lofty/Zapier: skipped (check ZAPIER_LOFTY_WEBHOOK in .env)")


# ---------------------------------------------------------------------------
# Step 3 — Send Instagram DM via phantom
# ---------------------------------------------------------------------------

async def send_phantom_dm(lead_id: int):
    from pipeline.phantom import InstagramDMBot, _build_message, _already_dmed, _log_dm, _is_within_hours, _init_dm_table
    from playwright.async_api import async_playwright

    _init_dm_table()

    if not _is_within_hours():
        print("[Test] Outside DM hours — forcing DM anyway for test purposes")

    if _already_dmed("optionsscanner.io"):
        print("[Test] Already DMed optionsscanner.io today — clearing log entry for test")
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "DELETE FROM dm_log WHERE instagram_username = 'optionsscanner.io'"
        )
        conn.commit()
        conn.close()

    message = _build_message("optionsscanner.io", "instagram_hashtags")
    print(f"[Test] Message that will be sent:\n  {message}\n")

    bot = InstagramDMBot()
    async with async_playwright() as p:
        await bot.setup(p)
        logged_in = await bot.login()
        if not logged_in:
            print("[Test] Login failed — check instagram_session.json")
            await bot.teardown()
            return

        success = await bot.send_dm(
            ig_username="optionsscanner.io",
            message=message,
            lead_id=lead_id,
            source="instagram_hashtags",
        )
        await bot.teardown()

    if success:
        print("[Test] Instagram DM: SENT")
    else:
        print("[Test] Instagram DM: FAILED (see screenshot if saved)")


# ---------------------------------------------------------------------------
# Run all 3 steps
# ---------------------------------------------------------------------------

async def main():
    print("=" * 60)
    print("RELIX Full Pipeline Test — optionsscanner.io")
    print("=" * 60)

    print("\n--- Step 1: Insert HOT lead into DB ---")
    lead_id = insert_test_lead()

    print("\n--- Step 2: Push to Lofty via Zapier ---")
    await push_to_lofty(lead_id)

    print("\n--- Step 3: Send Instagram DM ---")
    await send_phantom_dm(lead_id)

    print("\n" + "=" * 60)
    print("Pipeline test complete.")
    print("=" * 60)


asyncio.run(main())
