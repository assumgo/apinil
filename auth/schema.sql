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

CREATE TABLE IF NOT EXISTS auth_throttle (
    bucket text PRIMARY KEY,
    failures integer NOT NULL,
    window_start timestamptz NOT NULL
);

DELETE FROM app_session WHERE length(id) <> 64 OR id !~ '^[0-9a-f]{64}$';

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

CREATE TABLE IF NOT EXISTS pessoa (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tipo text NOT NULL,
    documento text NOT NULL,
    nome text NOT NULL,
    fantasia text NOT NULL DEFAULT '',
    papel text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pessoa_tipo_chk CHECK (tipo IN ('F', 'J')),
    CONSTRAINT pessoa_papel_chk CHECK (papel IN ('fornecedor', 'cliente', 'ambos'))
);

CREATE UNIQUE INDEX IF NOT EXISTS pessoa_documento_idx ON pessoa (documento);

CREATE TABLE IF NOT EXISTS pessoa_contato (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pessoa_id bigint NOT NULL REFERENCES pessoa (id) ON DELETE CASCADE,
    tipo text NOT NULL,
    valor text NOT NULL,
    CONSTRAINT pessoa_contato_tipo_chk CHECK (
        tipo IN ('fixo', 'celular', 'whatsapp', 'email', 'email_xml', 'email_nfe', 'email_boleto')
    ),
    CONSTRAINT pessoa_contato_uniq UNIQUE (pessoa_id, tipo)
);

CREATE TABLE IF NOT EXISTS pessoa_endereco (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pessoa_id bigint NOT NULL REFERENCES pessoa (id) ON DELETE CASCADE,
    tipo text NOT NULL,
    cep text NOT NULL DEFAULT '',
    logradouro text NOT NULL DEFAULT '',
    numero text NOT NULL DEFAULT '',
    complemento text NOT NULL DEFAULT '',
    bairro text NOT NULL DEFAULT '',
    cidade text NOT NULL DEFAULT '',
    uf text NOT NULL DEFAULT '',
    CONSTRAINT pessoa_endereco_tipo_chk CHECK (tipo IN ('comercial', 'entrega', 'faturamento')),
    CONSTRAINT pessoa_endereco_uniq UNIQUE (pessoa_id, tipo)
);

CREATE TABLE IF NOT EXISTS lancamento (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    natureza text NOT NULL,
    origem text NOT NULL DEFAULT 'manual',
    situacao text NOT NULL DEFAULT 'rascunho',
    tipo_documento_id bigint REFERENCES tipo_documento (id),
    pessoa_id bigint REFERENCES pessoa (id),
    numero_documento text NOT NULL DEFAULT '',
    forma_pagamento_id bigint REFERENCES forma_pagamento (id),
    condicao_pagamento_id bigint REFERENCES condicao_pagamento (id),
    qtde_parcelas integer NOT NULL DEFAULT 1,
    valor_total numeric(14,2) NOT NULL DEFAULT 0,
    data_emissao date,
    observacao text NOT NULL DEFAULT '',
    arquivo_nome text NOT NULL DEFAULT '',
    arquivo_tipo text NOT NULL DEFAULT '',
    arquivo bytea,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT lancamento_natureza_chk CHECK (natureza IN ('pagar', 'receber')),
    CONSTRAINT lancamento_origem_chk CHECK (origem IN ('manual', 'xml', 'pdf')),
    CONSTRAINT lancamento_situacao_chk CHECK (situacao IN ('rascunho', 'confirmado')),
    CONSTRAINT lancamento_qtde_chk CHECK (qtde_parcelas BETWEEN 1 AND 48)
);

CREATE TABLE IF NOT EXISTS lancamento_parcela (
    lancamento_id bigint NOT NULL REFERENCES lancamento (id) ON DELETE CASCADE,
    numero integer NOT NULL,
    valor numeric(14,2) NOT NULL,
    vencimento date,
    PRIMARY KEY (lancamento_id, numero)
);

CREATE TABLE IF NOT EXISTS titulo (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lancamento_id bigint NOT NULL REFERENCES lancamento (id) ON DELETE CASCADE,
    natureza text NOT NULL,
    pessoa_id bigint REFERENCES pessoa (id),
    numero_documento text NOT NULL DEFAULT '',
    numero_parcela integer NOT NULL,
    valor numeric(14,2) NOT NULL,
    vencimento date NOT NULL,
    status text NOT NULL DEFAULT 'aberto',
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT titulo_natureza_chk CHECK (natureza IN ('pagar', 'receber')),
    CONSTRAINT titulo_status_chk CHECK (status IN ('aberto', 'baixado'))
);

CREATE INDEX IF NOT EXISTS titulo_natureza_idx ON titulo (natureza, vencimento);

ALTER TABLE titulo ADD COLUMN IF NOT EXISTS data_baixa date;

ALTER TABLE titulo ADD COLUMN IF NOT EXISTS forma_pagamento_id bigint REFERENCES forma_pagamento (id);

ALTER TABLE titulo ADD COLUMN IF NOT EXISTS conta text NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS lancamento_natureza_idx ON lancamento (natureza, id);
