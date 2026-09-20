# Use the active environment; outputs remain local and ignored.
PYTHON ?= python
INPUTS ?=

.PHONY: help install metrics eval serve
help:
	@echo "install | serve | eval | metrics INPUTS='path/to/audited.jsonl'"
install:
	$(PYTHON) -m pip install -r requirements.txt
metrics:
	@test -n "$(INPUTS)" || (echo 'Set INPUTS to completed local audit files'; exit 1)
	$(PYTHON) evaluation/compute_metrics.py --inputs $(INPUTS) --by_tag
eval:
	bash scripts/run_full_eval.sh
serve:
	$(PYTHON) -m uvicorn audit_layer.api:app --host 127.0.0.1 --port 8020
