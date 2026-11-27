"""Tela de início, backups e histórico de ações."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse
from zoneinfo import ZoneInfo

ZONA = ZoneInfo("America/Sao_Paulo")
NOME_RE = re.compile(r"^apinil-\d{8}-\d{6}\.dump$")
ACOES = ("inclusao", "alteracao", "visualizacao", "exclusao")
ROTULOS_ACAO = {
    "inclusao": "Inclusão",
    "alteracao": "Alteração",
    "visualizacao": "Visualização",
    "exclusao": "Exclusão",
}
SUCESSO = {
    "incluido", "alterado", "excluido", "baixado", "rascunho", "lido",
    "bloqueado", "desbloqueado", "admin", "senha", "criado", "restaurado",
}
VISTAS_IGNORADAS = {
    "/restrito/login",
    "/restrito/app.css",
    "/restrito/cadastros/pessoas/consulta",
    "/restrito/cep",
}
AVISOS = {
    "criado": "Backup criado.",
    "restaurado": "Backup restaurado. Se a sessão cair, entre de novo.",
    "falha": "Não foi possível concluir a operação.",
    "confirmacao": "Digite RESTAURAR para confirmar a restauração.",
    "arquivo": "Backup não encontrado.",
    "ocupado": "Já existe um backup em andamento.",
    "ferramenta": "O servidor não tem o pg_dump do PostgreSQL 18.",
}
_backup_lock = threading.Lock()
_backup_agendado = False


def ui():
    import app
    return app


class BackupErro(Exception):
    def __init__(self, codigo: str):
        super().__init__(codigo)
        self.codigo = codigo if codigo in AVISOS else "falha"


def agora() -> datetime:
    return datetime.now(ZONA)


def versao_app() -> str:
    aqui = Path(__file__).resolve()
    for path in (aqui.parent / "VERSION", aqui.parent.parent / "VERSION"):
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()[:40]
    return os.environ.get("APP_VERSION", "").strip()[:40]


def reais(valor) -> str:
    from financeiro import format_valor
    numero = Decimal(str(valor or 0)).quantize(Decimal("0.01"))
    sinal = "-" if numero < 0 else ""
    return sinal + "R$ " + format_valor(abs(numero))


def texto_titulos(qtd: int) -> str:
    return "1 título" if int(qtd) == 1 else f"{int(qtd)} títulos"


def formatar_quando(valor) -> str:
    if not isinstance(valor, datetime):
        return ""
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=timezone.utc)
    return valor.astimezone(ZONA).strftime("%d/%m/%Y, %H:%M")


def periodos(dia: date) -> dict:
    inicio_semana = dia - timedelta(days=dia.weekday())
    if dia.month == 12:
        proximo_mes = date(dia.year + 1, 1, 1)
    else:
        proximo_mes = date(dia.year, dia.month + 1, 1)
    return {
        "dia": dia,
        "semana": (inicio_semana, inicio_semana + timedelta(days=6)),
        "mes": (dia.replace(day=1), proximo_mes - timedelta(days=1)),
    }


def papel(user: dict | None) -> str:
    if (user or {}).get("role") in ("master", "admin"):
        return "Administrador"
    return "Usuário"


def nome_usuario(user: dict | None) -> str:
    dados = user or {}
    return (dados.get("name") or dados.get("username") or "Sistema").strip()[:120]


def nome_recurso(path: str) -> str:
    app = ui()
    mapa = {
        "/restrito/cadastros": "Início",
        "/restrito/backups": "Backups",
        "/restrito/backups/arquivo": "Backups",
        "/restrito/trocar-senha": "Senha",
        "/restrito/lancamentos/arquivo": "Arquivo do lançamento",
        "/restrito/login": "Sessão",
        "/restrito/sair": "Sessão",
    }
    for href, label, _texto in app.CADASTROS + app.MODULES + app.RELATORIOS:
        mapa[href.rstrip("/")] = label
    return mapa.get(path, "Página")


def _decimal(valor) -> Decimal:
    if isinstance(valor, Decimal):
        return valor
    return Decimal(str(valor or 0))


def _faixa(conn, natureza: str, status: str, coluna: str, janela: dict) -> dict:
    if coluna not in ("vencimento", "data_baixa"):
        raise ValueError("coluna")
    dia = janela["dia"]
    semana = janela["semana"]
    mes = janela["mes"]
    row = conn.execute(
        f"""
        SELECT
            COALESCE(SUM(valor) FILTER (WHERE {coluna} = %s), 0),
            COUNT(*) FILTER (WHERE {coluna} = %s),
            COALESCE(SUM(valor) FILTER (WHERE {coluna} BETWEEN %s AND %s), 0),
            COALESCE(SUM(valor) FILTER (WHERE {coluna} BETWEEN %s AND %s), 0)
        FROM titulo
        WHERE natureza = %s AND status = %s
        """,
        (dia, dia, semana[0], semana[1], mes[0], mes[1], natureza, status),
    ).fetchone()
    return {
        "dia": _decimal(row[0]),
        "quantidade": int(row[1] or 0),
        "semana": _decimal(row[2]),
        "mes": _decimal(row[3]),
    }


def _pendentes(conn, natureza: str):
    return conn.execute(
        """
        SELECT t.id, COALESCE(p.nome, ''), t.numero_documento, t.numero_parcela,
               COUNT(*) OVER (PARTITION BY t.lancamento_id), t.valor, t.vencimento
        FROM titulo t
        LEFT JOIN pessoa p ON p.id = t.pessoa_id
        WHERE t.natureza = %s AND t.status = 'aberto'
        ORDER BY t.vencimento, t.id
        LIMIT 10
        """,
        (natureza,),
    ).fetchall()


def carregar_inicio(conn, dia: date | None = None) -> dict:
    janela = periodos(dia or agora().date())
    vencidos = conn.execute(
        """
        SELECT
            COALESCE(SUM(valor) FILTER (WHERE natureza = 'receber'), 0),
            COUNT(*) FILTER (WHERE natureza = 'receber'),
            COALESCE(SUM(valor) FILTER (WHERE natureza = 'pagar'), 0),
            COUNT(*) FILTER (WHERE natureza = 'pagar')
        FROM titulo
        WHERE status = 'aberto' AND vencimento < %s
        """,
        (janela["dia"],),
    ).fetchone()
    saldo = conn.execute(
        """
        SELECT
            COALESCE(SUM(valor) FILTER (WHERE natureza = 'receber'), 0)
            - COALESCE(SUM(valor) FILTER (WHERE natureza = 'pagar'), 0)
        FROM titulo
        WHERE vencimento BETWEEN %s AND %s
        """,
        (janela["mes"][0], janela["mes"][1]),
    ).fetchone()
    historico = conn.execute(
        """
        SELECT created_at, usuario, acao, recurso, descricao
        FROM auditoria
        WHERE acao <> 'visualizacao'
        ORDER BY created_at DESC, id DESC
        LIMIT 10
        """
    ).fetchall()
    return {
        "pagar_aberto": _faixa(conn, "pagar", "aberto", "vencimento", janela),
        "receber_aberto": _faixa(conn, "receber", "aberto", "vencimento", janela),
        "pagar_baixado": _faixa(conn, "pagar", "baixado", "data_baixa", janela),
        "receber_baixado": _faixa(conn, "receber", "baixado", "data_baixa", janela),
        "vencidos_receber": _decimal(vencidos[0]),
        "vencidos_receber_qtd": int(vencidos[1] or 0),
        "vencidos_pagar": _decimal(vencidos[2]),
        "vencidos_pagar_qtd": int(vencidos[3] or 0),
        "saldo": _decimal(saldo[0]),
        "pagar_pendentes": _pendentes(conn, "pagar"),
        "receber_pendentes": _pendentes(conn, "receber"),
        "historico": historico,
    }


def _metrica(rotulo: str, detalhe: str, faixa: dict, classe: str) -> str:
    app = ui()
    return f"""
