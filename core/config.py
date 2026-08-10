"""Environment-driven settings (pydantic-settings), worker-only.

No HTTP surface, no NATS, and no database of its own — this service polls the
``outbound_message`` table in the CRM database and sends what it finds. ``ENV``
selects the ``.env.<env>`` file at import time; a module ``model_validator``
enforces the variables that must be present per environment.
"""

from __future__ import annotations

import os
from enum import StrEnum

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The transports `EMAIL_TRANSPORT` may name. Kept here rather than in the
# factory so the settings can reject a typo at boot instead of at first send.
TRANSPORTS = ("SMTP", "BREVO", "NOOP")


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


def _get_env_file() -> str:
    env = os.getenv("ENV", "local").strip()
    return f".env.{env}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_get_env_file(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        # Tolerate leftover variables from the shared compose env block.
        extra="ignore",
    )

    # ---- Core ----
    ENV: Environment = Environment.LOCAL

    # ---- CRM database ----
    # The only database this service touches, and it owns none of it: the
    # delivery tables live in the CRM schema, owned by crm-backend. There is
    # deliberately no LOCAL_DATABASE_URL.
    CRM_DATABASE_URL: str = ""
    # A poller, not an API — it holds one short transaction per tick. Do not
    # copy the annexes' pool of 20.
    CRM_DB_POOL_SIZE: int = 5
    CRM_DB_MAX_OVERFLOW: int = 5
    CRM_DB_POOL_RECYCLE: int = 3600
    CRM_DB_POOL_TIMEOUT: int = 30
    CRM_DB_SSL: bool = False

    # ---- Email transport ----
    # SMTP | BREVO | NOOP. There is no "unconfigured → silently disabled" state:
    # transactional email is a channel, not a feature, so a missing transport is
    # a boot failure rather than a quiet no-op.
    EMAIL_TRANSPORT: str = "NOOP"
    EMAIL_FROM_ADDRESS: str = ""
    EMAIL_FROM_NAME: str = "OptimCE"
    EMAIL_REPLY_TO: str = ""
    # Absolute base URL used for links inside email bodies. Relative links are
    # meaningless in a mail client.
    APP_URL: str = ""
    # Used for the stable Message-ID domain. Falls back to the sender domain.
    EMAIL_MESSAGE_ID_DOMAIN: str = ""

    # ---- SMTP transport ----
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_STARTTLS: bool = True
    SMTP_TLS: bool = False
    SMTP_TIMEOUT_SECONDS: int = 15

    # ---- Brevo transport ----
    BREVO_API_KEY: str = ""
    BREVO_BASE_URL: str = "https://api.brevo.com"
    BREVO_TIMEOUT_SECONDS: int = 10
    # Brevo reports bounces asynchronously, by webhook only. This service has no
    # inbound HTTP surface by design, so suppression is kept current by polling
    # Brevo's own blocked-contact list instead. 0 disables the sync.
    BREVO_SUPPRESSION_SYNC_INTERVAL_SECONDS: int = 3600

    # ---- Dispatch loop ----
    DISPATCH_POLL_INTERVAL_SECONDS: int = 15
    DISPATCH_BATCH_SIZE: int = 20
    DISPATCH_MAX_ATTEMPTS: int = 5
    DISPATCH_BACKOFF_BASE_SECONDS: int = 60
    # A row CLAIMED longer ago than this is assumed abandoned by a dead worker
    # and returned to PENDING. Must be comfortably above the transport timeout.
    DISPATCH_CLAIM_STALE_SECONDS: int = 300

    # ---- Localisation ----
    # Applied when a queued message carries no locale (``locale = ''``), which is
    # every message for a user who never chose a language. Wallonia-first, so the
    # default is French rather than English.
    DEFAULT_LOCALE: str = "fr"

    # ---- Observability ----
    LOGGING_TOKEN: str = ""
    LOGGING_TRACES_URL: str = ""
    LOGGING_LOGS_URL: str = ""
    LOGGING_METRICS_URL: str = ""

    @model_validator(mode="after")
    def validate_env_config(self) -> Settings:
        transport = self.EMAIL_TRANSPORT.strip().upper()
        if transport not in TRANSPORTS:
            raise ValueError(f"EMAIL_TRANSPORT must be one of {', '.join(TRANSPORTS)}")
        if self.DISPATCH_MAX_ATTEMPTS < 1:
            raise ValueError("DISPATCH_MAX_ATTEMPTS must be >= 1")
        if self.DISPATCH_POLL_INTERVAL_SECONDS < 1:
            raise ValueError("DISPATCH_POLL_INTERVAL_SECONDS must be >= 1")
        if self.ENV != Environment.TEST and not self.CRM_DATABASE_URL.strip():
            raise ValueError("CRM_DATABASE_URL is required")
        if self.ENV in (Environment.STAGING, Environment.PRODUCTION):
            # NOOP outside local/test is the dangerous configuration: every
            # message would be marked SENT, no mail would leave, and every
            # dashboard would look healthy. Refuse to boot instead.
            if transport == "NOOP":
                raise ValueError("EMAIL_TRANSPORT=NOOP is not allowed in staging/production")
            if not self.EMAIL_FROM_ADDRESS.strip():
                raise ValueError("EMAIL_FROM_ADDRESS is required in staging/production")
            if not self.APP_URL.strip():
                raise ValueError("APP_URL is required in staging/production")
        if self.ENV == Environment.PRODUCTION:
            if not self.LOGGING_TOKEN:
                raise ValueError("LOGGING_TOKEN required for production")
            if not self.LOGGING_LOGS_URL:
                raise ValueError("LOGGING_LOGS_URL required for production")
            if not self.LOGGING_METRICS_URL:
                raise ValueError("LOGGING_METRICS_URL required for production")
        return self


settings = Settings()
