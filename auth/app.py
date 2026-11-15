#!/usr/bin/env python3
"""Área restrita da Apinil: login, sessão e páginas internas."""

from __future__ import annotations

import contextvars
import hashlib
import hmac
import html
import ipaddress
import os
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import psycopg

SESSION_COOKIE = "apinil_session"
SESSION_HOURS = 12
PBKDF2_ROUNDS = 600_000
MAX_FORM_BYTES = 64 * 1024
MAX_UPLOAD_BYTES = 4 * 1024 * 1024
MAX_WORKERS = 8
REQUEST_TIMEOUT = 20
THROTTLE_WINDOW = timedelta(minutes=15)
LOGIN_PAIR_LIMIT = 5
LOGIN_USER_LIMIT = 10
LOGIN_IP_LIMIT = 30
PASSWORD_LIMIT = 5
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

RELATORIOS = (
    ("/restrito/relatorios/titulos-em-aberto", "Títulos em aberto", "O que ainda falta pagar ou receber."),
    ("/restrito/relatorios/titulos-baixados", "Títulos baixados", "O que já foi pago ou recebido, com data, forma e conta."),
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


def session_id(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def session_cookie(token: str, *, secure: bool, clear: bool = False) -> str:
    value = "" if clear else token
    age = 0 if clear else SESSION_HOURS * 3600
    parts = [
        f"{SESSION_COOKIE}={value}",
        "HttpOnly",
        "SameSite=Lax",
        "Path=/",
        f"Max-Age={age}",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def security_headers(host: str) -> list[tuple[str, str]]:
    headers = [
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Content-Security-Policy", "frame-ancestors 'none'"),
        ("Referrer-Policy", "same-origin"),
    ]
    name = host.split(":")[0].strip("[]").lower()
    if name not in {"localhost", "127.0.0.1", "::1"}:
        headers.append(("Strict-Transport-Security", "max-age=31536000"))
    return headers


def request_host(handler: BaseHTTPRequestHandler) -> str:
    return (handler.headers.get("Host") or "").strip()


def request_is_local(handler: BaseHTTPRequestHandler) -> bool:
    name = request_host(handler).split(":")[0].strip("[]").lower()
    return name in {"localhost", "127.0.0.1", "::1"}


_TRUSTED_PROXIES = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)
_dummy_password_hash = ""


def parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def is_trusted_proxy(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(ip in network for network in _TRUSTED_PROXIES)


def dummy_password_hash() -> str:
    global _dummy_password_hash
    if not _dummy_password_hash:
        _dummy_password_hash = hash_password("not-a-real-password")
    return _dummy_password_hash


def client_ip(handler: BaseHTTPRequestHandler) -> str:
    address = getattr(handler, "client_address", None)
    peer_text = str(address[0] or "") if address else ""
    peer = parse_ip(peer_text)
    if peer is None or not is_trusted_proxy(peer):
        return (peer_text or "desconhecido")[:80]
    forwarded = handler.headers.get("X-Forwarded-For", "")
    parts = [part.strip() for part in forwarded.split(",") if part.strip()]
    for part in reversed(parts):
        ip = parse_ip(part)
        if ip is not None and not is_trusted_proxy(ip):
            return str(ip)[:80]
    if parts:
        ip = parse_ip(parts[-1])
        if ip is not None:
            return str(ip)[:80]
    return str(peer)[:80]


def throttle_blocked(conn, buckets: list[tuple[str, int]]) -> bool:
    cutoff = datetime.now(timezone.utc) - THROTTLE_WINDOW
    for bucket, limit in buckets:
        row = conn.execute(
            """
            SELECT failures FROM auth_throttle
            WHERE bucket = %s AND window_start >= %s
            """,
            (bucket, cutoff),
        ).fetchone()
        if row and row[0] >= limit:
            return True
    return False


def throttle_record(conn, buckets: list[str]) -> None:
    cutoff = datetime.now(timezone.utc) - THROTTLE_WINDOW
    now = datetime.now(timezone.utc)
    for bucket in buckets:
        conn.execute(
            """
            INSERT INTO auth_throttle (bucket, failures, window_start)
            VALUES (%s, 1, %s)
            ON CONFLICT (bucket) DO UPDATE
            SET failures = CASE
                    WHEN auth_throttle.window_start < %s THEN 1
                    ELSE auth_throttle.failures + 1
                END,
                window_start = CASE
                    WHEN auth_throttle.window_start < %s THEN %s
                    ELSE auth_throttle.window_start
                END
            """,
            (bucket, now, cutoff, cutoff, now),
        )


def throttle_clear(conn, buckets: list[str]) -> None:
    if buckets:
        conn.execute("DELETE FROM auth_throttle WHERE bucket = ANY(%s)", (buckets,))


def login_buckets(ip: str, username: str) -> list[tuple[str, int]]:
    user = username.lower()[:80]
    return [
        (f"login-pair:{ip}:{user}", LOGIN_PAIR_LIMIT),
        (f"login-user:{user}", LOGIN_USER_LIMIT),
        (f"login-ip:{ip}", LOGIN_IP_LIMIT),
    ]


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


ICONS = {
    "home": '<path d="M4 10.5 12 4l8 6.5V20a1 1 0 0 1-1 1h-5v-6H10v6H5a1 1 0 0 1-1-1z"/>',
    "folder": '<path d="M3 7.5A1.5 1.5 0 0 1 4.5 6H9l2 2h8.5A1.5 1.5 0 0 1 21 9.5v8A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5z"/>',
    "users": '<path d="M16 19v-1a3 3 0 0 0-3-3H7a3 3 0 0 0-3 3v1"/><circle cx="10" cy="8" r="3"/><path d="M20 19v-1a3 3 0 0 0-2.2-2.9"/><path d="M16 5.1a3 3 0 0 1 0 5.8"/>',
    "file": '<path d="M7 3.5h7l5 5V20a1.5 1.5 0 0 1-1.5 1.5h-10.5A1.5 1.5 0 0 1 6 20V5A1.5 1.5 0 0 1 7.5 3.5H7z"/><path d="M14 3.5V9h5"/>',
    "calendar": '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M8 3.5V7M16 3.5V7M4 10h16"/>',
    "card": '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M3 10h18"/>',
    "tag": '<path d="M3 12V5.5A1.5 1.5 0 0 1 4.5 4H11l9 9-7 7z"/><circle cx="8" cy="8" r="1.2"/>',
    "user": '<circle cx="12" cy="8" r="3.2"/><path d="M5 19.5v-1A4.5 4.5 0 0 1 9.5 14h5a4.5 4.5 0 0 1 4.5 4.5v1"/>',
    "down": '<path d="M7 10h10l-5 6z"/>',
    "up": '<path d="M7 14h10l-5-6z"/>',
    "moon": '<path d="M16 3.5A8 8 0 1 0 20.5 14 6.2 6.2 0 0 1 16 3.5z"/>',
    "search": '<circle cx="11" cy="11" r="6"/><path d="m16 16 4 4"/>',
    "chart": '<path d="M4 19h16"/><path d="M7 16V9"/><path d="M12 16V5"/><path d="M17 16v-6"/>',
    "menu": '<path d="M4 7h16M4 12h16M4 17h16"/>',
}


def icon(name: str) -> str:
    return (
        f'<svg class="ico" viewBox="0 0 24 24" aria-hidden="true" fill="none" '
        f'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        f'{ICONS[name]}</svg>'
    )


NAV_ICONS = {
    "/restrito/cadastros/pessoas": "users",
    "/restrito/cadastros/tipos-de-documentos": "file",
    "/restrito/cadastros/condicoes-de-pagamentos": "calendar",
    "/restrito/cadastros/formas-de-pagamentos": "card",
    "/restrito/cadastros/centros-de-custo": "tag",
    "/restrito/cadastros/usuarios": "user",
    "/restrito/contas-a-pagar": "down",
    "/restrito/contas-a-receber": "up",
    "/restrito/titulos": "file",
    "/restrito/relatorios/titulos-em-aberto": "chart",
    "/restrito/relatorios/titulos-baixados": "chart",
}


def nav_link(href: str, label: str, current: str) -> str:
    path = href.rstrip("/") or "/"
    selected = ' aria-current="page"' if current == path else ""
    mark = icon(NAV_ICONS.get(path, "file"))
    return f'<a href="{esc(href)}"{selected}>{mark}<span>{esc(label)}</span></a>'


def side_nav(current: str) -> str:
    user = VIEWER.get()
    cadastro_open = current.startswith("/restrito/cadastros")
    section = "is-open" if cadastro_open else ""
    links = [
        f'<a href="/restrito/cadastros"{" aria-current=\"page\"" if current == "/restrito/cadastros" else ""}>'
        f'{icon("home")}<span>Início</span></a>',
        f'<p class="nav-label {section}">Cadastros</p>',
        '<div class="submenu">',
        "".join(nav_link(href, label, current) for href, label, _text in CADASTROS),
        "</div>",
    ]
    links.extend(nav_link(href, label, current) for href, label, _text in MODULES)
    links.append('<p class="nav-label">Relatórios</p>')
    links.append('<div class="submenu">')
    links.extend(nav_link(href, label, current) for href, label, _text in RELATORIOS)
    links.append("</div>")
    account = ""
    if user:
        identity = user.get("email") or user.get("username") or ""
        account = (
            '<div class="account">'
            "<span>Conectado como</span>"
            f"<strong>{esc(user.get('name') or identity)}</strong>"
            f"<small>{esc(identity)}</small>"
            '<form method="post" action="/restrito/sair"><button type="submit" class="link-danger">Sair</button></form>'
            "</div>"
        )
    return '<nav class="side">' + "".join(links) + "</nav>" + account


def page(
    title: str,
    body: str,
    *,
    logged: bool = False,
    current: str = "",
    wide: bool = False,
    search: dict | None = None,
    action: dict | None = None,
) -> bytes:
    if not logged:
        document = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)} | Apinil</title>
  <link rel="icon" href="/assets/img/logo.png">
  <link rel="stylesheet" href="/assets/css/site.css">
  <link rel="stylesheet" href="/restrito/app.css">
</head>
<body class="app-body app-login">
  <main class="login-wrap">{body}</main>
</body>
</html>"""
        return document.encode("utf-8")
    search_html = ""
    if search:
        search_html = (
            f'<form class="top-search" method="get" action="{esc(search["action"])}">'
            f'{icon("search")}'
            f'<input name="q" value="{esc(search.get("q", ""))}" placeholder="{esc(search.get("placeholder", "Pesquisar"))}" aria-label="{esc(search.get("placeholder", "Pesquisar"))}">'
            "</form>"
        )
    action_html = ""
    if action:
        action_html = f'<a class="primary" href="{esc(action["href"])}">{esc(action["label"])}</a>'
    document = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)} | Apinil</title>
  <link rel="icon" href="/assets/img/logo.png">
  <link rel="stylesheet" href="/assets/css/site.css">
  <link rel="stylesheet" href="/restrito/app.css">
</head>
<body class="app-body">
  <aside class="app-side" id="app-side">
    <a class="app-brand" href="/restrito/cadastros"><img src="/assets/img/logo.png" alt="Apinil cosméticos"></a>
    {side_nav(current)}
  </aside>
  <div class="app-main">
    <header class="app-top">
      <button type="button" class="icon-btn menu-btn" id="menu-btn" aria-label="Abrir menu">{icon("menu")}</button>
      {search_html}
      <div class="top-actions">
        <button type="button" class="icon-btn" id="theme-btn" aria-label="Alternar tema">{icon("moon")}</button>
        {action_html}
      </div>
    </header>
    <div class="app-content">{body}</div>
  </div>
  <script>
    const root = document.documentElement;
    const saved = localStorage.getItem("apinil-theme");
    if (saved === "dark") root.dataset.theme = "dark";
    document.getElementById("theme-btn").addEventListener("click", () => {{
      const next = root.dataset.theme === "dark" ? "" : "dark";
      if (next) root.dataset.theme = next;
      else delete root.dataset.theme;
      localStorage.setItem("apinil-theme", next);
    }});
    const side = document.getElementById("app-side");
    document.getElementById("menu-btn").addEventListener("click", () => side.classList.toggle("is-open"));
  </script>
</body>
</html>"""
    return document.encode("utf-8")


LOCK = """<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8">
  <rect x="5" y="11" width="14" height="9" rx="2"></rect>
  <path d="M8 11V8a4 4 0 0 1 8 0v3"></path>
</svg>"""

APP_CSS = """
html[data-theme="dark"] {
  color-scheme: dark;
  --green: #3dbe74;
  --green-dark: #d7f5e4;
  --ink: #f3faf6;
  --muted: #c5d4cb;
  --line: #3d5248;
  --paper: #121815;
  --white: #1c2420;
  --field: #24302a;
  --shadow: 0 10px 30px rgba(0, 0, 0, .28);
}
.app-body {
  margin: 0; min-height: 100vh; background: var(--paper); color: var(--ink);
  font-family: "Segoe UI", sans-serif; font-size: 15px;
}
.app-body h1, .app-body h2, .app-body .section-title {
  font-family: "Segoe UI", sans-serif; text-transform: none; letter-spacing: 0; text-align: left; color: var(--ink);
}
.app-login { display: grid; place-items: center; padding: 32px 16px; }
.login-wrap { width: min(440px, 100%); }
.login-card {
  background: var(--white); border-radius: 18px; padding: 28px; box-shadow: var(--shadow);
}
.login-card h1 { margin: 12px 0 6px; font-size: 28px; }
.login-card p { color: var(--muted); }
.login-brand { height: 64px; width: auto; }
.app-body form { display: grid; gap: 12px; }
.app-body label { display: grid; gap: 6px; font-size: 13px; font-weight: 600; color: var(--muted); }
.app-body input, .app-body textarea, .app-body select {
  font: inherit; color: var(--ink); background: #f3f6f4; border: 1px solid transparent;
  border-radius: 10px; padding: 12px 14px;
}
html[data-theme="dark"] .app-body input:not([type="radio"]):not([type="checkbox"]):not([type="file"]),
html[data-theme="dark"] .app-body textarea,
html[data-theme="dark"] .app-body select {
  background: var(--field); color: var(--ink); border-color: #5d7a6a;
}
html[data-theme="dark"] .app-body input::placeholder,
html[data-theme="dark"] .app-body textarea::placeholder { color: #b7c9be; }
.app-body input:focus, .app-body textarea:focus, .app-body select:focus {
  outline: none; border-color: var(--green); background: var(--white);
}
html[data-theme="dark"] .app-body input:focus,
html[data-theme="dark"] .app-body textarea:focus,
html[data-theme="dark"] .app-body select:focus { background: #2c3b33; }
.app-body button, .app-body .primary {
  border: 0; background: var(--green); color: white; border-radius: 10px;
  padding: 12px 16px; font: inherit; font-weight: 700; cursor: pointer; text-decoration: none;
}
.app-side {
  position: fixed; inset: 0 auto 0 0; width: 248px; background: #f7faf8;
  border-right: 1px solid var(--line); display: flex; flex-direction: column; padding: 18px 14px 16px;
}
html[data-theme="dark"] .app-side { background: #18211c; }
.app-brand img { height: 58px; width: auto; display: block; margin: 0 8px 18px; }
.side { display: flex; flex-direction: column; gap: 4px; overflow: auto; }
.side a, .nav-label {
  display: flex; align-items: center; gap: 10px; text-decoration: none; color: var(--ink);
  border-radius: 10px; padding: 9px 12px; font-weight: 600;
}
.nav-label { margin: 14px 0 2px; color: var(--muted); font-size: 12px; letter-spacing: .08em; text-transform: uppercase; }
.side a:hover { background: #e7f3ec; }
html[data-theme="dark"] .side a:hover { background: #24302a; }
.side a[aria-current="page"] { background: #e5f3ea; color: var(--green-dark); }
html[data-theme="dark"] .side a[aria-current="page"] { background: #234233; color: var(--green); }
.submenu { display: flex; flex-direction: column; gap: 2px; margin-left: 8px; }
.submenu a { font-weight: 500; }
.ico { width: 18px; height: 18px; flex: 0 0 auto; }
.account { margin-top: auto; padding-top: 16px; border-top: 1px solid var(--line); display: grid; gap: 2px; }
.account span { font-size: 11px; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); }
.account strong { font-size: 14px; }
.account small { color: var(--muted); }
.account form { display: block; }
.link-danger, button.link-danger {
  background: transparent; color: #c43838; padding: 8px 0; text-align: left; font-weight: 700;
}
.app-main { margin-left: 248px; min-height: 100vh; overflow-x: hidden; }
.app-top {
  position: sticky; top: 0; z-index: 5; display: flex; align-items: center; gap: 12px;
  padding: 16px 22px; background: rgba(244, 248, 245, .92); backdrop-filter: blur(8px);
}
html[data-theme="dark"] .app-top { background: rgba(18, 24, 21, .92); }
.app-body .top-search {
  flex: 1; display: flex; align-items: center; gap: 8px; background: var(--white);
  border: 1px solid var(--line); border-radius: 999px; padding: 0 14px; min-width: 0;
}
.top-search input { border: 0; background: transparent; padding: 11px 0; width: 100%; }
.top-search input:focus { border: 0; background: transparent; }
.top-actions { display: flex; align-items: center; gap: 10px; margin-left: auto; }
.icon-btn {
  width: 40px; height: 40px; border-radius: 999px; background: var(--white); color: var(--ink);
  border: 1px solid var(--line); display: grid; place-items: center; padding: 0;
}
.menu-btn { display: none; }
.app-content { padding: 8px 22px 48px; }
.page-head { margin: 0 0 16px; }
.eyebrow { margin: 0 0 4px; color: var(--green); font-size: 12px; font-weight: 700; letter-spacing: .12em; }
.page-head h1 { margin: 0; font-size: 32px; }
.lede { margin: 6px 0 0; color: var(--muted); max-width: 640px; }
.flash { background: #e7f5ec; color: #114d2c; border-radius: 12px; padding: 12px 14px; margin: 0 0 14px; }
.flash.error, .error { color: #8d2b2b; }
.flash.error { background: #fdecec; font-weight: 600; }
html[data-theme="dark"] .flash { background: #1b3d2b; color: #d8f5e4; }
html[data-theme="dark"] .flash.error { background: #3d2424; color: #ffd0d0; }
html[data-theme="dark"] .error { color: #ffb4b4; }
.split { display: grid; grid-template-columns: minmax(260px, 340px) minmax(0, 1fr); gap: 16px; align-items: start; }
.panel { background: var(--white); border-radius: 16px; box-shadow: var(--shadow); padding: 18px; }
.panel h2 { margin: 0 0 14px; font-size: 18px; }
.panel-head { display: flex; justify-content: space-between; gap: 12px; align-items: baseline; }
.panel-head span { color: var(--muted); font-size: 13px; white-space: nowrap; }
.record-form button[type="submit"] { width: 100%; margin-top: 4px; }
.record-form .form-actions { display: flex; gap: 8px; }
.record-form .form-actions button[type="submit"] { width: auto; flex: 1; margin-top: 0; }
.tabs { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 12px; }
.tabs button { width: auto; background: transparent; color: var(--ink); border: 1px solid var(--line); }
.tabs button.is-on { background: var(--green); color: white; border-color: var(--green); }
.tab-panel { display: none; gap: 12px; }
.tab-panel.is-on { display: grid; }
.doc-row, .cep-row { display: flex; gap: 8px; align-items: end; }
.doc-row input, .cep-row input { flex: 1; }
.inline-btn { width: auto; white-space: nowrap; }
.addr { border: 1px solid var(--line); border-radius: 12px; padding: 12px; display: grid; gap: 10px; }
.addr h3 { margin: 0; font-size: 15px; }
.hint { margin: 0; color: var(--muted); font-size: 13px; }
.split.wide-form { grid-template-columns: minmax(440px, 1.2fr) minmax(280px, .8fr); }
.parcelas { display: grid; gap: 8px; }
.parcela { display: grid; grid-template-columns: 72px 1fr 1fr; gap: 8px; align-items: end; }
.app-content:has(.finance-desk) { padding-bottom: 16px; }
.finance-desk {
  display: grid;
  grid-template-rows: auto auto minmax(180px, 1fr) auto;
  gap: 10px;
  min-height: calc(100vh - 124px);
}
.finance-desk .page-head { margin: 0; }
.finance-desk .page-head h1 { font-size: 22px; }
.finance-desk .lede { margin: 2px 0 0; font-size: 13px; }
.finance-desk .flash { margin: 0; padding: 8px 10px; }
.finance-desk .panel {
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  min-height: auto;
  overflow: visible;
}
.finance-desk .panel h2 { margin: 0; font-size: 15px; }
.finance-desk .panel-head { margin: 0 0 8px; flex-wrap: wrap; align-items: center; row-gap: 6px; }
.finance-desk .panel-head span { white-space: normal; }
.finance-desk .panel-head .hint { font-weight: 500; }
.finance-desk .grid-wrap { overflow: auto; min-height: 0; }
.finance-list .grid-wrap { max-height: min(320px, 34vh); }
.finance-parcelas .grid-wrap { max-height: min(220px, 26vh); }
.finance-desk table.grid td, .finance-desk table.grid th { padding: 6px 8px; }
.finance-desk form.finance-form {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px 10px;
  align-items: end;
}
.finance-form input[type="hidden"] { display: none; }
.finance-desk form.finance-form > label,
.finance-desk form.finance-form > .campo { min-width: 0; max-width: 100%; }
.finance-desk .span-2 { grid-column: span 2; }
.finance-desk input, .finance-desk select { padding: 7px 10px; }
.finance-desk form.finance-form input:not([type="radio"]):not([type="checkbox"]):not([type="file"]):not([type="hidden"]),
.finance-desk form.finance-form select {
  width: 100%;
  min-width: 0;
  max-width: 100%;
}
@media (max-width: 1180px) {
  .finance-desk form.finance-form { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
.finance-origem { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; color: var(--ink); min-width: 0; }
.finance-origem label { display: flex; flex-direction: row; align-items: center; gap: 4px; color: var(--ink); white-space: nowrap; }
.finance-origem input { width: auto; accent-color: var(--green); }
.file-btn {
  position: relative; overflow: hidden; display: inline-flex; align-items: center; justify-content: center;
  box-sizing: border-box; height: 40px; background: var(--white); color: var(--ink); border: 1px solid var(--line);
  border-radius: 10px; padding: 0 12px; font-weight: 700; cursor: pointer; white-space: nowrap;
}
html[data-theme="dark"] .file-btn { background: var(--field); color: var(--ink); border-color: #5d7a6a; }
.file-btn input { position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; }
.finance-desk .campo { display: grid; gap: 4px; font-size: 13px; font-weight: 600; color: var(--muted); }
.finance-desk .campo input[type="file"] { padding: 6px 8px; }
.finance-desk .form-actions { display: flex; gap: 8px; align-items: center; flex: 0 0 auto; }
.finance-desk .form-actions button,
.finance-desk form.pay-filters button,
.app-top .primary {
  box-sizing: border-box; height: 40px; min-width: 132px; margin: 0; padding: 0 16px;
  white-space: nowrap; line-height: 1; display: inline-flex; align-items: center; justify-content: center;
}
.finance-desk .form-actions button { width: auto; flex: 0 0 auto; }
.finance-desk .num, .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.parcelas-grid { width: min(720px, 100%); }
.parcelas-grid td:first-child { width: 72px; font-weight: 700; }
.finance-parcelas input { width: 100%; }
.finance-desk form.pay-filters {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 8px;
  align-items: end;
  margin: 0 0 8px;
}
.finance-desk form.pay-filters button { width: auto; justify-self: start; }
.finance-list tr.is-pick { cursor: pointer; }
.finance-list tr.is-pick:hover td { background: #f3faf6; }
html[data-theme="dark"] .finance-list tr.is-pick:hover td { background: #203128; }
.text-link { color: var(--muted); font-weight: 600; text-decoration: none; text-align: center; }
.record-form textarea { min-height: 96px; resize: vertical; }
.record-form input[readonly] { color: var(--muted); }
.checkline { display: flex; align-items: center; justify-content: space-between; color: var(--ink); }
.checkline input { width: auto; accent-color: var(--green); }
.grid-wrap { overflow-x: auto; }
table.grid { width: 100%; border-collapse: collapse; }
table.grid th {
  font-size: 11px; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); font-weight: 700;
}
table.grid td, table.grid th { padding: 12px 8px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
table.grid td strong { font-weight: 700; }
table.grid tr.is-selected { background: #f3faf6; }
html[data-theme="dark"] table.grid tr.is-selected { background: #203128; }
.row-actions { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; }
.row-actions a { color: var(--ink); font-weight: 700; text-decoration: none; }
.row-actions form { display: inline; }
.row-actions button { background: transparent; color: var(--ink); padding: 0; font-weight: 700; }
.row-actions button.link-danger { color: #c43838; }
html[data-theme="dark"] .row-actions a,
html[data-theme="dark"] .row-actions button { color: #f3faf6; }
html[data-theme="dark"] .row-actions button.link-danger,
html[data-theme="dark"] button.link-danger { color: #ffb4b4; }
.app-body input[type="checkbox"] {
  width: 16px; height: 16px; padding: 0; accent-color: var(--green); vertical-align: middle;
}
.baixa-bar { display: flex; flex-direction: column; gap: 8px; margin: 0; }
.baixa-bar input[type="hidden"] { display: none; }
.baixa-fields {
  display: grid;
  grid-template-columns: minmax(0, 0.8fr) minmax(0, 1fr) minmax(0, 1.4fr) auto;
  gap: 8px 12px;
  align-items: start;
}
.baixa-fields > label { min-width: 0; max-width: 100%; }
.baixa-fields input,
.baixa-fields select {
  width: 100%; min-width: 0; max-width: 100%; box-sizing: border-box; height: 40px; padding: 8px 10px;
}
.baixa-fields .error { display: block; margin-top: 4px; }
.baixa-action { padding-top: 24px; }
.baixa-action button {
  box-sizing: border-box; height: 40px; margin: 0; padding: 0 16px; white-space: nowrap;
}
.baixa-bar .hint { margin: 0; }
.baixa-bar td.row-actions { vertical-align: middle; white-space: nowrap; }
.report-filters {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) minmax(0, .8fr) minmax(0, 1.2fr) auto;
  gap: 8px 12px;
  align-items: start;
  margin: 0 0 12px;
}
.report-filters > label { min-width: 0; max-width: 100%; }
.report-filters input, .report-filters select {
  width: 100%; min-width: 0; max-width: 100%; box-sizing: border-box; height: 40px; padding: 8px 10px;
}
.report-filters .error { display: block; margin-top: 4px; }
.report-action { padding-top: 24px; }
.report-action button {
  box-sizing: border-box; height: 40px; margin: 0; padding: 0 16px; white-space: nowrap;
}
.eyebrow.section-gap { margin-top: 22px; }
.pill { display: inline-block; background: #e5f6ea; color: #114d2c; border-radius: 999px; padding: 3px 10px; font-size: 12px; font-weight: 700; white-space: nowrap; }
.pill.off { background: #fdecec; color: #8d2b2b; }
html[data-theme="dark"] .pill { background: #1b4a30; color: #d8f5e4; }
html[data-theme="dark"] .pill.off { background: #4a2424; color: #ffd0d0; }
.module-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; }
.module-card {
  background: var(--white); border-radius: 16px; padding: 18px; box-shadow: var(--shadow);
  text-decoration: none; color: inherit; display: block;
}
.module-card h2 { margin: 0 0 6px; font-size: 20px; }
.module-card p { margin: 0; color: var(--muted); }
dialog.modal { border: 0; border-radius: 16px; padding: 24px; max-width: 420px; box-shadow: var(--shadow); }
dialog.modal::backdrop { background: rgba(16, 32, 24, .45); }
dialog.modal h2 { margin: 0 0 8px; }
@media (max-width: 980px) {
  .menu-btn { display: grid; }
  .app-side { transform: translateX(-105%); transition: transform .2s ease; z-index: 20; }
  .app-side.is-open { transform: none; }
  .app-main { margin-left: 0; }
  .split, .split.wide-form, .baixa-fields, .report-filters { grid-template-columns: 1fr 1fr; }
  .baixa-action { padding-top: 24px; }
  .parcela { grid-template-columns: 1fr; }
  .app-top { padding: 12px; }
  .app-content { padding: 8px 12px 36px; }
}
"""


def flashes_html(aviso: str, erro: str) -> str:
    parts = []
    if aviso:
        parts.append(f'<p class="flash">{esc(aviso)}</p>')
    if erro:
        parts.append(f'<p class="flash error">{esc(erro)}</p>')
    return "".join(parts)


def field_error(errors: dict, key: str) -> str:
    message = (errors or {}).get(key)
    return f'<span class="error">{esc(message)}</span>' if message else ""


def count_label(total: int) -> str:
    return f"{total} registro(s)"


def row_actions(path: str, item_id: int, q: str, confirm: str, extra: str = "") -> str:
    edit = f'<a href="{esc(path + "?" + urlencode({"acao": "editar", "id": str(item_id), "q": q}))}">Editar</a>'
    delete = (
        f'<form method="post" action="{esc(path)}" onsubmit="return confirm(\'{esc(confirm)}\')">'
        '<input type="hidden" name="acao" value="excluir">'
        f'<input type="hidden" name="id" value="{item_id}">'
        f'<input type="hidden" name="q" value="{esc(q)}">'
        '<button type="submit" class="link-danger">Excluir</button></form>'
    )
    return f'<div class="row-actions">{edit}{delete}{extra}</div>'


def screen(kicker: str, title: str, lede: str, flashes: str, form_title: str, form: str, list_title: str, total: int, table: str) -> str:
    return f"""
<header class="page-head">
  <p class="eyebrow">{esc(kicker)}</p>
  <h1>{esc(title)}</h1>
  <p class="lede">{esc(lede)}</p>
</header>
{flashes}
<div class="split">
  <section class="panel"><h2>{esc(form_title)}</h2>{form}</section>
  <section class="panel">
    <div class="panel-head"><h2>{esc(list_title)}</h2><span>{esc(count_label(total))}</span></div>
    <div class="grid-wrap">{table}</div>
  </section>
</div>
"""


def login_form(error: str = "") -> bytes:
    message = f'<p class="error">{esc(error)}</p>' if error else ""
    body = f"""
<section class="login-card">
  <img class="login-brand" src="/assets/img/logo.png" alt="Apinil cosméticos">
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


VIEWER: contextvars.ContextVar[dict | None] = contextvars.ContextVar("viewer", default=None)


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
            SELECT u.id, u.username, u.display_name, u.role, u.must_change_password, coalesce(u.email, '')
            FROM app_session s
            JOIN app_user u ON u.id = s.user_id
            WHERE s.id = %s AND s.expires_at > now() AND u.active
            """,
            (session_id(token),),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "username": row[1],
        "name": row[2],
        "role": row[3],
        "must_change_password": bool(row[4]),
        "email": row[5],
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
<header class="page-head">
  <p class="eyebrow">CADASTROS</p>
  <h1>Início</h1>
  <p class="lede">Escolha o cadastro que deseja consultar. Os registros já gravados continuam disponíveis.</p>
</header>
{cards_for(CADASTROS + MODULES)}
<p class="eyebrow section-gap">RELATÓRIOS</p>
{cards_for(RELATORIOS)}
"""
    return page("Início", body, logged=True, current="/restrito/cadastros")


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



def tipos_page(*, rows, q: str, aviso: str, erro: str, editor: dict | None) -> bytes:
    if editor is None:
        editor = {"titulo": "Novo tipo de documento", "id": "", "nome": "", "descricao": "", "esfera": "", "errors": {}}
    creating = not editor.get("id")
    options = ['<option value="">Selecione</option>']
    for esfera in ESFERAS:
        selected = " selected" if editor.get("esfera") == esfera else ""
        options.append(f'<option value="{esc(esfera)}"{selected}>{esc(esfera)}</option>')
    errors = editor.get("errors") or {}
    cancel = f'<a class="text-link" href="{esc(tipos_url(q=q))}">Cancelar</a>' if not creating else ""
    form = f"""
<form class="record-form" method="post" action="{esc(TIPOS_PATH)}">
  <input type="hidden" name="acao" value="salvar">
  <input type="hidden" name="id" value="{esc(editor.get("id", ""))}">
  <input type="hidden" name="q" value="{esc(q)}">
  <label>Nome<input name="nome" maxlength="30" required value="{esc(editor.get("nome", ""))}" autofocus>{field_error(errors, "nome")}</label>
  <label>Descrição<input name="descricao" maxlength="180" required value="{esc(editor.get("descricao", ""))}">{field_error(errors, "descricao")}</label>
  <label>Esfera<select name="esfera" required>{"".join(options)}</select>{field_error(errors, "esfera")}</label>
  <button type="submit">{"Criar tipo de documento" if creating else "Salvar alterações"}</button>
  {cancel}
</form>
"""
    body_rows = []
    for item_id, nome, descricao, esfera in rows:
        selected = " is-selected" if str(item_id) == str(editor.get("id") or "") else ""
        body_rows.append(
            f'<tr class="{selected.strip()}">'
            f"<td><strong>{esc(nome)}</strong></td><td>{esc(descricao)}</td><td>{esc(esfera)}</td>"
            f"<td>{row_actions(TIPOS_PATH, item_id, q, 'Excluir este tipo de documento?')}</td></tr>"
        )
    table = (
        "<table class=\"grid\"><thead><tr><th>Nome</th><th>Descrição</th><th>Esfera</th><th>Ações</th></tr></thead><tbody>"
        + ("".join(body_rows) or '<tr><td colspan="4">Nenhum tipo de documento encontrado.</td></tr>')
        + "</tbody></table>"
    )
    body = screen(
        "CADASTROS", "Tipos de Documentos", "Tipos usados em títulos e lançamentos.",
        flashes_html(aviso, erro), editor["titulo"], form, "Tipos cadastrados", len(rows), table,
    )
    return page(
        "Tipos de Documentos", body, logged=True, current=TIPOS_PATH, wide=True,
        search={"action": TIPOS_PATH, "q": q, "placeholder": "Nome, descrição ou esfera"},
        action={"href": tipos_url(acao="incluir", q=q), "label": "+ Novo tipo"},
    )


def formas_page(*, rows, q: str, aviso: str, erro: str, editor: dict | None) -> bytes:
    if editor is None:
        editor = {"titulo": "Nova forma de pagamento", "id": "", "nome": "", "descricao": "", "errors": {}}
    creating = not editor.get("id")
    errors = editor.get("errors") or {}
    cancel = f'<a class="text-link" href="{esc(formas_url(q=q))}">Cancelar</a>' if not creating else ""
    form = f"""
<form class="record-form" method="post" action="{esc(FORMAS_PATH)}">
  <input type="hidden" name="acao" value="salvar">
  <input type="hidden" name="id" value="{esc(editor.get("id", ""))}">
  <input type="hidden" name="q" value="{esc(q)}">
  <label>Nome<input name="nome" maxlength="30" required value="{esc(editor.get("nome", ""))}" autofocus>{field_error(errors, "nome")}</label>
  <label>Descrição<input name="descricao" maxlength="80" required value="{esc(editor.get("descricao", ""))}">{field_error(errors, "descricao")}</label>
  <button type="submit">{"Criar forma de pagamento" if creating else "Salvar alterações"}</button>
  {cancel}
</form>
"""
    body_rows = []
    for item_id, nome, descricao in rows:
        selected = " is-selected" if str(item_id) == str(editor.get("id") or "") else ""
        body_rows.append(
            f'<tr class="{selected.strip()}">'
            f"<td><strong>{esc(nome)}</strong></td><td>{esc(descricao)}</td>"
            f"<td>{row_actions(FORMAS_PATH, item_id, q, 'Excluir esta forma de pagamento?')}</td></tr>"
        )
    table = (
        "<table class=\"grid\"><thead><tr><th>Nome</th><th>Descrição</th><th>Ações</th></tr></thead><tbody>"
        + ("".join(body_rows) or '<tr><td colspan="3">Nenhuma forma de pagamento encontrada.</td></tr>')
        + "</tbody></table>"
    )
    body = screen(
        "CADASTROS", "Formas de Pagamentos", "Meios usados para pagar e receber.",
        flashes_html(aviso, erro), editor["titulo"], form, "Formas cadastradas", len(rows), table,
    )
    return page(
        "Formas de Pagamentos", body, logged=True, current=FORMAS_PATH, wide=True,
        search={"action": FORMAS_PATH, "q": q, "placeholder": "Nome ou descrição"},
        action={"href": formas_url(acao="incluir", q=q), "label": "+ Nova forma"},
    )


def condicoes_page(*, rows, q: str, aviso: str, erro: str, editor: dict | None) -> bytes:
    if editor is None:
        editor = {"titulo": "Nova condição de pagamento", "id": "", "qtde_parcelas": "", "nome": "", "errors": {}}
    creating = not editor.get("id")
    errors = editor.get("errors") or {}
    cancel = f'<a class="text-link" href="{esc(condicoes_url(q=q))}">Cancelar</a>' if not creating else ""
    form = f"""
<form class="record-form" method="post" action="{esc(CONDICOES_PATH)}">
  <input type="hidden" name="acao" value="salvar">
  <input type="hidden" name="id" value="{esc(editor.get("id", ""))}">
  <input type="hidden" name="q" value="{esc(q)}">
  <label>Qtde parcelas<input name="qtde_parcelas" inputmode="numeric" maxlength="2" required value="{esc(editor.get("qtde_parcelas", ""))}" autofocus>{field_error(errors, "qtde_parcelas")}</label>
  <label>Nome<input name="nome" maxlength="40" required value="{esc(editor.get("nome", ""))}">{field_error(errors, "nome")}</label>
  <button type="submit">{"Criar condição" if creating else "Salvar alterações"}</button>
  {cancel}
</form>
"""
    body_rows = []
    for item_id, qtde, nome in rows:
        selected = " is-selected" if str(item_id) == str(editor.get("id") or "") else ""
        body_rows.append(
            f'<tr class="{selected.strip()}">'
            f"<td><strong>{esc(nome)}</strong></td><td>{qtde}</td>"
            f"<td>{row_actions(CONDICOES_PATH, item_id, q, 'Excluir esta condição de pagamento?')}</td></tr>"
        )
    table = (
        "<table class=\"grid\"><thead><tr><th>Nome</th><th>Parcelas</th><th>Ações</th></tr></thead><tbody>"
        + ("".join(body_rows) or '<tr><td colspan="3">Nenhuma condição de pagamento encontrada.</td></tr>')
        + "</tbody></table>"
    )
    body = screen(
        "CADASTROS", "Condições de Pagamentos", "Prazos e parcelas das negociações.",
        flashes_html(aviso, erro), editor["titulo"], form, "Condições cadastradas", len(rows), table,
    )
    return page(
        "Condições de Pagamentos", body, logged=True, current=CONDICOES_PATH, wide=True,
        search={"action": CONDICOES_PATH, "q": q, "placeholder": "Nome ou quantidade de parcelas"},
        action={"href": condicoes_url(acao="incluir", q=q), "label": "+ Nova condição"},
    )


def centros_page(*, rows, q: str, aviso: str, erro: str, editor: dict | None) -> bytes:
    if editor is None:
        editor = {"titulo": "Novo centro de custo", "id": "", "codigo_reduzido": "", "nome": "", "descricao": "", "errors": {}}
    creating = not editor.get("id")
    errors = editor.get("errors") or {}
    cancel = f'<a class="text-link" href="{esc(centros_url(q=q))}">Cancelar</a>' if not creating else ""
    ident = esc(editor.get("id") or "")
    form = f"""
<form class="record-form" method="post" action="{esc(CENTROS_PATH)}">
  <input type="hidden" name="acao" value="salvar">
  <input type="hidden" name="id" value="{ident}">
  <input type="hidden" name="q" value="{esc(q)}">
  <label>ID<input value="{ident}" readonly tabindex="-1" placeholder="Gerado ao salvar"></label>
  <label>Código reduzido<input name="codigo_reduzido" maxlength="20" required value="{esc(editor.get("codigo_reduzido", ""))}" autocomplete="off" autofocus>{field_error(errors, "codigo_reduzido")}</label>
  <label>Nome<input name="nome" maxlength="80" required value="{esc(editor.get("nome", ""))}">{field_error(errors, "nome")}</label>
  <label>Descrição<textarea name="descricao" maxlength="200" required>{esc(editor.get("descricao", ""))}</textarea>{field_error(errors, "descricao")}</label>
  <button type="submit">{"Criar centro de custo" if creating else "Salvar alterações"}</button>
  {cancel}
</form>
"""
    body_rows = []
    for item_id, codigo, nome, descricao in rows:
        selected = " is-selected" if str(item_id) == str(editor.get("id") or "") else ""
        body_rows.append(
            f'<tr class="{selected.strip()}">'
            f"<td><strong>{esc(codigo)}</strong></td><td>{esc(nome)}</td><td>{esc(descricao) if descricao else '—'}</td>"
            f"<td>{row_actions(CENTROS_PATH, item_id, q, 'Excluir este centro de custo?')}</td></tr>"
        )
    table = (
        "<table class=\"grid\"><thead><tr><th>Código</th><th>Nome</th><th>Descrição</th><th>Ações</th></tr></thead><tbody>"
        + ("".join(body_rows) or '<tr><td colspan="4">Nenhum centro de custo encontrado.</td></tr>')
        + "</tbody></table>"
    )
    body = screen(
        "CADASTROS", "Centros de Custo", "Centros usados para classificar gastos e receitas.",
        flashes_html(aviso, erro), editor["titulo"], form, "Centros cadastrados", len(rows), table,
    )
    return page(
        "Centros de Custo", body, logged=True, current=CENTROS_PATH, wide=True,
        search={"action": CENTROS_PATH, "q": q, "placeholder": "Código, nome ou descrição"},
        action={"href": centros_url(acao="incluir", q=q), "label": "+ Novo centro"},
    )


def usuarios_page(*, rows, q: str, aviso: str, erro: str, editor: dict | None, current_id: int) -> bytes:
    popup = ""
    shown = aviso
    if aviso == "Usuário incluído com sucesso.":
        shown = ""
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
    if editor is None:
        editor = {"modo": "incluir", "nome": "", "login": "", "email": "", "administrador": False, "errors": {}}
    creating = editor.get("modo") == "incluir"
    errors = editor.get("errors") or {}
    cancel = f'<a class="text-link" href="{esc(usuarios_url(q=q))}">Cancelar</a>' if not creating else ""
    if creating:
        checked = " checked" if editor.get("administrador") else ""
        form = f"""
<form class="record-form" method="post" action="{esc(USUARIOS_PATH)}">
  <input type="hidden" name="acao" value="salvar">
  <input type="hidden" name="q" value="{esc(q)}">
  <label>Nome<input name="nome" maxlength="80" required value="{esc(editor.get("nome", ""))}" autofocus>{field_error(errors, "nome")}</label>
  <label>Login<input name="login" maxlength="40" required value="{esc(editor.get("login", ""))}" autocomplete="off">{field_error(errors, "login")}</label>
  <label>E-mail<input name="email" type="email" maxlength="120" required value="{esc(editor.get("email", ""))}" autocomplete="off">{field_error(errors, "email")}</label>
  <label>Senha<input name="senha" type="password" required autocomplete="new-password">{field_error(errors, "senha")}</label>
  <label>Confirmar senha<input name="confirmar" type="password" required autocomplete="new-password">{field_error(errors, "confirmar")}</label>
  <label class="checkline"><span>Tornar administrador</span><input type="checkbox" name="administrador" value="1"{checked}></label>
  <p class="lede">No primeiro acesso, o usuário será obrigado a trocar a senha.</p>
  <button type="submit">Criar usuário</button>
</form>
"""
        form_title = "Novo usuário"
    else:
        form = f"""
<form class="record-form" method="post" action="{esc(USUARIOS_PATH)}">
  <input type="hidden" name="acao" value="salvar">
  <input type="hidden" name="id" value="{esc(editor.get("id", ""))}">
  <input type="hidden" name="q" value="{esc(q)}">
  <label>ID<input value="{esc(editor.get("id", ""))}" readonly tabindex="-1"></label>
  <label>Nome<input name="nome" maxlength="80" required value="{esc(editor.get("nome", ""))}" autofocus>{field_error(errors, "nome")}</label>
  <label>Login<input name="login" maxlength="40" required value="{esc(editor.get("login", ""))}" autocomplete="off">{field_error(errors, "login")}</label>
  <p class="lede">E-mail {esc(editor.get("email") or "—")}. O e-mail e a senha não mudam neste formulário.</p>
  <button type="submit">Salvar alterações</button>
  {cancel}
</form>
"""
        form_title = "Editar usuário"
    body_rows = []
    for item_id, nome, login, email, role, active, must_change in rows:
        admin = role in ("master", "admin")
        selected = " is-selected" if str(item_id) == str(editor.get("id") or "") else ""
        pill = '<span class="pill">Ativo</span>' if active else '<span class="pill off">Bloqueado</span>'
        extra = ""
        if item_id != current_id:
            block_label = "Bloquear" if active else "Desbloquear"
            extra += (
                f'<form method="post" action="{esc(USUARIOS_PATH)}" onsubmit="return confirm(\'{block_label} este usuário?\')">'
                '<input type="hidden" name="acao" value="bloquear">'
                f'<input type="hidden" name="id" value="{item_id}">'
                f'<input type="hidden" name="q" value="{esc(q)}">'
                f'<button type="submit">{block_label}</button></form>'
            )
        admin_label = "Remover administrador" if admin else "Tornar administrador"
        extra += (
            f'<form method="post" action="{esc(USUARIOS_PATH)}">'
            '<input type="hidden" name="acao" value="administrador">'
            f'<input type="hidden" name="id" value="{item_id}">'
            f'<input type="hidden" name="q" value="{esc(q)}">'
            f'<button type="submit">{admin_label}</button></form>'
            f'<form method="post" action="{esc(USUARIOS_PATH)}" onsubmit="return confirm(\'Pedir a troca de senha no próximo acesso?\')">'
            '<input type="hidden" name="acao" value="senha">'
            f'<input type="hidden" name="id" value="{item_id}">'
            f'<input type="hidden" name="q" value="{esc(q)}">'
            '<button type="submit">Trocar senha</button></form>'
        )
        delete = row_actions(USUARIOS_PATH, item_id, q, "Excluir este usuário?", extra) if item_id != current_id else (
            f'<div class="row-actions"><a href="{esc(usuarios_url(acao="editar", id=str(item_id), q=q))}">Editar</a>{extra}</div>'
        )
        body_rows.append(
            f'<tr class="{selected.strip()}">'
            f"<td><strong>{esc(nome)}</strong><br>{esc(login)}</td>"
            f"<td>{esc(email or '—')}</td><td>{pill}</td><td>{delete}</td></tr>"
        )
    table = (
        "<table class=\"grid\"><thead><tr><th>Nome</th><th>E-mail</th><th>Status</th><th>Ações</th></tr></thead><tbody>"
        + ("".join(body_rows) or '<tr><td colspan="4">Nenhum usuário encontrado.</td></tr>')
        + "</tbody></table>"
    )
    body = popup + screen(
        "CADASTROS", "Usuários", "Pessoas com acesso à área restrita.",
        flashes_html(shown, erro), form_title, form, "Usuários cadastrados", len(rows), table,
    )
    return page(
        "Usuários", body, logged=True, current=USUARIOS_PATH, wide=True,
        search={"action": USUARIOS_PATH, "q": q, "placeholder": "Nome, login ou e-mail"},
        action={"href": usuarios_url(acao="incluir", q=q), "label": "+ Novo usuário"},
    )


def password_form(error: str = "") -> bytes:
    message = f'<p class="error">{esc(error)}</p>' if error else ""
    body = f"""
<header class="page-head">
  <p class="eyebrow">CONTA</p>
  <h1>Trocar senha</h1>
  <p class="lede">Para continuar, informe a senha atual e escolha uma nova senha.</p>
</header>
<section class="panel">
  {message}
  <form class="record-form" method="post" action="{esc(PASSWORD_PATH)}">
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
<header class="page-head">
  <p class="eyebrow">APINIL</p>
  <h1>{esc(title)}</h1>
  <p class="lede">{esc(text)}</p>
</header>
<section class="panel"><p>A estrutura de acesso já está pronta. O lançamento dos registros entra na próxima etapa.</p></section>
"""
    return page(title, body, logged=True, current=current)


class Handler(BaseHTTPRequestHandler):
    timeout = REQUEST_TIMEOUT
    server_version = "Apinil"
    sys_version = ""

    def version_string(self) -> str:
        return "Apinil"

    def log_message(self, fmt: str, *args) -> None:
        print(fmt % args)

    def add_security_headers(self) -> None:
        for key, value in security_headers(request_host(self)):
            self.send_header(key, value)

    def send_html(self, status: int, payload: bytes, *, headers: list[tuple[str, str]] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.add_security_headers()
        for key, value in headers or []:
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, status: int, payload: dict) -> None:
        import json
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.add_security_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, location: str, *, cookie: str | None = None) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.add_security_headers()
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
            self.add_security_headers()
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
        VIEWER.set(user)
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
        if path in (
            "/restrito/cadastros/pessoas", "/restrito/cadastros/pessoas/consulta", "/restrito/cep",
            "/restrito/contas-a-pagar", "/restrito/contas-a-receber", "/restrito/titulos",
            "/restrito/lancamentos/arquivo", "/restrito/relatorios/titulos-em-aberto",
            "/restrito/relatorios/titulos-baixados",
        ):
            if not user:
                self.redirect("/restrito/login")
                return
            from financeiro import handle_financeiro_get
            handle_financeiro_get(self, path)
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
        self.send_header("Cache-Control", "public, max-age=300")
        self.add_security_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        path = urlparse(self.path).path.rstrip("/")
        from financeiro import read_request_form
        form, files, rejected = read_request_form(self.headers, self.rfile, MAX_FORM_BYTES, MAX_UPLOAD_BYTES)
        if rejected:
            self.send_html(413, page("Pedido grande", "<h1>Pedido grande demais</h1>"))
            return
        self.uploads = files
        ensure_schema()
        if path == "/restrito/login":
            self.handle_login(form.get("username", ""), form.get("password", ""))
            return
        if path == "/restrito/sair":
            self.handle_logout()
            return
        if path == PASSWORD_PATH:
            user = current_user(self)
            VIEWER.set(user)
            if not user:
                self.redirect("/restrito/login")
                return
            self.handle_password_post(user, form)
            return
        user = current_user(self)
        VIEWER.set(user)
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
        if path in ("/restrito/cadastros/pessoas", "/restrito/contas-a-pagar", "/restrito/contas-a-receber", "/restrito/titulos"):
            if not user:
                self.redirect("/restrito/login")
                return
            from financeiro import handle_financeiro_post
            handle_financeiro_post(self, path, form)
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
        bucket = f"password:{user['id']}"
        with connect() as conn:
            if throttle_blocked(conn, [(bucket, PASSWORD_LIMIT)]):
                self.send_html(429, password_form("Muitas tentativas. Espere alguns minutos e tente de novo."))
                return
            row = conn.execute(
                "SELECT password_hash FROM app_user WHERE id = %s AND active",
                (user["id"],),
            ).fetchone()
            if not row or not verify_password(atual, row[0]):
                throttle_record(conn, [bucket])
                conn.commit()
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
            conn.execute("DELETE FROM app_session WHERE user_id = %s", (user["id"],))
            throttle_clear(conn, [bucket])
            token = secrets.token_urlsafe(32)
            expires = datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)
            conn.execute(
                "INSERT INTO app_session (id, user_id, expires_at) VALUES (%s, %s, %s)",
                (session_id(token), user["id"], expires),
            )
            conn.commit()
        self.redirect(
            "/restrito/cadastros",
            cookie=session_cookie(token, secure=not request_is_local(self)),
        )

    def handle_login(self, username: str, password: str) -> None:
        username = username.strip()[:80]
        generic = "Usuário ou senha inválidos."
        limited = "Muitas tentativas. Espere alguns minutos e tente de novo."
        if not database_url():
            self.send_html(503, login_form("O banco ainda não está configurado."))
            return
        buckets = login_buckets(client_ip(self), username)
        with connect() as conn:
            if throttle_blocked(conn, buckets):
                self.send_html(429, login_form(limited))
                return
            row = conn.execute(
                """
                SELECT id, password_hash, active, must_change_password
                FROM app_user
                WHERE lower(username) = lower(%s)
                """,
                (username,),
            ).fetchone()
            stored = row[1] if row else dummy_password_hash()
            password_ok = verify_password(password, stored)
            if not row or not password_ok:
                throttle_record(conn, [bucket for bucket, _limit in buckets])
                conn.commit()
                self.send_html(401, login_form(generic))
                return
            if not row[2]:
                throttle_record(conn, [bucket for bucket, _limit in buckets])
                conn.commit()
                self.send_html(403, login_form("Usuário bloqueado. Procure o administrador."))
                return
            must_change = bool(row[3])
            token = secrets.token_urlsafe(32)
            expires = datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)
            conn.execute("DELETE FROM app_session WHERE expires_at < now()")
            conn.execute(
                "INSERT INTO app_session (id, user_id, expires_at) VALUES (%s, %s, %s)",
                (session_id(token), row[0], expires),
            )
            throttle_clear(conn, [bucket for bucket, _limit in buckets[:2]])
            conn.commit()
        self.redirect(
            PASSWORD_PATH if must_change else "/restrito/cadastros",
            cookie=session_cookie(token, secure=not request_is_local(self)),
        )

    def handle_logout(self) -> None:
        token = ""
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == SESSION_COOKIE:
                token = value
        if token and database_url():
            with connect() as conn:
                conn.execute("DELETE FROM app_session WHERE id = %s", (session_id(token),))
                conn.commit()
        self.redirect(
            "/restrito/login",
            cookie=session_cookie("", secure=not request_is_local(self), clear=True),
        )


_schema_ready = False


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready or not database_url():
        return
    migrate_and_seed()
    _schema_ready = True


class BoundedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_timeout = REQUEST_TIMEOUT

    def __init__(self, server_address, handler):
        super().__init__(server_address, handler)
        self._slots = threading.BoundedSemaphore(MAX_WORKERS)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.settimeout(2)
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Connection: close\r\n"
                    b"Retry-After: 1\r\n"
                    b"Content-Length: 0\r\n\r\n"
                )
            except OSError:
                pass
            request.close()
            return
        try:
            request.settimeout(self.request_timeout)
        except OSError:
            self._slots.release()
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def main() -> None:
    ensure_schema()
    bind = os.environ.get("BIND", "127.0.0.1")
    port = int(os.environ.get("PORT", "8090"))
    server = BoundedHTTPServer((bind, port), Handler)
    print(f"área restrita em http://{bind}:{port}/restrito/")
    server.serve_forever()


if __name__ == "__main__":
    main()
