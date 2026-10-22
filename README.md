# Apinil Cosméticos

Site estático da Apinil Indústria de Cosméticos, pronto para instalar em um servidor com Docker. O conteúdo público de [apinil.com](https://www.apinil.com) (textos, fotos de produtos e linhas) foi republicado em HTML, sem o runtime do Wix.

## Páginas

- `/` linha Pele
- `/about-1/` home com os destaques
- `/gallery/` as seis linhas
- `/biography/` sobre a fábrica
- `/catálogo/` catálogo
- `/contact/` contato, com envio pelo WhatsApp ou e-mail
- uma página para cada produto publicado no site original

O PDF do catálogo não estava disponível no snapshot público (o arquivo no Wix responde 404). Quando tiver o arquivo, salve como `public/assets/files/catalogo-apinil.pdf` e rode de novo o gerador, ou copie o PDF para essa pasta dentro da imagem.

## Instalar no servidor

Requisitos: Docker e o plugin Compose v2.

```bash
git clone https://github.com/assumgo/apinil.git
cd apinil
PORT=80 ./scripts/install.sh
```

Sem `PORT`, o site sobe em `http://servidor:8080`. Com `PORT=80`, sobe na porta 80.

Para atualizar depois de um `git pull`:

```bash
PORT=80 docker compose up -d --build
```

A imagem nginx serve a pasta `public/`. O healthcheck usa `GET /healthz`.

## Atualizar o conteúdo a partir do site público

```bash
python3 scripts/build_site.py
```

O script lê o sitemap público, baixa as imagens da marca e regrava `public/`.
