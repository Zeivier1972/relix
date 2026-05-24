import sqlite3, json

conn = sqlite3.connect("leads.db")
conn.row_factory = sqlite3.Row
c = conn.cursor()

c.execute("SELECT name FROM sqlite_master WHERE type='table'")
print("Tables:", [r[0] for r in c.fetchall()])

c.execute("PRAGMA table_info(leads)")
print("Leads columns:", [r[1] for r in c.fetchall()])

c.execute("SELECT COUNT(*) FROM leads")
print("Total leads:", c.fetchone()[0])

conn.close()
