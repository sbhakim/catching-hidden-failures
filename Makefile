# Convenience targets. All targets assume the active Python env has the
# packages from requirements.txt installed.

PYTHON ?= python
AUDITED := evaluation/audited
RUNS    := evaluation/runs
METRICS := evaluation/metrics

.PHONY: help install test smoke metrics ablation eval serve clean

help:
	@echo "Targets:"
	@echo "  install   pip install -r requirements.txt (+ pytest)"
	@echo "  test      run the offline pytest suite"
	@echo "  smoke     end-to-end synthetic-plan exercise (needs Postgres + swipl)"
	@echo "  metrics   recompute Table 1 from frozen audited snapshots"
	@echo "  ablation  recompute Table 3 (leave-one-out verifier ablation)"
	@echo "  eval      full reproduction: gen -> audit -> metrics -> ablation"
	@echo "  serve     run the FastAPI audit endpoint on :8020"
	@echo "  clean     remove __pycache__ and pytest caches"

install:
	pip install -r requirements.txt
	pip install pytest

test:
	pytest tests/ -q

smoke:
	$(PYTHON) scripts/smoke_test.py

metrics:
	$(PYTHON) evaluation/compute_metrics.py --inputs \
	    $(AUDITED)/qwen2_5_7b_full.jsonl \
	    $(AUDITED)/hf_mistral_7b_v03_full.jsonl \
	    $(AUDITED)/hf_gemma_2_9b_it_full.jsonl \
	    --by_tag

ablation:
	$(PYTHON) evaluation/run_ablations.py --inputs \
	    $(AUDITED)/qwen2_5_7b_full.jsonl \
	    $(AUDITED)/hf_mistral_7b_v03_full.jsonl \
	    $(AUDITED)/hf_gemma_2_9b_it_full.jsonl \
	    --out $(METRICS)/ablations_v0.json

eval:
	bash scripts/run_full_eval.sh

serve:
	bash scripts/run_services.sh

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	rm -rf .pytest_cache
