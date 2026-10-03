#!/usr/bin/env bash
# Archive OKX realised funding rates on a schedule - Linux counterpart of archive_funding.ps1.
#
# Closes advisor finding F-8 / prerequisite P-11. OKX retains only ~95 days of realised funding
# and the window ROLLS, so a gap between two runs longer than ~95 days is ground truth lost for
# good. Weekly is comfortable, monthly is the outer limit.
#
# Public endpoints only. No credential is read or transmitted.
#
# THIS MUST RUN ON A PERSISTENT HOST. A crontab inside an ephemeral cloud container is not a
# schedule: it disappears with the container, and so does everything it archived.
#
# Usage:
#   scripts/archive_funding.sh [--env PAPER|DEMO] [--symbols N]   run the archive once, now
#   scripts/archive_funding.sh --install   [--env ...] [--symbols N]   register weekly cron job
#   scripts/archive_funding.sh --uninstall [--env ...]                 remove it
#   scripts/archive_funding.sh --status    [--env ...]                 verify it is installed AND
#                                                                      has succeeded recently
#
# --status exits non-zero if the cron entry is missing or the last successful run is older than
# STALE_AFTER_DAYS. "Installed" is not evidence that it ran; the success stamp is.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/archive_funding.sh"
PY="$ROOT/.venv/bin/python"

# Sundays 03:17 UTC. Off the hour so it does not coincide with every other job at :00.
CRON_SCHEDULE="17 3 * * 0"
# Weekly cadence plus a day of slack. Well inside the ~95-day retention window, so a stale
# warning arrives long before any data is actually lost.
STALE_AFTER_DAYS=8

mode="run"
env_name="PAPER"
symbols=20

while [[ $# -gt 0 ]]; do
    case "$1" in
        --install | --uninstall | --status) mode="${1#--}" ;;
        --env)
            env_name="${2:?--env needs a value}"
            shift
            ;;
        --symbols)
            symbols="${2:?--symbols needs a value}"
            shift
            ;;
        -h | --help)
            sed -n '2,23p' "$SCRIPT"
            exit 0
            ;;
        *)
            echo "unknown argument: $1" >&2
            exit 2
            ;;
    esac
    shift
done

# LIVE is deliberately not accepted: this is public data, and the LIVE profile is locked.
case "$env_name" in
    PAPER | DEMO) ;;
    *)
        echo "--env must be PAPER or DEMO, got '$env_name'" >&2
        exit 2
        ;;
esac
if ! [[ "$symbols" =~ ^[1-9][0-9]*$ ]]; then
    echo "--symbols must be a positive integer, got '$symbols'" >&2
    exit 2
fi

slug="${env_name,,}"
log_dir="$ROOT/logs/$slug"
state_dir="$ROOT/state/$slug"
log_file="$log_dir/archive_funding.log"
stamp_file="$state_dir/archive_funding.last_ok"
lock_file="$state_dir/archive_funding.lock"
# The marker identifies our line in the crontab so install/uninstall touch nothing else.
marker="# okxq-archive-funding env=$env_name"

utc_now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

require_crontab() {
    if ! command -v crontab >/dev/null 2>&1; then
        echo "crontab not found. Install cron on this host (e.g. 'apt-get install cron')." >&2
        exit 3
    fi
}

current_crontab() { crontab -l 2>/dev/null || true; }

case "$mode" in
    install)
        require_crontab
        if [[ ! -x "$PY" ]]; then
            echo "venv not found at $PY - run 'uv sync --locked --all-extras' first" >&2
            exit 3
        fi
        entry="$CRON_SCHEDULE /usr/bin/env bash '$SCRIPT' --env $env_name --symbols $symbols $marker"
        # Idempotent: drop any previous line carrying our marker, then append the new one.
        # Read fully BEFORE writing: piping `crontab -l` straight into `crontab -` races the
        # reader against the writer and can wipe the user's unrelated jobs.
        others="$(current_crontab | grep -vF -- "$marker" || true)"
        printf '%s\n' ${others:+"$others"} "$entry" | crontab -
        echo "Installed weekly funding archive (Sundays 03:17 UTC):"
        echo "  $entry"
        echo "Confirm it ran with: $SCRIPT --status --env $env_name"
        ;;

    uninstall)
        require_crontab
        others="$(current_crontab | grep -vF -- "$marker" || true)"
        printf '%s' "${others:+$others$'\n'}" | crontab -
        echo "Removed funding archive cron entry for $env_name (if it existed)."
        ;;

    status)
        require_crontab
        rc=0
        if current_crontab | grep -qF -- "$marker"; then
            echo "cron entry: present"
            current_crontab | grep -F -- "$marker" | sed 's/^/  /'
        else
            echo "cron entry: MISSING"
            rc=1
        fi
        if [[ -f "$stamp_file" ]]; then
            last="$(cat "$stamp_file")"
            age_days=$(( ($(date -u +%s) - $(date -u -d "$last" +%s)) / 86400 ))
            echo "last successful run: $last ($age_days days ago)"
            if (( age_days > STALE_AFTER_DAYS )); then
                echo "STALE: older than $STALE_AFTER_DAYS days - check $log_file"
                rc=1
            fi
        else
            echo "last successful run: NEVER - no evidence it has run (expected stamp: $stamp_file)"
            rc=1
        fi
        exit "$rc"
        ;;

    run)
        if [[ ! -x "$PY" ]]; then
            echo "venv not found at $PY - run 'uv sync --locked --all-extras' first" >&2
            exit 3
        fi
        mkdir -p "$log_dir" "$state_dir"
        # Cron runs with a minimal environment and no tty: everything goes to the log file.
        exec >>"$log_file" 2>&1
        # Never let two archives overlap on the same store (a slow run meeting the next one).
        exec 9>"$lock_file"
        if ! flock -n 9; then
            echo "$(utc_now) SKIP another archive run holds $lock_file"
            exit 0
        fi
        echo "$(utc_now) START env=$env_name symbols=$symbols"
        cd "$ROOT"
        set +e
        "$PY" -m okxq.data.cli backfill --env "$env_name" --symbols "$symbols" --funding-only
        code=$?
        set -e
        if [[ $code -ne 0 ]]; then
            echo "$(utc_now) FAIL exit=$code"
            exit "$code"
        fi
        # Written only on success, atomically: --status trusts this file, not the cron entry.
        utc_now >"$stamp_file.tmp" && mv -f "$stamp_file.tmp" "$stamp_file"
        echo "$(utc_now) OK"
        ;;
esac
