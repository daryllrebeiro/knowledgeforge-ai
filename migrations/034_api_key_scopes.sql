-- Fix 6: Granular permission scopes for API keys.
-- Allows restricting API keys to least privilege (e.g. read:documents, write:documents, query:ask, admin:schemas).
-- Default is '{"*"}' for backward compatibility with existing keys inheriting creator role.
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS scopes TEXT[] NOT NULL DEFAULT '{"*"}';
