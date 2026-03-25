.PHONY: dev test lint format migrate seed install

install:
	uv sync --all-extras

dev:
	sanic agri_data_service.app:create_app --factory --dev --host 0.0.0.0 --port 8000

test:
	pytest --cov=agri_data_service --cov-report=term-missing -v

lint:
	ruff check src/ tests/
	mypy src/

format:
	ruff format src/ tests/
	ruff check --fix src/ tests/

migrate:
	alembic upgrade head

seed:
	agri-cli seed

reset-db:
	agri-cli reset-db
