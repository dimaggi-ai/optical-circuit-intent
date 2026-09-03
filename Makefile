# Reproduction entry points. See README.md for what each target produces.
PY := .venv/bin/python

.PHONY: help venv data test validate examples experiments smoke-test clean

help:
	@echo "make venv        create the pinned virtual environment"
	@echo "make data        fetch and SHA-verify the HEDGE testbed files (needed by the rest)"
	@echo "make test        run the test suite, including the mutation tests"
	@echo "make validate    the validation registry and the list of what it declines"
	@echo "make examples    run every example input through the CLI"
	@echo "make experiments the four figures the README quotes"
	@echo "make smoke-test  tests, registry and examples (<1 min)"
	@echo "make clean       remove build artifacts"

venv:
	python3.12 -m venv .venv
	.venv/bin/pip install --quiet --upgrade pip
	.venv/bin/pip install --quiet -e ".[dev]"

data:
	PYTHONPATH=src $(PY) data/hedge/fetch_hedge.py

test: data
	PYTHONPATH=src:validation $(PY) -m pytest tests/ -q

validate: data
	$(PY) validation/validate_intent.py

examples: data
	$(PY) examples/run_examples.py

experiments: data
	PYTHONPATH=src $(PY) experiments/retune_disagreement.py
	PYTHONPATH=src $(PY) experiments/checkpoint_crossover.py
	PYTHONPATH=src $(PY) experiments/stranded_ports.py
	PYTHONPATH=src $(PY) experiments/measured_lead_time.py

smoke-test: test validate examples
	@echo "smoke test complete"

clean:
	rm -rf build dist src/*.egg-info .pytest_cache
