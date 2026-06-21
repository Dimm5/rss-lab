#!/usr/bin/env python3
"""SQLite helpers for RSS-Lab V1."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT_DIR / "data" / "rss_lab.sqlite3"


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with the pragmas used by the V1 pipeline."""

    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def init_schema(connection: sqlite3.Connection) -> None:
    """Create the V1 tables if they do not exist yet."""

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS article_analysis (
            article_id INTEGER PRIMARY KEY,
            summary_short TEXT NOT NULL,
            score REAL NOT NULL,
            themes_json TEXT NOT NULL,
            keywords_json TEXT NOT NULL,
            model_name TEXT,
            status TEXT NOT NULL DEFAULT 'done',
            analyzed_at TEXT NOT NULL,
            error_message TEXT,
            FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_article_analysis_status ON article_analysis(status)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_article_analysis_analyzed_at ON article_analysis(analyzed_at)"
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_digest (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            digest_date TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            markdown TEXT NOT NULL,
            source_window_json TEXT NOT NULL,
            model_name TEXT,
            status TEXT NOT NULL DEFAULT 'done',
            generated_at TEXT NOT NULL,
            error_message TEXT
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_daily_digest_digest_date ON daily_digest(digest_date)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_daily_digest_generated_at ON daily_digest(generated_at)"
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_state (
            agent_name TEXT PRIMARY KEY,
            last_run_at TEXT,
            last_success_at TEXT,
            last_article_id INTEGER,
            state_json TEXT
        )
        """
    )

    connection.commit()


def fetch_unanalyzed_articles(
    connection: sqlite3.Connection,
    limit: int,
) -> list[dict[str, Any]]:
    """Return articles that do not yet have an analysis row."""

    if limit <= 0:
        return []

    rows = connection.execute(
        """
        SELECT
            a.id,
            a.feed_name,
            a.feed_url,
            a.title,
            a.summary,
            a.url,
            a.published_at,
            a.collected_at
        FROM articles AS a
        LEFT JOIN article_analysis AS aa
            ON aa.article_id = a.id
        WHERE aa.article_id IS NULL
        ORDER BY datetime(COALESCE(a.published_at, a.collected_at)) DESC, a.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_recent_analyses(
    connection: sqlite3.Connection,
    since_iso: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return the most relevant analyses for the daily editor."""

    if limit <= 0:
        return []

    rows = connection.execute(
        """
        SELECT
            aa.article_id,
            aa.summary_short,
            aa.score,
            aa.themes_json,
            aa.keywords_json,
            aa.model_name,
            aa.status,
            aa.analyzed_at,
            a.feed_name,
            a.feed_url,
            a.title,
            a.summary AS rss_summary,
            a.url,
            a.published_at,
            a.collected_at
        FROM article_analysis AS aa
        JOIN articles AS a
            ON a.id = aa.article_id
        WHERE aa.status = 'done'
          AND aa.analyzed_at >= ?
        ORDER BY aa.score DESC, aa.analyzed_at DESC, a.id ASC
        LIMIT ?
        """,
        (since_iso, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def upsert_article_analysis(
    connection: sqlite3.Connection,
    *,
    article_id: int,
    summary_short: str,
    score: float,
    themes_json: str,
    keywords_json: str,
    model_name: str | None,
    status: str,
    analyzed_at: str,
    error_message: str | None = None,
) -> None:
    """Insert or replace an article analysis row."""

    connection.execute(
        """
        INSERT INTO article_analysis (
            article_id,
            summary_short,
            score,
            themes_json,
            keywords_json,
            model_name,
            status,
            analyzed_at,
            error_message
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(article_id) DO UPDATE SET
            summary_short = excluded.summary_short,
            score = excluded.score,
            themes_json = excluded.themes_json,
            keywords_json = excluded.keywords_json,
            model_name = excluded.model_name,
            status = excluded.status,
            analyzed_at = excluded.analyzed_at,
            error_message = excluded.error_message
        """,
        (
            article_id,
            summary_short,
            score,
            themes_json,
            keywords_json,
            model_name,
            status,
            analyzed_at,
            error_message,
        ),
    )
    connection.commit()


def upsert_daily_digest(
    connection: sqlite3.Connection,
    *,
    digest_date: str,
    title: str,
    markdown: str,
    source_window_json: str,
    model_name: str | None,
    status: str,
    generated_at: str,
    error_message: str | None = None,
) -> None:
    """Insert or replace a daily digest row."""

    connection.execute(
        """
        INSERT INTO daily_digest (
            digest_date,
            title,
            markdown,
            source_window_json,
            model_name,
            status,
            generated_at,
            error_message
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(digest_date) DO UPDATE SET
            title = excluded.title,
            markdown = excluded.markdown,
            source_window_json = excluded.source_window_json,
            model_name = excluded.model_name,
            status = excluded.status,
            generated_at = excluded.generated_at,
            error_message = excluded.error_message
        """,
        (
            digest_date,
            title,
            markdown,
            source_window_json,
            model_name,
            status,
            generated_at,
            error_message,
        ),
    )
    connection.commit()


def get_agent_state(
    connection: sqlite3.Connection,
    agent_name: str,
) -> dict[str, Any] | None:
    """Return the current state for an agent, if present."""

    row = connection.execute(
        "SELECT agent_name, last_run_at, last_success_at, last_article_id, state_json FROM agent_state WHERE agent_name = ?",
        (agent_name,),
    ).fetchone()
    return dict(row) if row is not None else None


def update_agent_state(
    connection: sqlite3.Connection,
    agent_name: str,
    *,
    last_run_at: str | None = None,
    last_success_at: str | None = None,
    last_article_id: int | None = None,
    state_json: str | dict[str, Any] | list[Any] | None = None,
) -> None:
    """Upsert the persisted state for an agent."""

    current = get_agent_state(connection, agent_name) or {}
    payload = {
        "last_run_at": current.get("last_run_at") if last_run_at is None else last_run_at,
        "last_success_at": current.get("last_success_at") if last_success_at is None else last_success_at,
        "last_article_id": current.get("last_article_id") if last_article_id is None else last_article_id,
        "state_json": current.get("state_json") if state_json is None else _serialize_state_json(state_json),
    }

    connection.execute(
        """
        INSERT INTO agent_state (
            agent_name,
            last_run_at,
            last_success_at,
            last_article_id,
            state_json
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(agent_name) DO UPDATE SET
            last_run_at = excluded.last_run_at,
            last_success_at = excluded.last_success_at,
            last_article_id = excluded.last_article_id,
            state_json = excluded.state_json
        """,
        (
            agent_name,
            payload["last_run_at"],
            payload["last_success_at"],
            payload["last_article_id"],
            payload["state_json"],
        ),
    )
    connection.commit()


def _serialize_state_json(state_json: str | dict[str, Any] | list[Any]) -> str:
    """Store JSON-like state as canonical text."""

    if isinstance(state_json, str):
        return state_json
    return json.dumps(state_json, ensure_ascii=False, separators=(",", ":"))
