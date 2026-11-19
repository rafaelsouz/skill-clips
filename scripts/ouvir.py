"""Passos 1 e 2: baixa só o áudio da live, escuta tudo no computador e separa o que a IA vai ler.

Sai, na pasta da live:
  candidatos.txt   os trechos mais "quentes" da live (gritos, chat acelerado, reações). A IA lê SÓ ESTE arquivo.
  blocos/          a live inteira em blocos de 12 minutos (só pra quem quiser ler tudo, mais lento)

Marcas nas falas:
  [ALTO +11dB]   o streamer ficou bem mais alto que o normal daquele trecho (grito, surto)
  [CHAT 3.4x]    no replay do YouTube, o chat andou 3,4 vezes mais rápido que o normal
"""
import json
import math
import os
import re
import subprocess
import sys
import time
import wave
from pathlib import Path

from comum import (BLOCO_MIN, TRABALHO, gravar_json, hms, ler_json, motivo_trancada, rodar, slug, url_segura, ytdlp)

PEDACO = 600  # escuta 10 minutos por vez e guarda: se parar no meio, continua dali
LINKS = TRABALHO / "links.json"


# ---------------------------------------------------------------- ler a live (rápido, em primeiro plano)
def ler_live(url):
    """Lê título, duração e situação da live. Sai com aviso claro quando não dá pra baixar."""
    url_segura(url)
    links = ler_json(LINKS, {})
    if url in links and (TRABALHO / links[url] / "info.json").exists():
        return TRABALHO / links[url]
    print(f"\n[1/4] Lendo a live: {url}", flush=True)
    r = subprocess.run(ytdlp() + ["-J", "--no-playlist", "--no-warnings", "--", url], capture_output=True, text=True)
    if r.returncode != 0:
        motivo = motivo_trancada(r.stderr)
        if motivo:
            sys.exit(f"\n  ✗ NÃO DÁ PRA BAIXAR: {motivo}.\n    Isso é trava do site, não erro do clipper. "
                     "Escolha outra live (o 'buscar' já pula as trancadas).")
        if "kick.com" in url:
            sys.exit("\n  ✗ NÃO DÁ PRA BAIXAR: o Kick bloqueou o download dessa live agora.\n"
                     "    Quase todo streamer grande também passa a live no YouTube ou na Twitch: "
                     "use o link de lá (ou rode 'buscar').")
        print(r.stderr[-1200:])
        sys.exit("\n  ✗ Não consegui ler essa live. Confira se o link abre no navegador, rode 'instalar' "
                 "(atualiza o baixador) e tente de novo.")
    meta = json.loads(r.stdout)
    situacao = meta.get("live_status")
    if situacao in ("is_live", "is_upcoming"):
        sys.exit("\n  ✗ ESSA LIVE AINDA ESTÁ AO VIVO (ou nem começou).\n    Só dá pra cortar depois que ela acabar. "
                 "Escolha uma live que já terminou (o 'buscar' mostra as de hoje que já acabaram).")
    if situacao == "post_live":
        sys.exit("\n  ✗ ESSA LIVE ACABOU AGORA e o site ainda está processando o vídeo.\n"
                 "    Tente de novo daqui a algumas horas, ou escolha outra live.")
    nome = slug(f"{meta.get('uploader') or meta.get('channel') or 'live'}-{meta.get('title', '')}")
    pasta = TRABALHO / nome
    pasta.mkdir(parents=True, exist_ok=True)
    info = {k: meta.get(k) for k in ("id", "title", "uploader", "channel", "duration", "view_count",
                                     "webpage_url", "extractor_key", "was_live", "live_status")}
    info["url"] = url
    gravar_json(pasta / "info.json", info)
    links[url] = nome
    gravar_json(LINKS, links)
    dur = info.get("duration") or 0
    print(f"      {info['title']}  |  {info.get('uploader')}  |  {hms(dur)}", flush=True)
    return pasta


