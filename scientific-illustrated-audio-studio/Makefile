.PHONY: test lint plan audit smoke notebooks
test:
	python -m pytest tests -q
lint:
	python -m ruff check src tests
plan:
	PYTHONPATH=src python -m sias.cli plan --episode examples/nonstop_rain/episode.yaml --workspace workspace --json
audit:
	PYTHONPATH=src python -m sias.cli audit --json
smoke:
	python -m pytest tests/integration/test_smoke_render.py -q
notebooks:
	python tools/build_notebooks.py
