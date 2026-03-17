-- 007_api_key.sql
--
-- API key table for securing the v1 REST API.
-- Single active key — DELETE + INSERT on regeneration.
--

CREATE TABLE IF NOT EXISTS api_keys (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT    NOT NULL UNIQUE,
    created_at TEXT    DEFAULT CURRENT_TIMESTAMP
);
