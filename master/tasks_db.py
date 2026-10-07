import json
from db import conn


def init():
    with conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                inputs TEXT NOT NULL,
                expected_outputs TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
            )
        """)


def replace_tasks(project_id, tasks):
    with conn() as c:
        c.execute("DELETE FROM tasks WHERE project_id = ?", (project_id,))
        for t in tasks:
            c.execute(
                "INSERT INTO tasks (project_id, type, title, description, inputs, expected_outputs) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (project_id, t["type"], t["title"], t["description"],
                 json.dumps(t["inputs"], ensure_ascii=False),
                 json.dumps(t["expected_outputs"], ensure_ascii=False)),
            )


def get_tasks(project_id):
    with conn() as c:
        rows = c.execute("SELECT * FROM tasks WHERE project_id = ? ORDER BY id", (project_id,)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["inputs"] = json.loads(d["inputs"])
        d["expected_outputs"] = json.loads(d["expected_outputs"])
        result.append(d)
    return result
