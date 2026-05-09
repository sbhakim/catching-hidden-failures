"""Postgres helpers for the audit layer.

Reads from the curriculum schema in ``db/schema.sql``. Connections are
short-lived: the audit layer is a low-throughput verification service, so
the cost of opening a connection per call is negligible compared to the
SWI-Prolog subprocess.

`credits_of` and `course_exists` are LRU-cached because the verifier hits
them once per course per plan and the catalog is effectively static. The
cache is per-process — restart to pick up catalog edits.
"""
from __future__ import annotations
import os, psycopg2
from functools import lru_cache


def _conn():
    return psycopg2.connect(
        dbname=os.getenv("POSTGRES_DB", "course_advisor"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "advising_dev_password"),
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
    )


@lru_cache(maxsize=2048)
def credits_of(course_id: str) -> int:
    """Return credits for a course; 0 if unknown."""
    with _conn() as c, c.cursor() as cur:
        cur.execute("SELECT credits FROM courses WHERE course_id=%s", (course_id.upper(),))
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0


@lru_cache(maxsize=2048)
def course_exists(course_id: str) -> bool:
    with _conn() as c, c.cursor() as cur:
        cur.execute("SELECT 1 FROM courses WHERE course_id=%s", (course_id.upper(),))
        return cur.fetchone() is not None


def student_context(user_id: int) -> tuple[str | None, list[str]]:
    """(program_id, completed_course_ids) for an active student."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """
            SELECT mo.program_id
              FROM user_program up
              JOIN program_offerings mo USING(program_id)
             WHERE up.user_id=%s
          ORDER BY (up.status='active') DESC, up.start_date DESC
             LIMIT 1
            """,
            (user_id,),
        )
        row = cur.fetchone()
        program = row[0] if row else None
        cur.execute(
            "SELECT course_id FROM user_course WHERE user_id=%s AND status='completed'",
            (user_id,),
        )
        taken = [r[0].upper() for r in cur.fetchall()]
    return program, taken


def program_required_courses(program_id: str) -> list[str]:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT course_id FROM program_course WHERE program_id=%s AND is_core=TRUE",
            (program_id,),
        )
        return [r[0].upper() for r in cur.fetchall()]


@lru_cache(maxsize=128)
def program_courses(program_id: str) -> list[str]:
    """All courses attached to a program offering."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT course_id FROM program_course WHERE program_id=%s",
            (program_id,),
        )
        return [r[0].upper() for r in cur.fetchall()]
