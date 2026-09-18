.PHONY: test demo demo-live metrics check-metrics run run-both results screenshots clean

PY ?= python3

test:            ## full test suite
	$(PY) -m pytest tests -q

demo:            ## the four-minute demo, headless, every verdict asserted
	$(PY) -m sentinel.demo

demo-live:       ## drive a running console through the same sequence
	$(PY) -m sentinel.demo --live --url $${SENTINEL_URL:-http://localhost:8080} --token "$${SENTINEL_CONSOLE_TOKEN:-}"

metrics:         ## regenerate docs/RESULTS.md and the README metrics block from a real run
	$(PY) -m sentinel.metrics --write

check-metrics:   ## fail if the committed numbers differ from a fresh run
	$(PY) -m sentinel.metrics --check

run:             ## grid console (the demo) on http://localhost:8080
	./run.sh

run-both:        ## grid + pipeline consoles
	./run.sh --profile both

screenshots:     ## refresh docs/img from a running console
	$(PY) -m sentinel.screenshots

clean:
	rm -rf .pytest_cache htmlcov .coverage
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
