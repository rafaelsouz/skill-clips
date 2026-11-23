"""Passos 3 e 4: junta as notas que a IA deu, fica com os melhores e monta os cortes verticais.

Cada corte: câmera do streamer em cima, jogo embaixo, título de gancho, legenda palavra por palavra
e um .txt com a legenda do post e as hashtags.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

from comum import (CORTES, YUNET, gravar_json, hms, ler_json, limpar_texto, rodar, segundos, ytdlp)  # noqa: F401

FONTES = Path(__file__).resolve().parent.parent / "fontes"
L, A = 1080, 1920          # vídeo vertical
CAM_H = 768                # faixa da câmera em cima
JOGO_H = A - CAM_H         # faixa do jogo embaixo


# ================================================================ escolher
CHAVES = {"inicio": ("inicio", "início", "start", "comeco", "começo", "de"),
          "fim": ("fim", "end", "final", "ate", "até"),
          "nota": ("nota", "score", "pontos", "pontuacao", "pontuação"),
          "titulo": ("titulo", "título", "title", "gancho", "hook"),
          "post": ("post", "legenda", "caption", "descricao", "descrição")}


def ler_tolerante(arq):
    """A IA às vezes escreve o JSON com ```json, vírgula sobrando ou comentário. Aceita tudo isso."""
    t = Path(arq).read_text(encoding="utf-8", errors="ignore").strip()
    t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t)
    for tentativa in (t, None):
        if tentativa is None:
            ini = [i for i in (t.find("["), t.find("{")) if i >= 0]
            fim = max(t.rfind("]"), t.rfind("}"))
            if not ini or fim < 0:
                return None
            tentativa = re.sub(r"//[^\n]*", "", t[min(ini):fim + 1])
            tentativa = re.sub(r",\s*([\]}])", r"\1", tentativa)
        try:
            d = json.loads(tentativa)
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict):
            d = d.get("momentos") or d.get("cortes") or d.get("moments") or [d]
        return d if isinstance(d, list) else None
    return None


def _campo(m, nome):
    for k in CHAVES[nome]:
        if k in m and m[k] not in (None, ""):
            return m[k]
    return None


def _titulo_auto(pasta, s, e):
    # prefere a escuta refinada (modelo melhor nos trechos quentes) quando existir
    trans = (ler_json(pasta / "transcricao_refinada.json", None)
             or ler_json(pasta / "transcricao.json", {})).get("segmentos", [])
    falas = [t for a, b, t in trans if s <= a <= e and len(t.split()) >= 3]
    if not falas:
        return "OLHA O QUE ACONTECEU NA LIVE"
    t = max(falas, key=lambda x: (x.count("!") + len(re.findall(r"[A-ZÁÉÍÓÚÃÕÇ]{3,}", x)), len(x)))
    t = re.sub(r"\s+", " ", t).strip(" .,…").upper()
    return (t[:42].rsplit(" ", 1)[0] + "…") if len(t) > 45 else t


def momentos_automaticos(pasta):
    """Sem a IA ter escrito nada: usa os trechos mais quentes (pico de grito/chat) como cortes."""
    cand = ler_json(pasta / "candidatos.json", [])
    nome = ler_json(pasta / "info.json", {}).get("uploader") or ""
    out = []
    for c in cand:
        s, e = max(0, c["pico"] - 15), c["pico"] + 30
        out.append({"inicio": s, "fim": e, "nota": c["calor"], "titulo": _titulo_auto(pasta, s, e),
                    "post": f"Olha isso na live {('do ' + nome) if nome else ''} 😳 #gta6 #gtavi #gta #cortes #streamer",
                    "_auto": True})
    return out


def escolher(pasta, manter=3, minimo=20, maximo=90, auto=False):
    dur_live = float(ler_json(pasta / "info.json", {}).get("duration") or 10 ** 7)
    brutos, ruins = [], []
    for arq in sorted((pasta / "momentos").glob("*.json")):
        dados = ler_tolerante(arq)
        if dados is None:
            ruins.append(f"{arq.name}: não é um JSON que dá pra ler")
            continue
        brutos += [(arq.stem, m) for m in dados]
    if auto or not brutos:
        if not auto:
            print("A IA não escreveu nenhum momento ainda: usando os trechos mais quentes da live (modo automático).")
        brutos = [("automatico", m) for m in momentos_automaticos(pasta)]
    todos = []
    for origem, m in brutos:
        try:
            s, e = segundos(_campo(m, "inicio")), segundos(_campo(m, "fim"))
        except (TypeError, ValueError, AttributeError):
            ruins.append(f"{origem}: tempo que não entendi em {str(m)[:70]}")
            continue
        if e < s:
            s, e = e, s
        s, e = max(0.0, s), min(dur_live, e)
        if e - s < minimo:
            e = min(dur_live, s + minimo)
        if e - s > maximo:
            e = s + maximo
        try:
            nota = float(str(_campo(m, "nota") or 70).split("/")[0])
        except ValueError:
            nota = 70.0
        # título e legenda do post vêm da IA, que leu fala de terceiros: sem links nem < >, tamanho limitado
        titulo = limpar_texto(_campo(m, "titulo") or "", 70) or _titulo_auto(pasta, s, e)
        post = limpar_texto(_campo(m, "post") or "", 400)
        if "#gta6" not in post.lower():
            post = (post + " #gta6 #gtavi #gta #cortes").strip()
        todos.append({**m, "titulo": titulo, "post": post, "nota": nota, "_s": s, "_e": e, "_bloco": origem})
    if ruins:
        print("ATENÇÃO, pulei:", *ruins, sep="\n  ")
    todos.sort(key=lambda m: -m["nota"])
    escolhidos = []
    for m in todos:
        if any(m["_s"] < o["_e"] and o["_s"] < m["_e"] for o in escolhidos):
            continue  # sobrepõe um corte melhor
        escolhidos.append(m)
        if len(escolhidos) >= manter:
            break
    escolhidos.sort(key=lambda m: m["_s"])
    gravar_json(pasta / "escolhidos.json", {"possiveis": len(todos), "cortes": escolhidos})
    print(f"\nAchei {len(todos)} momentos possíveis e fiquei com os {len(escolhidos)} melhores:")
    for i, m in enumerate(escolhidos, 1):
        print(f"  {i}. [{hms(m['_s'])}–{hms(m['_e'])}] nota {m['nota']:.0f}  {m.get('titulo', '')}")
    return escolhidos


# ================================================================ câmera do streamer
def achar_camera(video):
    """Procura o rosto em 14 quadros do trecho e devolve a caixa (x, y, l, a) do rosto mais frequente."""
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(str(video))
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    esc = 960 / W
    det = cv2.FaceDetectorYN.create(str(YUNET), "", (960, int(H * esc)), 0.75)
    caixas = []
    for k in range(14):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * (k + 0.5) / 14))
        ok, q = cap.read()
        if not ok:
            continue
        _, rostos = det.detect(cv2.resize(q, (960, int(H * esc))))
        for r in rostos if rostos is not None else []:
            if r[14] >= 0.75 and r[3] / esc > H * 0.05:
                caixas.append([v / esc for v in r[:4]])
    cap.release()
    if len(caixas) < 3:
        return W, H, None
    c = np.array(caixas)
    centros = c[:, :2] + c[:, 2:] / 2
    med = np.median(centros, axis=0)
    perto = c[np.linalg.norm(centros - med, axis=1) < W * 0.08]
    if len(perto) < 3:
        return W, H, None
    rosto = np.median(perto, axis=0)
    if rosto[3] > H * 0.12:  # rosto grande = não é a caixinha da câmera (podcast, live só de câmera): imagem inteira
        return W, H, None
    return W, H, rosto


def _par(v):
    return int(v) // 2 * 2


def filtro(W, H, rosto):
    if rosto is None:  # sem câmera: jogo inteiro no meio, fundo desfocado
        return ("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:3[bg];"
                "[0:v]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p,ass=legenda.ass:fontsdir=fontes[v]")
    fx, fy, fw, fh = rosto
    cw = min(W, max(fw * 3.2, 200))
    ch = cw * CAM_H / L
    if ch > H:
        ch, cw = H, H * L / CAM_H
    cx = min(max(fx + fw / 2 - cw / 2, 0), W - cw)
    cy = min(max(fy + fh * 0.65 - ch / 2, 0), H - ch)
    gw = min(W, H * L / JOGO_H)
    gx = (W - gw) / 2
    return (f"[0:v]crop={_par(cw)}:{_par(ch)}:{_par(cx)}:{_par(cy)},scale={L}:{CAM_H},setsar=1[cam];"
            f"[0:v]crop={_par(gw)}:{_par(H)}:{_par(gx)}:0,scale={L}:{JOGO_H},setsar=1[jogo];"
            f"[cam][jogo]vstack=inputs=2,format=yuv420p,ass=legenda.ass:fontsdir=fontes[v]")


# ================================================================ legenda
def _ass_tempo(t):
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def _limpo(txt):
    return txt.replace("{", "(").replace("}", ")").replace("\\", "/").strip()


def audio_16k(arquivo):
    """O ffmpeg lê o áudio (não depende do leitor do faster-whisper, que quebra entre versões)."""
    import numpy as np
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(arquivo), "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768


def palavras_do_trecho(video, idioma, modelo="small"):
    from faster_whisper import WhisperModel
    m = WhisperModel(modelo, device="cpu", compute_type="int8")
    segs, _ = m.transcribe(audio_16k(video), language=idioma, word_timestamps=True, vad_filter=True,
                           condition_on_previous_text=False)
    return [(w.start, w.end, w.word.strip()) for s in segs for w in (s.words or []) if w.word.strip()]


def escrever_ass(caminho, titulo, palavras, dur, com_camera):
    y_titulo = CAM_H if com_camera else 520
    y_leg = CAM_H + int(JOGO_H * 0.62) if com_camera else 1480
    linhas = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {L}", f"PlayResY: {A}", "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        "Style: Gancho,Montserrat ExtraBold,56,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,3,18,0,5,60,60,0,1",
        "Style: Leg,Montserrat ExtraBold,80,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,7,3,5,40,40,0,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        f"Dialogue: 1,{_ass_tempo(0)},{_ass_tempo(dur)},Gancho,,0,0,0,,{{\\pos(540,{y_titulo})}}{_limpo(titulo).upper()}",
    ]
    grupos = [palavras[i:i + 3] for i in range(0, len(palavras), 3)]
    for g, grupo in enumerate(grupos):
        prox = grupos[g + 1][0][0] if g + 1 < len(grupos) else dur
        for k, (s, e, _) in enumerate(grupo):
            fim = grupo[k + 1][0] if k + 1 < len(grupo) else (prox if prox - e < 0.6 else e + 0.25)
            txt = " ".join(("{\\c&H00E5FF&}" if j == k else "{\\c&HFFFFFF&}") + _limpo(w).upper()
                           for j, (_, _, w) in enumerate(grupo))
            linhas.append(f"Dialogue: 0,{_ass_tempo(s)},{_ass_tempo(fim)},Leg,,0,0,0,,{{\\pos(540,{y_leg})}}{txt}")
    Path(caminho).write_text("\n".join(linhas) + "\n", encoding="utf-8")


# ================================================================ baixar só o pedaço
FORMATO = "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b"


def tem_video(arq):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-count_packets", "-show_entries",
                        "stream=nb_read_packets", "-of", "csv=p=0", str(arq)], capture_output=True, text=True)
    return r.stdout.strip().isdigit() and int(r.stdout.strip()) > 10


def baixar_trecho(pasta, url, s, e, destino):
    """Baixa só os segundos do corte. 1º jeito: o baixador pede só o pedaço. 2º jeito: o ffmpeg pula direto pro
    minuto certo no link do vídeo. Nunca baixa a live inteira (uma live de 10 h teria dezenas de GB)."""
    print("      baixando só esse pedaço do vídeo…", flush=True)
    try:
        rodar(ytdlp() + ["-f", FORMATO, "--no-playlist", "--no-warnings", "-q", "--download-sections",
                         f"*{s:.2f}-{e:.2f}", "--force-keyframes-at-cuts", "--merge-output-format", "mp4",
                         "-o", str(destino), "--", url], timeout=1800)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass
    if destino.exists() and tem_video(destino):
        return
    destino.unlink(missing_ok=True)
    print("      tentando de outro jeito (pulando direto pro minuto do corte)…", flush=True)
    r = subprocess.run(ytdlp() + ["-f", FORMATO, "-g", "--no-playlist", "--no-warnings", "--", url],
                       capture_output=True, text=True)
    links = [l for l in r.stdout.split() if l.startswith("http")]
    if links:
        entrada = []
        for l in links[:2]:
            entrada += ["-ss", f"{s:.2f}", "-i", l]
        mapa = ["-map", "0:v:0", "-map", f"{len(links[:2]) - 1}:a:0?"]
        try:
            rodar(["ffmpeg", "-y", "-loglevel", "error", *entrada, "-t", f"{e - s:.2f}", *mapa,
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-c:a", "aac", "-b:a", "192k", destino],
                  timeout=1800)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
    if destino.exists() and tem_video(destino):
        return
    destino.unlink(missing_ok=True)
    raise SystemExit("NÃO CONSEGUI BAIXAR esse pedaço do vídeo. Rode 'instalar' (atualiza o baixador) e depois "
                     "'fazer' de novo: os cortes que já ficaram prontos não são refeitos.")


# ================================================================ fazer
def fazer(pasta, modelo_legenda="small"):
    info = ler_json(pasta / "info.json", {})
    escolhidos = ler_json(pasta / "escolhidos.json", {}).get("cortes")
    if not escolhidos:
        raise SystemExit("Nenhum corte escolhido ainda. Rode antes: clip.py escolher " + pasta.name)
    idioma = ler_json(pasta / "transcricao.json", {}).get("idioma")
    destino = CORTES / pasta.name
    destino.mkdir(parents=True, exist_ok=True)
    feitos = []
    for i, m in enumerate(escolhidos, 1):
        s, e = m["_s"], m["_e"]
        print(f"\n[4/4] Corte {i}/{len(escolhidos)}: {m.get('titulo', '')}  ({hms(s)}–{hms(e)})", flush=True)
        tdir = pasta / f"corte_{i:02d}"
        tdir.mkdir(exist_ok=True)
        saida = destino / f"corte_{i:02d}.mp4"
        assinatura = f"{s:.2f}-{e:.2f}-{m.get('titulo', '')}"
        if saida.exists() and (tdir / "feito.txt").exists() and (tdir / "feito.txt").read_text(encoding="utf-8") == assinatura:
            print("      já estava pronto", flush=True)
            feitos.append(saida)
            continue
        trecho = tdir / "trecho.mp4"
        if (tdir / "de.txt").exists() and (tdir / "de.txt").read_text() != f"{s:.2f}-{e:.2f}":
            trecho.unlink(missing_ok=True)  # o momento mudou: baixa o pedaço novo
        if trecho.exists() and not tem_video(trecho):
            trecho.unlink()
        if not trecho.exists():
            baixar_trecho(pasta, info["url"], s, e, trecho)
            (tdir / "de.txt").write_text(f"{s:.2f}-{e:.2f}")
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                    "csv=p=0", str(trecho)], capture_output=True, text=True).stdout.strip())
        print("      procurando a câmera do streamer…")
        W, H, rosto = achar_camera(trecho)
        print("      câmera do streamer achada: câmera em cima, jogo embaixo" if rosto is not None
              else "      sem caixinha de câmera: imagem inteira no meio com fundo desfocado", flush=True)
        print("      legenda palavra por palavra…")
        palavras = palavras_do_trecho(trecho, idioma, modelo_legenda)
        escrever_ass(tdir / "legenda.ass", m.get("titulo", ""), palavras, dur, rosto is not None)
        (tdir / "fontes").mkdir(exist_ok=True)
        for fonte in FONTES.glob("*.ttf"):
            shutil.copy(fonte, tdir / "fontes" / fonte.name)
        rodar(["ffmpeg", "-y", "-loglevel", "error", "-i", "trecho.mp4", "-filter_complex",
               filtro(W, H, rosto), "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "medium",
               "-crf", "19", "-r", "30", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
               str(saida.resolve())], cwd=tdir)
        post = (f"{m.get('titulo', '')}\n\n{m.get('post', '').strip()}\n\n"
                f"Corte da live de {info.get('uploader') or info.get('channel')}: {info.get('webpage_url') or info['url']}\n")
        saida.with_suffix(".txt").write_text(post, encoding="utf-8")
        (tdir / "feito.txt").write_text(assinatura, encoding="utf-8")
        feitos.append(saida)
        print(f"      PRONTO: {saida}", flush=True)
    for velho in destino.glob("corte_*.*"):  # escolheu menos cortes que da última vez: tira os que sobraram
        if velho.stem not in {f.stem for f in feitos}:
            velho.unlink(missing_ok=True)
    print(f"\n{len(feitos)} cortes prontos pra postar em {destino}")
    return feitos
