#!/usr/bin/env python3
"""RSS Analyst V1: analyze new articles with Hermes and store results in SQLite."""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db import (
    DEFAULT_DB_PATH,
    connect,
    fetch_unanalyzed_articles,
    init_schema,
    update_agent_state,
    upsert_article_analysis,
)
from prompts import build_rss_analyst_prompt

AGENT_NAME = "rss_analyst"
DEFAULT_LIMIT = 20
HERMES_TIMEOUT_SECONDS = int(os.environ.get("RSS_LAB_HERMES_TIMEOUT", "300"))


def get_hermes_bin() -> str:
    """Return the Hermes executable path used by cron and local runs."""

    return os.environ.get("HERMES_BIN") or "/home/dimitri/.local/bin/hermes"


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(description="Analyse les nouveaux articles RSS avec Hermes.")
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"Chemin vers SQLite (défaut: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Nombre maximum d'articles à traiter (défaut: {DEFAULT_LIMIT})",
    )
    return parser.parse_args()


def configure_logging() -> None:
    """Configure a concise console logger."""

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def utc_now_iso() -> str:
    """Return the current UTC time in ISO format."""

    return datetime.now(timezone.utc).isoformat()


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


def validate_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize the Hermes analysis payload."""

    summary_short = payload.get("summary_short")
    score = payload.get("score")
    themes = payload.get("themes")
    keywords = payload.get("keywords")

    if not isinstance(summary_short, str) or not summary_short.strip():
        raise ValueError("summary_short doit être une chaîne non vide.")
    if not isinstance(score, (int, float)):
        raise ValueError("score doit être numérique.")
    if not isinstance(themes, list) or not all(isinstance(item, str) and item.strip() for item in themes):
        raise ValueError("themes doit être une liste de chaînes non vides.")
    if not isinstance(keywords, list) or not all(isinstance(item, str) and item.strip() for item in keywords):
        raise ValueError("keywords doit être une liste de chaînes non vides.")

    normalized_themes = [item.strip() for item in themes if item.strip()]
    normalized_keywords = [item.strip() for item in keywords if item.strip()]

    if not normalized_themes:
        raise ValueError("themes ne peut pas être vide.")
    if not normalized_keywords:
        raise ValueError("keywords ne peut pas être vide.")

    score_value = float(score)
    if score_value < 0:
        score_value = 0.0
    if score_value > 100:
        score_value = 100.0
    score_value = int(score_value + 0.5)

    return {
        "summary_short": summary_short.strip(),
        "score": score_value,
        "themes": normalized_themes,
        "keywords": normalized_keywords,
    }


def analyze_article(article: dict[str, Any]) -> dict[str, Any]:
    """Ask Hermes to analyze a single article."""

    prompt = build_rss_analyst_prompt(article)
    logging.info("Hermes analyse l'article %s - %s", article["id"], article.get("title", ""))
    output = run_hermes(prompt)
    payload = extract_json_object(output)
    return validate_analysis(payload)


def store_success(
    connection,
    article: dict[str, Any],
    analysis: dict[str, Any],
    model_name: str | None,
) -> None:
    """Persist a successful analysis."""

    now = utc_now_iso()
    upsert_article_analysis(
        connection,
        article_id=int(article["id"]),
        summary_short=analysis["summary_short"],
        score=analysis["score"],
        themes_json=json.dumps(analysis["themes"], ensure_ascii=False),
        keywords_json=json.dumps(analysis["keywords"], ensure_ascii=False),
        model_name=model_name,
        status="done",
        analyzed_at=now,
        error_message=None,
    )
    update_agent_state(
        connection,
        AGENT_NAME,
        last_run_at=now,
        last_success_at=now,
        last_article_id=int(article["id"]),
        state_json={"last_processed_title": article.get("title"), "last_status": "done"},
    )


def store_failure(
    connection,
    article: dict[str, Any],
    error_message: str,
    model_name: str | None,
) -> None:
    """Persist a failed analysis so the article is not retried endlessly."""

    now = utc_now_iso()
    upsert_article_analysis(
        connection,
        article_id=int(article["id"]),
        summary_short="(échec d'analyse)",
        score=0.0,
        themes_json="[]",
        keywords_json="[]",
        model_name=model_name,
        status="error",
        analyzed_at=now,
        error_message=error_message,
    )
    update_agent_state(
        connection,
        AGENT_NAME,
        last_run_at=now,
        last_article_id=int(article["id"]),
        state_json={"last_processed_title": article.get("title"), "last_status": "error", "last_error": error_message},
    )


def main() -> int:
    """Run the RSS Analyst pipeline."""

    configure_logging()
    args = parse_args()
    limit = max(0, args.limit)

    logging.info("Ouverture de la base SQLite: %s", args.db)
    connection = connect(args.db)
    try:
        init_schema(connection)
        articles = fetch_unanalyzed_articles(connection, limit=limit)
        if not articles:
            logging.info("Aucun nouvel article à analyser.")
            return 0

        logging.info("%d article(s) à analyser.", len(articles))
        processed = 0
        for article in articles:
            try:
                analysis = analyze_article(article)
                store_success(connection, article, analysis, os.environ.get("HERMES_MODEL"))
                processed += 1
                logging.info(
                    "Analyse enregistrée pour l'article %s (score %.1f)",
                    article["id"],
                    analysis["score"],
                )
            except Exception as exc:
                logging.error("Échec d'analyse pour l'article %s: %s", article["id"], exc)
                store_failure(connection, article, str(exc), os.environ.get("HERMES_MODEL"))

        logging.info("Terminé: %d article(s) analysé(s) avec succès.", processed)
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
