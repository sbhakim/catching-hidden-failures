"""FastAPI endpoint exposing the audit-layer.

Endpoints:
    POST /audit         — verify + (optionally) repair a plan in one call
    POST /parse         — turn raw LLM output into a normalized Plan

The HTTP surface is intentionally small: callers POST a `plan` (any of
JSON dict / markdown / free text), an identifying `student_id`, and either
explicit `program` + `completed` lists or rely on the service to fill them
in from the curriculum DB. Everything else (verifier behaviour, repair
strategy, explainer output) is reached through this entry point — there is
no second "expert" API for power users.

Run:
    uvicorn audit_layer.api:app --host 127.0.0.1 --port 8020 --reload
"""
from __future__ import annotations
from typing import Any, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import db, plan_parser, verifier, repair as repair_mod, explainer
from .models import Plan, AuditResult


class AuditRequest(BaseModel):
    student_id: int
    program: Optional[str] = None
    completed: Optional[list[str]] = None
    plan: Any  # JSON dict, markdown string, or free-text
    source: str = "unknown"
    credit_cap: int = verifier.DEFAULT_CREDIT_CAP
    repair: bool = True


app = FastAPI(title="Audit-Layer · Verify · Repair · Explain")


def _resolve_context(req: AuditRequest) -> tuple[str, list[str]]:
    """Pull program + completed from DB if caller didn't supply them.

    Caller-supplied values always win — this lets test harnesses and the
    eval scripts pass synthetic histories without touching Postgres. We
    only fall back to the DB when the caller leaves the field unset.
    """
    if req.program and req.completed is not None:
        return req.program, req.completed
    prog, taken = db.student_context(req.student_id)
    return req.program or (prog or ""), req.completed if req.completed is not None else taken


@app.post("/audit", response_model=AuditResult)
def audit(req: AuditRequest):
    program, completed = _resolve_context(req)
    if not program:
        raise HTTPException(404, f"No active program for student {req.student_id}")

    plan = plan_parser.parse(
        req.plan,
        student_id=req.student_id,
        program=program.lower().replace("-", "_"),
        completed=[c.upper() for c in completed],
        source=req.source,
    )

    violations = verifier.verify(plan, credit_cap=req.credit_cap)
    proof = explainer.explain(plan, violations)

    repaired_plan = None
    repair_ops = []
    if req.repair and violations:
        repaired_plan, repair_ops = repair_mod.repair(
            plan, violations, credit_cap=req.credit_cap
        )

    return AuditResult(
        plan=plan,
        compliant=not violations,
        violations=violations,
        repaired_plan=repaired_plan,
        repair_ops=repair_ops,
        edit_distance=len(repair_ops),
        proof_tree=proof,
    )


@app.post("/parse", response_model=Plan)
def parse_endpoint(req: AuditRequest):
    program, completed = _resolve_context(req)
    return plan_parser.parse(
        req.plan,
        student_id=req.student_id,
        program=program.lower().replace("-", "_"),
        completed=[c.upper() for c in completed],
        source=req.source,
    )
