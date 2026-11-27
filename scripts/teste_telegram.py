"""Testes de segurança do Telegram (sem rede e sem token): updates falsos de estranhos, grupos, cliques forjados etc.
Rode:  python scripts/teste_telegram.py"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import requests  # noqa: E402

import bot_telegram as bt  # noqa: E402

DONO, ESTRANHO = 111222333, 999888777
TOKEN_FALSO = "123456789:AAEhBOweik6ad9r_QXMENQjcrGbqCr4K-Rg"


def msg(origem, chat, tipo="private", texto="oi", bot=False, uid=1):
    return {"update_id": uid, "message": {"message_id": 5, "from": {"id": origem, "is_bot": bot},
                                          "chat": {"id": chat, "type": tipo}, "text": texto}}


def clique(origem, chat, data, tipo="private", uid=1):
    return {"update_id": uid, "callback_query": {"id": "cb1", "from": {"id": origem, "is_bot": False}, "data": data,
                                                 "message": {"message_id": 7, "chat": {"id": chat, "type": tipo}}}}


class patch_env:
    """Aponta o envio pra uma pasta de cortes falsa (sem tocar nos seus arquivos de verdade)."""

    def __init__(self, caso, cortes):
        self.ps = [mock.patch.object(bt, "CORTES", cortes), mock.patch.object(bt, "pasta_da_live", lambda n: Path(n)),
                   mock.patch.object(bt, "_dims", lambda a: (1080, 1920, 10.0))]

    def __enter__(self):
        for p in self.ps:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self.ps:
            p.stop()


class BotFalso:
    def __init__(self, lotes=None):
        self.chamadas, self.lotes, self.offsets = [], list(lotes or []), []

    def send_text(self, chat, texto, markup=None):
        self.chamadas.append(("texto", chat, texto))

    def responder_clique(self, id_, texto=""):
        self.chamadas.append(("resposta", id_, texto))

    def trocar_botoes(self, chat, id_msg, markup):
        self.chamadas.append(("botoes", chat, markup))

    def send_video(self, chat, caminho, legenda, markup, largura, altura, duracao):
        self.chamadas.append(("video", chat, Path(caminho).name, legenda, markup))

    def updates(self, offset, timeout=50):
        self.offsets.append(offset)
        return self.lotes.pop(0) if self.lotes else []


class TesteAutorizacao(unittest.TestCase):
    def test_dono_no_privado_passa(self):
        self.assertTrue(bt.autorizado(msg(DONO, DONO), DONO))
        self.assertTrue(bt.autorizado(clique(DONO, DONO, "aprovar:1"), DONO))

    def test_outro_usuario_no_privado_nao_passa(self):
        self.assertFalse(bt.autorizado(msg(ESTRANHO, ESTRANHO), DONO))
        self.assertFalse(bt.autorizado(clique(ESTRANHO, ESTRANHO, "aprovar:1"), DONO))

    def test_dono_mas_em_grupo_nao_passa(self):
        for tipo in ("group", "supergroup", "channel"):
            self.assertFalse(bt.autorizado(msg(DONO, -100123, tipo), DONO), tipo)
            self.assertFalse(bt.autorizado(clique(DONO, -100123, "aprovar:1", tipo), DONO), tipo)

    def test_clique_de_estranho_numa_mensagem_do_chat_do_dono_nao_passa(self):
        # o LiveCut só olhava o chat.id da mensagem: aqui o remetente do clique também conta
        self.assertFalse(bt.autorizado(clique(ESTRANHO, DONO, "aprovar:1"), DONO))
        self.assertFalse(bt.autorizado(msg(ESTRANHO, DONO), DONO))

    def test_bot_e_tipos_estranhos_nao_passam(self):
        self.assertFalse(bt.autorizado(msg(DONO, DONO, bot=True), DONO))
        self.assertFalse(bt.autorizado(msg(str(DONO), str(DONO)), DONO))
        self.assertFalse(bt.autorizado(msg(True, True), 1))
        self.assertFalse(bt.autorizado(msg(DONO, DONO), str(DONO)))
        self.assertFalse(bt.autorizado(msg(DONO, DONO), None))
        self.assertFalse(bt.autorizado(msg(DONO, DONO), True))

    def test_outros_tipos_de_update_e_lixo_nao_passam(self):
        for u in ({}, None, [], "x", {"update_id": 1}, {"inline_query": {"from": {"id": DONO}}},
                  {"my_chat_member": {"from": {"id": DONO}}}, {"channel_post": {"chat": {"id": DONO}}},
                  {"edited_message": msg(DONO, DONO)["message"]}, {"message": "texto"}, {"callback_query": 5},
                  {"message": {"from": {"id": DONO}}}, {"message": {"chat": {"id": DONO, "type": "private"}}},
                  {"callback_query": {"from": {"id": DONO}}}):
            self.assertFalse(bt.autorizado(u, DONO), repr(u))


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fila = bt.Fila(self._tmp.name)
        self.bot = BotFalso()
        self.t = [1000.0]
        self.esc = bt.Escutador(self.bot, DONO, self.fila, agora=lambda: self.t[0])
        self.cid = self.fila.novo(live="x", corte=1)


class TesteEscutador(Base):
    def test_estranho_nao_altera_nada_nem_recebe_resposta(self):
        for u in (msg(ESTRANHO, ESTRANHO, texto="/start"), clique(ESTRANHO, ESTRANHO, f"aprovar:{self.cid}"),
                  clique(ESTRANHO, DONO, f"aprovar:{self.cid}"), msg(DONO, -5, "group", "oi")):
            self.assertEqual(self.esc.processar(u), "ignorado")
        self.assertEqual(self.fila.clipe(self.cid)["estado"], "aguardando")
        self.assertEqual(self.bot.chamadas, [])

    def test_dono_aprova_e_nao_muda_de_novo(self):
        self.assertEqual(self.esc.processar(clique(DONO, DONO, f"aprovar:{self.cid}")), "aprovar")
        self.assertEqual(self.fila.clipe(self.cid)["estado"], "aprovado")
        self.esc.processar(clique(DONO, DONO, f"rejeitar:{self.cid}"))
        self.assertEqual(self.fila.clipe(self.cid)["estado"], "aprovado")  # idempotente
        self.assertTrue(any(c[0] == "botoes" for c in self.bot.chamadas))

    def test_clipe_postado_nao_volta_a_ser_decidido(self):
        self.fila.mudar(self.cid, estado="postado")
        for acao in ("aprovar", "rejeitar", "alterar"):
            self.esc.processar(clique(DONO, DONO, f"{acao}:{self.cid}"))
        self.assertEqual(self.fila.clipe(self.cid)["estado"], "postado")

    def test_dono_rejeita(self):
        self.esc.processar(clique(DONO, DONO, f"rejeitar:{self.cid}"))
        self.assertEqual(self.fila.clipe(self.cid)["estado"], "rejeitado")

    def test_botoes_fora_da_lista_fechada_sao_recusados(self):
        for data in ("aprovar:abc", "rm -rf /", "aprovar:1;ls", "aprovar:1234567", "APROVAR:1", "aprovar:-1",
                     "aprovar: 1", "aprovar:1\n", None, "", 5, "apagar:1", "aprovar", "aprovar:1:2"):
            self.assertEqual(self.esc.processar(clique(DONO, DONO, data)), "invalido", repr(data))
        self.assertEqual(self.fila.clipe(self.cid)["estado"], "aguardando")

    def test_clique_em_clipe_que_nao_existe(self):
        self.esc.processar(clique(DONO, DONO, "aprovar:9999"))
        self.assertIn("não existe", str(self.bot.chamadas))

    def test_pedido_de_alteracao_e_so_registrado_e_limpo(self):
        self.esc.processar(clique(DONO, DONO, f"alterar:{self.cid}"))
        self.assertEqual(self.fila.clipe(self.cid)["estado"], "alterar_aguardando_texto")
        # texto de estranho não vira pedido
        self.esc.processar(msg(ESTRANHO, ESTRANHO, texto="troque tudo"))
        self.assertNotIn("pedido", self.fila.clipe(self.cid))
        # texto do dono: sem < >, links e quebras de linha
        self.assertEqual(self.esc.processar(msg(DONO, DONO, texto="Trocar <b>título</b>\nhttp://evil.com/x agora")), "alteracao")
        c = self.fila.clipe(self.cid)
        self.assertEqual(c["estado"], "alteracao_pedida")
        self.assertNotIn("<", c["pedido"])
        self.assertNotIn("http", c["pedido"])
        self.assertNotIn("\n", c["pedido"])
        self.assertIsNone(self.fila.aguardando_texto())

    def test_pedido_longo_e_cortado_em_500(self):
        self.esc.processar(clique(DONO, DONO, f"alterar:{self.cid}"))
        self.esc.processar(msg(DONO, DONO, texto="a" * 5000))
        self.assertLessEqual(len(self.fila.clipe(self.cid)["pedido"]), 500)

    def test_texto_sem_contexto_e_comando_estranho_nao_executam_nada(self):
        for texto in ("rm -rf /", "/status; del *", "/clip kai", "aprovar 1", "oi"):
            self.esc.processar(msg(DONO, DONO, texto=texto))
        self.assertEqual(self.fila.clipe(self.cid)["estado"], "aguardando")

    def test_cancelar_volta_o_estado(self):
        self.esc.processar(clique(DONO, DONO, f"alterar:{self.cid}"))
        self.esc.processar(msg(DONO, DONO, texto="/cancelar"))
        self.assertEqual(self.fila.clipe(self.cid)["estado"], "aguardando")
        self.assertIsNone(self.fila.aguardando_texto())

    def test_aprovar_enquanto_espera_texto_limpa_a_espera(self):
        self.esc.processar(clique(DONO, DONO, f"alterar:{self.cid}"))
        self.esc.processar(clique(DONO, DONO, f"aprovar:{self.cid}"))
        self.assertIsNone(self.fila.aguardando_texto())

    def test_limite_de_30_eventos_por_minuto(self):
        resultados = [self.esc.processar(msg(DONO, DONO, texto="oi")) for _ in range(40)]
        self.assertEqual(resultados.count("limite"), 10)

    def test_aviso_de_estranhos_no_maximo_1_por_hora_sem_conteudo(self):
        for _ in range(5):
            self.esc.processar(msg(ESTRANHO, ESTRANHO, texto="segredo123"))
        self.assertEqual(self.bot.chamadas, [])
        self.t[0] += 3600
        self.esc.processar(msg(ESTRANHO, ESTRANHO, texto="outra"))
        avisos = [c for c in self.bot.chamadas if c[0] == "texto"]
        self.assertEqual(len(avisos), 1)
        self.assertEqual(avisos[0][1], DONO)
        self.assertNotIn("segredo123", avisos[0][2])
        self.esc.processar(msg(ESTRANHO, ESTRANHO, texto="de novo"))  # mesma hora: sem novo aviso
        self.assertEqual(len([c for c in self.bot.chamadas if c[0] == "texto"]), 1)


class TestePareamento(unittest.TestCase):
    def setUp(self):
        self.t = [0.0]
        self.p = bt.Pareamento(codigo="12345678", agora=lambda: self.t[0])

    def test_codigo_certo_pareia(self):
        self.assertEqual(self.p.tentar(msg(DONO, DONO, texto="/start 12345678")), ("ok", DONO))

    def test_so_aceita_no_privado_e_de_pessoa(self):
        self.assertEqual(self.p.tentar(msg(DONO, -9, "group", "/start 12345678"))[0], "ignorar")
        self.assertEqual(self.p.tentar(msg(DONO, DONO, texto="/start 12345678", bot=True))[0], "ignorar")
        self.assertEqual(self.p.tentar(msg(DONO, DONO + 1, texto="/start 12345678"))[0], "ignorar")

    def test_sem_codigo_ou_formato_errado_e_ignorado(self):
        for texto in ("/start", "12345678", "/start 12345678 extra", "oi"):
            self.assertEqual(self.p.tentar(msg(DONO, DONO, texto=texto))[0], "ignorar", texto)

    def test_tres_erros_esgotam_mesmo_com_o_codigo_certo_depois(self):
        self.assertEqual(self.p.tentar(msg(ESTRANHO, ESTRANHO, texto="/start 00000000"))[0], "errado")
        self.assertEqual(self.p.tentar(msg(ESTRANHO, ESTRANHO, texto="/start 11111111"))[0], "errado")
        self.assertEqual(self.p.tentar(msg(ESTRANHO, ESTRANHO, texto="/start 22222222"))[0], "esgotado")
        self.assertEqual(self.p.tentar(msg(DONO, DONO, texto="/start 12345678"))[0], "esgotado")

    def test_expira_em_5_minutos(self):
        self.t[0] = 301
        self.assertEqual(self.p.tentar(msg(DONO, DONO, texto="/start 12345678"))[0], "expirou")

    def test_codigo_nao_ascii_nao_derruba(self):
        self.assertEqual(self.p.tentar(msg(ESTRANHO, ESTRANHO, texto="/start ééééééé"))[0], "errado")

    def test_codigo_gerado_tem_8_digitos_e_varia(self):
        codigos = {bt.Pareamento().codigo for _ in range(20)}
        self.assertTrue(all(len(c) == 8 and c.isdigit() for c in codigos))
        self.assertGreater(len(codigos), 15)


class TesteSegredos(unittest.TestCase):
    def test_redigir_troca_o_token(self):
        self.assertNotIn(TOKEN_FALSO, bt.redigir(f"erro em https://api.telegram.org/bot{TOKEN_FALSO}/getUpdates"))
        self.assertNotIn(TOKEN_FALSO, bt.redigir(f"token {TOKEN_FALSO} vazou"))

    def test_erro_de_rede_nao_traz_o_token(self):
        bot = bt.Bot(TOKEN_FALSO)
        falha = requests.ConnectionError(f"Max retries exceeded with url: /bot{TOKEN_FALSO}/getUpdates")
        with mock.patch.object(bot._http, "post", side_effect=falha):
            with self.assertRaises(bt.TelegramErro) as cm:
                bot.updates(0, 1)
        self.assertNotIn(TOKEN_FALSO, str(cm.exception))
        self.assertTrue(cm.exception.__suppress_context__)
        self.assertIsNone(cm.exception.__cause__)

    def test_descricao_de_erro_da_api_e_redigida(self):
        bot = bt.Bot(TOKEN_FALSO)
        resp = mock.Mock()
        resp.json.return_value = {"ok": False, "error_code": 400, "description": f"bad token {TOKEN_FALSO}"}
        with mock.patch.object(bot._http, "post", return_value=resp):
            with self.assertRaises(bt.TelegramErro) as cm:
                bot.get_me()
        self.assertNotIn(TOKEN_FALSO, str(cm.exception))

    def test_falha_de_rede_fecha_a_conexao_para_a_proxima_ser_nova(self):
        bot = bt.Bot(TOKEN_FALSO)
        with mock.patch.object(bot._http, "post", side_effect=requests.ReadTimeout("lento")), \
                mock.patch.object(bot._http, "close") as fechar:
            with self.assertRaises(bt.TelegramErro):
                bot.updates(0, 1)
        fechar.assert_called_once()

    def test_espera_longa_e_curta_e_o_timeout_da_requisicao_cobre_ela(self):
        self.assertLessEqual(bt.ESPERA_LONGA, 30)
        bot = bt.Bot(TOKEN_FALSO)
        resp = mock.Mock()
        resp.json.return_value = {"ok": True, "result": []}
        with mock.patch.object(bot._http, "post", return_value=resp) as post:
            bot.updates(0, bt.ESPERA_LONGA)
        self.assertGreater(post.call_args.kwargs["timeout"], bt.ESPERA_LONGA)

    def test_repr_do_bot_nao_mostra_o_token(self):
        self.assertNotIn(TOKEN_FALSO, repr(bt.Bot(TOKEN_FALSO)))

    def test_token_com_formato_errado_e_recusado_sem_mostrar(self):
        with mock.patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "lixo-sem-formato"}):
            with self.assertRaises(SystemExit) as cm:
                bt.carregar_token()
        self.assertNotIn("lixo-sem-formato", str(cm.exception))

    def test_token_valido_e_lido_do_ambiente(self):
        with mock.patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": TOKEN_FALSO}):
            self.assertEqual(bt.carregar_token(), TOKEN_FALSO)

    def test_env_aceita_bom_e_aspas(self):
        with tempfile.TemporaryDirectory() as d:
            arq = Path(d) / ".env"
            arq.write_text('﻿# comentario\nTELEGRAM_BOT_TOKEN="abc"\n', encoding="utf-8")
            self.assertEqual(bt.ler_env(arq)["TELEGRAM_BOT_TOKEN"], "abc")


class TesteOuvir(Base):
    def test_so_o_dono_e_atendido_e_o_offset_avanca(self):
        lote = [msg(ESTRANHO, ESTRANHO, texto="oi", uid=11),
                clique(ESTRANHO, ESTRANHO, f"aprovar:{self.cid}", uid=12),
                clique(DONO, DONO, f"rejeitar:{self.cid}", uid=13),
                msg(DONO, -7, "group", "oi", uid=14)]
        bot = BotFalso([[{"update_id": 5}], lote])  # 1º: descarte do que ficou acumulado
        arq = Path(self._tmp.name) / "offset.json"
        bt.ouvir(bot, DONO, uma_vez=True, fila=self.fila, arq_offset=arq)
        self.assertEqual(self.fila.clipe(self.cid)["estado"], "rejeitado")
        self.assertEqual(bot.offsets, [-1, 6])
        self.assertEqual(bt.ler_offset(arq), 15)

    def test_token_recusado_encerra_com_mensagem(self):
        class Recusa(BotFalso):
            def updates(self, offset, timeout=50):
                raise bt.TelegramErro("getUpdates: Unauthorized", 401)
        arq = Path(self._tmp.name) / "o.json"
        bt.gravar_offset(3, arq)
        with self.assertRaises(SystemExit):
            bt.ouvir(Recusa(), DONO, uma_vez=True, fila=self.fila, arq_offset=arq)


class TesteEnvio(unittest.TestCase):
    def test_legenda_escapa_html_e_cabe_no_limite(self):
        leg = bt.montar_legenda(3, "<script>alert(1)</script>", "x" * 3000, "Corte da live de <i>Fulano</i>")
        self.assertNotIn("<script>", leg)
        self.assertLessEqual(len(leg), 1024)
        self.assertIn("#3", leg)

    def _montar(self, tmp):
        cortes = Path(tmp) / "cortes" / "minhalive"
        cortes.mkdir(parents=True)
        for i in (1, 2):
            (cortes / f"corte_0{i}.mp4").write_bytes(b"x" * 1000)
            (cortes / f"corte_0{i}.txt").write_text(f"TITULO {i}\n\nPost <b>{i}</b> #gta6\n\nCorte da live de Fulano: https://x\n",
                                                    encoding="utf-8")
        return patch_env(self, Path(tmp) / "cortes")

    def test_enviar_manda_so_pro_dono_sem_duplicar_e_substitui(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._montar(tmp):
                fila, bot = bt.Fila(Path(tmp) / "fila"), BotFalso()
                bt.enviar(bot, DONO, "minhalive", fila=fila)
                videos = [c for c in bot.chamadas if c[0] == "video"]
                self.assertEqual(len(videos), 2)
                self.assertTrue(all(v[1] == DONO for v in videos))
                self.assertNotIn("<b>1</b>", videos[0][3])  # HTML escapado
                bt.enviar(bot, DONO, "minhalive", fila=fila)  # igual: não manda de novo
                self.assertEqual(len([c for c in bot.chamadas if c[0] == "video"]), 2)
                bt.enviar(bot, DONO, "minhalive", cortes={1}, reenviar=True, fila=fila)
                self.assertEqual(len([c for c in bot.chamadas if c[0] == "video"]), 3)
                estados = {i: c["estado"] for i, c in fila.todos().items()}
                self.assertEqual(estados[1], "substituido")  # a versão nova do corte 1 substituiu a antiga
                self.assertEqual(estados[2], "aguardando")
                self.assertEqual(estados[3], "aguardando")

    def test_arquivo_acima_do_limite_nao_e_enviado(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._montar(tmp), mock.patch.object(bt, "LIMITE_BYTES", 500):
                bot = BotFalso()
                bt.enviar(bot, DONO, "minhalive", fila=bt.Fila(Path(tmp) / "fila"))
                self.assertEqual([c for c in bot.chamadas if c[0] == "video"], [])

    def test_botoes_so_usam_o_formato_aceito(self):
        for linha in bt.botoes(42)["inline_keyboard"]:
            for b in linha:
                self.assertTrue(bt.CB_RE.match(b["callback_data"]))


if __name__ == "__main__":
    unittest.main(verbosity=1)
