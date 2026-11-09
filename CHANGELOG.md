# Histórico de versões

O número publicado no site fica em `VERSION` e em `public/version.txt`. Cada versão estável recebe uma tag `vX.Y.Z` e um release no GitHub.

## 0.8.2 — 2026-11-09

- O limite de login usa o endereço de quem acessa, e não o do proxy local.
- Uma senha errada e um usuário inexistente levam o mesmo tempo de verificação.
- O deploy entra pela porta 2289 e só aceita a chave ed25519 gravada em `deploy/known_hosts`. O processo da área restrita escuta em loopback.
- A área restrita não carrega mais fontes da Google.

## 0.8.1 — 2026-11-09

- O acesso ao servidor passa a ser só por HTTPS, com redirecionamento e HSTS.
- Login e troca de senha travam depois de várias tentativas, e o servidor limita requisições simultâneas.
- As páginas não podem ser embutidas em outro site. A sessão guarda só o resumo do token e a troca de senha encerra as sessões anteriores.

## 0.8.0 — 2026-11-07

- Centros de Custo ganha formulário com ID, código reduzido, nome e descrição.
- Inserir, editar e excluir. O ID é gerado pelo sistema e não pode ser alterado. O código reduzido é único.
- O processo da área restrita também serve o site público quando a pasta `public` está disponível.

## 0.7.0 — 2026-11-07

- Cadastro de usuários com nome, login, e-mail único e senha.
- O administrador inclui, edita, bloqueia, exclui, define outro administrador e pode pedir a troca de senha.
- No primeiro acesso, e quando o administrador pede, o usuário troca a senha antes de continuar.

## 0.6.3 — 2026-10-24

- O menu Painel sai da área restrita. O cadeado e o login abrem Cadastros.

## 0.6.2 — 2026-10-23

- Cadastros ganha o submenu Centros de Custo.

## 0.6.1 — 2026-10-23

- O menu principal da área restrita ganha o item Titulos.

## 0.6.0 — 2026-10-23

- Condições de Pagamentos ganha grade com incluir, editar, pesquisar e deletar.
- A tabela começa com 16 prazos, de AVISTA a 30/60/90/120DD.

## 0.5.1 — 2026-10-23

- Formas de Pagamentos passa a ter nome e descrição. Os oito registros iniciais ficam DIH/Dinheiro, PIX/Pix, BOL/Boleto, CRE/Cartão Crédito, DEB/Cartão Débito, CHQ/Cheque, DUP/Duplicata e PRO/Promissória.

## 0.5.0 — 2026-10-23

- Formas de Pagamentos ganha grade com incluir, editar, pesquisar e deletar.
- A tabela começa com DIH, PIX, BOL, CRE, DEB, CHQ, DUP e PRO.

## 0.4.0 — 2026-10-22

- Tipos de Documentos ganha grade com incluir, editar, pesquisar e deletar.
- A tabela começa com os 20 tipos iniciais, da NFe ao GRNE.

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
