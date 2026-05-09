"""Minimum-edit plan repair.

V0 strategy: greedy local fixes. For each violation, apply the cheapest
single-edit operation that resolves it (without introducing a new one if
possible). The contract is intentionally narrow:

    repair(plan, violations) -> (repaired_plan, ops)

so an alternative strategy (ILP, beam search, LLM-guided proposals with this
verifier as the oracle) can drop in without touching the rest of the layer.

Two non-obvious invariants the caller relies on:
  1. The output `Plan` is always a deepcopy — `plan` is never mutated.
  2. A compliant input returns `(plan, [])` unchanged — the function is a
     no-op on empty `violations`. This makes `verify -> repair -> verify`
     idempotent on already-clean plans.
"""
from __future__ import annotations
from copy import deepcopy
from .models import Plan, RepairOp, Violation, ViolationKind
from . import db, prolog_bridge, verifier as _verifier


def _can_swap_to_earlier(course: str, plan: Plan, target_idx: int) -> int | None:
    """Find an earlier semester index where `course` would be eligible
    given courses completed up to and including that semester."""
    completed = set(c.upper() for c in plan.completed)
    for idx in range(target_idx):
        if prolog_bridge.is_eligible(list(completed), course, program=plan.program):
            return idx
        completed.update(c.upper() for c in plan.blocks[idx].courses)
    return None


def _remove_course(plan: Plan, semester: str, course: str) -> RepairOp:
    for block in plan.blocks:
        if block.semester == semester and course in block.courses:
            block.courses.remove(course)
            return RepairOp(
                action="remove",
                semester=semester,
                course=course,
                rationale="violated constraint, no eligible move target",
            )
    return RepairOp(action="remove", semester=semester, course=course,
                    rationale="course not present (no-op)")


def _move_course(plan: Plan, course: str, from_sem: str, to_sem: str) -> RepairOp:
    for block in plan.blocks:
        if block.semester == from_sem and course in block.courses:
            block.courses.remove(course)
    for block in plan.blocks:
        if block.semester == to_sem:
            if course not in block.courses:
                block.courses.append(course)
            break
    return RepairOp(
        action="move",
        semester=from_sem,
        target_semester=to_sem,
        course=course,
        rationale="moved to earliest semester where prereqs are satisfied",
    )


def repair(plan: Plan, violations: list[Violation],
           *, credit_cap: int = _verifier.DEFAULT_CREDIT_CAP) -> tuple[Plan, list[RepairOp]]:
    """Greedy repair pass. Returns (repaired_plan, ops). Idempotent: a
    compliant plan returns unchanged with [] ops."""
    if not violations:
        return plan, []

    repaired = deepcopy(plan)
    ops: list[RepairOp] = []
    sem_index = {b.semester: i for i, b in enumerate(repaired.blocks)}

    for v in violations:
        if v.kind == ViolationKind.UNKNOWN_COURSE and v.course:
            ops.append(_remove_course(repaired, v.semester, v.course))

        elif v.kind == ViolationKind.DUPLICATE_OF_COMPLETED and v.course:
            ops.append(_remove_course(repaired, v.semester, v.course))

        elif v.kind == ViolationKind.PROGRAM_REQUIREMENT_UNMET and v.course:
            ops.append(_remove_course(repaired, v.semester, v.course))

        elif v.kind == ViolationKind.PREREQ_MISSING and v.course:
            # Strategy: push the course later. Walk forward from the
            # offending semester, advancing the simulated completed-set as
            # we cross each block, and place the course at the first
            # semester where its prereqs are satisfied. If no such
            # semester exists in the plan, fall back to removal.
            cur_idx = sem_index.get(v.semester, 0)
            completed = set(c.upper() for c in repaired.completed)
            for j in range(cur_idx):
                completed.update(c.upper() for c in repaired.blocks[j].courses)
            placed = False
            for j in range(cur_idx, len(repaired.blocks)):
                if prolog_bridge.is_eligible(list(completed), v.course,
                                             program=repaired.program):
                    if j != cur_idx:
                        ops.append(_move_course(
                            repaired, v.course,
                            v.semester, repaired.blocks[j].semester,
                        ))
                    placed = True
                    break
                completed.update(c.upper() for c in repaired.blocks[j].courses)
            if not placed:
                ops.append(_remove_course(repaired, v.semester, v.course))

        elif v.kind == ViolationKind.CREDIT_CAP_EXCEEDED:
            # drop the highest-credit course in that block until under cap
            block = next((b for b in repaired.blocks if b.semester == v.semester), None)
            if block:
                while sum(db.credits_of(c) for c in block.courses) > credit_cap and block.courses:
                    victim = max(block.courses, key=db.credits_of)
                    block.courses.remove(victim)
                    ops.append(RepairOp(
                        action="remove",
                        semester=v.semester,
                        course=victim,
                        rationale=f"trimmed to fit credit cap of {credit_cap}",
                    ))

        elif v.kind == ViolationKind.NO_PLAN_EXTRACTED:
            # A missing plan cannot be repaired without a generator; expose it
            # as an audit failure rather than inventing courses.
            continue

        # other violation kinds (term offerings, one-of, program reqs):
        # to be handled in v1.

    return repaired, ops
