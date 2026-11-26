"""Testes do Instagram (sem rede e sem token): uma "Meta" de mentira responde às chamadas.
Rode:  python scripts/teste_instagram.py"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import requests  # noqa: E402

import bot_telegram as bt  # noqa: E402
import instagram_api as ia  # noqa: E402

TOKEN = "IGAAXyZabc123_DEF456-ghi789JKLmno012PQRstu345VWXyz678"
TOKEN_FB = "EAAWxyz" + "AbC123" * 40  # login unificado (usuário de sistema)
URL = "https://videos.exemplo.com/clipes/corte_01.mp4"
PERMS_DO_TOKEN = ["instagram_basic", "instagram_content_publish", "ads_management", "ads_read",
                  "instagram_manage_comments", "instagram_manage_messages", "public_profile"]


class Resp:
    def __init__(self, status, corpo):
        self.status_code, self.ok, self._corpo = status, status < 400, corpo

    def json(self):
        return self._corpo


class HttpFalso:
    def __init__(self, handler=None):
        self.chamadas, self.handler = [], handler or padrao

    def request(self, metodo, url, params=None, data=None, headers=None, timeout=None):
        self.chamadas.append({"metodo": metodo, "url": url, "params": params, "dados": data, "headers": headers})
        r = self.handler(metodo, url, params, data)
        if isinstance(r, Exception):
            raise r
        return Resp(*r)

    def com(self, trecho, metodo=None):
        return [c for c in self.chamadas if c["url"].endswith(trecho) and (metodo is None or c["metodo"] == metodo)]


def padrao(metodo, url, params, data):
    if url.endswith("/me"):
        return 200, {"user_id": 1784, "username": "conta.teste"}
    if url.endswith("me/assigned_instagram_accounts"):
        return 200, {"data": [{"id": "17841400000000001", "username": "conta.teste"}]}
    if url.endswith("me/permissions"):
        return 200, {"data": [{"permission": p, "status": "granted"} for p in PERMS_DO_TOKEN]}
    if url.endswith("content_publishing_limit"):
        return 200, {"data": [{"quota_usage": 2, "config": {"quota_total": 50, "quota_duration": 86400}}]}
    if url.endswith("/media") and metodo == "POST":
        if isinstance(data, dict) and data.get("upload_type") == "resumable":
            return 200, {"id": "C9", "uri": "https://rupload.facebook.com/ig-api-upload/v27.0/C9"}
        return 200, {"id": "C1"}
    if url.startswith("https://rupload.facebook.com/") and metodo == "POST":
        return 200, {"success": True, "message": "Upload successful."}
    if url.endswith("/media_publish"):
        return 200, {"id": "M1"}
    if url.endswith("/C1") or url.endswith("/C2") or url.endswith("/C9"):
        return 200, {"status_code": "FINISHED"}
    if url.endswith("/M1"):
        return 200, {"permalink": "https://www.instagram.com/reel/abc/"}
    if url.endswith("refresh_access_token"):
        return 200, {"access_token": "IGAANOVO" + "x" * 60, "token_type": "bearer", "expires_in": 5183944}
    return 404, {"error": {"message": "caminho inesperado " + url, "code": 100}}


def cliente(handler=None, token=TOKEN):
    http = HttpFalso(handler)
    return ia.Instagram(token, versao="v27.0", http=http, dormir=lambda s: None), http


class TesteCliente(unittest.TestCase):
    def test_token_so_no_cabecalho_nunca_na_url(self):
        ig, http = cliente()
        ig.me()
        ig.limite("1784")
        ig.criar_conteiner("1784", URL, "legenda")
        ig.publicar("1784", "C1")
        for c in http.chamadas:
            self.assertNotIn(TOKEN, c["url"])
            self.assertEqual(c["headers"]["Authorization"], f"Bearer {TOKEN}")
            self.assertNotIn(TOKEN, str(c["params"]) + str(c["dados"]))

    def test_parametros_do_reels(self):
        ig, http = cliente()
        ig.criar_conteiner("1784", URL, "oi #gta6")
        d = http.com("/media", "POST")[0]["dados"]
        self.assertEqual((d["media_type"], d["video_url"], d["caption"]), ("REELS", URL, "oi #gta6"))

    def test_erro_da_api_vem_sem_o_token(self):
        ig, _ = cliente(lambda m, u, p, d: (400, {"error": {"message": f"Invalid OAuth access token {TOKEN}", "code": 190}}))
        with self.assertRaises(ia.InstagramErro) as cm:
            ig.me()
        self.assertNotIn(TOKEN, str(cm.exception))
        self.assertEqual(cm.exception.codigo, 190)

    def test_erro_de_rede_nao_traz_o_token_nem_a_url(self):
        falha = requests.ConnectionError(f"Max retries exceeded with url: /v27.0/me?access_token={TOKEN}")
        ig, _ = cliente(lambda m, u, p, d: falha)
        with self.assertRaises(ia.InstagramErro) as cm:
            ig.me()
        self.assertNotIn(TOKEN, str(cm.exception))
        self.assertNotIn("access_token", str(cm.exception))
        self.assertTrue(cm.exception.__suppress_context__)

    def test_resposta_sem_json_ou_http_ruim(self):
        ig, _ = cliente(lambda m, u, p, d: (500, {}))
        with self.assertRaises(ia.InstagramErro):
            ig.me()

    def test_me_sem_campos_e_recusado(self):
        ig, _ = cliente(lambda m, u, p, d: (200, {"id": "1"}))
        with self.assertRaises(ia.InstagramErro):
            ig.me()

    def test_repr_nao_mostra_token(self):
        self.assertNotIn(TOKEN, repr(cliente()[0]))

    def test_redigir_remove_token_e_strings_longas(self):
        self.assertNotIn(TOKEN, ia.redigir_ig(f"falhou {TOKEN} e access_token={TOKEN}&x=1", TOKEN))
        self.assertNotIn("a" * 100, ia.redigir_ig("segredo " + "a" * 100))

    def test_aguardar_espera_ate_ficar_pronto(self):
        respostas = iter(["IN_PROGRESS", "IN_PROGRESS", "FINISHED"])
        ig, _ = cliente(lambda m, u, p, d: (200, {"status_code": next(respostas)}))
        self.assertEqual(ig.aguardar("C1"), "FINISHED")

    def test_aguardar_erro_expirado_e_tempo_esgotado(self):
        for codigo in ("ERROR", "EXPIRED"):
            ig, _ = cliente(lambda m, u, p, d, c=codigo: (200, {"status_code": c, "status": "falhou"}))
            with self.assertRaises(ia.InstagramErro) as cm:
                ig.aguardar("C1")
            self.assertIn(codigo, str(cm.exception))
        ig, _ = cliente(lambda m, u, p, d: (200, {"status_code": "IN_PROGRESS"}))
        with self.assertRaises(ia.InstagramErro):
            ig.aguardar("C1", tentativas=3)

    def test_renovar_devolve_o_token_novo(self):
        ig, http = cliente()
        novo, expira = ig.renovar()
        self.assertTrue(novo.startswith("IGAANOVO"))
        self.assertEqual(expira, 5183944)
        self.assertEqual(http.chamadas[0]["params"]["grant_type"], "ig_refresh_token")


class TesteUnificado(unittest.TestCase):
    """Login unificado (token EAA, graph.facebook.com): conta atribuída e upload resumível do arquivo local."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.mp4 = Path(self._tmp.name) / "v.mp4"
        self.mp4.write_bytes(bytes(range(256)) * 4)  # 1024 bytes

    def test_hosts_e_token_so_em_cabecalho(self):
        ig, http = cliente(token=TOKEN_FB)
        self.assertTrue(ig.unificado)
        ig.contas()
        ig.permissoes()
        ig.limite("1784")
        for c in http.chamadas:
            self.assertTrue(c["url"].startswith("https://graph.facebook.com/v27.0/"), c["url"])
            self.assertNotIn(TOKEN_FB, c["url"])
            self.assertEqual(c["headers"]["Authorization"], f"Bearer {TOKEN_FB}")

    def test_contas_vem_de_assigned_instagram_accounts(self):
        ig, http = cliente(token=TOKEN_FB)
        self.assertEqual(ig.contas(), [{"user_id": "17841400000000001", "username": "conta.teste"}])
        self.assertTrue(http.chamadas[0]["url"].endswith("me/assigned_instagram_accounts"))

    def test_sem_conta_atribuida_explica(self):
        ig, _ = cliente(lambda m, u, p, d: (200, {"data": []}), token=TOKEN_FB)
        with self.assertRaises(ia.InstagramErro) as cm:
            ig.contas()
        self.assertIn("nenhuma conta", str(cm.exception))

    def test_varias_contas_escolhe_pela_marca_ou_pede_para_definir(self):
        contas = [{"user_id": "1", "username": "outra"}, {"user_id": "2", "username": "Conta.Teste"}]
        self.assertEqual(ia.escolher_conta(contas, "conta.teste")["user_id"], "2")
        with self.assertRaises(SystemExit):
            ia.escolher_conta(contas, "")
        self.assertEqual(ia.escolher_conta(contas[:1], "qualquer")["user_id"], "1")

    def test_permissoes_em_excesso_sao_apontadas(self):
        ig, _ = cliente(token=TOKEN_FB)
        extra = ia.permissoes_em_excesso(ig.permissoes())
        self.assertEqual(extra, ["ads_management", "ads_read", "instagram_manage_comments", "instagram_manage_messages"])
        self.assertEqual(ia.permissoes_em_excesso(["instagram_basic", "instagram_content_publish"]), [])
        self.assertIsNone(cliente()[0].permissoes())  # login do Instagram: sem essa checagem

    def test_upload_manda_o_arquivo_com_os_cabecalhos_certos(self):
        recebido = {}

        def h(m, u, p, d):
            if u.startswith("https://rupload.facebook.com/"):
                recebido["corpo"] = d.read()
            return padrao(m, u, p, d)
        ig, http = cliente(h, token=TOKEN_FB)
        ig.enviar_arquivo("https://rupload.facebook.com/ig-api-upload/v27.0/C9", self.mp4)
        c = http.chamadas[0]
        self.assertEqual(c["headers"]["Authorization"], f"OAuth {TOKEN_FB}")
        self.assertEqual((c["headers"]["offset"], c["headers"]["file_size"]), ("0", "1024"))
        self.assertEqual(len(recebido["corpo"]), 1024)
        self.assertNotIn(TOKEN_FB, c["url"])

    def test_upload_so_para_o_host_da_meta_e_nunca_manda_o_token_para_outro(self):
        for uri in ("https://evil.com/ig-api-upload/v27.0/C9", "http://rupload.facebook.com/ig-api-upload/C9",
                    "https://rupload.facebook.com.evil.com/ig-api-upload/C9", "https://rupload.facebook.com/outro/C9"):
            ig, http = cliente(token=TOKEN_FB)
            with self.assertRaises(ia.InstagramErro):
                ig.enviar_arquivo(uri, self.mp4)
            self.assertEqual(http.chamadas, [], uri)

    def test_conteiner_com_endereco_de_upload_estranho_e_recusado(self):
        def h(m, u, p, d):
            if u.endswith("/media") and m == "POST":
                return 200, {"id": "C9", "uri": "https://evil.com/steal/C9"}
            return padrao(m, u, p, d)
        ig, _ = cliente(h, token=TOKEN_FB)
        with self.assertRaises(ia.InstagramErro):
            ig.criar_conteiner_resumivel("17841400000000001", "legenda")

    def test_upload_retoma_do_ponto_que_a_meta_recebeu(self):
        estado = {"tentativas": 0, "corpos": []}

        def h(m, u, p, d):
            if u.startswith("https://rupload.facebook.com/"):
                estado["tentativas"] += 1
                if estado["tentativas"] == 1:
                    return requests.ConnectionError("caiu")
                estado["corpos"].append(d.read())
                return 200, {"success": True}
            if u.endswith("/C9") and (p or {}).get("fields") == "video_status":
                return 200, {"video_status": {"uploading_phase": {"bytes_transferred": 600}}}
            return padrao(m, u, p, d)
        ig, http = cliente(h, token=TOKEN_FB)
        ig.enviar_arquivo("https://rupload.facebook.com/ig-api-upload/v27.0/C9", self.mp4)
        uploads = http.com("/C9", "POST")
        self.assertEqual(uploads[1]["headers"]["offset"], "600")
        self.assertEqual(len(estado["corpos"][0]), 1024 - 600)

    def test_upload_recusado_pela_meta(self):
        ig, _ = cliente(lambda m, u, p, d: (200, {"success": False, "debug_info": {"retriable": False, "type": "ProcessingFailedError"}}),
                        token=TOKEN_FB)
        with self.assertRaises(ia.InstagramErro):
            ig.enviar_arquivo("https://rupload.facebook.com/ig-api-upload/v27.0/C9", self.mp4)

    def test_renovar_nao_se_aplica_ao_token_de_sistema(self):
        with self.assertRaises(ia.InstagramErro):
            cliente(token=TOKEN_FB)[0].renovar()


