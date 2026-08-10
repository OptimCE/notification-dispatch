"""Retry scheduling. Pure, so it is unit-testable without a clock.

The value is fed to SQL as a number of seconds to add to ``now()``; the worker
never computes an absolute timestamp itself, so a clock skew between the worker
and Postgres cannot make a message due in the past or the far future.
"""

from __future__ import annotations

# One hour. Past this a slower retry buys nothing: either the provider is back
# and the next tick sends it, or the attempt budget runs out anyway.
_MAX_DELAY_SECONDS = 3600


def backoff_seconds(*, attempts: int, base_seconds: int, jitter: float = 1.0) -> int:
    """Delay before the next attempt: ``base * 2^(attempts-1)``, capped and jittered.

    ``attempts`` is the value AFTER the claim incremented it, so the first
    failure (attempts=1) waits one base interval.

    ``jitter`` is passed in rather than drawn here so this stays a pure
    function; the caller supplies ``0.5 + random()`` in production. Without it,
    a provider outage synchronises every queued message onto the same retry
    instant and the recovery becomes a thundering herd.
    """
    exponent = max(attempts - 1, 0)
    # Cap the exponent before shifting: a runaway attempts value would otherwise
    # build an enormous integer before min() ever saw it.
    if exponent > 20:
        exponent = 20
    delay = min(base_seconds * (2**exponent), _MAX_DELAY_SECONDS)
    return max(int(delay * jitter), 1)
