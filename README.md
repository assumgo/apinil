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

## Versões

A versão atual está em `VERSION`. O rodapé do site lê `public/version.txt`. Cada entrega estável é uma tag `vX.Y.Z` e um release no GitHub. O histórico fica em `CHANGELOG.md`.

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

A imagem nginx serve a pasta `public/`. O healthcheck usa `GET /healthz`. A versão publicada aparece no rodapé.

## Conectar o servidor

Não há, nesta conta, um servidor com SSH nem um worker do Cursor ligado. O workflow `deploy` fica pronto e só publica quando estes segredos existirem em Settings → Secrets and variables → Actions:

| Segredo | Conteúdo |
| --- | --- |
| `DEPLOY_HOST` | IP ou domínio do servidor |
| `DEPLOY_USER` | usuário SSH com sudo sem senha |
| `DEPLOY_SSH_KEY` | chave privada correspondente ao `authorized_keys` |
| `DEPLOY_PORT` | porta SSH, se não for 22 |

A variável `WEB_PORT` (opcional) é a porta pública do site. O padrão é 80.

No servidor, uma vez, com a tag já existente no GitHub:

```bash
curl -fsSL https://raw.githubusercontent.com/assumgo/apinil/main/scripts/prepare-server.sh | sudo bash -s -- v0.1.0
```

Enquanto o pull request não estiver em `main`, rode o script a partir do clone deste branch. Depois, cada release `vX.Y.Z` dispara o workflow `deploy`. Também dá para publicar à mão em Actions → deploy → Run workflow.

## Atualizar o conteúdo a partir do site público

```bash
python3 scripts/build_site.py
```

O script lê o sitemap público, baixa as imagens da marca e regrava `public/`.
