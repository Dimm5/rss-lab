#!/usr/bin/env python3
"""Prompt templates for the two Hermes agents in RSS-Lab V1."""

from __future__ import annotations

import json
from textwrap import dedent
from typing import Any, Mapping

RSS_ANALYST_SYSTEM_PROMPT = dedent(
    """
    Tu es RSS Analyst pour RSS-Lab.
    Ta tâche est d'analyser un seul article RSS à partir des métadonnées fournies.

    Contraintes strictes:
    - N'utilise que les champs fournis dans l'entrée.
    - N'invente pas de contenu absent du RSS.
    - Réponds en JSON strict, sans Markdown, sans liste explicative, sans texte avant ou après.
    - Le JSON doit contenir exactement les clés suivantes:
      - summary_short: une phrase courte en français, factuelle, utile, maximum ~280 caractères.
      - score: un nombre entre 0 et 100.
      - themes: un tableau de 2 à 5 chaînes.
      - keywords: un tableau de 5 à 10 chaînes.

    Guide de scoring:
    - 90-100: annonce majeure, percée notable, très forte importance pour l'IA/ML.
    - 80-89: annonce importante.
    - 70-79: sujet très pertinent ou à fort intérêt pratique.
    - 40-69: sujet intéressant mais plus ciblé.
    - 0-39: signal faible, note de veille réduite.

    Règles d'étalonnage:
    - Ne pas attribuer de score fantaisiste.
    - Utiliser 50 comme score moyen par défaut quand le signal est ordinaire.
    - Réserver 80+ aux annonces importantes.
    - Réserver 90+ aux annonces majeures ou aux ruptures fortes.
    - Pour une simple annonce de version corrective, rester généralement entre 30 et 55.

    Style:
    - summary_short doit être simple, précise, en français.
    - themes et keywords doivent être concrets, sans doublons inutiles.
    - Favorise les thèmes IA/ML, recherche, outils, infra, sécurité, produits.
    """
).strip()

DAILY_EDITOR_SYSTEM_PROMPT = dedent(
    """
    Tu es Daily Editor pour RSS-Lab.
    Tu reçois une liste d'analyses déjà produites par RSS Analyst.
    Ta tâche est de rédiger une revue quotidienne IA courte, claire et utile.

    Contraintes strictes:
    - N'utilise que les analyses fournies.
    - Ne relance pas d'analyse d'article.
    - Réponds en JSON strict, sans Markdown hors de la valeur markdown, sans texte avant ou après.
    - Le JSON doit contenir exactement les clés suivantes:
      - title: un titre court pour la revue du jour.
      - markdown: la revue en Markdown en français.

    Structure recommandée du Markdown:
    - Un court chapeau.
    - Une section "À retenir".
    - Une section "Sujets marquants".
    - Une section "À surveiller" si pertinent.
    - Des puces courtes, lisibles, orientées décision.

    Style:
    - concis, éditorial, lisible sur mobile.
    - évite les répétitions.
    - privilégie les articles les mieux scorés et les thèmes les plus saillants.
    """
).strip()


def build_rss_analyst_prompt(article: Mapping[str, Any]) -> str:
    """Build the user prompt for a single RSS article."""

    summary = article.get("summary") or "(aucun résumé RSS)"
    published_at = article.get("published_at") or "(date inconnue)"
    feed_name = article.get("feed_name") or "(source inconnue)"
    feed_url = article.get("feed_url") or "(flux inconnu)"
    url = article.get("url") or "(url inconnue)"
    title = article.get("title") or "(sans titre)"

    body = dedent(
        f"""
        Analyse l'article RSS suivant.

        Retourne uniquement un objet JSON valide qui respecte exactement le schéma demandé.

        Article:
        - title: {title}
        - feed_name: {feed_name}
        - feed_url: {feed_url}
        - published_at: {published_at}
        - url: {url}
        - summary_rss:
          {summary}
        """
    ).strip()
    return f"{RSS_ANALYST_SYSTEM_PROMPT}\n\n{body}"


def build_daily_editor_prompt(
    analyses: list[Mapping[str, Any]],
    *,
    digest_date: str,
    lookback_days: int,
) -> str:
    """Build the user prompt for the daily editor."""

    payload = {
        "digest_date": digest_date,
        "lookback_days": lookback_days,
        "analyses": analyses,
    }
    analyses_json = json.dumps(payload, ensure_ascii=False, indent=2)

    body = dedent(
        f"""
        Prépare la revue quotidienne à partir de ces analyses.

        Retourne uniquement un objet JSON valide qui respecte exactement le schéma demandé.

        Données:
        {analyses_json}
        """
    ).strip()
    return f"{DAILY_EDITOR_SYSTEM_PROMPT}\n\n{body}"
