"""SQLite state storage for Aegis-Code scan runs and failures."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DEFAULT_DB_DIR = Path.home() / ".aegis_code"
_DB_NAME = "aegis.db"


def _db_path() -> Path:
    path = _DEFAULT_DB_DIR / _DB_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Open (or create) the SQLite database and return a connection."""
    target = db_path or _db_path()
    conn = sqlite3.connect(str(target))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS scan_runs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            target_path     TEXT    NOT NULL,
            started_at      TEXT    NOT NULL,
            completed_at    TEXT,
            status          TEXT    DEFAULT 'running',
            total_failures  INTEGER DEFAULT 0,
            patches_applied INTEGER DEFAULT 0,
            final_diff      TEXT
        );

        CREATE TABLE IF NOT EXISTS failures (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id          INTEGER NOT NULL,
            test_name       TEXT,
            exception_type  TEXT,
            traceback       TEXT,
            severity        TEXT,
            patched         INTEGER DEFAULT 0,
            FOREIGN KEY (run_id) REFERENCES scan_runs(id)
        );

        CREATE TABLE IF NOT EXISTS patches (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id          INTEGER NOT NULL,
            file_path       TEXT    NOT NULL,
            original_code   TEXT,
            patched_code    TEXT,
            diff            TEXT,
            attempt         INTEGER DEFAULT 1,
            applied_at      TEXT    NOT NULL,
            FOREIGN KEY (run_id) REFERENCES scan_runs(id)
        );
        """
    )


# ── Run helpers ───────────────────────────────────────────────

def create_run(conn: sqlite3.Connection, target_path: str) -> int:
    """Insert a new scan run and return its id."""
    cur = conn.execute(
        "INSERT INTO scan_runs (target_path, started_at) VALUES (?, ?)",
        (str(target_path), _now()),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def update_run(conn: sqlite3.Connection, run_id: int, **fields: Any) -> None:
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE scan_runs SET {set_clause} WHERE id = ?",
        [*fields.values(), run_id],
    )
    conn.commit()


def complete_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str = "completed",
    total_failures: int = 0,
    patches_applied: int = 0,
    final_diff: str = "",
) -> None:
    update_run(
        conn,
        run_id,
        completed_at=_now(),
        status=status,
        total_failures=total_failures,
        patches_applied=patches_applied,
        final_diff=final_diff,
    )


# ── Failure helpers ───────────────────────────────────────────

def record_failure(
    conn: sqlite3.Connection,
    run_id: int,
    test_name: str,
    exception_type: str,
    traceback_text: str,
    severity: str,
) -> int:
    cur = conn.execute(
        "INSERT INTO failures (run_id, test_name, exception_type, traceback, severity) "
        "VALUES (?, ?, ?, ?, ?)",
        (run_id, test_name, exception_type, traceback_text, severity),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def mark_failure_patched(conn: sqlite3.Connection, failure_id: int) -> None:
    conn.execute("UPDATE failures SET patched = 1 WHERE id = ?", (failure_id,))
    conn.commit()


# ── Patch helpers ─────────────────────────────────────────────

def record_patch(
    conn: sqlite3.Connection,
    run_id: int,
    file_path: str,
    original_code: str,
    patched_code: str,
    diff: str,
    attempt: int,
) -> int:
    cur = conn.execute(
        "INSERT INTO patches (run_id, file_path, original_code, patched_code, diff, attempt, applied_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (run_id, file_path, original_code, patched_code, diff, attempt, _now()),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


# ── Query helpers ─────────────────────────────────────────────

def get_run(conn: sqlite3.Connection, run_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM scan_runs WHERE id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def get_failures_for_run(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM failures WHERE run_id = ? ORDER BY id", (run_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
