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

CREATE TABLE IF NOT EXISTS tipo_documento (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nome text NOT NULL,
    descricao text NOT NULL,
    esfera text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT tipo_documento_nome_uniq UNIQUE (nome),
    CONSTRAINT tipo_documento_esfera_chk CHECK (
        esfera IN ('Nacional', 'Federal', 'Estadual', 'Municipal', 'Geral')
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS tipo_documento_nome_lower_idx
    ON tipo_documento (lower(nome));

CREATE TABLE IF NOT EXISTS forma_pagamento (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nome text NOT NULL,
    descricao text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT forma_pagamento_nome_uniq UNIQUE (nome),
    CONSTRAINT forma_pagamento_descricao_uniq UNIQUE (descricao)
);

ALTER TABLE forma_pagamento ADD COLUMN IF NOT EXISTS nome text;

CREATE UNIQUE INDEX IF NOT EXISTS forma_pagamento_nome_lower_idx
    ON forma_pagamento (lower(nome));
CREATE UNIQUE INDEX IF NOT EXISTS forma_pagamento_descricao_lower_idx
    ON forma_pagamento (lower(descricao));
