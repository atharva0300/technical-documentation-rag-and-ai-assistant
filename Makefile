.PHONY: test test-dataset eval-agent build-frontend

test:
	PYTHONPATH=. .venv/bin/pytest -m "not integration" -v

test-dataset:
	PYTHONPATH=. .venv/bin/pytest tests/test_no_autoverification.py tests/test_dataset_validation.py tests/test_corpus_integrity.py -v

eval-agent:
	PYTHONPATH=. .venv/bin/python scripts/eval_agent.py

build-frontend:
	cd frontend && npm run build
