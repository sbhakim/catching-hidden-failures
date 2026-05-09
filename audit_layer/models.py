"""Pydantic types — the stable boundary between parser, verifier, repair,
and explainer.

Any plan generator (commercial APIs, local HF models, tool-calling agents)
is normalized into `Plan` before audit, so downstream modules never branch
on the LLM's output format.

`ViolationKind` is intentionally a closed enum: every new constraint
family must be declared here, which forces the ablation runner, the
metrics script, and the repair branches to acknowledge it. A free-form
string would let a new failure mode slip through unmeasured.
"""
from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class ViolationKind(str, Enum):
    PREREQ_MISSING = "prereq_missing"
    CREDIT_CAP_EXCEEDED = "credit_cap_exceeded"
    TERM_NOT_OFFERED = "term_not_offered"
    DUPLICATE_OF_COMPLETED = "duplicate_of_completed"
    ONE_OF_GROUP_VIOLATED = "one_of_group_violated"
    UNKNOWN_COURSE = "unknown_course"
    PROGRAM_REQUIREMENT_UNMET = "program_requirement_unmet"
    NO_PLAN_EXTRACTED = "no_plan_extracted"


class SemesterBlock(BaseModel):
    """One semester's worth of courses in a plan."""
    semester: str = Field(..., description="e.g. 'Fall 2026'")
    courses: list[str] = Field(default_factory=list, description="course IDs (e.g. 'COP_3530')")
    credits: Optional[int] = None


class Plan(BaseModel):
    """Normalized advising plan."""
    student_id: int
    program: str = Field(..., description="program atom, e.g. 'cs_bs'")
    completed: list[str] = Field(default_factory=list)
    blocks: list[SemesterBlock]
    source: str = Field("unknown", description="LLM identifier (e.g. 'qwen2.5:7b', 'gpt-4o', 'claude-opus-4-7').")
    raw_text: Optional[str] = None


class Violation(BaseModel):
    kind: ViolationKind
    semester: str
    course: Optional[str] = None
    detail: str
    proof: list[str] = Field(default_factory=list, description="resolution trace lines from Prolog")


class RepairOp(BaseModel):
    """A single edit applied to fix a plan."""
    action: str = Field(..., description="'remove', 'add', 'move', 'swap'")
    semester: str
    course: Optional[str] = None
    target_semester: Optional[str] = None
    rationale: str


class AuditResult(BaseModel):
    plan: Plan
    compliant: bool
    violations: list[Violation] = Field(default_factory=list)
    repaired_plan: Optional[Plan] = None
    repair_ops: list[RepairOp] = Field(default_factory=list)
    edit_distance: int = 0
    proof_tree: list[str] = Field(default_factory=list)