<article class="metric {classe}">
  <div class="metric-label"><span>{app.esc(rotulo)}</span><em>{app.esc(detalhe)}</em></div>
  <div class="metric-body">
    <div class="metric-main">
      <strong>{app.esc(reais(faixa["dia"]))}</strong>
      <span>{app.esc(texto_titulos(faixa["quantidade"]))}</span>
    </div>
    <div class="metric-side">
      <div>Semana<b>{app.esc(reais(faixa["semana"]))}</b></div>
      <div>Mês<b>{app.esc(reais(faixa["mes"]))}</b></div>
    </div>
  </div>
</article>
"""


def _tabela_pendente(linhas, vazio: str) -> str:
    from financeiro import format_data
    app = ui()
    if not linhas:
        return f'<p class="home-empty">{app.esc(vazio)}</p>'
    corpo = []
    for _item_id, nome, numero, parcela, total, valor, vencimento in linhas:
        corpo.append(
            "<tr>"
            f"<td>{app.esc(format_data(vencimento))}</td>"
            f"<td>{app.esc(nome or '—')}</td>"
            f"<td>{app.esc(numero or '—')}<br><span class='hint'>{parcela}/{total}</span></td>"
            f"<td class='num'>{app.esc(reais(valor))}</td>"
            "</tr>"
        )
    return (
        "<div class='grid-wrap'><table class='grid'><thead><tr>"
        "<th>Vencimento</th><th>Pessoa</th><th>Documento</th><th class='num'>Valor</th>"
        "</tr></thead><tbody>"
        + "".join(corpo)
        + "</tbody></table></div>"
    )


def _link_titulos(natureza: str) -> str:
    return "/restrito/titulos?" + urlencode({
        "natureza": natureza,
        "situacao": "aberto",
        "pesquisar": "1",
    })


def _historico_html(linhas) -> str:
    app = ui()
    if not linhas:
        return '<p class="home-empty">Nenhuma alteração registrada.</p>'
    blocos = []
    for criado, usuario, _acao, _recurso, descricao in linhas:
        blocos.append(
            '<div class="hist-row">'
            f"<span>{app.esc(formatar_quando(criado))}</span>"
            f"<p><strong>{app.esc(usuario or 'Sistema')}</strong> · {app.esc(descricao or '')}</p>"
            "</div>"
        )
    return "".join(blocos)


def _backup_html(itens, admin: bool) -> str:
    app = ui()
    if not itens:
        lista = '<p class="home-empty">Nenhum backup ainda.</p>'
    else:
        primeiro, *resto = itens
        lista = (
            '<div class="backup-latest">'
            f"<strong>✓ Último backup: {app.esc(formatar_quando(primeiro['quando']))}</strong>"
            f"<span>{app.esc(primeiro['rotulo'])}</span>"
            "</div>"
        )
        linhas = []
        for item in resto:
            linhas.append(
                '<div class="backup-row">'
                f"<span>{app.esc(formatar_quando(item['quando']))}</span>"
                f"<b>{app.esc(item['rotulo'])}</b>"
                "</div>"
            )
        lista += "".join(linhas)
    botao = ""
    if admin:
        botao = (
            '<form class="backup-now" method="post" action="/restrito/backups">'
            '<input type="hidden" name="acao" value="criar">'
            '<input type="hidden" name="retorno" value="inicio">'
            '<button type="submit">Fazer backup agora</button>'
            "</form>"
        )
    return lista + botao


def render_inicio(user: dict, dados: dict, params: dict | None = None) -> bytes:
    app = ui()
    params = params or {}
    admin = app.is_admin(user)
    saldo = dados["saldo"]
    classe_saldo = "saldo-positivo" if saldo >= 0 else "saldo-negativo"
    aviso = AVISOS.get(params.get("aviso", ""), "")
    erro = AVISOS.get(params.get("erro", ""), "")
    corpo = f"""
