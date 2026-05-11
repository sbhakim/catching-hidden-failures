"""Unit tests for the repair engine.

These tests are infra-free: every Prolog or Postgres call is mocked at
the `prolog_bridge`/`db` boundary so the test suite runs in CI without
SWI-Prolog or PostgreSQL.

Coverage:
  - idempotence on a clean plan (no violations)
  - non-mutation of the input plan (deepcopy contract)
  - per-violation-kind dispatch (UNKNOWN, DUPLICATE, PROGRAM, PREREQ,
    CREDIT_CAP, NO_PLAN_EXTRACTED)
  - fall-back-to-remove when greedy prereq move finds no eligible target
  - credit-cap drop-by-highest-credit strategy
"""
from __future__ import annotations
from unittest.mock import patch

import pytest

from audit_layer.models import (
    Plan, SemesterBlock, Violation, ViolationKind,
)
from audit_layer.repair import repair


def _plan(blocks=None, completed=None) -> Plan:
    return Plan(
        student_id=1,
        program="cs_bs",
        completed=completed or [],
        blocks=blocks or [SemesterBlock(semester="Fall 2026", courses=["COP_3530"])],
        source="test",
    )


def _viol(kind: ViolationKind, course: str | None = None, semester: str = "Fall 2026") -> Violation:
    return Violation(kind=kind, semester=semester, course=course,
                     detail=f"test {kind.value}")


# ── idempotence + non-mutation ───────────────────────────────────────

def test_repair_noop_on_clean_plan():
    """repair(P, []) returns (P, []) — empty violations is a no-op."""
    p = _plan()
    out, ops = repair(p, [])
    assert ops == []
    assert out is p          # no copy needed when there is nothing to do


def test_repair_does_not_mutate_input_plan():
    """The returned plan must be a deepcopy when any repair fires."""
    p = _plan(blocks=[SemesterBlock(semester="Fall 2026",
                                    courses=["COP_3530", "FAKE_999"])])
    viols = [_viol(ViolationKind.UNKNOWN_COURSE, "FAKE_999")]
    out, _ = repair(p, viols)
    assert out is not p
    assert p.blocks[0].courses == ["COP_3530", "FAKE_999"]   # input untouched
    assert "FAKE_999" not in out.blocks[0].courses           # output edited


# ── per-violation-kind dispatch (no infra needed) ────────────────────

def test_repair_removes_unknown_course():
    p = _plan(blocks=[SemesterBlock(semester="Fall 2026",
                                    courses=["COP_3530", "FAKE_999"])])
    viols = [_viol(ViolationKind.UNKNOWN_COURSE, "FAKE_999")]
    out, ops = repair(p, viols)
    assert "FAKE_999" not in out.blocks[0].courses
    assert any(op.action == "remove" and op.course == "FAKE_999" for op in ops)


def test_repair_removes_duplicate_of_completed():
    p = _plan(blocks=[SemesterBlock(semester="Fall 2026",
                                    courses=["MAD_2104", "COP_3530"])],
              completed=["MAD_2104"])
    viols = [_viol(ViolationKind.DUPLICATE_OF_COMPLETED, "MAD_2104")]
    out, ops = repair(p, viols)
    assert "MAD_2104" not in out.blocks[0].courses
    assert ops[0].action == "remove"


def test_repair_removes_program_requirement_unmet():
    p = _plan(blocks=[SemesterBlock(semester="Fall 2026",
                                    courses=["OUT_OF_PROGRAM_4444"])])
    viols = [_viol(ViolationKind.PROGRAM_REQUIREMENT_UNMET, "OUT_OF_PROGRAM_4444")]
    out, ops = repair(p, viols)
    assert "OUT_OF_PROGRAM_4444" not in out.blocks[0].courses
    assert ops[0].action == "remove"


def test_repair_no_plan_extracted_is_skipped():
    """A structural NO_PLAN_EXTRACTED violation is unrepairable -- the
    audit layer intentionally refuses to invent a plan. repair() should
    return no ops for this kind."""
    p = _plan(blocks=[])
    viols = [_viol(ViolationKind.NO_PLAN_EXTRACTED, course=None, semester="")]
    out, ops = repair(p, viols)
    assert ops == []


# ── PREREQ_MISSING with mocked Prolog ────────────────────────────────

def test_repair_prereq_missing_falls_back_to_remove_when_no_target():
    """When no later semester satisfies the prereqs, repair removes the
    course rather than leaving the violation unresolved."""
    p = _plan(blocks=[SemesterBlock(semester="Fall 2026",
                                    courses=["COP_4338"])])
    viols = [_viol(ViolationKind.PREREQ_MISSING, "COP_4338")]
    with patch("audit_layer.repair.prolog_bridge.is_eligible",
               return_value=False):
        out, ops = repair(p, viols)
    assert "COP_4338" not in out.blocks[0].courses
    assert any(op.action == "remove" and op.course == "COP_4338" for op in ops)


# ── CREDIT_CAP_EXCEEDED with mocked db.credits_of ────────────────────

def test_repair_credit_cap_drops_highest_credit_course():
    """When the term exceeds the credit cap, repair removes courses
    starting from the highest-credit one until the cap is met."""
    p = _plan(blocks=[SemesterBlock(semester="Fall 2026",
                                    courses=["COP_1", "COP_4", "COP_3"])])
    # Simulated credits: COP_1=3, COP_4=4, COP_3=3 -> sum=10, cap=8 -> drop COP_4
    credits = {"COP_1": 3, "COP_4": 4, "COP_3": 3}
    viols = [_viol(ViolationKind.CREDIT_CAP_EXCEEDED, course=None)]
    with patch("audit_layer.repair.db.credits_of",
               side_effect=lambda c: credits.get(c, 0)):
        out, ops = repair(p, viols, credit_cap=8)
    assert "COP_4" not in out.blocks[0].courses
    assert any(op.action == "remove" and op.course == "COP_4" for op in ops)
    # remaining courses should still fit under the cap (3 + 3 = 6)
    remaining = sum(credits[c] for c in out.blocks[0].courses)
    assert remaining <= 8
