"""The CRM database connection — the only one this service has.

A reduced copy of the annexe template: there is no ``LocalBase`` and no
``local_engine`` because this service owns no tables. It reads and drives
``outbound_message`` (and reads ``email_suppression`` / ``community``), all of
which live in the CRM schema owned by ``crm-backend``. news-board set the
precedent for an annexe writing to that schema; the invariant this buys is
"the producer's business write committed => the message is queued", which is
only expressible if the enqueue shares the producer's transaction.
"""

from __future__ import annotations

import ssl
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from core.config import settings


class CrmBase(DeclarativeBase):
    """Declarative base for the CRM tables. Mirrors the annexes' ``CrmBase``."""


def _build_connect_args(setting_db_ssl: bool) -> dict:
    """Build asyncpg connect_args, enabling SSL/TLS for non-local environments."""
    if not setting_db_ssl:
        return {}
    ctx = ssl.create_default_context()
    return {"ssl": ctx}


crm_engine = create_async_engine(
    settings.CRM_DATABASE_URL or "postgresql+asyncpg://localhost/unset",
    pool_pre_ping=True,
    pool_size=settings.CRM_DB_POOL_SIZE,
    max_overflow=settings.CRM_DB_MAX_OVERFLOW,
    pool_recycle=settings.CRM_DB_POOL_RECYCLE,
    pool_timeout=settings.CRM_DB_POOL_TIMEOUT,
    connect_args=_build_connect_args(settings.CRM_DB_SSL),
)

AsyncSessionCRMFactory = async_sessionmaker(
    crm_engine,
    # Attributes stay readable after a commit, which the dispatch loop relies on
    # when it reports what it sent.
    expire_on_commit=False,
)


async def get_crm_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionCRMFactory() as session:
        yield session