<header class="home-head">
  <h1>Início</h1>
  <div class="home-user">
    <strong>{app.esc(nome_usuario(user))}</strong>
    <span>{app.esc(papel(user))}</span>
  </div>
</header>
{app.flashes_html(aviso, erro)}
<section class="home-metrics">
  {_metrica("A pagar hoje", "Em aberto", dados["pagar_aberto"], "pagar")}
  {_metrica("A receber hoje", "Em aberto", dados["receber_aberto"], "receber")}
  {_metrica("Pago hoje", "Baixado", dados["pagar_baixado"], "pagar")}
  {_metrica("Recebido hoje", "Baixado", dados["receber_baixado"], "receber")}
</section>
<section class="home-split">
  <article class="metric">
    <div class="metric-label"><span>Vencidos</span><em>Em aberto</em></div>
    <div class="vencidos-cols">
      <div>
        <span>A receber</span>
        <strong class="valor-vencido">{app.esc(reais(dados["vencidos_receber"]))}</strong>
        <span>{app.esc(texto_titulos(dados["vencidos_receber_qtd"]))}</span>
      </div>
      <div>
        <span>A pagar</span>
        <strong class="valor-vencido">{app.esc(reais(dados["vencidos_pagar"]))}</strong>
        <span>{app.esc(texto_titulos(dados["vencidos_pagar_qtd"]))}</span>
      </div>
    </div>
  </article>
  <article class="metric">
    <div class="metric-label"><span>Saldo previsto</span><em>Mês corrente</em></div>
    <div class="metric-main">
      <strong class="{classe_saldo}">{app.esc(reais(saldo))}</strong>
      <span>A receber menos a pagar, títulos que vencem neste mês, em aberto ou baixados.</span>
    </div>
  </article>
</section>
<section class="home-split">
  <section class="home-card">
    <div class="panel-head">
      <h2>Contas a pagar pendentes</h2>
      <a class="more" href="{app.esc(_link_titulos("pagar"))}">Ver todas</a>
    </div>
    {_tabela_pendente(dados["pagar_pendentes"], "Nenhuma conta a pagar pendente.")}
  </section>
  <section class="home-card">
    <div class="panel-head">
      <h2>Contas a receber pendentes</h2>
      <a class="more" href="{app.esc(_link_titulos("receber"))}">Ver todas</a>
    </div>
    {_tabela_pendente(dados["receber_pendentes"], "Nenhuma conta a receber pendente.")}
  </section>
</section>
<section class="home-split">
  <section class="home-card">
    <div class="panel-head">
      <h2>Últimas alterações</h2>
      <a class="more" href="/restrito/relatorios/historico">Histórico completo</a>
    </div>
    {_historico_html(dados["historico"])}
  </section>
  <section class="home-card">
    <div class="panel-head">
      <h2>Backup</h2>
      <a class="more" href="/restrito/backups">Abrir</a>
    </div>
    {_backup_html(dados["backups"], admin)}
  </section>