# ---------------------------------------------------------------- download do áudio (+ chat)
def audio_pronto(pasta):
    ok = [p for p in pasta.glob("audio.*") if p.suffix not in (".part", ".ytdl", ".temp")
          and not list(pasta.glob(p.name + ".part"))]
    return None if list(pasta.glob("audio.*.part")) else (ok[0] if ok else None)


def baixar_audio(pasta):
    info = ler_json(pasta / "info.json", {})
    url = info["url"]
    if not audio_pronto(pasta):
        print("\n[1/4] Baixando só o áudio da live (continua de onde parou se cair)…", flush=True)
        rodar(ytdlp() + ["-f", "ba/b", "-x", "--no-playlist", "--no-warnings", "-q", "--progress", "--newline",
                         "--progress-delta", "15", "--progress-template",
                         "download:      baixado %(progress._percent_str)s", "--continue",
                         "-o", str(pasta / "audio.%(ext)s"), "--", url])
    if (info.get("extractor_key") or "").lower().startswith("youtube") and not (pasta / "chat.feito").exists():
        print("      pegando o replay do chat (se a live tiver)…", flush=True)
        try:
            rodar(ytdlp() + ["--skip-download", "--write-subs", "--sub-langs", "live_chat", "--no-warnings", "-q",
                             "-o", str(pasta / "chat"), "--", url], timeout=900)
        except Exception as e:  # sem chat não é erro: só não tem a marca [CHAT]
            print(f"      sem replay de chat ({type(e).__name__})", flush=True)
        (pasta / "chat.feito").write_text("ok")


def wav16k(pasta):
    w = pasta / "audio16k.wav"
    if not w.exists():
        audio = audio_pronto(pasta)
        tmp = pasta / "audio16k.tmp.wav"
        print("      preparando o áudio pra escutar…", flush=True)
        rodar(["ffmpeg", "-y", "-loglevel", "error", "-i", audio, "-ac", "1", "-ar", "16000", tmp])
        tmp.replace(w)
    return w


# ---------------------------------------------------------------- transcrição (10 min por vez, com progresso salvo)
def modelo_padrao():
    return "base" if (os.cpu_count() or 4) >= 6 else "tiny"


def transcrever(pasta, modelo=None, idioma=None):
    saida = pasta / "transcricao.json"
    if saida.exists():
        return ler_json(saida)
    import numpy as np
    from faster_whisper import WhisperModel
    modelo = modelo or modelo_padrao()
    w = wav16k(pasta)
    with wave.open(str(w)) as f:
        sr, n = f.getframerate(), f.getnframes()
    total = n / sr
    partes = max(1, math.ceil(total / PEDACO))
    pdir = pasta / "escuta"
    pdir.mkdir(exist_ok=True)
    print(f"\n[2/4] Escutando a live aqui no computador ({hms(total)}, {partes} partes de 10 min)…", flush=True)
    m, t0, feitas, lingua = None, time.time(), 0, idioma
    for k in range(partes):
        arq = pdir / f"{k:04d}.json"
        if arq.exists():
            continue
        if m is None:
            m = WhisperModel(modelo, device="cpu", compute_type="int8")
        with wave.open(str(w)) as f:
            f.setpos(min(n, k * PEDACO * sr))
            a = np.frombuffer(f.readframes(PEDACO * sr), dtype=np.int16).astype(np.float32) / 32768
        segs, inf = m.transcribe(a, language=lingua, vad_filter=True, beam_size=1, condition_on_previous_text=False)
        out = [[round(s.start + k * PEDACO, 2), round(s.end + k * PEDACO, 2), s.text.strip()]
               for s in segs if s.text.strip()]
        if lingua is None and (inf.language_probability or 0) > 0.8 and out:
            lingua = inf.language  # fixa a língua quando tem certeza (fica mais rápido e mais certo)
        gravar_json(arq, {"idioma": inf.language, "segmentos": out})
        feitas += 1
        falta = (time.time() - t0) / feitas * (partes - k - 1)
        print(f"      escutei {hms(min(total, (k + 1) * PEDACO))} de {hms(total)} "
              f"({100 * (k + 1) / partes:.0f}%), falta uns {int(falta / 60) + 1} min", flush=True)
    segs, linguas = [], {}
    for k in range(partes):
        d = ler_json(pdir / f"{k:04d}.json", {})
        segs += d.get("segmentos", [])
        if d.get("segmentos"):
            linguas[d.get("idioma")] = linguas.get(d.get("idioma"), 0) + len(d["segmentos"])
    dados = {"idioma": idioma or (max(linguas, key=linguas.get) if linguas else None), "segmentos": segs}
    gravar_json(saida, dados)
    return dados


