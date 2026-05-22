# Portability

This document quantifies what is required to deploy the audit layer at a
second institution. It answers four questions reviewers commonly ask:

1. *What artefacts must a new deployer provide?*
2. *How many lines of rules per program, on average?*
3. *Does the Prolog encoding scale to a larger catalog?*
4. *What ongoing maintenance does the deployer carry?*

The numbers below come from `wc -l` and predicate-counting over the
current FIU encoding (`prolog_kb/` and `prolog_kb/flowchart_rules/`).
They are facts about the codebase, not projections.

---

## TL;DR

| Concern | Cost at a fresh institution |
|---|---|
| **Engine code** (`audit_layer/`, `prolog_kb/rules_loader.pl`, `validator_rules.pl`, `planner_rules.pl`, `flowchart_rules/eligibility_rules.pl`) | **0 LOC of new code.** Used unchanged. |
| **Course catalog** (`prolog_kb/course_titles.pl` + DB `courses` table) | ~1 line of Prolog per course title (FIU currently has 56 lines for 56 courses); DB seeded from the institution's authoritative catalog. |
| **Per-program rule file** (`flowchart_rules/<level>/<program>_rules.pl`) | **Mean 72 code-lines per program**, range 38–178. Almost entirely mechanical translation from the institution's published flowchart. |
| **DB tables** (`courses`, `program_offerings`, `user_program`, `user_course`, `program_course`) | Schema unchanged; the deployer populates rows from their SIS or course catalog dump. |
| **Per-program author-hours** | ~4–8 hours (1–2 to read the flowchart, 2–4 to encode, 1–2 to smoke-test with one student). |
| **Per-institution total (5–10 programs)** | ~40–80 author-hours plus catalog ingestion. |

---

## 1. Architecture artefacts the audit layer reads

Audited from the source. The audit layer touches exactly these inputs:

| Source | Read by | Notes |
|---|---|---|
| `courses` (DB table) | `db.credits_of`, `db.course_exists` | Course catalog + credit hours. Institution-specific data. |
| `program_offerings` (DB table) | `db.student_context` | Catalog of programs. |
| `user_program` (DB table) | `db.student_context` | Which student is in which program. |
| `user_course` (DB table) | `db.student_context` | Per-student completed courses. |
| `program_course` (DB table) | `db.program_courses`, `db.program_required_courses` | Which courses belong to a program. |
| `prolog_kb/course_titles.pl` | `audit_layer/explainer.py` | Human-readable titles for the explainer. |
| `prolog_kb/flowchart_rules/<level>/<program>_rules.pl` | `prolog_bridge.py` (`prerequisite/2`, `one_of_prereqs_fact/2`, `corequisite/2`) | One file per program — the only program-specific rule artefact. |

The remaining tables in `db/schema.sql` (career advice, job-skill matching, conversation history) are **not** read by the audit layer. They can stay empty at a new deployment.

The Prolog interface from Python is narrow: the audit layer calls exactly four predicates against a program's rule file: `user:prerequisite/2`, `user:one_of_prereqs_fact/2`, `user:corequisite/2`, and `validator_rules:in_program/1`. The explainer additionally uses `needed_chain/3` from `planner_rules.pl` (engine, not per-program).

---

## 2. Anatomy of the current FIU encoding

### 2.1 Engine layer (program-agnostic, 0 LOC to author per new program)

| File | Code-lines | Role |
|---|---:|---|
| `rules_loader.pl` | 43 | Entry point; dispatches `consult/1` to the right program file based on the `program` atom. |
| `validator_rules.pl` | 101 | Generic predicates: `in_program/1`, eligibility helpers, credit-cap math, top-sort. |
| `planner_rules.pl` | 330 | Higher-level planning predicates, including `needed_chain/3` used by the explainer. |
| `flowchart_rules/eligibility_rules.pl` | 28 | Cross-program eligibility helpers and `plan_of_study/3` dispatch. |
| **Subtotal (engine)** | **502** | **reused unchanged across institutions** |

`course_titles.pl` (68 code-lines, 56 facts) is technically institution-specific but is a single flat list of `course_title/2` facts — trivially regenerated from the institution's catalog CSV.

### 2.2 Per-program rule files (the only thing a port really authors)

Twelve programs are currently encoded — 8 undergraduate, 4 graduate. The mean program file is **72 code-lines** with **58 declared predicate facts**.

