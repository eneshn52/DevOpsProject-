import sqlite3
from datetime import datetime

DB_FILE = "master.db"

def conn():
    c = sqlite3.connect(DB_FILE)
    c.row_factory = sqlite3.Row
    return c

def init():
    with conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                requirements TEXT NOT NULL
            )
        """)

def create_project(name, requirements):
    with conn() as c:
        cur = c.execute(
            "INSERT INTO projects (name, created_at, status, requirements) VALUES (?, ?, ?, ?)",
            (name, datetime.now().isoformat(timespec="seconds"), "submitted", requirements),
        )
        return cur.lastrowid

def list_projects():
    with conn() as c:
        rows = c.execute("SELECT id, name, created_at, status FROM projects ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]

def get_project(project_id):
    with conn() as c:
        row = c.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return dict(row) if row else None

