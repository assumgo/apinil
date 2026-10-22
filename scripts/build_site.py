#!/usr/bin/env python3
"""Gera o site estático da Apinil a partir do conteúdo público de apinil.com.

O HTML do Wix não é copiado. Este script lê o snapshot público (sitemap + HTML
renderizado para buscadores), baixa as imagens da marca e grava páginas estáticas
em public/.
"""

from __future__ import annotations

import html
import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
IMG_DIR = PUBLIC / "assets" / "img"
FILE_DIR = PUBLIC / "assets" / "files"

SITEMAP = "https://www.apinil.com/pages-sitemap.xml"
UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
ORIGIN = "https://www.apinil.com"

NAV = [
    ("/about-1/", "Home"),
    ("/biography/", "Sobre"),
    ("/gallery/", "Produtos"),
    ("/catálogo/", "Catálogo"),
    ("/contact/", "Contatos"),
]

CATEGORIES = [
    ("/gel-massageador/", "Massageadores"),
    ("/cópia-sabonete-masculino/", "Corpo & Banho"),
    ("/cópia-corpo-banho/", "Skin Care"),
    ("/", "Pele"),
    ("/cópia-sabonete-feminino-1/", "Capilar"),
    ("/desodorante-e-colonia/", "Perfumaria"),
]

SKIP_MEDIA = {
    "510543_e00541a487834481b579fa4e6ea4e824~mv2.png",
    "11062b_1db239e728f641c3a3be5b7ca708f239~mv2.png",
    "11062b_2381e8a6e7444f4f902e7b649aa3f0ac~mv2.png",
    "11062b_55e4be1e75564866b6c28290f9a9d271~mv2.png",
    # textura de fundo do Wix, repetida em várias páginas
    "11062b_e55321c53b02478b9bafa28958160488f000.jpg",
}

NAV_LABELS = {
    "home",
    "sobre",
    "produtos",
    "catálogo",
    "catalogo",
    "contatos",
    "contato",
    "more",
    "ver mais produtos",
}

DETAIL_MARKERS = {
    "informações",
    "informacoes",
    "modo de usar",
    "composição",
    "composicao",
    "precaução",
    "precaucao",
}

FOOTER_BITS = (
    "3584",
    "hotmail",
    "aparecida de goiânia",
    "aparecida de goiania",
    "avenida joão",
    "avenida joao",
    "parque industrial",
    "cep:",
    "horário de funcionamento",
    "horario de funcionamento",
    "segunda à",
    "segunda a",
    "8:00hrs",
    "qd. 009",
    "telefone:",
    "e-mail",
)


