"""Push all HOT+WARM leads that were skipped due to the raw_data bug."""
from dotenv import load_dotenv
load_dotenv()

import asyncio, sqlite3, json, os

DB_PATH = os.getenv("DB_PATH", "./leads.db")

def get_qualified_leads(scores=("HOT", "WARM")):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT l.*, q.score, q.reasoning
        FROM leads l
        JOIN qualifications q ON l.id = q.lead_id
        WHERE q.score IN ({})
        ORDER BY q.score DESC, l.created_at DESC
    """.format(",".join("?" * len(scores))), scores).fetchall()
    conn.close()
    return [dict(r) for r in rows]

async def main():
    from pipeline.lofty import LoftyCRMClient
    leads = get_qualified_leads()
    print(f"Pushing {len(leads)} HOT/WARM leads to Zapier/Lofty...")

    lofty = LoftyCRMClient()
    sent = skipped = errors = 0

    for lead in leads:
        lead["qualification_score"] = lead.pop("score", "")
        lead["qualification_reasoning"] = lead.pop("reasoning", "")
        try:
            result = await lofty.create_lead(lead)
            if result:
                print(f"  [OK] {lead['name']} ({lead['qualification_score']})")
                sent += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"  [ERR] {lead['name']}: {e}")
            errors += 1

    await lofty.close()
    print(f"\nDone — sent={sent}  skipped={skipped}  errors={errors}")

asyncio.run(main())
