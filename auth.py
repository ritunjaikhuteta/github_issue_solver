import os
import uuid
import json
import bcrypt
import jwt
from datetime import datetime, timedelta, timezone
from database import get_db

SECRET_KEY = os.getenv("SECRET_KEY", "ghisolver-super-secret-2024-change-me")
ALGORITHM = "HS256"
TOKEN_EXPIRE_DAYS = 7


# ── Password helpers ──────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


# ── JWT helpers ───────────────────────────────────────────────────────────────

def create_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(days=TOKEN_EXPIRE_DAYS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload["sub"]
    except Exception:
        return None


# ── User operations ───────────────────────────────────────────────────────────

def register_user(email: str, name: str, password: str) -> dict | None:
    """Returns user dict on success, None if email already exists."""
    conn = get_db()
    c = conn.cursor()
    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    try:
        c.execute(
            "INSERT INTO users (id, email, name, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, email.lower().strip(), name.strip(), hash_password(password), now),
        )
        conn.commit()
        return {"id": user_id, "email": email.lower().strip(), "name": name.strip()}
    except Exception:
        return None
    finally:
        conn.close()


def login_user(email: str, password: str) -> dict | None:
    """Returns user dict on success, None if credentials invalid."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE email = ?", (email.lower().strip(),))
    row = c.fetchone()
    conn.close()
    if row and verify_password(password, row["password_hash"]):
        return {"id": row["id"], "email": row["email"], "name": row["name"]}
    return None


def get_user(user_id: str) -> dict | None:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, email, name, created_at FROM users WHERE id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


# ── Session operations ────────────────────────────────────────────────────────

def create_session(user_id: str, issue_url: str, session_id: str) -> None:
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO solve_sessions (id, user_id, issue_url, status, created_at) VALUES (?, ?, ?, 'running', ?)",
        (session_id, user_id, issue_url, now),
    )
    conn.commit()
    conn.close()


def update_session_meta(session_id: str, issue_title: str, issue_number: int, repo_name: str) -> None:
    conn = get_db()
    conn.execute(
        "UPDATE solve_sessions SET issue_title=?, issue_number=?, repo_name=? WHERE id=?",
        (issue_title, issue_number, repo_name, session_id),
    )
    conn.commit()
    conn.close()


def complete_session(session_id: str, status: str = "done") -> None:
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE solve_sessions SET status=?, completed_at=? WHERE id=?",
        (status, now, session_id),
    )
    conn.commit()
    conn.close()


def save_event(session_id: str, event_type: str, message: str = None, extra_data: dict = None) -> None:
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO session_events (session_id, event_type, message, extra_data, created_at) VALUES (?, ?, ?, ?, ?)",
        (session_id, event_type, message, json.dumps(extra_data) if extra_data else None, now),
    )
    conn.commit()
    conn.close()


def get_user_sessions(user_id: str) -> list:
    conn = get_db()
    c = conn.cursor()
    c.execute(
        """SELECT id, issue_url, issue_title, issue_number, repo_name, status, created_at, completed_at
           FROM solve_sessions WHERE user_id=? ORDER BY created_at DESC LIMIT 50""",
        (user_id,),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_session_events(session_id: str, user_id: str) -> list | None:
    """Returns events only if session belongs to user."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT user_id FROM solve_sessions WHERE id=?", (session_id,))
    row = c.fetchone()
    if not row or row["user_id"] != user_id:
        conn.close()
        return None
    c.execute(
        "SELECT event_type, message, extra_data, created_at FROM session_events WHERE session_id=? ORDER BY id",
        (session_id,),
    )
    events = []
    for r in c.fetchall():
        e = dict(r)
        if e["extra_data"]:
            try:
                e["extra_data"] = json.loads(e["extra_data"])
            except Exception:
                pass
        events.append(e)
    conn.close()
    return events
