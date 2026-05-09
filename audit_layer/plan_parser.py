"""Parse arbitrary LLM advising outputs into a normalized `Plan`.

Supports three input modes, tried in order of fidelity:
  1. JSON dict (preferred — when the LLM was asked for structured output)
  2. Markdown bullets ("- **Fall 2026**: COP_3530, MAD_2104, ...")
  3. Free text — extract semester mentions + course codes via regex

The parser is intentionally **tolerant**: when no semester block can be
recovered it returns a `Plan` with `blocks=[]` rather than raising. This
keeps parser failures inside the verification framework — they surface as
the structural NO_PLAN_EXTRACTED violation, are counted in metrics, and
flow through the same audit interface as any other failure. Raising here
would force every caller to special-case parse failure separately.
"""
from __future__ import annotations
import re
from typing import Optional
from .models import Plan, SemesterBlock

# Course-code regex accepts the three styles LLMs actually produce: "COP3530",
# "COP_3530", "COP-3530", "COP 3530". 2–4 letters of dept + 3–4 digits of
# number is loose enough to catch both 3-digit (e.g. ENC_101) and 4-digit
# courses without false-matching things like file paths.
_COURSE_RX = re.compile(r"\b([A-Z]{2,4})[_\-\s]?(\d{3,4})\b")
_SEMESTER_RX = re.compile(
    r"\b(Fall|Spring|Summer|Autumn|Winter)\s*(\d{4})?\b", re.I
)
# Markdown bullet headers like "- **Fall 2026**: COP_3530, MAD_2104".
# We accept "*", "-", or "•" bullet, optional bold markers, optional year,
# and either ":" or "-" as the separator before the course list.
_BULLET_HEADER_RX = re.compile(
    r"^\s*[-\*•]\s*\*\*?(Fall|Spring|Summer|Autumn|Winter)\s*(\d{4})?\*\*?\s*[:\-]?\s*(.*)$",
    re.I | re.M,
)


def _normalize_course(prefix: str, num: str) -> str:
    return f"{prefix.upper()}_{num}"


def extract_courses(text: str) -> list[str]:
    return [_normalize_course(p, n) for p, n in _COURSE_RX.findall(text.upper())]


def parse_markdown(text: str, student_id: int, program: str,
                   completed: list[str], source: str = "unknown") -> Plan:
    """Parse markdown-style plans like '- **Fall 2026**: COP_3530, MAD_2104'."""
    blocks: list[SemesterBlock] = []
    seen_courses: set[str] = set()
    for m in _BULLET_HEADER_RX.finditer(text):
        sem_name, year, body = m.group(1), m.group(2), m.group(3)
        sem_label = f"{sem_name.title()} {year}" if year else sem_name.title()
        courses = []
        for cid in extract_courses(body):
            if cid not in seen_courses:
                seen_courses.add(cid)
                courses.append(cid)
        blocks.append(SemesterBlock(semester=sem_label, courses=courses))
    return Plan(
        student_id=student_id,
        program=program,
        completed=completed,
        blocks=blocks,
        source=source,
        raw_text=text,
    )


def parse_json(payload: dict, student_id: int, program: str,
               completed: list[str], source: str = "unknown") -> Plan:
    """Parse a structured JSON output. Expected shape:
        {"plan": [{"semester": "Fall 2026", "courses": ["COP_3530", ...]}]}
    """
    raw_blocks = payload.get("plan") or payload.get("blocks") or []
    blocks = [
        SemesterBlock(
            semester=b["semester"],
            courses=[c.upper() for c in b.get("courses", [])],
        )
        for b in raw_blocks
    ]
    return Plan(
        student_id=student_id,
        program=program,
        completed=completed,
        blocks=blocks,
        source=source,
    )


def parse_free_text(text: str, student_id: int, program: str,
                    completed: list[str], source: str = "unknown") -> Plan:
    """Last-resort regex-only parse: split on semester mentions, take all
    courses between consecutive mentions."""
    matches = list(_SEMESTER_RX.finditer(text))
    blocks: list[SemesterBlock] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sem_label = (
            f"{m.group(1).title()} {m.group(2)}" if m.group(2)
            else m.group(1).title()
        )
        courses = list(dict.fromkeys(extract_courses(text[start:end])))
        if courses:
            blocks.append(SemesterBlock(semester=sem_label, courses=courses))
    return Plan(
        student_id=student_id,
        program=program,
        completed=completed,
        blocks=blocks,
        source=source,
        raw_text=text,
    )


def parse(text_or_json, student_id: int, program: str,
          completed: list[str], source: str = "unknown") -> Plan:
    """Top-level entry point. Tries JSON first, falls back to markdown,
    then free text."""
    if isinstance(text_or_json, dict):
        return parse_json(text_or_json, student_id, program, completed, source)
    text = str(text_or_json)
    md_plan = parse_markdown(text, student_id, program, completed, source)
    if md_plan.blocks:
        return md_plan
    return parse_free_text(text, student_id, program, completed, source)
