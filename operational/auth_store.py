"""
P0.7 (2026-09-24 hardening block): minimal, production-appropriate
authentication for a small user base (owner + a few friends) --
deliberately NOT an enterprise identity platform. Passwords are never
stored in plain text: PBKDF2-HMAC-SHA256 with a random per-user salt
(stdlib `hashlib`, no new dependency), a real, standard, slow-by-design
hash -- not a fast general-purpose hash like SHA-256 alone, which would
be unsafe for password storage.

Roles are exactly two: ADMIN (the owner) and USER (a friend). This
module only stores identity/role -- it has no opinion about what a
given role may see; that enforcement lives in dashboard/auth.py's
require_login()/require_admin() guards, called at the top of each page.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "operational" / "auth_store.db"

_PBKDF2_ITERATIONS = 260_000
_SALT_BYTES = 16
VALID_ROLES = ("ADMIN", "USER")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username        TEXT PRIMARY KEY,
    password_hash   TEXT NOT NULL,
    salt            TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('ADMIN', 'USER')),
    created_at_utc  TEXT NOT NULL
);
"""


class AuthError(Exception):
    pass


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path if db_path is not None else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS).hex()


def create_user(conn: sqlite3.Connection, username: str, password: str, role: str) -> None:
    if role not in VALID_ROLES:
        raise AuthError(f"invalid role {role!r} -- must be one of {VALID_ROLES}")
    if not password:
        raise AuthError("password must not be empty")
    import datetime as dt
    salt = os.urandom(_SALT_BYTES)
    password_hash = _hash_password(password, salt)
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, salt, role, created_at_utc) VALUES (?, ?, ?, ?, ?)",
            (username, password_hash, salt.hex(), role, dt.datetime.now(dt.timezone.utc).isoformat()))
        conn.commit()
    except sqlite3.IntegrityError:
        raise AuthError(f"username {username!r} already exists") from None


def verify_login(conn: sqlite3.Connection, username: str, password: str) -> str | None:
    """Returns the user's role on a correct username+password, or None
    on any failure (unknown user, wrong password) -- deliberately the
    same return value for both, so a caller can't distinguish "no such
    user" from "wrong password" by branching on the result (a real,
    if minor, information-disclosure guard)."""
    row = conn.execute("SELECT password_hash, salt, role FROM users WHERE username = ?", (username,)).fetchone()
    if row is None:
        return None
    candidate_hash = _hash_password(password, bytes.fromhex(row["salt"]))
    if hmac.compare_digest(candidate_hash, row["password_hash"]):
        return row["role"]
    return None


def user_exists(conn: sqlite3.Connection, username: str) -> bool:
    return conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone() is not None


def list_users(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT username, role, created_at_utc FROM users ORDER BY created_at_utc").fetchall()
    return [dict(r) for r in rows]


def delete_user(conn: sqlite3.Connection, username: str) -> None:
    conn.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()


def change_password(conn: sqlite3.Connection, username: str, new_password: str) -> None:
    if not user_exists(conn, username):
        raise AuthError(f"no such user {username!r}")
    salt = os.urandom(_SALT_BYTES)
    password_hash = _hash_password(new_password, salt)
    conn.execute("UPDATE users SET password_hash = ?, salt = ? WHERE username = ?",
                 (password_hash, salt.hex(), username))
    conn.commit()