</section>
"""
    return app.page("Início", corpo, logged=True, current="/restrito/cadastros")


def inicio_page(user: dict, params: dict | None = None) -> bytes:
    app = ui()
    with app.connect() as conn:
        dados = carregar_inicio(conn)
    dados["backups"] = listar_backups()[:5]
    return render_inicio(user, dados, params)


def registrar_direto(usuario, acao: str, recurso: str, registro_id: str, descricao: str, ip: str) -> None:
    if acao not in ACOES:
        return
    app = ui()
    if not app.database_url():
        return
    try:
        with app.connect() as conn:
            conn.execute(
                """
                INSERT INTO auditoria (user_id, usuario, acao, recurso, registro_id, descricao, ip)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    (usuario or {}).get("id"),
                    nome_usuario(usuario),
                    acao,
                    (recurso or "Página")[:80],
                    str(registro_id or "")[:80],
                    (descricao or "")[:300],
                    (ip or "")[:80],
                ),
            )
            conn.commit()
    except Exception:
        return


def partes_location(location: str) -> tuple[str, str, str]:
    consulta = parse_qs(urlparse(location or "").query)
    def primeiro(chave: str) -> str:
        return ((consulta.get(chave) or [""])[0] or "")[:80]
    return primeiro("aviso"), primeiro("erro"), primeiro("nome")


def evento_da_requisicao(pedido: dict, status: int, location: str) -> dict | None:
    metodo = pedido.get("metodo") or ""
    path = (pedido.get("path") or "").rstrip("/") or "/"
    if metodo == "GET":
        if status != 200 or not path.startswith("/restrito") or path in VISTAS_IGNORADAS:
            return None
        recurso = nome_recurso(path)
        registro = str(pedido.get("registro") or "")
        registro = registro if registro.isdigit() else ""
        consulta = pedido.get("consulta") or ""
        if consulta == "editar" and registro:
            descricao = f"Abriu {recurso} para edição"
        elif consulta == "incluir":
            descricao = f"Abriu inclusão de {recurso}"
            registro = ""
        else:
            descricao = f"Abriu {recurso}"
        return {"acao": "visualizacao", "recurso": recurso, "registro_id": registro, "descricao": descricao}
    if status != 303:
        return None
    aviso, erro, nome = partes_location(location)
    if erro and aviso not in SUCESSO:
        return None
    acao_form = pedido.get("acao") or ""
    registro = str(pedido.get("registro") or "")
    if not registro.isdigit():
        registro = ""
    recurso = nome_recurso(path)
    seguro = nome if NOME_RE.fullmatch(nome or "") else ""
    if path == "/restrito/login":
        return {"acao": "inclusao", "recurso": "Sessão", "registro_id": "", "descricao": "Entrou no sistema"}
    if path == "/restrito/sair":
        return {"acao": "exclusao", "recurso": "Sessão", "registro_id": "", "descricao": "Saiu do sistema"}
    if path == "/restrito/trocar-senha":
        return {"acao": "alteracao", "recurso": "Senha", "registro_id": "", "descricao": "Alterou a própria senha"}
    if path == "/restrito/backups" and acao_form == "criar" and aviso == "criado":
        texto = "Backup manual concluído" + (f": {seguro}" if seguro else "")
        return {"acao": "inclusao", "recurso": "Backups", "registro_id": seguro, "descricao": texto}
    if path == "/restrito/backups" and acao_form == "restaurar" and aviso == "restaurado":
        texto = "Restaurou o backup" + (f" {seguro}" if seguro else "")
        return {"acao": "alteracao", "recurso": "Backups", "registro_id": seguro, "descricao": texto}
    if acao_form == "gerar" and aviso == "incluido":
        return {"acao": "inclusao", "recurso": recurso, "registro_id": registro, "descricao": "Gerou títulos"}
    if acao_form == "gerar" and aviso == "alterado":
        return {"acao": "alteracao", "recurso": recurso, "registro_id": registro, "descricao": "Gerou títulos de novo"}
    if aviso == "excluido":
        return {"acao": "exclusao", "recurso": recurso, "registro_id": registro, "descricao": f"Excluiu {recurso}"}
    if aviso == "incluido":
        return {"acao": "inclusao", "recurso": recurso, "registro_id": registro, "descricao": f"Incluiu {recurso}"}
    if aviso == "baixado":
        return {"acao": "alteracao", "recurso": "Títulos", "registro_id": "", "descricao": "Baixou títulos"}
    if aviso == "lido":
        return {
            "acao": "alteracao" if registro else "inclusao",
            "recurso": recurso,
            "registro_id": registro,
            "descricao": "Leu arquivo e gravou a entrada",
        }
    if aviso == "rascunho":
        return {
            "acao": "alteracao" if registro else "inclusao",
            "recurso": recurso,
            "registro_id": registro,
            "descricao": "Salvou rascunho",
        }
    if aviso == "alterado":
        return {"acao": "alteracao", "recurso": recurso, "registro_id": registro, "descricao": f"Alterou {recurso}"}
    if aviso == "bloqueado":
        return {"acao": "alteracao", "recurso": "Usuários", "registro_id": registro, "descricao": "Bloqueou usuário"}
    if aviso == "desbloqueado":
        return {"acao": "alteracao", "recurso": "Usuários", "registro_id": registro, "descricao": "Desbloqueou usuário"}
    if aviso == "admin":
        return {"acao": "alteracao", "recurso": "Usuários", "registro_id": registro, "descricao": "Alterou o perfil do usuário"}
    if aviso == "senha" or (acao_form == "senha" and not erro):
        return {"acao": "alteracao", "recurso": "Usuários", "registro_id": registro, "descricao": "Pediu troca de senha"}
    return None


