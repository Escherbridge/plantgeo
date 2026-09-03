.PHONY: dev test lint format check receipt verify-receipt migrate migration-status seed local-status maintain-job-logs install

# `install` is the ONLY target that syncs. Every other target runs `uv run --no-sync`: a bare
# `uv run` re-resolves from the lock's default groups, which drops the dev extra and takes pytest,
# ruff and mypy with it mid-sweep. Recorded incident; scripts/check.py carries the same rule.
install:
	uv sync --locked --all-extras

dev:
	uv run --no-sync sanic agri_data_service.app:create_app --factory --dev --host 0.0.0.0 --port 8000

test:
	uv run --no-sync pytest --cov=agri_data_service --cov-report=term-missing -v

lint:
	uv run --no-sync ruff check src tests scripts
	uv run --no-sync mypy src scripts

check:
	uv run --no-sync python scripts/check.py

receipt:
	uv run --no-sync python scripts/check.py --write-receipt

verify-receipt:
	uv run --no-sync python scripts/verify_quality_receipt.py

format:
	uv run --no-sync ruff format src tests scripts
	uv run --no-sync ruff check --fix src tests scripts

migrate:
	uv run --no-sync agri-service ops db-upgrade head

migration-status:
	uv run --no-sync agri-service ops db-status

seed:
	uv run --no-sync agri-service ops seed

local-status:
	@uv run --no-sync agri-service ops local --help

maintain-job-logs:
	uv run --no-sync agri-service ops job-logs-maintain --retention-days 30 --future-days 7
