#!/usr/bin/env bash
# One-command reproduction of the three-model audit pipeline.
#
# Stages, in order:
#   1. Generate raw plans for each model on the 46-query suite.
#   2. Audit each runs/*.jsonl into audited/*.jsonl (parse + verify + repair + explain).
#   3. Compute headline metrics across all three audited files.
#   4. Run the leave-one-out verifier ablation.
#
# Idempotent: stages 1-2 skip a model if its output already exists.
# Override with FORCE=1 to regenerate.
#
# Usage:
#   bash scripts/run_full_eval.sh
#   FORCE=1 bash scripts/run_full_eval.sh

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

# ── env ─────────────────────────────────────────────────────────────
# Activate the conda env that has the dependencies in requirements.txt.
# Override with CONDA_BASE / CONDA_ENV if your install differs.
CONDA_BASE="${CONDA_BASE:-$(conda info --base 2>/dev/null || echo "$HOME/anaconda3")}"
CONDA_ENV="${CONDA_ENV:-nesy}"
# shellcheck disable=SC1091
source "$CONDA_BASE/etc/profile.d/conda.sh"
conda activate "$CONDA_ENV"

# ── service preflight ──────────────────────────────────────────────
pg_isready -q -h "${POSTGRES_HOST:-localhost}" -p "${POSTGRES_PORT:-5432}" -U "${POSTGRES_USER:-postgres}" || {
    echo "[error] Postgres not reachable. Start it locally and apply db/schema.sql, then retry."
    exit 1
}
curl -fs --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null || {
    echo "[warn] Ollama is not reachable at 127.0.0.1:11434 — Qwen step will fail."
}
command -v swipl >/dev/null || { echo "[error] swipl missing"; exit 1; }

# ── models under test ──────────────────────────────────────────────
MODELS=(
    "ollama:qwen2.5:7b|qwen2_5_7b_full"
    "hf:mistral-7b-instruct-v0.3|hf_mistral_7b_v03_full"
    "hf:gemma-2-9b-it|hf_gemma_2_9b_it_full"
)

mkdir -p evaluation/runs evaluation/audited evaluation/metrics

# ── stage 1+2: generate then audit ─────────────────────────────────
for entry in "${MODELS[@]}"; do
    llm="${entry%%|*}"
    stem="${entry##*|}"
    raw="evaluation/runs/${stem}.jsonl"
    aud="evaluation/audited/${stem}.jsonl"

    if [[ "${FORCE:-0}" != "1" && -s "$raw" ]]; then
        echo "[skip-gen] $stem  ($raw exists)"
    else
        echo "[gen] $llm -> $raw"
        python evaluation/run_llm.py --queries evaluation/queries.yaml \
                                     --llm "$llm" --out "$raw"
    fi

    if [[ "${FORCE:-0}" != "1" && -s "$aud" ]]; then
        echo "[skip-audit] $stem  ($aud exists)"
    else
        echo "[audit] $raw -> $aud"
        python evaluation/run_audit.py --in "$raw" --out "$aud"
    fi
done

# ── stage 3: headline metrics ──────────────────────────────────────
echo "=== Headline metrics ==="
python evaluation/compute_metrics.py --inputs \
    evaluation/audited/qwen2_5_7b_full.jsonl \
    evaluation/audited/hf_mistral_7b_v03_full.jsonl \
    evaluation/audited/hf_gemma_2_9b_it_full.jsonl \
    --by_tag

# ── stage 4: leave-one-out ablation ───────────────────────────────
echo "=== Leave-one-out ablation ==="
python evaluation/run_ablations.py --inputs \
    evaluation/audited/qwen2_5_7b_full.jsonl \
    evaluation/audited/hf_mistral_7b_v03_full.jsonl \
    evaluation/audited/hf_gemma_2_9b_it_full.jsonl \
    --out evaluation/metrics/ablations_v0.json

echo "Done. Snapshots in evaluation/metrics/"
