"""Application configuration via Pydantic settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://plantgeo:plantgeo@localhost:5432/agri_data"
    database_url_sync: str = "postgresql://plantgeo:plantgeo@localhost:5432/agri_data"
    db_pool_min: int = 10
    db_pool_max: int = 20

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Sanic
    sanic_host: str = "0.0.0.0"
    sanic_port: int = 8000
    sanic_debug: bool = False

    # CORS
    cors_origins: str = "http://localhost:3000"

    # Martin
    martin_database_url: str = "postgresql://plantgeo:plantgeo@localhost:5432/agri_data"


settings = Settings()
