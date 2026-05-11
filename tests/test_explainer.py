"""Unit tests for the explainer.

The explainer turns Violation objects into human-readable strings. All
branches except PREREQ_MISSING are pure string formatting; the prereq
branch is exercised via a mocked prolog_bridge.prereqs_of so the test
suite stays infra-free.
"""
from __future__ import annotations
from unittest.mock import patch

from audit_layer.models import (
    Plan, SemesterBlock, Violation, ViolationKind,
)
from audit_layer.explainer import explain


def _plan(completed=None) -> Plan:
    return Plan(
        student_id=1,
        program="cs_bs",
        completed=completed or [],
        blocks=[SemesterBlock(semester="Fall 2026", courses=["COP_3530"])],
        source="test",
    )


def _viol(kind: ViolationKind, course: str | None = None,
          semester: str = "Fall 2026", detail: str = "") -> Violation:
    return Violation(
        kind=kind,
        semester=semester,
        course=course,
        detail=detail or f"test {kind.value}",
    )


# ── empty input ──────────────────────────────────────────────────────

def test_explain_empty_violations_returns_empty_list():
    assert explain(_plan(), []) == []


# ── string-only branches (no infra needed) ───────────────────────────

def test_explain_unknown_course_mentions_catalog():
    out = explain(_plan(), [_viol(ViolationKind.UNKNOWN_COURSE, "FAKE_999")])
    assert len(out) == 1
    assert "FAKE_999" in out[0]
    assert "catalog" in out[0].lower()


def test_explain_duplicate_of_completed_mentions_history():
    out = explain(_plan(completed=["MAD_2104"]),
                  [_viol(ViolationKind.DUPLICATE_OF_COMPLETED, "MAD_2104")])
    assert len(out) == 1
    assert "MAD_2104" in out[0]
    # the explanation references prior completion in some form
    assert any(w in out[0].lower() for w in ("history", "already", "completed"))


def test_explain_credit_cap_exceeded_uses_violation_detail():
    """For CREDIT_CAP, the explainer passes the verifier's `detail` field
    through verbatim because the message ('N credits exceeds cap of M')
    is already self-describing."""
    out = explain(
        _plan(),
        [_viol(ViolationKind.CREDIT_CAP_EXCEEDED, course=None,
               detail="22 credits exceeds cap of 18")],
    )
    assert len(out) == 1
    assert "22" in out[0] and "18" in out[0]


def test_explain_program_requirement_unmet_passes_detail():
    out = explain(_plan(),
                  [_viol(ViolationKind.PROGRAM_REQUIREMENT_UNMET, "COP_4540",
                         detail="COP_4540 is not listed for program CS-BS")])
    assert len(out) == 1
    assert "COP_4540" in out[0]


def test_explain_no_plan_extracted_passes_detail():
    out = explain(_plan(),
                  [_viol(ViolationKind.NO_PLAN_EXTRACTED, course=None,
                         semester="",
                         detail="No semester/course plan could be extracted")])
    assert len(out) == 1
    assert "plan" in out[0].lower()


# ── PREREQ_MISSING with mocked Prolog ────────────────────────────────

def test_explain_prereq_missing_renders_chain():
    """When prereqs_of returns a non-empty chain, the explainer formats
    it as an arrow-joined path. Uses the immediate-prereq tree branch."""
    # COP_4338 requires COP_3530; COP_3530 requires COP_3337.
    def fake_prereqs_of(course, program=None):
        return {
            "COP_4338": ["COP_3530"],
            "COP_3530": ["COP_3337"],
            "COP_3337": [],
        }.get(course, [])

    with patch("audit_layer.explainer.prolog_bridge.prereqs_of",
               side_effect=fake_prereqs_of):
        out = explain(
            _plan(completed=[]),
            [_viol(ViolationKind.PREREQ_MISSING, "COP_4338")],
        )

    assert len(out) == 1
    line = out[0]
    # The explanation should mention the blocked course and the chain it
    # is blocked by, joined by the arrow glyph the explainer uses.
    assert "COP_4338" in line
    assert "→" in line or "->" in line
    assert "COP_3337" in line and "COP_3530" in line


def test_explain_prereq_missing_falls_back_when_tree_is_empty():
    """When the immediate-prereq tree returns nothing (e.g., the program
    file declares prereqs only via needed_chain/3), the explainer falls
    back to the program's own predicate. We mock run_prolog so that path
    returns an empty chain too, and verify the explainer still produces a
    diagnostic line rather than raising."""
    with patch("audit_layer.explainer.prolog_bridge.prereqs_of",
               return_value=[]), \
         patch("audit_layer.explainer.prolog_bridge.run_prolog",
               return_value=("[]", "")), \
         patch("audit_layer.explainer.prolog_bridge.atoms_from_list_literal",
               return_value=[]):
        out = explain(
            _plan(completed=[]),
            [_viol(ViolationKind.PREREQ_MISSING, "COP_4338")],
        )
    assert len(out) == 1
    assert "COP_4338" in out[0]
