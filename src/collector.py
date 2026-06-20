#!/usr/bin/env python3
"""Collecteur RSS minimal pour rss-lab."""

from __future__ import annotations

import argparse
import contextlib
import html
import signal
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_FEEDS_PATH = ROOT_DIR / "feeds.yaml"
DEFAULT_DB_PATH = ROOT_DIR / "data" / "rss_lab.sqlite3"
USER_AGENT = "rss-lab/0.1 (+https://localhost)"
FEED_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class Feed:
    """Décrit un flux configuré dans feeds.yaml."""

    name: str
    url: str


@dataclass(frozen=True)
class Article:
    """Représente un article normalisé avant insertion en base."""

    feed_name: str
    feed_url: str
    title: str
    url: str
    summary: str | None
    published_at: str | None


def parse_args() -> argparse.Namespace:
    """Prépare les options de ligne de commande."""

    parser = argparse.ArgumentParser(description="Collecte les articles RSS dans SQLite.")
    parser.add_argument(
        "--feeds",
        type=Path,
        default=DEFAULT_FEEDS_PATH,
        help=f"Chemin vers feeds.yaml (défaut: {DEFAULT_FEEDS_PATH})",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"Chemin vers la base SQLite (défaut: {DEFAULT_DB_PATH})",
    )
    return parser.parse_args()


def load_feeds(path: Path) -> list[Feed]:
    """Lit un YAML volontairement simple sans dépendance externe.

    Formats acceptés:

    feeds:
      - name: Example
        url: https://example.com/feed.xml

    ou:

    feeds:
      - https://example.com/feed.xml
    """

    if not path.exists():
        raise FileNotFoundError(f"Fichier de flux introuvable: {path}")

    feeds: list[Feed] = []
    current_name: str | None = None
    current_url: str | None = None
    in_feeds_section = False

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        stripped = line.strip()

        if not stripped:
            continue

        if stripped == "feeds:":
            in_feeds_section = True
            continue

        if not in_feeds_section:
            continue

        if stripped.startswith("- "):
            if current_url:
                feeds.append(make_feed(current_name, current_url))

            current_name = None
            current_url = None
            value = stripped[2:].strip()

            if value.startswith("url:"):
                current_url = clean_yaml_value(value.removeprefix("url:"))
            elif value.startswith("name:"):
                current_name = clean_yaml_value(value.removeprefix("name:"))
            elif value:
                current_url = clean_yaml_value(value)
            continue

        if ":" in stripped:
            key, value = stripped.split(":", 1)
            if key.strip() == "name":
                current_name = clean_yaml_value(value)
            elif key.strip() == "url":
                current_url = clean_yaml_value(value)

    if current_url:
        feeds.append(make_feed(current_name, current_url))

    return feeds


def clean_yaml_value(value: str) -> str:
    """Nettoie une valeur YAML simple, avec ou sans guillemets."""

    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
        return cleaned[1:-1]
    return cleaned


def make_feed(name: str | None, url: str) -> Feed:
    """Construit un flux avec un nom par défaut lisible."""

    return Feed(name=name or url, url=url)


def init_db(db_path: Path) -> sqlite3.Connection:
    """Crée la base SQLite et ses index si nécessaire."""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            feed_name TEXT NOT NULL,
            feed_url TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL UNIQUE,
            summary TEXT,
            published_at TEXT,
            collected_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_articles_published_at ON articles(published_at)"
    )
    connection.commit()
    return connection


def fetch_feed(feed: Feed) -> bytes:
    """Télécharge le XML d'un flux RSS ou Atom."""

    request = Request(feed.url, headers={"User-Agent": USER_AGENT})
    with per_feed_timeout(FEED_TIMEOUT_SECONDS):
        with urlopen(request, timeout=FEED_TIMEOUT_SECONDS) as response:
            return response.read()


def parse_feed(feed: Feed, xml_content: bytes) -> list[Article]:
    """Extrait les articles d'un flux RSS ou Atom."""

    root = ElementTree.fromstring(xml_content)
    tag = strip_namespace(root.tag).lower()

    if tag == "rss":
        return parse_rss(feed, root)
    if tag == "feed":
        return parse_atom(feed, root)

    # Certains flux anciens exposent directement un channel.
    if tag == "channel":
        return parse_rss_channel(feed, root)

    raise ValueError(f"Format de flux non reconnu pour {feed.url}")


def parse_rss(feed: Feed, root: ElementTree.Element) -> list[Article]:
    """Parse un document RSS 2.x."""

    channel = first_child(root, "channel")
    if channel is None:
        return []
    return parse_rss_channel(feed, channel)


def parse_rss_channel(feed: Feed, channel: ElementTree.Element) -> list[Article]:
    """Parse les items contenus dans un channel RSS."""

    articles: list[Article] = []
    for item in children(channel, "item"):
        title = text_of(item, "title") or "(Sans titre)"
        url = text_of(item, "link") or text_of(item, "guid")
        if not url:
            continue

        articles.append(
            Article(
                feed_name=feed.name,
                feed_url=feed.url,
                title=normalize_text(title),
                url=url.strip(),
                summary=optional_normalized_text(
                    text_of(item, "description") or text_of(item, "summary")
                ),
                published_at=parse_date(text_of(item, "pubDate")),
            )
        )

    return articles


