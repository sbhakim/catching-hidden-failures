# Symbolic knowledge base

SWI-Prolog rule files consulted by the audit layer's verifier and
explainer (`audit_layer/prolog_bridge.py`). The audit layer treats this
directory as read-only ground truth.

## Top-level files

| File | Purpose |
|---|---|
| `rules_loader.pl`   | Entry point. Loads the catalog + validator + planner files; consulted by every Prolog query. |
| `validator_rules.pl`| Predicates the verifier queries: `in_program/1`, `prerequisite/2`, `one_of_prereqs_fact/2`, `corequisite/2`. |
| `planner_rules.pl`  | Higher-level planning predicates including `needed_chain/3` (used by the explainer for prereq why-chains). |
| `course_titles.pl`  | Static facts mapping course atoms to human-readable titles. Used by the explainer for output formatting. |

## Per-program rule files (`flowchart_rules/`)

Each program has its own file declaring `prerequisite/2`,
`one_of_prereqs_fact/2`, and `in_program/1` for that program's catalog.
The bridge consults the right file based on the `program` argument
(`norm_prog` → `consult_program`).

| Subdirectory | Programs |
|---|---|
| `undergraduate/` | `cs_bs`, `cs_ba`, `cs_minor`, `cs_bs_sdd`, `ds_bs`, `it_ba`, `it_bs`, `it_bs_sw` |
| `graduate/`      | `ms_cs`, `ms_ds`, `ms_it`, `phd_cs` |
| (top-level)      | `eligibility_rules.pl` — cross-program eligibility helpers |

## Conventions

- Course atoms are lowercase, no separator: `cop3530`, `mac2311`.
- Program atoms use snake_case: `cs_bs`, `ms_cs`. The bridge converts
  display names like `CS-BS` to this form via `norm_prog`.
- Predicates exposed to Python live under `user:` (e.g.
  `user:prerequisite/2`); helper predicates internal to a file are
  module-scoped.

## Adding a new program

1. Drop a `<program_atom>_rules.pl` into the right subdirectory.
2. Declare `in_program/1`, `prerequisite/2`, `one_of_prereqs_fact/2`,
   and (if applicable) `corequisite/2`.
3. No code changes are needed in the audit layer — the bridge
   consults whatever file matches the program atom passed at runtime.

## Provenance

The program-flowchart files under `flowchart_rules/` are reused unchanged
from prior curriculum-grounded advising work; see the top-level README
*Provenance* note for the full citation.
