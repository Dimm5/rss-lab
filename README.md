# RSS Lab

Collecteur RSS auto-hébergé sur Raspberry Pi.

## Objectifs

- Collecter des flux RSS
- Stocker les articles dans SQLite
- Exposer les données via FastAPI
- Préparer une future intégration RAG

## Stack

- Python
- SQLite
- Docker
- FastAPI

## Collecteur RSS

Les flux à collecter sont définis dans `feeds.yaml`:

```yaml
feeds:
  - name: Python Insider
    url: https://blog.python.org/feeds/posts/default
```

Lancer la collecte:

```bash
python3 src/collector.py
```

Par défaut, la base SQLite est créée dans `data/rss_lab.sqlite3`. Les doublons sont
évités avec une contrainte unique sur l'URL de l'article.
