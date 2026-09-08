-- Platform admin: separate concept from tenant roles (Fix for H0)
ALTER TABLE users ADD COLUMN is_platform_admin BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX users_platform_admin_idx ON users (is_platform_admin) WHERE is_platform_admin;