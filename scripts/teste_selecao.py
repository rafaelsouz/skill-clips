"""Testes da escolha de momentos: nota mínima, trava do 'olhar', cortar_miolo e as medições (fala, silêncio, jogo parado).
Rode:  python scripts/teste_selecao.py"""
import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cortar  # noqa: E402
import olhar  # noqa: E402

TEM_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def momento(inicio, fim, nota, titulo="T", **extra):
    return {"inicio": inicio, "fim": fim, "nota": nota, "titulo": titulo, "post": "p #gta6", **extra}


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.pasta = Path(self._tmp.name) / "live"
        (self.pasta / "momentos").mkdir(parents=True)
        (self.pasta / "info.json").write_text(json.dumps({"duration": 40000, "url": "https://x"}), encoding="utf-8")

    def escrever(self, momentos):
        (self.pasta / "momentos" / "candidatos.json").write_text(json.dumps(momentos), encoding="utf-8")

    def escolher(self, **kw):
        kw.setdefault("manter", 5)
        with contextlib.redirect_stdout(io.StringIO()) as saida:
            r = cortar.escolher(self.pasta, **kw)
        return r, saida.getvalue()


class TesteMiolo(unittest.TestCase):
    def test_miolo_valido(self):
        self.assertEqual(cortar._ler_miolo([["00:01:50", "00:02:10"]], 100, 190), [[110.0, 130.0]])

    def test_ordem_invertida_e_sobreposicao_viram_um_so(self):
        r = cortar._ler_miolo([["00:02:10", "00:01:50"], ["00:02:00", "00:02:20"]], 100, 190)
        self.assertEqual(r, [[110.0, 140.0]])

    def test_pontas_ficam_intocadas(self):
        # nunca corta nos 3 s do começo nem do fim
        self.assertEqual(cortar._ler_miolo([["00:01:40", "00:01:48"]], 100, 190), [[103.0, 108.0]])
        self.assertEqual(cortar._ler_miolo([["00:03:00", "00:03:10"]], 100, 190), [[180.0, 187.0]])

    def test_lixo_e_pedaco_curto_sao_ignorados(self):
        for v in (None, "x", 5, [], [["a", "b"]], [["00:01:50"]], [[None, None]], [["00:01:50", "00:01:51"]]):
            self.assertEqual(cortar._ler_miolo(v, 100, 190), [], repr(v))

    def test_nao_deixa_o_corte_com_menos_de_15_s(self):
        self.assertEqual(cortar._ler_miolo([["00:01:43", "00:03:07"]], 100, 190), [])

    def test_chave_do_corte_sem_miolo_e_igual_a_de_antes(self):
        m = {"_s": 100.0, "_e": 160.0}
        self.assertEqual(cortar._chave_corte(m), "100.00-160.00")  # cortes já feitos continuam "prontos"
        m["_miolo"] = [[110.0, 120.0]]
        self.assertNotEqual(cortar._chave_corte(m), "100.00-160.00")
        self.assertTrue(cortar._chave_corte(m).startswith("100.00-160.00-m"))

    def test_chave_do_olhar_muda_com_o_miolo(self):
        a = {"_s": 100.0, "_e": 160.0, "_miolo": []}
        b = {"_s": 100.0, "_e": 160.0, "_miolo": [[110.0, 120.0]]}
        self.assertEqual(cortar.chave_olhar(a), "100-160")
        self.assertNotEqual(cortar.chave_olhar(a), cortar.chave_olhar(b))

    def test_intervalos_mantidos(self):
        self.assertEqual(cortar.intervalos_mantidos(20, [[5, 10], [12, 14]]), [(0.0, 5), (10, 12), (14, 20)])
        self.assertEqual(cortar.intervalos_mantidos(20, []), [(0.0, 20)])


