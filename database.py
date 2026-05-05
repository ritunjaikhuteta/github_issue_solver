import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "github_solver.db")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id          TEXT PRIMARY KEY,
            email       TEXT UNIQUE NOT NULL,
            name        TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at  TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS solve_sessions (
            id           TEXT PRIMARY KEY,
            user_id      TEXT NOT NULL,
            issue_url    TEXT NOT NULL,
            issue_title  TEXT,
            issue_number INTEGER,
            repo_name    TEXT,
            status       TEXT NOT NULL DEFAULT 'running',
            created_at   TEXT NOT NULL,
            completed_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS session_events (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   TEXT NOT NULL,
            event_type   TEXT NOT NULL,
            message      TEXT,
            extra_data   TEXT,
            created_at   TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES solve_sessions(id)
        )
    """)

    conn.commit()
    conn.close()
