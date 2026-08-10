"""NoopTransport — logs instead of sending.

For tests and for a local stack with no mail catcher. The config validator
refuses ``EMAIL_TRANSPORT=NOOP`` outside local/test precisely because this
adapter marks everything SENT while no mail leaves the building, which is the
one failure mode no dashboard would ever show.
"""

from __future__ import annotations

import logging

from domain.message import EmailMessage

logger = logging.getLogger(__name__)


class NoopTransport:
    async def send(self, message: EmailMessage) -> str | None:
        logger.info(
            "noop email -> to=%s subject=%s",
            message.to,
            message.subject,
            extra={"operation": "dispatch:send", "transport": "noop"},
        )
        return None

    async def aclose(self) -> None:
        return None
