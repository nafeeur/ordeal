.PHONY: api tui test demo
api:
	cd backend && uvicorn app.main:app --reload
tui:
	python3 ordeal_cli.py tui
test:
	cd backend && PYTHONPATH=. pytest -q
demo:
	python3 ordeal_cli.py seed-demo