class TesteEscolher(Base):
    def test_nota_abaixo_de_60_nao_entra_e_60_entra(self):
        self.escrever([momento("01:00:00", "01:00:30", 59, "FRACO"), momento("01:10:00", "01:10:30", 60, "LIMITE"),
                       momento("01:20:00", "01:20:30", 85, "BOM")])
        r, saida = self.escolher()
        self.assertEqual([m["titulo"] for m in r], ["LIMITE", "BOM"])
        self.assertIn("nota 59", saida)

    def test_nao_enche_com_momento_fraco(self):
        self.escrever([momento("01:00:00", "01:00:30", 40), momento("01:10:00", "01:10:30", 55),
                       momento("01:20:00", "01:20:30", 80, "BOM")])
        r, saida = self.escolher(manter=3)
        self.assertEqual(len(r), 1)
        self.assertIn("não encho", saida)

    def test_trava_do_olhar_descarta_o_que_nao_passou(self):
        m1, m2 = momento("01:00:00", "01:00:30", 80, "PARADO"), momento("01:10:00", "01:10:30", 75, "OK")
        self.escrever([m1, m2])
        self.pasta.joinpath("olhar.json").write_text(json.dumps({
            "3600-3630": {"ok": False, "motivos": ["jogo parado em 77% do tempo"]},
            "4200-4230": {"ok": True, "motivos": []}}), encoding="utf-8")
        r, saida = self.escolher()
        self.assertEqual([m["titulo"] for m in r], ["OK"])
        self.assertIn("jogo parado em 77%", saida)
        r2, _ = self.escolher(ignorar_trava=True)
        self.assertEqual(len(r2), 2)

    def test_sem_olhar_avisa_mas_nao_bloqueia(self):
        self.escrever([momento("01:00:00", "01:00:30", 80)])
        r, saida = self.escolher()
        self.assertEqual(len(r), 1)
        self.assertIn("ainda não rodou 'olhar'", saida)

    def test_resultado_velho_de_outro_momento_nao_vale(self):
        self.escrever([momento("01:00:00", "01:00:30", 80, "NOVO")])
        self.pasta.joinpath("olhar.json").write_text(json.dumps({"1-2": {"ok": False, "motivos": ["x"]}}), encoding="utf-8")
        r, _ = self.escolher()
        self.assertEqual(len(r), 1)

    def test_miolo_chega_ao_escolhidos(self):
        self.escrever([momento("01:00:00", "01:01:00", 80, cortar_miolo=[["01:00:20", "01:00:40"]])])
        r, saida = self.escolher()
        self.assertEqual(r[0]["_miolo"], [[3620.0, 3640.0]])
        self.assertIn("tira 20 s do miolo", saida)
        gravado = json.loads(self.pasta.joinpath("escolhidos.json").read_text(encoding="utf-8"))
        self.assertEqual(gravado["cortes"][0]["_miolo"], [[3620.0, 3640.0]])

    def test_modo_automatico_nao_aplica_as_travas(self):
        self.pasta.joinpath("candidatos.json").write_text(
            json.dumps([{"trecho": 1, "inicio": 100, "fim": 250, "pico": 150, "calor": 30}]), encoding="utf-8")
        r, _ = self.escolher(auto=True)
        self.assertEqual(len(r), 1)  # a "nota" do automático é calor, não vale a regra de 60

    def test_sobreposicao_fica_so_com_o_melhor(self):
        self.escrever([momento("01:00:00", "01:00:40", 70, "A"), momento("01:00:20", "01:01:00", 90, "B")])
        r, _ = self.escolher()
        self.assertEqual([m["titulo"] for m in r], ["B"])


class TesteMedicoes(unittest.TestCase):
    def test_fala_e_silencio(self):
        cob, total, maior = olhar.fala_e_silencio([(1, 2, "a"), (10, 11, "b")], [(0, 20)])
        self.assertAlmostEqual(cob, 3.6, places=1)
        self.assertEqual(total, 20)
        self.assertAlmostEqual(maior, 8.6, places=1)  # de 11,4 até 20

    def test_silencio_longo_some_quando_o_miolo_e_removido(self):
        palavras = [(1, 2, "a"), (18, 19, "b")]
        _, _, com = olhar.fala_e_silencio(palavras, [(0, 20)])
        _, _, sem = olhar.fala_e_silencio(palavras, [(0, 5), (15, 20)])
        self.assertGreater(com, 10)
        self.assertLess(sem, 4)

    def test_sem_fala_nenhuma_o_silencio_e_o_corte_todo(self):
        cob, total, maior = olhar.fala_e_silencio([], [(0, 12)])
        self.assertEqual((cob, total, maior), (0, 12, 12))

    def test_tempo_morto_e_so_sem_fala_E_com_o_jogo_parado(self):
        self.assertAlmostEqual(olhar.maior_tempo_morto([], [(0, 10)], [[(2, 9)]]), 7.0, places=1)
        # com fala no meio, a sequência morta se divide
        self.assertLess(olhar.maior_tempo_morto([(5, 6, "x")], [(0, 10)], [[(2, 9)]]), 3.5)
        # silêncio com o jogo se mexendo não é tempo morto
        self.assertEqual(olhar.maior_tempo_morto([], [(0, 10)], [[]]), 0.0)

    def test_avaliar_motivos(self):
        ruim = {"parado_pct": 77, "maior_silencio": 10, "morto_max": 8, "fala_pct": 40}
        self.assertEqual(len(olhar.avaliar(ruim)), 3)
        self.assertEqual(olhar.avaliar({"parado_pct": 20, "maior_silencio": 6, "morto_max": 5, "fala_pct": 50}), [])
        self.assertIn("cortar_miolo", " ".join(olhar.avaliar({"parado_pct": 0, "maior_silencio": 22, "morto_max": 0})))


