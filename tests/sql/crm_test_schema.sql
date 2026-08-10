-- Test-only DDL for the CRM tables this worker drives.
--
-- The real schema is owned by crm-backend
-- (database_script/2026-08-03_notification_delivery.sql). This service owns no
-- tables at all, so there is deliberately no scripts/sql/schema.sql beside this
-- file — the queue lives in someone else's database and that is the whole point:
-- the enqueue rides on the producer's transaction, which is only expressible if
-- the table is in the producer's database.
--
-- `community` and `app_user` are mirrored because the claim path reads community
-- names for the templates and the fixtures need somewhere to hang a recipient.

CREATE TABLE IF NOT EXISTS community (
    id                INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name              VARCHAR(255) NOT NULL UNIQUE,
    auth_community_id VARCHAR(255) NOT NULL UNIQUE,
    created_at        TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_user (
    id            INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    auth_user_id  VARCHAR(255) NOT NULL UNIQUE,
    email         VARCHAR(256) NOT NULL,
    locale        VARCHAR(8)   NULL,
    first_name    TEXT         NULL,
    last_name     TEXT         NULL
);

CREATE TABLE IF NOT EXISTS notification (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id_community INTEGER REFERENCES community (id) ON DELETE CASCADE,
    id_user      INTEGER NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    type         VARCHAR(128) NOT NULL,
    data         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    read_at      TIMESTAMPTZ,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- The queue this worker drives.
CREATE TABLE IF NOT EXISTS outbound_message (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id_notification BIGINT NULL REFERENCES notification (id) ON DELETE SET NULL,
    id_community    INTEGER NULL REFERENCES community (id) ON DELETE CASCADE,
    channel         SMALLINT     NOT NULL CHECK (channel IN (1, 2)),
    recipient       VARCHAR(320) NOT NULL,
    recipient_name  VARCHAR(255) NULL,
    locale          VARCHAR(8)   NOT NULL DEFAULT '',
    type            VARCHAR(128) NOT NULL,
    category        SMALLINT     NOT NULL CHECK (category IN (1, 2)),
    data            JSONB        NOT NULL DEFAULT '{}'::jsonb,
    dedupe_key      VARCHAR(200) NOT NULL,
    status          SMALLINT     NOT NULL DEFAULT 1 CHECK (status IN (1, 2, 3, 4, 5)),
    attempts        SMALLINT     NOT NULL DEFAULT 0,
    last_error      TEXT         NULL,
    scheduled_for   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    claimed_at      TIMESTAMPTZ  NULL,
    sent_at         TIMESTAMPTZ  NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_outbound_message_dedupe
    ON outbound_message (dedupe_key);
CREATE INDEX IF NOT EXISTS ix_outbound_message_due
    ON outbound_message (scheduled_for) WHERE status = 1;
CREATE INDEX IF NOT EXISTS ix_outbound_message_stale
    ON outbound_message (claimed_at) WHERE status = 5;

CREATE TABLE IF NOT EXISTS email_suppression (
    email      VARCHAR(320) PRIMARY KEY,
    reason     SMALLINT     NOT NULL CHECK (reason IN (1, 2, 3, 4)),
    detail     TEXT         NULL,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS notification_preference (
    id_user     INTEGER      NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    type_prefix VARCHAR(128) NOT NULL,
    channel     SMALLINT     NOT NULL CHECK (channel IN (1, 2)),
    mode        SMALLINT     NOT NULL CHECK (mode IN (1, 3)),

    PRIMARY KEY (id_user, type_prefix, channel)
);
