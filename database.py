import sqlite3
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
import os
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "./leads.db")


class LeadDatabase:
    """SQLite database for lead storage with duplicate detection."""
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        """Initialize database with leads table."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS qualifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id INTEGER NOT NULL,
                score TEXT,
                reasoning TEXT,
                ai_analysis TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (lead_id) REFERENCES leads(id)
            )
        """)
        
        conn.commit()
        conn.close()
    
    def check_duplicate(self, email: Optional[str] = None, phone: Optional[str] = None,
                       property_address: Optional[str] = None) -> bool:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        if email:
            cursor.execute("SELECT id FROM leads WHERE email = ?", (email,))
            if cursor.fetchone():
                conn.close()
                return True

        if phone:
            cursor.execute("SELECT id FROM leads WHERE phone = ?", (phone,))
            if cursor.fetchone():
                conn.close()
                return True

        if property_address:
            cursor.execute("SELECT id FROM leads WHERE property_address = ?", (property_address,))
            if cursor.fetchone():
                conn.close()
                return True

        conn.close()
        return False

    def is_recent_username(self, name: str, days: int = 7) -> bool:
        """Return True if this username was already saved within the last N days."""
        if not name:
            return False
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM leads WHERE name = ? AND created_at >= datetime('now', ?)",
            (name, f"-{days} days"),
        )
        found = cursor.fetchone() is not None
        conn.close()
        return found

    def get_lead_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Look up the most recent lead whose name matches the given username."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM leads WHERE name = ? ORDER BY created_at DESC LIMIT 1",
            (username,),
        )
        row = cursor.fetchone()
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
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        raw_data_json = json.dumps(raw_data) if raw_data else None
        
        try:
            cursor.execute("""
                INSERT INTO leads (name, email, phone, property_url, property_address, source, raw_data)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (name, email, phone, property_url, property_address, source, raw_data_json))
            
            lead_id = cursor.lastrowid
            conn.commit()
            conn.close()
            return lead_id
        except sqlite3.IntegrityError:
            conn.close()
            return None
    
    def get_lead(self, lead_id: int) -> Optional[Dict[str, Any]]:
        """Retrieve a lead by ID."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM leads WHERE id = ?", (lead_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return dict(row)
        return None
    
    def get_new_leads(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get unqualified leads."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM leads 
            WHERE lead_status = 'NEW' 
            ORDER BY created_at DESC 
            LIMIT ?
        """, (limit,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    def update_lead_status(self, lead_id: int, status: str):
        """Update lead status."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE leads 
            SET lead_status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (status, lead_id))
        
        conn.commit()
        conn.close()
    
    def add_qualification(self, lead_id: int, score: str, reasoning: str, ai_analysis: Optional[str] = None):
        """Add qualification result for a lead."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO qualifications (lead_id, score, reasoning, ai_analysis)
            VALUES (?, ?, ?, ?)
        """, (lead_id, score, reasoning, ai_analysis))
        
        conn.commit()
        conn.close()
        
        # Update lead status to QUALIFIED
        self.update_lead_status(lead_id, "QUALIFIED")
    
    def get_hot_leads(self) -> List[Dict[str, Any]]:
        """Get all HOT qualified leads."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT l.* FROM leads l
            JOIN qualifications q ON l.id = q.lead_id
            WHERE q.score = 'HOT'
            ORDER BY l.created_at DESC
        """)
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    def get_all_leads(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get all leads."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM leads 
            ORDER BY created_at DESC 
            LIMIT ?
        """, (limit,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
