# Histórico de versões

O número publicado no site fica em `VERSION` e em `public/version.txt`. Cada versão estável recebe uma tag `vX.Y.Z` e um release no GitHub.

## 0.3.0 — 2026-10-22

- Cadastros abre os submenus Pessoas, Tipos de Documentos, Condições de Pagamentos, Formas de Pagamentos e Usuários.

## 0.2.0 — 2026-10-22

- Ícone de cadeado no cabeçalho abre a área restrita.
- Login com sessão no PostgreSQL. O usuário master é criado no banco do servidor, fora do repositório.
- Painel com Cadastros, Contas a pagar e Contas a receber.

## 0.1.0 — 2026-10-22

- Site estático da Apinil, com as páginas públicas, fotos e textos.
- Imagem Docker com nginx e `docker compose` para o servidor.
- Script `scripts/prepare-server.sh` para instalar Docker e publicar uma tag.
- Workflow de deploy por SSH, disparado por um release, quando os segredos do servidor existirem.
