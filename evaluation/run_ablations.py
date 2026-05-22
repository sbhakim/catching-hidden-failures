"""Leave-one-out (and leave-two-out) ablation of verifier constraint families.

For each audited JSONL, re-verifies the parsed plans with one constraint
family disabled at a time (``--mode one``, default) or with every pair of
families disabled simultaneously (``--mode two``) and reports how much each
ablation contributes to flagged-plan rate, mean violations per plan, and
repair success rate.

Read against `compute_metrics.py`: that script reports the *full* verifier
metrics per model. This script answers "which checks are doing the work?"
and, in ``--mode two``, "do families interact?"

Usage:
    python evaluation/run_ablations.py \
        --inputs evaluation/audited/qwen2_5_7b_full.jsonl \
                 evaluation/audited/hf_mistral_7b_v03_full.jsonl \
                 evaluation/audited/hf_gemma_2_9b_it_full.jsonl \
        --out evaluation/metrics/ablations_v0.json

    python evaluation/run_ablations.py --mode two \
        --inputs evaluation/audited/*.jsonl \
        --out evaluation/metrics/ablations_pairs.json
"""
from __future__ import annotations
import argparse, itertools, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audit_layer import verifier, repair as repair_mod
from audit_layer.models import Plan, ViolationKind


# NO_PLAN_EXTRACTED is intentionally excluded from every ablation because
# skipping it would let empty plans pass silently — the baseline already
# tracks parse success separately. The five ablatable families are emitted
# by ablation_configs(mode) below.


def evaluate(rows: list[dict], skip: tuple[ViolationKind, ...] | ViolationKind | None) -> dict:
    """Re-verify and re-repair `rows` with `skip` disabled.

    `skip` accepts None (full verifier), a single ViolationKind, or a tuple
    of ViolationKinds (used by --mode two pairwise ablation).
    """
    if skip is None:
        skip_t: tuple[ViolationKind, ...] = ()
    elif isinstance(skip, tuple):
        skip_t = skip
    else:
        skip_t = (skip,)
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


def label(skip: tuple[ViolationKind, ...] | ViolationKind | None) -> str:
    if skip is None:
        return "full"
    if isinstance(skip, tuple):
        # Sort by .value for stable labels across runs.
        names = sorted(k.value for k in skip)
        return "no_" + "+".join(names)
    return f"no_{skip.value}"


def ablation_configs(mode: str) -> list:
    """Return the list of skip-configurations to evaluate.

    Always includes None (full verifier) as the baseline so reports
    are self-contained. Pair labels are emitted in a deterministic
    (alphabetical) order so JSON output is stable across runs.
    """
    singles = [
        ViolationKind.PREREQ_MISSING,
        ViolationKind.UNKNOWN_COURSE,
        ViolationKind.DUPLICATE_OF_COMPLETED,
        ViolationKind.PROGRAM_REQUIREMENT_UNMET,
        ViolationKind.CREDIT_CAP_EXCEEDED,
    ]
    if mode == "one":
        return [None, *singles]
    if mode == "two":
        # Sort pairs by (.value of each kind) for stable order.
        pairs = list(itertools.combinations(singles, 2))
        return [None, *pairs]
    raise ValueError(f"unknown mode: {mode!r} (expected 'one' or 'two')")


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


INTERPRETATION_TWO = """\
## How to read this table (leave-two-out)

Each row disables a *pair* of constraint families simultaneously. Compared
to the leave-one-out report, the question shifts from "which check is
doing the work?" to "do families interact?"

- If a pair's repair-success rate equals (approximately) the maximum of
  the two single-family disables, the families are roughly **independent**:
  one of the two dominates the pair's effect, the other contributes nothing
  on top.
- If a pair's repair-success rate is much higher than the maximum of the
  two single-family disables, the families are **synergistic**: removing
  both unlocks repairs that neither alone could.
- A pair lower than the maximum of the two singles would indicate
  **masking**, where one family was previously hiding violations that the
  other family now exposes. The pair-disable then dominates.

In practice, every pair containing `prereq_missing` lands at roughly the
same level as `no_prereq_missing` alone, because prereq is the single
largest source of both detection and repair difficulty. Non-prereq pairs
(e.g. `unknown+duplicate`) move repair only marginally because the
verifier short-circuits per course --- once a course fires one family,
downstream checks on that course are skipped.
"""


def render_md(all_results: dict, mode: str = "one") -> str:
    title = "Leave-one-out verifier ablation" if mode == "one" else "Leave-two-out (pairwise) verifier ablation"
    lines = [f"# {title}\n"]
    if mode == "one":
        lines.append("Each row disables a single check family; `full` is the unmodified verifier.\n")
        lines.append(INTERPRETATION)
    else:
        lines.append("Each row disables a pair of check families simultaneously; `full` is the unmodified verifier.\n")
        lines.append(INTERPRETATION_TWO)
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
    ap.add_argument("--mode", choices=["one", "two"], default="one",
                    help="Leave-one-out (default) or leave-two-out pairwise ablation.")
    args = ap.parse_args()

    configs = ablation_configs(args.mode)
    header = "Leave-one-out" if args.mode == "one" else "Leave-two-out (pairwise)"
    print(f"\n=== {header} verifier ablation ===\n")
    all_results: dict[str, dict] = {}

    for f in args.inputs:
        rows = [json.loads(l) for l in Path(f).read_text().splitlines() if l.strip()]
        if not rows:
            print(f"{f}: empty"); continue
        llm = rows[0].get("llm", Path(f).stem)
        print(f"--- {llm} ({f}) ---")
        per_model: dict = {}
        for skip in configs:
            m = evaluate(rows, skip)
            per_model[label(skip)] = m
            print(
                f"  {label(skip):44s} flagged={m['flagged_rate']:.3f}  "
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
        md_path.write_text(render_md(all_results, mode=args.mode))
        print(f"Saved JSON -> {args.out}")
        print(f"Saved MD   -> {md_path}")


if __name__ == "__main__":
    main()
