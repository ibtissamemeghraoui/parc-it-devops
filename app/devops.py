"""Informations d'exploitation affichées sur la page DevOps."""

import json
import os
import platform
import socket
import time
import urllib.parse
import urllib.request

from sqlalchemy import text

VERSION = "0.7.0-dev"
DEMARRAGE = time.time()


def duree_fonctionnement():
    secondes = int(time.time() - DEMARRAGE)
    jours, secondes = divmod(secondes, 86400)
    heures, secondes = divmod(secondes, 3600)
    minutes = secondes // 60
    return f"{jours} j {heures} h {minutes} min" if jours else f"{heures} h {minutes} min"


def informations(db):
    """État du conteneur et de la base de données."""
    nom = db.engine.dialect.name
    version = db.session.execute(text("SHOW server_version")).scalar() if nom == "postgresql" else None
    return {
        "version": VERSION,
        "python": platform.python_version(),
        "machine": socket.gethostname(),
        "duree": duree_fonctionnement(),
        "base": nom,
        "base_version": version,
    }


def requete_prometheus(requete, adresse):
    """Valeur d'une requête Prometheus, ou None si Prometheus ne répond pas."""
    url = f"{adresse}/api/v1/query?" + urllib.parse.urlencode({"query": requete})
    try:
        with urllib.request.urlopen(url, timeout=1) as reponse:  # noqa: S310 (adresse fournie par l'environnement)
            resultat = json.load(reponse)["data"]["result"]
        return float(resultat[0]["value"][1]) if resultat else 0.0
    except (OSError, ValueError, KeyError, IndexError):
        return None


def metriques(adresse=None):
    """Quelques métriques de supervision, ou None si Prometheus est absent."""
    adresse = adresse or os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
    if not adresse.startswith(("http://", "https://")):
        return None
    cibles = requete_prometheus("count(up == 1)", adresse)
    if cibles is None:
        return None
    debit = requete_prometheus("sum(rate(flask_http_request_total[5m]))", adresse) or 0.0
    erreurs = requete_prometheus('sum(rate(flask_http_request_total{status=~"5.."}[5m]))', adresse) or 0.0
    return {"cibles": int(cibles), "debit": round(debit, 2), "erreurs": round(erreurs, 3)}