# ---------------------------------------------------------------- picos de volume
def picos_de_volume(pasta):
    import numpy as np
    saida = pasta / "volume.json"
    if saida.exists():
        return ler_json(saida)
    dbs = []
    with wave.open(str(wav16k(pasta))) as f:  # lê aos poucos: live de 10 h não cabe inteira na memória
        sr = f.getframerate()
        while True:
            q = f.readframes(sr * 600)
            if not q:
                break
            a = np.frombuffer(q, dtype=np.int16).astype(np.float32)
            seg = len(a) // sr
            if seg == 0:
                break
            rms = np.sqrt(np.mean(a[: seg * sr].reshape(seg, sr) ** 2, axis=1) + 1e-6)
            dbs.append(20 * np.log10(rms / 32768 + 1e-9))
    db = np.concatenate(dbs) if dbs else np.zeros(1)
    seg = len(db)
    fala = db > np.percentile(db, 20)                       # ignora silêncio no cálculo da base
    base = np.zeros_like(db)
    for i in range(0, seg, 60):                             # "normal" = mediana dos 5 min em volta
        j0, j1 = max(0, i - 150), min(seg, i + 150)
        janela = db[j0:j1][fala[j0:j1]]
        base[i:i + 60] = np.median(janela) if len(janela) else np.median(db)
    excesso = db - base
    corte = max(8.0, float(np.percentile(excesso, 98)))
    picos = [[int(i), round(float(excesso[i]), 1)] for i in np.where(excesso >= corte)[0]]
    gravar_json(saida, picos)
    return picos