@unittest.skipUnless(TEM_FFMPEG, "ffmpeg não encontrado")
class TesteComFfmpegDeVerdade(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = Path(self._tmp.name)

    def gerar(self, nome, filtro, com_audio=True, dur=10):
        saida = self.d / nome
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-filter_complex", filtro]
        if com_audio:
            cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}"]
        cmd += ["-map", "[v]"]
        # com áudio: o vídeo vem do filtro e o áudio da entrada lavfi (índice 0, pois o filtro não tem entrada)
        if com_audio:
            cmd += ["-map", "0:a", "-c:a", "aac", "-shortest"]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(saida)]
        subprocess.run(cmd, check=True)
        return saida

    def test_corte_seco_remove_o_miolo_do_video_e_do_audio(self):
        v = self.gerar("o.mp4", "testsrc=size=320x240:rate=25:duration=20[v]", dur=20)
        cortar.aplicar_miolo(v, [[5, 10]], self.d / "e.mp4")
        self.assertAlmostEqual(cortar.duracao(self.d / "e.mp4"), 15.0, delta=0.5)
        self.assertTrue(cortar.tem_audio(self.d / "e.mp4"))
        self.assertAlmostEqual(cortar.duracao(v), 20.0, delta=0.3)  # o original não muda

    def test_corte_seco_com_varios_pedacos_e_sem_audio(self):
        v = self.gerar("o.mp4", "testsrc=size=320x240:rate=25:duration=20[v]", com_audio=False)
        cortar.aplicar_miolo(v, [[2, 4], [10, 14]], self.d / "e.mp4")
        self.assertAlmostEqual(cortar.duracao(self.d / "e.mp4"), 14.0, delta=0.5)
        self.assertFalse(cortar.tem_audio(self.d / "e.mp4"))

    def test_detecta_jogo_parado_e_jogo_em_movimento(self):
        meio = self.gerar("m.mp4", "testsrc=size=640x360:rate=25:duration=5[a];color=c=blue:size=640x360:rate=25:duration=5[b];"
                                   "[a][b]concat=n=2:v=1:a=0[v]", com_audio=False)
        med = olhar.medir(meio, [], [])
        self.assertTrue(40 <= med["parado_pct"] <= 60, med)
        mexe = self.gerar("x.mp4", "testsrc=size=640x360:rate=25:duration=10[v]", com_audio=False)
        self.assertEqual(olhar.medir(mexe, [], [])["parado_pct"], 0)

    def test_tirar_o_miolo_parado_faz_o_momento_passar(self):
        # 5 s se mexendo + 8 s parado + 5 s se mexendo: parado demais; sem o miolo parado, passa
        v = self.gerar("p.mp4", "testsrc=size=640x360:rate=25:duration=5[a];color=c=blue:size=640x360:rate=25:duration=8[b];"
                                "testsrc=size=640x360:rate=25:duration=5[c];[a][b][c]concat=n=3:v=1:a=0[v]", com_audio=False)
        palavras = [(0.5, 4.5, "oi"), (13.5, 17.5, "fim")]
        self.assertTrue(olhar.avaliar(olhar.medir(v, palavras, [])))
        self.assertEqual(olhar.avaliar(olhar.medir(v, palavras, [[5.2, 12.8]])), [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
