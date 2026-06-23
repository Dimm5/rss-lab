#!/usr/bin/env python3
"""Daily Editor V1: build a daily AI digest from stored analyses."""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from db import (
    DEFAULT_DB_PATH,
    connect,
    fetch_recent_analyses,
    init_schema,
    update_agent_state,
    upsert_daily_digest,
)
from prompts import build_daily_editor_prompt

AGENT_NAME = "daily_editor"
DEFAULT_LOOKBACK_DAYS = 1
MAX_ANALYSES_IN_DIGEST = 20
HERMES_TIMEOUT_SECONDS = int(os.environ.get("RSS_LAB_HERMES_TIMEOUT", "300"))


def get_hermes_bin() -> str:
    """Return the Hermes executable path used by cron and local runs."""

    return os.environ.get("HERMES_BIN") or "/home/dimitri/.local/bin/hermes"


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(description="Produit la revue IA quotidienne à partir des analyses stockées.")
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"Chemin vers SQLite (défaut: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help=f"Fenêtre de recherche en jours (défaut: {DEFAULT_LOOKBACK_DAYS})",
    )
    return parser.parse_args()


def configure_logging() -> None:
    """Configure a concise console logger."""

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def utc_now() -> datetime:
    """Return the current UTC datetime."""

    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Return the current UTC time in ISO format."""

    return utc_now().isoformat()


def run_hermes(prompt: str) -> str:
    """Execute Hermes in non-interactive mode and return its output."""

    command = [
        get_hermes_bin(),
        "chat",
        "--quiet",
        "--ignore-rules",
        "--source",
        "rss-lab",
        "--max-turns",
        "1",
        "-q",
        prompt,
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=HERMES_TIMEOUT_SECONDS)

    if completed.returncode != 0:
        raise RuntimeError(
            f"Hermes a échoué (code {completed.returncode}). stderr: {completed.stderr.strip() or '(vide)'}"
        )

    return completed.stdout.strip()


def extract_json_object(output: str) -> dict[str, Any]:
    """Extract a JSON object from Hermes output, ignoring trailing session info."""

    text = output.strip()
    if text.startswith("```"):
        text = _strip_code_fence(text)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError(f"Aucun objet JSON trouvé dans la réponse Hermes: {output[:500]!r}")
        parsed = json.loads(text[start : end + 1])

    if not isinstance(parsed, dict):
        raise ValueError("La réponse Hermes doit être un objet JSON.")
    return parsed


def _strip_code_fence(text: str) -> str:
    """Remove a single fenced block if Hermes returned one."""

    lines = text.splitlines()
    if len(lines) >= 2 and lines[0].startswith("```"):
        if lines[-1].startswith("```"):
            return "\n".join(lines[1:-1]).strip()
        return "\n".join(lines[1:]).strip()
    return text


def validate_digest_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the Hermes payload for the daily digest."""

    title = payload.get("title")
    markdown = payload.get("markdown")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title doit être une chaîne non vide.")
    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError("markdown doit être une chaîne non vide.")
    return {"title": title.strip(), "markdown": markdown.strip()}


def build_digest_rows(connection, lookback_days: int) -> list[dict[str, Any]]:
    """Load the analyses that will feed the daily digest."""

    cutoff = utc_now() - timedelta(days=max(0, lookback_days))
    rows = fetch_recent_analyses(
        connection,
        since_iso=cutoff.isoformat(),
        limit=MAX_ANALYSES_IN_DIGEST,
    )
    return rows


def generate_digest(analyses: list[dict[str, Any]], lookback_days: int) -> dict[str, Any]:
    """Ask Hermes to write the daily digest."""

    digest_date = utc_now().date().isoformat()
    prompt = build_daily_editor_prompt(analyses, digest_date=digest_date, lookback_days=lookback_days)
    logging.info("Hermes rédige la revue quotidienne à partir de %d analyse(s).", len(analyses))
    output = run_hermes(prompt)
    payload = extract_json_object(output)
    validated = validate_digest_payload(payload)
    validated["digest_date"] = digest_date
    validated["source_window_json"] = json.dumps(
        {
            "digest_date": digest_date,
            "lookback_days": lookback_days,
            "analysis_count": len(analyses),
            "article_ids": [row["article_id"] for row in analyses],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return validated


def main() -> int:
    """Run the daily editor pipeline."""

    configure_logging()
    args = parse_args()
    lookback_days = max(0, args.lookback_days)

    logging.info("Ouverture de la base SQLite: %s", args.db)
    connection = connect(args.db)
    try:
        init_schema(connection)
        analyses = build_digest_rows(connection, lookback_days)
        if not analyses:
            logging.info("Aucune analyse récente disponible pour la revue du jour.")
            return 0

        logging.info("%d analyse(s) retenue(s) pour la revue.", len(analyses))
        digest = generate_digest(analyses, lookback_days)
        now = utc_now_iso()
        upsert_daily_digest(
            connection,
            digest_date=digest["digest_date"],
            title=digest["title"],
            markdown=digest["markdown"],
            source_window_json=digest["source_window_json"],
            model_name=os.environ.get("HERMES_MODEL"),
            status="done",
            generated_at=now,
            error_message=None,
        )
        update_agent_state(
            connection,
            AGENT_NAME,
            last_run_at=now,
            last_success_at=now,
            state_json={"digest_date": digest["digest_date"], "lookback_days": lookback_days, "analysis_count": len(analyses)},
        )
        logging.info("Revue quotidienne enregistrée pour %s.", digest["digest_date"])
        return 0
    except Exception as exc:
        logging.error("Échec de la revue quotidienne: %s", exc)
        now = utc_now_iso()
        update_agent_state(connection, AGENT_NAME, last_run_at=now, state_json={"last_error": str(exc), "lookback_days": lookback_days})
        try:
            upsert_daily_digest(
                connection,
                digest_date=utc_now().date().isoformat(),
                title="Revue IA quotidienne (échec)",
                markdown=f"_Échec de génération_: {exc}",
                source_window_json=json.dumps({"error": str(exc), "lookback_days": lookback_days}, ensure_ascii=False),
                model_name=os.environ.get("HERMES_MODEL"),
                status="error",
                generated_at=now,
                error_message=str(exc),
            )
        except Exception:
            logging.exception("Impossible d'enregistrer la revue en erreur.")
        return 1
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
