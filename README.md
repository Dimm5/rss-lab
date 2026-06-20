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
  - name: OpenAI News
    url: https://openai.com/news/rss.xml
```

Lancer la collecte:

```bash
python3 src/collector.py
```

Par défaut, la base SQLite est créée dans `data/rss_lab.sqlite3`. Les doublons sont
évités avec une contrainte unique sur l'URL de l'article.

## Automatisation avec cron

Le collecteur peut être exécuté de manière non interactive. Il ne demande aucune
saisie et peut donc être lancé par cron toutes les 6 heures.

Un timeout par flux est activé dans le collecteur pour éviter qu'un site lent
bloque toute l'exécution.

Depuis le Raspberry Pi:

```bash
cd /home/dimitri/rss-lab
mkdir -p logs
python3 src/collector.py >> logs/collector.log 2>&1
```

Si la commande fonctionne, éditer la crontab:

```bash
crontab -e
```

Ajouter cette ligne pour lancer le collecteur toutes les 6 heures:

```cron
0 */6 * * * cd /home/dimitri/rss-lab && /usr/bin/python3 src/collector.py >> logs/collector.log 2>&1
```

La base SQLite existante reste utilisée dans `data/rss_lab.sqlite3`. Les doublons
continuent d'être évités par la contrainte unique sur l'URL de l'article.

Vérifier l'installation cron:

```bash
crontab -l
tail -n 100 /home/dimitri/rss-lab/logs/collector.log
ls -lh /home/dimitri/rss-lab/data/rss_lab.sqlite3
```

Pour suivre les exécutions dans le temps:

```bash
tail -f /home/dimitri/rss-lab/logs/collector.log
```

Pour tester la ligne cron sans attendre 6 heures:

```bash
cd /home/dimitri/rss-lab && /usr/bin/python3 src/collector.py >> logs/collector.log 2>&1
tail -n 100 logs/collector.log
```
