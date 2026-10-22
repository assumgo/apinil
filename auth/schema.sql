CREATE TABLE IF NOT EXISTS app_user (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username text NOT NULL UNIQUE,
    password_hash text NOT NULL,
    display_name text NOT NULL,
    role text NOT NULL DEFAULT 'master',
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app_session (
    id text PRIMARY KEY,
    user_id bigint NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS app_session_user_idx ON app_session (user_id);
CREATE INDEX IF NOT EXISTS app_session_expires_idx ON app_session (expires_at);
