"""Compute headline metrics across one or more audited LLM runs.

This is the script that produces Table 1 of the paper. All rates are
computed against the full input population N (the number of audited rows
in the file), *not* the number of flagged plans, so they are directly
comparable across models.

Headline metrics:
  - parse_success_rate        : % plans where the parser recovered ≥1 block
  - compliant_rate            : % plans the verifier accepted unchanged
  - prereq_violation_rate     : % plans with ≥1 prereq violation
  - credit_cap_violation_rate : % plans with ≥1 credit-cap violation
  - unknown_course_rate       : % plans referencing nonexistent courses
  - duplicate_rate            : % plans re-listing already-completed courses
  - program_mismatch_rate     : % plans containing courses not in the program
  - mean_violations_per_plan  : average # of violation records per plan
  - mean_edit_distance        : average # of repair ops per plan
  - repair_success_rate       : among plans where repair was attempted,
                                % whose *re-verified* output was clean.
                                This is the verifier-checked figure, not a
                                counted-edits proxy — a bad fix that still
                                violates a constraint does not count as a
                                success.

Slice-able by tags (e.g. only adversarial, only short_term).

Usage:
    python evaluation/compute_metrics.py --inputs evaluation/audited/*.jsonl
"""
from __future__ import annotations
import argparse, json, glob
from collections import defaultdict
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audit_layer import verifier
from audit_layer.models import Plan


def _has_kind(violations: list[dict], kind: str) -> bool:
    return any(v["kind"] == kind for v in violations)


def metrics_for(rows: list[dict]) -> dict:
    n = len(rows) or 1
    parse_ok = sum(1 for r in rows if r["parsed_plan"]["blocks"])
    prereq = sum(1 for r in rows if _has_kind(r["violations"], "prereq_missing"))
    cap = sum(1 for r in rows if _has_kind(r["violations"], "credit_cap_exceeded"))
    unk = sum(1 for r in rows if _has_kind(r["violations"], "unknown_course"))
    dup = sum(1 for r in rows if _has_kind(r["violations"], "duplicate_of_completed"))
    prog = sum(1 for r in rows if _has_kind(r["violations"], "program_requirement_unmet"))
    total_v = sum(len(r["violations"]) for r in rows)
    total_e = sum(r.get("edit_distance", 0) for r in rows)
    repair_attempted = [r for r in rows if r.get("repaired_plan") is not None]
    repair_ok = 0
    for r in repair_attempted:
        repaired = Plan.model_validate(r["repaired_plan"])
        if not verifier.verify(repaired):
            repair_ok += 1
    return {
        "n": n,
        "parse_success_rate":      parse_ok / n,
        "compliant_rate":          sum(1 for r in rows if r.get("compliant")) / n,
        "prereq_violation_rate":   prereq / n,
        "credit_cap_violation_rate": cap / n,
        "unknown_course_rate":     unk / n,
        "duplicate_rate":          dup / n,
        "program_mismatch_rate":   prog / n,
        "mean_violations_per_plan": total_v / n,
        "mean_edit_distance":      total_e / n,
        "repair_success_rate":     repair_ok / (len(repair_attempted) or 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", nargs="+", required=True,
                    help="One or more audited JSONL files (or glob patterns)")
    ap.add_argument("--by_tag", action="store_true",
                    help="Also report metrics sliced by query tag")
    args = ap.parse_args()

    files: list[str] = []
    for p in args.inputs:
        matched = glob.glob(p)
        files.extend(matched if matched else [p])

    print(f"\n=== Metrics across {len(files)} run(s) ===\n")
    for f in files:
        rows = [json.loads(l) for l in Path(f).read_text().splitlines() if l.strip()]
        if not rows:
            print(f"{f}: empty")
            continue
        llm = rows[0].get("llm", Path(f).stem)
        print(f"--- {llm} ({f}) ---")
        m = metrics_for(rows)
        for k, v in m.items():
            print(f"  {k:30s} {v:.3f}" if isinstance(v, float) else f"  {k:30s} {v}")

        if args.by_tag:
            buckets: dict[str, list[dict]] = defaultdict(list)
            for r in rows:
                for t in r.get("tags", []) or ["untagged"]:
                    buckets[t].append(r)
            for tag, sub in sorted(buckets.items()):
                m = metrics_for(sub)
                print(f"  ↳ tag={tag} (n={m['n']})  "
                      f"prereq={m['prereq_violation_rate']:.2f} "
                      f"compliant={m['compliant_rate']:.2f} "
                      f"edits={m['mean_edit_distance']:.2f}")
        print()


if __name__ == "__main__":
    main()