def ascii_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(urllib.parse.unquote(parts.path), safe="/%")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def fetch(url: str, accept: str | None = None, timeout: int = 40) -> tuple[bytes, str]:
    headers = {"User-Agent": UA}
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(ascii_url(url), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        ctype = resp.headers.get("Content-Type", "")
    return data, ctype


def sitemap_urls() -> list[str]:
    raw, _ = fetch(SITEMAP)
    locs = re.findall(r"<loc>(.*?)</loc>", raw.decode("utf-8", "ignore"))
    # Stable, homepage first.
    def sort_key(url: str) -> tuple[int, str]:
        path = urllib.parse.urlparse(url).path or "/"
        return (0 if path == "/" else 1, path)

    return sorted(set(locs), key=sort_key)


def local_path(url_path: str) -> str:
    path = urllib.parse.unquote(url_path.split("?")[0])
    if path in ("", "/"):
        return "/"
    return "/" + path.strip("/") + "/"


def norm(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return folded.casefold()


def is_footer(text: str) -> bool:
    n = norm(text)
    return any(bit in n for bit in FOOTER_BITS)


def is_section(key: str) -> bool:
    return any(marker in key for marker in DETAIL_MARKERS)


def is_paragraph(text: str) -> bool:
    if len(text) > 110:
        return True
    if text.endswith((".", "!", "?", "…")):
        return True
    if text.count(" ") > 14:
        return True
    return False


def dims(src: str) -> tuple[int, int]:
    w = re.search(r"[/,]w_(\d+)", src)
    h = re.search(r"[/,]h_(\d+)", src)
    return (int(w.group(1)) if w else 0, int(h.group(1)) if h else 0)


class Stream(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.events: list[tuple] = []
        self.in_rich = 0
        self.rich_buf: list[str] = []
        self.in_a = False
        self.a_href = ""
        self.a_buf: list[str] = []
        self.in_title = False
        self.title = ""
        self.pdfs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        d = {k: (v or "") for k, v in attrs}
        if tag in ("script", "style"):
            self.skip += 1
            return
        if self.skip:
            return
        if tag == "title":
            self.in_title = True
        cls = d.get("class", "")
        if tag == "div" and "wixui-rich-text" in cls and "wixui-rich-text__text" not in cls:
            self.in_rich += 1
            if self.in_rich == 1:
                self.rich_buf = []
        if tag == "a":
            href = d.get("href", "")
            if href:
                self.in_a = True
                self.a_href = href
                self.a_buf = []
                if ".pdf" in href.lower() or "wixstatic.com/ugd" in href:
                    self.pdfs.append(html.unescape(href))
        if tag == "img":
            src = d.get("src") or d.get("data-src") or ""
            alt = d.get("alt") or ""
            match = re.search(r"/media/([^/]+)", src)
            if not match or "wixstatic.com/media" not in src:
                return
            media_id = urllib.parse.unquote(match.group(1))
            if media_id in SKIP_MEDIA:
                return
            if "~mv2" not in media_id and not media_id.startswith("11062b_"):
                return
            width, height = dims(src)
            if 0 < width < 90 and 0 < height < 90:
                return
            self.events.append(("img", alt.strip(), src, media_id, width, height))

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
            return
        if tag == "title":
            self.in_title = False
        if tag == "div" and self.in_rich:
            self.in_rich -= 1
            if self.in_rich == 0:
                text = re.sub(r"\s+", " ", " ".join(self.rich_buf)).strip()
                self.rich_buf = []
                if text and not is_footer(text) and norm(text) not in NAV_LABELS:
                    if norm(text) not in {"top of page", "bottom of page"}:
                        self.events.append(("text", text))
        if tag == "a" and self.in_a:
            label = re.sub(r"\s+", " ", " ".join(self.a_buf)).strip()
            href = html.unescape(self.a_href)
            self.in_a = False
            if href.startswith(ORIGIN) or href.startswith("/"):
                self.events.append(("link", label, href))

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title += data
        if self.skip:
            return
        if self.in_rich:
            self.rich_buf.append(data)
        if self.in_a:
            self.a_buf.append(data)


def page_title(raw: str, path: str) -> str:
    title = raw.split("|")[0].strip() or "Apinil"
    if path == "/":
        return "Pele"
    if path == "/about-1/":
        return "Home"
    if path == "/gallery/":
        return "Produtos"
    if path == "/biography/":
        return "Sobre"
    if path == "/contact/":
        return "Contatos"
    if path == "/catálogo/":
        return "Catálogo"
    return title


def extract_page(url: str) -> dict:
    path = local_path(urllib.parse.urlparse(url).path)
    data, _ = fetch(url)
    parser = Stream()
    parser.feed(data.decode("utf-8", "ignore"))
    title = page_title(parser.title, path)
    return {
        "url": url,
        "path": path,
        "title": title,
        "events": parser.events,
        "pdfs": parser.pdfs,
    }


def safe_name(media_id: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", media_id)
    return base[:120]


def download_images(pages: list[dict]) -> dict[str, str]:
    wanted: dict[str, str] = {}
    for page in pages:
        for event in page["events"]:
            if event[0] == "img":
                wanted[event[3]] = event[2]
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, str] = {}

    def one(item: tuple[str, str]) -> tuple[str, str | None]:
        media_id, _src = item
        dest_stem = safe_name(media_id)
        existing = list(IMG_DIR.glob(dest_stem + ".*"))
        if existing:
            return media_id, f"/assets/img/{existing[0].name}"
        url = (
            "https://static.wixstatic.com/media/"
            + urllib.parse.quote(media_id)
            + "/v1/fit/w_1100,h_1100,q_72,enc_auto/file.webp"
        )
        try:
            blob, ctype = fetch(url, accept="image/webp,image/png,image/jpeg,*/*", timeout=50)
        except Exception as exc:  # noqa: BLE001
            print("  imagem falhou", media_id, exc)
            return media_id, None
        if len(blob) < 400:
            return media_id, None
        ext = ".webp"
        if "png" in ctype:
            ext = ".png"
        elif "jpeg" in ctype or "jpg" in ctype:
            ext = ".jpg"
        elif blob[:4] == b"\x89PNG":
            ext = ".png"
        elif blob[:3] == b"\xff\xd8\xff":
            ext = ".jpg"
        name = dest_stem + ext
        (IMG_DIR / name).write_bytes(blob)
        return media_id, f"/assets/img/{name}"

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(one, item) for item in wanted.items()]
        done = 0
        for future in as_completed(futures):
            media_id, rel = future.result()
            done += 1
            if rel:
                mapping[media_id] = rel
            if done % 20 == 0:
                print(f"  imagens {done}/{len(wanted)}")
    print(f"  imagens {len(mapping)}/{len(wanted)}")
    return mapping


def download_logo() -> str:
    media_id = "510543_e00541a487834481b579fa4e6ea4e824~mv2.png"
    url = (
        "https://static.wixstatic.com/media/"
        + media_id
        + "/v1/fit/w_420,h_220,q_85/logo.png"
    )
    blob, _ = fetch(url, accept="image/png")
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    (IMG_DIR / "logo.png").write_bytes(blob)
    return "/assets/img/logo.png"


def download_pdfs(pages: list[dict]) -> dict[str, str]:
    FILE_DIR.mkdir(parents=True, exist_ok=True)
    saved: dict[str, str] = {}
    seen: set[str] = set()
    for page in pages:
        for pdf in page["pdfs"]:
            if pdf in seen:
                continue
            seen.add(pdf)
            absolute = pdf if pdf.startswith("http") else urllib.parse.urljoin(ORIGIN, pdf)
            try:
                blob, _ = fetch(absolute, timeout=60)
            except Exception as exc:  # noqa: BLE001
                print("  pdf falhou", absolute, exc)
                continue
            if len(blob) < 1000 or blob[:4] != b"%PDF":
                continue
            name = f"catalogo-{len(saved)+1}.pdf"
            (FILE_DIR / name).write_bytes(blob)
            saved[pdf] = f"/assets/files/{name}"
    return saved


def build_blocks(page: dict, images: dict[str, str], titles: dict[str, str]) -> list[dict]:
    path = page["path"]
    cards: list[dict] = []
    queue: list[dict] = []
    used_captions: set[str] = set()
    used_media: set[str] = set()
    seen_text: set[str] = set()
    hero = None
    blocks: list[dict] = []
    saw_text = False

    def flush_queue(as_gallery: bool = False) -> None:
        nonlocal queue
        ready = []
        for card in queue:
            if card.get("caption") or as_gallery or card.get("src"):
                if not card.get("caption") and not as_gallery:
                    ready.append(card)
                else:
                    ready.append(card)
        if ready:
            cards.extend(ready)
            blocks.append({"type": "grid", "cards": ready})
        queue = []

    def close_open_images(force_gallery: bool) -> None:
        nonlocal queue
        if not queue:
            return
        if force_gallery or any(c.get("caption") for c in queue):
            # Uncaptioned leftovers stay as image tiles.
            flush_queue(as_gallery=True)
        else:
            flush_queue(as_gallery=True)

    for event in page["events"]:
        kind = event[0]
        if kind == "img":
            _alt, _src, media_id, width, height = event[1], event[2], event[3], event[4], event[5]
            rel = images.get(media_id)
            if not rel or media_id in used_media:
                continue
            if hero is None and path == "/" and not saw_text and width >= 500:
                hero = rel
                used_media.add(media_id)
                continue
            used_media.add(media_id)
            card = {"src": rel, "alt": _alt or page["title"], "caption": None, "href": None, "media": media_id}
            queue.append(card)
            continue

        if kind == "text":
            text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", event[1]).strip()
            key = norm(text)
            if not text or key in NAV_LABELS:
                continue
            if re.fullmatch(r"\d+", text):
                continue
            if key in {"enviar", "sua mensagem foi enviada com sucesso. obrigado!"}:
                continue
            if key in seen_text:
                if not is_paragraph(text):
                    target = next((c for c in queue if not c.get("caption")), None)
                    if target is not None:
                        queue.remove(target)
                continue
            saw_text = True
            if is_section(key):
                close_open_images(True)
                blocks.append({"type": "heading", "text": text.strip()})
                seen_text.add(key)
                continue
            category_hrefs = {href for href, _label in CATEGORIES}
            if queue and not is_paragraph(text):
                target = next(
                    (c for c in queue if not c.get("caption") and c.get("href") not in category_hrefs),
                    None,
                )
                if target is None:
                    close_open_images(True)
                    heading = text
                    if norm(page["title"]) and key.startswith(norm(page["title"]) + " "):
                        heading = text[len(page["title"]) :].strip(" -–")
                    if heading and norm(heading) != norm(page["title"]):
                        blocks.append({"type": "heading", "text": heading})
                    continue
                if key in used_captions:
                    queue.remove(target)
                    continue
                target["caption"] = text
                if target["alt"] in ("", page["title"]) or norm(target["alt"]).startswith("design sem nome"):
                    target["alt"] = text
                used_captions.add(key)
                seen_text.add(key)
                continue
            seen_text.add(key)
            close_open_images(True)
            if is_paragraph(text):
                blocks.append({"type": "p", "text": text})
            else:
                heading = text
                if norm(page["title"]) and key.startswith(norm(page["title"]) + " "):
                    heading = text[len(page["title"]) :].strip(" -–")
                if heading and norm(heading) != norm(page["title"]):
                    blocks.append({"type": "heading", "text": heading})
            continue

        if kind == "link":
            label, href = event[1], event[2]
            label_n = norm(label)
            target = local_path(urllib.parse.urlparse(href).path or "/")
            if target in {"/about-1/", "/biography/", "/gallery/", "/catálogo/", "/contact/", "/blog/"}:
                continue
            if label_n in NAV_LABELS:
                continue
            if target == path:
                continue
            if target == "/" and label_n != "pele":
                continue
            if target not in titles and target != "/":
                continue
            assigned = False
            if label_n:
                for card in reversed(queue):
                    cap = norm(card.get("caption") or "")
                    if cap and (label_n in cap or cap in label_n):
                        card["href"] = target
                        assigned = True
                        break
            if not assigned:
                for card in reversed(queue):
                    if not card.get("href"):
                        card["href"] = target
                        assigned = True
                        break
            if not assigned and label_n and label_n not in used_captions:
                blocks.append({"type": "textlink", "text": label, "href": target})

    close_open_images(True)

    # Cross-link captions to product pages with the same name.
    for block in blocks:
        if block["type"] != "grid":
            continue
        for card in block["cards"]:
            if card.get("href"):
                continue
            caption = norm(card.get("caption") or "")
            if len(caption) < 4:
                continue
            best = None
            best_len = 0
            for tpath, ttitle in titles.items():
                if tpath == path or tpath in {n[0] for n in NAV}:
                    continue
                tn = norm(ttitle)
                if len(tn) < 4:
                    continue
                if tn == caption or (len(tn) >= 8 and (tn in caption or caption in tn)):
                    if len(tn) > best_len:
                        best = tpath
                        best_len = len(tn)
            if best:
                card["href"] = best

    if hero:
        blocks.insert(0, {"type": "hero", "src": hero, "alt": page["title"]})

    # Drop empty grids
    cleaned = []
    for block in blocks:
        if block["type"] == "grid":
            cards = [c for c in block["cards"] if c.get("src")]
            if cards:
                cleaned.append({**block, "cards": cards})
            continue
        cleaned.append(block)
    return cleaned


def is_detail(blocks: list[dict]) -> bool:
    headings = [norm(b["text"]) for b in blocks if b["type"] == "heading"]
    return any(h in DETAIL_MARKERS or any(m in h for m in DETAIL_MARKERS) for h in headings)


def esc(text: str) -> str:
    return html.escape(text, quote=True)


def render_header(path: str, logo: str) -> str:
    nav = []
    for href, label in NAV:
        current = ' aria-current="page"' if href == path else ""
        nav.append(f'<a href="{esc(href)}"{current}>{esc(label)}</a>')
    cats = []
    for href, label in CATEGORIES:
        current = ' aria-current="page"' if href == path else ""
        cats.append(f'<a href="{esc(href)}"{current}>{esc(label)}</a>')
    return f"""
<header class="site-header">
  <div class="header-inner">
    <a class="brand" href="/about-1/">
      <img src="{esc(logo)}" alt="Apinil cosméticos">
    </a>
    <button class="nav-toggle" type="button" aria-expanded="false" aria-controls="site-nav">Menu</button>
    <nav id="site-nav" class="site-nav">{''.join(nav)}</nav>
  </div>
  <nav class="cat-nav" aria-label="Linhas">{''.join(cats)}</nav>
</header>
"""


def render_footer() -> str:
    return """
<footer class="site-footer">
  <div class="footer-grid">
    <section>
      <h2>Contato</h2>
      <p><a href="tel:+556235845787">(62) 3584-5787</a></p>
      <p><a href="mailto:apinilindustria@hotmail.com">apinilindustria@hotmail.com</a></p>
      <p class="social">
        <a href="https://www.instagram.com/apinilindustria/" rel="noopener">Instagram</a>
        <a href="https://www.facebook.com/apinilindustria" rel="noopener">Facebook</a>
        <a href="https://api.whatsapp.com/send?phone=556235845787&amp;text=Ol%C3%A1%20Apinil%2C%20vim%20pelo%20site." rel="noopener">WhatsApp</a>
      </p>
    </section>
    <section>
      <h2>Fábrica</h2>
      <p>Aparecida de Goiânia - GO<br>
      Avenida João Rodolfo de Oliveira<br>
      Qd. 009, Lt. 0008, S/N<br>
      Parque Industrial Vice Presidente José Alencar<br>
      CEP 74993-515</p>
    </section>
    <section>
      <h2>Horário</h2>
      <p>Segunda à sexta<br>8:00 às 18:00</p>
    </section>
  </div>
  <p class="legal">Apinil Indústria de Cosméticos · site estático para instalação própria.</p>
</footer>
<a class="whatsapp" href="https://api.whatsapp.com/send?phone=556235845787&amp;text=Ol%C3%A1%20Apinil%2C%20vim%20pelo%20site.">
  <span>Vamos conversar por chat</span>
</a>
<script src="/assets/js/site.js"></script>
"""


def render_cards(cards: list[dict]) -> str:
    items = []
    for card in cards:
        caption = card.get("caption") or ""
        alt = card.get("alt") or caption or "Produto Apinil"
        if card.get("src"):
            img = f'<img src="{esc(card["src"])}" alt="{esc(alt)}" loading="lazy">'
        else:
            img = f'<div class="card-fallback" role="img" aria-label="{esc(alt)}"></div>'
        body = img
        if caption:
            body += f'<p>{esc(caption)}</p>'
        if card.get("href"):
            label = caption or alt
            items.append(f'<a class="card" href="{esc(card["href"])}">{body}<span class="sr">{esc(label)}</span></a>')
        else:
            items.append(f'<article class="card">{body}</article>')
    return f'<div class="product-grid">{"".join(items)}</div>'


def render_contact_form() -> str:
    return """
<section class="contact-panel">
  <h2>Fale conosco</h2>
  <p>Envie uma mensagem pelo WhatsApp ou pelo seu aplicativo de e-mail. O atendimento da fábrica é de segunda a sexta, das 8h às 18h.</p>
  <form id="contact-form">
    <label>Nome<input name="nome" required autocomplete="name"></label>
    <label>E-mail<input name="email" type="email" required autocomplete="email"></label>
    <label>Telefone<input name="telefone" autocomplete="tel"></label>
    <label>Mensagem<textarea name="mensagem" rows="5" required></textarea></label>
    <div class="form-actions">
      <button type="submit">Enviar no WhatsApp</button>
      <button type="button" id="mailto-btn" class="secondary">Enviar por e-mail</button>
    </div>
  </form>
</section>
"""


def render_page(page: dict, blocks: list[dict], logo: str, pdf_map: dict[str, str]) -> str:
    path = page["path"]
    title = page["title"]
    detail = is_detail(blocks) and path not in {p for p, _ in CATEGORIES} | {n[0] for n in NAV}
    description = ""
    for block in blocks:
        if block["type"] == "p":
            description = block["text"]
            break
    if not description:
        description = f"{title} · Apinil Indústria de Cosméticos"

    main_parts: list[str] = [f'<h1>{esc(title)}</h1>']
    if detail:
        images = []
        for block in blocks:
            if block["type"] == "hero":
                images.append((block["src"], block.get("alt") or title))
            elif block["type"] == "banner":
                images.append((block["src"], block.get("alt") or title))
            elif block["type"] == "grid":
                for card in block["cards"]:
                    images.append((card["src"], card.get("alt") or title))
        gallery = ""
        if images:
            thumbs = "".join(
                f'<button type="button" data-src="{esc(src)}" aria-label="Ver imagem"><img src="{esc(src)}" alt=""></button>'
                for src, _alt in images
            )
            gallery = (
                f'<div class="product-photo"><img id="product-main" src="{esc(images[0][0])}" alt="{esc(images[0][1])}">'
                f'<div class="thumbs">{thumbs}</div></div>'
            )
        info = []
        current: list[str] | None = None
        sections: list[tuple[str, list[str]]] = []
        for block in blocks:
            if block["type"] == "heading":
                current = []
                sections.append((block["text"], current))
            elif block["type"] == "p" and current is not None:
                current.append(block["text"])
            elif block["type"] == "p":
                sections.append(("", [block["text"]]))
            elif block["type"] == "textlink":
                sections.append(("", [f'__link__{block["href"]}__{block["text"]}']))
        for heading_text, paras in sections:
            chunk = ""
            if heading_text:
                chunk += f"<h2>{esc(heading_text)}</h2>"
            for para in paras:
                if para.startswith("__link__"):
                    href, label = para[len("__link__") :].split("__", 1)
                    chunk += f'<p><a href="{esc(href)}">{esc(label)}</a></p>'
                else:
                    chunk += f"<p>{esc(para)}</p>"
            info.append(chunk)
        info.append('<p class="more"><a href="/gallery/">Ver mais produtos</a></p>')
        main_parts = [
            f'<article class="product-detail">{gallery}<div class="product-copy"><h1>{esc(title)}</h1>{"".join(info)}</div></article>'
        ]
    else:
        for block in blocks:
            if block["type"] == "hero":
                main_parts.insert(0, f'<img class="hero" src="{esc(block["src"])}" alt="{esc(block.get("alt") or title)}">')
                # h1 should follow hero
                continue
            if block["type"] == "banner":
                main_parts.append(f'<img class="banner" src="{esc(block["src"])}" alt="{esc(block.get("alt") or "")}">')
            elif block["type"] == "heading":
                main_parts.append(f'<h2 class="section-title">{esc(block["text"])}</h2>')
            elif block["type"] == "p":
                main_parts.append(f'<p class="prose">{esc(block["text"])}</p>')
            elif block["type"] == "grid":
                main_parts.append(render_cards(block["cards"]))
            elif block["type"] == "textlink":
                main_parts.append(f'<p class="prose"><a href="{esc(block["href"])}">{esc(block["text"])}</a></p>')
        if path == "/contact/":
            main_parts.append(render_contact_form())
        if path == "/catálogo/":
            local_pdf = PUBLIC / "assets" / "files" / "catalogo-apinil.pdf"
            mapped = next((pdf_map[pdf] for pdf in page["pdfs"] if pdf in pdf_map), None)
            href = mapped or ("/assets/files/catalogo-apinil.pdf" if local_pdf.exists() else "")
            if href:
                box = f'<a class="catalog-btn" href="{esc(href)}" download>Baixar catálogo (PDF)</a>'
            else:
                box = (
                    "<p>O arquivo PDF do catálogo não veio no snapshot público. "
                    "Coloque o arquivo em <code>public/assets/files/catalogo-apinil.pdf</code> "
                    "e gere a imagem de novo, ou peça o PDF pelo WhatsApp.</p>"
                )
            main_parts.append(f'<section class="catalog-box">{box}</section>')

    # Move hero to the front if it was inserted at 0 after h1 was already there.
    heroes = [part for part in main_parts if part.startswith("<img class=\"hero\"")]
    rest = [part for part in main_parts if part not in heroes]
    main_html = "".join(heroes + rest)

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)} | Apinil Cosméticos</title>
  <meta name="description" content="{esc(description[:180])}">
  <link rel="icon" href="/assets/img/logo.png">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@600;700&family=Outfit:wght@400;500;600&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/assets/css/site.css">
