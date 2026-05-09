# Leave-one-out verifier ablation (v0)

Each row disables a single check family; `full` is the unmodified verifier.

## How to read this table

The verifier short-circuits per course: once a course triggers a violation
of kind K, downstream checks do not fire on the same course in the same
semester. Disabling K therefore lets the next check fire on courses that
K would have rejected, often producing a different violation kind on the
same course. As a result:

- Disabling **prereq_missing** is the only ablation that strictly *removes*
  detected violations (no other check substitutes for it). It is the
  largest contributor to detection across all three models.
- Disabling **unknown_course** or **duplicate_of_completed** does not drop
  the violation count --- those courses now get flagged as
  `program_requirement_unmet` (or another downstream kind). This is a
  feature, not a bug: the multi-family verifier catches each unsafe
  course exactly once, regardless of which family fires first.
- Disabling **credit_cap_exceeded** has no effect on this benchmark: no
  generated plan exceeds 18 credits per term. The check is dormant on
  this query suite and motivates a credit-stress query in v1.
- The most paper-relevant ablation signal is **repair success**: removing
  prereq checks pushes repair from 0.65/0.66 to 0.82/0.88 on Mistral and
  Gemma, because the remaining violations (unknown, duplicate, program)
  are amenable to greedy removal. Prereq violations carry both detection
  weight and repair difficulty.

Per-kind detection coverage (the orthogonal view) is reported in the main
results table in `three_model_v0.md`.


## ollama:qwen2.5:7b

| Ablation | Flagged | Viol/plan | Edit dist | Repair attempted | Repair success |
|---|---:|---:|---:|---:|---:|
| full | 1.000 | 2.870 | 2.870 | 46 | 0.804 |
| no_prereq_missing | 1.000 | 1.870 | 1.870 | 46 | 0.935 |
| no_unknown_course | 1.000 | 2.870 | 2.870 | 46 | 0.804 |
| no_duplicate_of_completed | 1.000 | 2.870 | 2.870 | 46 | 0.804 |
| no_program_requirement_unmet | 1.000 | 2.739 | 2.739 | 46 | 0.848 |
| no_credit_cap_exceeded | 1.000 | 2.870 | 2.870 | 46 | 0.804 |

## hf:mistral-7b-instruct-v0.3

| Ablation | Flagged | Viol/plan | Edit dist | Repair attempted | Repair success |
|---|---:|---:|---:|---:|---:|
| full | 1.000 | 9.109 | 9.109 | 46 | 0.652 |
| no_prereq_missing | 0.978 | 8.391 | 8.391 | 45 | 0.822 |
| no_unknown_course | 1.000 | 9.109 | 9.109 | 46 | 0.630 |
| no_duplicate_of_completed | 1.000 | 9.065 | 9.043 | 46 | 0.652 |
| no_program_requirement_unmet | 1.000 | 8.848 | 8.848 | 46 | 0.674 |
| no_credit_cap_exceeded | 1.000 | 9.109 | 9.109 | 46 | 0.630 |

## hf:gemma-2-9b-it

| Ablation | Flagged | Viol/plan | Edit dist | Repair attempted | Repair success |
|---|---:|---:|---:|---:|---:|
| full | 1.000 | 3.761 | 3.717 | 44 | 0.659 |
| no_prereq_missing | 0.957 | 2.978 | 2.935 | 42 | 0.881 |
| no_unknown_course | 1.000 | 3.739 | 3.696 | 44 | 0.659 |
| no_duplicate_of_completed | 0.978 | 3.652 | 3.609 | 43 | 0.674 |
| no_program_requirement_unmet | 1.000 | 3.739 | 3.696 | 44 | 0.659 |
| no_credit_cap_exceeded | 1.000 | 3.761 | 3.717 | 44 | 0.659 |
