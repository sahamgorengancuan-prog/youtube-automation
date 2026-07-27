.PHONY: test lint notebooks validate plan-exec smoke all
test:
	python -m pytest tests -q
lint:
	python -m ruff check src tests tools
notebooks:
	python tools/build_notebooks.py
validate:
	python tools/validate_notebook.py
plan-exec:
	python tools/execute_plan_notebook.py
smoke:
	python tools/local_render_smoke_test.py
all: lint test validate plan-exec smoke