| Level | Program file | Code-lines | Predicate facts | Top predicates |
|---|---|---:|---:|---|
| UG | `cs_ba_rules.pl` | 70 | 60 | `prerequisite`=33, `required`=19 |
| UG | `cs_bs_rules.pl` | 178 | 142 | `elective_box`=33, `prerequisite`=33, `elective`=30, `required`=19 |
| UG | `cs_bs_sdd_rules.pl` | 91 | 81 | `prerequisite`=54, `required`=16 |
| UG | `cs_minor_rules.pl` | 110 | 77 | `elective`=22, `elective_box`=21 |
| UG | `ds_bs_rules.pl` | 56 | 48 | `prerequisite`=31, `required`=13 |
| UG | `it_ba_rules.pl` | 45 | 37 | `prerequisite`=29 |
| UG | `it_bs_rules.pl` | 73 | 65 | `prerequisite`=32, `required`=27 |
| UG | `it_bs_sw.pl` | 50 | 42 | `prerequisite`=24, `required`=14 |
| Grad | `ms_cs_rules.pl` | 60 | 44 | `elective`=28 |
| Grad | `ms_ds_rules.pl` | 38 | 21 | — |
| Grad | `ms_it_rules.pl` | 52 | 43 | `elective`=25 |
| Grad | `phd_cs_rules.pl` | 41 | 40 | `elective`=18 |
| **TOTAL** | (12 programs) | **864** | **700** | **mean 72 lines / program** |

The distribution is bounded by the program flowchart: a large UG program with many electives lands around 180 lines; a graduate program with a short core lands near 40. No file exceeds 230 lines.

### 2.3 What the audit layer actually queries

Despite the four `user:*` predicate families declared in each program file, the audit-layer Python side calls only:

- `user:prerequisite/2` — hard prereq edges.
- `user:one_of_prereqs_fact/2` — disjunctive prereqs (one-of groups).
- `user:corequisite/2` — corequisite pairs.
- `validator_rules:in_program/1` — program-membership check.
- `needed_chain/3` (engine, from `planner_rules.pl`) — explainer's why-chain.

Predicates like `required/1`, `elective/1`, `elective_box/1` exist for completeness against the flowchart but are not on the audit hot path. A minimal port can populate just the four queried predicates.

---

## 3. What ports as-is vs. what must be authored

| Component | Reused | Authored fresh |
|---|---|---|
| Python audit layer (`audit_layer/`) | ✅ All ~900 LOC | — |
| SQL schema (`db/schema.sql`) | ✅ All 22 tables | — |
| Generic Prolog engine (502 LOC) | ✅ | — |
| Course catalog (Prolog facts + DB rows) | partial format reuse | populate from institution's catalog |
| Per-program rule files | template reuse | one file per program (mean 72 LOC) |
| Evaluation benchmark (`evaluation/queries.yaml`) | template reuse | new institution-relevant queries |
| Taxonomy (`evaluation/taxonomy.md`) | ✅ full | — |

---

## 4. Step-by-step porting recipe

1. **Clone the repository, install the engine.** Standard `requirements.txt` install. No conditional code paths per institution.

2. **Apply `db/schema.sql`** to a fresh PostgreSQL database. The schema is institution-agnostic.

3. **Populate the five DB tables the audit layer reads** from the institution's authoritative catalog or SIS export:
   - `courses` (course code, title, credits)
   - `program_offerings` (program atoms, e.g. `cs_bs`, `ms_cs`)
   - `program_course` (which courses belong to which program, with `is_core` flag)
   - `user_program`, `user_course` (only needed to evaluate against real students)

4. **Regenerate `course_titles.pl`** from the catalog. One `course_title/2` fact per course, one line each. A 200-course catalog yields ~200 lines.

5. **Author one rule file per program** under `flowchart_rules/<undergraduate|graduate>/<program_atom>_rules.pl`. Each file declares:
   - `:- multifile` / `:- dynamic` boilerplate (8 lines, copy verbatim from an existing file).
   - `user:required/1` facts for each core course.
   - `user:prerequisite/2` facts for each hard prereq edge.
   - `user:one_of_prereqs_fact/2` facts for any one-of-N groups.
   - `user:corequisite/2` facts for any corequisite pairs.

   Optional, used by other features but not by the audit layer:
   - `user:elective/1`, `user:elective_box/2` for electives.

