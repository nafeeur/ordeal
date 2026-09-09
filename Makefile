.PHONY: api web test demo
api:
	cd backend && uvicorn app.main:app --reload
web:
	cd frontend && npm run dev
test:
	cd backend && PYTHONPATH=. pytest -q
demo:
	python3 ordeal_cli.py seed-demo