# ---------------------------------------------------------------- velocidade do chat
def picos_do_chat(pasta):
    import numpy as np
    arq = next(iter(pasta.glob("chat*.live_chat.json")), None)
    if not arq:
        return []
    tempos = []
    with open(arq, encoding="utf-8", errors="ignore") as f:
        for linha in f:
            try:
                ms = json.loads(linha)["replayChatItemAction"]["videoOffsetTimeMsec"]
                tempos.append(int(ms) / 1000)
            except (KeyError, ValueError, json.JSONDecodeError):
                continue
    if len(tempos) < 50:
        return []
    caixas = np.bincount((np.array(tempos) // 10).astype(int))   # mensagens a cada 10 s
    picos = []
    for i, c in enumerate(caixas):
        viz = caixas[max(0, i - 30): i + 30]
        normal = max(1.0, float(np.median(viz)))
        if c >= 8 and c / normal >= 2.5:
            picos.append([i * 10, round(c / normal, 1)])
    return picos


# ---------------------------------------------------------------- texto que a IA lê
def _linha(s, e, t, vol, chat):
    marca = ""
    alto = max((vol.get(x, 0) for x in range(int(s), int(e) + 1)), default=0)
    if alto:
        marca += f"  [ALTO +{alto:.0f}dB]"
    ch = max((r for c, r in chat if s - 10 <= c <= e), default=0)
    if ch:
        marca += f"  [CHAT {ch}x]"
    return f"[{hms(s)}] {t}{marca}"


def blocos(pasta, trans, volume, chat):
    pb = pasta / "blocos"
    pb.mkdir(exist_ok=True)
    (pasta / "momentos").mkdir(exist_ok=True)
    vol = dict((s, e) for s, e in volume)
    segs = trans["segmentos"]
    fim = segs[-1][1] if segs else 0
    passo = BLOCO_MIN * 60
    n = 0
    for k, ini in enumerate(range(0, int(fim) + 1, passo), start=1):
        linhas = [_linha(s, e, t, vol, chat) for s, e, t in segs if ini <= s < ini + passo]
        if not linhas:
            continue
        n += 1
        cab = (f"# BLOCO {k:02d} — de {hms(ini)} a {hms(ini + passo)}\n"
               f"# Escreva os momentos deste bloco em: momentos/bloco_{k:02d}.json\n\n")
        (pb / f"bloco_{k:02d}.txt").write_text(cab + "\n".join(linhas) + "\n", encoding="utf-8")
    return n


REACAO = re.compile(r"(meu deus|caralho|porra|n[ãa]o acredito|que isso|mds|kkk+|ha(ha)+|rs(rs)+|nossa|cara+lho|"
                    r"oh my god|what the|no way|holy|wtf|insane|crazy|let'?s go|bro what|ai meu|socorro|pqp)", re.I)


def candidatos(pasta, trans, volume, chat, quantos=12, janela=150):
    """Os trechos mais quentes da live, sem se sobrepor. É o que a IA lê: rápido até numa IA pequena."""
    import numpy as np
    segs = trans["segmentos"]
    if not segs:
        return 0
    fim = int(segs[-1][1]) + 1
    calor = np.zeros(fim + janela)
    for s, e in volume:
        calor[int(s)] += e
    for c, r in chat:
        calor[int(c):int(c) + 10] += r * 3
    for s, e, t in segs:
        calor[int(s)] += 5 * len(REACAO.findall(t)) + 0.03 * len(t.split())
    calor[:240] *= 0.3            # começo da live: chat sempre explode no "boa noite", não é momento
    calor[max(0, fim - 240):] *= 0.3  # fim: despedida
    soma = np.convolve(calor, np.ones(janela), "valid")[:max(1, fim - janela // 2)]
    escolhidos = []
    for i in np.argsort(-soma):
        if len(escolhidos) >= quantos:
            break
        if all(abs(int(i) - j) >= janela for j in escolhidos):
            escolhidos.append(int(i))
    escolhidos.sort()
    topo = max(float(soma[i]) for i in escolhidos) or 1
    vol = dict((s, e) for s, e in volume)
    partes, meta = [], []
    for n, i in enumerate(escolhidos, 1):
        linhas = [_linha(s, e, t, vol, chat) for s, e, t in segs if i <= s < i + janela]
        if not linhas:
            continue
        pico = i + int(np.argmax(np.convolve(calor[i:i + janela], np.ones(10), "same")))
        nota = round(100 * float(soma[i]) / topo)
        meta.append({"trecho": n, "inicio": i, "fim": i + janela, "pico": pico, "calor": nota})
        partes.append(f"## TRECHO {n:02d} — de {hms(i)} a {hms(i + janela)} (calor {nota})\n" + "\n".join(linhas))
    (pasta / "momentos").mkdir(exist_ok=True)
    cab = ("# OS TRECHOS MAIS QUENTES DA LIVE\n"
           "# Leia todos e escreva os melhores momentos em: momentos/candidatos.json (formato no SKILL.md).\n"
           "# 'calor' = o quanto o streamer gritou, o chat acelerou e teve reação. Mesmo assim, confirme pela fala.\n\n")
    (pasta / "candidatos.txt").write_text(cab + "\n\n".join(partes) + "\n", encoding="utf-8")
    gravar_json(pasta / "candidatos.json", meta)
    return len(partes)


# ---------------------------------------------------------------- o trabalho inteiro (em segundo plano)
def trabalho_preparar(pasta, modelo=None, idioma=None, trechos=12):
    baixar_audio(pasta)
    trans = transcrever(pasta, modelo, idioma)
    print("\n[2/4] Marcando onde o streamer ficou alto e onde o chat acelerou…", flush=True)
    volume = picos_de_volume(pasta)
    chat = picos_do_chat(pasta)
    blocos(pasta, trans, volume, chat)
    n = candidatos(pasta, trans, volume, chat, quantos=trechos)
    print(f"      {len(volume)} segundos de grito/surto, {len(chat)} picos de chat, {n} trechos quentes", flush=True)
