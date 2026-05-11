"""End-to-end snapshot regression test.

Audits one synthetic canary plan through the full pipeline (parse ->
verify -> repair -> explain) and compares the result against a committed
JSON fixture. Catches silent behaviour drift introduced by future
commits to the verifier, repair engine, or explainer.

Unlike the per-module tests in this directory, this one runs against the
*real* SWI-Prolog backend and Postgres catalog. It is therefore marked
to auto-skip when either is unavailable, so the default `pytest tests/`
invocation (which CI runs without infra) stays green.

Workflow:
  - first time / after an intentional behaviour change:
        UPDATE_SNAPSHOT=1 pytest tests/test_snapshot.py
        # re-commit tests/fixtures/canary_audit.json
  - regression check (regular CI / dev loop):
        pytest tests/test_snapshot.py
"""
from __future__ import annotations
import json
import os
import shutil
import socket
from pathlib import Path

import pytest

from audit_layer import (
    plan_parser,
    verifier,
    repair as repair_mod,
    explainer,
    prolog_bridge,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "canary_audit.json"


# ──────────────────────────────────────────────────────────────────────
# Infrastructure detection — auto-skip if either dependency is missing.
# Keeping CI green when run on a machine without swipl / Postgres.
# ──────────────────────────────────────────────────────────────────────

def _swipl_available() -> bool:
    return shutil.which("swipl") is not None


def _postgres_available() -> bool:
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


# ──────────────────────────────────────────────────────────────────────
# Canary plan
# ──────────────────────────────────────────────────────────────────────

CANARY_PLAN = (
    "- **Fall 2026**: COP_3530, MAD_2104\n"
    "- **Spring 2027**: COP_9999\n"
)
CANARY_PROGRAM = "cs_bs"
CANARY_COMPLETED = ["MAD_2104"]            # makes the duplicate fire
CANARY_STUDENT_ID = 4942915

# Designed to exercise three independent constraint families in one plan:
#   - prereq_missing      : COP_3530 in Fall 2026 with no prereqs satisfied
#   - duplicate_of_completed : MAD_2104 already in the completed list
#   - unknown_course      : COP_9999 is not in the catalog


def _summarize(plan, violations, ops, proof, residual) -> dict:
    """Project the audit result to a stable, comparable shape.
    Drops timestamps and free-text; sorts where order is irrelevant."""
    return {
        "n_violations":           len(violations),
        "violation_kinds":        sorted({v.kind.value for v in violations}),
        "violations_by_course":   sorted(
            [v.kind.value, v.course or ""] for v in violations
        ),
        "repair_ops":             sorted(
            [op.action, op.course or ""] for op in ops
        ),
        "n_explanations":         len(proof),
        "compliant_after_repair": not residual,
        "residual_kinds":         sorted({v.kind.value for v in residual}),
    }


def _run_canary() -> dict:
    """Run the full audit pipeline on the canary plan and return a
    snapshot-shaped summary."""
    prolog_bridge.clear_cache()
    plan = plan_parser.parse(
        CANARY_PLAN,
        student_id=CANARY_STUDENT_ID,
        program=CANARY_PROGRAM,
        completed=CANARY_COMPLETED,
        source="canary",
    )
    violations = verifier.verify(plan)
    proof = explainer.explain(plan, violations)
    repaired_plan, ops = (None, [])
    residual: list = []
    if violations:
        repaired_plan, ops = repair_mod.repair(plan, violations)
        residual = verifier.verify(repaired_plan)
    return _summarize(plan, violations, ops, proof, residual)


# ──────────────────────────────────────────────────────────────────────
# Test
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not _swipl_available(),
                    reason="requires SWI-Prolog on PATH")
@pytest.mark.skipif(not _postgres_available(),
                    reason="requires Postgres reachable at POSTGRES_HOST/PORT")
def test_canary_audit_matches_snapshot():
    summary = _run_canary()

    if os.environ.get("UPDATE_SNAPSHOT") == "1":
        FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE_PATH.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n"
        )
        return                              # pass: fixture regenerated

    if not FIXTURE_PATH.exists():
        pytest.fail(
            f"snapshot fixture missing at {FIXTURE_PATH}.\n"
            "Regenerate it by running:\n"
            "    UPDATE_SNAPSHOT=1 pytest tests/test_snapshot.py"
        )

    expected = json.loads(FIXTURE_PATH.read_text())
    assert summary == expected, (
        f"\naudit output diverged from {FIXTURE_PATH.name}:\n"
        f"  expected: {json.dumps(expected, indent=2, sort_keys=True)}\n"
        f"  actual:   {json.dumps(summary, indent=2, sort_keys=True)}\n"
        "If this divergence is intentional, regenerate the fixture with:\n"
        "    UPDATE_SNAPSHOT=1 pytest tests/test_snapshot.py"
    )
