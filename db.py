"""SQLite storage: users, per-user document registry, per-user evaluation log."""
import json
import os
import sqlite3
import time
from contextlib import contextmanager

from dotenv import load_dotenv

load_dotenv()
DB_PATH = os.getenv("DOCMIND_DB", "docmind.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS documents(
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    doc_id TEXT NOT NULL,
    name TEXT NOT NULL,
    chunks INTEGER NOT NULL,
    kind TEXT NOT NULL DEFAULT 'pdf',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, doc_id)
);
CREATE TABLE IF NOT EXISTS evals(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    time TEXT NOT NULL,
    source TEXT,
    question TEXT,
    answer TEXT,
    faithfulness REAL,
    answer_relevancy REAL,
    context_precision REAL,
    claims TEXT
);
CREATE INDEX IF NOT EXISTS idx_evals_user ON evals(user_id);
"""


@contextmanager
def _conn():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init():
    with _conn() as c:
        c.executescript(SCHEMA)
        cols = [r["name"] for r in c.execute("PRAGMA table_info(documents)").fetchall()]
        if "kind" not in cols:
            c.execute("ALTER TABLE documents ADD COLUMN kind TEXT NOT NULL DEFAULT 'pdf'")


# ---------------------------------------------------------------- users
def create_user(username, password_hash):
    """Returns the new user id, or None if the username is taken."""
    try:
        with _conn() as c:
            cur = c.execute(
                "INSERT INTO users(username, password_hash) VALUES (?, ?)",
                (username, password_hash),
            )
            return cur.lastrowid
    except sqlite3.IntegrityError:
        return None


def get_user(user_id):
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_name(username):
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------- documents
def add_document(user_id, doc_id, name, chunks, kind="pdf"):
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO documents(user_id, doc_id, name, chunks, kind) VALUES (?, ?, ?, ?, ?)",
            (user_id, doc_id, name, chunks, kind),
        )


def list_documents(user_id):
    with _conn() as c:
        rows = c.execute(
            "SELECT doc_id, name, chunks, kind FROM documents WHERE user_id = ? ORDER BY rowid",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_documents(user_id, doc_ids):
    """Only returns documents that belong to this user."""
    if not doc_ids:
        return []
    marks = ",".join("?" * len(doc_ids))
    with _conn() as c:
        rows = c.execute(
            f"SELECT doc_id, name, chunks, kind FROM documents WHERE user_id = ? AND doc_id IN ({marks})",
            (user_id, *doc_ids),
        ).fetchall()
    return [dict(r) for r in rows]


def remove_document(user_id, doc_id):
    with _conn() as c:
        c.execute("DELETE FROM documents WHERE user_id = ? AND doc_id = ?", (user_id, doc_id))


# ---------------------------------------------------------------- evaluations
def add_eval(user_id, source, question, answer, result):
    with _conn() as c:
        c.execute(
            "INSERT INTO evals(user_id, time, source, question, answer, faithfulness, "
            "answer_relevancy, context_precision, claims) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                user_id,
                time.strftime("%Y-%m-%d %H:%M:%S"),
                source,
                question,
                answer[:400],
                result.get("faithfulness"),
                result.get("answer_relevancy"),
                result.get("context_precision"),
                json.dumps(result.get("claims", []), ensure_ascii=False),
            ),
        )


def list_evals(user_id):
    with _conn() as c:
        rows = c.execute(
            "SELECT time, source, question, answer, faithfulness, answer_relevancy, "
            "context_precision, claims FROM evals WHERE user_id = ? ORDER BY id",
            (user_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["claims"] = json.loads(d["claims"] or "[]")
        out.append(d)
    return out


def clear_evals(user_id):
    with _conn() as c:
        c.execute("DELETE FROM evals WHERE user_id = ?", (user_id,))
