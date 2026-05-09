"""Constraint verifier.

Walks a plan semester-by-semester, threading completed-courses forward, and
records every violation. Each check is independent and additive — adding a new
constraint family means appending one helper. The `skip_checks` argument is
what makes the leave-one-out ablation in `evaluation/run_ablations.py` a
one-liner rather than a fork of this file.

Per-course ordering inside a semester is *short-circuit*: once a course fails
one family (e.g. UNKNOWN_COURSE), downstream checks on that same course are
skipped, because they would either be undefined (you cannot check prereqs of
a non-existent course) or redundant. The verifier still runs over the
remaining courses in the same semester.
"""
from __future__ import annotations
from .models import Plan, SemesterBlock, Violation, ViolationKind
from . import db, prolog_bridge


DEFAULT_CREDIT_CAP = 18  # university-typical max; per-call override supported.


def _check_unknown_course(course: str) -> Violation | None:
    if not db.course_exists(course):
        return Violation(
            kind=ViolationKind.UNKNOWN_COURSE,
            semester="",
            course=course,
            detail=f"{course} is not in the catalog",
        )
    return None


def _check_duplicate(course: str, completed: set[str], block_sem: str) -> Violation | None:
    if course in completed:
        return Violation(
            kind=ViolationKind.DUPLICATE_OF_COMPLETED,
            semester=block_sem,
            course=course,
            detail=f"{course} is already completed",
        )
    return None


def _program_db_id(program: str) -> str:
    return program.upper().replace("_", "-")


def _check_program_course(course: str, program: str, block_sem: str) -> Violation | None:
    if not program:
        return None
    if not prolog_bridge.in_program(course, program):
        return Violation(
            kind=ViolationKind.PROGRAM_REQUIREMENT_UNMET,
            semester=block_sem,
            course=course,
            detail=f"{course} is not listed for program {_program_db_id(program)}",
        )
    return None


def _check_prereq(course: str, taken_so_far: list[str], program: str,
                  block_sem: str) -> Violation | None:
    if prolog_bridge.is_eligible(taken_so_far, course, program=program):
        return None
    missing = prolog_bridge.prereqs_of(course, program=program)
    missing = [m for m in missing if m not in taken_so_far]
    return Violation(
        kind=ViolationKind.PREREQ_MISSING,
        semester=block_sem,
        course=course,
        detail=f"{course} requires {missing or '<unknown>'}; not satisfied at start of {block_sem}",
    )


def _check_credit_cap(block: SemesterBlock, cap: int) -> Violation | None:
    total = sum(db.credits_of(c) for c in block.courses)
    if total > cap:
        return Violation(
            kind=ViolationKind.CREDIT_CAP_EXCEEDED,
            semester=block.semester,
            course=None,
            detail=f"{total} credits exceeds cap of {cap}",
        )
    return None


def verify(plan: Plan, *, credit_cap: int = DEFAULT_CREDIT_CAP,
           skip_checks: tuple[ViolationKind, ...] = ()) -> list[Violation]:
    """Return all violations in `plan`. Empty list ↔ compliant.

    `skip_checks` lets evaluations ablate individual constraint types.
    """
    violations: list[Violation] = []
    taken: set[str] = set(c.upper() for c in plan.completed)

    # Structural fail-closed guard. If the parser could not recover any
    # semester block (or all blocks are empty) we emit a single
    # NO_PLAN_EXTRACTED violation and stop. This prevents a silent
    # "compliant" verdict on a plan the audit layer never actually saw.
    if ViolationKind.NO_PLAN_EXTRACTED not in skip_checks:
        has_courses = any(block.courses for block in plan.blocks)
        if not plan.blocks or not has_courses:
            return [
                Violation(
                    kind=ViolationKind.NO_PLAN_EXTRACTED,
                    semester="",
                    course=None,
                    detail="No semester/course plan could be extracted",
                )
            ]

    for block in plan.blocks:
        # `snapshot` freezes the completed-set at the *start* of this semester.
        # Prereq checks read from `snapshot`, not from `taken`, so two courses
        # listed in the same term cannot unlock each other — students cannot
        # complete a co-listed prereq mid-term to satisfy a peer course.
        snapshot = list(taken)
        for course in block.courses:
            course = course.upper()

            if ViolationKind.UNKNOWN_COURSE not in skip_checks:
                if v := _check_unknown_course(course):
                    v.semester = block.semester
                    violations.append(v)
                    continue  # downstream checks meaningless if course unknown

            if ViolationKind.DUPLICATE_OF_COMPLETED not in skip_checks:
                if v := _check_duplicate(course, taken, block.semester):
                    violations.append(v)
                    continue

            if ViolationKind.PROGRAM_REQUIREMENT_UNMET not in skip_checks:
                if v := _check_program_course(course, plan.program, block.semester):
                    violations.append(v)
                    continue

            if ViolationKind.PREREQ_MISSING not in skip_checks:
                if v := _check_prereq(course, snapshot, plan.program, block.semester):
                    violations.append(v)

        # 2) per-block checks
        if ViolationKind.CREDIT_CAP_EXCEEDED not in skip_checks:
            if v := _check_credit_cap(block, credit_cap):
                violations.append(v)

        # 3) advance "taken" with this block's courses for the next iteration
        for c in block.courses:
            taken.add(c.upper())

    return violations
