"""Cadastro de pessoas e lançamentos de contas a pagar e a receber."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from email import message_from_bytes
from email.policy import default
from urllib.error import URLError
from urllib.request import Request, urlopen

import psycopg

PESSOAS_PATH = "/restrito/cadastros/pessoas"
PAGAR_PATH = "/restrito/contas-a-pagar"
RECEBER_PATH = "/restrito/contas-a-receber"
TITULOS_PATH = "/restrito/titulos"
CONSULTA_PATH = "/restrito/cadastros/pessoas/consulta"
CEP_PATH = "/restrito/cep"
ARQUIVO_PATH = "/restrito/lancamentos/arquivo"

PAPEIS = (("fornecedor", "Fornecedor"), ("cliente", "Cliente"), ("ambos", "Ambos"))
CONTATOS = (
    ("fixo", "Telefone fixo", False),
    ("celular", "Celular", False),
    ("whatsapp", "WhatsApp", False),
    ("email", "E-mail", True),
    ("email_xml", "E-mail para XML", True),
    ("email_nfe", "E-mail para NF-e", True),
    ("email_boleto", "E-mail para boleto", True),
)
ENDERECOS = (("comercial", "Comercial"), ("entrega", "Entrega"), ("faturamento", "Faturamento"))
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def ui():
    import app
    return app


def only_digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def valid_cpf(digits: str) -> bool:
    if len(digits) != 11 or digits == digits[0] * 11:
        return False

    def dv(base: str, factor: int) -> int:
        total = sum(int(digit) * weight for digit, weight in zip(base, range(factor, 1, -1)))
        rest = (total * 10) % 11
        return 0 if rest == 10 else rest

    return dv(digits[:9], 10) == int(digits[9]) and dv(digits[:10], 11) == int(digits[10])


def valid_cnpj(digits: str) -> bool:
    if len(digits) != 14 or digits == digits[0] * 14:
        return False

    def dv(base: str, weights: list[int]) -> int:
        rest = sum(int(digit) * weight for digit, weight in zip(base, weights)) % 11
        return 0 if rest < 2 else 11 - rest

    first = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    second = [6, *first]
    return dv(digits[:12], first) == int(digits[12]) and dv(digits[:13], second) == int(digits[13])


def format_documento(digits: str) -> str:
    if len(digits) == 11:
        return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"
    if len(digits) == 14:
        return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"
    return digits


def parse_valor(value: str) -> Decimal | None:
    text = (value or "").strip().replace("R$", "").replace(" ", "")
    if not text:
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", text):
        text = text.replace(".", "")
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if number <= 0 or number > Decimal("999999999999.99"):
        return None
    return number.quantize(Decimal("0.01"))


def format_valor(value) -> str:
    if isinstance(value, Decimal):
        number = value.quantize(Decimal("0.01"))
    else:
        number = parse_valor(str(value or ""))
        if number is None:
            return ""
    inteiro, frac = f"{number:.2f}".split(".")
    grupos = []
    while inteiro:
        grupos.append(inteiro[-3:])
        inteiro = inteiro[:-3]
    return ".".join(reversed(grupos)) + "," + frac


def parse_data(value: str) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    if "T" in text:
        text = text.split("T", 1)[0]
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def format_data(value) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    parsed = parse_data(str(value or ""))
    return parsed.strftime("%d/%m/%Y") if parsed else ""


def dias_da_condicao(nome: str, qtde: int) -> list[int]:
    qtde = max(1, min(48, qtde))
    if not (nome or "").strip():
        return [0] if qtde == 1 else [30 * (i + 1) for i in range(qtde)]
    normal = nome.upper().replace("À", "A")
    if normal.startswith("AVISTA") or normal.startswith("A VISTA"):
        return [0] if qtde == 1 else [30 * i for i in range(qtde)]
    nums = [int(item) for item in re.findall(r"\d+", nome)]
    if len(nums) >= qtde:
        return nums[:qtde]
    if len(nums) == 1:
        return [nums[0] * (i + 1) for i in range(qtde)]
    return [30 * (i + 1) for i in range(qtde)]


def dividir_valor(total: Decimal, qtde: int) -> list[Decimal]:
    cents = int((total * 100).to_integral_value())
    base, resto = divmod(cents, qtde)
    valores = [Decimal(base) / Decimal(100) for _ in range(qtde)]
    if qtde:
        valores[-1] = (Decimal(base + resto) / Decimal(100)).quantize(Decimal("0.01"))
    return valores


def read_request_form(headers, rfile, max_form: int, max_upload: int):
    ctype = headers.get("Content-Type", "")
    try:
        length = int(headers.get("Content-Length", "0") or 0)
    except ValueError:
        return {}, {}, True
    multipart = ctype.lower().startswith("multipart/form-data")
    limit = max_upload if multipart else max_form
    if length < 0 or length > limit:
        return {}, {}, True
    raw = rfile.read(length) if length else b""
    if not multipart:
        from urllib.parse import parse_qs
        parsed = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
        return {key: values[0] for key, values in parsed.items()}, {}, False
    return parse_multipart(ctype, raw)


def parse_multipart(content_type: str, body: bytes):
    header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
    message = message_from_bytes(header + body, policy=default)
    form: dict[str, str] = {}
    files: dict[str, dict] = {}
    if not message.is_multipart():
        return form, files, False
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        payload = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if filename:
            files[name] = {
                "filename": filename,
                "data": payload,
                "type": part.get_content_type(),
            }
        else:
            form[name] = payload.decode("utf-8", "replace")
    return form, files, False


def fetch_json(url: str) -> dict | None:
    request = Request(url, headers={"User-Agent": "Apinil", "Accept": "application/json"})
    try:
        with urlopen(request, timeout=8) as response:
            return json.loads(response.read().decode("utf-8"))
    except (URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def consultar_documento(documento: str) -> dict:
    digits = only_digits(documento)
    if len(digits) == 11:
        if not valid_cpf(digits):
            return {"ok": False, "mensagem": "CPF inválido."}
        return {
            "ok": True,
            "tipo": "F",
            "documento": format_documento(digits),
            "mensagem": "CPF válido. A Receita Federal não oferece consulta pública de CPF.",
        }
    if len(digits) != 14 or not valid_cnpj(digits):
        return {"ok": False, "mensagem": "Informe um CPF ou CNPJ válido."}
    data = fetch_json(f"https://brasilapi.com.br/api/cnpj/v1/{digits}")
    if not data or data.get("message"):
        return {
            "ok": False,
            "mensagem": "Não foi possível consultar o CNPJ agora. Confira o número e preencha os dados.",
        }
    phone = only_digits(str(data.get("ddd_telefone_1") or ""))
    return {
        "ok": True,
        "tipo": "J",
        "documento": format_documento(digits),
        "nome": data.get("razao_social") or "",
        "fantasia": data.get("nome_fantasia") or "",
        "telefone": phone,
        "email": data.get("email") or "",
        "cep": only_digits(str(data.get("cep") or "")),
        "logradouro": data.get("logradouro") or "",
        "numero": data.get("numero") or "",
        "complemento": data.get("complemento") or "",
        "bairro": data.get("bairro") or "",
        "cidade": data.get("municipio") or "",
        "uf": data.get("uf") or "",
        "mensagem": "Dados públicos do CNPJ localizados.",
    }


def consultar_cep(cep: str) -> dict:
    digits = only_digits(cep)
    if len(digits) != 8:
        return {"ok": False, "mensagem": "Informe o CEP com 8 números."}
    data = fetch_json(f"https://viacep.com.br/ws/{digits}/json/")
    if not data or data.get("erro"):
        return {"ok": False, "mensagem": "CEP não encontrado."}
    return {
        "ok": True,
        "cep": format_cep(digits),
        "logradouro": data.get("logradouro") or "",
        "bairro": data.get("bairro") or "",
        "cidade": data.get("localidade") or "",
        "uf": data.get("uf") or "",
    }


def format_cep(digits: str) -> str:
    return f"{digits[:5]}-{digits[5:]}" if len(digits) == 8 else digits


def parse_nfe(data: bytes) -> dict | None:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    def first(node, name: str):
        if node is None:
            return None
        for element in node.iter():
            if local(element.tag) == name:
                return element
        return None

    def text(node, name: str) -> str:
        element = first(node, name)
        return (element.text or "").strip() if element is not None else ""

    emit = first(root, "emit")
    dest = first(root, "dest")
    ide = first(root, "ide")
    total = first(root, "ICMSTot")
    dups = []
    for element in root.iter():
        if local(element.tag) != "dup":
            continue
        dups.append({
            "vencimento": text(element, "dVenc"),
            "valor": text(element, "vDup"),
        })
    if emit is None and ide is None:
        return None
    return {
        "numero": text(ide, "nNF"),
        "emissao": text(ide, "dhEmi")[:10],
        "valor": text(total, "vNF"),
        "emit_doc": only_digits(text(emit, "CNPJ") or text(emit, "CPF")),
        "emit_nome": text(emit, "xNome"),
        "dest_doc": only_digits(text(dest, "CNPJ") or text(dest, "CPF")),
        "dest_nome": text(dest, "xNome"),
        "dups": dups,
    }


DINHEIRO_RE = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}")
ROTULO_LINHA = re.compile(
    r"(?i)^(cnpj|cpf|nf-?e|n[º°]|n[uú]mero|nome|raz[aã]o|inscri|endere|bairro|cep|munic|"
    r"fone|natureza|destinat|emitente|protocolo|chave|s[eé]rie|data|valor|recebemos)\b"
)


def documentos_no_texto(text: str) -> list[tuple[int, str]]:
    ocupado = [False] * (len(text) + 1)
    found: list[tuple[int, str]] = []

    def marcar(inicio: int, fim: int, digits: str) -> None:
        if any(ocupado[inicio:fim]):
            return
        found.append((inicio, digits))
        for index in range(inicio, fim):
            ocupado[index] = True

    for match in re.finditer(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", text):
        digits = only_digits(match.group(0))
        if valid_cnpj(digits):
            marcar(match.start(), match.end(), digits)
    for match in re.finditer(r"(?<!\d)\d{14}(?!\d)", text):
        if valid_cnpj(match.group(0)):
            marcar(match.start(), match.end(), match.group(0))
    for match in re.finditer(r"\d{3}\.\d{3}\.\d{3}-\d{2}", text):
        digits = only_digits(match.group(0))
        if valid_cpf(digits):
            marcar(match.start(), match.end(), digits)
    for match in re.finditer(r"(?<!\d)\d{11}(?!\d)", text):
        if valid_cpf(match.group(0)):
            marcar(match.start(), match.end(), match.group(0))
    found.sort()
    unicos = []
    vistos = set()
    for pos, digits in found:
        if digits in vistos:
            continue
        vistos.add(digits)
        unicos.append((pos, digits))
    return unicos


def nome_perto(text: str, start: int) -> str:
    linha_inicio = text.rfind("\n", 0, start) + 1
    mesma = text[linha_inicio:start]
    mesma = re.sub(r"(?i)\b(cnpj|cpf)\b[:\s/]*$", "", mesma).strip(" :-/")
    if re.search(r"[A-Za-zÀ-ÿ]{3,}", mesma):
        return re.sub(r"\s+", " ", mesma)[:160]
    antes = text[max(0, start - 320):linha_inicio]
    for linha in reversed([item.strip(" :-") for item in antes.splitlines() if item.strip()]):
        if ROTULO_LINHA.match(linha):
            continue
        if re.search(r"[A-Za-zÀ-ÿ]{3,}", linha) and not re.fullmatch(r"[\d.\-/\s]+", linha):
            return re.sub(r"\s+", " ", linha)[:160]
    return ""


def numero_da_nota(text: str) -> str:
    match = re.search(
        r"(?:N[º°o]\.?|N[UÚ]MERO)\s*[:.]?\s*(\d{1,3}(?:\.\d{3})+|\d{1,9})",
        text,
        re.I,
    )
    if not match:
        return ""
    digits = only_digits(match.group(1))
    return digits.lstrip("0") or "0"


def valor_da_nota(text: str) -> str:
    janela = re.search(r"VALOR\s+TOTAL(?:\s+DA\s+NOTA)?(.{0,80})", text, re.I | re.S)
    if janela:
        achados = DINHEIRO_RE.findall(janela.group(1))
        if achados:
            return achados[-1]
    achados = DINHEIRO_RE.findall(text)
    if not achados:
        return ""
    return max(achados, key=lambda item: parse_valor(item) or Decimal("0"))


def parse_nota_texto(text: str) -> dict | None:
    if not text or not text.strip():
        return None
    docs = documentos_no_texto(text)
    if not docs and not numero_da_nota(text) and not valor_da_nota(text):
        return None
    emissao = ""
    data = re.search(r"EMISS[AÃ]O.{0,40}?(\d{2}/\d{2}/\d{4})", text, re.I | re.S)
    if data:
        emissao = data.group(1)
    emit_doc = docs[0][1] if docs else ""
    dest_doc = docs[1][1] if len(docs) > 1 else ""
    return {
        "numero": numero_da_nota(text),
        "emissao": emissao,
        "valor": valor_da_nota(text),
        "emit_doc": emit_doc,
        "emit_nome": nome_perto(text, docs[0][0]) if docs else "",
        "dest_doc": dest_doc,
        "dest_nome": nome_perto(text, docs[1][0]) if len(docs) > 1 else "",
        "dups": [],
    }


def texto_de_pdf(data: bytes) -> str:
    from io import BytesIO
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def parse_pdf_nota(data: bytes) -> dict | None:
    try:
        text = texto_de_pdf(data)
    except Exception:
        return None
    return parse_nota_texto(text)


def blank_pessoa() -> dict:
    return {
        "id": "",
        "tipo": "F",
        "documento": "",
        "nome": "",
        "fantasia": "",
        "papel": "cliente",
        "contatos": {},
        "enderecos": {},
        "errors": {},
        "titulo": "Nova pessoa",
    }


def pessoa_from_form(form: dict) -> dict:
    editor = blank_pessoa()
    editor["id"] = form.get("id", "").strip()
    editor["tipo"] = "J" if form.get("tipo") == "J" else "F"
    editor["documento"] = form.get("documento", "").strip()
    editor["nome"] = " ".join(form.get("nome", "").split())
    editor["fantasia"] = " ".join(form.get("fantasia", "").split())
    editor["papel"] = form.get("papel", "")
    editor["titulo"] = "Editar pessoa" if editor["id"] else "Nova pessoa"
    for key, _label, _email in CONTATOS:
        editor["contatos"][key] = form.get(f"contato_{key}", "").strip()
    for key, _label in ENDERECOS:
        editor["enderecos"][key] = {
            "cep": form.get(f"end_{key}_cep", "").strip(),
            "logradouro": " ".join(form.get(f"end_{key}_logradouro", "").split()),
            "numero": form.get(f"end_{key}_numero", "").strip(),
            "complemento": " ".join(form.get(f"end_{key}_complemento", "").split()),
            "bairro": " ".join(form.get(f"end_{key}_bairro", "").split()),
            "cidade": " ".join(form.get(f"end_{key}_cidade", "").split()),
            "uf": form.get(f"end_{key}_uf", "").strip().upper(),
        }
    return editor


def validate_pessoa(editor: dict) -> dict[str, str]:
    errors = {}
    digits = only_digits(editor["documento"])
    if editor["tipo"] == "F":
        if not valid_cpf(digits):
            errors["documento"] = "Informe um CPF válido."
    elif not valid_cnpj(digits):
        errors["documento"] = "Informe um CNPJ válido."
    if not editor["nome"]:
        errors["nome"] = "Informe o nome ou a razão social."
    elif len(editor["nome"]) > 160:
        errors["nome"] = "Use no máximo 160 caracteres."
    if len(editor["fantasia"]) > 160:
        errors["fantasia"] = "Use no máximo 160 caracteres."
    if editor["papel"] not in {item[0] for item in PAPEIS}:
        errors["papel"] = "Classifique como fornecedor, cliente ou ambos."
    for key, label, is_email in CONTATOS:
        value = editor["contatos"].get(key, "")
        if not value:
            continue
        if is_email and (not EMAIL_RE.fullmatch(value) or len(value) > 120):
            errors[f"contato_{key}"] = f"Informe um e-mail válido em {label.lower()}."
        if not is_email and not 10 <= len(only_digits(value)) <= 11:
            errors[f"contato_{key}"] = f"Informe {label.lower()} com DDD e número."
    for key, label in ENDERECOS:
        address = editor["enderecos"].get(key) or {}
        used = any(address.values())
        if not used:
            continue
        cep = only_digits(address.get("cep", ""))
        if cep and len(cep) != 8:
            errors[f"end_{key}_cep"] = f"CEP do endereço {label.lower()} deve ter 8 números."
        if not address.get("logradouro"):
            errors[f"end_{key}_logradouro"] = f"Informe o logradouro do endereço {label.lower()}."
        if not address.get("cidade"):
            errors[f"end_{key}_cidade"] = f"Informe a cidade do endereço {label.lower()}."
        if not re.fullmatch(r"[A-Z]{2}", address.get("uf", "")):
            errors[f"end_{key}_uf"] = f"Informe a UF do endereço {label.lower()}."
    return errors


def list_pessoas(conn, q: str):
    if not q:
        return conn.execute(
            "SELECT id, tipo, documento, nome, fantasia, papel FROM pessoa ORDER BY nome, id"
        ).fetchall()
    like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    return conn.execute(
        """
        SELECT id, tipo, documento, nome, fantasia, papel
        FROM pessoa
        WHERE nome ILIKE %s ESCAPE '\\'
           OR fantasia ILIKE %s ESCAPE '\\'
           OR documento ILIKE %s ESCAPE '\\'
        ORDER BY nome, id
        """,
        (like, like, like),
    ).fetchall()


def load_pessoa(conn, item_id: int) -> dict | None:
    row = conn.execute(
        "SELECT id, tipo, documento, nome, fantasia, papel FROM pessoa WHERE id = %s",
        (item_id,),
    ).fetchone()
    if row is None:
        return None
    editor = blank_pessoa()
    editor.update({
        "id": str(row[0]),
        "tipo": row[1],
        "documento": format_documento(row[2]),
        "nome": row[3],
        "fantasia": row[4],
        "papel": row[5],
        "titulo": "Editar pessoa",
    })
    for tipo, valor in conn.execute(
        "SELECT tipo, valor FROM pessoa_contato WHERE pessoa_id = %s", (item_id,)
    ):
        editor["contatos"][tipo] = valor
    for item in conn.execute(
        """
        SELECT tipo, cep, logradouro, numero, complemento, bairro, cidade, uf
        FROM pessoa_endereco WHERE pessoa_id = %s
        """,
        (item_id,),
    ):
        editor["enderecos"][item[0]] = {
            "cep": format_cep(item[1]) if item[1] else "",
            "logradouro": item[2],
            "numero": item[3],
            "complemento": item[4],
            "bairro": item[5],
            "cidade": item[6],
            "uf": item[7],
        }
    return editor


def save_pessoa(conn, editor: dict) -> int:
    digits = only_digits(editor["documento"])
    item_id = ui().parse_record_id(editor["id"])
    if item_id:
        conn.execute(
            """
            UPDATE pessoa
            SET tipo = %s, documento = %s, nome = %s, fantasia = %s, papel = %s, updated_at = now()
            WHERE id = %s
            """,
            (editor["tipo"], digits, editor["nome"], editor["fantasia"], editor["papel"], item_id),
        )
    else:
        item_id = conn.execute(
            """
            INSERT INTO pessoa (tipo, documento, nome, fantasia, papel)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (editor["tipo"], digits, editor["nome"], editor["fantasia"], editor["papel"]),
        ).fetchone()[0]
    conn.execute("DELETE FROM pessoa_contato WHERE pessoa_id = %s", (item_id,))
    for key, _label, _email in CONTATOS:
        value = editor["contatos"].get(key, "")
        if value:
            conn.execute(
                "INSERT INTO pessoa_contato (pessoa_id, tipo, valor) VALUES (%s, %s, %s)",
                (item_id, key, value),
            )
    conn.execute("DELETE FROM pessoa_endereco WHERE pessoa_id = %s", (item_id,))
    for key, _label in ENDERECOS:
        address = editor["enderecos"].get(key) or {}
        if not any(address.values()):
            continue
        conn.execute(
            """
            INSERT INTO pessoa_endereco
                (pessoa_id, tipo, cep, logradouro, numero, complemento, bairro, cidade, uf)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                item_id, key, only_digits(address.get("cep", "")), address.get("logradouro", ""),
                address.get("numero", ""), address.get("complemento", ""), address.get("bairro", ""),
                address.get("cidade", ""), address.get("uf", ""),
            ),
        )
    return item_id


def pessoas_page(rows, q: str, aviso: str, erro: str, editor: dict) -> bytes:
    app = ui()
    errors = editor.get("errors") or {}
    open_tab = "contato" if any(key.startswith("contato_") for key in errors) else "principais"
    if any(key.startswith("end_") for key in errors):
        open_tab = "enderecos"
    papel_options = []
    for value, label in PAPEIS:
        selected = " selected" if editor.get("papel") == value else ""
        papel_options.append(f'<option value="{value}"{selected}>{label}</option>')
    tipo_f = " checked" if editor.get("tipo") != "J" else ""
    tipo_j = " checked" if editor.get("tipo") == "J" else ""
    nome_label = "Razão social" if editor.get("tipo") == "J" else "Nome"
    fantasia_label = "Nome fantasia" if editor.get("tipo") == "J" else "Nome"
    contacts = []
    for key, label, _email in CONTATOS:
        contacts.append(
            f'<label>{app.esc(label)}<input name="contato_{key}" value="{app.esc(editor["contatos"].get(key, ""))}">'
            f'{app.field_error(errors, f"contato_{key}")}</label>'
        )
    addresses = []
    for key, label in ENDERECOS:
        address = editor["enderecos"].get(key) or {}
        addresses.append(f"""
<section class="addr">
  <h3>{app.esc(label)}</h3>
  <div class="cep-row">
    <label>CEP<input name="end_{key}_cep" value="{app.esc(address.get("cep", ""))}" data-cep="{key}" inputmode="numeric" maxlength="9">{app.field_error(errors, f"end_{key}_cep")}</label>
    <button type="button" class="inline-btn" data-buscar-cep="{key}">Buscar CEP</button>
  </div>
  <label>Logradouro<input name="end_{key}_logradouro" value="{app.esc(address.get("logradouro", ""))}">{app.field_error(errors, f"end_{key}_logradouro")}</label>
  <label>Número<input name="end_{key}_numero" value="{app.esc(address.get("numero", ""))}"></label>
  <label>Complemento<input name="end_{key}_complemento" value="{app.esc(address.get("complemento", ""))}"></label>
  <label>Bairro<input name="end_{key}_bairro" value="{app.esc(address.get("bairro", ""))}"></label>
  <label>Cidade<input name="end_{key}_cidade" value="{app.esc(address.get("cidade", ""))}">{app.field_error(errors, f"end_{key}_cidade")}</label>
  <label>UF<input name="end_{key}_uf" value="{app.esc(address.get("uf", ""))}" maxlength="2">{app.field_error(errors, f"end_{key}_uf")}</label>
</section>
""")
    cancel = ""
    if editor.get("id"):
        cancel = f'<a class="text-link" href="{app.esc(PESSOAS_PATH + ("?q=" + q if q else ""))}">Cancelar</a>'
    form = f"""
<form class="record-form" method="post" action="{PESSOAS_PATH}">
  <input type="hidden" name="acao" value="salvar">
  <input type="hidden" name="id" value="{app.esc(editor.get("id", ""))}">
  <input type="hidden" name="q" value="{app.esc(q)}">
  <div class="tabs">
    <button type="button" class="{'is-on' if open_tab == 'principais' else ''}" data-tab="principais">Principais</button>
    <button type="button" class="{'is-on' if open_tab == 'contato' else ''}" data-tab="contato">Contato</button>
    <button type="button" class="{'is-on' if open_tab == 'enderecos' else ''}" data-tab="enderecos">Endereços</button>
  </div>
  <div class="tab-panel {'is-on' if open_tab == 'principais' else ''}" data-panel="principais">
    <div class="checkline">
      <label><input type="radio" name="tipo" value="F"{tipo_f}> Física</label>
      <label><input type="radio" name="tipo" value="J"{tipo_j}> Jurídica</label>
    </div>
    <div class="doc-row">
      <label>CPF/CNPJ<input name="documento" value="{app.esc(editor.get("documento", ""))}" inputmode="numeric">{app.field_error(errors, "documento")}</label>
      <button type="button" class="inline-btn" id="buscar-receita">Buscar na Receita</button>
    </div>
    <p class="hint" id="consulta-msg"></p>
    <label id="label-nome">{nome_label}<input name="nome" maxlength="160" value="{app.esc(editor.get("nome", ""))}">{app.field_error(errors, "nome")}</label>
    <label id="label-fantasia">{fantasia_label}<input name="fantasia" maxlength="160" value="{app.esc(editor.get("fantasia", ""))}">{app.field_error(errors, "fantasia")}</label>
    <label>Classificação<select name="papel">{''.join(papel_options)}</select>{app.field_error(errors, "papel")}</label>
  </div>
  <div class="tab-panel {'is-on' if open_tab == 'contato' else ''}" data-panel="contato">{''.join(contacts)}</div>
  <div class="tab-panel {'is-on' if open_tab == 'enderecos' else ''}" data-panel="enderecos">{''.join(addresses)}</div>
  <button type="submit">{"Salvar alterações" if editor.get("id") else "Criar pessoa"}</button>
  {cancel}
</form>
<script>
document.querySelectorAll("[data-tab]").forEach((button) => {{
  button.addEventListener("click", () => {{
    document.querySelectorAll("[data-tab]").forEach((item) => item.classList.toggle("is-on", item === button));
    document.querySelectorAll("[data-panel]").forEach((panel) => panel.classList.toggle("is-on", panel.dataset.panel === button.dataset.tab));
  }});
}});
function tipoPessoa() {{
  const juridica = document.querySelector('input[name="tipo"]:checked')?.value === "J";
  document.getElementById("label-nome").childNodes[0].textContent = juridica ? "Razão social" : "Nome";
  document.getElementById("label-fantasia").childNodes[0].textContent = juridica ? "Nome fantasia" : "Nome";
}}
document.querySelectorAll('input[name="tipo"]').forEach((input) => input.addEventListener("change", tipoPessoa));
document.getElementById("buscar-receita").addEventListener("click", async () => {{
  const documento = document.querySelector('[name="documento"]').value;
  const resposta = await fetch("{CONSULTA_PATH}?documento=" + encodeURIComponent(documento));
  const dados = await resposta.json();
  document.getElementById("consulta-msg").textContent = dados.mensagem || "";
  if (!dados.ok || !dados.nome) return;
  document.querySelector('[name="documento"]').value = dados.documento || documento;
  document.querySelector('[name="nome"]').value = dados.nome || "";
  document.querySelector('[name="fantasia"]').value = dados.fantasia || "";
  document.querySelector('input[name="tipo"][value="J"]').checked = true;
  tipoPessoa();
  if (dados.telefone) document.querySelector('[name="contato_fixo"]').value = dados.telefone;
  if (dados.email) document.querySelector('[name="contato_email"]').value = dados.email;
  const prefixo = "end_comercial_";
  ["cep", "logradouro", "numero", "complemento", "bairro", "cidade", "uf"].forEach((campo) => {{
    if (dados[campo]) document.querySelector('[name="' + prefixo + campo + '"]').value = dados[campo];
  }});
}});
document.querySelectorAll("[data-buscar-cep]").forEach((button) => {{
  button.addEventListener("click", async () => {{
    const tipo = button.dataset.buscarCep;
    const cep = document.querySelector('[name="end_' + tipo + '_cep"]').value;
    const resposta = await fetch("{CEP_PATH}?cep=" + encodeURIComponent(cep));
    const dados = await resposta.json();
    document.getElementById("consulta-msg").textContent = dados.mensagem || (dados.ok ? "CEP localizado." : "");
    if (!dados.ok) return;
    ["cep", "logradouro", "bairro", "cidade", "uf"].forEach((campo) => {{
      const input = document.querySelector('[name="end_' + tipo + '_' + campo + '"]');
      if (input && dados[campo]) input.value = dados[campo];
    }});
  }});
}});
</script>
"""
    body_rows = []
    for item_id, tipo, documento, nome, fantasia, papel in rows:
        selected = " is-selected" if str(item_id) == str(editor.get("id") or "") else ""
        papel_label = dict(PAPEIS).get(papel, papel)
        body_rows.append(
            f'<tr class="{selected.strip()}">'
            f"<td><strong>{app.esc(format_documento(documento))}</strong><br>{'Jurídica' if tipo == 'J' else 'Física'}</td>"
            f"<td>{app.esc(nome)}<br><span class='hint'>{app.esc(fantasia)}</span></td>"
            f"<td><span class='pill'>{app.esc(papel_label)}</span></td>"
            f"<td>{app.row_actions(PESSOAS_PATH, item_id, q, 'Excluir esta pessoa?')}</td></tr>"
        )
    table = (
        "<table class='grid'><thead><tr><th>Documento</th><th>Nome</th><th>Classe</th><th>Ações</th></tr></thead><tbody>"
        + ("".join(body_rows) or "<tr><td colspan='4'>Nenhuma pessoa encontrada.</td></tr>")
        + "</tbody></table>"
    )
    body = f"""
<header class="page-head">
  <p class="eyebrow">CADASTROS</p>
  <h1>Pessoas</h1>
  <p class="lede">O mesmo formulário serve para pessoa física e jurídica. O CPF ou CNPJ é a chave única.</p>
</header>
{app.flashes_html(aviso, erro)}
<div class="split wide-form">
  <section class="panel"><h2>{app.esc(editor["titulo"])}</h2>{form}</section>
  <section class="panel">
    <div class="panel-head"><h2>Pessoas cadastradas</h2><span>{app.esc(app.count_label(len(rows)))}</span></div>
    <div class="grid-wrap">{table}</div>
  </section>
</div>
"""
    return app.page(
        "Pessoas", body, logged=True, current=PESSOAS_PATH,
        search={"action": PESSOAS_PATH, "q": q, "placeholder": "Nome, fantasia ou documento"},
        action={"href": PESSOAS_PATH + "?acao=incluir" + (f"&q={q}" if q else ""), "label": "+ Nova pessoa"},
    )


def handle_pessoas_get(handler) -> None:
    app = ui()
    params = handler.query()
    q = params.get("q", "").strip()[:80]
    aviso = {"incluido": "Pessoa incluída.", "alterado": "Pessoa alterada.", "excluido": "Pessoa excluída."}.get(params.get("aviso", ""), "")
    erro = {"ausente": "Registro não encontrado.", "uso": "Esta pessoa está em lançamentos e não pode ser excluída."}.get(params.get("erro", ""), "")
    editor = blank_pessoa()
    with app.connect() as conn:
        if params.get("acao") == "editar":
            item_id = app.parse_record_id(params.get("id", ""))
            loaded = load_pessoa(conn, item_id) if item_id else None
            if loaded is None:
                erro = "Registro não encontrado."
            else:
                editor = loaded
        rows = list_pessoas(conn, q)
    handler.send_html(200, pessoas_page(rows, q, aviso, erro, editor))


def handle_pessoas_post(handler, form: dict) -> None:
    app = ui()
    q = form.get("q", "").strip()[:80]
    if form.get("acao") == "excluir":
        item_id = app.parse_record_id(form.get("id", ""))
        if item_id is None:
            handler.redirect(PESSOAS_PATH)
            return
        try:
            with app.connect() as conn:
                deleted = conn.execute("DELETE FROM pessoa WHERE id = %s", (item_id,)).rowcount
                conn.commit()
        except psycopg.errors.ForeignKeyViolation:
            handler.redirect(f"{PESSOAS_PATH}?erro=uso")
            return
        handler.redirect(f"{PESSOAS_PATH}?aviso=excluido" if deleted else f"{PESSOAS_PATH}?erro=ausente")
        return
    editor = pessoa_from_form(form)
    editor["errors"] = validate_pessoa(editor)
    if editor["errors"]:
        with app.connect() as conn:
            rows = list_pessoas(conn, q)
        handler.send_html(400, pessoas_page(rows, q, "", "", editor))
        return
    try:
        with app.connect() as conn:
            existed = bool(app.parse_record_id(editor["id"]))
            if existed and load_pessoa(conn, app.parse_record_id(editor["id"])) is None:
                handler.redirect(f"{PESSOAS_PATH}?erro=ausente")
                return
            save_pessoa(conn, editor)
            conn.commit()
    except psycopg.errors.UniqueViolation:
        editor["errors"] = {"documento": "Já existe uma pessoa com este CPF ou CNPJ."}
        with app.connect() as conn:
            rows = list_pessoas(conn, q)
        handler.send_html(400, pessoas_page(rows, q, "", "", editor))
        return
    handler.redirect(f"{PESSOAS_PATH}?aviso={'alterado' if app.parse_record_id(form.get('id', '')) else 'incluido'}")


def opcoes(rows, selected: str, vazio: str) -> str:
    app = ui()
    html = [f'<option value="">{app.esc(vazio)}</option>']
    for item_id, label, extra in rows:
        mark = " selected" if str(item_id) == str(selected) else ""
        extra_attr = f" {extra}" if extra else ""
        html.append(f'<option value="{item_id}"{mark}{extra_attr}>{app.esc(label)}</option>')
    return "".join(html)


def carregar_apoio(conn, natureza: str):
    tipos = conn.execute("SELECT id, nome FROM tipo_documento ORDER BY nome").fetchall()
    papel = ("fornecedor", "ambos") if natureza == "pagar" else ("cliente", "ambos")
    pessoas = [
        (item_id, f"{nome} · {format_documento(documento)}", "")
        for item_id, nome, documento in conn.execute(
            """
            SELECT id, nome, documento
            FROM pessoa
            WHERE papel IN (%s, %s)
            ORDER BY nome
            """,
            papel,
        )
    ]
    formas = conn.execute("SELECT id, descricao, '' FROM forma_pagamento ORDER BY descricao").fetchall()
    condicoes = []
    for item_id, qtde, nome in conn.execute(
        "SELECT id, qtde_parcelas, nome FROM condicao_pagamento ORDER BY nome"
    ):
        dias = ",".join(str(dia) for dia in dias_da_condicao(nome, qtde))
        condicoes.append((item_id, f"{nome} ({qtde})", f'data-qtde="{qtde}" data-dias="{dias}"'))
    return tipos, pessoas, formas, condicoes


def blank_lancamento(natureza: str) -> dict:
    return {
        "id": "",
        "natureza": natureza,
        "origem": "manual",
        "situacao": "rascunho",
        "tipo_documento_id": "",
        "pessoa_id": "",
        "numero_documento": "",
        "forma_pagamento_id": "",
        "condicao_pagamento_id": "",
        "qtde_parcelas": "1",
        "valor_total": "",
        "data_emissao": format_data(date.today()),
        "observacao": "",
        "arquivo_nome": "",
        "pessoa_documento": "",
        "pessoa_nome": "",
        "pessoa_rotulo": "",
        "parcelas": [{"numero": 1, "valor": "", "vencimento": format_data(date.today())}],
        "errors": {},
        "titulo": "Nova entrada",
    }


def parcelas_do_form(form: dict, qtde: int) -> list[dict]:
    parcelas = []
    for numero in range(1, qtde + 1):
        parcelas.append({
            "numero": numero,
            "valor": form.get(f"parcela_valor_{numero}", "").strip(),
            "vencimento": form.get(f"parcela_vencimento_{numero}", "").strip(),
        })
    return parcelas


def lancamento_from_form(form: dict, natureza: str) -> dict:
    editor = blank_lancamento(natureza)
    editor["id"] = form.get("id", "").strip()
    editor["origem"] = form.get("origem") if form.get("origem") in ("manual", "xml", "pdf") else "manual"
    editor["tipo_documento_id"] = form.get("tipo_documento_id", "").strip()
    editor["pessoa_id"] = form.get("pessoa_id", "").strip()
    editor["numero_documento"] = form.get("numero_documento", "").strip()
    editor["forma_pagamento_id"] = form.get("forma_pagamento_id", "").strip()
    editor["condicao_pagamento_id"] = form.get("condicao_pagamento_id", "").strip()
    editor["qtde_parcelas"] = form.get("qtde_parcelas", "1").strip()
    editor["valor_total"] = form.get("valor_total", "").strip()
    editor["data_emissao"] = form.get("data_emissao", "").strip()
    editor["observacao"] = " ".join(form.get("observacao", "").split())
    editor["arquivo_nome"] = form.get("arquivo_nome", "").strip()
    editor["pessoa_documento"] = only_digits(form.get("pessoa_documento", ""))
    editor["pessoa_nome"] = " ".join(form.get("pessoa_nome", "").split())[:160]
    editor["titulo"] = "Editar entrada" if editor["id"] else "Nova entrada"
    qtde = int(editor["qtde_parcelas"]) if editor["qtde_parcelas"].isdigit() else 1
    editor["parcelas"] = parcelas_do_form(form, max(1, min(48, qtde)))
    return editor


def sugerir_parcelas(editor: dict, condicao_nome: str) -> list[dict]:
    qtde = int(editor["qtde_parcelas"]) if str(editor["qtde_parcelas"]).isdigit() else 1
    qtde = max(1, min(48, qtde))
    total = parse_valor(str(editor["valor_total"])) or Decimal("0.00")
    base = parse_data(editor.get("data_emissao", "")) or date.today()
    dias = dias_da_condicao(condicao_nome, qtde)
    valores = dividir_valor(total, qtde) if total > 0 else [Decimal("0.00")] * qtde
    parcelas = []
    for numero, (valor, dia) in enumerate(zip(valores, dias), start=1):
        parcelas.append({
            "numero": numero,
            "valor": format_valor(valor) if valor else "",
            "vencimento": format_data(base + timedelta(days=dia)),
        })
    return parcelas


def validate_lancamento(editor: dict) -> dict[str, str]:
    errors = {}
    if not editor["tipo_documento_id"].isdigit():
        errors["tipo_documento_id"] = "Escolha o tipo de documento."
    if editor["pessoa_id"] == "__nova__":
        digits = only_digits(editor.get("pessoa_documento", ""))
        if not (valid_cpf(digits) or valid_cnpj(digits)):
            errors["pessoa_id"] = "A nota não trouxe um CPF ou CNPJ válido."
        elif not editor.get("pessoa_nome"):
            errors["pessoa_id"] = "A nota não trouxe o nome do fornecedor ou do cliente."
    elif not str(editor["pessoa_id"]).isdigit():
        errors["pessoa_id"] = "Escolha a pessoa."
    if not editor["numero_documento"]:
        errors["numero_documento"] = "Informe o número do documento."
    elif len(editor["numero_documento"]) > 40:
        errors["numero_documento"] = "Use no máximo 40 caracteres."
    if not editor["forma_pagamento_id"].isdigit():
        errors["forma_pagamento_id"] = "Escolha a forma de pagamento."
    if not editor["condicao_pagamento_id"].isdigit():
        errors["condicao_pagamento_id"] = "Escolha a condição de pagamento."
    if not editor["qtde_parcelas"].isdigit() or not 1 <= int(editor["qtde_parcelas"]) <= 48:
        errors["qtde_parcelas"] = "Informe de 1 a 48 parcelas."
    total = parse_valor(editor["valor_total"])
    if total is None:
        errors["valor_total"] = "Informe o valor total."
    if parse_data(editor["data_emissao"]) is None:
        errors["data_emissao"] = "Informe a data de emissão."
    qtde = int(editor["qtde_parcelas"]) if editor["qtde_parcelas"].isdigit() else 0
    if qtde and len(editor["parcelas"]) != qtde:
        errors["parcelas"] = "Recalcule as parcelas antes de gerar os títulos."
    soma = Decimal("0.00")
    for parcela in editor["parcelas"]:
        valor = parse_valor(str(parcela["valor"]))
        if valor is None or parse_data(parcela["vencimento"]) is None:
            errors["parcelas"] = "Informe valor e vencimento em todas as parcelas."
            break
        soma += valor
    if total is not None and "parcelas" not in errors and soma != total:
        errors["parcelas"] = "A soma das parcelas precisa ser igual ao valor total."
    return errors


def garantir_pessoa(conn, documento: str, nome: str, natureza: str) -> int | None:
    digits = only_digits(documento)
    if not (valid_cpf(digits) or valid_cnpj(digits)):
        return None
    papel_alvo = "fornecedor" if natureza == "pagar" else "cliente"
    row = conn.execute("SELECT id, papel FROM pessoa WHERE documento = %s", (digits,)).fetchone()
    if row:
        if row[1] not in ("ambos", papel_alvo):
            conn.execute(
                "UPDATE pessoa SET papel = 'ambos', updated_at = now() WHERE id = %s",
                (row[0],),
            )
        return row[0]
    if not nome:
        return None
    tipo = "J" if len(digits) == 14 else "F"
    return conn.execute(
        """
        INSERT INTO pessoa (tipo, documento, nome, fantasia, papel)
        VALUES (%s, %s, %s, '', %s)
        RETURNING id
        """,
        (tipo, digits, nome[:160], papel_alvo),
    ).fetchone()[0]


def aplicar_nota(editor: dict, parsed: dict | None, conn, natureza: str, origem: str) -> str:
    if not parsed:
        return "Não foi possível ler os dados da nota nesse arquivo."
    if parsed.get("numero") and not editor["numero_documento"]:
        editor["numero_documento"] = str(parsed["numero"])[:40]
    if parsed.get("emissao"):
        editor["data_emissao"] = format_data(parsed["emissao"]) or editor["data_emissao"]
    if parsed.get("valor") and not editor["valor_total"]:
        editor["valor_total"] = format_valor(parsed["valor"])
    if not editor["tipo_documento_id"]:
        tipo = conn.execute("SELECT id FROM tipo_documento WHERE nome = 'NFe'").fetchone()
        if tipo:
            editor["tipo_documento_id"] = str(tipo[0])
    documento = parsed.get("emit_doc") if natureza == "pagar" else (parsed.get("dest_doc") or parsed.get("emit_doc"))
    nome = parsed.get("emit_nome") if natureza == "pagar" else (parsed.get("dest_nome") or parsed.get("emit_nome"))
    aviso = ""
    if documento and (valid_cpf(documento) or valid_cnpj(documento)):
        pessoa = conn.execute(
            "SELECT id, nome FROM pessoa WHERE documento = %s",
            (documento,),
        ).fetchone()
        editor["pessoa_documento"] = documento
        editor["pessoa_nome"] = (nome or (pessoa[1] if pessoa else ""))[:160]
        if pessoa:
            editor["pessoa_id"] = str(pessoa[0])
            editor["pessoa_rotulo"] = f"{pessoa[1]} · {format_documento(documento)}"
        else:
            editor["pessoa_id"] = "__nova__"
            if not editor["pessoa_nome"]:
                aviso = "A nota não trouxe o nome do fornecedor ou do cliente."
    else:
        aviso = "Não encontrei um CPF ou CNPJ válido na nota."
    if parsed.get("dups"):
        editor["qtde_parcelas"] = str(len(parsed["dups"]))
        editor["parcelas"] = [
            {
                "numero": numero,
                "valor": format_valor(dup.get("valor", "")),
                "vencimento": format_data(dup.get("vencimento", "")),
            }
            for numero, dup in enumerate(parsed["dups"], start=1)
        ]
    elif editor["valor_total"]:
        editor["parcelas"] = sugerir_parcelas(editor, "")
    editor["origem"] = origem
    return aviso


def aplicar_arquivo(editor: dict, data: bytes, filename: str, conn, natureza: str) -> str:
    nome = (filename or "").lower()
    if nome.endswith(".xml") or data.lstrip().startswith(b"<"):
        return aplicar_nota(editor, parse_nfe(data), conn, natureza, "xml")
    if nome.endswith(".pdf"):
        parsed = parse_pdf_nota(data)
        if parsed is None:
            editor["origem"] = "pdf"
            return "Não foi possível ler o PDF. Se for uma imagem, preencha número, pessoa e valor."
        return aplicar_nota(editor, parsed, conn, natureza, "pdf")
    return "Envie um arquivo XML ou PDF."


def list_lancamentos(conn, natureza: str, q: str):
    if not q:
        return conn.execute(
            """
            SELECT l.id, l.numero_documento, l.situacao, l.valor_total, l.qtde_parcelas, p.nome
            FROM lancamento l
            LEFT JOIN pessoa p ON p.id = l.pessoa_id
            WHERE l.natureza = %s
            ORDER BY l.id DESC
            """,
            (natureza,),
        ).fetchall()
    like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    return conn.execute(
        """
        SELECT l.id, l.numero_documento, l.situacao, l.valor_total, l.qtde_parcelas, p.nome
        FROM lancamento l
        LEFT JOIN pessoa p ON p.id = l.pessoa_id
        WHERE l.natureza = %s
          AND (l.numero_documento ILIKE %s ESCAPE '\\' OR coalesce(p.nome, '') ILIKE %s ESCAPE '\\')
        ORDER BY l.id DESC
        """,
        (natureza, like, like),
    ).fetchall()


def load_lancamento(conn, item_id: int, natureza: str) -> dict | None:
    row = conn.execute(
        """
        SELECT id, origem, situacao, tipo_documento_id, pessoa_id, numero_documento,
               forma_pagamento_id, condicao_pagamento_id, qtde_parcelas, valor_total,
               data_emissao, observacao, arquivo_nome
        FROM lancamento
        WHERE id = %s AND natureza = %s
        """,
        (item_id, natureza),
    ).fetchone()
    if row is None:
        return None
    editor = blank_lancamento(natureza)
    editor.update({
        "id": str(row[0]),
        "origem": row[1],
        "situacao": row[2],
        "tipo_documento_id": str(row[3] or ""),
        "pessoa_id": str(row[4] or ""),
        "numero_documento": row[5],
        "forma_pagamento_id": str(row[6] or ""),
        "condicao_pagamento_id": str(row[7] or ""),
        "qtde_parcelas": str(row[8]),
        "valor_total": format_valor(row[9]),
        "data_emissao": format_data(row[10]),
        "observacao": row[11],
        "arquivo_nome": row[12],
        "titulo": "Editar entrada",
    })
    parcelas = conn.execute(
        """
        SELECT numero_parcela, valor, vencimento FROM titulo
        WHERE lancamento_id = %s ORDER BY numero_parcela
        """,
        (item_id,),
    ).fetchall()
    if not parcelas:
        parcelas = conn.execute(
            """
            SELECT numero, valor, vencimento FROM lancamento_parcela
            WHERE lancamento_id = %s ORDER BY numero
            """,
            (item_id,),
        ).fetchall()
    if parcelas:
        editor["parcelas"] = [
            {"numero": item[0], "valor": format_valor(item[1]), "vencimento": format_data(item[2])}
            for item in parcelas
        ]
        editor["qtde_parcelas"] = str(len(editor["parcelas"]))
    return editor


def salvar_lancamento(conn, editor: dict, arquivo: dict | None, gerar: bool) -> int:
    item_id = ui().parse_record_id(editor["id"])
    if editor.get("pessoa_id") == "__nova__":
        novo = garantir_pessoa(
            conn, editor.get("pessoa_documento", ""), editor.get("pessoa_nome", ""), editor["natureza"]
        )
        editor["pessoa_id"] = str(novo or "")
    elif str(editor.get("pessoa_id", "")).isdigit():
        papel_alvo = "fornecedor" if editor["natureza"] == "pagar" else "cliente"
        atual = conn.execute("SELECT papel FROM pessoa WHERE id = %s", (int(editor["pessoa_id"]),)).fetchone()
        if atual and atual[0] not in ("ambos", papel_alvo):
            conn.execute(
                "UPDATE pessoa SET papel = 'ambos', updated_at = now() WHERE id = %s",
                (int(editor["pessoa_id"]),),
            )
    valores = (
        editor["natureza"], editor["origem"], "confirmado" if gerar else "rascunho",
        int(editor["tipo_documento_id"] or 0) or None,
        int(editor["pessoa_id"] or 0) or None,
        editor["numero_documento"],
        int(editor["forma_pagamento_id"] or 0) or None,
        int(editor["condicao_pagamento_id"] or 0) or None,
        int(editor["qtde_parcelas"]),
        parse_valor(editor["valor_total"]) or Decimal("0.00"),
        parse_data(editor["data_emissao"]),
        editor["observacao"],
    )
    if item_id:
        conn.execute(
            """
            UPDATE lancamento SET
                origem = %s, situacao = %s, tipo_documento_id = %s, pessoa_id = %s,
                numero_documento = %s, forma_pagamento_id = %s, condicao_pagamento_id = %s,
                qtde_parcelas = %s, valor_total = %s, data_emissao = %s, observacao = %s,
                updated_at = now()
            WHERE id = %s AND natureza = %s
            """,
            (*valores[1:], item_id, editor["natureza"]),
        )
    else:
        item_id = conn.execute(
            """
            INSERT INTO lancamento (
                natureza, origem, situacao, tipo_documento_id, pessoa_id, numero_documento,
                forma_pagamento_id, condicao_pagamento_id, qtde_parcelas, valor_total,
                data_emissao, observacao
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            valores,
        ).fetchone()[0]
    if arquivo and arquivo.get("data"):
        conn.execute(
            """
            UPDATE lancamento
            SET arquivo_nome = %s, arquivo_tipo = %s, arquivo = %s
            WHERE id = %s
            """,
            (arquivo["filename"][:180], arquivo.get("type") or "", arquivo["data"], item_id),
        )
    conn.execute("DELETE FROM lancamento_parcela WHERE lancamento_id = %s", (item_id,))
    for parcela in editor["parcelas"]:
        conn.execute(
            """
            INSERT INTO lancamento_parcela (lancamento_id, numero, valor, vencimento)
            VALUES (%s, %s, %s, %s)
            """,
            (item_id, parcela["numero"], parse_valor(str(parcela["valor"])) or Decimal("0.00"), parse_data(parcela["vencimento"])),
        )
    if gerar:
        conn.execute("DELETE FROM titulo WHERE lancamento_id = %s", (item_id,))
        for parcela in editor["parcelas"]:
            conn.execute(
                """
                INSERT INTO titulo (
                    lancamento_id, natureza, pessoa_id, numero_documento, numero_parcela, valor, vencimento
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    item_id, editor["natureza"], int(editor["pessoa_id"]), editor["numero_documento"],
                    parcela["numero"], parse_valor(str(parcela["valor"])), parse_data(parcela["vencimento"]),
                ),
            )
    return item_id


def lancamento_page(conn, natureza: str, rows, q: str, aviso: str, erro: str, editor: dict) -> bytes:
    app = ui()
    path = PAGAR_PATH if natureza == "pagar" else RECEBER_PATH
    title = "Contas a pagar" if natureza == "pagar" else "Contas a receber"
    pessoa_label = "Fornecedor" if natureza == "pagar" else "Cliente"
    lede = "O XML ou o PDF preenche pessoa, número e valor. As parcelas nascem desses dados e ficam na grade de baixo."
    tipos, pessoas, formas, condicoes = carregar_apoio(conn, natureza)
    pessoas = list(pessoas)
    if editor.get("pessoa_id") == "__nova__" and editor.get("pessoa_nome"):
        rotulo = f"{editor['pessoa_nome']} · {format_documento(editor.get('pessoa_documento', ''))}"
        pessoas.insert(0, ("__nova__", rotulo, ""))
    elif editor.get("pessoa_id") and not any(str(item[0]) == str(editor["pessoa_id"]) for item in pessoas):
        pessoas.insert(0, (editor["pessoa_id"], editor.get("pessoa_rotulo") or "Pessoa da nota", ""))
    errors = editor.get("errors") or {}
    origem = editor.get("origem") or "manual"
    radios = []
    for value, label in (("manual", "Manual"), ("xml", "XML"), ("pdf", "PDF")):
        checked = " checked" if origem == value else ""
        radios.append(f'<label><input type="radio" name="origem" value="{value}"{checked}> {label}</label>')
    parcelas_html = []
    for parcela in editor["parcelas"]:
        numero = parcela["numero"]
        parcelas_html.append(
            "<tr>"
            f"<td>{numero}</td>"
            f'<td><input form="entrada" name="parcela_valor_{numero}" value="{app.esc(str(parcela["valor"]))}" inputmode="decimal"></td>'
            f'<td><input form="entrada" name="parcela_vencimento_{numero}" value="{app.esc(str(parcela["vencimento"]))}" placeholder="dd/mm/aaaa"></td>'
            "</tr>"
        )
    if editor.get("arquivo_nome") and editor.get("id"):
        arquivo_link = (
            f'<a class="hint" href="{ARQUIVO_PATH}?id={app.esc(str(editor["id"]))}">'
            f'{app.esc(editor["arquivo_nome"])}</a>'
        )
    elif editor.get("arquivo_nome"):
        arquivo_link = f'<span class="hint">{app.esc(editor["arquivo_nome"])}</span>'
    else:
        arquivo_link = ""
    form = f"""
<form id="entrada" class="record-form finance-form" method="post" action="{path}" enctype="multipart/form-data">
  <input type="hidden" name="id" value="{app.esc(editor.get("id", ""))}">
  <input type="hidden" name="q" value="{app.esc(q)}">
  <input type="hidden" name="arquivo_nome" value="{app.esc(editor.get("arquivo_nome", ""))}">
  <input type="hidden" name="pessoa_documento" value="{app.esc(editor.get("pessoa_documento", ""))}">
  <input type="hidden" name="pessoa_nome" value="{app.esc(editor.get("pessoa_nome", ""))}">
  <label>Tipo de documento<select name="tipo_documento_id">{opcoes([(item[0], item[1], "") for item in tipos], editor.get("tipo_documento_id", ""), "Selecione")}</select>{app.field_error(errors, "tipo_documento_id")}</label>
  <label class="span-2">{pessoa_label}<select name="pessoa_id">{opcoes(pessoas, editor.get("pessoa_id", ""), "Selecione")}</select>{app.field_error(errors, "pessoa_id")}</label>
  <label>Número<input name="numero_documento" maxlength="40" value="{app.esc(editor.get("numero_documento", ""))}">{app.field_error(errors, "numero_documento")}</label>
  <label>Valor total<input name="valor_total" id="valor-total" inputmode="decimal" placeholder="0,00" value="{app.esc(format_valor(editor.get("valor_total", "")) or editor.get("valor_total", ""))}">{app.field_error(errors, "valor_total")}</label>
  <label>Emissão<input name="data_emissao" id="emissao" placeholder="dd/mm/aaaa" value="{app.esc(editor.get("data_emissao", ""))}">{app.field_error(errors, "data_emissao")}</label>
  <label>Forma<select name="forma_pagamento_id">{opcoes(formas, editor.get("forma_pagamento_id", ""), "Selecione")}</select>{app.field_error(errors, "forma_pagamento_id")}</label>
  <label>Condição<select name="condicao_pagamento_id" id="condicao">{opcoes(condicoes, editor.get("condicao_pagamento_id", ""), "Selecione")}</select>{app.field_error(errors, "condicao_pagamento_id")}</label>
  <label>Parcelas<input name="qtde_parcelas" id="qtde" inputmode="numeric" value="{app.esc(str(editor.get("qtde_parcelas", "1")))}">{app.field_error(errors, "qtde_parcelas")}</label>
  <label>Observação<input name="observacao" maxlength="180" value="{app.esc(editor.get("observacao", ""))}"></label>
  <div class="campo span-2">
    <span>Arquivo {arquivo_link}</span>
    <div class="finance-origem">
      {''.join(radios)}
      <label class="file-btn">Escolher XML ou PDF<input type="file" name="arquivo" id="arquivo" accept=".xml,.pdf,application/pdf,text/xml"></label>
      <span class="hint" id="nome-arquivo"></span>
    </div>
  </div>
</form>
"""
    body_rows = []
    for item_id, numero, situacao, valor, qtde, nome in rows:
        selected = " is-selected" if str(item_id) == str(editor.get("id") or "") else ""
        pill = "Confirmado" if situacao == "confirmado" else "Rascunho"
        body_rows.append(
            f'<tr class="{selected.strip()}">'
            f"<td><strong>{app.esc(numero or '—')}</strong></td>"
            f"<td>{app.esc(nome or 'Sem pessoa')}</td>"
            f"<td class='num'>{app.esc(format_valor(valor))}</td>"
            f"<td class='num'>{qtde}</td>"
            f"<td><span class='pill{' off' if situacao != 'confirmado' else ''}'>{pill}</span></td>"
            f"<td>{app.row_actions(path, item_id, q, 'Excluir esta entrada e os títulos dela?')}</td></tr>"
        )
    table = (
        "<table class='grid'><thead><tr><th>Número</th><th>Pessoa</th><th class='num'>Valor</th><th class='num'>Parcelas</th><th>Situação</th><th>Ações</th></tr></thead><tbody>"
        + ("".join(body_rows) or "<tr><td colspan='6'>Nenhuma entrada encontrada.</td></tr>")
        + "</tbody></table>"
    )
    parcelas = (
        "<table class='grid parcelas-grid'><thead><tr><th>Parcela</th><th>Valor</th><th>Vencimento</th></tr></thead>"
        f"<tbody id='parcelas'>{''.join(parcelas_html)}</tbody></table>"
    )
    body = f"""
<div class="finance-desk">
<header class="page-head">
  <p class="eyebrow">FINANCEIRO</p>
  <h1>{app.esc(title)}</h1>
  <p class="lede">{app.esc(lede)}</p>
</header>
<section class="panel">
  <div class="panel-head"><h2>{app.esc(editor["titulo"])}</h2>
    <div class="form-actions">
      <button form="entrada" type="submit" name="acao" value="ler">Ler arquivo</button>
      <button form="entrada" type="submit" name="acao" value="gerar">Gerar títulos</button>
    </div>
  </div>
  {app.flashes_html(aviso, erro)}
  {form}
</section>
<section class="panel finance-list">
  <div class="panel-head"><h2>{app.esc(title)}</h2><span>{app.esc(app.count_label(len(rows)))}</span></div>
  <div class="grid-wrap">{table}</div>
</section>
<section class="panel finance-parcelas">
  <div class="panel-head"><h2>Parcelas</h2><span class="hint">Acompanham valor, emissão, condição e quantidade. Dá para ajustar uma célula antes de gerar os títulos.</span></div>
  <div class="grid-wrap">{parcelas}</div>
  {app.field_error(errors, "parcelas")}
</section>
</div>
<script>
function dataBR(texto) {{
  const m = /^(\\d{{2}})\\/(\\d{{2}})\\/(\\d{{4}})$/.exec((texto || "").trim());
  if (!m) return null;
  const dia = Number(m[1]), mes = Number(m[2]), ano = Number(m[3]);
  const data = new Date(ano, mes - 1, dia, 12);
  if (data.getFullYear() !== ano || data.getMonth() !== mes - 1 || data.getDate() !== dia) return null;
  return data;
}}
function formatData(data) {{
  return String(data.getDate()).padStart(2, "0") + "/" + String(data.getMonth() + 1).padStart(2, "0") + "/" + data.getFullYear();
}}
function parseValor(texto) {{
  let t = (texto || "").trim().replace("R$", "").replace(/\\s/g, "");
  if (!t) return 0;
  if (t.includes(",")) t = t.replace(/\\./g, "").replace(",", ".");
  else if (/^\\d{{1,3}}(\\.\\d{{3}})+$/.test(t)) t = t.replace(/\\./g, "");
  const n = Number(t);
  return Number.isFinite(n) ? n : 0;
}}
function formatValor(cents) {{
  const inteiro = Math.floor(Math.abs(cents) / 100);
  const frac = String(Math.abs(cents) % 100).padStart(2, "0");
  return String(inteiro).replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ".") + "," + frac;
}}
function gerarParcelas() {{
  const qtde = Math.max(1, Math.min(48, Number(document.getElementById("qtde").value || 1)));
  document.getElementById("qtde").value = String(qtde);
  const cents = Math.round(parseValor(document.getElementById("valor-total").value) * 100);
  const base = Math.floor(cents / qtde);
  const resto = cents - base * qtde;
  const option = document.getElementById("condicao").selectedOptions[0];
  let dias = (option && option.dataset.dias ? option.dataset.dias.split(",") : []).map(Number).filter((n) => !Number.isNaN(n));
  if (!dias.length) dias = qtde === 1 ? [0] : Array.from({{length: qtde}}, (_, i) => 30 * (i + 1));
  const inicio = dataBR(document.getElementById("emissao").value) || new Date();
  const alvo = document.getElementById("parcelas");
  alvo.innerHTML = "";
  for (let numero = 1; numero <= qtde; numero += 1) {{
    const extra = numero === qtde ? resto : 0;
    const valor = cents > 0 ? formatValor(base + extra) : "";
    const dia = dias[numero - 1] === undefined ? 30 * numero : dias[numero - 1];
    const vencimento = formatData(new Date(inicio.getFullYear(), inicio.getMonth(), inicio.getDate() + dia, 12));
    alvo.insertAdjacentHTML("beforeend",
      "<tr><td>" + numero + "</td><td><input form=\\"entrada\\" name=\\"parcela_valor_" + numero + "\\" value=\\"" + valor + "\\" inputmode=\\"decimal\\"></td><td><input form=\\"entrada\\" name=\\"parcela_vencimento_" + numero + "\\" value=\\"" + vencimento + "\\" placeholder=\\"dd/mm/aaaa\\"></td></tr>");
  }}
}}
document.getElementById("condicao").addEventListener("change", (event) => {{
  const option = event.target.selectedOptions[0];
  if (option && option.dataset.qtde) document.getElementById("qtde").value = option.dataset.qtde;
  gerarParcelas();
}});
["qtde", "valor-total", "emissao"].forEach((id) => {{
  document.getElementById(id).addEventListener("change", gerarParcelas);
  document.getElementById(id).addEventListener("input", gerarParcelas);
}});
document.getElementById("arquivo").addEventListener("change", (event) => {{
  const file = event.target.files && event.target.files[0];
  document.getElementById("nome-arquivo").textContent = file ? file.name : "";
  if (!file) return;
  const nome = file.name.toLowerCase();
  const origem = nome.endsWith(".pdf") ? "pdf" : (nome.endsWith(".xml") ? "xml" : "");
  if (origem) document.querySelector('input[name="origem"][value="' + origem + '"]').checked = true;
}});
</script>
"""
    return app.page(
        title, body, logged=True, current=path,
        search={"action": path, "q": q, "placeholder": "Número ou pessoa"},
        action={"href": path + "?acao=incluir" + (f"&q={q}" if q else ""), "label": "+ Nova entrada"},
    )


def handle_lancamento_get(handler, natureza: str) -> None:
    app = ui()
    params = handler.query()
    q = params.get("q", "").strip()[:80]
    path = PAGAR_PATH if natureza == "pagar" else RECEBER_PATH
    aviso = {
        "incluido": "Títulos gerados.",
        "alterado": "Entrada atualizada e títulos gerados de novo.",
        "lido": "Arquivo lido. Confira as parcelas antes de gerar os títulos.",
        "rascunho": "Rascunho salvo. Ajuste as parcelas e gere os títulos quando estiver certo.",
        "excluido": "Entrada excluída.",
    }.get(params.get("aviso", ""), "")
    erro = {"ausente": "Registro não encontrado."}.get(params.get("erro", ""), "")
    editor = blank_lancamento(natureza)
    with app.connect() as conn:
        if params.get("acao") == "editar":
            item_id = app.parse_record_id(params.get("id", ""))
            loaded = load_lancamento(conn, item_id, natureza) if item_id else None
            if loaded is None:
                erro = "Registro não encontrado."
            else:
                editor = loaded
        rows = list_lancamentos(conn, natureza, q)
        payload = lancamento_page(conn, natureza, rows, q, aviso, erro, editor)
    handler.send_html(200, payload)


def arquivo_do_pedido(handler) -> dict | None:
    arquivo = getattr(handler, "uploads", {}).get("arquivo")
    if not arquivo or not arquivo.get("filename") or not arquivo.get("data"):
        return None
    nome = arquivo["filename"].lower()
    if not nome.endswith((".xml", ".pdf")):
        return {"erro": "Envie um arquivo XML ou PDF."}
    return arquivo


def handle_lancamento_post(handler, form: dict, natureza: str) -> None:
    app = ui()
    path = PAGAR_PATH if natureza == "pagar" else RECEBER_PATH
    q = form.get("q", "").strip()[:80]
    if form.get("acao") == "excluir":
        item_id = app.parse_record_id(form.get("id", ""))
        if item_id is None:
            handler.redirect(path)
            return
        with app.connect() as conn:
            deleted = conn.execute(
                "DELETE FROM lancamento WHERE id = %s AND natureza = %s", (item_id, natureza)
            ).rowcount
            conn.commit()
        handler.redirect(f"{path}?aviso=excluido" if deleted else f"{path}?erro=ausente")
        return
    editor = lancamento_from_form(form, natureza)
    arquivo = arquivo_do_pedido(handler)
    aviso_xml = ""
    if isinstance(arquivo, dict) and arquivo.get("erro"):
        editor["errors"] = {"parcelas": arquivo["erro"]}
        arquivo = None
    elif arquivo:
        editor["arquivo_nome"] = arquivo["filename"]
        with app.connect() as conn:
            aviso_xml = aplicar_arquivo(editor, arquivo["data"], arquivo["filename"], conn, natureza)
    erros_arquivo = editor["errors"]
    gerar = form.get("acao") == "gerar"
    if gerar:
        editor["errors"] = {**erros_arquivo, **validate_lancamento(editor)}
    else:
        editor["errors"] = dict(erros_arquivo)
        if not editor["numero_documento"] and not arquivo and not erros_arquivo:
            editor["errors"]["numero_documento"] = "Envie um XML ou PDF, ou informe o documento."
    if editor["errors"] or (aviso_xml and gerar and not editor["pessoa_id"]):
        if aviso_xml and not editor["pessoa_id"]:
            editor["errors"]["pessoa_id"] = aviso_xml
        with app.connect() as conn:
            rows = list_lancamentos(conn, natureza, q)
            payload = lancamento_page(conn, natureza, rows, q, "", aviso_xml, editor)
        handler.send_html(400, payload)
        return
    if not gerar and not editor["parcelas"]:
        editor["parcelas"] = sugerir_parcelas(editor, "")
    if not str(editor["qtde_parcelas"]).isdigit() or not 1 <= int(editor["qtde_parcelas"]) <= 48:
        editor["qtde_parcelas"] = str(max(1, min(48, len(editor["parcelas"]) or 1)))
    try:
        with app.connect() as conn:
            existed = bool(app.parse_record_id(editor["id"]))
            item_id = salvar_lancamento(conn, editor, arquivo if isinstance(arquivo, dict) else None, gerar)
            conn.commit()
    except (psycopg.Error, ValueError):
        editor["errors"] = {"tipo_documento_id": "Não foi possível gravar. Confira tipo, pessoa, forma e condição."}
        with app.connect() as conn:
            rows = list_lancamentos(conn, natureza, q)
            payload = lancamento_page(conn, natureza, rows, q, "", "", editor)
        handler.send_html(400, payload)
        return
    if gerar:
        aviso = "alterado" if existed else "incluido"
    else:
        aviso = "lido" if arquivo else "rascunho"
    handler.redirect(f"{path}?acao=editar&id={item_id}&aviso={aviso}" if not gerar else f"{path}?aviso={aviso}")


def titulos_page(rows, q: str) -> bytes:
    app = ui()
    body_rows = []
    for natureza, nome, numero, parcela, valor, vencimento, status in rows:
        body_rows.append(
            "<tr>"
            f"<td>{'Pagar' if natureza == 'pagar' else 'Receber'}</td>"
            f"<td><strong>{app.esc(nome or '—')}</strong><br><span class='hint'>{app.esc(numero)}</span></td>"
            f"<td>{parcela}</td><td class='num'>{app.esc(format_valor(valor))}</td><td>{app.esc(format_data(vencimento))}</td>"
            f"<td><span class='pill'>{app.esc(status.capitalize())}</span></td></tr>"
        )
    table = (
        "<table class='grid'><thead><tr><th>Natureza</th><th>Pessoa</th><th>Parcela</th><th>Valor</th><th>Vencimento</th><th>Status</th></tr></thead><tbody>"
        + ("".join(body_rows) or "<tr><td colspan='6'>Nenhum título gerado.</td></tr>")
        + "</tbody></table>"
    )
    body = f"""
<header class="page-head">
  <p class="eyebrow">FINANCEIRO</p>
  <h1>Títulos</h1>
  <p class="lede">Os títulos nascem das entradas de contas a pagar e contas a receber.</p>
</header>
<section class="panel">
  <div class="panel-head"><h2>Títulos gerados</h2><span>{app.esc(app.count_label(len(rows)))}</span></div>
  <div class="grid-wrap">{table}</div>
</section>
"""
    return app.page(
        "Títulos", body, logged=True, current=TITULOS_PATH,
        search={"action": TITULOS_PATH, "q": q, "placeholder": "Pessoa ou documento"},
    )


def handle_titulos_get(handler) -> None:
    app = ui()
    q = handler.query().get("q", "").strip()[:80]
    with app.connect() as conn:
        if not q:
            rows = conn.execute(
                """
                SELECT t.natureza, p.nome, t.numero_documento, t.numero_parcela, t.valor, t.vencimento, t.status
                FROM titulo t
                LEFT JOIN pessoa p ON p.id = t.pessoa_id
                ORDER BY t.vencimento, t.id
                """
            ).fetchall()
        else:
            like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            rows = conn.execute(
                """
                SELECT t.natureza, p.nome, t.numero_documento, t.numero_parcela, t.valor, t.vencimento, t.status
                FROM titulo t
                LEFT JOIN pessoa p ON p.id = t.pessoa_id
                WHERE coalesce(p.nome, '') ILIKE %s ESCAPE '\\'
                   OR t.numero_documento ILIKE %s ESCAPE '\\'
                ORDER BY t.vencimento, t.id
                """,
                (like, like),
            ).fetchall()
    handler.send_html(200, titulos_page(rows, q))


def handle_arquivo_get(handler) -> None:
    app = ui()
    item_id = app.parse_record_id(handler.query().get("id", ""))
    if item_id is None:
        handler.send_html(404, app.page("Arquivo", "<h1>Arquivo não encontrado</h1>", logged=True))
        return
    with app.connect() as conn:
        row = conn.execute(
            "SELECT arquivo_nome, arquivo_tipo, arquivo FROM lancamento WHERE id = %s",
            (item_id,),
        ).fetchone()
    if row is None or not row[2]:
        handler.send_html(404, app.page("Arquivo", "<h1>Arquivo não encontrado</h1>", logged=True))
        return
    nome = re.sub(r"[^A-Za-z0-9._-]", "_", row[0] or "arquivo")
    body = bytes(row[2])
    handler.send_response(200)
    handler.send_header("Content-Type", row[1] or "application/octet-stream")
    handler.send_header("Content-Disposition", f'attachment; filename="{nome}"')
    handler.add_security_headers()
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def handle_financeiro_get(handler, path: str) -> None:
    if path == CONSULTA_PATH:
        handler.send_json(200, consultar_documento(handler.query().get("documento", "")))
        return
    if path == CEP_PATH:
        handler.send_json(200, consultar_cep(handler.query().get("cep", "")))
        return
    if path == ARQUIVO_PATH:
        handle_arquivo_get(handler)
        return
    if path == PESSOAS_PATH:
        handle_pessoas_get(handler)
        return
    if path == PAGAR_PATH:
        handle_lancamento_get(handler, "pagar")
        return
    if path == RECEBER_PATH:
        handle_lancamento_get(handler, "receber")
        return
    if path == TITULOS_PATH:
        handle_titulos_get(handler)
        return
    handler.send_html(404, ui().page("Não encontrado", "<h1>Página não encontrada</h1>", logged=True))


def handle_financeiro_post(handler, path: str, form: dict) -> None:
    if path == PESSOAS_PATH:
        handle_pessoas_post(handler, form)
        return
    if path == PAGAR_PATH:
        handle_lancamento_post(handler, form, "pagar")
        return
    if path == RECEBER_PATH:
        handle_lancamento_post(handler, form, "receber")
        return
    handler.send_html(404, ui().page("Não encontrado", "<h1>Página não encontrada</h1>", logged=True))
