#!/bin/bash
#
# Non-interactive sync: for each provider that has fresh files waiting, convert
# to the standard Firefly CSV and import it. No provider-specific logic lives
# here — providers are self-contained plugins discovered under providers/.
#
# Provider contract (see providers/README.md):
#   providers/<name>/convert.py   CLI:  convert.py <inbox_dir> <out_csv>
#   providers/<name>/inbox/       raw exports dropped here by the user
#   providers/<name>/processed/   consumed raw files are archived here
#
# Idempotent: the Data Importer dedups on external_id, and consumed files move to
# processed/, so re-running is safe.
#
# ponytail: no lockfile — a scrape colliding with the timer is negligible on a
# 1-user home PC; add flock if it ever bites.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PROVIDERS_DIR="$PROJECT_DIR/providers"
DATA_DIR="$PROJECT_DIR/data"
AUTO_LOG="$DATA_DIR/auto_sync.log"
IMPORT="$SCRIPT_DIR/import-csv-firefly.sh"

mkdir -p "$DATA_DIR"
if [ -f "$PROJECT_DIR/.env" ]; then set -a; source "$PROJECT_DIR/.env"; set +a; fi

notify() { command -v notify-send >/dev/null 2>&1 && notify-send "$@" || true; }

log_event() {  # <provider> <event> [transactions]
    local ts; ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    printf '{"timestamp":"%s","source":"%s","event":"%s","transactions":%s}\n' \
        "$ts" "$1" "$2" "${3:-null}" >> "$AUTO_LOG"
}
count_rows() { [ -f "$1" ] && tail -n +2 "$1" | wc -l | tr -d ' ' || echo 0; }

sync_provider() {
    local dir="$1" name; name="$(basename "$dir")"
    local inbox="$dir/inbox" processed="$dir/processed" out="$dir/firefly.csv"
    [ -d "$inbox" ] || return 0
    # anything to do?
    [ -z "$(ls -A "$inbox" 2>/dev/null || true)" ] && return 0
    [ -f "$dir/convert.py" ] || { echo "→ $name: no convert.py, skip"; return 0; }
    mkdir -p "$processed"

    echo "→ $name: convert"
    if ! python3 "$dir/convert.py" "$inbox" "$out"; then
        log_event "$name" convert_failed; notify "NORA" "$name : conversion échouée"; return 1
    fi
    echo "→ $name: import"
    if "$IMPORT" "$out"; then
        local n; n=$(count_rows "$out")
        find "$inbox" -maxdepth 1 -type f -exec mv {} "$processed/" \;
        log_event "$name" import_completed "$n"; notify "NORA" "$name : import OK ($n lignes)"
    else
        log_event "$name" import_failed; notify "NORA" "$name : import échoué"; return 1
    fi
}

# Sources directes : une configuration d'import par source, stockée sur l'hôte
# (importer/config/import_config_<source>.json, gitignoré), exécutée par l'outil
# d'import en ligne de commande. Les doublons signalés sont un succès : l'outil
# dédoublonne sur external_id.
IMPORTER_CONTAINER="${IMPORTER_CONTAINER:-firefly-iii-importer}"
sync_direct() {
    local cfg="$1" name; name="$(basename "$cfg" .json)"; name="${name#import_config_}"
    echo "→ $name: import direct"
    local out
    out=$(docker exec "$IMPORTER_CONTAINER" php artisan importer:import "/config/$(basename "$cfg")" 2>&1 || true)
    local real_errors
    real_errors=$(echo "$out" | grep -E 'production\.ERROR|Exception' \
        | grep -viE 'already a transaction|Exit code is GENERAL_ERROR|already exists|Failed to create account' || true)
    if [ -n "$real_errors" ] || ! grep -q 'Done!' <<<"$out"; then
        log_event "$name" import_failed; notify "NORA" "$name : import échoué"; return 1
    fi
    log_event "$name" import_completed; echo "  ok"
}

echo "=== auto-sync $(date -u +%FT%TZ) ==="
rc=0
if [ -d "$PROVIDERS_DIR" ]; then
    for dir in "$PROVIDERS_DIR"/*/; do
        [ -d "$dir" ] || continue
        [ "$(basename "$dir")" = "example" ] && continue
        sync_provider "$dir" || rc=1
    done
fi
for cfg in "$PROJECT_DIR"/importer/config/import_config_*.json; do
    [ -f "$cfg" ] || continue
    sync_direct "$cfg" || rc=1
done
exit $rc
