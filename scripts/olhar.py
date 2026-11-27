"""Olhar antes de cortar: pra cada momento que a IA escolheu, baixa só aquele pedaço em baixa qualidade e mede
  - quanto do tempo tem FALA e qual o MAIOR SILÊNCIO (a escuta é a mesma que vira legenda);
  - quanto do tempo o JOGO ficou PARADO (centro do quadro sem se mexer por 1,5 s ou mais: menu, pausa, tela de carregamento);
  - se há trechos longos SEM FALA E SEM MOVIMENTO (tempo morto).
Gera uma folha de quadros (6 por momento) pra IA olhar e grava olhar.json: o 'escolher' descarta o que não passa na trava.
Limites PROVISÓRIOS (calibrados em poucos cortes): mude aqui se a prática mostrar outro número."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import cortar
from comum import gravar_json, hms, ler_json

PARADO_MAX = 0.20      # no máximo 20% do tempo com o jogo parado
SILENCIO_MAX = 6.0     # maior silêncio (s) depois de tirar o miolo morto
MORTO_MAX = 5.0        # no máximo 5 s seguidos sem fala e sem movimento


def fala_e_silencio(palavras, mantidos, folga=0.4):
    """palavras [(inicio, fim, texto)] em tempo do trecho; mantidos [(a, b)] = pedaços que ficam no corte.
    Devolve (segundos com fala, segundos totais, maior silêncio contínuo)."""
    total = cobertura = maior = 0.0
    for a, b in mantidos:
        total += b - a
        faixas = sorted((max(a, s - folga), min(b, e + folga)) for s, e, _ in palavras if e + folga > a and s - folga < b)
        ultimo = a
        for x, y in faixas:
            if x > ultimo:
                maior = max(maior, x - ultimo)
            if y > ultimo:
                cobertura += y - max(x, ultimo)
                ultimo = y
        maior = max(maior, b - ultimo)
    return cobertura, total, maior


def intervalos_parados(video, a, b):
    """Intervalos (tempo relativo a 'a') em que o centro do quadro (sem a câmera dos cantos) ficou parado por >= 1,5 s."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats"]
    if a > 0:
        cmd += ["-ss", f"{a:.3f}"]
    cmd += ["-t", f"{b - a:.3f}", "-i", str(video), "-an", "-vf", "crop=iw*0.55:ih:iw*0.225:0,freezedetect=n=-55dB:d=1.5",
            "-f", "null", "-"]
    saida = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore").stderr
    inicios = [float(x) for x in re.findall(r"freeze_start: ([\d.]+)", saida)]
    fins = [float(x) for x in re.findall(r"freeze_end: ([\d.]+)", saida)]
    return [(st, fins[k] if k < len(fins) else b - a) for k, st in enumerate(inicios)]


def maior_tempo_morto(palavras, mantidos, parados, folga=0.4, passo=0.5):
    """Maior sequência (s) sem fala E com o jogo parado, dentro de cada pedaço que fica."""
    maior = 0.0
    for (a, b), congelados in zip(mantidos, parados):
        seq, t = 0.0, 0.0
        while t < b - a:
            meio = a + t + passo / 2
            fala = any(s - folga <= meio <= e + folga for s, e, _ in palavras)
            parado = any(f0 <= t + passo / 2 <= f1 for f0, f1 in congelados)
            seq = seq + passo if (parado and not fala) else 0.0
            maior = max(maior, seq)
            t += passo
    return maior


def medir(video, palavras, miolo_rel):
    mantidos = cortar.intervalos_mantidos(cortar.duracao(video), miolo_rel)
    cobertura, total, silencio = fala_e_silencio(palavras, mantidos)
    parados = [intervalos_parados(video, a, b) for a, b in mantidos]
    parado_s = sum(f1 - f0 for lista in parados for f0, f1 in lista)
    return {"duracao": round(total, 1), "fala_pct": round(100 * cobertura / total) if total else 0,
            "maior_silencio": round(silencio, 1), "parado_pct": round(100 * parado_s / total) if total else 0,
            "morto_max": round(maior_tempo_morto(palavras, mantidos, parados), 1)}


