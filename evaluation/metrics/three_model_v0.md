# Three-Model Audit Metrics (v0)

Generated from the 46-query adversarial advising benchmark in
`evaluation/queries.yaml`.

Command:

```bash
conda activate nesy
python evaluation/compute_metrics.py \
  --inputs \
    evaluation/audited/qwen2_5_7b_full.jsonl \
    evaluation/audited/hf_mistral_7b_v03_full.jsonl \
    evaluation/audited/hf_gemma_2_9b_it_full.jsonl \
  --by_tag
```

## Headline Table

| Model | n | Parse success | Compliant | Prereq violation | Credit cap | Unknown course | Duplicate | Program mismatch | Mean violations | Mean edit distance | Repair success |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `ollama:qwen2.5:7b` | 46 | 1.000 | 0.000 | 0.935 | 0.000 | 0.696 | 0.957 | 0.043 | 2.870 | 2.870 | 0.804 |
| `hf:mistral-7b-instruct-v0.3` | 46 | 1.000 | 0.000 | 0.674 | 0.000 | 0.935 | 0.174 | 0.261 | 9.109 | 9.109 | 0.652 |
| `hf:gemma-2-9b-it` | 46 | 0.957 | 0.000 | 0.783 | 0.000 | 0.761 | 0.587 | 0.022 | 3.761 | 3.717 | 0.630 |

## Interpretation For Manuscript

The three models fail differently under the same audit layer:

- Qwen2.5-7B-Instruct produces parseable plans with comparatively fewer total violations, but it has the highest duplicate rate and the highest prerequisite-violation rate.
- Mistral-7B-Instruct-v0.3 produces the most severe failures overall, especially unknown-course and program-membership failures.
- Gemma-2-9B-IT is a middle case: it mostly follows the output format but still produces substantial prerequisite, catalog, and duplicate failures.

This supports the paper claim that the contribution is model-agnostic. The audit layer is not tuned to one generator; it exposes distinct failure profiles across generators and verifies repairs under the same symbolic constraints.
