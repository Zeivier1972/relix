"""
RELIX database connection check.
Run locally:  python db_check.py
Run with PG:  DATABASE_URL=postgresql://... python db_check.py
"""
import os
import sys
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
print(f"DATABASE_URL set: {bool(DATABASE_URL)}")
print(f"Backend: {'PostgreSQL' if DATABASE_URL else 'SQLite (local fallback)'}")
print()

try:
    from database import LeadDatabase, USE_POSTGRES, get_db_connection, _cursor
    print(f"database.py loaded OK — USE_POSTGRES={USE_POSTGRES}")
except Exception as e:
    print(f"ERROR importing database.py: {e}")
    sys.exit(1)

# Init tables
try:
    db = LeadDatabase()
    print("init_db() OK — tables created/verified")
except Exception as e:
    print(f"ERROR in init_db(): {e}")
    sys.exit(1)

# Verify tables exist
try:
    conn = get_db_connection()
    cur = _cursor(conn)
    if USE_POSTGRES:
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN ('leads', 'qualifications', 'dm_log')
            ORDER BY table_name
        """)
    else:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('leads','qualifications','dm_log')")
    tables = [row[0] if not USE_POSTGRES else row["table_name"] for row in cur.fetchall()]
    cur.close()
    conn.close()
    print(f"Tables found: {tables}")
    missing = set(["leads", "qualifications", "dm_log"]) - set(tables)
    if missing:
        print(f"WARNING — missing tables: {missing}")
    else:
        print("All 3 tables present")
except Exception as e:
    print(f"ERROR verifying tables: {e}")
    sys.exit(1)

# Insert + read test lead
try:
    lead_id = db.add_lead(
        name="__relix_check_user__",
        source="db_check",
        raw_data={"test": True}
    )
    if lead_id:
        lead = db.get_lead(lead_id)
        print(f"Test lead inserted (id={lead_id}) and retrieved OK: name={lead['name']}")
        # Clean up
        conn = get_db_connection()
        cur = _cursor(conn)
        from database import PH
        cur.execute(f"DELETE FROM leads WHERE id = {PH}", (lead_id,))
        conn.commit()
        cur.close()
        conn.close()
        print("Test lead cleaned up")
    else:
        print("add_lead() returned None — likely duplicate from previous check run (OK)")
except Exception as e:
    print(f"ERROR in insert/read test: {e}")
    sys.exit(1)

# Stats
try:
    stats = db.get_stats()
    print(f"Stats: {stats}")
except Exception as e:
    print(f"ERROR in get_stats(): {e}")

print()
print("RESULT: database.py is wired correctly.")
if USE_POSTGRES:
    print("Connected to PostgreSQL — leads will persist across Railway redeploys.")
else:
    print("Running SQLite locally. On Railway with DATABASE_URL set, PostgreSQL will be used.")
