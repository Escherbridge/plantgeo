FROM python:3.12-slim

WORKDIR /app

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy dependency files first for layer caching
COPY pyproject.toml ./
RUN uv sync --no-dev --no-install-project

# Copy source
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini ./

# Install the project itself
RUN uv sync --no-dev

EXPOSE 8000

CMD ["uv", "run", "sanic", "agri_data_service.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
