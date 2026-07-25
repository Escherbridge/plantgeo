"""Async SQLAlchemy engine and session factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from agri_data_service.config import settings


class _CombinedLocalState:
    engine: AsyncEngine | None = None
    session_factory: async_sessionmaker[AsyncSession] | None = None


_combined_state = _CombinedLocalState()
_service_engines: dict[str, AsyncEngine] = {}


def combined_local_engine() -> AsyncEngine:
    """Create the legacy local pool only inside the combined-local profile."""
    database_url = settings.require_combined_local_database_url()
    if _combined_state.engine is None:
        _combined_state.engine = create_async_engine(
            database_url,
            pool_size=settings.db_pool_min,
            max_overflow=settings.db_pool_max - settings.db_pool_min,
            echo=settings.sanic_debug,
        )
        _combined_state.session_factory = async_sessionmaker(
            _combined_state.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _combined_state.engine


def async_session() -> AsyncSession:
    """Return one combined-local session and fail closed in production profiles."""
    combined_local_engine()
    assert _combined_state.session_factory is not None
    return _combined_state.session_factory()


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield an async database session."""
    async with async_session() as session:
        yield session


def _service_engine(profile: Literal["receiver_writer", "published_reader"]) -> AsyncEngine:
    database_url = (
        settings.require_receiver_writer_database_url()
        if profile == "receiver_writer"
        else settings.require_published_reader_database_url()
    )
    cached = _service_engines.get(profile)
    if cached is None:
        cached = create_async_engine(
            database_url,
            pool_size=settings.db_pool_min,
            max_overflow=settings.db_pool_max - settings.db_pool_min,
            pool_pre_ping=True,
            echo=settings.sanic_debug,
        )
        _service_engines[profile] = cached
    return cached


@asynccontextmanager
async def _service_session(
    profile: Literal["receiver_writer", "published_reader"],
) -> AsyncIterator[AsyncSession]:
    if settings.service_profile == "combined_local":
        async with async_session() as session:
            yield session
        return
    if settings.service_profile != profile:
        raise ValueError(f"{profile} database session is unavailable in the {settings.service_profile} profile")
    profile_session = async_sessionmaker(_service_engine(profile), class_=AsyncSession, expire_on_commit=False)
    async with profile_session() as session:
        yield session


@asynccontextmanager
async def receiver_writer_session() -> AsyncIterator[AsyncSession]:
    """Yield the explicit production receiver/writer session."""
    async with _service_session("receiver_writer") as session:
        yield session


@asynccontextmanager
async def published_reader_session() -> AsyncIterator[AsyncSession]:
    """Yield the explicit published-only reader session."""
    async with _service_session("published_reader") as session:
        yield session


async def dispose_service_engines() -> None:
    """Dispose any lazily created production-profile pools."""
    for service_engine in _service_engines.values():
        await service_engine.dispose()
    _service_engines.clear()


async def dispose_combined_local_engine() -> None:
    """Dispose the legacy local pool only if it was explicitly created."""
    if _combined_state.engine is not None:
        await _combined_state.engine.dispose()
    _combined_state.engine = None
    _combined_state.session_factory = None


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


@asynccontextmanager
async def forecast_mv_refresh_session(database_url: str) -> AsyncIterator[AsyncSession]:
    """Yield one isolated session for the reviewed forecast MV refresher DSN."""
    refresh_engine = create_async_engine(
        database_url,
        pool_size=1,
        max_overflow=0,
        pool_pre_ping=True,
        echo=False,
    )
    refresh_session = async_sessionmaker(refresh_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with refresh_session() as session:
            yield session
    finally:
        await refresh_engine.dispose()


@asynccontextmanager
async def forecast_iteration_session(database_url: str) -> AsyncIterator[AsyncSession]:
    """Yield one isolated session for local evaluation-only forecast iterations."""
    iteration_engine = create_async_engine(
        database_url,
        pool_size=1,
        max_overflow=0,
        pool_pre_ping=True,
        echo=False,
    )
    iteration_session = async_sessionmaker(iteration_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with iteration_session() as session:
            yield session
    finally:
        await iteration_engine.dispose()
