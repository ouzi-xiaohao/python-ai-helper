from __future__ import annotations

"""SQLite persistence helpers.

The project keeps persistence intentionally simple. A few helper functions
wrap sqlite3 so routes can create sessions, save messages, and record model
audits without bringing in an ORM.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.schemas import ChatMessage

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "aethervoice.db"


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DATA_DIR.mkdir(exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db() -> None:
    """Create all tables if this is the first application startup."""
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES chat_sessions(id)
            );

            CREATE TABLE IF NOT EXISTS model_call_audits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                model TEXT NOT NULL,
                provider TEXT NOT NULL,
                prompt_tokens INTEGER NOT NULL,
                completion_tokens INTEGER NOT NULL,
                total_tokens INTEGER NOT NULL,
                prompt_cost REAL NOT NULL,
                completion_cost REAL NOT NULL,
                total_cost REAL NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                latency_ms INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES chat_sessions(id)
            );
            """
        )


def create_session(title: str | None = None) -> sqlite3.Row:
    session_title = title or "新的对话"
    with connect() as db:
        cursor = db.execute(
            "INSERT INTO chat_sessions (title) VALUES (?) RETURNING *",
            (session_title,),
        )
        return cursor.fetchone()


def list_sessions() -> list[sqlite3.Row]:
    with connect() as db:
        return db.execute(
            """
            SELECT * FROM chat_sessions
            ORDER BY updated_at DESC, id DESC
            """
        ).fetchall()


def get_session(session_id: int) -> sqlite3.Row | None:
    with connect() as db:
        return db.execute(
            "SELECT * FROM chat_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()


def get_or_create_session(session_id: int | None, title: str | None = None) -> sqlite3.Row:
    """Return an existing session or create one for new conversations."""
    if session_id is not None:
        session = get_session(session_id)
        if session:
            return session
    return create_session(title)


def add_message(session_id: int, role: str, content: str) -> None:
    with connect() as db:
        db.execute(
            "INSERT INTO chat_messages (session_id, role, content) VALUES (?, ?, ?)",
            (session_id, role, content),
        )
        db.execute(
            "UPDATE chat_sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (session_id,),
        )


def replace_session_messages(session_id: int, messages: list[ChatMessage]) -> None:
    """Persist the prompt-side history before appending the assistant reply."""
    with connect() as db:
        db.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
        db.executemany(
            "INSERT INTO chat_messages (session_id, role, content) VALUES (?, ?, ?)",
            [(session_id, message.role, message.content) for message in messages],
        )
        db.execute(
            "UPDATE chat_sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (session_id,),
        )


def list_messages(session_id: int) -> list[sqlite3.Row]:
    with connect() as db:
        return db.execute(
            """
            SELECT role, content FROM chat_messages
            WHERE session_id = ?
            ORDER BY id ASC
            """,
            (session_id,),
        ).fetchall()


def record_audit(
    *,
    session_id: int,
    model: str,
    provider: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    prompt_cost: float,
    completion_cost: float,
    total_cost: float,
    status: str,
    latency_ms: int,
    error: str | None = None,
) -> sqlite3.Row:
    """Insert one model-call audit row and return it."""
    with connect() as db:
        cursor = db.execute(
            """
            INSERT INTO model_call_audits (
                session_id, model, provider, prompt_tokens, completion_tokens,
                total_tokens, prompt_cost, completion_cost, total_cost,
                status, error, latency_ms
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING *
            """,
            (
                session_id,
                model,
                provider,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                prompt_cost,
                completion_cost,
                total_cost,
                status,
                error,
                latency_ms,
            ),
        )
        return cursor.fetchone()


def list_audits(limit: int = 100) -> list[sqlite3.Row]:
    with connect() as db:
        return db.execute(
            """
            SELECT * FROM model_call_audits
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
