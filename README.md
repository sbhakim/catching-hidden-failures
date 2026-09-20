# Auditing LLM-Generated Degree Plans

A post-generation audit and repair layer for academic advising plans. It parses
semester schedules, checks encoded curricular constraints, and re-verifies repair
candidates before acceptance. The API and batch runner share the same pipeline.

## Repository contents

- `audit_layer/`: parsing, verification, greedy and scheduling repair, diagnostics,
  and the FastAPI service.
- `prolog_kb/`: program-scoped curriculum rules and prerequisite queries.
- `db/`: PostgreSQL schema and connection helpers.
- `evaluation/`: benchmark queries, generation and audit runners, and metrics.
- `scripts/`: service startup and local evaluation commands.

Generated responses, results, figures, PDFs, local tests, and working notes are
excluded from this source release. Replaced local files are not deleted by Git's
ignore rules.

## Setup

Requires Python 3.10+, PostgreSQL 14+, and SWI-Prolog 8.4+ (`swipl` on `PATH`).
The startup scripts assume Bash and Conda. Model generation additionally requires
an appropriate local model server or provider credentials.

```bash
git clone https://github.com/sbhakim/catching-hidden-failures.git
cd catching-hidden-failures
conda create -n nesy python=3.11 -y
conda activate nesy
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your database connection and any provider credentials.
createdb course_advisor
psql -d course_advisor -f db/schema.sql
```

The schema contains **no data rows**. Populate `courses` with identifiers and
recorded credits before auditing. Requests without explicit program/history also
need student context in `users_students`, `program_offerings`, `user_program`, and
`user_course`. The generation tool baseline additionally uses `program_course`.
Use authorized institutional data and keep credentials and student data local.

A database schema alone cannot reproduce the paper's numerical results. Exact
replay also requires the corresponding catalog snapshot, saved responses, query
selection, and evaluation configuration. Those local artifacts are not bundled
here. New provider responses can differ from historical runs.

## Run the service

```bash
uvicorn audit_layer.api:app --host 127.0.0.1 --port 8020
```

Example request, after loading a compatible catalog:

```bash
curl -X POST http://127.0.0.1:8020/audit \
  -H 'Content-Type: application/json' \
  -d '{
    "student_id": 1,
    "program": "CS-BS",
    "completed": ["COP_2210", "MAD_2104", "COT_3100"],
    "plan": "- **Fall 2026**: COP_3530\n- **Spring 2027**: COP_4534",
    "source": "manual",
    "credit_cap": 18
  }'
```

`compliant` describes the original plan; `repaired_compliant` and
`residual_violations` describe the candidate. `repair_strategy` identifies the
selected method, and `unresolved_courses` records omitted original courses.
Only `compliant` and `repaired` statuses pass the encoded checks.

Nonempty plans require explicit, strictly increasing Spring/Summer/Fall years
(Autumn is accepted as Fall). Missing years or unsupported/repeated/reversed terms
produce `needs_review`, with no repair candidate. Empty extraction is a failure.
Missing or negative credits and unavailable Prolog checks stop certification;
recorded zero-credit courses remain valid. These unavailable checks produce HTTP
503 with `audit_unavailable`. Other infrastructure errors can also stop requests.

Hard prerequisites require prior completion; co-requisites may use feasible
concurrent courses. Repair never invents courses or extends the original horizon.
Acceptance does not establish complete degree fulfillment, offering availability,
student preference satisfaction, or an optimal edit set. Prerequisite-chain
context is supplementary, not a complete explanation certificate.

## Generate and audit local responses

`evaluation/queries.yaml` contains the original 46 author-constructed queries and
15 additional routine queries. `run_llm_fixture.py --v1-only` selects the original
suite and supplies the three seeded benchmark profiles; it is an evaluation
input adapter, not a local test fixture. Without this flag it runs all 61 queries.
Category definitions are documented in the YAML header. The suite has not been
independently validated by academic advisors.

For a local Ollama model, pull and serve `qwen2.5:7b` first. Then:

```bash
python evaluation/run_llm_fixture.py --queries evaluation/queries.yaml \
  --llm ollama:qwen2.5:7b --v1-only --out evaluation/runs/qwen.jsonl
python evaluation/run_audit.py --in evaluation/runs/qwen.jsonl \
  --out evaluation/audited/qwen.jsonl --credit-cap 18
python evaluation/compute_metrics.py --inputs evaluation/audited/qwen.jsonl --by_tag
```

`run_llm.py` resolves profiles from PostgreSQL instead. Run either generator with
`--help` to see registered model identifiers. Hugging Face generation needs
separately installed `torch`, `transformers`, and model access where required.
Provider-backed generation requires the relevant API key and incurs provider fees.
No API calls run during installation.

`run_tool_calling.py` provides the structured tool-access baseline. The general
generation runners use their own prompts and decoding settings; they are not the
separate grounded DeepSeek V4 Flash (0731) or GLM 5.3 experiment protocols.

Metrics re-verify candidates against the configured database and rules, using
the recorded credit cap. They require the same catalog/rule version as the audit.
Check successful process completion and expected response counts: unavailable
checks abort batch processing and can leave partial output files. The convenience
script `scripts/run_full_eval.sh` runs the original three-generator workflow;
it does not reproduce every experiment in the revised manuscript.

## Curriculum maintenance

Version the catalog, rule sources, completed-course histories, and evaluation
configuration together. Restart the service after changes because lookups are
cached for the process lifetime. A curriculum specialist should resolve policy
ambiguities; an engineer should map identifiers and check affected rule consumers.

The focal CS-BS rules preserve the [April 2023 FIU flowchart](https://users.cs.fiu.edu/~prabakar/upc/flowcharts/backup/2024-03-22_backup/CS-BS.pdf).
They include alternative co-requisites and prior MAC-and-COP requirements for
COT3100. The source's syllabus-dependent COT3510 prerequisite remains unresolved.
This is not a statement of current catalog coverage. Other institutions may need
changes for identifier formats, Boolean requirements, transfer credit, exceptions,
term offerings, and full degree requirements. Multi-catalog-year selection and
advisor-facing effectiveness have not been established.

## Provenance and citation

The audit layer and evaluation runners extend the curriculum-grounded foundation
in [Aurora (SAC 2026)](https://doi.org/10.1145/3748522.3779850). The schema and program
rules originate in that work; CS-BS rules and their consumers have been revised
as described above. See `CITATION.cff` for software attribution.

## License and contact

MIT; see [LICENSE](LICENSE). Contact: safayat.b.hakim@gmail.com.
