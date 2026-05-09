"""Unit tests for the audit layer.

These tests intentionally avoid Postgres and SWI-Prolog so they can run
in CI on any machine. They cover the parser, model-level invariants, and
the verifier's structural empty-plan guard. Tests that need the curriculum
DB or program rule files belong in `scripts/smoke_test.py`, not here.

Run:
    pip install pytest
    pytest tests/
"""
from __future__ import annotations
import pytest

from audit_layer import plan_parser
from audit_layer.models import Plan, SemesterBlock, ViolationKind
from audit_layer.verifier import verify


# ── parser ──────────────────────────────────────────────────────────

def test_parser_markdown_extracts_blocks():
    raw = (
        "- **Fall 2026**: COP_3530, MAD_2104\n"
        "- **Spring 2027**: COP_3337, CDA_3102\n"
    )
    plan = plan_parser.parse(raw, student_id=1, program="cs_bs", completed=[])
    assert len(plan.blocks) == 2
    assert plan.blocks[0].semester == "Fall 2026"
    assert plan.blocks[0].courses == ["COP_3530", "MAD_2104"]
    assert plan.blocks[1].courses == ["COP_3337", "CDA_3102"]


def test_parser_json_extracts_blocks():
    payload = {
        "plan": [
            {"semester": "Fall 2026", "courses": ["cop_3530", "mad_2104"]},
            {"semester": "Spring 2027", "courses": ["cop_3337"]},
        ]
    }
    plan = plan_parser.parse(payload, student_id=1, program="cs_bs", completed=[])
    assert len(plan.blocks) == 2
    # parser must upper-case course IDs regardless of input casing
    assert plan.blocks[0].courses == ["COP_3530", "MAD_2104"]


def test_parser_free_text_falls_back():
    raw = "Take in Fall 2026: COP3530 and MAD 2104. Then in Spring 2027: COP-3337."
    plan = plan_parser.parse(raw, student_id=1, program="cs_bs", completed=[])
    assert len(plan.blocks) == 2
    assert "COP_3530" in plan.blocks[0].courses
    assert "COP_3337" in plan.blocks[1].courses


def test_parser_empty_input_returns_empty_blocks():
    plan = plan_parser.parse("", student_id=1, program="cs_bs", completed=[])
    # Tolerant contract: parser never raises; verifier flags structurally.
    assert plan.blocks == []


# ── models ──────────────────────────────────────────────────────────

def test_violation_kind_is_closed_set():
    expected = {
        "prereq_missing",
        "credit_cap_exceeded",
        "term_not_offered",
        "duplicate_of_completed",
        "one_of_group_violated",
        "unknown_course",
        "program_requirement_unmet",
        "no_plan_extracted",
    }
    assert {k.value for k in ViolationKind} == expected


# ── verifier (structural-only, no DB / no Prolog) ───────────────────

def _infra_checks() -> tuple[ViolationKind, ...]:
    """Skip every check that hits Postgres or SWI-Prolog so the verifier
    runs purely in memory. Useful for asserting non-infra behaviour."""
    return (
        ViolationKind.UNKNOWN_COURSE,
        ViolationKind.PROGRAM_REQUIREMENT_UNMET,
        ViolationKind.PREREQ_MISSING,
        ViolationKind.CREDIT_CAP_EXCEEDED,
    )


def _make_plan(blocks: list[SemesterBlock], completed: list[str] | None = None) -> Plan:
    return Plan(
        student_id=1,
        program="cs_bs",
        completed=completed or [],
        blocks=blocks,
        source="test",
    )


def test_empty_plan_emits_structural_guard():
    plan = _make_plan(blocks=[])
    viols = verify(plan)
    assert len(viols) == 1
    assert viols[0].kind == ViolationKind.NO_PLAN_EXTRACTED


def test_blocks_with_no_courses_emit_structural_guard():
    plan = _make_plan(blocks=[SemesterBlock(semester="Fall 2026", courses=[])])
    viols = verify(plan)
    assert len(viols) == 1
    assert viols[0].kind == ViolationKind.NO_PLAN_EXTRACTED


def test_duplicate_of_completed_is_detected_offline():
    plan = _make_plan(
        blocks=[SemesterBlock(semester="Fall 2026", courses=["COP_3530"])],
        completed=["COP_3530"],
    )
    viols = verify(plan, skip_checks=_infra_checks())
    assert len(viols) == 1
    assert viols[0].kind == ViolationKind.DUPLICATE_OF_COMPLETED
    assert viols[0].course == "COP_3530"


def test_skip_checks_disables_a_family():
    plan = _make_plan(
        blocks=[SemesterBlock(semester="Fall 2026", courses=["COP_3530"])],
        completed=["COP_3530"],
    )
    viols = verify(
        plan,
        skip_checks=_infra_checks() + (ViolationKind.DUPLICATE_OF_COMPLETED,),
    )
    assert viols == []


def test_snapshot_blocks_intra_term_unlock():
    """A peer course in the same semester must not satisfy a duplicate
    check on a later course in that same semester — the snapshot of
    completed courses freezes at the start of each block."""
    plan = _make_plan(
        blocks=[
            SemesterBlock(semester="Fall 2026", courses=["COP_3530", "COP_3530"]),
        ],
    )
    # COP_3530 listed twice in the same term: the second one is a duplicate
    # of itself only after the first is added to `taken` — but `taken` is
    # advanced *after* the block, not within it, so the second occurrence
    # is treated as another fresh course rather than a duplicate-of-completed.
    viols = verify(plan, skip_checks=_infra_checks())
    assert all(v.kind != ViolationKind.DUPLICATE_OF_COMPLETED for v in viols)
