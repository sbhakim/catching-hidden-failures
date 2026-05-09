# Catching Hidden Failures &mdash; Code & Data

A model-agnostic, post-generation **audit-and-repair layer** that wraps any LLM-generated
academic advising plan and returns (i) a verdict over five constraint families plus a
structural empty-plan guard, (ii) a Prolog-grounded explanation of any violation, and
(iii) a verifier-checked, minimum-edit repaired plan.

The repository contains the audit layer, the SWI-Prolog knowledge base, the 46-query
adversarial benchmark, the three frozen evaluation snapshots used in Table 1, and the
leave-one-out verifier ablation (Table 3).

---

## Headline results (3 LLMs &times; 46 queries)

| Model | Flagged plans | Mean violations / plan | Mean edit distance | Repair success |
|---|---:|---:|---:|---:|
| Qwen2.5-7B-Instruct  | 100% | 0.65 | 0.60 | 80.4% |
| Mistral-7B-Instruct-v0.3 | 100% | 0.65 | 0.74 | 65.2% |
| Gemma-2-9B-IT       | 100% | 0.66 | 0.61 | 63.0% |

Numbers come from `evaluation/audited/*_full.jsonl` (committed to this repo) via
`evaluation/compute_metrics.py`. They reproduce to three decimals on a fresh checkout
without re-querying the LLMs.

---

## Repository layout

```
.
├── audit_layer/                 # core contribution (~830 LOC Python)
│   ├── models.py                # Pydantic types: Plan, Violation, AuditResult
│   ├── plan_parser.py           # LLM output (markdown / JSON / free text) -> Plan
│   ├── prolog_bridge.py         # subprocess shell to SWI-Prolog
│   ├── db.py                    # Postgres helpers (catalog, completed credits)
│   ├── verifier.py              # 5 constraint families + empty-plan guard
│   ├── explainer.py             # prerequisite-chain explanations from the proof tree
│   ├── repair.py                # greedy minimum-edit repair (re-verified)
│   └── api.py                   # FastAPI: POST /audit, POST /parse
│
├── prolog_kb/                   # symbolic KB (program rules + course catalog)
│   ├── rules_loader.pl
│   ├── validator_rules.pl
│   ├── planner_rules.pl
│   ├── course_titles.pl
│   └── flowchart_rules/         # 12 hand-written program rule files
│
├── db/
│   ├── schema.sql               # Postgres schema for the curriculum DB
│   └── db_config.py
│
├── evaluation/
│   ├── queries.yaml             # 46-query adversarial benchmark
│   ├── run_llm.py               # collect raw plans from a given LLM
│   ├── run_audit.py             # apply the audit layer to runs/*.jsonl
│   ├── compute_metrics.py       # Table 1 numbers (+ optional --by_tag breakdown)
│   ├── run_ablations.py         # Table 3 leave-one-out verifier ablation
│   ├── runs/                    # frozen raw LLM outputs (3 models)
│   ├── audited/                 # frozen audited plans (3 models) — Table 1 source
│   └── metrics/                 # snapshots referenced by the paper
│
├── scripts/
│   ├── run_full_eval.sh         # one-command reproduction (idempotent)
│   ├── run_services.sh          # bring up the FastAPI audit service
│   └── smoke_test.py            # synthetic-plan exercise of the layer (no LLM)
│
├── docs/design_notes.md
├── .env.example
├── requirements.txt
├── CITATION.cff
└── LICENSE                       # MIT
```

---

## Requirements

| Dependency | Version | Notes |
|---|---|---|
| Python      | 3.10+   | Pydantic v2, FastAPI 0.110 |
| SWI-Prolog  | 8.4+    | `swipl` on `$PATH` |
| PostgreSQL  | 14+     | Schema in `db/schema.sql` |
| (optional) Ollama | latest | local LLM serving for `evaluation/run_llm.py` |
| (optional) HuggingFace Transformers | 4.40+ | for `hf:` LLM backends |

The codebase is Linux-only by default (the SWI-Prolog subprocess is invoked via POSIX
shell). It has been tested on Ubuntu 24.04 with an NVIDIA RTX&nbsp;3090.

---

## Quickstart

### 1. Clone and create the environment

```bash
git clone https://github.com/<user>/NeSy-Advising-Agent.git
cd NeSy-Advising-Agent

conda create -n nesy python=3.10 -y
conda activate nesy
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
$EDITOR .env       # POSTGRES_* and (optionally) ANTHROPIC_API_KEY / OPENAI_API_KEY
```

### 3. Bring up Postgres and seed the curriculum schema

