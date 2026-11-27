"""Cadastro de pessoas: PIX e o espaço da lista."""

import unittest

import financeiro as fin


class PixTest(unittest.TestCase):
    def test_tipos_e_formatos(self):
        self.assertEqual(fin.normalizar_pix("cpf", "529.982.247-25"), "52998224725")
        self.assertIsNone(fin.normalizar_pix("cpf", "111.111.111-11"))
        self.assertEqual(fin.normalizar_pix("cnpj", "11.444.777/0001-61"), "11444777000161")
        self.assertEqual(fin.normalizar_pix("email", "Ana@Banco.com"), "ana@banco.com")
        self.assertEqual(fin.normalizar_pix("telefone", "(11) 98888-7777"), "+5511988887777")
        self.assertEqual(fin.normalizar_pix("telefone", "+55 11 98888-7777"), "+5511988887777")
        chave = "123e4567-e89b-12d3-a456-426614174000"
        self.assertEqual(fin.normalizar_pix("aleatoria", chave.upper()), chave)
        self.assertIsNone(fin.normalizar_pix("aleatoria", "nao-e-chave"))
        self.assertIsNone(fin.normalizar_pix("email", "sem-arroba"))

    def editor(self, **extra):
        base = fin.blank_pessoa()
        base.update({
            "tipo": "F",
            "documento": "529.982.247-25",
            "nome": "Charles Elias",
            "papel": "fornecedor",
        })
        base.update(extra)
        return base

    def test_pix_vazio_passa(self):
        self.assertEqual(fin.validate_pessoa(self.editor()), {})

    def test_chave_sem_tipo_avisa(self):
        erros = fin.validate_pessoa(self.editor(pix_chave="529.982.247-25"))
        self.assertIn("pix_tipo", erros)

    def test_chave_incompativel_avisa(self):
        erros = fin.validate_pessoa(self.editor(pix_tipo="email", pix_chave="529.982.247-25"))
        self.assertIn("pix_chave", erros)

    def test_telefone_valido_passa(self):
        erros = fin.validate_pessoa(self.editor(
            pix_tipo="telefone",
            pix_chave="11988887777",
            pix_instituicao="Banco do Brasil S.A.",
        ))
        self.assertNotIn("pix_tipo", erros)
        self.assertNotIn("pix_chave", erros)


class PessoasPaginaTest(unittest.TestCase):
    def test_lista_larga_e_tipos_de_pix(self):
        html = fin.pessoas_page([], "", "", "", fin.blank_pessoa()).decode("utf-8")
        self.assertIn("pessoa-desk", html)
        self.assertIn("pessoa-lista", html)
        self.assertIn("pessoa-form", html)
        self.assertNotIn("wide-form", html)
        for rotulo in ("CPF", "CNPJ", "E-mail", "Telefone", "Chave aleatória"):
            self.assertIn(rotulo, html)
        self.assertIn('name="pix_chave"', html)
        self.assertIn("Nome da instituição", html)
        self.assertIn('id="pix-tipo"', html)


if __name__ == "__main__":
    unittest.main()