class TesteValidacao(unittest.TestCase):
    def test_url_publica(self):
        self.assertTrue(ia.url_publica_ok(URL))
        for ruim in ("http://videos.exemplo.com/a.mp4", "https://localhost/a.mp4", "https://127.0.0.1/a.mp4",
                     "https://192.168.0.5/a.mp4", "https://[::1]/a.mp4", "https://user:pw@exemplo.com/a.mp4",
                     "https://exemplo.com/a b.mp4", "https://intranet/a.mp4", "https://x.local/a.mp4", "ftp://x.com/a",
                     "https://exemplo.com/" + "a" * 2100, "", None, 5, "file:///c:/a.mp4", "https://x.com/\n"):
            self.assertFalse(ia.url_publica_ok(ruim), repr(ruim))

    def test_legenda_limites_e_sem_links(self):
        longa = "texto " * 600 + " ".join(f"#tag{i}" for i in range(40)) + " https://evil.com/x"
        leg = ia.montar_legenda(longa, "Crédito: Fulano")
        self.assertLessEqual(len(leg), 2200)
        self.assertNotIn("http", leg)
        self.assertTrue(leg.endswith("Crédito: Fulano"))
        curta = ia.montar_legenda("Olha isso 😳 " + " ".join(f"#t{i}" for i in range(40)))
        self.assertLessEqual(len(__import__("re").findall(r"#\w+", curta)), 30)

    def test_video(self):
        with tempfile.TemporaryDirectory() as d:
            mp4 = Path(d) / "a.mp4"
            mp4.write_bytes(b"x" * 100)
            self.assertEqual(ia.avisos_do_video(mp4, (1080, 1920, 50)), ([], []))
            erros, avisos = ia.avisos_do_video(mp4, (1080, 1350, 120))
            self.assertEqual(erros, [])
            self.assertEqual(len(avisos), 2)
            with mock.patch.object(ia, "LIMITE_BYTES", 10):
                self.assertTrue(ia.avisos_do_video(mp4, (1080, 1920, 50))[0])
            self.assertTrue(ia.avisos_do_video(Path(d) / "nao.mp4")[0])
            (Path(d) / "a.mov").write_bytes(b"x")
            self.assertTrue(ia.avisos_do_video(Path(d) / "a.mov")[0])

    def test_token_ausente_ou_malformado_encerra_sem_mostrar(self):
        with mock.patch.dict("os.environ", {"INSTAGRAM_ACCESS_TOKEN": "curto"}):
            with self.assertRaises(SystemExit) as cm:
                ia.carregar_token()
        self.assertNotIn("curto", str(cm.exception))
        with mock.patch.dict("os.environ", {"INSTAGRAM_ACCESS_TOKEN": ""}), \
                mock.patch.object(bt, "ENV", Path(tempfile.gettempdir()) / "nao_existe.env"):
            with self.assertRaises(SystemExit):
                ia.carregar_token()
        with mock.patch.dict("os.environ", {"INSTAGRAM_ACCESS_TOKEN": TOKEN}):
            self.assertEqual(ia.carregar_token(), TOKEN)

    def test_token_do_login_unificado_e_aceito(self):
        with mock.patch.dict("os.environ", {"INSTAGRAM_ACCESS_TOKEN": TOKEN_FB}):
            self.assertEqual(ia.carregar_token(), TOKEN_FB)

    def test_atualizar_env_preserva_o_resto(self):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text("# comentario\nTELEGRAM_BOT_TOKEN=aaa\nINSTAGRAM_ACCESS_TOKEN=velho\nOUTRA=1\n", encoding="utf-8")
            with mock.patch.object(bt, "restringir", lambda c: None):
                ia.atualizar_env("INSTAGRAM_ACCESS_TOKEN", "novo", env)
                ia.atualizar_env("NOVA_CHAVE", "x", env)
            d_ = bt.ler_env(env)
            self.assertEqual((d_["TELEGRAM_BOT_TOKEN"], d_["INSTAGRAM_ACCESS_TOKEN"], d_["OUTRA"], d_["NOVA_CHAVE"]),
                             ("aaa", "novo", "1", "x"))
            self.assertIn("# comentario", env.read_text(encoding="utf-8"))


