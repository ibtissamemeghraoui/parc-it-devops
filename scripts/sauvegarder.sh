#!/usr/bin/env bash
# Sauvegarde la base PostgreSQL dans sauvegardes/parc-AAAAMMJJ-HHMMSS.dump
# Usage : bash scripts/sauvegarder.sh
# Les 7 sauvegardes les plus récentes sont conservées (RETENTION=n pour changer).
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash sous Windows : ne pas transformer les chemins /tmp/...
cd "$(dirname "$0")/.."

dossier="sauvegardes"
retention="${RETENTION:-7}"
mkdir -p "$dossier"
fichier="$dossier/parc-$(date +%Y%m%d-%H%M%S).dump"

# La sauvegarde est faite dans le conteneur, puis copiée : pas de redirection binaire.
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --file=/tmp/sauvegarde.dump'
docker compose cp db:/tmp/sauvegarde.dump "$fichier"
docker compose exec -T db rm -f /tmp/sauvegarde.dump

if [ ! -s "$fichier" ]; then
  echo "ERREUR : la sauvegarde est vide." >&2
  rm -f "$fichier"
  exit 1
fi

# Ne garde que les sauvegardes les plus récentes.
ls -1t "$dossier"/parc-*.dump | tail -n +"$((retention + 1))" | xargs -r rm --

echo "Sauvegarde créée : $fichier ($(du -h "$fichier" | cut -f1))"