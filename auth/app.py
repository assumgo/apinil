#!/usr/bin/env python3
"""Área restrita da Apinil: login, sessão e páginas internas."""

from __future__ import annotations

import hashlib
import hmac
import html
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import psycopg

SESSION_COOKIE = "apinil_session"
SESSION_HOURS = 12
PBKDF2_ROUNDS = 600_000
MAX_FORM_BYTES = 64 * 1024
PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"
STATIC_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
    ".xml": "application/xml",
    ".ico": "image/x-icon",
    ".pdf": "application/pdf",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}

CADASTROS = (
    ("/restrito/cadastros/pessoas", "Pessoas", "Clientes, fornecedores e demais pessoas da fábrica."),
    ("/restrito/cadastros/tipos-de-documentos", "Tipos de Documentos", "Tipos usados em títulos e lançamentos."),
    ("/restrito/cadastros/condicoes-de-pagamentos", "Condições de Pagamentos", "Prazos e parcelas das negociações."),
    ("/restrito/cadastros/formas-de-pagamentos", "Formas de Pagamentos", "Meios usados para pagar e receber."),
    ("/restrito/cadastros/centros-de-custo", "Centros de Custo", "Centros usados para classificar gastos e receitas."),
    ("/restrito/cadastros/usuarios", "Usuários", "Pessoas com acesso à área restrita."),
)

MODULES = (
    ("/restrito/contas-a-pagar", "Contas a pagar", "Compromissos e pagamentos da indústria."),
    ("/restrito/contas-a-receber", "Contas a receber", "Vendas e recebimentos."),
    ("/restrito/titulos", "Titulos", "Títulos da fábrica, a pagar e a receber."),
)

TIPOS_PATH = "/restrito/cadastros/tipos-de-documentos"
ESFERAS = ("Nacional", "Federal", "Estadual", "Municipal", "Geral")
TIPOS_DOCUMENTO_INICIAIS = (
    (1, "NFe", "Nota Fiscal Eletrônica", "Nacional"),
    (2, "NFCe", "Nota Fiscal Consumidor Eletrônica", "Nacional"),
    (3, "NFSe", "Nota Fiscal de Serviço Eletrônica", "Nacional"),
    (4, "CTe", "Conhecimento de Transporte Eletrônico", "Nacional"),
    (5, "NF3e", "Nota Fiscal de Energia Elétrica Eletrônica", "Nacional"),
    (6, "NFCom", "Nota Fiscal Fatura de Serviço de Comunicação Eletrônica", "Nacional"),
    (7, "NFAg-e", "Nota Fiscal de Água e Saneamento Eletrônica", "Nacional"),
    (8, "REC", "Recibo", "Geral"),
    (9, "BOL", "Boleto", "Geral"),
    (10, "DUP", "Duplicata", "Geral"),
    (11, "PRO", "Promissória", "Geral"),
    (12, "DUA", "Documento Único de Arrecadação", "Estadual"),
    (13, "DARF", "Documento de Arrecadação das Receitas Federais", "Federal"),
    (14, "DUAM", "Documento Único de Arrecadação Municipal", "Municipal"),
    (15, "DARE", "Documento de Arrecadação das Receitas Estaduais", "Estadual"),
    (16, "GPS", "Guia da Previdência Social", "Federal"),
    (17, "DAS", "Documento de Arrecadação do Simples Nacional", "Federal"),
    (18, "DAE", "Documento de Arrecadação Estadual", "Estadual"),
    (19, "DAR", "Documento de Arrecadação de Receitas", "Estadual"),
    (20, "GRNE", "Guia Nacional de Recolhimento de Tributos Estaduais", "Estadual"),
)

FORMAS_PATH = "/restrito/cadastros/formas-de-pagamentos"
FORMAS_PAGAMENTO_INICIAIS = (
    (1, "DIH", "Dinheiro"),
    (2, "PIX", "Pix"),
    (3, "BOL", "Boleto"),
    (4, "CRE", "Cartão Crédito"),
    (5, "DEB", "Cartão Débito"),
    (6, "CHQ", "Cheque"),
    (7, "DUP", "Duplicata"),
    (8, "PRO", "Promissória"),
)

CONDICOES_PATH = "/restrito/cadastros/condicoes-de-pagamentos"
CENTROS_PATH = "/restrito/cadastros/centros-de-custo"
USUARIOS_PATH = "/restrito/cadastros/usuarios"
PASSWORD_PATH = "/restrito/trocar-senha"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
LOGIN_RE = re.compile(r"^[A-Za-z0-9._-]{3,40}$")
CODIGO_REDUZIDO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9./-]{0,19}$")
CONDICOES_PAGAMENTO_INICIAIS = (
    (1, 1, "AVISTA"),
    (2, 1, "10DD"),
    (3, 1, "15DD"),
    (4, 1, "21DD"),
    (5, 1, "30DD"),
    (6, 2, "E/10DD"),
    (7, 2, "E/15DD"),
    (8, 2, "E/30DD"),
    (9, 2, "15/30DD"),
    (10, 2, "30/60DD"),
    (11, 3, "10/20/30DD"),
    (12, 3, "15/30/45DD"),
    (13, 3, "30/60/90DD"),
    (14, 4, "10/20/30/40DD"),
    (15, 4, "15/30/45/60DD"),
    (16, 4, "30/60/90/120DD"),
)


def database_url() -> str:
    return os.environ.get("DATABASE_URL", "").strip()


def public_file(url_path: str) -> Path | None:
    if url_path.startswith("/restrito") or not PUBLIC_DIR.is_dir():
        return None
    relative = url_path.lstrip("/") or "index.html"
    root = PUBLIC_DIR.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if candidate.is_dir():
        candidate = (candidate / "index.html").resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
    if candidate.is_file():
        return candidate
    return None


def connect():
    return psycopg.connect(database_url())


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, rounds, salt_hex, digest_hex = stored.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds)
    )
    return hmac.compare_digest(digest.hex(), digest_hex)


def migrate_and_seed() -> None:
    url = database_url()
    if not url:
        print("DATABASE_URL ausente; a área restrita fica indisponível.")
        return
    schema = open(os.path.join(os.path.dirname(__file__), "schema.sql"), encoding="utf-8").read()
    statements = [part.strip() for part in schema.split(";") if part.strip()]
    with connect() as conn:
        for statement in statements:
            conn.execute(statement)
        username = os.environ.get("ADMIN_USERNAME", "admin").strip() or "admin"
        password = os.environ.get("ADMIN_PASSWORD", "")
        row = conn.execute(
            "SELECT id FROM app_user WHERE username = %s", (username,)
        ).fetchone()
        if row is None and password:
            conn.execute(
                """
                INSERT INTO app_user (username, password_hash, display_name, role)
                VALUES (%s, %s, %s, 'master')
                """,
                (username, hash_password(password), "Administrador"),
            )
            print(f"Usuário master criado: {username}")
        elif row is None:
            print("Usuário master ainda não existe. Defina ADMIN_PASSWORD para criá-lo.")
        seed_tipos_documento(conn)
        seed_formas_pagamento(conn)
        seed_condicoes_pagamento(conn)
        conn.commit()


def seed_tipos_documento(conn) -> None:
    for item_id, nome, descricao, esfera in TIPOS_DOCUMENTO_INICIAIS:
        conn.execute(
            """
            INSERT INTO tipo_documento (id, nome, descricao, esfera)
            OVERRIDING SYSTEM VALUE
            VALUES (%s, %s, %s, %s)
            ON CONFLICT ON CONSTRAINT tipo_documento_nome_uniq DO NOTHING
            """,
            (item_id, nome, descricao, esfera),
        )
    conn.execute(
        """
        SELECT setval(
            pg_get_serial_sequence('tipo_documento', 'id'),
            GREATEST(COALESCE((SELECT MAX(id) FROM tipo_documento), 1), 1),
            true
        )
        """
    )


def seed_formas_pagamento(conn) -> None:
    conn.execute("ALTER TABLE forma_pagamento ADD COLUMN IF NOT EXISTS nome text")
    for _item_id, nome, descricao in FORMAS_PAGAMENTO_INICIAIS:
        conn.execute(
            """
            UPDATE forma_pagamento
            SET nome = %s, descricao = %s, updated_at = now()
            WHERE (nome IS NULL OR btrim(nome) = '')
              AND descricao = %s
            """,
            (nome, descricao, nome),
        )
    conn.execute(
        """
        UPDATE forma_pagamento
        SET nome = descricao
        WHERE nome IS NULL OR btrim(nome) = ''
        """
    )
    for item_id, nome, descricao in FORMAS_PAGAMENTO_INICIAIS:
        conn.execute(
            """
            INSERT INTO forma_pagamento (id, nome, descricao)
            OVERRIDING SYSTEM VALUE
            VALUES (%s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (item_id, nome, descricao),
        )
    conn.execute("ALTER TABLE forma_pagamento ALTER COLUMN nome SET NOT NULL")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS forma_pagamento_nome_lower_idx
        ON forma_pagamento (lower(nome))
        """
    )
    conn.execute(
        """
        SELECT setval(
            pg_get_serial_sequence('forma_pagamento', 'id'),
            GREATEST(COALESCE((SELECT MAX(id) FROM forma_pagamento), 1), 1),
            true
        )
        """
    )


