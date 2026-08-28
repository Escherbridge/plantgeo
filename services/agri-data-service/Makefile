.PHONY: dev test lint format check migrate migration-status seed local-status maintain-job-logs install

install:
	uv sync --locked --all-extras

dev:
	uv run sanic agri_data_service.app:create_app --factory --dev --host 0.0.0.0 --port 8000

test:
	uv run pytest --cov=agri_data_service --cov-report=term-missing -v

lint:
	uv run ruff check src/ tests/
	uv run mypy src/

check:
	uv run python scripts/check.py

format:
	uv run ruff format src/ tests/
	uv run ruff check --fix src/ tests/

migrate:
	uv run agri-service ops db-upgrade head

migration-status:
	uv run agri-service ops db-status

seed:
	uv run agri-service ops seed

local-status:
	@uv run agri-service ops local --help

maintain-job-logs:
	uv run agri-service ops job-logs-maintain --retention-days 30 --future-days 7