</head>
<body>
{render_header(path, logo)}
<main>
{main_html}
</main>
{render_footer()}
</body>
</html>
"""


CSS = r"""
:root {
  --green: #1c7a45;
  --green-dark: #114d2c;
  --ink: #1a1f1c;
  --muted: #5d675f;
  --line: #d7e6db;
  --paper: #f4f8f5;
  --white: #fff;
  --shadow: 0 10px 30px rgba(17, 77, 44, 0.08);
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  color: var(--ink);
  background: var(--white);
  font-family: Outfit, "Segoe UI", sans-serif;
  font-size: 17px;
  line-height: 1.55;
}
img { max-width: 100%; height: auto; }
a { color: var(--green-dark); }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
.site-header {
  position: sticky;
  top: 0;
  z-index: 20;
  background: rgba(255,255,255,.96);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--line);
}
.header-inner {
  max-width: 1180px;
  margin: 0 auto;
  padding: 12px 20px 8px;
  display: flex;
  align-items: center;
  gap: 18px;
}
.brand img { height: 74px; width: auto; display: block; }
.site-nav { display: flex; flex: 1; justify-content: flex-end; gap: 8px; }
.site-nav a, .cat-nav a {
  text-decoration: none;
  color: var(--green);
  letter-spacing: .14em;
  text-transform: uppercase;
  font-size: 13px;
  font-weight: 600;
  padding: 10px 14px;
  border-bottom: 1px solid var(--line);
}
.site-nav a[aria-current="page"], .cat-nav a[aria-current="page"] {
  color: var(--green-dark);
  border-bottom-color: var(--green);
}
.nav-toggle {
  display: none;
  margin-left: auto;
  background: var(--white);
  color: var(--green-dark);
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 8px 14px;
  font: inherit;
  font-size: 14px;
}
.cat-nav {
  max-width: 1180px;
  margin: 0 auto;
  padding: 0 12px 10px;
  display: flex;
  gap: 6px;
  overflow-x: auto;
}
.cat-nav a {
  border: 1px solid var(--line);
  border-radius: 999px;
  white-space: nowrap;
  letter-spacing: .08em;
  font-size: 12px;
  background: var(--paper);
}
main { max-width: 1180px; margin: 0 auto; padding: 0 20px 72px; }
.hero, .banner {
  width: 100vw;
  max-width: 100vw;
  margin-left: calc(50% - 50vw);
  margin-bottom: 28px;
  max-height: 520px;
  object-fit: cover;
  display: block;
}
h1, h2, .section-title {
  font-family: "Cormorant Garamond", Georgia, serif;
  font-weight: 700;
  color: var(--green-dark);
  letter-spacing: .04em;
  text-transform: uppercase;
}
h1 { text-align: center; font-size: clamp(40px, 6vw, 64px); margin: 28px 0 8px; }
.section-title, main h2 { text-align: center; font-size: clamp(28px, 4vw, 40px); margin: 36px 0 18px; }
.prose { max-width: 760px; margin: 0 auto 16px; text-align: center; color: var(--ink); }
.product-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
  gap: 28px 18px;
  margin: 10px 0 20px;
}
.card {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  text-decoration: none;
  color: var(--ink);
  background: transparent;
}
.card img {
  width: min(100%, 280px);
  height: 280px;
  object-fit: contain;
  background: linear-gradient(#fff, var(--paper));
  border-radius: 18px;
}
.card-fallback {
  width: min(100%, 280px);
  height: 180px;
  border-radius: 18px;
  background:
    radial-gradient(circle at 30% 20%, #fff, transparent 40%),
    linear-gradient(160deg, #e7f6ee, #b7dcc6);
}
.card p {
  margin: 12px 0 0;
  font-size: 13px;
  letter-spacing: .08em;
  text-transform: uppercase;
  font-weight: 600;
  color: var(--green-dark);
}
a.card:hover img { transform: translateY(-3px); }
a.card img { transition: transform .2s ease; }
.product-detail {
  display: grid;
  grid-template-columns: minmax(260px, 420px) 1fr;
  gap: 36px;
  align-items: start;
  margin-top: 28px;
}
.product-photo img { width: 100%; border-radius: 24px; background: var(--paper); }
.product-copy h1, .product-copy h2, .product-copy p { text-align: left; margin-left: 0; }
.product-copy h1 { margin-top: 0; }
.thumbs { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
.thumbs button { border: 1px solid var(--line); background: #fff; padding: 0; border-radius: 10px; cursor: pointer; }
.thumbs img { width: 72px; height: 72px; object-fit: cover; display: block; border-radius: 10px; }
.more a {
  display: inline-block;
  margin-top: 12px;
  color: var(--white);
  background: var(--green);
  text-decoration: none;
  border-radius: 999px;
  padding: 10px 16px;
}
.contact-panel, .catalog-box {
  max-width: 680px;
  margin: 28px auto;
  background: var(--paper);
  border-radius: 24px;
  padding: 28px;
}
.contact-panel h2, .contact-panel p { text-align: left; }
form { display: grid; gap: 12px; }
label { display: grid; gap: 6px; font-size: 14px; font-weight: 600; color: var(--green-dark); }
input, textarea {
  font: inherit;
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 12px 14px;
  background: #fff;
}
.form-actions { display: flex; gap: 10px; flex-wrap: wrap; }
button, .catalog-btn {
  border: 0;
  background: var(--green);
  color: white;
  border-radius: 999px;
  padding: 12px 18px;
  font: inherit;
  font-weight: 600;
  cursor: pointer;
  text-decoration: none;
  display: inline-block;
}
button.secondary { background: var(--green-dark); }
.site-footer { background: var(--green-dark); color: #e7f5ec; padding: 42px 20px 28px; }
.footer-grid { max-width: 1180px; margin: 0 auto; display: grid; grid-template-columns: repeat(3, 1fr); gap: 24px; }
.site-footer h2 { color: white; font-size: 28px; margin: 0 0 8px; text-align: left; }
.site-footer a { color: white; }
.site-footer p { margin: 0 0 8px; }
.social { display: flex; gap: 14px; flex-wrap: wrap; }
.legal { max-width: 1180px; margin: 26px auto 0; opacity: .8; font-size: 14px; }
.whatsapp {
  position: fixed;
  right: 18px;
  bottom: 18px;
  background: #1ebe5d;
  color: white;
  text-decoration: none;
  border-radius: 999px;
  padding: 12px 16px 12px 42px;
  font-weight: 700;
  box-shadow: var(--shadow);
  z-index: 30;
}
.whatsapp::before {
  content: "";
  position: absolute;
  left: 14px;
  top: 50%;
  width: 18px;
  height: 18px;
  transform: translateY(-50%);
  background: white;
  border-radius: 50%;
}
@media (max-width: 860px) {
  .nav-toggle { display: inline-block; }
  .site-nav {
    display: none;
    position: absolute;
    top: 100%;
    left: 0; right: 0;
    background: white;
    flex-direction: column;
    padding: 8px 16px 16px;
    border-bottom: 1px solid var(--line);
  }
  .site-nav.open { display: flex; }
  .product-detail { grid-template-columns: 1fr; }
  .footer-grid { grid-template-columns: 1fr; }
  .brand img { height: 58px; }
  .whatsapp span { display: none; }
  .whatsapp { padding: 14px 14px 14px 36px; }
}
"""

JS = r"""
const toggle = document.querySelector(".nav-toggle");
const nav = document.querySelector(".site-nav");
if (toggle && nav) {
  toggle.addEventListener("click", () => {
    const open = nav.classList.toggle("open");
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
  });
}
document.querySelectorAll(".thumbs button").forEach((button) => {
  button.addEventListener("click", () => {
    const main = document.getElementById("product-main");
    if (main) main.src = button.dataset.src;
  });
});
function messageFrom(form) {
  const data = new FormData(form);
  return [
    "Olá Apinil, vim pelo site.",
    `Nome: ${data.get("nome") || ""}`,
    `E-mail: ${data.get("email") || ""}`,
    `Telefone: ${data.get("telefone") || ""}`,
    "",
    String(data.get("mensagem") || ""),
  ].join("\n");
}
const form = document.getElementById("contact-form");
if (form) {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = encodeURIComponent(messageFrom(form));
    window.open(`https://api.whatsapp.com/send?phone=556235845787&text=${text}`, "_blank", "noopener");
  });
  const mail = document.getElementById("mailto-btn");
  mail?.addEventListener("click", () => {
    if (!form.reportValidity()) return;
    const text = encodeURIComponent(messageFrom(form));
    window.location.href = `mailto:apinilindustria@hotmail.com?subject=${encodeURIComponent("Contato pelo site Apinil")}&body=${text}`;
  });
}
fetch("/version.txt")
  .then((response) => (response.ok ? response.text() : ""))
  .then((version) => {
    const label = version.trim();
    const legal = document.querySelector(".legal");
    if (label && legal) legal.insertAdjacentText("beforeend", ` · v${label}`);
  })
  .catch(() => {});
"""


def write_support(logo: str) -> None:
    css_dir = PUBLIC / "assets" / "css"
    js_dir = PUBLIC / "assets" / "js"
    css_dir.mkdir(parents=True, exist_ok=True)
    js_dir.mkdir(parents=True, exist_ok=True)
    (css_dir / "site.css").write_text(CSS, encoding="utf-8")
    (js_dir / "site.js").write_text(JS, encoding="utf-8")
    not_found = {
        "path": "/404.html",
        "title": "Página não encontrada",
        "events": [],
        "pdfs": [],
    }
    # Simple 404 uses the same chrome.
    html_404 = render_page(not_found, [{"type": "p", "text": "Não encontramos esta página. Volte para os produtos ou fale com a fábrica."}], logo, {})
    (PUBLIC / "404.html").write_text(html_404, encoding="utf-8")
    (PUBLIC / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")


def category_images(pages: list[dict], images: dict[str, str]) -> dict[str, str]:
    wanted = {href for href, _label in CATEGORIES}
    found: dict[str, str] = {}
    for page in pages:
        pending: str | None = None
        for event in page["events"]:
            if event[0] == "img":
                pending = event[3]
            elif event[0] == "link" and pending:
                target = local_path(urllib.parse.urlparse(event[2]).path or "/")
                if target in wanted and target not in found and pending in images:
                    found[target] = images[pending]
                pending = None
    return found


def decorate(page: dict, blocks: list[dict], pages: list[dict], images: dict[str, str]) -> list[dict]:
    if page["path"] == "/blog/":
        return [{
            "type": "p",
            "text": "O blog da Apinil ainda não publica novidades da fábrica. Para lançamentos e pedidos, fale pelo WhatsApp ou e-mail.",
        }]
    if page["path"] == "/gallery/":
        photos = category_images(pages, images)
        cards = []
        for href, label in CATEGORIES:
            cards.append({
                "src": photos.get(href),
                "alt": label,
                "caption": label,
                "href": href,
            })
        return [
            {"type": "p", "text": "Seis linhas de cosméticos fabricados em Aparecida de Goiânia."},
            {"type": "grid", "cards": cards},
        ]
    for block in blocks:
        if block["type"] != "grid":
            continue
        for card in block["cards"]:
            if card.get("href") and not card.get("caption"):
                for href, label in CATEGORIES:
                    if href == card["href"]:
                        card["caption"] = label
                        card["alt"] = label
    title_n = norm(page["title"])
    for block in blocks:
        if block["type"] == "grid":
            block["cards"] = [
                card for card in block["cards"]
                if norm(card.get("caption") or "") != title_n
            ]
    blocks = [block for block in blocks if block["type"] != "grid" or block["cards"]]
    captions = {
        norm(card["caption"])
        for block in blocks if block["type"] == "grid"
        for card in block["cards"] if card.get("caption")
    }
    cleaned = []
    for block in blocks:
        if block["type"] == "textlink" and (block["href"] == "/" or norm(block["text"]) in captions):
            continue
        if block["type"] == "heading" and cleaned and cleaned[-1]["type"] == "textlink":
            if norm(cleaned[-1]["text"]) == norm(block["text"]):
                continue
        cleaned.append(block)
    useful = [b for b in cleaned if b["type"] in {"grid", "p", "hero", "heading", "banner"}]
    if not useful:
        cleaned.append({"type": "p", "text": "Conteúdo em atualização. Fale com a Apinil pelo WhatsApp."})
    return cleaned


def write_pages(pages: list[dict], images: dict[str, str], pdf_map: dict[str, str], logo: str) -> None:
    titles = {page["path"]: page["title"] for page in pages}
    catalog = []
    for page in pages:
        blocks = decorate(page, build_blocks(page, images, titles), pages, images)
        rendered = render_page(page, blocks, logo, pdf_map)
        if page["path"] == "/":
            dest = PUBLIC / "index.html"
        else:
            dest = PUBLIC / page["path"].strip("/") / "index.html"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(rendered, encoding="utf-8")
        image = None
        for block in blocks:
            if block["type"] == "grid" and block["cards"]:
                image = block["cards"][0]["src"]
                break
            if block["type"] == "hero":
                image = block["src"]
        catalog.append({
            "title": page["title"],
            "path": page["path"],
            "image": image,
            "detail": is_detail(blocks),
        })
        print("página", page["path"], "blocos", len(blocks))
    (PUBLIC / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    urls = ["https://localhost/"]
    for item in catalog:
        if item["path"] != "/":
            urls.append(item["path"])
    body = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for item in catalog:
        body.append(f"<url><loc>{html.escape(item['path'])}</loc></url>")
    body.append("</urlset>")
    (PUBLIC / "sitemap.xml").write_text("\n".join(body), encoding="utf-8")


def main() -> None:
    print("lendo sitemap")
    urls = [u for u in sitemap_urls() if not u.endswith(".xml")]
    print(f"{len(urls)} páginas")
    pages = []
    for index, url in enumerate(urls, start=1):
        print(f"[{index}/{len(urls)}] {url}")
        try:
            pages.append(extract_page(url))
        except Exception as exc:  # noqa: BLE001
            print("  falhou", exc)
        time.sleep(0.25)
    print("baixando logo e imagens")
    logo = download_logo()
    images = download_images(pages)
    print("baixando pdfs", sum(len(p["pdfs"]) for p in pages))
    pdf_map = download_pdfs(pages)
    write_support(logo)
    write_pages(pages, images, pdf_map, logo)
    used = set()
    for html_file in PUBLIC.rglob("*.html"):
        used.update(re.findall(r"/assets/img/[^\"')\s]+", html_file.read_text(encoding="utf-8")))
    removed = 0
    for image in IMG_DIR.iterdir():
        if f"/assets/img/{image.name}" not in used and image.name != "logo.png":
            image.unlink()
            removed += 1
    print("imagens órfãs removidas", removed)
    print("ok", PUBLIC)


if __name__ == "__main__":
    main()
