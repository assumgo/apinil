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

CREATE TABLE IF NOT EXISTS condicao_pagamento (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    qtde_parcelas integer NOT NULL,
    nome text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT condicao_pagamento_nome_uniq UNIQUE (nome),
    CONSTRAINT condicao_pagamento_qtde_chk CHECK (qtde_parcelas BETWEEN 1 AND 48)
);

CREATE UNIQUE INDEX IF NOT EXISTS condicao_pagamento_nome_lower_idx
    ON condicao_pagamento (lower(nome));

CREATE TABLE IF NOT EXISTS centro_custo (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    codigo_reduzido text NOT NULL,
    nome text NOT NULL,
    descricao text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT centro_custo_codigo_uniq UNIQUE (codigo_reduzido)
);

CREATE UNIQUE INDEX IF NOT EXISTS centro_custo_codigo_lower_idx
    ON centro_custo (lower(codigo_reduzido));

ALTER TABLE app_user ADD COLUMN IF NOT EXISTS email text;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS must_change_password boolean NOT NULL DEFAULT false;

CREATE UNIQUE INDEX IF NOT EXISTS app_user_username_lower_idx
    ON app_user (lower(username));
CREATE UNIQUE INDEX IF NOT EXISTS app_user_email_lower_idx
    ON app_user (lower(email))
    WHERE email IS NOT NULL AND btrim(email) <> '';
