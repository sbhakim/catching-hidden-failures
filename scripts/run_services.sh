#!/usr/bin/env bash
# Bring up the audit-layer plus the Postgres dependency.
#
# Usage:
#   ./scripts/run_services.sh           # foreground audit-layer api
#   ./scripts/run_services.sh --bg      # background, logs to /tmp/audit_api.log

set -euo pipefail
cd "$(dirname "$0")/.."

# 1) ensure conda env is active (override with CONDA_ENV)
CONDA_ENV="${CONDA_ENV:-nesy}"
if [[ -z "${CONDA_DEFAULT_ENV:-}" ]] || [[ "$CONDA_DEFAULT_ENV" != "$CONDA_ENV" ]]; then
  echo "[run_services] activating conda env '$CONDA_ENV'..."
  # shellcheck disable=SC1091
  source "$(conda info --base)/etc/profile.d/conda.sh"
  conda activate "$CONDA_ENV"
fi

# 2) Postgres reachable?
export POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
export POSTGRES_PORT="${POSTGRES_PORT:-5432}"
export POSTGRES_USER="${POSTGRES_USER:-postgres}"
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-advising_dev_password}"
export POSTGRES_DB="${POSTGRES_DB:-course_advisor}"

if ! pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then
  echo "[run_services] Postgres not reachable at $POSTGRES_HOST:$POSTGRES_PORT" >&2
  echo "  hint: start your local Postgres + apply db/schema.sql" >&2
  exit 1
fi
echo "[run_services] Postgres OK"

# 3) SWI-Prolog available?
command -v swipl >/dev/null || { echo "[run_services] swipl not found in PATH"; exit 1; }
echo "[run_services] swipl OK"

# 4) start audit-layer API
PORT="${AUDIT_PORT:-8020}"
if [[ "${1:-}" == "--bg" ]]; then
  nohup uvicorn audit_layer.api:app --host 127.0.0.1 --port "$PORT" \
       > /tmp/audit_api.log 2>&1 &
  echo "[run_services] audit_api → http://127.0.0.1:$PORT (logs: /tmp/audit_api.log, pid $!)"
else
  exec uvicorn audit_layer.api:app --host 127.0.0.1 --port "$PORT" --reload
fi
