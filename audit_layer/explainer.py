"""Proof-tree-grounded explanations.

For each violation, produce a human-readable explanation backed by the
symbolic resolution itself, not by a second LLM call. The explainer uses
the same Prolog bridge as the verifier, so an explanation cannot disagree
with the verdict it is explaining.

V0: re-derive the missing-prereq chain depth-first; fall back to the
program's `needed_chain/3` when the immediate-prereq path returns nothing.
Full leashed-trace capture (parsing SWI-Prolog's debugger output) is v1.
"""
from __future__ import annotations
from .models import Plan, Violation, ViolationKind
from . import prolog_bridge


def _missing_prereq_tree(course: str, taken: set[str], program: str,
                         seen: set[str] | None = None) -> list[str]:
    """Depth-first missing prerequisite tree rendered as course IDs.

    This uses the same immediate-prereq bridge as the verifier, so explanation
    quality does not depend on SWI-Prolog debugger trace parsing.
    """
    seen = seen or set()
    course = course.upper()
    if course in seen:
        return []
    seen.add(course)

    missing = [
        p for p in prolog_bridge.prereqs_of(course, program=program)
        if p not in taken
    ]
    chain: list[str] = []
    for prereq in missing:
        chain.extend(_missing_prereq_tree(prereq, taken, program, seen))
        if prereq not in chain:
            chain.append(prereq)
    if missing and course not in chain:
        chain.append(course)
    return chain


def _why_prereq_missing(course: str, plan: Plan) -> list[str]:
    """Return a chain of atoms explaining what's missing to take `course`."""
    taken_lit = "[" + ",".join(prolog_bridge.norm_course(c) for c in plan.completed) + "]"
    tgt = prolog_bridge.norm_course(course)
    consult = prolog_bridge.consult_program(prolog_bridge.norm_prog(plan.program))
    goal = (
        f"({consult}"
        f" (needed_chain({tgt},{taken_lit},Chain) -> format('~q',[Chain]); format('~q',[[]])))"
    )
    out, _ = prolog_bridge.run_prolog(goal)
    return [prolog_bridge.denorm_course(a) for a in prolog_bridge.atoms_from_list_literal(out)]


def explain(plan: Plan, violations: list[Violation]) -> list[str]:
    """Return a list of human-readable lines, one per violation, grounded in
    the symbolic state of the program rules.

    Prereq strategy: try the depth-first immediate-prereq tree first
    (fast, agrees with the verifier by construction). If empty — typically
    because the program file declares its prereqs via `needed_chain/3`
    rather than plain `prerequisite/2` — fall back to that predicate.
    """
    lines: list[str] = []
    for v in violations:
        if v.kind == ViolationKind.PREREQ_MISSING and v.course:
            taken = {c.upper() for c in plan.completed}
            chain = _missing_prereq_tree(v.course, taken, plan.program)
            if not chain:
                chain = _why_prereq_missing(v.course, plan)
            if chain and chain != [v.course.replace("_", "").upper()]:
                arrow = " → ".join(chain)
                lines.append(
                    f"[{v.semester}] {v.course} is blocked. Required path: {arrow}"
                )
            else:
                lines.append(
                    f"[{v.semester}] {v.course}: prerequisites not satisfied "
                    f"(no chain found in program rules — may be unmodeled)."
                )
        elif v.kind == ViolationKind.CREDIT_CAP_EXCEEDED:
            lines.append(f"[{v.semester}] {v.detail}")
        elif v.kind == ViolationKind.DUPLICATE_OF_COMPLETED:
            lines.append(
                f"[{v.semester}] {v.course} is already in the student's history."
            )
        elif v.kind == ViolationKind.UNKNOWN_COURSE:
            lines.append(f"[{v.semester}] {v.course} is not in the catalog.")
        elif v.kind == ViolationKind.PROGRAM_REQUIREMENT_UNMET:
            lines.append(f"[{v.semester}] {v.detail}")
        elif v.kind == ViolationKind.NO_PLAN_EXTRACTED:
            lines.append(v.detail)
        else:
            lines.append(f"[{v.semester}] {v.kind}: {v.detail}")
    return lines
