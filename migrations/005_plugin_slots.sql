-- Migration 005: Plugin slot enable/disable configuration
-- Stores per-plugin per-slot enabled state for the Integrations UI.
-- Defaults to enabled (1) when no row exists — only explicit disablements are stored.

CREATE TABLE IF NOT EXISTS plugin_slots (
    plugin_name TEXT NOT NULL,
    slot        TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (plugin_name, slot)
);
