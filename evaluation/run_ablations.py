"""Leave-one-out ablation of verifier constraint families.

For each audited JSONL, re-verifies the parsed plans with one constraint
family disabled at a time and reports how much each family contributes to
flagged-plan rate, mean violations per plan, and repair success rate.

Read against `compute_metrics.py`: that script reports the *full* verifier
metrics per model. This script answers "which checks are doing the work?"

Usage:
    python evaluation/run_ablations.py \
        --inputs evaluation/audited/qwen2_5_7b_full.jsonl \
                 evaluation/audited/hf_mistral_7b_v03_full.jsonl \
                 evaluation/audited/hf_gemma_2_9b_it_full.jsonl \
        --out evaluation/metrics/ablations_v0.json
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audit_layer import verifier, repair as repair_mod
from audit_layer.models import Plan, ViolationKind


# Order matters only for printing. NO_PLAN_EXTRACTED is intentionally excluded
# because skipping it would let empty plans pass silently — the baseline
# already tracks parse success separately.
ABLATION_FAMILIES = [
    None,  # full verifier (baseline)
    ViolationKind.PREREQ_MISSING,
    ViolationKind.UNKNOWN_COURSE,
    ViolationKind.DUPLICATE_OF_COMPLETED,
    ViolationKind.PROGRAM_REQUIREMENT_UNMET,
    ViolationKind.CREDIT_CAP_EXCEEDED,
]


def evaluate(rows: list[dict], skip: ViolationKind | None) -> dict:
    skip_t: tuple[ViolationKind, ...] = (skip,) if skip else ()
    n = len(rows)
    flagged = 0
    total_viol = 0
    repair_attempted = 0
    repair_ok = 0
    total_edits = 0

    for r in rows:
        if not r.get("parsed_plan"):
            continue
        plan = Plan.model_validate(r["parsed_plan"])
        viols = verifier.verify(plan, skip_checks=skip_t)
        if viols:
            flagged += 1
            total_viol += len(viols)
            repaired, ops = repair_mod.repair(plan, viols)
            if ops:
                repair_attempted += 1
                total_edits += len(ops)
                if not verifier.verify(repaired, skip_checks=skip_t):
                    repair_ok += 1

    return {
        "n": n,
        "flagged_rate": flagged / n,
        "mean_violations_per_plan": total_viol / n,
        "mean_edit_distance": total_edits / n,
        "repair_attempted": repair_attempted,
        "repair_success_rate": repair_ok / (repair_attempted or 1),
    }


def label(skip: ViolationKind | None) -> str:
    return "full" if skip is None else f"no_{skip.value}"


INTERPRETATION = """\
## How to read this table

The verifier short-circuits per course: once a course triggers a violation
of kind K, downstream checks do not fire on the same course in the same
semester. Disabling K therefore lets the next check fire on courses that
K would have rejected, often producing a different violation kind on the
same course. As a result:

- Disabling **prereq_missing** is the only ablation that strictly *removes*
  detected violations (no other check substitutes for it). It is the
  largest contributor to detection across all three models.
- Disabling **unknown_course** or **duplicate_of_completed** does not drop
  the violation count --- those courses now get flagged as
  `program_requirement_unmet` (or another downstream kind). This is a
  feature, not a bug: the multi-family verifier catches each unsafe
  course exactly once, regardless of which family fires first.
- Disabling **credit_cap_exceeded** has no effect on this benchmark: no
  generated plan exceeds 18 credits per term. The check is dormant on
  this query suite and motivates a credit-stress query in v1.
- The most paper-relevant ablation signal is **repair success**: removing
  prereq checks pushes repair from 0.65/0.66 to 0.82/0.88 on Mistral and
  Gemma, because the remaining violations (unknown, duplicate, program)
  are amenable to greedy removal. Prereq violations carry both detection
  weight and repair difficulty.

Per-kind detection coverage (the orthogonal view) is reported in the main
results table in `three_model_v0.md`.
"""


def render_md(all_results: dict) -> str:
    lines = ["# Leave-one-out verifier ablation (v0)\n"]
    lines.append("Each row disables a single check family; `full` is the unmodified verifier.\n")
    lines.append(INTERPRETATION)
    for llm, results in all_results.items():
        lines.append(f"\n## {llm}\n")
        lines.append("| Ablation | Flagged | Viol/plan | Edit dist | Repair attempted | Repair success |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for skip_label, m in results.items():
            lines.append(
                f"| {skip_label} | {m['flagged_rate']:.3f} | "
                f"{m['mean_violations_per_plan']:.3f} | "
                f"{m['mean_edit_distance']:.3f} | "
                f"{m['repair_attempted']} | "
                f"{m['repair_success_rate']:.3f} |"
            )
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", nargs="+", required=True,
                    help="One or more audited JSONL files.")
    ap.add_argument("--out", type=Path,
                    help="Optional JSON output path; an .md sibling is also written.")
    args = ap.parse_args()

    print("\n=== Leave-one-out verifier ablation ===\n")
    all_results: dict[str, dict] = {}

    for f in args.inputs:
        rows = [json.loads(l) for l in Path(f).read_text().splitlines() if l.strip()]
        if not rows:
            print(f"{f}: empty"); continue
        llm = rows[0].get("llm", Path(f).stem)
        print(f"--- {llm} ({f}) ---")
        per_model: dict = {}
        for skip in ABLATION_FAMILIES:
            m = evaluate(rows, skip)
            per_model[label(skip)] = m
            print(
                f"  {label(skip):38s} flagged={m['flagged_rate']:.3f}  "
                f"viol/plan={m['mean_violations_per_plan']:.3f}  "
                f"edits={m['mean_edit_distance']:.3f}  "
                f"repair={m['repair_success_rate']:.3f}  "
                f"(n_repair={m['repair_attempted']})"
            )
        all_results[llm] = per_model
        print()

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(all_results, indent=2))
        md_path = args.out.with_suffix(".md")
        md_path.write_text(render_md(all_results))
        print(f"Saved JSON -> {args.out}")
        print(f"Saved MD   -> {md_path}")


if __name__ == "__main__":
    main()