def seed_condicoes_pagamento(conn) -> None:
    for item_id, qtde, nome in CONDICOES_PAGAMENTO_INICIAIS:
        conn.execute(
            """
            INSERT INTO condicao_pagamento (id, qtde_parcelas, nome)
            OVERRIDING SYSTEM VALUE
            VALUES (%s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (item_id, qtde, nome),
        )
    conn.execute(
        """
        SELECT setval(
            pg_get_serial_sequence('condicao_pagamento', 'id'),
            GREATEST(COALESCE((SELECT MAX(id) FROM condicao_pagamento), 1), 1),
            true
        )
        """
    )


def esc(value: str) -> str:
    return html.escape(value, quote=True)


def nav_link(href: str, label: str, current: str, *, sub: bool = False) -> str:
    path = href.rstrip("/") or "/"
    classes = ["sub"] if sub else []
    if path == "/restrito/cadastros" and current.startswith("/restrito/cadastros"):
        classes.append("is-section")
    class_attr = f' class="{" ".join(classes)}"' if classes else ""
    selected = ' aria-current="page"' if current == path else ""
    return f'<a href="{esc(href)}"{class_attr}{selected}>{esc(label)}</a>'


def side_nav(current: str) -> str:
    links = [nav_link("/restrito/cadastros", "Cadastros", current)]
    links.append(
        '<div class="submenu">'
        + "".join(nav_link(href, label, current, sub=True) for href, label, _text in CADASTROS)
        + "</div>"
    )
    links.extend(nav_link(href, label, current) for href, label, _text in MODULES)
    return (
        '<nav class="side">'
        + "".join(links)
        + '<form method="post" action="/restrito/sair"><button type="submit">Sair</button></form>'
        "</nav>"
    )


def page(title: str, body: str, *, logged: bool = False, current: str = "", wide: bool = False) -> bytes:
    nav = side_nav(current) if logged else ""
    shell_class = "restrito-shell wide" if wide else "restrito-shell"
    document = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)} | Apinil</title>
  <link rel="icon" href="/assets/img/logo.png">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@600;700&family=Outfit:wght@400;500;600&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/assets/css/site.css">
  <link rel="stylesheet" href="/restrito/app.css">
</head>
<body class="restrito-body">
  <header class="site-header">
    <div class="header-inner">
      <a class="brand" href="/about-1/"><img src="/assets/img/logo.png" alt="Apinil cosméticos"></a>
      <a class="restrito-link" href="/restrito/cadastros" aria-label="Área restrita" title="Área restrita">{LOCK}</a>
    </div>
  </header>
  <div class="{shell_class}">{nav}<main class="restrito-main">{body}</main></div>
</body>
</html>"""
    return document.encode("utf-8")


LOCK = """<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8">
  <rect x="5" y="11" width="14" height="9" rx="2"></rect>
  <path d="M8 11V8a4 4 0 0 1 8 0v3"></path>
</svg>"""

APP_CSS = """
.restrito-body { background: var(--paper); min-height: 100vh; }
.restrito-shell { max-width: 1100px; margin: 0 auto; padding: 28px 20px 64px; display: grid; grid-template-columns: 220px 1fr; gap: 28px; }
.restrito-shell:not(:has(.side)) { grid-template-columns: 1fr; }
.side { display: flex; flex-direction: column; gap: 8px; }
.side a, .side button {
  text-align: left; text-decoration: none; color: var(--green-dark);
  background: white; border: 1px solid var(--line); border-radius: 14px;
  padding: 12px 14px; font: inherit; font-weight: 600; cursor: pointer;
}
.side a:hover, .side button:hover { border-color: var(--green); }
.side a[aria-current="page"], .side a.is-section { background: var(--green); color: white; border-color: var(--green); }
.submenu { display: flex; flex-direction: column; gap: 4px; margin: -2px 0 4px 14px; }
.submenu a { font-size: 15px; font-weight: 500; padding: 8px 12px; }
.restrito-main h1 { text-align: left; margin-top: 0; }
.login-card, .module-card {
  background: white; border-radius: 24px; padding: 28px; box-shadow: var(--shadow);
}
.login-card { max-width: 440px; margin: 24px auto; }
.login-card h1, .login-card p { text-align: left; }
.login-card form, .module-grid { display: grid; gap: 14px; }
.module-grid { grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
.module-card { text-decoration: none; color: inherit; display: block; }
.module-card h2 { text-align: left; margin: 0 0 8px; font-size: 32px; }
.module-card p { margin: 0; color: var(--muted); }
.error { color: #8d2b2b; font-weight: 600; }
.restrito-shell.wide { max-width: 1240px; }
.crud-toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin: 0 0 16px; }
.search-form { display: flex; gap: 8px; flex: 1 1 280px; align-items: center; margin: 0; }
.search-form input { flex: 1; min-width: 180px; }
.crud-actions { display: flex; gap: 8px; flex-wrap: wrap; }
a.btn, button.ghost {
  background: white; color: var(--green-dark); border: 1px solid var(--line);
}
a.btn {
  border-radius: 999px; padding: 12px 18px; font-weight: 600;
  text-decoration: none; display: inline-block;
}
button.danger { background: #8d2b2b; }
.flash { background: #e7f5ec; color: var(--green-dark); border-radius: 14px; padding: 12px 14px; margin: 0 0 14px; }
.flash.error { background: #fdecec; color: #8d2b2b; }
.editor, .grid-card {
  background: white; border-radius: 24px; padding: 22px; box-shadow: var(--shadow); margin-bottom: 16px;
}
.editor h2 { text-align: left; margin: 0 0 12px; font-size: 32px; }
.record-form { display: grid; gap: 12px; }
.record-form select {
  font: inherit; border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; background: #fff;
}
.record-form input[readonly] {
  background: #f3f6f4; color: var(--muted); cursor: default;
}
.record-form textarea { min-height: 96px; resize: vertical; }
.grid-wrap { overflow-x: auto; }
table.grid { width: 100%; border-collapse: collapse; font-size: 15px; }
table.grid th {
  font-size: 12px; letter-spacing: .04em; text-transform: uppercase; color: var(--muted); font-weight: 600;
}
table.grid td, table.grid th { padding: 10px 12px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
table.grid tbody tr { cursor: pointer; }
table.grid tbody tr:hover { background: #f4faf6; }
table.grid tbody tr:has(input:checked) { background: #e7f5ec; }
.col-id { width: 72px; }
.col-codigo { width: 150px; white-space: nowrap; }
.col-nome { width: 110px; white-space: nowrap; }
.col-esfera { width: 130px; }
.grid-count { color: var(--muted); margin: 0 0 12px; }
.checkline { display: flex; align-items: center; gap: 10px; }
.checkline input { width: auto; padding: 0; accent-color: var(--green); }
.readonly { margin: 0; color: var(--muted); }
dialog.modal { border: 0; border-radius: 24px; padding: 28px; max-width: 440px; box-shadow: var(--shadow); }
dialog.modal::backdrop { background: rgba(16, 32, 24, .45); }
dialog.modal h2, dialog.modal p { text-align: left; }
dialog.modal h2 { margin: 0 0 8px; }
@media (max-width: 800px) {
  .restrito-shell { grid-template-columns: 1fr; }
}
"""


def login_form(error: str = "") -> bytes:
    message = f'<p class="error">{esc(error)}</p>' if error else ""
    body = f"""
<section class="login-card">
  <h1>Área restrita</h1>
  <p>Entre com o usuário e a senha da fábrica.</p>
  {message}
  <form method="post" action="/restrito/login">
    <label>Login<input name="username" autocomplete="username" required autofocus></label>
    <label>Senha<input name="password" type="password" autocomplete="current-password" required></label>
    <button type="submit">Entrar</button>
  </form>
</section>
"""
    return page("Entrar", body)


def current_user(handler: BaseHTTPRequestHandler):
    token = ""
    raw = handler.headers.get("Cookie", "")
    for part in raw.split(";"):
        name, _, value = part.strip().partition("=")
        if name == SESSION_COOKIE and value:
            token = value
            break
    if not token or not database_url():
        return None
    with connect() as conn:
        row = conn.execute(
            """
            SELECT u.id, u.username, u.display_name, u.role, u.must_change_password
            FROM app_session s
            JOIN app_user u ON u.id = s.user_id
            WHERE s.id = %s AND s.expires_at > now() AND u.active
            """,
            (token,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "username": row[1],
        "name": row[2],
        "role": row[3],
        "must_change_password": bool(row[4]),
    }


def cards_for(items) -> str:
    cards = []
    for href, label, text in items:
        cards.append(
            f'<a class="module-card" href="{esc(href)}"><h2>{esc(label)}</h2><p>{esc(text)}</p></a>'
        )
    return f'<div class="module-grid">{"".join(cards)}</div>'


def cadastros_page() -> bytes:
    body = f"""
<h1>Cadastros</h1>
<p>Escolha o cadastro que deseja consultar.</p>
{cards_for(CADASTROS)}
"""
    return page("Cadastros", body, logged=True, current="/restrito/cadastros")


def tipos_url(**params: str) -> str:
    clean = {key: value for key, value in params.items() if value}
    if not clean:
        return TIPOS_PATH
    return TIPOS_PATH + "?" + urlencode(clean)


def like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def parse_record_id(value: str) -> int | None:
    if value.isdigit() and len(value) <= 18:
        return int(value)
    return None


def list_tipos(conn, q: str):
    if not q:
        return conn.execute(
            "SELECT id, nome, descricao, esfera FROM tipo_documento ORDER BY id"
        ).fetchall()
    pattern = like_pattern(q)
    return conn.execute(
        """
        SELECT id, nome, descricao, esfera
        FROM tipo_documento
        WHERE nome ILIKE %s ESCAPE '\\'
           OR descricao ILIKE %s ESCAPE '\\'
           OR esfera ILIKE %s ESCAPE '\\'
        ORDER BY id
        """,
        (pattern, pattern, pattern),
    ).fetchall()


def get_tipo(conn, item_id: int):
    return conn.execute(
        "SELECT id, nome, descricao, esfera FROM tipo_documento WHERE id = %s",
        (item_id,),
    ).fetchone()


def validate_tipo(nome: str, descricao: str, esfera: str) -> dict[str, str]:
    errors = {}
    if not nome:
        errors["nome"] = "Informe o nome."
    elif len(nome) > 30:
        errors["nome"] = "O nome pode ter no máximo 30 caracteres."
    if not descricao:
        errors["descricao"] = "Informe a descrição."
    elif len(descricao) > 180:
        errors["descricao"] = "A descrição pode ter no máximo 180 caracteres."
    if esfera not in ESFERAS:
        errors["esfera"] = "Escolha a esfera."
    return errors


def tipos_page(*, rows, q: str, aviso: str, erro: str, editor: dict | None) -> bytes:
    flashes = ""
    if aviso:
        flashes += f'<p class="flash">{esc(aviso)}</p>'
    if erro:
        flashes += f'<p class="flash error">{esc(erro)}</p>'
    editor_html = ""
    if editor:
        options = ['<option value="">Selecione</option>']
        for esfera in ESFERAS:
            selected = " selected" if editor["esfera"] == esfera else ""
            options.append(f'<option value="{esc(esfera)}"{selected}>{esc(esfera)}</option>')
        field_errors = editor.get("errors") or {}
        def field_error(key: str) -> str:
            message = field_errors.get(key)
            return f'<span class="error">{esc(message)}</span>' if message else ""
        editor_html = f"""
<section class="editor">
  <h2>{esc(editor["titulo"])}</h2>
  <form class="record-form" method="post" action="{esc(TIPOS_PATH)}">
    <input type="hidden" name="acao" value="salvar">
    <input type="hidden" name="id" value="{esc(editor["id"])}">
    <input type="hidden" name="q" value="{esc(q)}">
    <label>Nome<input name="nome" maxlength="30" required value="{esc(editor["nome"])}" autofocus>{field_error("nome")}</label>
    <label>Descrição<input name="descricao" maxlength="180" required value="{esc(editor["descricao"])}">{field_error("descricao")}</label>
    <label>Esfera<select name="esfera" required>{"".join(options)}</select>{field_error("esfera")}</label>
    <div class="form-actions">
      <button type="submit">Salvar</button>
      <a class="btn" href="{esc(tipos_url(q=q))}">Cancelar</a>
    </div>
  </form>
</section>
"""
    body_rows = []
    for item_id, nome, descricao, esfera in rows:
        body_rows.append(
            "<tr>"
            f'<td class="col-id"><input type="radio" name="id" value="{item_id}" aria-label="Selecionar {esc(nome)}"></td>'
            f"<td>{item_id}</td>"
            f"<td class=\"col-nome\">{esc(nome)}</td>"
            f"<td>{esc(descricao)}</td>"
            f"<td class=\"col-esfera\">{esc(esfera)}</td>"
            "</tr>"
        )
    if body_rows:
        grid_body = "".join(body_rows)
    else:
        grid_body = '<tr><td colspan="5">Nenhum tipo de documento encontrado.</td></tr>'
    quantidade = len(rows)
    legenda = "1 registro" if quantidade == 1 else f"{quantidade} registros"
    body = f"""
<h1>Tipos de Documentos</h1>
{flashes}
<div class="crud-toolbar">
  <form class="search-form" method="get" action="{esc(TIPOS_PATH)}">
    <input name="q" value="{esc(q)}" placeholder="Nome, descrição ou esfera" aria-label="Pesquisar tipos de documento">
    <button type="submit">Pesquisar</button>
  </form>
  <div class="crud-actions">
    <a class="btn" href="{esc(tipos_url(acao="incluir", q=q))}">Incluir</a>
    <button type="submit" class="ghost" form="grade-tipos" name="acao" value="editar" id="editar-tipo">Editar</button>
    <button type="submit" class="danger" form="grade-tipos" name="acao" value="excluir" id="deletar-tipo">Deletar</button>
  </div>
</div>
{editor_html}
<section class="grid-card">
  <p class="grid-count">{esc(legenda)}</p>
  <form id="grade-tipos" method="post" action="{esc(TIPOS_PATH)}">
    <input type="hidden" name="q" value="{esc(q)}">
    <div class="grid-wrap">
      <table class="grid">
        <thead>
          <tr><th></th><th>ID</th><th>Nome</th><th>Descrição</th><th>Esfera</th></tr>
        </thead>
        <tbody>{grid_body}</tbody>
      </table>
    </div>
  </form>
</section>
<script>
const grade = document.getElementById("grade-tipos");
function tipoSelecionado() {{
  const marcado = grade.querySelector('input[name="id"]:checked');
  return marcado ? marcado.value : "";
}}
document.getElementById("editar-tipo").addEventListener("click", (event) => {{
  if (!tipoSelecionado()) {{
    event.preventDefault();
    alert("Selecione um registro na grade.");
  }}
}});
document.getElementById("deletar-tipo").addEventListener("click", (event) => {{
  if (!tipoSelecionado()) {{
    event.preventDefault();
    alert("Selecione um registro na grade.");
    return;
  }}
  if (!confirm("Deletar o tipo de documento selecionado?")) event.preventDefault();
}});
grade.querySelectorAll("tbody tr").forEach((row) => {{
  row.addEventListener("click", (event) => {{
    if (event.target.closest("input,a,button")) return;
    const radio = row.querySelector('input[type="radio"]');
    if (radio) radio.checked = true;
  }});
  row.addEventListener("dblclick", () => {{
    const radio = row.querySelector('input[type="radio"]');
    if (!radio) return;
    radio.checked = true;
    document.getElementById("editar-tipo").click();
  }});
}});
</script>
"""
    return page("Tipos de Documentos", body, logged=True, current=TIPOS_PATH, wide=True)


def formas_url(**params: str) -> str:
    clean = {key: value for key, value in params.items() if value}
    if not clean:
        return FORMAS_PATH
    return FORMAS_PATH + "?" + urlencode(clean)


def list_formas(conn, q: str):
    if not q:
        return conn.execute(
            "SELECT id, nome, descricao FROM forma_pagamento ORDER BY id"
        ).fetchall()
    pattern = like_pattern(q)
    return conn.execute(
        """
        SELECT id, nome, descricao
        FROM forma_pagamento
        WHERE nome ILIKE %s ESCAPE '\\'
           OR descricao ILIKE %s ESCAPE '\\'
        ORDER BY id
        """,
        (pattern, pattern),
    ).fetchall()


def get_forma(conn, item_id: int):
    return conn.execute(
        "SELECT id, nome, descricao FROM forma_pagamento WHERE id = %s",
        (item_id,),
    ).fetchone()


def validate_forma(nome: str, descricao: str) -> dict[str, str]:
    errors = {}
    if not nome:
        errors["nome"] = "Informe o nome."
    elif len(nome) > 30:
        errors["nome"] = "O nome pode ter no máximo 30 caracteres."
    if not descricao:
        errors["descricao"] = "Informe a descrição."
    elif len(descricao) > 80:
        errors["descricao"] = "A descrição pode ter no máximo 80 caracteres."
    return errors


def formas_page(*, rows, q: str, aviso: str, erro: str, editor: dict | None) -> bytes:
    flashes = ""
    if aviso:
        flashes += f'<p class="flash">{esc(aviso)}</p>'
    if erro:
        flashes += f'<p class="flash error">{esc(erro)}</p>'
    editor_html = ""
    if editor:
        field_errors = editor.get("errors") or {}
        def field_error(key: str) -> str:
            message = field_errors.get(key)
            return f'<span class="error">{esc(message)}</span>' if message else ""
        editor_html = f"""
<section class="editor">
  <h2>{esc(editor["titulo"])}</h2>
  <form class="record-form" method="post" action="{esc(FORMAS_PATH)}">
    <input type="hidden" name="acao" value="salvar">
    <input type="hidden" name="id" value="{esc(editor["id"])}">
    <input type="hidden" name="q" value="{esc(q)}">
    <label>Nome<input name="nome" maxlength="30" required value="{esc(editor["nome"])}" autofocus>{field_error("nome")}</label>
    <label>Descrição<input name="descricao" maxlength="80" required value="{esc(editor["descricao"])}">{field_error("descricao")}</label>
    <div class="form-actions">
      <button type="submit">Salvar</button>
      <a class="btn" href="{esc(formas_url(q=q))}">Cancelar</a>
    </div>
  </form>
</section>
"""
    body_rows = []
    for item_id, nome, descricao in rows:
        body_rows.append(
            "<tr>"
            f'<td class="col-id"><input type="radio" name="id" value="{item_id}" aria-label="Selecionar {esc(nome)}"></td>'
            f"<td>{item_id}</td>"
            f"<td class=\"col-nome\">{esc(nome)}</td>"
            f"<td>{esc(descricao)}</td>"
            "</tr>"
        )
    grid_body = "".join(body_rows) or '<tr><td colspan="4">Nenhuma forma de pagamento encontrada.</td></tr>'
    quantidade = len(rows)
    legenda = "1 registro" if quantidade == 1 else f"{quantidade} registros"
    body = f"""
<h1>Formas de Pagamentos</h1>
{flashes}
<div class="crud-toolbar">
  <form class="search-form" method="get" action="{esc(FORMAS_PATH)}">
    <input name="q" value="{esc(q)}" placeholder="Nome ou descrição" aria-label="Pesquisar formas de pagamento">
    <button type="submit">Pesquisar</button>
  </form>
  <div class="crud-actions">
    <a class="btn" href="{esc(formas_url(acao="incluir", q=q))}">Incluir</a>
    <button type="submit" class="ghost" form="grade-formas" name="acao" value="editar" id="editar-forma">Editar</button>
    <button type="submit" class="danger" form="grade-formas" name="acao" value="excluir" id="deletar-forma">Deletar</button>
  </div>
</div>
{editor_html}
<section class="grid-card">
  <p class="grid-count">{esc(legenda)}</p>
  <form id="grade-formas" method="post" action="{esc(FORMAS_PATH)}">
    <input type="hidden" name="q" value="{esc(q)}">
    <div class="grid-wrap">
      <table class="grid">
        <thead>
          <tr><th></th><th>ID</th><th>Nome</th><th>Descrição</th></tr>
        </thead>
        <tbody>{grid_body}</tbody>
      </table>
    </div>
  </form>
</section>
<script>
const grade = document.getElementById("grade-formas");
function formaSelecionada() {{
  const marcado = grade.querySelector('input[name="id"]:checked');
  return marcado ? marcado.value : "";
}}
document.getElementById("editar-forma").addEventListener("click", (event) => {{
  if (!formaSelecionada()) {{
    event.preventDefault();
    alert("Selecione um registro na grade.");
  }}
}});
document.getElementById("deletar-forma").addEventListener("click", (event) => {{
  if (!formaSelecionada()) {{
    event.preventDefault();
    alert("Selecione um registro na grade.");
    return;
  }}
  if (!confirm("Deletar a forma de pagamento selecionada?")) event.preventDefault();
}});
grade.querySelectorAll("tbody tr").forEach((row) => {{
  row.addEventListener("click", (event) => {{
    if (event.target.closest("input,a,button")) return;
    const radio = row.querySelector('input[type="radio"]');
    if (radio) radio.checked = true;
  }});
  row.addEventListener("dblclick", () => {{
    const radio = row.querySelector('input[type="radio"]');
    if (!radio) return;
    radio.checked = true;
    document.getElementById("editar-forma").click();
  }});
}});
</script>
"""
    return page("Formas de Pagamentos", body, logged=True, current=FORMAS_PATH, wide=True)


def condicoes_url(**params: str) -> str:
    clean = {key: value for key, value in params.items() if value}
    if not clean:
        return CONDICOES_PATH
    return CONDICOES_PATH + "?" + urlencode(clean)


def list_condicoes(conn, q: str):
    if not q:
        return conn.execute(
            "SELECT id, qtde_parcelas, nome FROM condicao_pagamento ORDER BY id"
        ).fetchall()
    pattern = like_pattern(q)
    return conn.execute(
        """
        SELECT id, qtde_parcelas, nome
        FROM condicao_pagamento
        WHERE nome ILIKE %s ESCAPE '\\'
           OR qtde_parcelas::text ILIKE %s ESCAPE '\\'
        ORDER BY id
        """,
        (pattern, pattern),
    ).fetchall()


def get_condicao(conn, item_id: int):
    return conn.execute(
        "SELECT id, qtde_parcelas, nome FROM condicao_pagamento WHERE id = %s",
        (item_id,),
    ).fetchone()


def validate_condicao(qtde: str, nome: str) -> dict[str, str]:
    errors = {}
    if not qtde.isdigit() or int(qtde) < 1 or int(qtde) > 48:
        errors["qtde_parcelas"] = "Informe a quantidade de parcelas, de 1 a 48."
    if not nome:
        errors["nome"] = "Informe o nome."
    elif len(nome) > 40:
        errors["nome"] = "O nome pode ter no máximo 40 caracteres."
    return errors


def condicoes_page(*, rows, q: str, aviso: str, erro: str, editor: dict | None) -> bytes:
    flashes = ""
    if aviso:
        flashes += f'<p class="flash">{esc(aviso)}</p>'
    if erro:
        flashes += f'<p class="flash error">{esc(erro)}</p>'
    editor_html = ""
    if editor:
        field_errors = editor.get("errors") or {}
        def field_error(key: str) -> str:
            message = field_errors.get(key)
            return f'<span class="error">{esc(message)}</span>' if message else ""
        editor_html = f"""
<section class="editor">
  <h2>{esc(editor["titulo"])}</h2>
  <form class="record-form" method="post" action="{esc(CONDICOES_PATH)}">
    <input type="hidden" name="acao" value="salvar">
    <input type="hidden" name="id" value="{esc(editor["id"])}">
    <input type="hidden" name="q" value="{esc(q)}">
    <label>Qtde Parcelas<input name="qtde_parcelas" inputmode="numeric" maxlength="2" required value="{esc(editor["qtde_parcelas"])}" autofocus>{field_error("qtde_parcelas")}</label>
    <label>Nome<input name="nome" maxlength="40" required value="{esc(editor["nome"])}">{field_error("nome")}</label>
    <div class="form-actions">
      <button type="submit">Salvar</button>
      <a class="btn" href="{esc(condicoes_url(q=q))}">Cancelar</a>
    </div>
  </form>
</section>
"""
    body_rows = []
    for item_id, qtde, nome in rows:
        body_rows.append(
            "<tr>"
            f'<td class="col-id"><input type="radio" name="id" value="{item_id}" aria-label="Selecionar {esc(nome)}"></td>'
            f"<td>{item_id}</td>"
            f"<td>{qtde}</td>"
            f"<td class=\"col-nome\">{esc(nome)}</td>"
            "</tr>"
        )
    grid_body = "".join(body_rows) or '<tr><td colspan="4">Nenhuma condição de pagamento encontrada.</td></tr>'
    quantidade = len(rows)
    legenda = "1 registro" if quantidade == 1 else f"{quantidade} registros"
    body = f"""
<h1>Condições de Pagamentos</h1>
{flashes}
<div class="crud-toolbar">
  <form class="search-form" method="get" action="{esc(CONDICOES_PATH)}">
    <input name="q" value="{esc(q)}" placeholder="Nome ou quantidade de parcelas" aria-label="Pesquisar condições de pagamento">
    <button type="submit">Pesquisar</button>
  </form>
  <div class="crud-actions">
    <a class="btn" href="{esc(condicoes_url(acao="incluir", q=q))}">Incluir</a>
    <button type="submit" class="ghost" form="grade-condicoes" name="acao" value="editar" id="editar-condicao">Editar</button>
    <button type="submit" class="danger" form="grade-condicoes" name="acao" value="excluir" id="deletar-condicao">Deletar</button>
  </div>
</div>
{editor_html}
<section class="grid-card">
  <p class="grid-count">{esc(legenda)}</p>
  <form id="grade-condicoes" method="post" action="{esc(CONDICOES_PATH)}">
    <input type="hidden" name="q" value="{esc(q)}">
    <div class="grid-wrap">
      <table class="grid">
        <thead>
          <tr><th></th><th>ID</th><th>Qtde Parcelas</th><th>Nome</th></tr>
        </thead>
        <tbody>{grid_body}</tbody>
      </table>
    </div>
  </form>
</section>
<script>
const grade = document.getElementById("grade-condicoes");
function condicaoSelecionada() {{
  const marcado = grade.querySelector('input[name="id"]:checked');
  return marcado ? marcado.value : "";
}}
document.getElementById("editar-condicao").addEventListener("click", (event) => {{
  if (!condicaoSelecionada()) {{
    event.preventDefault();
    alert("Selecione um registro na grade.");
  }}
}});
document.getElementById("deletar-condicao").addEventListener("click", (event) => {{
  if (!condicaoSelecionada()) {{
    event.preventDefault();
    alert("Selecione um registro na grade.");
    return;
  }}
  if (!confirm("Deletar a condição de pagamento selecionada?")) event.preventDefault();
}});
grade.querySelectorAll("tbody tr").forEach((row) => {{
  row.addEventListener("click", (event) => {{
    if (event.target.closest("input,a,button")) return;
    const radio = row.querySelector('input[type="radio"]');
    if (radio) radio.checked = true;
  }});
  row.addEventListener("dblclick", () => {{
    const radio = row.querySelector('input[type="radio"]');
    if (!radio) return;
    radio.checked = true;
    document.getElementById("editar-condicao").click();
  }});
}});
</script>
"""
    return page("Condições de Pagamentos", body, logged=True, current=CONDICOES_PATH, wide=True)


def centros_url(**params: str) -> str:
    clean = {key: value for key, value in params.items() if value}
    if not clean:
        return CENTROS_PATH
    return CENTROS_PATH + "?" + urlencode(clean)


def list_centros(conn, q: str):
    if not q:
        return conn.execute(
            "SELECT id, codigo_reduzido, nome, descricao FROM centro_custo ORDER BY id"
        ).fetchall()
    pattern = like_pattern(q)
    return conn.execute(
        """
        SELECT id, codigo_reduzido, nome, descricao
        FROM centro_custo
        WHERE codigo_reduzido ILIKE %s ESCAPE '\\'
           OR nome ILIKE %s ESCAPE '\\'
           OR descricao ILIKE %s ESCAPE '\\'
        ORDER BY id
        """,
        (pattern, pattern, pattern),
    ).fetchall()


def get_centro(conn, item_id: int):
    return conn.execute(
        "SELECT id, codigo_reduzido, nome, descricao FROM centro_custo WHERE id = %s",
        (item_id,),
    ).fetchone()


def validate_centro(codigo: str, nome: str, descricao: str) -> dict[str, str]:
    errors = {}
    if not codigo:
        errors["codigo_reduzido"] = "Informe o código reduzido."
    elif not CODIGO_REDUZIDO_RE.fullmatch(codigo):
        errors["codigo_reduzido"] = "Use letras, números e os sinais . - / , com no máximo 20 caracteres."
    if not nome:
        errors["nome"] = "Informe o nome."
    elif len(nome) > 80:
        errors["nome"] = "O nome pode ter no máximo 80 caracteres."
    if not descricao:
        errors["descricao"] = "Informe a descrição."
    elif len(descricao) > 200:
        errors["descricao"] = "A descrição pode ter no máximo 200 caracteres."
    return errors


def centros_page(*, rows, q: str, aviso: str, erro: str, editor: dict | None) -> bytes:
    flashes = ""
    if aviso:
        flashes += f'<p class="flash">{esc(aviso)}</p>'
    if erro:
        flashes += f'<p class="flash error">{esc(erro)}</p>'
    editor_html = ""
    if editor:
        field_errors = editor.get("errors") or {}

        def field_error(key: str) -> str:
            message = field_errors.get(key)
            return f'<span class="error">{esc(message)}</span>' if message else ""

        editor_html = f"""
<section class="editor">
  <h2>{esc(editor["titulo"])}</h2>
  <form class="record-form" method="post" action="{esc(CENTROS_PATH)}">
    <input type="hidden" name="acao" value="salvar">
    <input type="hidden" name="id" value="{esc(editor["id"])}">
    <input type="hidden" name="q" value="{esc(q)}">
    <label>ID<input value="{esc(editor["id"])}" readonly tabindex="-1" placeholder="Gerado ao salvar"></label>
    <label>Código reduzido<input name="codigo_reduzido" maxlength="20" required value="{esc(editor["codigo_reduzido"])}" autocomplete="off" autofocus>{field_error("codigo_reduzido")}</label>
    <label>Nome<input name="nome" maxlength="80" required value="{esc(editor["nome"])}">{field_error("nome")}</label>
    <label>Descrição<textarea name="descricao" maxlength="200" required>{esc(editor["descricao"])}</textarea>{field_error("descricao")}</label>
    <div class="form-actions">
      <button type="submit">Salvar</button>
      <a class="btn" href="{esc(centros_url(q=q))}">Cancelar</a>
    </div>
  </form>
</section>
"""
    body_rows = []
    for item_id, codigo, nome, descricao in rows:
        body_rows.append(
            "<tr>"
            f'<td class="col-id"><input type="radio" name="id" value="{item_id}" aria-label="Selecionar {esc(codigo)}"></td>'
            f"<td>{item_id}</td>"
            f'<td class="col-codigo">{esc(codigo)}</td>'
            f"<td>{esc(nome)}</td>"
            f"<td>{esc(descricao) if descricao else '—'}</td>"
            "</tr>"
        )
    grid_body = "".join(body_rows) or '<tr><td colspan="5">Nenhum centro de custo encontrado.</td></tr>'
    quantidade = len(rows)
    legenda = "1 registro" if quantidade == 1 else f"{quantidade} registros"
    body = f"""
<h1>Centros de Custo</h1>
{flashes}
<div class="crud-toolbar">
  <form class="search-form" method="get" action="{esc(CENTROS_PATH)}">
    <input name="q" value="{esc(q)}" placeholder="Código, nome ou descrição" aria-label="Pesquisar centros de custo">
    <button type="submit">Pesquisar</button>
  </form>
  <div class="crud-actions">
    <a class="btn" href="{esc(centros_url(acao="incluir", q=q))}">Inserir</a>
    <button type="submit" class="ghost" form="grade-centros" name="acao" value="editar" id="editar-centro">Editar</button>
    <button type="submit" class="danger" form="grade-centros" name="acao" value="excluir" id="excluir-centro">Excluir</button>
  </div>
</div>
{editor_html}
<section class="grid-card">
  <p class="grid-count">{esc(legenda)}</p>
  <form id="grade-centros" method="post" action="{esc(CENTROS_PATH)}">
    <input type="hidden" name="q" value="{esc(q)}">
    <div class="grid-wrap">
      <table class="grid">
        <thead>
          <tr><th></th><th>ID</th><th>Código reduzido</th><th>Nome</th><th>Descrição</th></tr>
        </thead>
        <tbody>{grid_body}</tbody>
      </table>
    </div>
  </form>
</section>
<script>
const grade = document.getElementById("grade-centros");
function centroSelecionado() {{
  const marcado = grade.querySelector('input[name="id"]:checked');
  return marcado ? marcado.value : "";
}}
document.getElementById("editar-centro").addEventListener("click", (event) => {{
  if (!centroSelecionado()) {{
    event.preventDefault();
    alert("Selecione um registro na grade.");
  }}
}});
document.getElementById("excluir-centro").addEventListener("click", (event) => {{
  if (!centroSelecionado()) {{
    event.preventDefault();
    alert("Selecione um registro na grade.");
    return;
  }}
  if (!confirm("Excluir o centro de custo selecionado?")) event.preventDefault();
}});
grade.querySelectorAll("tbody tr").forEach((row) => {{
  row.addEventListener("click", (event) => {{
    if (event.target.closest("input,a,button")) return;
    const radio = row.querySelector('input[type="radio"]');
    if (radio) radio.checked = true;
  }});
  row.addEventListener("dblclick", () => {{
    const radio = row.querySelector('input[type="radio"]');
    if (!radio) return;
    radio.checked = true;
    document.getElementById("editar-centro").click();
  }});
}});
</script>
"""
    return page("Centros de Custo", body, logged=True, current=CENTROS_PATH, wide=True)


def is_admin(user) -> bool:
    return user.get("role") in ("master", "admin")


def usuarios_url(**params: str) -> str:
    clean = {key: value for key, value in params.items() if value}
    if not clean:
        return USUARIOS_PATH
    return USUARIOS_PATH + "?" + urlencode(clean)


def list_usuarios(conn, q: str):
    if not q:
        return conn.execute(
            """
            SELECT id, display_name, username, email, role, active, must_change_password
            FROM app_user
            ORDER BY id
            """
        ).fetchall()
    pattern = like_pattern(q)
    return conn.execute(
        """
        SELECT id, display_name, username, email, role, active, must_change_password
        FROM app_user
        WHERE display_name ILIKE %s ESCAPE '\\'
           OR username ILIKE %s ESCAPE '\\'
           OR coalesce(email, '') ILIKE %s ESCAPE '\\'
        ORDER BY id
        """,
        (pattern, pattern, pattern),
    ).fetchall()


def get_usuario(conn, item_id: int):
    return conn.execute(
        """
        SELECT id, display_name, username, email, role, active, must_change_password
        FROM app_user
        WHERE id = %s
        """,
        (item_id,),
    ).fetchone()


def keeps_an_admin(conn, item_id: int, *, active: bool, role: str) -> bool:
    others = conn.execute(
        """
        SELECT count(*)
        FROM app_user
        WHERE id <> %s AND active AND role IN ('master', 'admin')
        """,
        (item_id,),
    ).fetchone()[0]
    still_admin = active and role in ("master", "admin")
    return others + (1 if still_admin else 0) >= 1


def validate_usuario(nome: str, login: str, email: str, senha: str, confirmar: str, *, creating: bool) -> dict[str, str]:
    errors = {}
    if not nome:
        errors["nome"] = "Informe o nome."
    elif len(nome) > 80:
        errors["nome"] = "O nome pode ter no máximo 80 caracteres."
    if not LOGIN_RE.fullmatch(login):
        errors["login"] = "O login deve ter de 3 a 40 letras, números, ponto, hífen ou sublinhado."
    if creating:
        if not EMAIL_RE.fullmatch(email) or len(email) > 120:
            errors["email"] = "Informe um e-mail válido."
        if len(senha) < 8:
            errors["senha"] = "A senha precisa ter pelo menos 8 caracteres."
        elif senha != confirmar:
            errors["confirmar"] = "A confirmação não confere com a senha."
    return errors


def usuarios_page(*, rows, q: str, aviso: str, erro: str, editor: dict | None, current_id: int) -> bytes:
    flashes = ""
    if aviso and aviso != "Usuário incluído com sucesso.":
        flashes += f'<p class="flash">{esc(aviso)}</p>'
    if erro:
        flashes += f'<p class="flash error">{esc(erro)}</p>'
    popup = ""
    if aviso == "Usuário incluído com sucesso.":
        popup = f"""
<dialog class="modal" open>
  <h2>Usuário incluído</h2>
  <p>O usuário foi incluído com sucesso. No primeiro acesso, a troca da senha será obrigatória.</p>
  <form method="get" action="{esc(USUARIOS_PATH)}">
    <input type="hidden" name="q" value="{esc(q)}">
    <button type="submit">OK</button>
  </form>
</dialog>
"""
    editor_html = ""
    if editor:
        field_errors = editor.get("errors") or {}

        def field_error(key: str) -> str:
            message = field_errors.get(key)
            return f'<span class="error">{esc(message)}</span>' if message else ""

        if editor["modo"] == "incluir":
            checked = " checked" if editor.get("administrador") else ""
            editor_html = f"""
<section class="editor">
  <h2>Incluir usuário</h2>
  <form class="record-form" method="post" action="{esc(USUARIOS_PATH)}">
    <input type="hidden" name="acao" value="salvar">
    <input type="hidden" name="q" value="{esc(q)}">
    <label>Nome<input name="nome" maxlength="80" required value="{esc(editor["nome"])}" autofocus>{field_error("nome")}</label>
    <label>Login<input name="login" maxlength="40" required value="{esc(editor["login"])}" autocomplete="off">{field_error("login")}</label>
    <label>E-mail<input name="email" type="email" maxlength="120" required value="{esc(editor["email"])}" autocomplete="off">{field_error("email")}</label>
    <label>Senha<input name="senha" type="password" required autocomplete="new-password">{field_error("senha")}</label>
    <label>Confirmar senha<input name="confirmar" type="password" required autocomplete="new-password">{field_error("confirmar")}</label>
    <label class="checkline"><input type="checkbox" name="administrador" value="1"{checked}>Tornar administrador</label>
    <p class="readonly">No primeiro acesso, o usuário será obrigado a trocar a senha.</p>
    <div class="form-actions">
      <button type="submit">Salvar</button>
      <a class="btn" href="{esc(usuarios_url(q=q))}">Cancelar</a>
    </div>
  </form>
</section>
"""
        else:
            editor_html = f"""
<section class="editor">
  <h2>Editar usuário</h2>
  <form class="record-form" method="post" action="{esc(USUARIOS_PATH)}">
    <input type="hidden" name="acao" value="salvar">
    <input type="hidden" name="id" value="{esc(editor["id"])}">
    <input type="hidden" name="q" value="{esc(q)}">
    <p class="readonly">ID {esc(editor["id"])}</p>
    <label>Nome<input name="nome" maxlength="80" required value="{esc(editor["nome"])}" autofocus>{field_error("nome")}</label>
    <label>Login<input name="login" maxlength="40" required value="{esc(editor["login"])}" autocomplete="off">{field_error("login")}</label>
    <p class="readonly">E-mail {esc(editor["email"] or "—")}. O e-mail e a senha não podem ser alterados aqui.</p>
    <div class="form-actions">
      <button type="submit">Salvar</button>
      <a class="btn" href="{esc(usuarios_url(q=q))}">Cancelar</a>
    </div>
  </form>
</section>
"""
    body_rows = []
    for item_id, nome, login, email, role, active, must_change in rows:
        admin = role in ("master", "admin")
        body_rows.append(
            "<tr>"
            f'<td class="col-id"><input type="radio" name="id" value="{item_id}"'
            f' data-active="{1 if active else 0}" data-admin="{1 if admin else 0}"'
            f' data-self="{1 if item_id == current_id else 0}"'
            f' aria-label="Selecionar {esc(login)}"></td>'
            f"<td>{item_id}</td>"
            f"<td>{esc(nome)}</td>"
            f"<td>{esc(login)}</td>"
            f"<td>{esc(email or '—')}</td>"
            f"<td>{'Sim' if admin else 'Não'}</td>"
            f"<td>{'Ativo' if active else 'Bloqueado'}</td>"
            f"<td>{'Pendente' if must_change else 'Não'}</td>"
            "</tr>"
        )
    grid_body = "".join(body_rows) or '<tr><td colspan="8">Nenhum usuário encontrado.</td></tr>'
    quantidade = len(rows)
    legenda = "1 registro" if quantidade == 1 else f"{quantidade} registros"
    body = f"""
<h1>Usuários</h1>
{flashes}
{popup}
<div class="crud-toolbar">
  <form class="search-form" method="get" action="{esc(USUARIOS_PATH)}">
    <input name="q" value="{esc(q)}" placeholder="Nome, login ou e-mail" aria-label="Pesquisar usuários">
    <button type="submit">Pesquisar</button>
  </form>
  <div class="crud-actions">
    <a class="btn" href="{esc(usuarios_url(acao="incluir", q=q))}">Inserir</a>
    <button type="submit" class="ghost" form="grade-usuarios" name="acao" value="editar" id="editar-usuario">Editar</button>
    <button type="submit" class="ghost" form="grade-usuarios" name="acao" value="bloquear" id="bloquear-usuario">Bloquear</button>
    <button type="submit" class="danger" form="grade-usuarios" name="acao" value="excluir" id="excluir-usuario">Excluir</button>
    <button type="submit" class="ghost" form="grade-usuarios" name="acao" value="administrador" id="admin-usuario">Tornar administrador</button>
    <button type="submit" class="ghost" form="grade-usuarios" name="acao" value="senha" id="senha-usuario">Solicitar troca de senha</button>
  </div>
</div>
{editor_html}
<section class="grid-card">
  <p class="grid-count">{esc(legenda)}</p>
  <form id="grade-usuarios" method="post" action="{esc(USUARIOS_PATH)}">
    <input type="hidden" name="q" value="{esc(q)}">
    <div class="grid-wrap">
      <table class="grid">
        <thead>
          <tr><th></th><th>ID</th><th>Nome</th><th>Login</th><th>E-mail</th><th>Administrador</th><th>Situação</th><th>Troca de senha</th></tr>
        </thead>
        <tbody>{grid_body}</tbody>
      </table>
    </div>
  </form>
</section>
<script>
const grade = document.getElementById("grade-usuarios");
function usuarioSelecionado() {{
  return grade.querySelector('input[name="id"]:checked');
}}
function atualizarAcoes() {{
  const marcado = usuarioSelecionado();
  const bloquear = document.getElementById("bloquear-usuario");
  const admin = document.getElementById("admin-usuario");
  if (!marcado) {{
    bloquear.textContent = "Bloquear";
    admin.textContent = "Tornar administrador";
    return;
  }}
  bloquear.textContent = marcado.dataset.active === "1" ? "Bloquear" : "Desbloquear";
  admin.textContent = marcado.dataset.admin === "1" ? "Remover administrador" : "Tornar administrador";
}}
grade.addEventListener("change", atualizarAcoes);
document.getElementById("editar-usuario").addEventListener("click", (event) => {{
  if (!usuarioSelecionado()) {{
    event.preventDefault();
    alert("Selecione um registro na grade.");
  }}
}});
document.getElementById("bloquear-usuario").addEventListener("click", (event) => {{
  const marcado = usuarioSelecionado();
  if (!marcado) {{
    event.preventDefault();
    alert("Selecione um registro na grade.");
    return;
  }}
  if (marcado.dataset.self === "1") {{
    event.preventDefault();
    alert("Você não pode bloquear o próprio usuário.");
    return;
  }}
  const acao = marcado.dataset.active === "1" ? "Bloquear" : "Desbloquear";
  if (!confirm(acao + " o usuário selecionado?")) event.preventDefault();
}});
document.getElementById("excluir-usuario").addEventListener("click", (event) => {{
  const marcado = usuarioSelecionado();
  if (!marcado) {{
    event.preventDefault();
    alert("Selecione um registro na grade.");
    return;
  }}
  if (marcado.dataset.self === "1") {{
    event.preventDefault();
    alert("Você não pode excluir o próprio usuário.");
    return;
  }}
  if (!confirm("Excluir o usuário selecionado?")) event.preventDefault();
}});
document.getElementById("admin-usuario").addEventListener("click", (event) => {{
  if (!usuarioSelecionado()) {{
    event.preventDefault();
    alert("Selecione um registro na grade.");
  }}
}});
document.getElementById("senha-usuario").addEventListener("click", (event) => {{
  if (!usuarioSelecionado()) {{
    event.preventDefault();
    alert("Selecione um registro na grade.");
    return;
  }}
  if (!confirm("Pedir para o usuário trocar a senha no próximo acesso?")) event.preventDefault();
}});
grade.querySelectorAll("tbody tr").forEach((row) => {{
  row.addEventListener("click", (event) => {{
    if (event.target.closest("input,a,button")) return;
    const radio = row.querySelector('input[type="radio"]');
    if (!radio) return;
    radio.checked = true;
    atualizarAcoes();
  }});
  row.addEventListener("dblclick", () => {{
    const radio = row.querySelector('input[type="radio"]');
    if (!radio) return;
    radio.checked = true;
    document.getElementById("editar-usuario").click();
  }});
}});
</script>
"""
    return page("Usuários", body, logged=True, current=USUARIOS_PATH, wide=True)


def password_form(error: str = "") -> bytes:
    message = f'<p class="error">{esc(error)}</p>' if error else ""
    body = f"""
<section class="login-card">
  <h1>Trocar senha</h1>
  <p>Para continuar, informe a senha atual e escolha uma nova senha.</p>
  {message}
  <form method="post" action="{esc(PASSWORD_PATH)}">
    <label>Senha atual<input name="atual" type="password" autocomplete="current-password" required autofocus></label>
    <label>Nova senha<input name="nova" type="password" autocomplete="new-password" required></label>
    <label>Confirmar nova senha<input name="confirmar" type="password" autocomplete="new-password" required></label>
    <button type="submit">Salvar senha</button>
  </form>
</section>
"""
    return page("Trocar senha", body, logged=True, current=PASSWORD_PATH)


def module_page(title: str, text: str, current: str) -> bytes:
    body = f"""
<h1>{esc(title)}</h1>
<p>{esc(text)}</p>
<p>A estrutura de acesso já está pronta. O lançamento dos registros entra na próxima etapa.</p>
"""
    return page(title, body, logged=True, current=current)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        print(fmt % args)

    def send_html(self, status: int, payload: bytes, *, headers: list[tuple[str, str]] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in headers or []:
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def redirect(self, location: str, *, cookie: str | None = None) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/restrito/app.css":
            body = APP_CSS.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/css; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if not path.startswith("/restrito"):
            static = public_file(path)
            if static is not None:
                self.send_static(static)
                return
        ensure_schema()
        if not database_url():
            self.send_html(503, page("Indisponível", "<h1>Área restrita indisponível</h1><p>O banco ainda não está configurado.</p>"))
            return
        if path in ("/restrito/login",):
            user = current_user(self)
            if user:
                self.redirect(PASSWORD_PATH if user["must_change_password"] else "/restrito/cadastros")
                return
            self.send_html(200, login_form())
            return
        user = current_user(self)
        if path == PASSWORD_PATH:
            if not user:
                self.redirect("/restrito/login")
                return
            if not user["must_change_password"]:
                self.redirect("/restrito/cadastros")
                return
            self.send_html(200, password_form())
            return
        if user and user["must_change_password"]:
            self.redirect(PASSWORD_PATH)
            return
        if path == "/restrito":
            if not user:
                self.redirect("/restrito/login")
                return
            self.redirect("/restrito/cadastros")
            return
        if path == "/restrito/cadastros":
            if not user:
                self.redirect("/restrito/login")
                return
            self.send_html(200, cadastros_page())
            return
        if path == TIPOS_PATH:
            if not user:
                self.redirect("/restrito/login")
                return
            self.handle_tipos_get()
            return
        if path == FORMAS_PATH:
            if not user:
                self.redirect("/restrito/login")
                return
            self.handle_formas_get()
            return
        if path == CONDICOES_PATH:
            if not user:
                self.redirect("/restrito/login")
                return
            self.handle_condicoes_get()
            return
        if path == CENTROS_PATH:
            if not user:
                self.redirect("/restrito/login")
                return
            self.handle_centros_get()
            return
        if path == USUARIOS_PATH:
            if not user:
                self.redirect("/restrito/login")
                return
            if not is_admin(user):
                self.send_html(403, page("Usuários", "<h1>Usuários</h1><p>Somente um administrador pode cadastrar usuários.</p>", logged=True, current=USUARIOS_PATH))
                return
            self.handle_usuarios_get(user)
            return
        pages = {href.rstrip("/"): (label, text) for href, label, text in CADASTROS + MODULES}
        if path in pages:
            if not user:
                self.redirect("/restrito/login")
                return
            label, text = pages[path]
            self.send_html(200, module_page(label, text, path))
            return
        self.send_html(404, page("Não encontrado", "<h1>Página não encontrada</h1>"))

    def send_static(self, file_path: Path) -> None:
        body = file_path.read_bytes()
        content_type = STATIC_TYPES.get(file_path.suffix.lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "public, max-age=300")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        path = urlparse(self.path).path.rstrip("/")
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_FORM_BYTES:
            self.send_html(413, page("Pedido grande", "<h1>Pedido grande demais</h1>"))
            return
        ensure_schema()
        raw = self.rfile.read(length).decode("utf-8", "replace")
        form = {key: values[0] for key, values in parse_qs(raw).items()}
        if path == "/restrito/login":
            self.handle_login(form.get("username", ""), form.get("password", ""))
            return
        if path == "/restrito/sair":
            self.handle_logout()
            return
        if path == PASSWORD_PATH:
            user = current_user(self)
            if not user:
                self.redirect("/restrito/login")
                return
            self.handle_password_post(user, form)
            return
        user = current_user(self)
        if user and user["must_change_password"]:
            self.redirect(PASSWORD_PATH)
            return
        if path == TIPOS_PATH:
            if not current_user(self):
                self.redirect("/restrito/login")
                return
            self.handle_tipos_post(form)
            return
        if path == FORMAS_PATH:
            if not current_user(self):
                self.redirect("/restrito/login")
                return
            self.handle_formas_post(form)
            return
        if path == CONDICOES_PATH:
            if not current_user(self):
                self.redirect("/restrito/login")
                return
            self.handle_condicoes_post(form)
            return
        if path == CENTROS_PATH:
            if not current_user(self):
                self.redirect("/restrito/login")
                return
            self.handle_centros_post(form)
            return
        if path == USUARIOS_PATH:
            if not user:
                self.redirect("/restrito/login")
                return
            if not is_admin(user):
                self.send_html(403, page("Usuários", "<h1>Usuários</h1><p>Somente um administrador pode cadastrar usuários.</p>", logged=True, current=USUARIOS_PATH))
                return
            self.handle_usuarios_post(user, form)
            return
        self.send_html(404, page("Não encontrado", "<h1>Página não encontrada</h1>"))

    def query(self) -> dict[str, str]:
        parsed = urlparse(self.path)
        return {key: values[0] for key, values in parse_qs(parsed.query).items()}

    def handle_tipos_get(self) -> None:
        params = self.query()
        q = params.get("q", "").strip()[:80]
        acao = params.get("acao", "")
        aviso = {
            "incluido": "Tipo de documento incluído.",
            "alterado": "Tipo de documento alterado.",
            "excluido": "Tipo de documento deletado.",
        }.get(params.get("aviso", ""), "")
        erro = {
            "selecione": "Selecione um registro na grade.",
            "ausente": "Registro não encontrado.",
        }.get(params.get("erro", ""), "")
        editor = None
        with connect() as conn:
            if acao == "incluir":
                editor = {
                    "titulo": "Incluir tipo de documento",
                    "id": "",
                    "nome": "",
                    "descricao": "",
                    "esfera": "",
                    "errors": {},
                }
            elif acao == "editar":
                item_id = parse_record_id(params.get("id", ""))
                row = get_tipo(conn, item_id) if item_id else None
                if row is None:
                    erro = "Selecione um registro na grade."
                else:
                    editor = {
                        "titulo": "Editar tipo de documento",
                        "id": str(row[0]),
                        "nome": row[1],
                        "descricao": row[2],
                        "esfera": row[3],
                        "errors": {},
                    }
            rows = list_tipos(conn, q)
        self.send_html(200, tipos_page(rows=rows, q=q, aviso=aviso, erro=erro, editor=editor))

    def handle_tipos_post(self, form: dict[str, str]) -> None:
        acao = form.get("acao", "")
        q = form.get("q", "").strip()[:80]
        if acao == "salvar":
            self.save_tipo(form, q)
            return
        if acao == "editar":
            item_id = parse_record_id(form.get("id", ""))
            if item_id is None:
                self.redirect(tipos_url(q=q, erro="selecione"))
                return
            self.redirect(tipos_url(acao="editar", id=str(item_id), q=q))
            return
        if acao == "excluir":
            item_id = parse_record_id(form.get("id", ""))
            if item_id is None:
                self.redirect(tipos_url(q=q, erro="selecione"))
                return
            with connect() as conn:
                deleted = conn.execute(
                    "DELETE FROM tipo_documento WHERE id = %s", (item_id,)
                ).rowcount
                conn.commit()
            if not deleted:
                self.redirect(tipos_url(q=q, erro="ausente"))
                return
            self.redirect(tipos_url(q=q, aviso="excluido"))
            return
        self.redirect(TIPOS_PATH)

    def save_tipo(self, form: dict[str, str], q: str) -> None:
        item_id = parse_record_id(form.get("id", ""))
        nome = " ".join(form.get("nome", "").split())
        descricao = " ".join(form.get("descricao", "").split())
        esfera = form.get("esfera", "").strip()
        errors = validate_tipo(nome, descricao, esfera)
        editor = {
            "titulo": "Editar tipo de documento" if item_id else "Incluir tipo de documento",
            "id": str(item_id or ""),
            "nome": nome,
            "descricao": descricao,
            "esfera": esfera,
            "errors": errors,
        }
        if errors:
            with connect() as conn:
                rows = list_tipos(conn, q)
            self.send_html(400, tipos_page(rows=rows, q=q, aviso="", erro="", editor=editor))
            return
        try:
            with connect() as conn:
                if item_id:
                    found = get_tipo(conn, item_id)
                    if found is None:
                        self.redirect(tipos_url(q=q, erro="ausente"))
                        return
                    conn.execute(
                        """
                        UPDATE tipo_documento
                        SET nome = %s, descricao = %s, esfera = %s, updated_at = now()
                        WHERE id = %s
                        """,
                        (nome, descricao, esfera, item_id),
                    )
                    aviso = "alterado"
                else:
                    conn.execute(
                        """
                        INSERT INTO tipo_documento (nome, descricao, esfera)
                        VALUES (%s, %s, %s)
                        """,
                        (nome, descricao, esfera),
                    )
                    aviso = "incluido"
                conn.commit()
        except psycopg.errors.UniqueViolation:
            editor["errors"] = {"nome": "Já existe um tipo de documento com este nome."}
            with connect() as conn:
                rows = list_tipos(conn, q)
            self.send_html(400, tipos_page(rows=rows, q=q, aviso="", erro="", editor=editor))
            return
        self.redirect(tipos_url(q=q, aviso=aviso))

    def handle_formas_get(self) -> None:
        params = self.query()
        q = params.get("q", "").strip()[:80]
        acao = params.get("acao", "")
        aviso = {
            "incluido": "Forma de pagamento incluída.",
            "alterado": "Forma de pagamento alterada.",
            "excluido": "Forma de pagamento deletada.",
        }.get(params.get("aviso", ""), "")
        erro = {
            "selecione": "Selecione um registro na grade.",
            "ausente": "Registro não encontrado.",
        }.get(params.get("erro", ""), "")
        editor = None
        with connect() as conn:
            if acao == "incluir":
                editor = {
                    "titulo": "Incluir forma de pagamento",
                    "id": "",
                    "nome": "",
                    "descricao": "",
                    "errors": {},
                }
            elif acao == "editar":
                item_id = parse_record_id(params.get("id", ""))
                row = get_forma(conn, item_id) if item_id else None
                if row is None:
                    erro = "Selecione um registro na grade."
                else:
                    editor = {
                        "titulo": "Editar forma de pagamento",
                        "id": str(row[0]),
                        "nome": row[1],
                        "descricao": row[2],
                        "errors": {},
                    }
            rows = list_formas(conn, q)
        self.send_html(200, formas_page(rows=rows, q=q, aviso=aviso, erro=erro, editor=editor))

    def handle_formas_post(self, form: dict[str, str]) -> None:
        acao = form.get("acao", "")
        q = form.get("q", "").strip()[:80]
        if acao == "salvar":
            self.save_forma(form, q)
            return
        if acao == "editar":
            item_id = parse_record_id(form.get("id", ""))
            if item_id is None:
                self.redirect(formas_url(q=q, erro="selecione"))
                return
            self.redirect(formas_url(acao="editar", id=str(item_id), q=q))
            return
        if acao == "excluir":
            item_id = parse_record_id(form.get("id", ""))
            if item_id is None:
                self.redirect(formas_url(q=q, erro="selecione"))
                return
            with connect() as conn:
                deleted = conn.execute(
                    "DELETE FROM forma_pagamento WHERE id = %s", (item_id,)
                ).rowcount
                conn.commit()
            if not deleted:
                self.redirect(formas_url(q=q, erro="ausente"))
                return
            self.redirect(formas_url(q=q, aviso="excluido"))
            return
        self.redirect(FORMAS_PATH)

    def save_forma(self, form: dict[str, str], q: str) -> None:
        item_id = parse_record_id(form.get("id", ""))
        nome = " ".join(form.get("nome", "").split())
        descricao = " ".join(form.get("descricao", "").split())
        errors = validate_forma(nome, descricao)
        editor = {
            "titulo": "Editar forma de pagamento" if item_id else "Incluir forma de pagamento",
            "id": str(item_id or ""),
            "nome": nome,
            "descricao": descricao,
            "errors": errors,
        }
        if errors:
            with connect() as conn:
                rows = list_formas(conn, q)
            self.send_html(400, formas_page(rows=rows, q=q, aviso="", erro="", editor=editor))
            return
        try:
            with connect() as conn:
                if item_id:
                    found = get_forma(conn, item_id)
                    if found is None:
                        self.redirect(formas_url(q=q, erro="ausente"))
                        return
                    conn.execute(
                        """
                        UPDATE forma_pagamento
                        SET nome = %s, descricao = %s, updated_at = now()
                        WHERE id = %s
                        """,
                        (nome, descricao, item_id),
                    )
                    aviso = "alterado"
                else:
                    conn.execute(
                        "INSERT INTO forma_pagamento (nome, descricao) VALUES (%s, %s)",
                        (nome, descricao),
                    )
                    aviso = "incluido"
                conn.commit()
        except psycopg.errors.UniqueViolation:
            editor["errors"] = {"nome": "Já existe uma forma de pagamento com este nome ou esta descrição."}
            with connect() as conn:
                rows = list_formas(conn, q)
            self.send_html(400, formas_page(rows=rows, q=q, aviso="", erro="", editor=editor))
            return
        self.redirect(formas_url(q=q, aviso=aviso))

    def handle_condicoes_get(self) -> None:
        params = self.query()
        q = params.get("q", "").strip()[:80]
        acao = params.get("acao", "")
        aviso = {
            "incluido": "Condição de pagamento incluída.",
            "alterado": "Condição de pagamento alterada.",
            "excluido": "Condição de pagamento deletada.",
        }.get(params.get("aviso", ""), "")
        erro = {
            "selecione": "Selecione um registro na grade.",
            "ausente": "Registro não encontrado.",
        }.get(params.get("erro", ""), "")
        editor = None
        with connect() as conn:
            if acao == "incluir":
                editor = {
                    "titulo": "Incluir condição de pagamento",
                    "id": "",
                    "qtde_parcelas": "",
                    "nome": "",
                    "errors": {},
                }
            elif acao == "editar":
                item_id = parse_record_id(params.get("id", ""))
                row = get_condicao(conn, item_id) if item_id else None
                if row is None:
                    erro = "Selecione um registro na grade."
                else:
                    editor = {
                        "titulo": "Editar condição de pagamento",
                        "id": str(row[0]),
                        "qtde_parcelas": str(row[1]),
                        "nome": row[2],
                        "errors": {},
                    }
            rows = list_condicoes(conn, q)
        self.send_html(200, condicoes_page(rows=rows, q=q, aviso=aviso, erro=erro, editor=editor))

    def handle_condicoes_post(self, form: dict[str, str]) -> None:
        acao = form.get("acao", "")
        q = form.get("q", "").strip()[:80]
        if acao == "salvar":
            self.save_condicao(form, q)
            return
        if acao == "editar":
            item_id = parse_record_id(form.get("id", ""))
            if item_id is None:
                self.redirect(condicoes_url(q=q, erro="selecione"))
                return
            self.redirect(condicoes_url(acao="editar", id=str(item_id), q=q))
            return
        if acao == "excluir":
            item_id = parse_record_id(form.get("id", ""))
            if item_id is None:
                self.redirect(condicoes_url(q=q, erro="selecione"))
                return
            with connect() as conn:
                deleted = conn.execute(
                    "DELETE FROM condicao_pagamento WHERE id = %s", (item_id,)
                ).rowcount
                conn.commit()
            if not deleted:
                self.redirect(condicoes_url(q=q, erro="ausente"))
                return
            self.redirect(condicoes_url(q=q, aviso="excluido"))
            return
        self.redirect(CONDICOES_PATH)

    def save_condicao(self, form: dict[str, str], q: str) -> None:
        item_id = parse_record_id(form.get("id", ""))
        qtde = form.get("qtde_parcelas", "").strip()
        nome = " ".join(form.get("nome", "").split())
        errors = validate_condicao(qtde, nome)
        editor = {
            "titulo": "Editar condição de pagamento" if item_id else "Incluir condição de pagamento",
            "id": str(item_id or ""),
            "qtde_parcelas": qtde,
            "nome": nome,
            "errors": errors,
        }
        if errors:
            with connect() as conn:
                rows = list_condicoes(conn, q)
            self.send_html(400, condicoes_page(rows=rows, q=q, aviso="", erro="", editor=editor))
            return
        try:
            with connect() as conn:
                if item_id:
                    found = get_condicao(conn, item_id)
                    if found is None:
                        self.redirect(condicoes_url(q=q, erro="ausente"))
                        return
                    conn.execute(
                        """
                        UPDATE condicao_pagamento
                        SET qtde_parcelas = %s, nome = %s, updated_at = now()
                        WHERE id = %s
                        """,
                        (int(qtde), nome, item_id),
                    )
                    aviso = "alterado"
                else:
                    conn.execute(
                        "INSERT INTO condicao_pagamento (qtde_parcelas, nome) VALUES (%s, %s)",
                        (int(qtde), nome),
                    )
                    aviso = "incluido"
                conn.commit()
        except psycopg.errors.UniqueViolation:
            editor["errors"] = {"nome": "Já existe uma condição de pagamento com este nome."}
            with connect() as conn:
                rows = list_condicoes(conn, q)
            self.send_html(400, condicoes_page(rows=rows, q=q, aviso="", erro="", editor=editor))
            return
        self.redirect(condicoes_url(q=q, aviso=aviso))

    def handle_centros_get(self) -> None:
        params = self.query()
        q = params.get("q", "").strip()[:80]
        acao = params.get("acao", "")
        aviso = {
            "incluido": "Centro de custo inserido.",
            "alterado": "Centro de custo alterado.",
            "excluido": "Centro de custo excluído.",
        }.get(params.get("aviso", ""), "")
        erro = {
            "selecione": "Selecione um registro na grade.",
            "ausente": "Registro não encontrado.",
        }.get(params.get("erro", ""), "")
        editor = None
        with connect() as conn:
            if acao == "incluir":
                editor = {
                    "titulo": "Inserir centro de custo",
                    "id": "",
                    "codigo_reduzido": "",
                    "nome": "",
                    "descricao": "",
                    "errors": {},
                }
            elif acao == "editar":
                item_id = parse_record_id(params.get("id", ""))
                row = get_centro(conn, item_id) if item_id else None
                if row is None:
                    erro = "Selecione um registro na grade."
                else:
                    editor = {
                        "titulo": "Editar centro de custo",
                        "id": str(row[0]),
                        "codigo_reduzido": row[1],
                        "nome": row[2],
                        "descricao": row[3],
                        "errors": {},
                    }
            rows = list_centros(conn, q)
        self.send_html(200, centros_page(rows=rows, q=q, aviso=aviso, erro=erro, editor=editor))

    def handle_centros_post(self, form: dict[str, str]) -> None:
        acao = form.get("acao", "")
        q = form.get("q", "").strip()[:80]
        if acao == "salvar":
            self.save_centro(form, q)
            return
        if acao == "editar":
            item_id = parse_record_id(form.get("id", ""))
            if item_id is None:
                self.redirect(centros_url(q=q, erro="selecione"))
                return
            self.redirect(centros_url(acao="editar", id=str(item_id), q=q))
            return
        if acao == "excluir":
            item_id = parse_record_id(form.get("id", ""))
            if item_id is None:
                self.redirect(centros_url(q=q, erro="selecione"))
                return
            with connect() as conn:
                deleted = conn.execute(
                    "DELETE FROM centro_custo WHERE id = %s", (item_id,)
                ).rowcount
                conn.commit()
            if not deleted:
                self.redirect(centros_url(q=q, erro="ausente"))
                return
            self.redirect(centros_url(q=q, aviso="excluido"))
            return
        self.redirect(CENTROS_PATH)

    def save_centro(self, form: dict[str, str], q: str) -> None:
        item_id = parse_record_id(form.get("id", ""))
        codigo = form.get("codigo_reduzido", "").strip()
        nome = " ".join(form.get("nome", "").split())
        descricao = " ".join(form.get("descricao", "").split())
        errors = validate_centro(codigo, nome, descricao)
        editor = {
            "titulo": "Editar centro de custo" if item_id else "Inserir centro de custo",
            "id": str(item_id or ""),
            "codigo_reduzido": codigo,
            "nome": nome,
            "descricao": descricao,
            "errors": errors,
        }
        if errors:
            with connect() as conn:
                rows = list_centros(conn, q)
            self.send_html(400, centros_page(rows=rows, q=q, aviso="", erro="", editor=editor))
            return
        try:
            with connect() as conn:
                if item_id:
                    found = get_centro(conn, item_id)
                    if found is None:
                        self.redirect(centros_url(q=q, erro="ausente"))
                        return
                    conn.execute(
                        """
                        UPDATE centro_custo
                        SET codigo_reduzido = %s, nome = %s, descricao = %s, updated_at = now()
                        WHERE id = %s
                        """,
                        (codigo, nome, descricao, item_id),
                    )
                    aviso = "alterado"
                else:
                    conn.execute(
                        """
                        INSERT INTO centro_custo (codigo_reduzido, nome, descricao)
                        VALUES (%s, %s, %s)
                        """,
                        (codigo, nome, descricao),
                    )
                    aviso = "incluido"
                conn.commit()
        except psycopg.errors.UniqueViolation:
            editor["errors"] = {
                "codigo_reduzido": "Já existe um centro de custo com este código reduzido."
            }
            with connect() as conn:
                rows = list_centros(conn, q)
            self.send_html(400, centros_page(rows=rows, q=q, aviso="", erro="", editor=editor))
            return
        self.redirect(centros_url(q=q, aviso=aviso))

    def handle_usuarios_get(self, user) -> None:
        params = self.query()
        q = params.get("q", "").strip()[:80]
        acao = params.get("acao", "")
        aviso = {
            "incluido": "Usuário incluído com sucesso.",
            "alterado": "Usuário alterado.",
            "bloqueado": "Usuário bloqueado.",
            "desbloqueado": "Usuário desbloqueado.",
            "excluido": "Usuário excluído.",
            "admin": "Permissão de administrador atualizada.",
            "senha": "O usuário deverá trocar a senha no próximo acesso.",
        }.get(params.get("aviso", ""), "")
        erro = {
            "selecione": "Selecione um registro na grade.",
            "ausente": "Registro não encontrado.",
            "proprio": "Você não pode bloquear ou excluir o próprio usuário.",
            "ultimo": "É preciso manter pelo menos um administrador ativo.",
        }.get(params.get("erro", ""), "")
        editor = None
        with connect() as conn:
            if acao == "incluir":
                editor = {
                    "modo": "incluir",
                    "nome": "",
                    "login": "",
                    "email": "",
                    "administrador": False,
                    "errors": {},
                }
            elif acao == "editar":
                item_id = parse_record_id(params.get("id", ""))
                row = get_usuario(conn, item_id) if item_id else None
                if row is None:
                    erro = "Selecione um registro na grade."
                else:
                    editor = {
                        "modo": "editar",
                        "id": str(row[0]),
                        "nome": row[1],
                        "login": row[2],
                        "email": row[3] or "",
                        "errors": {},
                    }
            rows = list_usuarios(conn, q)
        self.send_html(200, usuarios_page(rows=rows, q=q, aviso=aviso, erro=erro, editor=editor, current_id=user["id"]))

    def handle_usuarios_post(self, user, form: dict[str, str]) -> None:
        acao = form.get("acao", "")
        q = form.get("q", "").strip()[:80]
        if acao == "salvar":
            self.save_usuario(user, form, q)
            return
        if acao == "editar":
            item_id = parse_record_id(form.get("id", ""))
            if item_id is None:
                self.redirect(usuarios_url(q=q, erro="selecione"))
                return
            self.redirect(usuarios_url(acao="editar", id=str(item_id), q=q))
            return
        item_id = parse_record_id(form.get("id", ""))
        if item_id is None:
            self.redirect(usuarios_url(q=q, erro="selecione"))
            return
        if acao in ("bloquear", "excluir") and item_id == user["id"]:
            self.redirect(usuarios_url(q=q, erro="proprio"))
            return
        with connect() as conn:
            row = get_usuario(conn, item_id)
            if row is None:
                self.redirect(usuarios_url(q=q, erro="ausente"))
                return
            role, active = row[4], row[5]
            if acao == "bloquear":
                new_active = not active
                if not keeps_an_admin(conn, item_id, active=new_active, role=role):
                    self.redirect(usuarios_url(q=q, erro="ultimo"))
                    return
                conn.execute(
                    "UPDATE app_user SET active = %s, updated_at = now() WHERE id = %s",
                    (new_active, item_id),
                )
                if not new_active:
                    conn.execute("DELETE FROM app_session WHERE user_id = %s", (item_id,))
                aviso = "desbloqueado" if new_active else "bloqueado"
            elif acao == "excluir":
                if not keeps_an_admin(conn, item_id, active=False, role="user"):
                    self.redirect(usuarios_url(q=q, erro="ultimo"))
                    return
                conn.execute("DELETE FROM app_user WHERE id = %s", (item_id,))
                aviso = "excluido"
            elif acao == "administrador":
                new_role = "user" if role in ("master", "admin") else "admin"
                if not keeps_an_admin(conn, item_id, active=active, role=new_role):
                    self.redirect(usuarios_url(q=q, erro="ultimo"))
                    return
                conn.execute(
                    "UPDATE app_user SET role = %s, updated_at = now() WHERE id = %s",
                    (new_role, item_id),
                )
                aviso = "admin"
            elif acao == "senha":
                conn.execute(
                    "UPDATE app_user SET must_change_password = true, updated_at = now() WHERE id = %s",
                    (item_id,),
                )
                conn.commit()
                if item_id == user["id"]:
                    self.redirect(PASSWORD_PATH)
                    return
                aviso = "senha"
            else:
                self.redirect(USUARIOS_PATH)
                return
            conn.commit()
        self.redirect(usuarios_url(q=q, aviso=aviso))

    def save_usuario(self, user, form: dict[str, str], q: str) -> None:
        item_id = parse_record_id(form.get("id", ""))
        nome = " ".join(form.get("nome", "").split())
        login = form.get("login", "").strip()
        email = form.get("email", "").strip().lower()
        senha = form.get("senha", "")
        confirmar = form.get("confirmar", "")
        creating = item_id is None
        errors = validate_usuario(nome, login, email, senha, confirmar, creating=creating)
        editor = {
            "modo": "incluir" if creating else "editar",
            "id": str(item_id or ""),
            "nome": nome,
            "login": login,
            "email": email,
            "administrador": form.get("administrador") == "1",
            "errors": errors,
        }
        if not creating and not errors:
            with connect() as conn:
                found = get_usuario(conn, item_id)
            if found is None:
                self.redirect(usuarios_url(q=q, erro="ausente"))
                return
            editor["email"] = found[3] or ""
        if errors:
            with connect() as conn:
                if not creating:
                    found = get_usuario(conn, item_id)
                    if found:
                        editor["email"] = found[3] or ""
                rows = list_usuarios(conn, q)
            self.send_html(400, usuarios_page(rows=rows, q=q, aviso="", erro="", editor=editor, current_id=user["id"]))
            return

        def reject(field: str, message: str) -> None:
            editor["errors"] = {field: message}
            with connect() as conn:
                rows = list_usuarios(conn, q)
            self.send_html(400, usuarios_page(rows=rows, q=q, aviso="", erro="", editor=editor, current_id=user["id"]))

        try:
            with connect() as conn:
                login_owner = conn.execute(
                    """
                    SELECT id FROM app_user
                    WHERE lower(username) = lower(%s)
                      AND (%s::bigint IS NULL OR id <> %s)
                    """,
                    (login, item_id, item_id),
                ).fetchone()
                if login_owner:
                    conn.rollback()
                    reject("login", "Já existe um usuário com este login.")
                    return
                if creating:
                    email_owner = conn.execute(
                        """
                        SELECT id FROM app_user
                        WHERE lower(email) = lower(%s)
                        """,
                        (email,),
                    ).fetchone()
                    if email_owner:
                        conn.rollback()
                        reject("email", "Já existe um usuário com este e-mail.")
                        return
                    conn.execute(
                        """
                        INSERT INTO app_user
                            (username, password_hash, display_name, email, role, active, must_change_password)
                        VALUES (%s, %s, %s, %s, %s, true, true)
                        """,
                        (
                            login,
                            hash_password(senha),
                            nome,
                            email,
                            "admin" if editor["administrador"] else "user",
                        ),
                    )
                    aviso = "incluido"
                else:
                    conn.execute(
                        """
                        UPDATE app_user
                        SET display_name = %s, username = %s, updated_at = now()
                        WHERE id = %s
                        """,
                        (nome, login, item_id),
                    )
                    aviso = "alterado"
                conn.commit()
        except psycopg.errors.UniqueViolation:
            reject("login", "Já existe um usuário com este login ou este e-mail.")
            return
        self.redirect(usuarios_url(q=q, aviso=aviso))

    def handle_password_post(self, user, form: dict[str, str]) -> None:
        atual = form.get("atual", "")
        nova = form.get("nova", "")
        confirmar = form.get("confirmar", "")
        if len(nova) < 8:
            self.send_html(400, password_form("A senha precisa ter pelo menos 8 caracteres."))
            return
        if nova != confirmar:
            self.send_html(400, password_form("A confirmação não confere com a senha."))
            return
        with connect() as conn:
            row = conn.execute(
                "SELECT password_hash FROM app_user WHERE id = %s AND active",
                (user["id"],),
            ).fetchone()
            if not row or not verify_password(atual, row[0]):
                self.send_html(401, password_form("A senha atual não confere."))
                return
            if verify_password(nova, row[0]):
                self.send_html(400, password_form("Escolha uma senha diferente da atual."))
                return
            conn.execute(
                """
                UPDATE app_user
                SET password_hash = %s, must_change_password = false, updated_at = now()
                WHERE id = %s
                """,
                (hash_password(nova), user["id"]),
            )
            conn.commit()
        self.redirect("/restrito/cadastros")

    def handle_login(self, username: str, password: str) -> None:
        username = username.strip()
        generic = "Usuário ou senha inválidos."
        if not database_url():
            self.send_html(503, login_form("O banco ainda não está configurado."))
            return
        with connect() as conn:
            row = conn.execute(
                """
                SELECT id, password_hash, active, must_change_password
                FROM app_user
                WHERE lower(username) = lower(%s)
                """,
                (username,),
            ).fetchone()
            if not row or not verify_password(password, row[1]):
                self.send_html(401, login_form(generic))
                return
            if not row[2]:
                self.send_html(403, login_form("Usuário bloqueado. Procure o administrador."))
                return
            must_change = bool(row[3])
            token = secrets.token_urlsafe(32)
            expires = datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)
            conn.execute("DELETE FROM app_session WHERE expires_at < now()")
            conn.execute(
                "INSERT INTO app_session (id, user_id, expires_at) VALUES (%s, %s, %s)",
                (token, row[0], expires),
            )
            conn.commit()
        cookie = (
            f"{SESSION_COOKIE}={token}; HttpOnly; SameSite=Lax; Path=/; "
            f"Max-Age={SESSION_HOURS * 3600}"
        )
        self.redirect(PASSWORD_PATH if must_change else "/restrito/cadastros", cookie=cookie)

    def handle_logout(self) -> None:
        token = ""
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == SESSION_COOKIE:
                token = value
        if token and database_url():
            with connect() as conn:
                conn.execute("DELETE FROM app_session WHERE id = %s", (token,))
                conn.commit()
        cookie = f"{SESSION_COOKIE}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"
        self.redirect("/restrito/login", cookie=cookie)


_schema_ready = False


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready or not database_url():
        return
    migrate_and_seed()
    _schema_ready = True


def main() -> None:
    ensure_schema()
    bind = os.environ.get("BIND", "127.0.0.1")
    port = int(os.environ.get("PORT", "8090"))
    server = ThreadingHTTPServer((bind, port), Handler)
    print(f"área restrita em http://{bind}:{port}/restrito/")
    server.serve_forever()


if __name__ == "__main__":
    main()
