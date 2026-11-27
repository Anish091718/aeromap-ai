import sqlite3
from contextlib import contextmanager

from config import DATABASE

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name TEXT NOT NULL,
    location TEXT,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'Pending',
    progress INTEGER DEFAULT 0,
    owner_id INTEGER REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS project_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    image_name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS measurements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    plot_name TEXT NOT NULL,
    area_sqm REAL,
    area_hectares REAL,
    area_acres REAL,
    coordinates TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


@contextmanager
def db():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with db() as conn:
        conn.executescript(SCHEMA)
        # Upgrade existing databases without deleting user/project data.
        columns = {row[1] for row in conn.execute("PRAGMA table_info(projects)")}
        if "owner_id" not in columns:
            conn.execute("ALTER TABLE projects ADD COLUMN owner_id INTEGER REFERENCES users(id) ON DELETE CASCADE")

        # Safe legacy migration: only auto-assign old projects when there is
        # exactly one account. With multiple accounts, leave them unassigned
        # rather than exposing one user's projects to another user.
        user_rows = conn.execute("SELECT id FROM users ORDER BY id").fetchall()
        if len(user_rows) == 1:
            conn.execute("UPDATE projects SET owner_id=? WHERE owner_id IS NULL", (user_rows[0][0],))

        # Background jobs live in memory, so after a restart no job can still be
        # running. Mark leftovers as failed so the UI does not show them forever.
        conn.execute("UPDATE projects SET status='Failed', progress=0 WHERE status='Processing'")


def set_status(project_id, status, progress):
    with db() as conn:
        conn.execute(
            "UPDATE projects SET status=?, progress=? WHERE id=?",
            (status, progress, project_id),
        )
