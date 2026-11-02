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
    # billing/api/billing/service.py::_issued_notification. Neither carries a
    # due_date: nobody pays a credit note, and the community pays the producer.
    # `amount` is the credited magnitude (the credit note's own total is
    # negative); `corrects_number` is null if the credited invoice had no number.
    "credit_note.issued": {
        "invoice_id": 8413,
        "number": "NC-2026-00001",
        "amount": "5.45",
        "currency": "EUR",
        "corrects_number": "F-2026-00001",
    },
    "producer_statement.issued": {
        "invoice_id": 8414,
        "number": "DP-2026-00001",
        "total": "1.20",
        "currency": "EUR",
    },
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
    # administrative-document worker/digest.py: a community's summary, every
    # section and item kind a template may meet (news of its period: a
    # deadline missed, a third party late, a procedure in force, an answer
    # recorded and a mail to qualify; overdue of the community and of a third
    # party; an item with no operation).
    "admin_digest.summary": {
        "as_of": "2026-11-02",
        "as_of_display": "02/11/2026",
        "counts": {"news": 5, "overdue": 2, "upcoming": 2},
        "communities": [
            {
                "id": 10,
                "name": "CER Exemple",
                "since": "2026-10-26T05:00:12+00:00",
                "since_display": "26/10/2026",
                "until": "2026-11-16",
                "until_display": "16/11/2026",
                "news": [
                    {
                        "deadline_id": 92,
                        "dossier_id": 8,
                        "path": "/administrative-document/procedures/8",
                        "procedure": {"fr": "P", "en": "P", "nl": "P", "de": "P"},
                        "deadline": {"fr": "D", "en": "D", "nl": "D", "de": "D"},
                        "operation": None,
                        "due_date": "2026-11-01",
                        "due_display": "01/11/2026",
                        "days_left": -1,
                        "party": "community",
                        "news": "missed",
                    },
                    {
                        "deadline_id": 93,
                        "dossier_id": 9,
                        "path": "/administrative-document/procedures/9",
                        "procedure": {"fr": "P", "en": "P", "nl": "P", "de": "P"},
                        "deadline": {"fr": "D", "en": "D", "nl": "D", "de": "D"},
                        "operation": "Op Sud",
                        "due_date": "2026-10-30",
                        "due_display": "30/10/2026",
                        "days_left": -3,
                        "party": "grd",
                        "news": "late",
                    },
                    {
                        "dossier_id": 7,
                        "path": "/administrative-document/procedures/7",
                        "procedure": {
                            "fr": "Modification du partage",
                            "en": "Sharing change",
                            "nl": "Wijziging van de deling",
                            "de": "Änderung der Teilung",
                        },
                        "operation": "Op Centre",
                        "news": "in_force",
                        "effective_date": "2026-11-02",
                        "effective_display": "02/11/2026",
                        "response": None,
                    },
                    {
                        "dossier_id": 9,
                        "path": "/administrative-document/procedures/9",
                        "procedure": {"fr": "P", "en": "P", "nl": "P", "de": "P"},
                        "operation": "Op Sud",
                        "news": "answer",
                        "effective_date": None,
                        "effective_display": None,
                        "response": {
                            "fr": "Accord",
                            "en": "Approval",
                            "nl": "Goedkeuring",
                            "de": "Zustimmung",
                        },
                    },
                    {
                        "dossier_id": 8,
                        "path": "/administrative-document/procedures/8",
                        "procedure": {"fr": "P", "en": "P", "nl": "P", "de": "P"},
                        "operation": None,
                        "news": "answer",
                        "effective_date": None,
                        "effective_display": None,
                        "response": None,
                    },
                ],
                "overdue": [
                    {
                        "deadline_id": 92,
                        "dossier_id": 8,
                        "path": "/administrative-document/procedures/8",
                        "procedure": {"fr": "P", "en": "P", "nl": "P", "de": "P"},
                        "deadline": {"fr": "D", "en": "D", "nl": "D", "de": "D"},
                        "operation": None,
                        "due_date": "2026-11-01",
                        "due_display": "01/11/2026",
                        "days_left": -1,
                        "party": "community",
                    },
                    {
                        "deadline_id": 93,
                        "dossier_id": 9,
                        "path": "/administrative-document/procedures/9",
                        "procedure": {"fr": "P", "en": "P", "nl": "P", "de": "P"},
                        "deadline": {"fr": "D", "en": "D", "nl": "D", "de": "D"},
                        "operation": "Op Sud",
                        "due_date": "2026-10-30",
                        "due_display": "30/10/2026",
                        "days_left": -3,
                        "party": "regulator",
                    },
                ],
                "upcoming": [
                    {
                        "deadline_id": 91,
                        "dossier_id": 7,
                        "path": "/administrative-document/procedures/7",
                        "procedure": {"fr": "P", "en": "P", "nl": "P", "de": "P"},
                        "deadline": {"fr": "D", "en": "D", "nl": "D", "de": "D"},
                        "operation": "Op Centre",
                        "due_date": "2026-11-09",
                        "due_display": "09/11/2026",
                        "days_left": 7,
                        "party": "community",
                    },
                    {
                        "deadline_id": 94,
                        "dossier_id": 7,
                        "path": "/administrative-document/procedures/7",
                        "procedure": {"fr": "P", "en": "P", "nl": "P", "de": "P"},
                        "deadline": {"fr": "D", "en": "D", "nl": "D", "de": "D"},
                        "operation": "Op Centre",
                        "due_date": "2026-11-03",
                        "due_display": "03/11/2026",
                        "days_left": 1,
                        "party": "community",
                    },
                ],
            }
        ],
    },
    # The fallback renders from `type` alone and must reference no data at all,
    # which is what lets an unknown type from a future producer degrade instead
    # of dead-lettering.
    "_default": {},
}
