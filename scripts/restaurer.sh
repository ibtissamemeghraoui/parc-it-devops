#!/usr/bin/env bash
# Restaure la base PostgreSQL depuis une sauvegarde.
# Usage : bash scripts/restaurer.sh sauvegardes/parc-AAAAMMJJ-HHMMSS.dump
# ATTENTION : les données actuelles sont remplacées par celles de la sauvegarde.
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash sous Windows : ne pas transformer les chemins /tmp/...
cd "$(dirname "$0")/.."

fichier="${1:-}"
if [ -z "$fichier" ] || [ ! -f "$fichier" ]; then
  echo "Usage : bash scripts/restaurer.sh sauvegardes/parc-AAAAMMJJ-HHMMSS.dump" >&2
  echo "Sauvegardes disponibles :" >&2
  ls -1t sauvegardes/parc-*.dump 2>/dev/null >&2 || echo "  (aucune)" >&2
  exit 1
fi

echo "ATTENTION : la base actuelle sera REMPLACÉE par le contenu de $fichier."
read -r -p "Tape OUI (en majuscules) pour continuer : " reponse
if [ "$reponse" != "OUI" ]; then
  echo "Annulé, rien n'a été modifié."
  exit 1
fi

docker compose cp "$fichier" db:/tmp/restauration.dump
# L'application est arrêtée pendant la restauration, pour éviter des écritures en parallèle.
docker compose stop app
docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner /tmp/restauration.dump'
docker compose exec -T db rm -f /tmp/restauration.dump
docker compose start app

echo "Restauration terminée depuis $fichier."