class TestePublicar(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.mp4 = tmp / "corte_01.mp4"
        self.mp4.write_bytes(b"x" * 1000)
        self.mp4.with_suffix(".txt").write_text("TITULO\n\nO Speed achou o alien 👽 #gta6 #cortes\n\nCorte da live de X: https://x\n",
                                                encoding="utf-8")
        (tmp / "trabalho" / "minhalive").mkdir(parents=True)
        (tmp / "trabalho" / "minhalive" / "info.json").write_text('{"uploader": "IShowSpeed"}', encoding="utf-8")
        p = mock.patch.object(ia, "TRABALHO", tmp / "trabalho")
        p.start()
        self.addCleanup(p.stop)
        self.fila = bt.Fila(tmp / "fila")
        self.cid = self.fila.novo(live="minhalive", corte=1, arquivo=str(self.mp4))
        self.fila.mudar(self.cid, estado="aprovado")
        self.dims = (1080, 1920, 50.0)

    def publicar(self, ig, **kw):
        kw.setdefault("url", URL)
        return ia.publicar_clipe(ig, "1784", self.cid, fila=self.fila, dims=self.dims, **kw)

    def test_simulacao_nao_publica(self):
        ig, http = cliente()
        r = self.publicar(ig, postar=False)
        self.assertTrue(r["simulado"])
        self.assertEqual(len(http.com("/media", "POST")), 1)
        self.assertEqual(http.com("/media_publish"), [])
        c = self.fila.clipe(self.cid)
        self.assertEqual(c["estado"], "aprovado")  # simulação não muda a decisão
        self.assertEqual(c["instagram"]["estado"], "pronto_para_postar")
        self.assertIn("Crédito: IShowSpeed", r["legenda"])
        self.assertNotIn("http", r["legenda"])

    def test_postar_publica_uma_vez_e_marca_postado(self):
        ig, http = cliente()
        r = self.publicar(ig, postar=True)
        self.assertEqual(r["media_id"], "M1")
        self.assertEqual(len(http.com("/media_publish")), 1)
        c = self.fila.clipe(self.cid)
        self.assertEqual((c["estado"], c["instagram"]["estado"]), ("postado", "publicado"))
        self.assertEqual(c["instagram"]["link"], "https://www.instagram.com/reel/abc/")
        antes = len(http.chamadas)
        with self.assertRaises(ia.InstagramErro) as cm:
            self.publicar(ig, postar=True)
        self.assertIn("já foi publicado", str(cm.exception))
        self.assertEqual(len(http.chamadas), antes)  # nem falou com a Meta

    def test_login_unificado_sobe_o_arquivo_sem_precisar_de_url(self):
        ig, http = cliente(token=TOKEN_FB)
        r = self.publicar(ig, url=None, postar=True)
        self.assertEqual(r["modo"], "arquivo")
        dados = http.com("/media", "POST")[0]["dados"]
        self.assertEqual(dados["upload_type"], "resumable")
        self.assertNotIn("video_url", dados)
        self.assertEqual(len(http.com("/C9", "POST")), 1)  # o upload do arquivo
        self.assertEqual(len(http.com("/media_publish")), 1)
        self.assertEqual(self.fila.clipe(self.cid)["estado"], "postado")

    def test_login_unificado_simulacao_sobe_mas_nao_publica(self):
        ig, http = cliente(token=TOKEN_FB)
        r = self.publicar(ig, url=None, postar=False)
        self.assertTrue(r["simulado"])
        self.assertEqual(len(http.com("/C9", "POST")), 1)
        self.assertEqual(http.com("/media_publish"), [])
        self.assertEqual(self.fila.clipe(self.cid)["estado"], "aprovado")

    def test_login_unificado_com_url_usa_video_url_e_nao_sobe_arquivo(self):
        ig, http = cliente(token=TOKEN_FB)
        r = self.publicar(ig, url=URL, postar=False)
        self.assertEqual(r["modo"], "url")
        self.assertEqual(http.com("/C9", "POST"), [])
        self.assertEqual(http.com("/media", "POST")[0]["dados"]["video_url"], URL)

    def test_upload_recusado_nao_marca_postado_nem_publica(self):
        def h(m, u, p, d):
            if u.startswith("https://rupload.facebook.com/"):
                return 200, {"success": False, "debug_info": {"retriable": False, "type": "ProcessingFailedError"}}
            return padrao(m, u, p, d)
        ig, http = cliente(h, token=TOKEN_FB)
        with self.assertRaises(ia.InstagramErro):
            self.publicar(ig, url=None, postar=True)
        self.assertEqual(http.com("/media_publish"), [])
        self.assertEqual(self.fila.clipe(self.cid)["estado"], "aprovado")

    def test_login_do_instagram_sem_url_pede_a_url_e_nao_chama_a_meta(self):
        ig, http = cliente()
        with self.assertRaises(ia.InstagramErro) as cm:
            self.publicar(ig, url=None, postar=True)
        self.assertIn("URL PÚBLICA", str(cm.exception))
        self.assertEqual(http.chamadas, [])

    def test_so_publica_o_que_foi_aprovado(self):
        for estado in ("aguardando", "rejeitado", "alteracao_pedida", "substituido", "erro_envio"):
            self.fila.mudar(self.cid, estado=estado)
            ig, http = cliente()
            with self.assertRaises(ia.InstagramErro, msg=estado):
                self.publicar(ig, postar=True)
            self.assertEqual(http.chamadas, [])

    def test_clipe_inexistente(self):
        ig, _ = cliente()
        with self.assertRaises(ia.InstagramErro):
            ia.publicar_clipe(ig, "1784", 999, URL, postar=True, fila=self.fila, dims=self.dims)

    def test_sem_url_publica_nao_faz_nada(self):
        for url in (None, "", "http://x.com/a.mp4", "https://localhost/a.mp4"):
            ig, http = cliente()
            with self.assertRaises(ia.InstagramErro):
                self.publicar(ig, url=url, postar=True)
            self.assertEqual(http.chamadas, [])

    def test_cota_esgotada_nao_cria_conteiner(self):
        def h(m, u, p, d):
            if u.endswith("content_publishing_limit"):
                return 200, {"data": [{"quota_usage": 50, "config": {"quota_total": 50}}]}
            return padrao(m, u, p, d)
        ig, http = cliente(h)
        with self.assertRaises(ia.InstagramErro) as cm:
            self.publicar(ig, postar=True)
        self.assertIn("cota", str(cm.exception))
        self.assertEqual(http.com("/media", "POST"), [])

    def test_resposta_perdida_mas_post_feito_nao_falha_nem_repete(self):
        publicou = [False]

        def h(m, u, p, d):
            if u.endswith("/media_publish"):
                publicou[0] = True  # a Meta publicou, mas a resposta se perdeu
                return requests.ConnectionError("timeout")
            if u.endswith("/C1"):
                return 200, {"status_code": "PUBLISHED" if publicou[0] else "FINISHED"}
            return padrao(m, u, p, d)
        ig, http = cliente(h)
        r = self.publicar(ig, postar=True)
        self.assertEqual(len(http.com("/media_publish")), 1)  # não tentou de novo
        self.assertEqual(self.fila.clipe(self.cid)["estado"], "postado")
        self.assertIsNone(r["media_id"])

    def test_falha_real_ao_publicar_nao_marca_postado(self):
        def h(m, u, p, d):
            if u.endswith("/media_publish"):
                return 400, {"error": {"message": "recusado", "code": 9007}}
            return padrao(m, u, p, d)
        ig, _ = cliente(h)
        with self.assertRaises(ia.InstagramErro):
            self.publicar(ig, postar=True)
        self.assertEqual(self.fila.clipe(self.cid)["estado"], "aprovado")

    def test_conteiner_antigo_vencido_e_refeito(self):
        self.fila.mudar(self.cid, instagram={"estado": "pronto_para_postar", "conteiner": "OLD"})

        def h(m, u, p, d):
            if u.endswith("/OLD"):
                return 200, {"status_code": "EXPIRED"}
            if u.endswith("/media") and m == "POST":
                return 200, {"id": "C2"}
            return padrao(m, u, p, d)
        ig, http = cliente(h)
        self.publicar(ig, postar=True)
        self.assertEqual(self.fila.clipe(self.cid)["instagram"]["conteiner"], "C2")
        self.assertEqual(self.fila.clipe(self.cid)["estado"], "postado")

    def test_erro_do_video_recusado_pela_meta_nao_publica(self):
        def h(m, u, p, d):
            if u.endswith("/C1"):
                return 200, {"status_code": "ERROR", "status": "formato inválido"}
            return padrao(m, u, p, d)
        ig, http = cliente(h)
        with self.assertRaises(ia.InstagramErro):
            self.publicar(ig, postar=True)
        self.assertEqual(http.com("/media_publish"), [])

    def test_sem_credito_e_com_aviso_de_duracao(self):
        ig, _ = cliente()
        self.dims = (1080, 1920, 120.0)
        r = self.publicar(ig, postar=False, com_credito=False)
        self.assertNotIn("Crédito", r["legenda"])
        self.assertEqual(len(r["avisos"]), 1)

    def test_arquivo_sumiu(self):
        self.mp4.unlink()
        ig, http = cliente()
        with self.assertRaises(ia.InstagramErro):
            self.publicar(ig, postar=True)
        self.assertEqual(http.com("/media", "POST"), [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
