"""Analyse leave-two-out pairwise interactions on top of a leave-one-out
baseline.

For every (model, pair) in the leave-two-out JSON, this script compares
the pair's repair-success rate against the better of the two
single-family disables (computed in-process from the same audited
JSONLs). The signed difference is the *interaction*:

    interaction = repair_disable(A, B) - max(repair_disable(A),
                                             repair_disable(B))

Positive interaction = synergy (disabling both unlocks repairs neither
alone could); negative = masking; near-zero = the two families are
roughly independent.

The script emits a per-(model, pair) table to stdout and writes a
per-pair aggregation across models to
``evaluation/metrics/ablations_pairs_interactions.json``.

Usage:
    python evaluation/analyze_pair_interactions.py \
        --pairs evaluation/metrics/ablations_pairs_v1.json \
        --out   evaluation/metrics/ablations_pairs_interactions.json
"""
from __future__ import annotations
import argparse, json, sys, statistics as stats
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation.run_ablations import ablation_configs, evaluate, label


# Map model identifier (the ``llm`` field written by run_llm.py) -> audited
# JSONL path. Used so the leave-one-out side of the comparison can be
# re-derived without rerunning the full ablation script.
INPUTS = {
    "ollama:qwen2.5:7b":                  "evaluation/audited/qwen2_5_7b_full.jsonl",
    "hf:mistral-7b-instruct-v0.3":        "evaluation/audited/hf_mistral_7b_v03_full.jsonl",
    "hf:gemma-2-9b-it":                   "evaluation/audited/hf_gemma_2_9b_it_full.jsonl",
    "hf:gemma-3-12b-it":                  "evaluation/audited/hf_gemma_3_12b_it_full.jsonl",
    "hf:deepseek-r1-0528-qwen3-8b":       "evaluation/audited/hf_deepseek_r1_0528_qwen3_8b_full.jsonl",
    "gpt-4o-mini":                        "evaluation/audited/gpt4o_mini_full.jsonl",
    "deepseek-chat":                      "evaluation/audited/deepseek_chat_full.jsonl",
    "openrouter:gemini-2.5-flash-lite":   "evaluation/audited/openrouter_gemini_2_5_flash_lite_full.jsonl",
    "openrouter:llama-3.3-70b-instruct":  "evaluation/audited/openrouter_llama_3_3_70b_instruct_full.jsonl",
    "claude-haiku-4-5":                   "evaluation/audited/claude_haiku_4_5_full.jsonl",
}

THRESHOLD = 0.02  # |interaction| >= this is flagged as synergy / masking


def split_pair(lab: str) -> tuple[str, str]:
    assert lab.startswith("no_"), lab
    return tuple(sorted(lab[3:].split("+")))


def derive_singles(model: str, fp: Path) -> dict[str, float]:
    """Recompute single-family repair-success rates for one model."""
    rows = [json.loads(l) for l in fp.read_text().splitlines() if l.strip()]
    out: dict[str, float] = {}
    for c in ablation_configs("one"):
        m = evaluate(rows, c)
        out[label(c)] = m["repair_success_rate"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=Path, required=True,
                    help="Leave-two-out JSON produced by run_ablations.py --mode two.")
    ap.add_argument("--out", type=Path, required=True,
                    help="Where to write the per-pair interaction summary.")
    args = ap.parse_args()

    pairs = json.loads(args.pairs.read_text())

    print("[Computing leave-one-out rates for cross-reference...]")
    singles_by_model: dict[str, dict[str, float]] = {}
    for llm, rel in INPUTS.items():
        fp = ROOT / rel
        if not fp.exists():
            continue
        rows = [json.loads(l) for l in fp.read_text().splitlines() if l.strip()]
        if not rows:
            continue
        actual = rows[0].get("llm", llm)
        singles_by_model[actual] = derive_singles(actual, fp)

    print(f"\n=== Pairwise interactions per (model, pair) ===")
    print(f"{'Model':30s} {'Pair':50s} {'pair':>7s} {'A':>6s} {'B':>6s} {'max(A,B)':>9s} {'inter':>8s}")
    per_pair: dict[tuple[str, str], list[tuple[str, float]]] = {}
    for model, results in pairs.items():
        singles = singles_by_model.get(model, {})
        for plabel, m in sorted(results.items()):
            if "+" not in plabel:  # skip 'full'
                continue
            A, B = split_pair(plabel)
            rep_pair = m["repair_success_rate"]
            rep_a = singles.get(f"no_{A}")
            rep_b = singles.get(f"no_{B}")
            if rep_a is None or rep_b is None:
                continue
            max_ab = max(rep_a, rep_b)
            inter = rep_pair - max_ab
            flag = " *" if abs(inter) >= THRESHOLD else ""
            print(f"{model[:30]:30s} {plabel[:50]:50s} {rep_pair:>7.3f} {rep_a:>6.3f} {rep_b:>6.3f} {max_ab:>9.3f} {inter:>+8.3f}{flag}")
            per_pair.setdefault((A, B), []).append((model, inter))

    print("\n=== Mean interaction per pair (across models) ===")
    print(f"{'Pair':50s} {'mean':>8s} {'min':>7s} {'max':>7s} {'#syn':>6s} {'#mask':>7s}")
    summary: dict[str, dict] = {}
    for (A, B), entries in sorted(per_pair.items()):
        inters = [v for _, v in entries]
        syn = sum(1 for v in inters if v >= THRESHOLD)
        msk = sum(1 for v in inters if v <= -THRESHOLD)
        mean_v = stats.mean(inters)
        print(f"  no_{A}+{B:46s} {mean_v:>+8.3f} {min(inters):>+7.3f} {max(inters):>+7.3f} {syn:>6d} {msk:>7d}")
        summary[f"{A}+{B}"] = {
            "n_models": len(entries),
            "mean_interaction": mean_v,
            "min": min(inters),
            "max": max(inters),
            "n_synergy_ge_threshold":  syn,
            "n_masking_le_neg_threshold": msk,
            "threshold": THRESHOLD,
            "per_model": dict(entries),
        }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"interactions_by_pair": summary}, indent=2))
    print(f"\nSaved -> {args.out}")


if __name__ == "__main__":
    main()