def avaliar(med):
    """Lista dos motivos pelos quais o momento NÃO passa na trava (vazia = passa)."""
    motivos = []
    if med["parado_pct"] > PARADO_MAX * 100:
        motivos.append(f"jogo parado em {med['parado_pct']}% do tempo (menu, pausa ou tela de carregamento)")
    if med["maior_silencio"] > SILENCIO_MAX:
        motivos.append(f"silêncio de {med['maior_silencio']:.0f} s (tire com cortar_miolo)")
    if med["morto_max"] > MORTO_MAX:
        motivos.append(f"{med['morto_max']:.0f} s seguidos sem fala e sem movimento")
    return motivos


def folha(video, saida, n=6):
    """Folha com n quadros espalhados pelo momento (3 por linha), pra IA olhar."""
    dur = max(cortar.duracao(video), 1.0)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vf",
                    f"fps={n}/{dur:.3f},scale=320:-2,tile=3x{(n + 2) // 3}", "-frames:v", "1", str(saida)],
                   capture_output=True)


def olhar(pasta, modelo="small"):
    info = ler_json(pasta / "info.json", {})
    idioma = ler_json(pasta / "transcricao.json", {}).get("idioma")
    momentos, ruins = cortar.ler_momentos(pasta)
    if ruins:
        print("ATENÇÃO, pulei:", *ruins, sep="\n  ")
    momentos = sorted((m for m in momentos if m["nota"] >= cortar.NOTA_MINIMA), key=lambda x: x["_s"])
    if not momentos:
        raise SystemExit(f"Nenhum momento com nota >= {cortar.NOTA_MINIMA} em momentos/*.json. Escreva os momentos antes.")
    pasta_o = pasta / "olhar"
    pasta_o.mkdir(exist_ok=True)
    resultados, linhas = {}, []
    for k, m in enumerate(momentos, 1):
        chave, s, e = cortar.chave_olhar(m), m["_s"], m["_e"]
        print(f"\n[olhar {k}/{len(momentos)}] {hms(s)}–{hms(e)}  {m['titulo']}", flush=True)
        video = pasta_o / f"{chave}.mp4"
        if not (video.exists() and cortar.tem_video(video)):
            video.unlink(missing_ok=True)
            cortar.baixar_trecho(pasta, info["url"], s, e, video, formato=cortar.FORMATO_LEVE)
        cache = pasta_o / f"{chave}.palavras.json"
        palavras = ler_json(cache, None)
        if palavras is None:
            print("      escutando o momento…", flush=True)
            palavras = cortar.palavras_do_trecho(video, idioma, modelo)
            gravar_json(cache, palavras)
        med = medir(video, [tuple(p) for p in palavras], [[a - s, b - s] for a, b in m["_miolo"]])
        png = pasta_o / f"{chave}.png"
        folha(video, png)
        motivos = avaliar(med)
        resultados[chave] = {"ok": not motivos, "motivos": motivos, "titulo": m["titulo"], "folha": str(png), **med}
        veredito = "PASSA" if not motivos else "NÃO PASSA: " + "; ".join(motivos)
        linha = (f"{k}. [{hms(s)}–{hms(e)}] {m['titulo']}\n   fala {med['fala_pct']}% · maior silêncio {med['maior_silencio']:.0f} s · "
                 f"jogo parado {med['parado_pct']}% · tempo morto {med['morto_max']:.0f} s → {veredito}\n   quadros: {png}")
        linhas.append(linha)
        print("      " + linha.replace("\n", "\n      "), flush=True)
    gravar_json(pasta / "olhar.json", resultados)
    cab = ("# OLHAR: medição de cada momento escolhido (limites provisórios: jogo parado <= 20%, silêncio <= 6 s, "
           "tempo morto <= 5 s)\n# Olhe as folhas de quadros (PNG) e decida. O 'escolher' descarta o que NÃO PASSA.\n\n")
    (pasta / "olhar.txt").write_text(cab + "\n".join(linhas) + "\n", encoding="utf-8")
