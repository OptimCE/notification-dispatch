"""Worker process entry point.

Bootstraps logging and tracing, builds the configured email transport, then
polls ``outbound_message`` until SIGINT/SIGTERM.

**Polling, not LISTEN/NOTIFY, and deliberately so.** ``NOTIFY`` is not durable:
Postgres does not queue for absent listeners, so every message fired while this
worker is restarting would be lost. A polling sweep is therefore required as a
backstop regardless — and one is needed anyway for retry backoff, for reaping
rows a dead worker was holding, and for reviewing failures. Since the sweep must
exist, LISTEN/NOTIFY would buy only latency, which email does not need. It would
also need a dedicated long-lived connection outside the pool.

Run locally:

    python -m worker.main
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import logging
import pathlib
import signal
import sys

from adapters.transports.factory import build_transport
from core.config import settings
from core.database.database import AsyncSessionCRMFactory, crm_engine
from core.logging import configure_logging
from core.tracing import setup_tracer_provider
from infra.outbound_repository import OutboundRepository
from worker.dispatch import dispatch_once
from worker.suppression import sync_provider_suppressions

configure_logging()
logger = logging.getLogger(__name__)

# Touched every tick while the loop is alive. The container HEALTHCHECK reads
# this file's mtime to tell a live worker from a hung one.
_HEARTBEAT_PATH = pathlib.Path("/tmp/worker.alive")  # noqa: S108 — dedicated container, non-root

# How long a tick may take before the loop is considered wedged. Well above a
# full batch of transport timeouts.
_TICK_TIMEOUT_SECONDS = 300


async def main() -> None:
    setup_tracer_provider()
    # Built once and shared, so an HTTP transport reuses its connection pool
    # across ticks. Also fails fast: a misconfigured transport raises here, at
    # boot, rather than on the first message.
    transport = build_transport(settings)
    shutdown = asyncio.Event()
    _install_signal_handlers(shutdown)

    suppression_task = asyncio.create_task(_suppression_loop(shutdown))
    logger.info(
        "notification-dispatch ready (transport=%s, interval=%ss)",
        settings.EMAIL_TRANSPORT,
        settings.DISPATCH_POLL_INTERVAL_SECONDS,
    )
    try:
        while not shutdown.is_set():
            _touch_heartbeat()
            try:
                result = await asyncio.wait_for(
                    dispatch_once(transport=transport, settings=settings),
                    timeout=_TICK_TIMEOUT_SECONDS,
                )
                if result.claimed or result.reclaimed:
                    logger.info(
                        "dispatch tick: claimed=%s sent=%s retried=%s failed=%s "
                        "suppressed=%s reclaimed=%s",
                        result.claimed,
                        result.sent,
                        result.retried,
                        result.failed,
                        result.suppressed,
                        result.reclaimed,
                    )
            except TimeoutError:
                logger.error("dispatch tick exceeded %ss; continuing", _TICK_TIMEOUT_SECONDS)
            except Exception:
                # One bad tick must not kill the worker: the rows it claimed stay
                # CLAIMED and the reaper returns them on a later pass.
                logger.exception("dispatch tick failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    shutdown.wait(), timeout=settings.DISPATCH_POLL_INTERVAL_SECONDS
                )
    finally:
        suppression_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await suppression_task
        await transport.aclose()
        await crm_engine.dispose()
        logger.info("notification-dispatch stopped")


async def _suppression_loop(shutdown: asyncio.Event) -> None:
    """Periodically pull the provider's blocklist. No-op unless Brevo is active."""
    interval = settings.BREVO_SUPPRESSION_SYNC_INTERVAL_SECONDS
    if interval <= 0 or settings.EMAIL_TRANSPORT.strip().upper() != "BREVO":
        return
    since: datetime.datetime | None = None
    while not shutdown.is_set():
        try:
            async with AsyncSessionCRMFactory() as session:
                seen = await sync_provider_suppressions(
                    repo=OutboundRepository(session), cfg=settings, since=since
                )
                await session.commit()
            if seen:
                logger.info("suppression sync recorded %s blocked contact(s)", seen)
            # Overlap by a day so a message modified around the cursor boundary
            # is never skipped; `suppress` is idempotent, so re-seeing one costs
            # nothing.
            since = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1)
        except Exception:
            logger.exception("suppression sync failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown.wait(), timeout=interval)


def _touch_heartbeat() -> None:
    try:
        _HEARTBEAT_PATH.touch()
    except OSError:
        logger.debug("could not touch %s", _HEARTBEAT_PATH)


def _install_signal_handlers(shutdown: asyncio.Event) -> None:
    """Wire SIGINT/SIGTERM to ``shutdown`` (POSIX + Windows)."""
    loop = asyncio.get_running_loop()

    def _set_event() -> None:
        if not shutdown.is_set():
            shutdown.set()

    if sys.platform == "win32":
        signal.signal(signal.SIGINT, lambda *_: _set_event())
        signal.signal(signal.SIGTERM, lambda *_: _set_event())
        return

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _set_event)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _set_event())


if __name__ == "__main__":
    asyncio.run(main())
