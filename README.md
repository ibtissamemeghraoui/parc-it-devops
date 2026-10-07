# Parc IT DevOps

Application web de gestion de parc informatique (équipements, statuts, affectations),
industrialisée avec une chaîne DevOps complète : conteneurs, CI/CD, infrastructure
as code et supervision.

## Stack

- Python 3, Flask, SQLAlchemy
- SQLite en local (PostgreSQL à partir de la semaine 2)
- pytest pour les tests
- Prometheus : métriques exposées sur `/metrics`

## Lancer en local

```bash
python -m venv .venv
source .venv/Scripts/activate      # Git Bash sous Windows
pip install -r app/requirements.txt
cd app
python app.py
```

L'application est disponible sur http://localhost:5000

## Lancer les tests

```bash
cd app
pytest -v
```

## Routes

| Route | Méthode | Description |
|---|---|---|
| `/` | GET | Page de gestion du parc |
| `/health` | GET | Vérification de santé |
| `/metrics` | GET | Métriques Prometheus |
| `/api/equipements` | GET, POST | Lister, créer |
| `/api/equipements/<id>` | GET, PUT, DELETE | Lire, modifier, supprimer |

## Feuille de route

- [x] Application Flask, API JSON et tests
- [ ] Conteneurisation (Docker, Docker Compose)
- [ ] CI/CD (GitHub Actions)
- [ ] Infrastructure as Code (Terraform)
- [ ] Supervision (Prometheus, Grafana)
- [ ] Sécurité (Trivy)