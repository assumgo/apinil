#!/usr/bin/env python3
"""Área restrita da Apinil: login, sessão e páginas internas."""

from __future__ import annotations

import hashlib
import hmac
import html
import os
import secrets
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import psycopg

SESSION_COOKIE = "apinil_session"
SESSION_HOURS = 12
PBKDF2_ROUNDS = 600_000

CADASTROS = (
    ("/restrito/cadastros/pessoas", "Pessoas", "Clientes, fornecedores e demais pessoas da fábrica."),
    ("/restrito/cadastros/tipos-de-documentos", "Tipos de Documentos", "Tipos usados em títulos e lançamentos."),
    ("/restrito/cadastros/condicoes-de-pagamentos", "Condições de Pagamentos", "Prazos e parcelas das negociações."),
    ("/restrito/cadastros/formas-de-pagamentos", "Formas de Pagamentos", "Meios usados para pagar e receber."),
    ("/restrito/cadastros/usuarios", "Usuários", "Pessoas com acesso à área restrita."),
)

MODULES = (
    ("/restrito/contas-a-pagar", "Contas a pagar", "Compromissos e pagamentos da indústria."),
    ("/restrito/contas-a-receber", "Contas a receber", "Vendas e recebimentos."),
)


def database_url() -> str:
    return os.environ.get("DATABASE_URL", "").strip()


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
        conn.commit()


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
    links = [nav_link("/restrito/", "Painel", current)]
    links.append(nav_link("/restrito/cadastros", "Cadastros", current))
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


def page(title: str, body: str, *, logged: bool = False, current: str = "") -> bytes:
    nav = side_nav(current) if logged else ""
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
      <a class="restrito-link" href="/restrito/" aria-label="Área restrita" title="Área restrita">{LOCK}</a>
    </div>
  </header>
  <div class="restrito-shell">{nav}<main class="restrito-main">{body}</main></div>
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
    <label>Usuário<input name="username" autocomplete="username" required autofocus></label>
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
            SELECT u.id, u.username, u.display_name, u.role
            FROM app_session s
            JOIN app_user u ON u.id = s.user_id
            WHERE s.id = %s AND s.expires_at > now() AND u.active
            """,
            (token,),
        ).fetchone()
    if not row:
        return None
    return {"id": row[0], "username": row[1], "name": row[2], "role": row[3]}


def cards_for(items) -> str:
    cards = []
    for href, label, text in items:
        cards.append(
            f'<a class="module-card" href="{esc(href)}"><h2>{esc(label)}</h2><p>{esc(text)}</p></a>'
        )
    return f'<div class="module-grid">{"".join(cards)}</div>'


def dashboard(user) -> bytes:
    items = (("/restrito/cadastros", "Cadastros", "Pessoas, documentos, pagamentos e usuários."),) + MODULES
    body = f"""
<h1>Painel</h1>
<p class="prose" style="text-align:left;margin-left:0">Olá, {esc(user["name"])}. Escolha um módulo.</p>
{cards_for(items)}
"""
    return page("Painel", body, logged=True, current="/restrito")


def cadastros_page() -> bytes:
    body = f"""
<h1>Cadastros</h1>
<p>Escolha o cadastro que deseja consultar.</p>
{cards_for(CADASTROS)}
"""
    return page("Cadastros", body, logged=True, current="/restrito/cadastros")


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
        if not database_url():
            self.send_html(503, page("Indisponível", "<h1>Área restrita indisponível</h1><p>O banco ainda não está configurado.</p>"))
            return
        if path in ("/restrito/login",):
            user = current_user(self)
            if user:
                self.redirect("/restrito/")
                return
            self.send_html(200, login_form())
            return
        user = current_user(self)
        if path == "/restrito":
            if not user:
                self.redirect("/restrito/login")
                return
            self.send_html(200, dashboard(user))
            return
        if path == "/restrito/cadastros":
            if not user:
                self.redirect("/restrito/login")
                return
            self.send_html(200, cadastros_page())
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

    def do_POST(self) -> None:
        path = urlparse(self.path).path.rstrip("/")
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace")
        form = {key: values[0] for key, values in parse_qs(raw).items()}
        if path == "/restrito/login":
            self.handle_login(form.get("username", ""), form.get("password", ""))
            return
        if path == "/restrito/sair":
            self.handle_logout()
            return
        self.send_html(404, page("Não encontrado", "<h1>Página não encontrada</h1>"))

    def handle_login(self, username: str, password: str) -> None:
        username = username.strip()
        generic = "Usuário ou senha inválidos."
        if not database_url():
            self.send_html(503, login_form("O banco ainda não está configurado."))
            return
        with connect() as conn:
            row = conn.execute(
                """
                SELECT id, password_hash, active
                FROM app_user
                WHERE username = %s
                """,
                (username,),
            ).fetchone()
            if not row or not row[2] or not verify_password(password, row[1]):
                self.send_html(401, login_form(generic))
                return
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
        self.redirect("/restrito/", cookie=cookie)

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


def main() -> None:
    migrate_and_seed()
    bind = os.environ.get("BIND", "127.0.0.1")
    port = int(os.environ.get("PORT", "8090"))
    server = ThreadingHTTPServer((bind, port), Handler)
    print(f"área restrita em http://{bind}:{port}/restrito/")
    server.serve_forever()


if __name__ == "__main__":
    main()