def registrar_requisicao(usuario, pedido: dict, status: int, location: str, ip: str) -> bool:
    evento = evento_da_requisicao(pedido, status, location)
    if not evento:
        return False
    registrar_direto(
        usuario, evento["acao"], evento["recurso"], evento["registro_id"], evento["descricao"], ip,
    )
    return True


def _data_filtro(texto: str) -> date | None:
    from financeiro import parse_data
    return parse_data(texto) if texto else None


def listar_historico(conn, filtros: dict):
    clausulas = []
    parametros = []
    inicio = _data_filtro(filtros.get("de", ""))
    fim = _data_filtro(filtros.get("ate", ""))
    if inicio:
        clausulas.append("created_at >= %s")
        parametros.append(datetime(inicio.year, inicio.month, inicio.day, tzinfo=ZONA))
    if fim:
        clausulas.append("created_at < %s")
        parametros.append(datetime(fim.year, fim.month, fim.day, tzinfo=ZONA) + timedelta(days=1))
    if filtros.get("acao") in ACOES:
        clausulas.append("acao = %s")
        parametros.append(filtros["acao"])
    if filtros.get("usuario"):
        clausulas.append("usuario ILIKE %s")
        parametros.append("%" + filtros["usuario"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if filtros.get("recurso"):
        clausulas.append("recurso ILIKE %s")
        parametros.append("%" + filtros["recurso"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    where = (" WHERE " + " AND ".join(clausulas)) if clausulas else ""
    return conn.execute(
        f"""
        SELECT created_at, usuario, acao, recurso, registro_id, descricao, ip
        FROM auditoria
        {where}
        ORDER BY created_at DESC, id DESC
        LIMIT 200
        """,
        parametros,
    ).fetchall()


def historico_page(linhas, filtros: dict, erros: dict) -> bytes:
    app = ui()
    opcoes = ['<option value="">Todas</option>']
    for acao in ACOES:
        marca = " selected" if filtros.get("acao") == acao else ""
        opcoes.append(f'<option value="{acao}"{marca}>{app.esc(ROTULOS_ACAO[acao])}</option>')
    corpo_linhas = []
    for criado, usuario, acao, recurso, registro, descricao, ip in linhas:
        corpo_linhas.append(
            "<tr>"
            f"<td>{app.esc(formatar_quando(criado))}</td>"
            f"<td>{app.esc(usuario or '')}</td>"
            f"<td>{app.esc(ROTULOS_ACAO.get(acao, acao))}</td>"
            f"<td>{app.esc(recurso or '')}</td>"
            f"<td>{app.esc(descricao or '')}</td>"
            f"<td>{app.esc(ip or '')}</td>"
            "</tr>"
        )
    vazio = "<tr><td colspan='6'>Nenhum registro encontrado.</td></tr>"
    tabela = (
        "<div class='grid-wrap'><table class='grid'><thead><tr>"
        "<th>Quando</th><th>Usuário</th><th>Ação</th><th>Recurso</th><th>Descrição</th><th>IP</th>"
        "</tr></thead><tbody>"
        + ("".join(corpo_linhas) or vazio)
        + "</tbody></table></div>"
    )
    corpo = f"""
<header class="page-head">
  <p class="eyebrow">RELATÓRIOS</p>
  <h1>Histórico</h1>
  <p class="lede">Inclusões, alterações, visualizações e exclusões feitas no sistema.</p>
</header>
<section class="panel">
  <form class="history-filters" method="get" action="/restrito/relatorios/historico">
    <label>De<input name="de" placeholder="dd/mm/aaaa" value="{app.esc(filtros.get("de", ""))}">{app.field_error(erros, "de")}</label>
    <label>Até<input name="ate" placeholder="dd/mm/aaaa" value="{app.esc(filtros.get("ate", ""))}">{app.field_error(erros, "ate")}</label>
    <label>Ação<select name="acao">{"".join(opcoes)}</select></label>
    <label>Usuário<input name="usuario" value="{app.esc(filtros.get("usuario", ""))}"></label>
    <label>Recurso<input name="recurso" value="{app.esc(filtros.get("recurso", ""))}"></label>
    <div class="report-action"><button type="submit">Filtrar</button></div>
  </form>
  <div class="panel-head"><h2>Registros</h2><span>{app.esc(app.count_label(len(linhas)))}</span></div>
  {tabela}
</section>
"""
    return app.page("Histórico", corpo, logged=True, current="/restrito/relatorios/historico")


def filtros_do_historico(source: dict) -> tuple[dict, dict]:
    from financeiro import parse_data
    filtros = {
        "de": (source.get("de") or "").strip()[:10],
        "ate": (source.get("ate") or "").strip()[:10],
        "acao": (source.get("acao") or "").strip(),
        "usuario": (source.get("usuario") or "").strip()[:80],
        "recurso": (source.get("recurso") or "").strip()[:80],
    }
    erros = {}
    inicio = parse_data(filtros["de"]) if filtros["de"] else None
    fim = parse_data(filtros["ate"]) if filtros["ate"] else None
    if filtros["de"] and inicio is None:
        erros["de"] = "Use dd/mm/aaaa."
    if filtros["ate"] and fim is None:
        erros["ate"] = "Use dd/mm/aaaa."
    if inicio and fim and inicio > fim:
        erros["ate"] = "O fim do intervalo é anterior ao início."
    if filtros["acao"] not in ("", *ACOES):
        erros["acao"] = "Escolha uma ação."
        filtros["acao"] = ""
    return filtros, erros


def handle_historico_get(handler) -> None:
    app = ui()
    filtros, erros = filtros_do_historico(handler.query())
    with app.connect() as conn:
        linhas = [] if erros else listar_historico(conn, filtros)
    handler.send_html(200, historico_page(linhas, filtros, erros))


def backup_dir() -> Path:
    path = Path(os.environ.get("BACKUP_DIR", "/var/lib/apinil/backups"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def caminho_do_backup(nome: str) -> Path | None:
    if not NOME_RE.fullmatch(nome or ""):
        return None
    base = backup_dir().resolve()
    alvo = (base / nome).resolve()
    if alvo.parent != base:
        return None
    return alvo


def _quando_manifesto(texto: str) -> datetime:
    valor = datetime.fromisoformat(texto)
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=ZONA)
    return valor.astimezone(ZONA)


def listar_backups() -> list[dict]:
    try:
        base = backup_dir()
    except OSError:
        return []
    itens = []
    for manifesto in base.glob("apinil-*.json"):
        nome = manifesto.name[:-5] + ".dump"
        if caminho_do_backup(nome) is None:
            continue
        dump = base / nome
        if not dump.is_file():
            continue
        try:
            dados = json.loads(manifesto.read_text(encoding="utf-8"))
            quando = _quando_manifesto(str(dados.get("criado_em") or ""))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        origem = dados.get("origem") if dados.get("origem") in ("manual", "automatico") else "manual"
        itens.append({
            "nome": nome,
            "quando": quando,
            "origem": origem,
            "rotulo": "Automático" if origem == "automatico" else "Manual",
            "usuario": str(dados.get("usuario") or "")[:120],
            "bytes": int(dados.get("bytes") or dump.stat().st_size),
            "versao": str(dados.get("versao") or "")[:40],
        })
    itens.sort(key=lambda item: item["quando"], reverse=True)
    return itens


def _binario(nome: str) -> str | None:
    encontrado = shutil.which(nome)
    if encontrado:
        return encontrado
    candidato = Path(f"/usr/lib/postgresql/18/bin/{nome}")
    if candidato.is_file() and os.access(candidato, os.X_OK):
        return str(candidato)
    return None


def _conexao():
    app = ui()
    url = app.database_url()
    if not url:
        raise BackupErro("falha")
    partes = urlparse(url)
    nome = partes.path.lstrip("/").split("?")[0]
    if not partes.hostname or not partes.username or not nome:
        raise BackupErro("falha")
    return {
        "host": partes.hostname,
        "port": str(partes.port or 5432),
        "user": unquote(partes.username),
        "password": unquote(partes.password or ""),
        "dbname": nome,
    }


def _ambiente(senha: str) -> dict:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PGPASSWORD": senha,
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
    }


def _redigir(texto: str, senha: str) -> str:
    if senha:
        texto = texto.replace(senha, "***")
    return texto[:400]


def _executar(comando: list[str], ambiente: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        comando,
        env=ambiente,
        capture_output=True,
        timeout=180,
        check=False,
    )


def _nome_novo() -> str:
    base = backup_dir()
    momento = agora().replace(microsecond=0)
    for _ in range(5):
        nome = "apinil-" + momento.strftime("%Y%m%d-%H%M%S") + ".dump"
        if not (base / nome).exists():
            return nome
        momento += timedelta(seconds=1)
    raise BackupErro("falha")


def criar_backup(origem: str, usuario: str) -> dict:
    if origem not in ("manual", "automatico"):
        origem = "manual"
    if not _backup_lock.acquire(blocking=False):
        raise BackupErro("ocupado")
    dump = None
    pronto = False
    try:
        pg_dump = _binario("pg_dump")
        if not pg_dump:
            raise BackupErro("ferramenta")
        conexao = _conexao()
        nome = _nome_novo()
        dump = backup_dir() / nome
        comando = [
            pg_dump, "-h", conexao["host"], "-p", conexao["port"], "-U", conexao["user"],
            "-d", conexao["dbname"], "-Fc", "--no-owner", "--no-privileges", "-f", str(dump),
        ]
        resultado = _executar(comando, _ambiente(conexao["password"]))
        if resultado.returncode != 0 or not dump.is_file() or dump.stat().st_size == 0:
            print("backup falhou:", _redigir(resultado.stderr.decode("utf-8", "replace"), conexao["password"]))
            raise BackupErro("falha")
        manifesto = {
            "nome": nome,
            "criado_em": agora().isoformat(timespec="seconds"),
            "origem": origem,
            "usuario": (usuario or "Sistema")[:120],
            "bytes": dump.stat().st_size,
            "versao": versao_app(),
        }
        dump.with_suffix(".json").write_text(
            json.dumps(manifesto, ensure_ascii=False), encoding="utf-8",
        )
        pronto = True
        return manifesto
    finally:
        if dump is not None and not pronto:
            dump.unlink(missing_ok=True)
            dump.with_suffix(".json").unlink(missing_ok=True)
        _backup_lock.release()


def encerrar_sessoes() -> None:
    app = ui()
    with app.connect() as conn:
        conn.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = current_database() AND pid <> pg_backend_pid()
            """
        )
        conn.commit()


def restaurar_backup(nome: str, *, confirmacao: str, encerrar=None) -> None:
    if confirmacao != "RESTAURAR":
        raise BackupErro("confirmacao")
    caminho = caminho_do_backup(nome)
    if caminho is None or not caminho.is_file():
        raise BackupErro("arquivo")
    pg_restore = _binario("pg_restore")
    if not pg_restore:
        raise BackupErro("ferramenta")
    if not _backup_lock.acquire(blocking=False):
        raise BackupErro("ocupado")
    try:
        if encerrar is None:
            encerrar_sessoes()
        else:
            encerrar()
        conexao = _conexao()
        comando = [
            pg_restore, "--clean", "--if-exists", "--no-owner", "--no-privileges",
            "--single-transaction", "-h", conexao["host"], "-p", conexao["port"],
            "-U", conexao["user"], "-d", conexao["dbname"], str(caminho),
        ]
        resultado = _executar(comando, _ambiente(conexao["password"]))
        if resultado.returncode != 0:
            print("restauração falhou:", _redigir(resultado.stderr.decode("utf-8", "replace"), conexao["password"]))
            raise BackupErro("falha")
    finally:
        _backup_lock.release()


def _tamanho(valor: int) -> str:
    if valor < 1024:
        return f"{valor} B"
    if valor < 1024 * 1024:
        return f"{valor / 1024:.0f} KB"
    return f"{valor / (1024 * 1024):.1f} MB"


def backups_page(itens: list[dict], aviso: str, erro: str) -> bytes:
    app = ui()
    blocos = []
    for item in itens:
        nome = item["nome"]
        blocos.append(
            '<article class="backup-item">'
            "<div>"
            f"<strong>{app.esc(formatar_quando(item['quando']))}</strong>"
            f"<span>{app.esc(item['rotulo'])} · {app.esc(item['usuario'] or 'Sistema')} · {app.esc(_tamanho(item['bytes']))}</span>"
            f"<span class='hint'>{app.esc(nome)}</span>"
            "</div>"
            '<div class="backup-actions">'
            f'<a class="primary" href="/restrito/backups/arquivo?nome={quote(nome)}">Baixar</a>'
            '<form class="inline-form" method="post" action="/restrito/backups" '
            'onsubmit="return confirm(\'A restauração substitui o banco atual. Continuar?\')">'
            '<input type="hidden" name="acao" value="restaurar">'
            f'<input type="hidden" name="nome" value="{app.esc(nome)}">'
            '<label>Digite RESTAURAR<input name="confirmacao" autocomplete="off" placeholder="RESTAURAR"></label>'
            "<button type=\"submit\">Restaurar</button>"
            "</form>"
            "</div>"
            "</article>"
        )
    lista = "".join(blocos) or '<p class="home-empty">Nenhum backup ainda.</p>'
    corpo = f"""
<header class="page-head">
  <p class="eyebrow">SISTEMA</p>
  <h1>Backups</h1>
  <p class="lede">O arquivo guarda o banco inteiro: títulos, pessoas, usuários e os anexos gravados nos lançamentos. Um backup automático é feito uma vez por dia. A restauração substitui os dados atuais.</p>
</header>
{app.flashes_html(aviso, erro)}
<section class="panel">
  <form class="backup-now" method="post" action="/restrito/backups">
    <input type="hidden" name="acao" value="criar">
    <button type="submit">Fazer backup agora</button>
  </form>
  <div class="panel-head"><h2>Arquivos</h2><span>{app.esc(app.count_label(len(itens)))}</span></div>
  {lista}
</section>
"""
    return app.page("Backups", corpo, logged=True, current="/restrito/backups")


def _destino(form: dict, codigo: str, *, erro: bool = False, nome: str = "") -> str:
    base = "/restrito/cadastros" if form.get("retorno") == "inicio" else "/restrito/backups"
    consulta = {"erro" if erro else "aviso": codigo}
    if nome and NOME_RE.fullmatch(nome):
        consulta["nome"] = nome
    return base + "?" + urlencode(consulta)


def handle_backups_get(handler, user) -> None:
    app = ui()
    if not app.is_admin(user):
        handler.send_html(403, app.page(
            "Backups",
            "<h1>Backups</h1><p>Somente um administrador pode criar, baixar ou restaurar backups.</p>",
            logged=True, current="/restrito/backups",
        ))
        return
    params = handler.query()
    handler.send_html(200, backups_page(
        listar_backups(),
        AVISOS.get(params.get("aviso", ""), ""),
        AVISOS.get(params.get("erro", ""), ""),
    ))


def handle_backup_arquivo(handler, user) -> None:
    app = ui()
    if not app.is_admin(user):
        handler.send_html(403, app.page(
            "Backups", "<h1>Backups</h1><p>Somente um administrador pode baixar um backup.</p>",
            logged=True, current="/restrito/backups",
        ))
        return
    nome = handler.query().get("nome", "")
    caminho = caminho_do_backup(nome)
    if caminho is None or not caminho.is_file():
        handler.send_html(404, app.page("Backups", "<h1>Backup não encontrado</h1>", logged=True, current="/restrito/backups"))
        return
    registrar_direto(user, "visualizacao", "Backups", nome, f"Baixou o backup {nome}", app.client_ip(handler))
    corpo = caminho.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", "application/octet-stream")
    handler.send_header("Content-Disposition", f'attachment; filename="{nome}"')
    handler.send_header("Cache-Control", "no-store")
    handler.add_security_headers()
    handler.send_header("Content-Length", str(len(corpo)))
    handler.end_headers()
    handler.wfile.write(corpo)


def handle_backups_post(handler, user, form: dict) -> None:
    app = ui()
    if not app.is_admin(user):
        handler.send_html(403, app.page(
            "Backups",
            "<h1>Backups</h1><p>Somente um administrador pode criar, baixar ou restaurar backups.</p>",
            logged=True, current="/restrito/backups",
        ))
        return
    try:
        handler.connection.settimeout(180)
    except Exception:
        pass
    acao = form.get("acao", "")
    try:
        if acao == "criar":
            manifesto = criar_backup("manual", nome_usuario(user))
            handler.redirect(_destino(form, "criado", nome=manifesto["nome"]))
            return
        if acao == "restaurar":
            restaurar_backup(form.get("nome", ""), confirmacao=form.get("confirmacao", ""))
            handler.redirect(_destino(form, "restaurado", nome=form.get("nome", "")))
            return
    except BackupErro as exc:
        handler.redirect(_destino(form, exc.codigo, erro=True))
        return
    handler.redirect("/restrito/backups")


def ja_tem_backup_do_dia(dia: date) -> bool:
    for item in listar_backups():
        if item["origem"] == "automatico" and item["quando"].date() == dia:
            return True
    return False


def garantir_backup_do_dia() -> dict | None:
    dia = agora().date()
    if ja_tem_backup_do_dia(dia):
        return None
    manifesto = criar_backup("automatico", "Sistema")
    registrar_direto(
        {"id": None, "name": "Sistema"},
        "inclusao",
        "Backups",
        manifesto["nome"],
        f"Backup automático concluído: {manifesto['nome']}",
        "",
    )
    return manifesto


def _loop_backup() -> None:
    while True:
        try:
            garantir_backup_do_dia()
        except Exception as exc:
            print("backup automático:", type(exc).__name__)
        time.sleep(1800)


def agendar_backup_diario() -> None:
    global _backup_agendado
    if _backup_agendado or not ui().database_url():
        return
    _backup_agendado = True
    threading.Thread(target=_loop_backup, name="apinil-backup", daemon=True).start()
