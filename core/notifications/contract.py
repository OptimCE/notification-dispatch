"""Producer-facing notification contract (IMPLEMENTATION_PLAN.md §1.3).

This module deliberately imports nothing from the service around it, so it is
byte-identical in every producer and lifts verbatim into the standalone
notification service in Phase 2. It mirrors crm-backend's
``src/modules/notifications/api/notification.dtos.ts`` — keep the two in step.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum
from typing import Final, Literal


class Channel(IntEnum):
    """A delivery channel a producer *requests*. Never guaranteed.

    The values are the on-disk encoding: they land verbatim in the ``SMALLINT``
    ``channel`` columns of ``notification_preference`` and ``outbound_message``
    (Phase 1 step 3), and in the Phase 2 wire payload. Never renumber.
    """

    INAPP = 1
    EMAIL = 2


class NotificationCategory(IntEnum):
    """The producer's statement of *kind*, which decides opt-out policy.

    ``TRANSACTIONAL`` — an invoice, an invitation, a missed regulatory deadline.
    Consequential, so it overrides the recipient's channel preferences and must
    never offer an unsubscribe link.
    ``INFORMATIONAL`` — news, digests, reminders. An opt-out must exist and be
    honoured.

    Orthogonal to ``channels`` on purpose: the producer states intent, the
    notification layer owns policy. That split is the whole reason the layer can
    be extracted later, so it holds from day one.
    """

    TRANSACTIONAL = 1
    INFORMATIONAL = 2


# crm-backend owns this vocabulary:
#   community_user.role VARCHAR(50) CHECK (role IN ('ADMIN','MANAGER','MEMBER'))
# It is a CRM schema domain rather than an annexe auth enum, which is why these
# are literals and this module stays import-free. ADMIN is included because it
# outranks MANAGER.
MANAGER_ROLES: Final[tuple[str, ...]] = ("ADMIN", "MANAGER")


@dataclass(frozen=True, slots=True)
class UserTarget:
    """One recipient, by INTERNAL ``app_user.id``."""

    user_id: int
    community_id: int | None = None
    kind: Literal["user"] = "user"


@dataclass(frozen=True, slots=True)
class UsersTarget:
    """An explicit set of recipients, by INTERNAL ``app_user.id``.

    De-duplicated by ``publish``; caller order is preserved so a fan-out stays
    deterministic and testable.
    """

    user_ids: Sequence[int]
    community_id: int | None = None
    kind: Literal["users"] = "users"


@dataclass(frozen=True, slots=True)
class CommunityTarget:
    """Every member of a community, optionally narrowed and minus one author.

    ``roles`` narrows to ``community_user.role`` values (see ``MANAGER_ROLES``).

    ``exclude_auth_user_id`` is a Keycloak ``sub``, not an internal id: every
    Python producer has the sub in ``current_user_id`` and none of them has the
    internal id without a CRM read, which ``publish`` already knows how to do.
    The TypeScript union has no equivalent because its callers resolve the
    audience up front; the field is additive, so the contracts stay compatible.
    """

    community_id: int
    roles: Sequence[str] | None = None
    exclude_auth_user_id: str | None = None
    kind: Literal["community"] = "community"


NotificationTarget = UserTarget | UsersTarget | CommunityTarget
