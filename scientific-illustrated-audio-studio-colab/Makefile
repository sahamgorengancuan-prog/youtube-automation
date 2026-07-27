.PHONY: test lint notebooks validate plan-exec smoke standalone standalone-exec all
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
standalone:
	python tools/build_standalone_notebook.py
standalone-exec:
	python tools/execute_standalone_notebook.py
all: lint test validate plan-exec smoke standalone standalone-exec
