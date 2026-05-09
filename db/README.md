# Curriculum database

PostgreSQL backing store for the audit layer. Holds the course catalog,
program offerings, and per-student state.

## What ships in this repo

`schema.sql` is the **structure only**. It declares ~22 tables but
contains no rows. Applying it gives you an empty advising DB; the
verifier and the smoke test will not produce useful output until the
relevant tables are populated for your institution.

```bash
createdb course_advisor
psql -d course_advisor -f db/schema.sql
```

## What the audit layer actually reads

Most of the schema exists for related advising features (career advice,
job-skill matching, conversation history) that the audit layer does not
touch. Only the following tables are required for the audit pipeline to
return a useful verdict:

| Table | Read by | Purpose |
|---|---|---|
| `courses`              | `db.credits_of`, `db.course_exists` | course catalog + credit hours |
| `program_offerings`    | `db.student_context` | catalog of programs |
| `user_program`         | `db.student_context` | which student is in which program |
| `user_course`          | `db.student_context` | per-student completed courses |
| `program_course`       | `db.program_courses`, `db.program_required_courses` | which courses belong to a program |

The remaining tables can stay empty; the audit layer will not query them.

## Populating for evaluation

The frozen JSONL snapshots in `evaluation/audited/` already contain the
parsed plans, violations, and repair edits used by the paper, so
**recomputing Table 1 and Table 3 does not require any DB rows** — see
`make metrics` and `make ablation`. The DB is only needed to:

- regenerate raw plans from an LLM (`evaluation/run_llm.py` calls
  `db.student_context` to look up each student's program and history),
- run the smoke test end-to-end (`scripts/smoke_test.py` audits a
  hard-coded student id `4942915`), or
- serve live audit requests via the FastAPI endpoint.

For these paths you need data in `users_students`, `user_program`,
`user_course`, `program_offerings`, `program_course`, and `courses`.
Loading institution-specific catalogs is out of scope for this artifact
and discussed in the paper's *Limitations and Future Directions*.

## Connection settings

Defaults come from environment variables (see `.env.example`); the
service will fall back to `localhost:5432` with database `course_advisor`
and user `postgres` if nothing is set.
