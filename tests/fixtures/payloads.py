"""Canonical `data` payloads, copied verbatim from each producer.

This is the contract the templates are written against, and the only place it
is written down on this side of the wire — `notification-dispatch` cannot import
the annexes' `types.py`. Each entry cites its source so a producer change can be
traced here.

`tests/test_templates.py` renders every template against these, and a separate
AST check asserts no template references a key that is not present, which is
what makes a producer renaming a field a red test rather than a dead-lettered
email in production.
"""

from typing import Any

#: type -> the payload that producer sends.
PAYLOADS: dict[str, dict[str, Any]] = {
    # crm-backend/src/modules/invitations/infra/invitation.service.ts
    "member_invitation.received": {"invitation_id": 12, "community_id": 1},
    "manager_invitation.received": {"invitation_id": 13, "community_id": 1},
    # billing/api/billing/service.py::issue_invoice
    "invoice.issued": {
        "invoice_id": 8412,
        "number": "2026-0001",
        "due_date": "2026-09-15",
        "total": "5.45",
        "currency": "EUR",
    },
    # billing/api/billing/service.py::sweep_overdue
    "invoice.overdue": {"invoice_id": 8412, "number": "2026-0001"},
    # administrative-document/api/administrative_document/service.py::sweep_deadlines
    "admin_deadline.due_soon": {
        "deadline_id": 91,
        "dossier_id": 7,
        "deadline_type": "COMPLETENESS",
        "due_date": "2026-08-14",
        "as_of": "2026-08-03",
    },
    "admin_deadline.missed": {
        "deadline_id": 91,
        "dossier_id": 7,
        "deadline_type": "COMPLETENESS",
        "due_date": "2026-08-14",
    },
    # The fallback renders from `type` alone and must reference no data at all,
    # which is what lets an unknown type from a future producer degrade instead
    # of dead-lettering.
    "_default": {},
}
