"""Async SQLAlchemy engine and session factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from agri_data_service.config import settings

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    pool_size=settings.db_pool_min,
    max_overflow=settings.db_pool_max - settings.db_pool_min,
    echo=settings.sanic_debug,
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield an async database session."""
    async with async_session() as session:
        yield session


@asynccontextmanager
async def local_source_loader_session(database_url: str) -> AsyncIterator[AsyncSession]:
    """Yield one isolated session for an explicitly approved local source loader DSN."""
    loader_engine = create_async_engine(
        database_url,
        pool_size=1,
        max_overflow=0,
        pool_pre_ping=True,
        echo=False,
    )
    loader_session = async_sessionmaker(loader_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with loader_session() as session:
            yield session
    finally:
        await loader_engine.dispose()
