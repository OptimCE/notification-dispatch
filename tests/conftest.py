"""Test fixtures for notification-dispatch.

Infrastructure:
  pytest-docker — spins up tests/docker-compose.test.yml at session start and
  tears it down at the end. No manual `docker compose up`.

Schema:
  This service owns no tables, so there is no `scripts/sql/schema.sql` to apply.
  The only DDL is `tests/sql/crm_test_schema.sql`, the mirror of the CRM tables
  the worker drives (owned by crm-backend). Applied with asyncpg directly.

Session isolation:
  Each test runs inside a connection-level transaction rolled back on teardown.
  `join_transaction_mode="create_savepoint"` turns the dispatch loop's real
  `commit()` calls into RELEASE SAVEPOINT, so the loop's transaction boundaries
  are genuinely exercised while the outer rollback still cleans up.

Transport:
  Never real. `.env.test` sets `EMAIL_TRANSPORT=NOOP` so nothing constructed at
  import can fail, and each test injects its own recording fake into
  `dispatch_once(transport=...)` — the house style everywhere in this monorepo
  (hand-rolled fakes that record calls; there is no `respx` here).
"""

import os
import socket
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# Force the test environment file before any project module is imported:
# core.config.Settings reads ENV at import time to choose .env.<env>.
os.environ.setdefault("ENV", "test")

# Must match tests/docker-compose.test.yml: port 5433.
TEST_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5433/test_db_be"
_ASYNCPG_URL = "postgresql://postgres:postgres@localhost:5433/test_db_be"

CRM_TEST_SCHEMA_SQL = Path(__file__).parent / "sql" / "crm_test_schema.sql"


@pytest.fixture(scope="session")
def docker_compose_file(pytestconfig):
    return os.path.join(str(pytestconfig.rootdir), "tests", "docker-compose.test.yml")


@pytest.fixture(scope="session")
def docker_compose_project_name():
    return "notification-dispatch-test"


def _is_pg_ready(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def test_db_ready(request):
    """Block until Postgres accepts connections on 5433.

    In CI the database is a service container that is already running; locally,
    pytest-docker starts it from tests/docker-compose.test.yml first.
    """
    if os.getenv("CI"):
        import time

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if _is_pg_ready("localhost", 5433):
                return 5433
            time.sleep(0.5)
        raise RuntimeError("Postgres not ready on port 5433 after 30 s")

    docker_services = request.getfixturevalue("docker_services")
    docker_ip = request.getfixturevalue("docker_ip")
    port = docker_services.port_for("db-test", 5432)
    docker_services.wait_until_responsive(
        timeout=30.0, pause=0.5, check=lambda: _is_pg_ready(docker_ip, port)
    )
    return port


@pytest.fixture(scope="session", autouse=True)
def apply_schema(test_db_ready):
    import asyncio

    import asyncpg

    schema_sql = CRM_TEST_SCHEMA_SQL.read_text(encoding="utf-8")

    async def _apply():
        conn = await asyncpg.connect(_ASYNCPG_URL)
        await conn.execute(schema_sql)
        await conn.close()

    async def _teardown():
        conn = await asyncpg.connect(_ASYNCPG_URL)
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        await conn.close()

    asyncio.run(_apply())
    yield
    asyncio.run(_teardown())


@pytest.fixture(scope="session")
def test_engine(apply_schema):
    """Session-scoped SYNC fixture, with NullPool.

    Sync (not pytest_asyncio) and unpooled for the same reason as every other
    service here: on Windows the session-scoped event loop is already closed by
    the time an async fixture finaliser runs, and asyncpg then raises
    `AttributeError: 'NoneType' object has no attribute 'send'`.
    """
    import asyncio

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield engine
    asyncio.run(engine.dispose())


@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    async with test_engine.connect() as conn:
        await conn.begin()
        factory = async_sessionmaker(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        session = factory()
        yield session
        await session.close()
        await conn.rollback()