6. **Register the program in `rules_loader.pl`** (one `program_rules/2` clause).

7. **Smoke-test** with `scripts/smoke_test.py` against one seeded student in the new institution's DB. Confirm the verifier returns a sensible verdict on a known-good and a known-bad plan.

8. **Run the audit pipeline** on a small benchmark (recommend mirroring `queries.yaml`'s taxonomy with institution-relevant queries — see `evaluation/taxonomy.md`).

Total time at the FIU baseline cadence: ~1 hour for steps 1–4, ~4–8 hours per program for step 5, ~1 hour for steps 6–8 plus initial smoke-test.

---

## 5. Estimated effort

Based on the existing FIU encoding:

- **Per program file**: 4–8 author-hours.
  - 1–2 hrs reading the program flowchart, normalising course codes.
  - 2–4 hrs writing the rule file (mechanical translation; most lines are one-line `prerequisite/2` facts).
  - 1–2 hrs smoke-testing against one student.

- **Per institution (one-time)**: ~6 hours.
  - Catalog dump → `courses` table and `course_titles.pl` regeneration: ~2 hours.
  - Schema apply, env config, dispatch wiring: ~2 hours.
  - Per-institution benchmark seed (10–20 queries spanning the taxonomy categories): ~2 hours.

- **Typical institution (5–10 programs)**: **40–80 author-hours total.**

The cost scales **linearly** in the number of programs, not in the size of the catalog. The Prolog encoding is fact-based; 200 courses cost roughly twice as many lines as 100 courses, but inference time stays the same because SWI-Prolog indexes on the first argument.

---

## 6. Known limitations and what does *not* port cleanly

- **Transfer / AP credit handling.** The current encoding has no first-class `transfer_credit_for/2` predicate. Transfer-credit probes in the benchmark (`probe-transfer-credit`) are correctly flagged as out-of-program when the student's institution catalog does not contain `CS101`. A port that needs transfer credit must add the predicate and an engine-side helper.

- **One-of groups beyond simple disjunction.** `one_of_prereqs_fact/2` handles one-of-N alternatives but does not encode complex Boolean structures like "(A and B) or (C and D)". Programs needing nested logic must split the predicate by hand.

- **Corequisite vs. concurrent enrolment.** The corequisite check assumes the two courses appear in the same semester block. A port that distinguishes "must take before" from "must take with" needs additional semantics on the term comparison.

- **Catalog versioning.** The audit layer reads one version of the catalog at a time. There is no built-in handling of a student on an older catalog year. A deployer carrying multiple catalog years must either branch the rule files by year or include a `catalog_year` argument in the predicates (engine-side work required).

- **Credit-cap is institution-default.** The default per-term credit cap is hardcoded as 18 in `audit_layer/verifier.py`. Easy to make configurable per program, but currently a single number.

- **Course code conventions.** All course atoms are lowercase, no separator (`cop3530`, `mac2311`). A catalog using a different convention (e.g., space-separated or four-letter prefixes) must normalise at ingestion.

---

## 7. Ongoing maintenance

Once a port is in place, the recurring maintenance is small:

- **Catalog refresh** (annually, when the institution publishes the next catalog): re-ingest the `courses` table and regenerate `course_titles.pl`. Mechanical, ~1 hour.

- **Flowchart updates** (per program, when a department changes its plan): edit the corresponding `<program>_rules.pl`. Most updates are 1–10 edits per file per year.

- **Engine updates**: pulled from upstream; no per-deployer work.

There is no model retraining; no fine-tuning per institution; no LLM-side bookkeeping. The deployer can swap LLMs at any time without touching the rule files.

---

## 8. What this does *not* claim

This document quantifies the **encoding cost**. It does not address:

- Whether a given institution will accept the deployment for policy or governance reasons.
- How to populate `users_students` and `user_course` from the institution's SIS.
- Multi-institution generalisation (rule conflicts across institutions sharing a course code).
- UI / advisor-workflow integration above the audit-layer FastAPI endpoint.

The benchmark, evaluation methodology, and headline metrics in the paper all reflect one institutional setting (FIU). The taxonomy and metrics generalise; the rule LOC numbers above let a reader estimate what an extension to a second institution would cost.
