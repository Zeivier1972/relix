import os
import json
from datetime import datetime, timedelta, date
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
DB_PATH = os.getenv("DB_PATH", "./leads.db")
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras
    PH = "%s"
    _IntegrityError = psycopg2.IntegrityError
    _PK = "SERIAL PRIMARY KEY"
else:
    import sqlite3
    PH = "?"
    _IntegrityError = sqlite3.IntegrityError
    _PK = "INTEGER PRIMARY KEY AUTOINCREMENT"


def get_db_connection():
    """Return a database connection for the active backend."""
    if USE_POSTGRES:
        return psycopg2.connect(DATABASE_URL)
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn


def _cursor(conn):
    if USE_POSTGRES:
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        return conn.cursor()


class LeadDatabase:
    """Database for lead storage with duplicate detection. Supports PostgreSQL and SQLite."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        """Initialize database tables."""
        conn = get_db_connection()
        cur = _cursor(conn)

        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS leads (
                id {_PK},
                name TEXT NOT NULL,
                email TEXT,
                phone TEXT,
                property_url TEXT,
                property_address TEXT,
                source TEXT,
                raw_data TEXT,
                qualification_score TEXT,
                lead_status TEXT DEFAULT 'NEW',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(email, phone, property_address)
            )
        """)

        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS qualifications (
                id {_PK},
                lead_id INTEGER NOT NULL,
                score TEXT,
                reasoning TEXT,
                ai_analysis TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (lead_id) REFERENCES leads(id)
            )
        """)

        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS dm_log (
                id {_PK},
                lead_id INTEGER,
                instagram_username TEXT NOT NULL,
                source TEXT,
                message_preview TEXT,
                status TEXT DEFAULT 'sent',
                error_message TEXT,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS reddit_replies (
                id {_PK},
                lead_id INTEGER NOT NULL UNIQUE,
                reply_text TEXT,
                marked_replied INTEGER DEFAULT 0,
                replied_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS dm_queue (
                id {_PK},
                lead_id INTEGER,
                ig_username TEXT NOT NULL,
                source TEXT,
                score TEXT,
                priority INTEGER DEFAULT 2,
                scheduled_for TIMESTAMP NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS property_leads (
                id {_PK},
                address TEXT NOT NULL,
                city TEXT,
                zip_code TEXT,
                owner_name TEXT,
                listing_price REAL DEFAULT 0,
                days_on_market INTEGER DEFAULT 0,
                price_reduction_pct REAL DEFAULT 0,
                price_reduction_amt REAL DEFAULT 0,
                phone TEXT,
                email TEXT,
                listing_url TEXT,
                lead_type TEXT,
                score TEXT,
                source TEXT,
                raw_data TEXT,
                lofty_pushed INTEGER DEFAULT 0,
                sms_sent INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(listing_url)
            )
        """)

        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS sms_log (
                id {_PK},
                phone TEXT NOT NULL,
                lead_type TEXT,
                lead_source TEXT,
                message TEXT,
                status TEXT DEFAULT 'sent',
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
        cur.close()
        conn.close()

    def check_duplicate(self, email: Optional[str] = None, phone: Optional[str] = None,
                        property_address: Optional[str] = None) -> bool:
        conn = get_db_connection()
        cur = _cursor(conn)

        if email:
            cur.execute(f"SELECT id FROM leads WHERE email = {PH}", (email,))
            if cur.fetchone():
                cur.close(); conn.close()
                return True

        if phone:
            cur.execute(f"SELECT id FROM leads WHERE phone = {PH}", (phone,))
            if cur.fetchone():
                cur.close(); conn.close()
                return True

        if property_address:
            cur.execute(f"SELECT id FROM leads WHERE property_address = {PH}", (property_address,))
            if cur.fetchone():
                cur.close(); conn.close()
                return True

        cur.close()
        conn.close()
        return False

    def is_recent_username(self, name: str, days: int = 7) -> bool:
        """Return True if this username was already saved within the last N days."""
        if not name:
            return False
        cutoff = datetime.now() - timedelta(days=days)
        conn = get_db_connection()
        cur = _cursor(conn)
        cur.execute(
            f"SELECT id FROM leads WHERE name = {PH} AND created_at >= {PH}",
            (name, cutoff),
        )
        found = cur.fetchone() is not None
        cur.close()
        conn.close()
        return found

    def get_lead_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Look up the most recent lead whose name matches the given username."""
        conn = get_db_connection()
        cur = _cursor(conn)
        cur.execute(
            f"SELECT * FROM leads WHERE name = {PH} ORDER BY created_at DESC LIMIT 1",
            (username,),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        return dict(row) if row else None

    def add_lead(self, name: str, email: Optional[str] = None, phone: Optional[str] = None,
                 property_url: Optional[str] = None, property_address: Optional[str] = None,
                 source: str = "unknown", raw_data: Optional[Dict] = None) -> Optional[int]:
        """Add a new lead to database, skipping duplicates and recent usernames."""
        if self.is_recent_username(name):
            return None
        if self.check_duplicate(email, phone, property_address):
            return None

        raw_data_json = json.dumps(raw_data) if raw_data else None
        conn = get_db_connection()
        cur = _cursor(conn)

        try:
            if USE_POSTGRES:
                cur.execute(f"""
                    INSERT INTO leads (name, email, phone, property_url, property_address, source, raw_data)
                    VALUES ({PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH})
                    RETURNING id
                """, (name, email, phone, property_url, property_address, source, raw_data_json))
                row = cur.fetchone()
                lead_id = row["id"]
            else:
                cur.execute(f"""
                    INSERT INTO leads (name, email, phone, property_url, property_address, source, raw_data)
                    VALUES ({PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH})
                """, (name, email, phone, property_url, property_address, source, raw_data_json))
                lead_id = cur.lastrowid

            conn.commit()
            cur.close()
            conn.close()
            return lead_id
        except _IntegrityError:
            cur.close()
            conn.close()
            return None

    def get_lead(self, lead_id: int) -> Optional[Dict[str, Any]]:
        """Retrieve a lead by ID."""
        conn = get_db_connection()
        cur = _cursor(conn)
        cur.execute(f"SELECT * FROM leads WHERE id = {PH}", (lead_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return dict(row) if row else None

    def get_new_leads(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get unqualified leads."""
        conn = get_db_connection()
        cur = _cursor(conn)
        cur.execute(f"""
            SELECT * FROM leads
            WHERE lead_status = 'NEW'
            ORDER BY created_at DESC
            LIMIT {PH}
        """, (limit,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(r) for r in rows]

    def update_lead_status(self, lead_id: int, status: str):
        """Update lead status."""
        conn = get_db_connection()
        cur = _cursor(conn)
        cur.execute(f"""
            UPDATE leads
            SET lead_status = {PH}, updated_at = CURRENT_TIMESTAMP
            WHERE id = {PH}
        """, (status, lead_id))
        conn.commit()
        cur.close()
        conn.close()

    def add_qualification(self, lead_id: int, score: str, reasoning: str, ai_analysis: Optional[str] = None):
        """Add qualification result for a lead."""
        conn = get_db_connection()
        cur = _cursor(conn)
        cur.execute(f"""
            INSERT INTO qualifications (lead_id, score, reasoning, ai_analysis)
            VALUES ({PH}, {PH}, {PH}, {PH})
        """, (lead_id, score, reasoning, ai_analysis))
        conn.commit()
        cur.close()
        conn.close()
        self.update_lead_status(lead_id, "QUALIFIED")

    def get_hot_leads(self) -> List[Dict[str, Any]]:
        """Get all HOT qualified leads."""
        conn = get_db_connection()
        cur = _cursor(conn)
        cur.execute("""
            SELECT l.* FROM leads l
            JOIN qualifications q ON l.id = q.lead_id
            WHERE q.score = 'HOT'
            ORDER BY l.created_at DESC
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(r) for r in rows]

    def get_all_leads(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get all leads."""
        conn = get_db_connection()
        cur = _cursor(conn)
        cur.execute(f"""
            SELECT * FROM leads
            ORDER BY created_at DESC
            LIMIT {PH}
        """, (limit,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(r) for r in rows]

    def get_leads_with_dm_status(self, limit: int = 500) -> List[Dict[str, Any]]:
        """HOT/WARM leads joined with their most recent DM status."""
        conn = get_db_connection()
        cur = _cursor(conn)
        cur.execute(f"""
            SELECT
                l.id, l.name, l.source, l.property_url, l.created_at,
                q.score,
                COALESCE(
                    (SELECT d.status FROM dm_log d
                     WHERE d.instagram_username = l.name
                     ORDER BY d.sent_at DESC LIMIT 1),
                    'not_sent'
                ) AS dm_status,
                (SELECT d.sent_at FROM dm_log d
                 WHERE d.instagram_username = l.name
                 ORDER BY d.sent_at DESC LIMIT 1) AS dm_sent_at
            FROM leads l
            JOIN qualifications q ON l.id = q.lead_id
            WHERE q.score IN ('HOT', 'WARM')
            ORDER BY q.score DESC, l.created_at DESC
            LIMIT {PH}
        """, (limit,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(r) for r in rows]

    def get_dm_stats(self) -> Dict[str, int]:
        conn = get_db_connection()
        cur = _cursor(conn)

        cur.execute("SELECT COUNT(DISTINCT instagram_username) AS cnt FROM dm_log WHERE status='sent'")
        sent = cur.fetchone()["cnt"]

        cur.execute("SELECT COUNT(DISTINCT instagram_username) AS cnt FROM dm_log WHERE status='replied'")
        replied = cur.fetchone()["cnt"]

        cur.execute(f"""
            SELECT COUNT(*) AS cnt FROM leads l
            JOIN qualifications q ON l.id = q.lead_id
            WHERE l.source IN ('instagram_hashtags','instagram_comments','instagram')
              AND q.score IN ('HOT','WARM')
              AND l.name NOT IN (
                  SELECT instagram_username FROM dm_log WHERE status='sent'
              )
        """)
        pending = cur.fetchone()["cnt"]

        cur.close()
        conn.close()
        return {"sent": sent, "pending": pending, "replied": replied}

    def get_stats(self) -> Dict[str, int]:
        conn = get_db_connection()
        cur = _cursor(conn)
        cur.execute("""
            SELECT
                COUNT(DISTINCT l.id)                                            AS total,
                COUNT(DISTINCT CASE WHEN q.score = 'HOT'  THEN l.id END)       AS hot,
                COUNT(DISTINCT CASE WHEN q.score = 'WARM' THEN l.id END)       AS warm,
                COUNT(DISTINCT CASE WHEN q.score = 'COLD' THEN l.id END)       AS cold
            FROM leads l
            LEFT JOIN qualifications q ON l.id = q.lead_id
        """)
        row = cur.fetchone()
        cur.close()
        conn.close()
        return {"total": row["total"], "hot": row["hot"], "warm": row["warm"], "cold": row["cold"]}

    def get_leads_with_scores(self, limit: int = 200) -> List[Dict[str, Any]]:
        conn = get_db_connection()
        cur = _cursor(conn)
        cur.execute(f"""
            SELECT l.*, q.score
            FROM leads l
            LEFT JOIN qualifications q ON l.id = q.lead_id
            ORDER BY l.created_at DESC
            LIMIT {PH}
        """, (limit,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(r) for r in rows]

    # ── Reddit replies ────────────────────────────────────────────────────────

    def get_hot_reddit_leads(self, limit: int = 100) -> List[Dict[str, Any]]:
        """HOT Reddit leads (discovered in last 48 h) with cached reply and replied status."""
        cutoff = datetime.now() - timedelta(hours=48)
        conn = get_db_connection()
        cur = _cursor(conn)
        cur.execute(f"""
            SELECT l.id, l.name, l.source, l.property_url, l.created_at, l.raw_data,
                   q.score, q.reasoning,
                   r.reply_text, r.marked_replied, r.replied_at
            FROM leads l
            JOIN qualifications q ON l.id = q.lead_id
            LEFT JOIN reddit_replies r ON l.id = r.lead_id
            WHERE l.source = 'reddit' AND q.score = 'HOT'
              AND l.created_at >= {PH}
            ORDER BY l.created_at DESC
            LIMIT {PH}
        """, (cutoff, limit))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        result = []
        for row in rows:
            d = dict(row)
            if d.get("raw_data") and isinstance(d["raw_data"], str):
                try:
                    d["raw_data"] = json.loads(d["raw_data"])
                except Exception:
                    d["raw_data"] = {}
            result.append(d)
        return result

    def save_reddit_reply(self, lead_id: int, reply_text: str):
        """Cache a generated Reddit reply for a lead."""
        conn = get_db_connection()
        cur = _cursor(conn)
        if USE_POSTGRES:
            cur.execute("""
                INSERT INTO reddit_replies (lead_id, reply_text)
                VALUES (%s, %s)
                ON CONFLICT (lead_id) DO UPDATE SET reply_text = EXCLUDED.reply_text
            """, (lead_id, reply_text))
        else:
            cur.execute("""
                INSERT INTO reddit_replies (lead_id, reply_text)
                VALUES (?, ?)
                ON CONFLICT (lead_id) DO UPDATE SET reply_text = excluded.reply_text
            """, (lead_id, reply_text))
        conn.commit()
        cur.close()
        conn.close()

    def get_reddit_reply(self, lead_id: int) -> Optional[Dict[str, Any]]:
        """Return the cached reply row for a lead, or None."""
        conn = get_db_connection()
        cur = _cursor(conn)
        cur.execute(f"SELECT * FROM reddit_replies WHERE lead_id = {PH}", (lead_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return dict(row) if row else None

    def mark_reddit_replied(self, lead_id: int, replied: bool):
        """Toggle the replied flag for a Reddit lead."""
        conn = get_db_connection()
        cur = _cursor(conn)
        ts = datetime.now() if replied else None
        if USE_POSTGRES:
            cur.execute("""
                INSERT INTO reddit_replies (lead_id, marked_replied, replied_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (lead_id) DO UPDATE
                    SET marked_replied = EXCLUDED.marked_replied,
                        replied_at     = EXCLUDED.replied_at
            """, (lead_id, int(replied), ts))
        else:
            cur.execute("""
                INSERT INTO reddit_replies (lead_id, marked_replied, replied_at)
                VALUES (?, ?, ?)
                ON CONFLICT (lead_id) DO UPDATE
                    SET marked_replied = excluded.marked_replied,
                        replied_at     = excluded.replied_at
            """, (lead_id, int(replied), ts))
        conn.commit()
        cur.close()
        conn.close()

    # ── DM Queue ──────────────────────────────────────────────────────────────

    def add_to_dm_queue(self, lead_id: int, ig_username: str, source: str, score: str) -> Optional[int]:
        """Add an Instagram lead to the auto-DM queue with smart scheduling."""
        import random
        conn = get_db_connection()
        cur = _cursor(conn)

        # Skip if already pending in queue
        cur.execute(f"SELECT id FROM dm_queue WHERE ig_username = {PH} AND status = 'pending'", (ig_username,))
        if cur.fetchone():
            cur.close(); conn.close(); return None

        # Skip if already DM'd
        cur.execute(f"SELECT id FROM dm_log WHERE instagram_username = {PH} AND status = 'sent'", (ig_username,))
        if cur.fetchone():
            cur.close(); conn.close(); return None

        priority = 1 if score == "HOT" else 2
        now = datetime.now()
        today = now.date()
        tomorrow = today + timedelta(days=1)
        today_9am = now.replace(hour=9, minute=0, second=0, microsecond=0)
        tomorrow_9am = datetime.combine(tomorrow, datetime.min.time()).replace(hour=9)

        # Count DMs committed today (sent + pending)
        day_start = datetime.combine(today, datetime.min.time())
        day_end = datetime.combine(tomorrow, datetime.min.time())
        cur.execute(
            f"SELECT COUNT(*) AS cnt FROM dm_log WHERE status='sent' AND sent_at >= {PH} AND sent_at < {PH}",
            (day_start, day_end),
        )
        sent_today = cur.fetchone()["cnt"]
        cur.execute(
            f"SELECT COUNT(*) AS cnt FROM dm_queue WHERE status='pending' AND scheduled_for >= {PH} AND scheduled_for < {PH}",
            (day_start, day_end),
        )
        queued_today = cur.fetchone()["cnt"]
        total_today = sent_today + queued_today

        # Find last scheduled time for same or higher priority items
        cur.execute(
            f"SELECT MAX(scheduled_for) AS last_sf FROM dm_queue WHERE status='pending' AND priority <= {PH}",
            (priority,),
        )
        row = cur.fetchone()
        last_sf = row["last_sf"] if row and row.get("last_sf") else None
        if last_sf and isinstance(last_sf, str):
            last_sf = datetime.fromisoformat(last_sf)

        # Scheduling logic
        if total_today >= 15:
            scheduled_for = tomorrow_9am + timedelta(minutes=random.randint(0, 20))
        elif now.hour >= 20:
            scheduled_for = tomorrow_9am + timedelta(minutes=random.randint(0, 20))
        elif now.hour < 9:
            scheduled_for = today_9am + timedelta(minutes=random.randint(0, 20))
        elif last_sf and last_sf > now:
            scheduled_for = last_sf + timedelta(seconds=random.randint(180, 420))
            if scheduled_for.hour >= 20:
                scheduled_for = tomorrow_9am + timedelta(minutes=random.randint(0, 20))
        else:
            delay = random.randint(5, 30) if score == "HOT" else random.randint(30, 60)
            scheduled_for = now + timedelta(minutes=delay)
            if scheduled_for.hour >= 20:
                scheduled_for = tomorrow_9am + timedelta(minutes=random.randint(0, 20))

        if USE_POSTGRES:
            cur.execute("""
                INSERT INTO dm_queue (lead_id, ig_username, source, score, priority, scheduled_for)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
            """, (lead_id, ig_username, source, score, priority, scheduled_for))
            queue_id = cur.fetchone()["id"]
        else:
            cur.execute("""
                INSERT INTO dm_queue (lead_id, ig_username, source, score, priority, scheduled_for)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (lead_id, ig_username, source, score, priority, scheduled_for))
            queue_id = cur.lastrowid

        conn.commit()
        cur.close()
        conn.close()
        print(f"[Queue] {score} @{ig_username} → scheduled {scheduled_for.strftime('%H:%M')} (queue_id={queue_id})")
        return queue_id

    def get_dm_queue_stats(self) -> Dict[str, Any]:
        """Pending count and next scheduled DM time."""
        now = datetime.now()
        conn = get_db_connection()
        cur = _cursor(conn)
        cur.execute("SELECT COUNT(*) AS cnt FROM dm_queue WHERE status='pending'")
        pending = cur.fetchone()["cnt"]
        cur.execute(
            f"SELECT MIN(scheduled_for) AS next_sf FROM dm_queue WHERE status='pending' AND scheduled_for >= {PH}",
            (now,),
        )
        row = cur.fetchone()
        next_sf = row["next_sf"] if row else None
        cur.close()
        conn.close()
        if next_sf and isinstance(next_sf, str):
            next_sf = datetime.fromisoformat(next_sf)
        return {
            "pending": pending,
            "next_scheduled": next_sf.isoformat() if next_sf else None,
        }

    def get_dm_queue(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Recent dm_queue rows for display."""
        conn = get_db_connection()
        cur = _cursor(conn)
        cur.execute(f"""
            SELECT * FROM dm_queue
            ORDER BY scheduled_for ASC
            LIMIT {PH}
        """, (limit,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(r) for r in rows]

    # ── Property Leads ────────────────────────────────────────────────────────

    def add_property_lead(self, lead: Dict[str, Any]) -> Optional[int]:
        """Insert a property lead, skip if listing_url already exists."""
        raw = lead.get("raw_data")
        raw_json = json.dumps(raw) if raw else None
        url = lead.get("listing_url") or ""
        conn = get_db_connection()
        cur = _cursor(conn)
        try:
            if USE_POSTGRES:
                cur.execute("""
                    INSERT INTO property_leads
                        (address, city, zip_code, owner_name, listing_price,
                         days_on_market, price_reduction_pct, price_reduction_amt,
                         phone, email, listing_url, lead_type, score, source, raw_data)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (listing_url) DO NOTHING
                    RETURNING id
                """, (
                    lead.get("address"), lead.get("city"), lead.get("zip_code"),
                    lead.get("owner_name"), lead.get("listing_price", 0),
                    lead.get("days_on_market", 0), lead.get("price_reduction_pct", 0),
                    lead.get("price_reduction_amt", 0), lead.get("phone"),
                    lead.get("email"), url, lead.get("lead_type"),
                    lead.get("score"), lead.get("source"), raw_json,
                ))
                row = cur.fetchone()
                lead_id = row["id"] if row else None
            else:
                cur.execute("""
                    INSERT OR IGNORE INTO property_leads
                        (address, city, zip_code, owner_name, listing_price,
                         days_on_market, price_reduction_pct, price_reduction_amt,
                         phone, email, listing_url, lead_type, score, source, raw_data)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    lead.get("address"), lead.get("city"), lead.get("zip_code"),
                    lead.get("owner_name"), lead.get("listing_price", 0),
                    lead.get("days_on_market", 0), lead.get("price_reduction_pct", 0),
                    lead.get("price_reduction_amt", 0), lead.get("phone"),
                    lead.get("email"), url, lead.get("lead_type"),
                    lead.get("score"), lead.get("source"), raw_json,
                ))
                lead_id = cur.lastrowid if cur.rowcount > 0 else None
            conn.commit()
        except _IntegrityError:
            lead_id = None
        finally:
            cur.close()
            conn.close()
        return lead_id

    def get_property_leads(self, score: Optional[str] = None,
                           lead_type: Optional[str] = None,
                           limit: int = 200) -> List[Dict[str, Any]]:
        conn = get_db_connection()
        cur = _cursor(conn)
        where = []
        params = []
        if score:
            where.append(f"score = {PH}")
            params.append(score)
        if lead_type:
            where.append(f"lead_type = {PH}")
            params.append(lead_type)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        params.append(limit)
        cur.execute(f"""
            SELECT * FROM property_leads
            {clause}
            ORDER BY score DESC, created_at DESC
            LIMIT {PH}
        """, params)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        result = []
        for row in rows:
            d = dict(row)
            if d.get("raw_data") and isinstance(d["raw_data"], str):
                try:
                    d["raw_data"] = json.loads(d["raw_data"])
                except Exception:
                    d["raw_data"] = {}
            result.append(d)
        return result

    def get_property_lead_stats(self) -> Dict[str, Any]:
        conn = get_db_connection()
        cur = _cursor(conn)
        cur.execute("""
            SELECT
                COUNT(*) AS total,
                COUNT(CASE WHEN score='HOT' THEN 1 END) AS hot,
                COUNT(CASE WHEN score='WARM' THEN 1 END) AS warm,
                COUNT(CASE WHEN lofty_pushed=1 THEN 1 END) AS in_lofty,
                COUNT(CASE WHEN sms_sent=1 THEN 1 END) AS sms_sent_count,
                COUNT(CASE WHEN phone IS NOT NULL AND phone != '' THEN 1 END) AS has_phone,
                COUNT(CASE WHEN lead_type='FSBO' THEN 1 END) AS fsbo,
                COUNT(CASE WHEN lead_type='PRE_FORECLOSURE' THEN 1 END) AS pre_foreclosure,
                COUNT(CASE WHEN lead_type='PRICE_DROP' THEN 1 END) AS price_drop,
                COUNT(CASE WHEN lead_type='NEW_CONSTRUCTION' THEN 1 END) AS new_construction,
                COUNT(CASE WHEN lead_type IN ('LLC_PURCHASE','CASH_BUYER') THEN 1 END) AS cash_buyers
            FROM property_leads
        """)
        row = cur.fetchone()
        cur.close()
        conn.close()
        return dict(row) if row else {}

    def mark_property_lofty_pushed(self, lead_id: int):
        conn = get_db_connection()
        cur = _cursor(conn)
        cur.execute(f"UPDATE property_leads SET lofty_pushed=1 WHERE id={PH}", (lead_id,))
        conn.commit()
        cur.close()
        conn.close()

    def mark_property_sms_sent(self, lead_id: int):
        conn = get_db_connection()
        cur = _cursor(conn)
        cur.execute(f"UPDATE property_leads SET sms_sent=1 WHERE id={PH}", (lead_id,))
        conn.commit()
        cur.close()
        conn.close()

    def get_property_lead(self, lead_id: int) -> Optional[Dict[str, Any]]:
        conn = get_db_connection()
        cur = _cursor(conn)
        cur.execute(f"SELECT * FROM property_leads WHERE id={PH}", (lead_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return None
        d = dict(row)
        if d.get("raw_data") and isinstance(d["raw_data"], str):
            try:
                d["raw_data"] = json.loads(d["raw_data"])
            except Exception:
                d["raw_data"] = {}
        return d

    # ── SMS Log ───────────────────────────────────────────────────────────────

    def check_sms_cooldown(self, phone: str, days: int = 30) -> bool:
        """Return True if this phone was already texted within the cooldown window."""
        if not phone:
            return True
        cutoff = datetime.now() - timedelta(days=days)
        conn = get_db_connection()
        cur = _cursor(conn)
        cur.execute(
            f"SELECT id FROM sms_log WHERE phone={PH} AND sent_at >= {PH} AND status='sent'",
            (phone, cutoff),
        )
        found = cur.fetchone() is not None
        cur.close()
        conn.close()
        return found

    def log_sms_sent(self, phone: str, lead_type: str, lead_source: str, message: str):
        conn = get_db_connection()
        cur = _cursor(conn)
        cur.execute(
            f"INSERT INTO sms_log (phone, lead_type, lead_source, message) VALUES ({PH},{PH},{PH},{PH})",
            (phone, lead_type, lead_source, message),
        )
        conn.commit()
        cur.close()
        conn.close()

    def count_sms_today(self) -> int:
        today = datetime.now().date()
        conn = get_db_connection()
        cur = _cursor(conn)
        cur.execute(
            f"SELECT COUNT(*) AS cnt FROM sms_log WHERE sent_at >= {PH} AND status='sent'",
            (today,),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row["cnt"] if row else 0
