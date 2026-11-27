"""Recibos, grade vazia de contas a pagar e confirmação de dados fora do usual."""

import unittest
from datetime import date
from decimal import Decimal

import financeiro as fin


class FakeConn:
    def execute(self, sql, params=None):
        return self

    def fetchall(self):
        return []

    def fetchone(self):
        return None

    def __iter__(self):
        return iter(())


class ContagemConn:
    def __init__(self):
        self.called = False

    def execute(self, sql, params=None):
        self.called = True
        return self

    def fetchall(self):
        return [(1, "9", "confirmado", Decimal("10.00"), 1, "Fornecedor")]


class ValorTest(unittest.TestCase):
    def test_valor_negativo_e_zero(self):
        self.assertEqual(fin.parse_valor_assinado("-10,50"), Decimal("-10.50"))
        self.assertEqual(fin.parse_valor_assinado("1.234,56"), Decimal("1234.56"))
        self.assertIsNone(fin.parse_valor_assinado("0"))
        self.assertIsNone(fin.parse_valor_assinado("-0,00"))
        self.assertIsNone(fin.parse_valor("-10,50"))
        self.assertEqual(fin.format_valor(Decimal("-1234.50")), "-1.234,50")
        self.assertEqual(fin.format_valor(Decimal("1234.50")), "1.234,50")

    def test_dividir_valor_negativo(self):
        partes = fin.dividir_valor(Decimal("-10.00"), 3)
        self.assertEqual(sum(partes), Decimal("-10.00"))


class InconsistenciaTest(unittest.TestCase):
    def editor(self, **extra):
        base = fin.blank_lancamento("pagar")
        base.update({
            "tipo_documento_id": "1",
            "pessoa_id": "2",
            "numero_documento": "15",
            "plano_conta_id": "3",
            "centro_custo_id": "4",
            "forma_pagamento_id": "5",
            "condicao_pagamento_id": "6",
            "qtde_parcelas": "1",
            "valor_total": "10,00",
            "data_emissao": "27/11/2026",
            "parcelas": [{"numero": 1, "valor": "10,00", "vencimento": "27/11/2026"}],
        })
        base.update(extra)
        return base

    def test_negativo_e_vencimento_passado_avisam(self):
        hoje = date(2026, 11, 27)
        avisos = fin.inconsistencias_lancamento(
            self.editor(
                valor_total="-10,00",
                parcelas=[{"numero": 1, "valor": "-10,00", "vencimento": "26/11/2026"}],
            ),
            hoje,
        )
        self.assertIn("O valor total está negativo.", avisos)
        self.assertIn("O vencimento 26/11/2026 é anterior a hoje.", avisos)
        self.assertIn("Há parcela com valor negativo.", avisos)

    def test_hoje_e_valor_positivo_nao_avisam(self):
        avisos = fin.inconsistencias_lancamento(self.editor(), date(2026, 11, 27))
        self.assertEqual(avisos, [])

    def test_negativo_ainda_e_valor_valido(self):
        editor = self.editor(
            valor_total="-10,00",
            parcelas=[{"numero": 1, "valor": "-10,00", "vencimento": "27/11/2026"}],
        )
        erros = fin.validate_lancamento(editor)
        self.assertNotIn("valor_total", erros)
        self.assertNotIn("parcelas", erros)

    def test_numero_vazio_usa_o_id(self):
        self.assertEqual(fin.numero_efetivo("", 15), "15")
        self.assertEqual(fin.numero_efetivo("  RC-9 ", 15), "RC-9")

    def test_recibo_aceita_numero_vazio(self):
        editor = self.editor(numero_documento="", numero_opcional=True)
        erros = fin.validate_lancamento(editor)
        self.assertNotIn("numero_documento", erros)


class GradeTest(unittest.TestCase):
    def test_lista_so_depois_de_pesquisar(self):
        conn = ContagemConn()
        self.assertEqual(fin.lancamentos_visiveis(conn, "pagar", {"pesquisar": ""}), [])
        self.assertFalse(conn.called)
        linhas = fin.lancamentos_visiveis(conn, "pagar", {"pesquisar": "1"})
        self.assertEqual(len(linhas), 1)
        self.assertTrue(conn.called)

    def pagina(self, natureza="pagar", rows=(), editor=None, filtros=None):
        editor = editor or fin.blank_lancamento(natureza)
        filtros = filtros if filtros is not None else fin.filtros_da_busca({})
        html = fin.lancamento_page(FakeConn(), natureza, rows, filtros, "", "", editor).decode("utf-8")
        return html

    def test_grade_inicial_vazia_com_limpar(self):
        html = self.pagina()
        self.assertIn("Nenhuma conta para mostrar. Pesquise para listar.", html)
        self.assertIn("Selecione uma conta a pagar para ver as parcelas.", html)
        self.assertIn(">Limpar<", html)
        self.assertIn('name="pesquisar" value="1"', html)
        self.assertNotIn('name="parcela_valor_1"', html)
        self.assertIn('data-viva="0"', html)
        self.assertIn(">Recibos<", html)

    def test_conta_selecionada_mostra_parcelas(self):
        editor = fin.blank_lancamento("pagar")
        editor["id"] = "8"
        editor["parcelas"] = [{"numero": 1, "valor": "10,00", "vencimento": "27/11/2026"}]
        html = self.pagina(editor=editor, filtros=fin.filtros_da_busca({"pesquisar": "1"}))
        self.assertIn('name="parcela_valor_1"', html)
        self.assertIn('data-viva="1"', html)
        self.assertIn("pesquisar=1", html)

    def test_dialogo_quando_ha_inconsistencia(self):
        editor = fin.blank_lancamento("pagar")
        editor["inconsistencias"] = ["O valor total está negativo."]
        editor["parcelas"] = [{"numero": 1, "valor": "-10,00", "vencimento": "27/11/2026"}]
        html = self.pagina(editor=editor)
        self.assertIn("Dados fora do usual", html)
        self.assertIn("O valor total está negativo.", html)
        self.assertIn('name="confirmar_inconsistencia"', html)


class ReciboPaginaTest(unittest.TestCase):
    def test_formulario_e_popup_de_sucesso(self):
        html = fin.recibos_page(FakeConn(), fin.blank_lancamento("pagar"), "", "", None).decode("utf-8")
        self.assertIn("REC ou PRO", html)
        self.assertIn("Se vazio, usa o ID", html)
        self.assertIn("Plano de contas", html)
        self.assertIn("Centro de custo", html)
        self.assertIn('id="recibo"', html)
        self.assertNotIn("Gravado com sucesso", html)
        resumo = {
            "id": 15,
            "numero": "15",
            "fornecedor": "CHARLES ELIAS",
            "valor": Decimal("2129.00"),
            "parcelas": 2,
        }
        sucesso = fin.recibos_page(FakeConn(), fin.blank_lancamento("pagar"), "", "", resumo).decode("utf-8")
        self.assertIn("Gravado com sucesso", sucesso)
        self.assertIn(">15<", sucesso)
        self.assertIn("CHARLES ELIAS", sucesso)
        self.assertIn("2.129,00", sucesso)
        self.assertIn(">2<", sucesso)


if __name__ == "__main__":
    unittest.main()
