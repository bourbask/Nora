#!/usr/bin/env bash
# Déploie la version locale (déjà revue et fusionnée) sur le serveur.
# Les fichiers ignorés par git (secrets, stratégie réelle, données, base) ne
# sont jamais expédiés : ceux de l'hôte restent intacts.
set -euo pipefail
: "${VPS_HOST:?VPS_HOST requis (utilisateur@hôte)}"
NORA_DIR="${NORA_DIR:-nora}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.vps.yml"

tar czf - -C "$ROOT" --exclude-vcs-ignores --exclude=.git . \
  | ssh "$VPS_HOST" "mkdir -p '$NORA_DIR' && tar xzf - -C '$NORA_DIR'"
ssh "$VPS_HOST" "cd '$NORA_DIR' && $COMPOSE pull --ignore-buildable && $COMPOSE up -d --build"
