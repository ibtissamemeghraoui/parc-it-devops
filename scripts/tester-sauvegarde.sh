#!/usr/bin/env bash
# Vérifie qu'une sauvegarde est réellement restaurable, sans toucher à la base réelle :
# crée une sauvegarde, la restaure dans une base temporaire, compare le nombre de lignes
# de chaque table, puis supprime la base temporaire.
# Usage : bash scripts/tester-sauvegarde.sh
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash sous Windows : ne pas transformer les chemins /tmp/...
cd "$(dirname "$0")/.."

bash scripts/sauvegarder.sh
dernier="$(ls -1t sauvegardes/parc-*.dump | head -n 1)"
echo "Test de restauration de $dernier"

docker compose cp "$dernier" db:/tmp/test.dump

# Ce bloc s'exécute DANS le conteneur PostgreSQL.
docker compose exec -T db sh -s <<'EOF'
set -eu
export PGUSER="$POSTGRES_USER"
TEST=parc_test_restauration

nettoyer() {
  dropdb --if-exists "$TEST" >/dev/null 2>&1 || true
  rm -f /tmp/test.dump
}
trap nettoyer EXIT

dropdb --if-exists "$TEST"
createdb "$TEST"
pg_restore -d "$TEST" --no-owner /tmp/test.dump

echo "Comparaison du nombre de lignes (base réelle / base restaurée) :"
ok=1
for t in $(psql -d "$POSTGRES_DB" -tAc "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1"); do
  a=$(psql -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM \"$t\"")
  b=$(psql -d "$TEST" -tAc "SELECT count(*) FROM \"$t\"")
  if [ "$a" = "$b" ]; then
    echo "  OK     $t : $a lignes"
  else
    echo "  ECHEC  $t : $a lignes dans la base réelle, $b dans la base restaurée"
    ok=0
  fi
done

if [ "$ok" = 1 ]; then
  echo "RESTAURATION VÉRIFIÉE"
else
  echo "ÉCHEC DE LA VÉRIFICATION"
  exit 1
fi
EOF