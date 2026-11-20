import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from app.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    mode TEXT NOT NULL,
    pages_read INTEGER NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    status TEXT NOT NULL,
    risk TEXT NOT NULL,
    data_json TEXT NOT NULL,
    corrected_json TEXT,
    validation_json TEXT NOT NULL,
    trace_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_created ON documents (created_at);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def get_connection():
    connection = sqlite3.connect(settings.DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    folder = os.path.dirname(settings.DATABASE_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)

    with get_connection() as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(SCHEMA)


def purge_old_documents() -> int:
    if settings.DATA_RETENTION_DAYS <= 0:
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.DATA_RETENTION_DAYS)
    cutoff_text = cutoff.isoformat(timespec="seconds")

    with get_connection() as connection:
        cursor = connection.execute(
            "DELETE FROM documents WHERE created_at < ?", (cutoff_text,)
        )
        return cursor.rowcount