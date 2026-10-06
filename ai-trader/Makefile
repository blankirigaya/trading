.PHONY: dev test lint format typecheck db-up db-down migrate seed run replay-demo
dev:
	uv sync
test:
	uv run pytest
lint:
	uv run ruff check .
format:
	uv run ruff format .
typecheck:
	uv run mypy src
db-up:
	docker compose up -d
db-down:
	docker compose down
migrate:
	uv run alembic upgrade head
seed:
	uv run python scripts/seed_demo.py
run:
	uv run uvicorn ai_trader.main:app --reload
replay-demo:
	uv run python -m ai_trader replay --date 2026-09-01
