"""Apply the audit-layer to a JSONL of raw LLM outputs.

Reads ``evaluation/runs/<llm>.jsonl`` produced by ``run_llm.py``. For each
row, runs verify + repair + explain and writes an enriched JSONL with audit
results to ``evaluation/audited/<llm>.jsonl``.

Input row schema (one JSON object per line):
    {"id":..., "llm":..., "user_id":..., "program":..., "completed":[...],
     "raw":"<LLM text or JSON>", "tags":[...]}

Output schema = input schema + the fields written below (parsed_plan,
violations, proof_tree, repaired_plan, repair_ops, edit_distance,
compliant). This append-only layout keeps the audited files
self-contained: ``compute_metrics.py`` and ``run_ablations.py`` both
operate on the audited file alone, no joins required.

Usage:
    python evaluation/run_audit.py --in evaluation/runs/gpt-5.jsonl \
                                   --out evaluation/audited/gpt-5.jsonl
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audit_layer import plan_parser, verifier, repair as repair_mod, explainer


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

            plan = plan_parser.parse(
                row.get("raw", ""),
                student_id=row["user_id"],
                program=program,
                completed=completed,
                source=row["llm"],
            )

            violations = verifier.verify(plan, credit_cap=args.credit_cap)
            proof = explainer.explain(plan, violations)
            repaired_plan, ops = (None, [])
            if violations:
                repaired_plan, ops = repair_mod.repair(
                    plan, violations, credit_cap=args.credit_cap
                )

            row.update({
                "parsed_plan": plan.model_dump(),
                "violations": [v.model_dump() for v in violations],
                "proof_tree": proof,
                "repaired_plan": repaired_plan.model_dump() if repaired_plan else None,
                "repair_ops": [op.model_dump() for op in ops],
                "edit_distance": len(ops),
                "compliant": not violations,
            })
            fout.write(json.dumps(row) + "\n")
            print(f"[{row['id']}] compliant={not violations} "
                  f"violations={len(violations)} edits={len(ops)}")


if __name__ == "__main__":
    main()
