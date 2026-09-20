"""Apply the audit-layer to a JSONL of raw LLM outputs.

Reads ``evaluation/runs/<llm>.jsonl`` produced by ``run_llm.py``. For each
row, runs verify + repair + explain and writes an enriched JSONL with audit
results to ``evaluation/audited/<llm>.jsonl``.

Input row schema (one JSON object per line):
    {"id":..., "llm":..., "user_id":..., "program":..., "completed":[...],
     "raw":"<LLM text or JSON>", "tags":[...]}

Output schema = input schema + the fields written below (parsed_plan,
violations, proof_tree, repaired_plan, repair_ops, edit_distance,
compliant, timings_ms). This append-only layout keeps the audited
files self-contained: ``compute_metrics.py`` and ``run_ablations.py``
both operate on the audited file alone, no joins required.

The ``timings_ms`` field is a per-stage wall-time breakdown captured
with ``time.perf_counter()``:

    "timings_ms": {
        "parser":    <float, ms in plan_parser.parse>,
        "verifier":  <float, ms in verifier.verify, dominated by Prolog>,
        "explainer": <float, ms in explainer.explain>,
        "repair":    <float, ms in repair.repair; 0.0 when no violations>,
        "reverify":  <float, ms checking the repaired candidate>,
        "total":     <float, wall time including all five stages>,
    }

These values are reported per row, so ``compute_metrics.py
--show-timings`` can aggregate them to contextualise audit overhead
against LLM generation latency. Older audited JSONLs (v1 paper
snapshots) do not have this field and are handled by silent skip
downstream.

Usage:
    python evaluation/run_audit.py --in evaluation/runs/gpt-5.jsonl \
                                   --out evaluation/audited/gpt-5.jsonl
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audit_layer import plan_parser
from audit_layer.pipeline import audit_plan


def _ms_since(t0: float) -> float:
    """Wall-time delta (in milliseconds) since ``t0``, rounded to 3 dp."""
    return round((time.perf_counter() - t0) * 1000.0, 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--credit_cap", type=int, default=18)
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.inp.open() as fin, args.out.open("w") as fout:
        for line in fin:
            row = json.loads(line)
            program = (row.get("program") or "").lower().replace("-", "_")
            completed = [c.upper() for c in (row.get("completed") or [])]

            t_total = time.perf_counter()

            t0 = time.perf_counter()
            plan = plan_parser.parse(
                row.get("raw", ""),
                student_id=row["user_id"],
                program=program,
                completed=completed,
                source=row["llm"],
            )
            parser_ms = _ms_since(t0)

            result = audit_plan(plan, credit_cap=args.credit_cap)

            total_ms = _ms_since(t_total)

            row.update({
                "parsed_plan": plan.model_dump(),
                "violations": [v.model_dump() for v in result.violations],
                "proof_tree": result.proof_tree,
                "repaired_plan": result.repaired_plan.model_dump() if result.repaired_plan else None,
                "repair_ops": [op.model_dump() for op in result.repair_ops],
                "edit_distance": result.edit_distance,
                "compliant": result.compliant,
                "status": result.status,
                "credit_cap": result.credit_cap,
                "repair_strategy": result.repair_strategy,
                "handoff_reasons": result.handoff_reasons,
                "unresolved_courses": result.unresolved_courses,
                "repaired_compliant": result.repaired_compliant,
                "residual_violations": (
                    [v.model_dump() for v in result.residual_violations]
                    if result.residual_violations is not None else None
                ),
                "timings_ms": {
                    **result.timings_ms,
                    "parser":    parser_ms,
                    "total":     total_ms,
                },
            })
            fout.write(json.dumps(row) + "\n")
            print(f"[{row['id']}] status={result.status} "
                  f"violations={len(result.violations)} edits={result.edit_distance} "
                  f"audit={total_ms:.1f}ms")


if __name__ == "__main__":
    main()
