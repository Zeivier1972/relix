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
