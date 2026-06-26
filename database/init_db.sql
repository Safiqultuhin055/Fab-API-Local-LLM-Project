/* ============================================================
   Local Ollama AI Gateway — SQL Server schema (prototype track)
   Target: SQL Server 2019+ / LocalDB.  Keep in lockstep with app/db/models.py.
   The app also auto-creates these via SQLAlchemy create_all; this file is the
   canonical DDL for DBAs and the diagram's database/init_db.sql.
   ============================================================ */

IF DB_ID('ai_gateway') IS NULL
    CREATE DATABASE ai_gateway;
GO
USE ai_gateway;
GO

/* ---------- api_keys ----------
   Secret material is NEVER stored raw: only an HMAC-SHA256 hash + a short
   non-secret display prefix. The full key is shown once at creation. */
IF OBJECT_ID('dbo.api_keys', 'U') IS NULL
CREATE TABLE dbo.api_keys (
    id            INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    key_hash      NVARCHAR(64)  NOT NULL,
    key_prefix    NVARCHAR(16)  NOT NULL,
    key_encrypted NVARCHAR(MAX) NULL,        -- Fernet ciphertext of full key (UI re-copy)
    owner_name    NVARCHAR(200) NOT NULL,
    status        NVARCHAR(20)  NOT NULL CONSTRAINT DF_api_keys_status DEFAULT('active'),
    tier          NVARCHAR(20)  NOT NULL CONSTRAINT DF_api_keys_tier   DEFAULT('free'),
    rate_limit    INT           NOT NULL CONSTRAINT DF_api_keys_rl      DEFAULT(1000),
    expires_at    DATETIMEOFFSET NULL,
    last_used     DATETIMEOFFSET NULL,
    ip_whitelist  NVARCHAR(1000) NULL,
    -- audit + soft delete
    created_date  DATETIMEOFFSET NOT NULL CONSTRAINT DF_api_keys_cd DEFAULT(SYSDATETIMEOFFSET()),
    updated_date  DATETIMEOFFSET NOT NULL CONSTRAINT DF_api_keys_ud DEFAULT(SYSDATETIMEOFFSET()),
    created_by    NVARCHAR(100) NULL,
    updated_by    NVARCHAR(100) NULL,
    is_deleted    BIT           NOT NULL CONSTRAINT DF_api_keys_del DEFAULT(0),
    deleted_at    DATETIMEOFFSET NULL,
    CONSTRAINT uq_api_keys_key_hash UNIQUE (key_hash),
    CONSTRAINT ck_api_keys_status CHECK (status IN ('active','disabled','expired')),
    CONSTRAINT ck_api_keys_tier   CHECK (tier   IN ('free','pro','enterprise'))
);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='ix_api_keys_status' AND object_id=OBJECT_ID('dbo.api_keys'))
    CREATE INDEX ix_api_keys_status ON dbo.api_keys(status);
GO

/* ---------- request_logs ----------
   Privacy-aware: prompt/response are redactable/truncatable in the app per the
   retention policy before they ever reach this table. */
IF OBJECT_ID('dbo.request_logs', 'U') IS NULL
CREATE TABLE dbo.request_logs (
    id                BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    api_key_id        INT            NULL,
    key_prefix        NVARCHAR(16)   NULL,
    model             NVARCHAR(100)  NULL,
    endpoint          NVARCHAR(100)  NOT NULL,
    prompt            NVARCHAR(MAX)  NULL,
    response          NVARCHAR(MAX)  NULL,
    prompt_tokens     INT            NULL,
    completion_tokens INT            NULL,
    total_tokens      INT            NULL,
    status_code       INT            NOT NULL,
    response_time_ms  INT            NULL,
    ip_address        NVARCHAR(64)   NULL,
    user_agent        NVARCHAR(400)  NULL,
    request_id        NVARCHAR(64)   NULL,
    error             NVARCHAR(MAX)  NULL,
    created_date  DATETIMEOFFSET NOT NULL CONSTRAINT DF_logs_cd DEFAULT(SYSDATETIMEOFFSET()),
    updated_date  DATETIMEOFFSET NOT NULL CONSTRAINT DF_logs_ud DEFAULT(SYSDATETIMEOFFSET()),
    created_by    NVARCHAR(100) NULL,
    updated_by    NVARCHAR(100) NULL,
    is_deleted    BIT           NOT NULL CONSTRAINT DF_logs_del DEFAULT(0),
    deleted_at    DATETIMEOFFSET NULL,
    CONSTRAINT fk_request_logs_api_key FOREIGN KEY (api_key_id) REFERENCES dbo.api_keys(id)
);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='ix_request_logs_created_date' AND object_id=OBJECT_ID('dbo.request_logs'))
    CREATE INDEX ix_request_logs_created_date ON dbo.request_logs(created_date);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='ix_request_logs_api_key_id' AND object_id=OBJECT_ID('dbo.request_logs'))
    CREATE INDEX ix_request_logs_api_key_id ON dbo.request_logs(api_key_id);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='ix_request_logs_model' AND object_id=OBJECT_ID('dbo.request_logs'))
    CREATE INDEX ix_request_logs_model ON dbo.request_logs(model);
GO

/* ---------- settings ----------
   Key/value store. Also backs model enable/disable (models.enabled) and the
   default model (models.default). */
IF OBJECT_ID('dbo.settings', 'U') IS NULL
CREATE TABLE dbo.settings (
    id            INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    setting_key   NVARCHAR(100) NOT NULL,
    setting_value NVARCHAR(MAX) NULL,
    created_date  DATETIMEOFFSET NOT NULL CONSTRAINT DF_settings_cd DEFAULT(SYSDATETIMEOFFSET()),
    updated_date  DATETIMEOFFSET NOT NULL CONSTRAINT DF_settings_ud DEFAULT(SYSDATETIMEOFFSET()),
    created_by    NVARCHAR(100) NULL,
    updated_by    NVARCHAR(100) NULL,
    is_deleted    BIT           NOT NULL CONSTRAINT DF_settings_del DEFAULT(0),
    deleted_at    DATETIMEOFFSET NULL,
    CONSTRAINT uq_settings_key UNIQUE (setting_key)
);
GO