def parse_atom(feed: Feed, root: ElementTree.Element) -> list[Article]:
    """Parse un document Atom."""

    articles: list[Article] = []
    for entry in children(root, "entry"):
        title = text_of(entry, "title") or "(Sans titre)"
        url = atom_entry_url(entry)
        if not url:
            continue

        articles.append(
            Article(
                feed_name=feed.name,
                feed_url=feed.url,
                title=normalize_text(title),
                url=url,
                summary=optional_normalized_text(
                    text_of(entry, "summary") or text_of(entry, "content")
                ),
                published_at=parse_date(
                    text_of(entry, "published") or text_of(entry, "updated")
                ),
            )
        )

    return articles


def atom_entry_url(entry: ElementTree.Element) -> str | None:
    """Trouve le meilleur lien disponible pour une entrée Atom."""

    fallback: str | None = None
    for link in children(entry, "link"):
        href = link.attrib.get("href")
        if not href:
            continue

        rel = link.attrib.get("rel", "alternate")
        if rel == "alternate":
            return href.strip()
        fallback = fallback or href.strip()

    return fallback or text_of(entry, "id")


def first_child(element: ElementTree.Element, name: str) -> ElementTree.Element | None:
    """Retourne le premier enfant qui correspond au nom local demandé."""

    return next(children(element, name), None)


def children(element: ElementTree.Element, name: str) -> Iterable[ElementTree.Element]:
    """Itère sur les enfants en ignorant les namespaces XML."""

    for child in element:
        if strip_namespace(child.tag) == name:
            yield child


def text_of(element: ElementTree.Element, name: str) -> str | None:
    """Retourne le texte d'un enfant, si présent."""

    child = first_child(element, name)
    if child is None or child.text is None:
        return None
    return child.text.strip()


def strip_namespace(tag: str) -> str:
    """Supprime le namespace XML pour comparer les balises simplement."""

    return tag.rsplit("}", 1)[-1]


def normalize_text(value: str) -> str:
    """Convertit quelques entités HTML et compacte les espaces."""

    return " ".join(html.unescape(value).split())


def optional_normalized_text(value: str | None) -> str | None:
    """Normalise un texte facultatif."""

    if value is None:
        return None
    normalized = normalize_text(value)
    return normalized or None


def parse_date(value: str | None) -> str | None:
    """Convertit une date de flux en ISO 8601 quand c'est possible."""

    if not value:
        return None

    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return value.strip()

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc).isoformat()


def save_articles(connection: sqlite3.Connection, articles: Iterable[Article]) -> int:
    """Insère les articles et ignore les doublons grâce à l'URL unique."""

    inserted = 0
    collected_at = datetime.now(timezone.utc).isoformat()

    for article in articles:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO articles (
                feed_name,
                feed_url,
                title,
                url,
                summary,
                published_at,
                collected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                article.feed_name,
                article.feed_url,
                article.title,
                article.url,
                article.summary,
                article.published_at,
                collected_at,
            ),
        )
        inserted += cursor.rowcount

    connection.commit()
    return inserted


@contextlib.contextmanager
def per_feed_timeout(seconds: int):
    """Interrompt un téléchargement trop long sur les systèmes compatibles POSIX."""

    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    previous_handler = signal.getsignal(signal.SIGALRM)

    def timeout_handler(signum, frame):  # noqa: ARG001
        raise TimeoutError(f"Délai dépassé après {seconds} secondes")

    signal.signal(signal.SIGALRM, timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def collect(feeds_path: Path, db_path: Path) -> int:
    """Orchestre la collecte complète et retourne le nombre d'articles ajoutés."""

    feeds = load_feeds(feeds_path)
    if not feeds:
        print(f"Aucun flux configuré dans {feeds_path}.")
        return 0

    total_inserted = 0
    with init_db(db_path) as connection:
        for feed in feeds:
            try:
                xml_content = fetch_feed(feed)
                articles = parse_feed(feed, xml_content)
                inserted = save_articles(connection, articles)
            except (HTTPError, URLError, TimeoutError, ElementTree.ParseError, ValueError) as exc:
                print(f"[ERREUR] {feed.name}: {exc}", file=sys.stderr)
                continue

            total_inserted += inserted
            print(f"{feed.name}: {inserted}/{len(articles)} nouveaux articles")

    return total_inserted


def main() -> int:
    """Point d'entrée du script."""

    args = parse_args()

    try:
        total_inserted = collect(args.feeds, args.db)
    except KeyboardInterrupt:
        print("[ERREUR] Collecte interrompue par l'utilisateur.", file=sys.stderr)
        return 130

    print(f"Collecte terminée: {total_inserted} nouvel article ajouté.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
