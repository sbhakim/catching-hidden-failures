"""End-to-end smoke test: synthetic plan in, audit result out.

Doesn't need any LLM running. Hard-codes one good plan and one obviously
broken plan for student 4942915 and verifies the audit-layer's behavior:

  1. Compliant plan:           expect 0 violations, 0 edits.
  2. Plan with bad prereqs:    expect ≥1 PREREQ_MISSING, repair attempts move/remove.
  3. Plan with credit overload: expect CREDIT_CAP_EXCEEDED, repair trims.
  4. Plan with unknown course: expect UNKNOWN_COURSE, repair removes.

Run:
    python scripts/smoke_test.py
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audit_layer import db, plan_parser, verifier, repair as repair_mod, explainer
from audit_layer.models import Plan, SemesterBlock


STUDENT_ID = 4942915  # CS-BS, ~4 done


def show(title: str, plan: Plan, violations, ops):
    print(f"\n=== {title} ===")
    for b in plan.blocks:
        print(f"  {b.semester}: {', '.join(b.courses) or '(empty)'}")
    print(f"  → {len(violations)} violation(s), {len(ops)} repair op(s)")
    for v in violations:
        print(f"    ✗ [{v.semester}] {v.kind.value}  {v.course or ''}  — {v.detail}")
    for op in ops:
        print(f"    ↳ {op.action} {op.course} from {op.semester}"
              f"{' → ' + op.target_semester if op.target_semester else ''}"
              f"  ({op.rationale})")


def make_plan(blocks: list[tuple[str, list[str]]], program: str, completed: list[str]) -> Plan:
    return Plan(
        student_id=STUDENT_ID,
        program=program,
        completed=completed,
        blocks=[SemesterBlock(semester=s, courses=cs) for s, cs in blocks],
        source="smoke_test",
    )


def main():
    program, completed = db.student_context(STUDENT_ID)
    program_atom = (program or "cs_bs").lower().replace("-", "_")
    print(f"student {STUDENT_ID} → program={program} completed={completed}")

    # ── Case 1: a plan that should be (mostly) compliant ───────────────
    p1 = make_plan(
        [("Fall 2026", ["COP_3337", "CDA_3102"])],
        program_atom, completed,
    )
    v1 = verifier.verify(p1)
    rp1, ops1 = repair_mod.repair(p1, v1)
    show("Case 1: compliant-ish plan", p1, v1, ops1)

    # ── Case 2: prereq violation (jump to a course requiring 3530) ────
    p2 = make_plan(
        [("Fall 2026", ["COP_4338"])],   # requires COP_3530
        program_atom, completed,
    )
    v2 = verifier.verify(p2)
    rp2, ops2 = repair_mod.repair(p2, v2)
    show("Case 2: prereq-violation plan", p2, v2, ops2)
    if v2:
        print("  proof tree:")
        for line in explainer.explain(p2, v2):
            print(f"    {line}")

    # ── Case 3: credit overload ───────────────────────────────────────
    p3 = make_plan(
        [("Fall 2026", ["COP_3337", "CDA_3102", "COP_3530", "MAD_2104", "CGS_3095",
                          "ENC_3249", "CIS_3950"])],   # ~20+ credits
        program_atom, completed,
    )
    v3 = verifier.verify(p3, credit_cap=15)
    rp3, ops3 = repair_mod.repair(p3, v3, credit_cap=15)
    show("Case 3: credit-cap overload", p3, v3, ops3)

    # ── Case 4: unknown course ────────────────────────────────────────
    p4 = make_plan(
        [("Fall 2026", ["COP_9999", "CDA_3102"])],
        program_atom, completed,
    )
    v4 = verifier.verify(p4)
    rp4, ops4 = repair_mod.repair(p4, v4)
    show("Case 4: unknown course", p4, v4, ops4)

    print("\nsmoke test complete.")


if __name__ == "__main__":
    main()
