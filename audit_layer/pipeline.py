"""Shared audit orchestration for the service and batch evaluation.

Original compliance and repair success are separate outcomes. A candidate
is successful only after re-verification under the same credit cap.
"""
import time

from . import explainer, repair, verifier
from .models import AuditResult, Plan, RepairOp, ViolationKind
from .repair_candidate import schedule_candidate


def course_set(plan):
    return {c for b in plan.blocks for c in b.courses} - set(plan.completed)


def candidate_operations(original, candidate):
    """Occurrence-aware remove/move log; not a minimum-edit certificate."""
    sources = {}
    for block in original.blocks:
        for course in block.courses:
            sources.setdefault(course, []).append(block.semester)
    operations = []
    for block in candidate.blocks:
        for course in block.courses:
            locations = sources[course]
            origin = block.semester if block.semester in locations else locations[0]
            locations.remove(origin)
            if origin != block.semester:
                operations.append(RepairOp(action="move", semester=origin,
                    target_semester=block.semester, course=course,
                    rationale="deferred within the original horizon to fit eligibility and capacity"))
    for course, locations in sources.items():
        operations.extend(RepairOp(action="remove", semester=sem, course=course,
            rationale="not retained by the verified scheduling candidate") for sem in locations)
    return operations


def audit_plan(plan: Plan, *, credit_cap: int = verifier.DEFAULT_CREDIT_CAP,
               do_repair: bool = True) -> AuditResult:
    timings = {}

    def measured(name, function):
        start = time.perf_counter()
        value = function()
        timings[name] = round((time.perf_counter() - start) * 1000, 3)
        return value

    violations = measured("verifier", lambda: verifier.verify(plan, credit_cap=credit_cap))
    proof = measured("explainer", lambda: explainer.explain(plan, violations))
    candidate, operations = None, []
    residual, repaired_compliant = None, None
    status = "compliant" if not violations else "not_repaired"
    strategy, handoff = "none", []
    timings.update(repair=0.0, reverify=0.0)
    temporal = any(v.kind == ViolationKind.TERM_ORDER_UNRESOLVED for v in violations)
    if temporal:
        status = "needs_review"
        handoff = ["Clarify semester years and order before eligibility or repair can be accepted."]
    elif violations and do_repair:
        candidate, operations = measured("repair", lambda: repair.repair(
            plan, violations, credit_cap=credit_cap))
        residual = measured("reverify", lambda: verifier.verify(candidate, credit_cap=credit_cap))
        strategy = "greedy"
        scheduled, _ = measured("scheduler", lambda: schedule_candidate(plan, credit_cap=credit_cap))
        if scheduled is not None:
            remaining = measured("scheduler_reverify", lambda: verifier.verify(scheduled, credit_cap=credit_cap))
            if not remaining and (residual or len(course_set(scheduled)) > len(course_set(candidate))):
                candidate, residual = scheduled, remaining
                operations = candidate_operations(plan, candidate)
                strategy = "scheduled"
        repaired_compliant = not residual
        status = "repaired" if repaired_compliant else "failed"
        if not repaired_compliant:
            handoff = ["No compliant repair was found; review the residual violations."]
    unresolved = sorted(course_set(plan) - course_set(candidate)) if candidate is not None else []
    return AuditResult(
        plan=plan, compliant=not violations, violations=violations,
        repaired_plan=candidate, repair_ops=operations, edit_distance=len(operations),
        proof_tree=proof, status=status, credit_cap=credit_cap,
        repaired_compliant=repaired_compliant, residual_violations=residual,
        timings_ms=timings,
        repair_strategy=strategy, handoff_reasons=handoff, unresolved_courses=unresolved,
    )
