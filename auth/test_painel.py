"""Testes da tela de início, dos backups e do histórico."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import financeiro
import painel


class PeriodoTest(unittest.TestCase):
    def test_semana_comeca_na_segunda_e_o_mes_e_o_calendario(self):
        janela = painel.periodos(date(2026, 11, 27))
        self.assertEqual(janela["dia"], date(2026, 11, 27))
        self.assertEqual(janela["semana"], (date(2026, 11, 23), date(2026, 11, 29)))
        self.assertEqual(janela["mes"], (date(2026, 11, 1), date(2026, 11, 30)))

    def test_reais_negativo(self):
        self.assertEqual(painel.reais(Decimal("-1234.50")), "-R$ 1.234,50")
        self.assertEqual(painel.reais(Decimal("0")), "R$ 0,00")


class TitulosSituacaoTest(unittest.TestCase):
    def test_situacao_sozinha_nao_pesquisa(self):
        filtros = financeiro.filtros_dos_titulos({"situacao": "aberto"})
        self.assertEqual(filtros["situacao"], "aberto")
        self.assertFalse(financeiro.criterios_dos_titulos(filtros))

    def test_situacao_invalida_e_ignorada(self):
        filtros = financeiro.filtros_dos_titulos({"natureza": "pagar", "situacao": "tudo"})
        self.assertEqual(filtros["situacao"], "")

    def test_lista_filtra_status_sem_exigir_novo_criterio(self):
        consultas = []

        class Conexao:
            def execute(self, sql, params=None):
                consultas.append((sql, params))
                return self

            def fetchall(self):
                return []

        filtros = financeiro.filtros_dos_titulos({
            "natureza": "pagar", "situacao": "aberto", "pesquisar": "1",
        })
        financeiro.listar_titulos(Conexao(), filtros)
        sql, params = consultas[0]
        self.assertIn("t.status = %s", sql)
        self.assertIn("aberto", params)
        self.assertIn("pagar", params)


class AuditoriaTest(unittest.TestCase):
    def test_entrada_e_saida(self):
        entrou = painel.evento_da_requisicao(
            {"metodo": "POST", "path": "/restrito/login", "acao": "", "registro": ""},
            303, "/restrito/cadastros",
        )
        self.assertEqual(entrou["acao"], "inclusao")
        self.assertEqual(entrou["descricao"], "Entrou no sistema")
        saiu = painel.evento_da_requisicao(
            {"metodo": "POST", "path": "/restrito/sair", "acao": "", "registro": ""},
            303, "/restrito/login",
        )
        self.assertEqual(saiu["acao"], "exclusao")

    def test_visualizacao_e_mutacao(self):
        vista = painel.evento_da_requisicao(
            {"metodo": "GET", "path": "/restrito/cadastros/pessoas", "acao": "", "registro": "12", "consulta": "editar"},
            200, "",
        )
        self.assertEqual(vista["acao"], "visualizacao")
        self.assertIn("edição", vista["descricao"])
        inclusao = painel.evento_da_requisicao(
            {"metodo": "POST", "path": "/restrito/cadastros/pessoas", "acao": "salvar", "registro": ""},
            303, "/restrito/cadastros/pessoas?aviso=incluido",
        )
        self.assertEqual(inclusao["acao"], "inclusao")
        exclusao = painel.evento_da_requisicao(
            {"metodo": "POST", "path": "/restrito/cadastros/pessoas", "acao": "excluir", "registro": "4"},
            303, "/restrito/cadastros/pessoas?aviso=excluido",
        )
        self.assertEqual(exclusao["acao"], "exclusao")

    def test_erro_e_senha_nao_viram_registro(self):
        self.assertIsNone(painel.evento_da_requisicao(
            {"metodo": "POST", "path": "/restrito/cadastros/pessoas", "acao": "salvar", "registro": ""},
            303, "/restrito/cadastros/pessoas?erro=ausente",
        ))
        self.assertIsNone(painel.evento_da_requisicao(
            {"metodo": "POST", "path": "/restrito/login", "acao": "", "registro": ""},
            401, "",
        ))
        troca = painel.evento_da_requisicao(
            {"metodo": "POST", "path": "/restrito/trocar-senha", "acao": "", "registro": ""},
            303, "/restrito/cadastros",
        )
        self.assertEqual(troca["descricao"], "Alterou a própria senha")
        self.assertNotIn("senha=", troca["descricao"])


class BackupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = Path(self.tmp.name)
        self.ambiente = {
            "BACKUP_DIR": str(self.raiz / "backups"),
            "DATABASE_URL": "postgresql://apinil:s3cret@127.0.0.1:1/apinil?connect_timeout=1",
            "PATH": str(self.raiz / "bin") + os.pathsep + os.environ.get("PATH", ""),
        }
        self.anterior = {chave: os.environ.get(chave) for chave in self.ambiente}
        os.environ.update(self.ambiente)
        binario = self.raiz / "bin"
        binario.mkdir()
        (binario / "pg_dump").write_text(
            "#!/bin/sh\n"
            "arquivo=\n"
            "while [ $# -gt 0 ]; do\n"
            "  case \"$1\" in *s3cret*) echo senha >&2; exit 9;; esac\n"
            "  if [ \"$1\" = \"-f\" ]; then arquivo=$2; shift 2; continue; fi\n"
            "  shift\n"
            "done\n"
            "printf PGDMP > \"$arquivo\"\n",
            encoding="utf-8",
        )
        (binario / "pg_restore").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        for nome in ("pg_dump", "pg_restore"):
            caminho = binario / nome
            caminho.chmod(caminho.stat().st_mode | stat.S_IEXEC)

    def tearDown(self):
        for chave, valor in self.anterior.items():
            if valor is None:
                os.environ.pop(chave, None)
            else:
                os.environ[chave] = valor
        self.tmp.cleanup()

    def test_nome_nao_sai_do_diretorio(self):
        self.assertIsNone(painel.caminho_do_backup("../apinil-20261127-120000.dump"))
        self.assertIsNone(painel.caminho_do_backup("apinil-20261127-120000.dump/../../etc/passwd"))
        self.assertIsNone(painel.caminho_do_backup("apinil-20261127-120000.sql"))
        valido = painel.caminho_do_backup("apinil-20261127-120000.dump")
        self.assertEqual(valido.name, "apinil-20261127-120000.dump")

    def test_cria_dump_sem_gravar_senha(self):
        manifesto = painel.criar_backup("manual", "Charles")
        dump = Path(self.ambiente["BACKUP_DIR"]) / manifesto["nome"]
        self.assertTrue(dump.is_file())
        texto = dump.read_text(encoding="utf-8") + Path(str(dump)[:-5] + ".json").read_text(encoding="utf-8")
        self.assertNotIn("s3cret", texto)
        self.assertEqual(manifesto["origem"], "manual")
        self.assertEqual(painel.listar_backups()[0]["nome"], manifesto["nome"])

    def test_automatico_uma_vez_por_dia(self):
        primeiro = painel.garantir_backup_do_dia()
        self.assertIsNotNone(primeiro)
        self.assertIsNone(painel.garantir_backup_do_dia())
        self.assertEqual(len(painel.listar_backups()), 1)

    def test_restaurar_pede_confirmacao_e_nao_aceita_nome_estranho(self):
        chamado = []
        with self.assertRaises(painel.BackupErro) as erro:
            painel.restaurar_backup("apinil-20261127-120000.dump", confirmacao="sim", encerrar=lambda: chamado.append(1))
        self.assertEqual(erro.exception.codigo, "confirmacao")
        self.assertEqual(chamado, [])
        with self.assertRaises(painel.BackupErro) as estranho:
            painel.restaurar_backup("../../etc/passwd", confirmacao="RESTAURAR", encerrar=lambda: chamado.append(1))
        self.assertEqual(estranho.exception.codigo, "arquivo")
        self.assertEqual(chamado, [])

    def test_restaurar_confirmado(self):
        manifesto = painel.criar_backup("manual", "Charles")
        chamado = []
        painel.restaurar_backup(manifesto["nome"], confirmacao="RESTAURAR", encerrar=lambda: chamado.append("ok"))
        self.assertEqual(chamado, ["ok"])
        self.assertNotIn("s3cret", json.dumps(manifesto))


class InicioHtmlTest(unittest.TestCase):
    def test_pagina_tem_os_quadros_e_os_links(self):
        zona = ZoneInfo("America/Sao_Paulo")
        dados = {
            "pagar_aberto": {"dia": Decimal("10.50"), "quantidade": 2, "semana": Decimal("20"), "mes": Decimal("40")},
            "receber_aberto": {"dia": Decimal("0"), "quantidade": 0, "semana": Decimal("0"), "mes": Decimal("0")},
            "pagar_baixado": {"dia": Decimal("5"), "quantidade": 1, "semana": Decimal("5"), "mes": Decimal("8")},
            "receber_baixado": {"dia": Decimal("7"), "quantidade": 1, "semana": Decimal("7"), "mes": Decimal("9")},
            "vencidos_receber": Decimal("3"),
            "vencidos_receber_qtd": 1,
            "vencidos_pagar": Decimal("4"),
            "vencidos_pagar_qtd": 2,
            "saldo": Decimal("-1.50"),
            "pagar_pendentes": [],
            "receber_pendentes": [(1, "Oficina", "NF-1", 1, 2, Decimal("15"), date(2026, 11, 27))],
            "historico": [(
                datetime(2026, 11, 27, 7, 45, tzinfo=zona), "Sistema", "inclusao", "Backups",
                "Backup automático concluído: apinil-20261127-074500.dump",
            )],
            "backups": [{
                "nome": "apinil-20261127-074500.dump",
                "quando": datetime(2026, 11, 27, 7, 45, tzinfo=zona),
                "origem": "automatico",
                "rotulo": "Automático",
                "usuario": "Sistema",
                "bytes": 120,
                "versao": "0.9.14",
            }],
        }
        html = painel.render_inicio(
            {"name": "Charles Menezes", "role": "master"}, dados,
        ).decode("utf-8")
        for texto in (
            "A pagar hoje", "A receber hoje", "Pago hoje", "Recebido hoje",
            "Semana", "Mês", "Vencidos", "Saldo previsto", "Mês corrente",
            "Contas a pagar pendentes", "Contas a receber pendentes",
            "Nenhuma conta a pagar pendente.", "Oficina",
            "Últimas alterações", "Histórico completo", "Fazer backup agora",
            "R$ 10,50", "-R$ 1,50", "Administrador",
        ):
            self.assertIn(texto, html)
        self.assertIn("natureza=pagar&amp;situacao=aberto&amp;pesquisar=1", html)
        self.assertIn("natureza=receber&amp;situacao=aberto&amp;pesquisar=1", html)
        self.assertIn('href="/restrito/backups"', html)
        self.assertIn('href="/restrito/relatorios/historico"', html)
        visitante = painel.render_inicio({"name": "Ana", "role": "user"}, dados).decode("utf-8")
        self.assertNotIn("Fazer backup agora", visitante)

    def test_consultas_separam_vencimento_baixa_e_saldo(self):
        class Conexao:
            def __init__(self):
                self.sqls = []

            def execute(self, sql, params=None):
                self.sqls.append(sql)
                return self

            def fetchone(self):
                return (Decimal("0"), 0, Decimal("0"), 0)

            def fetchall(self):
                return []

        conexao = Conexao()
        painel.carregar_inicio(conexao, date(2026, 11, 27))
        texto = "\n".join(conexao.sqls)
        self.assertIn("status = 'aberto' AND vencimento <", texto)
        self.assertIn("data_baixa", texto)
        self.assertIn("vencimento BETWEEN", texto)
        self.assertIn("acao <> 'visualizacao'", texto)
        saldo = next(sql for sql in conexao.sqls if "BETWEEN" in sql and "-" in sql)
        self.assertNotIn("status", saldo)


if __name__ == "__main__":
    unittest.main()
