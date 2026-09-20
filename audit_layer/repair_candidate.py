"""Fixed-horizon candidate used by the shared audit pipeline.

Uses encoded eligibility, including concurrent co-requisites.
No optimality or full degree-completion claim.
"""
from copy import deepcopy

from . import db, prolog_bridge
from .terms import ordered_terms


def schedule_candidate(plan, *, credit_cap=18):
    """Defer eligible original courses; never advance or invent courses.

    Stable original ordering resolves capacity contention. Only successfully
    scheduled courses enter the next term's completed set. Unscheduled courses
    remain explicit in the diagnostic output. Invalid term labels abstain.
    """
    if not ordered_terms(plan):
        return None, [{"reason": "ambiguous_or_nonchronological_terms"}]
    result = deepcopy(plan)
    taken = set(plan.completed)
    pending, seen, dropped = [], set(taken), []
    for index, block in enumerate(result.blocks):
        original = list(block.courses)
        block.courses = []
        block.credits = None
        for course in original:
            if course in seen:
                dropped.append({"course": course, "term": index, "reason": "duplicate"})
                continue
            seen.add(course)
            if not db.course_exists(course) or not prolog_bridge.in_program(course, plan.program):
                dropped.append({"course": course, "term": index, "reason": "catalog_or_membership"})
                continue
            pending.append((course, index))
        remaining, credits = [], 0
        feasible = prolog_bridge.eligible_term(taken, [c for c, _ in pending], plan.program)
        for course, origin in pending:
            eligible = course in feasible
            weight = db.credits_of(course)
            if eligible and credits + weight <= credit_cap:
                block.courses.append(course)
                credits += weight
            else:
                remaining.append((course, origin))
        # Capacity trimming may remove a concurrent supporter. Prune again
        # rather than accepting a bundle whose co-requisites no longer fit.
        fitted = prolog_bridge.eligible_term(taken, block.courses, plan.program)
        pending = [(c, origin) for c, origin in pending if c not in fitted]
        block.courses = [c for c in block.courses if c in fitted]
        taken.update(block.courses)
    dropped.extend({"course": c, "term": origin, "reason": "unscheduled_within_horizon"}
                   for c, origin in pending)
    return result, dropped