Any local Postgres instance works (Docker, system service, or managed). Create the
database and apply the schema:

```bash
createdb course_advisor
psql -d course_advisor -f db/schema.sql
```

The audit layer expects the curriculum DB used by the paper's evaluation. The schema
file declares the tables; populating it for an arbitrary institution is out of scope
for this artifact and is discussed in the paper's *Limitations and Future Directions*.

### 4. Verify the install with the no-LLM smoke test

```bash
python scripts/smoke_test.py
```

This parses three synthetic plans, runs them through the verifier, and prints the
violations found &mdash; no LLM and no Postgres data are required for the parser/verifier
path.

### 5. Reproduce the paper's headline numbers from the frozen snapshots

```bash
python evaluation/compute_metrics.py --inputs \
    evaluation/audited/qwen2_5_7b_full.jsonl \
    evaluation/audited/hf_mistral_7b_v03_full.jsonl \
    evaluation/audited/hf_gemma_2_9b_it_full.jsonl \
    --by_tag
```

Output matches Table 1 of the paper to three decimals. Runs in ~20 seconds.

### 6. Reproduce the leave-one-out ablation (Table 3)

```bash
python evaluation/run_ablations.py --inputs \
    evaluation/audited/qwen2_5_7b_full.jsonl \
    evaluation/audited/hf_mistral_7b_v03_full.jsonl \
    evaluation/audited/hf_gemma_2_9b_it_full.jsonl \
    --out evaluation/metrics/ablations_v0.json
```

Snapshot is also pre-committed at `evaluation/metrics/ablations_v0.{json,md}`.

---

## Full reproduction from scratch

To regenerate raw plans from each LLM and rebuild every output file, including the
`runs/` and `audited/` JSONL snapshots:

```bash
# starts/ensures Postgres, Ollama, swipl are reachable, then runs every stage
bash scripts/run_full_eval.sh

# force re-generation even if outputs exist
FORCE=1 bash scripts/run_full_eval.sh
```

The script is **idempotent**: stages are skipped if their output file already exists.
Override the conda environment with `CONDA_ENV=<name>` and the conda root with
`CONDA_BASE=<path>`.

For Ollama:

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:7b-instruct
ollama serve &
```

For HuggingFace backends (`hf:mistral-7b-instruct-v0.3`, `hf:gemma-2-9b-it`), the model
weights are downloaded by `evaluation/run_llm.py` on first use; expect ~30 GB of disk
and a CUDA-capable GPU with ≥16 GB VRAM.

---

## Auditing a single plan over HTTP

Start the audit-layer API:

```bash
./scripts/run_services.sh                # foreground (uvicorn --reload)
# or
./scripts/run_services.sh --bg           # background, logs in /tmp/audit_api.log
```

Then audit a plan:

```bash
curl -X POST http://127.0.0.1:8020/audit \
  -H 'Content-Type: application/json' \
  -d '{
        "student_id": 4942915,
        "plan": "- **Fall 2026**: COP_4338, CDA_3102\n- **Spring 2027**: COP_3530",
        "source": "manual"
      }'
```

The response contains the parsed `Plan`, the list of `Violation`s grouped by family,
the prerequisite-chain explanations, and the repaired plan with its edit list.

---

## What this codebase does *not* claim

- It is **not** a curriculum DB for an arbitrary institution. The schema is provided;
  populating it for a specific catalog is the deployer's responsibility.
- It is **not** a benchmark release. The 46-query suite is the seed used in the paper;
  expansion to a multi-institution benchmark is left as future work (see the paper's
  *Limitations* section).
- It is **not** a new advising agent. The contribution is the post-generation
  **audit layer** that wraps any plan generator; the symbolic curriculum rules in
  `prolog_kb/flowchart_rules/` are pre-existing institutional rules.

---

## Contact

For questions or follow-ups, reach out at
**`safayat`** ‹dot› **`b`** ‹dot› **`hakim`** ‹at› **`gmail`** ‹dot› **`com`**
(replace each ‹dot› with `.` and ‹at› with `@`; written this way to deter
automated scrapers).

## License

MIT &mdash; see [`LICENSE`](LICENSE).

## Provenance

The program-flowchart rule files under `prolog_kb/flowchart_rules/` and
the underlying curriculum schema in `db/schema.sql` are reused, unchanged,
from prior curriculum-grounded advising work
([Quincoso&nbsp;Lugones et&nbsp;al., SAC&nbsp;'26](https://arxiv.org/abs/2602.17999)).
The audit-layer code (`audit_layer/`, `evaluation/`, `tests/`, scripts) is
original to this work.
