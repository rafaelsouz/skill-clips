#!/usr/bin/env python3
"""Clipper do GTA 6: transforma lives longas em cortes verticais prontos pra postar.

Comandos (depois do instalar, use o atalho ~/clipper-gta6/clip no lugar de "clip.py"):
  clip instalar                       confere e instala tudo que falta (também atualiza)
  clip buscar [--quantas 7] [--dias 2] acha as maiores lives de GTA 6 que já acabaram (--dias 2 = ontem e hoje)
  clip preparar LINK [LINK…]          baixa o áudio, escuta e separa os trechos mais quentes (candidatos.txt)
  clip refinar LIVE [--modelo small]  escuta de novo só os trechos quentes com modelo melhor (refaz candidatos.txt)
  clip olhar LIVE                     mede cada momento escolhido (fala, silêncio, jogo parado) e gera quadros pra olhar
  clip escolher LIVE [--manter 3]     junta os momentos que a IA escreveu e fica com os melhores (nota >= 60 e que passam no olhar)
  clip traduzir LIVE                  separa as falas de cada corte pra IA traduzir pra PT-BR (live em outro idioma)
  clip telegram AÇÃO                  configurar | testar | parear | enviar NOME | ouvir | status (só o dono é atendido)
  clip instagram AÇÃO                 configurar | testar | renovar | publicar N --url URL [--postar] | status
  clip marca [@perfil]                mostra ou define o @ da conta que vira marca d'água em todo vídeo
  clip fazer LIVE                     monta os cortes (câmera em cima, jogo embaixo, título, legenda em PT-BR)
  clip automatico LINK [--quantos 3]  tudo sozinho, sem a IA ler (títulos mais simples)
  clip status                         mostra em que passo cada live está

Os passos demorados rodam em segundo plano: se aparecer AINDA TRABALHANDO, é só rodar o mesmo comando de novo.
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
from comum import (CASA, CORTES, MODELOS, TRABALHO, YUNET, YUNET_URL, ffmpeg_embutido, hms, ler_json,  # noqa: E402
                   modelo_confere, pasta_da_live, tem)

VENV = CASA / "ambiente"
VENV_PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
REQUISITOS = AQUI.parent / "requirements.txt"  # versões fixas: nada de "--upgrade" nem pacote novo sem querer


def no_ambiente():
    """Roda sempre com o Python do próprio clipper, que o instalar cria."""
    if VENV_PY.exists() and Path(sys.prefix).resolve() != VENV.resolve() and sys.argv[1:2] != ["instalar"]:
        os.execv(str(VENV_PY), [str(VENV_PY), __file__, *sys.argv[1:]])


def instalar(_):
    print("Conferindo o que o computador precisa…\n")
    falta = []
    if sys.version_info < (3, 10):
        falta.append("python")
        print(f"  ✗ Python {sys.version.split()[0]} é antigo. Precisa do 3.10 ou mais novo.")
        if os.name == "nt":
            print("      Instale com:  winget install -e --id Python.Python.3.12 --scope user"
                  "   (depois feche e abra o terminal)")
        else:
            print("      Baixe o instalador oficial em https://www.python.org/downloads/ e abra o arquivo.")
    else:
        print(f"  ✓ Python {sys.version.split()[0]}")
    if "python" not in falta:
        if VENV_PY.exists():
            # ambiente criado com outro Python (ex.: um 3.15 sem pacotes prontos): apaga e refaz com o Python certo
            r = subprocess.run([str(VENV_PY), "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
                               capture_output=True, text=True)
            if r.stdout.strip() != "%d.%d" % sys.version_info[:2]:
                print("  … refazendo o ambiente do clipper com o Python", sys.version.split()[0])
                shutil.rmtree(VENV, ignore_errors=True)
        if not VENV_PY.exists():
            print("  … criando o ambiente do clipper em", VENV)
            subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
        print("  … instalando pacotes nas versões fixas de", REQUISITOS.name)
        # --only-binary: só pacote pronto (wheel), nunca roda setup.py de código baixado
        subprocess.run([str(VENV_PY), "-m", "pip", "install", "-q", "--only-binary=:all:", "-r", str(REQUISITOS)],
                       check=True)
        print("  ✓ pacotes do Python")
        if tem("ffmpeg") and tem("ffprobe"):
            print("  ✓ ffmpeg")
        else:
            print("  … baixando o ffmpeg (programa que monta os vídeos, uma vez só)")
            r = subprocess.run([str(VENV_PY), "-c", "import sys,static_ffmpeg,shutil;static_ffmpeg.add_paths();"
                                "sys.exit(0 if shutil.which('ffmpeg') and shutil.which('ffprobe') else 1)"])
            if r.returncode == 0:
                print("  ✓ ffmpeg")
            else:
                falta.append("ffmpeg")
                print("  ✗ não consegui baixar o ffmpeg. Confira a internet e rode 'clip.py instalar' de novo.")
    if not YUNET.exists():
        print("  … baixando o detector de rosto (OpenCV YuNet, 0,2 MB)")
        MODELOS.mkdir(parents=True, exist_ok=True)
        if tem("curl"):  # Mac e Windows 10+ já vêm com curl (o Python do python.org às vezes não acha os certificados)
            subprocess.run(["curl", "-fsSL", "-o", str(YUNET), YUNET_URL], check=True)
        else:
            subprocess.run([str(VENV_PY), "-c", "import sys,ssl,certifi,urllib.request as u;"
                            "c=ssl.create_default_context(cafile=certifi.where());"
                            "open(sys.argv[2],'wb').write(u.urlopen(sys.argv[1],context=c).read())",
                            YUNET_URL, str(YUNET)], check=True)
        if not modelo_confere():  # arquivo diferente do que foi conferido: apaga e não usa
            YUNET.unlink(missing_ok=True)
            sys.exit("O detector de rosto baixado não bate com o hash esperado. Apaguei. Não vou usar.")
    print("  ✓ detector de rosto")
    if "python" not in falta:
        print("  … baixando os modelos de escuta (base e small, uma vez só)")
        subprocess.run([str(VENV_PY), "-c", "from faster_whisper import WhisperModel as W; W('base'); W('small')"],
                       check=True)
        print("  ✓ modelos de escuta")
    for p in (TRABALHO, CORTES):
        p.mkdir(parents=True, exist_ok=True)
    if falta:
        print("\nFALTA:", ", ".join(falta), "— instale e rode 'clip.py instalar' de novo.")
        sys.exit(1)
    atalhos()
    print(f"\nTUDO PRONTO. Os cortes vão sair em {CORTES}")


def atalhos():
    """Cria o atalho ~/clipper-gta6/clip (e clip.cmd no Windows): a IA não precisa achar Python nem pasta."""
    script = Path(__file__).resolve()
    sh = CASA / "clip"
    sh.write_text(f'#!/bin/sh\nexec "{VENV_PY.as_posix()}" "{script.as_posix()}" "$@"\n', encoding="utf-8")
    try:
        sh.chmod(0o755)
    except OSError:
        pass
    if os.name == "nt":
        (CASA / "clip.cmd").write_text(f'@"{VENV_PY}" "{script}" %*\r\n', encoding="utf-8")
    print(f"  ✓ atalho: {sh}" + (f"  (PowerShell: {CASA / 'clip.cmd'})" if os.name == "nt" else ""))


GTA6 = __import__("re").compile(r"gta\s*-?\s*(6|vi)(?![0-9a-z])", 2)
GTA5 = __import__("re").compile(r"gta\s*-?\s*(5|v|iv|4|rp)(?![0-9a-z])", 2)


def buscar(a):
    import json
    from urllib.parse import quote_plus
    from comum import ytdlp
    from datetime import datetime, timedelta
    # filtros oficiais da busca do YouTube: ("Hoje" ou "Esta semana") + "Mais de 20 minutos"
    filtro = "EgQIAhgC" if a.dias <= 1 else "EgQIAxgC"
    desde = (datetime.now() - timedelta(days=max(a.dias, 1) - 1)).strftime("%Y%m%d")  # 2 dias = ontem e hoje
    vistos = {}
    for termo in a.termos:
        url = f"https://www.youtube.com/results?search_query={quote_plus(termo)}&sp={filtro}"
        r = subprocess.run(ytdlp() + ["--flat-playlist", "-J", "--playlist-end", "40", "--no-warnings", "--", url],
                           capture_output=True, text=True)
        try:
            itens = json.loads(r.stdout).get("entries", [])
        except json.JSONDecodeError:
            itens = []
        for v in itens:
            titulo = v.get("title") or ""
            if (v.get("id") and (v.get("duration") or 0) >= 30 * 60 and GTA6.search(titulo)
                    and not GTA5.search(titulo)):
                vistos[v["id"]] = v
    lista = []
    for v in sorted(vistos.values(), key=lambda v: -(v.get("view_count") or 0))[: a.quantas * (3 if a.dias <= 1 else 8)]:
        if len(lista) >= a.quantas:
            break
        # pula live trancada (idade, membros, privada), live ainda ao vivo e live fora do período pedido
        r = subprocess.run(ytdlp() + ["--print", "%(live_status)s|%(upload_date)s|%(release_date)s", "--no-warnings",
                                      "-q", "--", f"https://www.youtube.com/watch?v={v['id']}"],
                           capture_output=True, text=True)
        situacao, _, datas = r.stdout.strip().partition("|")
        data = next((d for d in datas.split("|") if d.isdigit() and len(d) == 8), "")
        if r.returncode == 0 and situacao not in ("is_live", "is_upcoming", "post_live"):
            if data and data < desde:
                print(f"  (pulei '{(v.get('title') or '')[:55]}': de {data[6:]}/{data[4:6]}, fora do período)")
                continue
            v["_data"] = f"{data[6:]}/{data[4:6]}" if data else "?"
            lista.append(v)
        else:
            motivo = "ainda ao vivo" if situacao in ("is_live", "is_upcoming", "post_live") else "trancada"
            print(f"  (pulei '{(v.get('title') or '')[:55]}': {motivo})")
    periodo = "de hoje" if a.dias <= 1 else f"dos últimos {a.dias} dias (desde {desde[6:]}/{desde[4:6]})"
    if not lista:
        print(f"Não achei lives longas de GTA 6 {periodo}. Tente com um link direto.")
        return
    print(f"As {len(lista)} maiores lives de GTA 6 {periodo}:\n")
    for i, v in enumerate(lista, 1):
        print(f"  {i}. {v.get('title')}\n     {v.get('channel') or v.get('uploader')} | "
              f"{hms(v.get('duration') or 0)} | {v.get('view_count') or 0:,} views | publicada {v.get('_data')}\n"
              f"     https://www.youtube.com/watch?v={v['id']}\n".replace(",", "."))


AINDA = ("\nAINDA TRABALHANDO (é normal, live longa demora). O trabalho continua sozinho em segundo plano.\n"
         "Rode este MESMO comando de novo pra acompanhar. Nada recomeça do zero.")


def _rodar_trabalho(pasta, nome, argv, espera):
    import tarefa
    situacao = tarefa.acompanhar(pasta, nome, argv, espera)
    if situacao == "rodando":
        print(AINDA)
        return False
    if situacao == "erro":
        msg = tarefa.explicar_erro(pasta, nome)
        if "NÃO" in msg or "NAO" in msg:
            print("\n  ✗ " + msg.strip())
        sys.exit("\nDEU ERRO (mostrei acima). Rode o mesmo comando de novo: ele continua de onde parou. "
                 "Se repetir, rode 'instalar' antes.")
    return True


def preparar(a):
    from ouvir import ler_live
    for link in a.links:
        pasta = ler_live(link)
        argv = [str(pasta), a.modelo or "", a.idioma or "", str(a.trechos), "0" if a.sem_refino else "1"]
        if not _rodar_trabalho(pasta, "preparar", argv, a.esperar):
            return
        print(f"\nPRONTO PRA ESCOLHER: {pasta.name}")
        print(f"  Leia: {pasta / 'candidatos.txt'}")
        print(f"  Escreva os momentos em: {pasta / 'momentos' / 'candidatos.json'}")
        print(f"  Depois rode: escolher {pasta.name} --manter 3   (ou 5, ou quantos a pessoa pediu)")


def refinar(a):
    """Escuta de novo, com um modelo melhor, só os trechos quentes de uma live já preparada (refaz o candidatos.txt)."""
    import tarefa
    pasta = pasta_da_live(a.live)
    if not (pasta / "candidatos.json").exists():
        sys.exit("Essa live ainda não terminou de ser escutada. Rode antes: preparar LINK")
    if a.refazer:
        tarefa.limpar(pasta, "refinar")
    if _rodar_trabalho(pasta, "refinar", [str(pasta), a.modelo or "small"], a.esperar):
        print(f"\nREFINADO: releia {pasta / 'candidatos.txt'} (agora com a escuta mais precisa)")


def olhar(a):
    """Mede cada momento escolhido (fala, silêncio, jogo parado) e gera a folha de quadros pra IA olhar."""
    import tarefa
    pasta = pasta_da_live(a.live)
    if not (pasta / "momentos").exists() or not list((pasta / "momentos").glob("*.json")):
        sys.exit("Escreva antes os momentos em momentos/candidatos.json (veja o SKILL.md).")
    (pasta / ".olhar.feito").unlink(missing_ok=True)  # sempre reavalia; o que já foi baixado e escutado é reaproveitado
    if _rodar_trabalho(pasta, "olhar", [str(pasta), a.modelo or "small"], a.esperar):
        print(f"\nOLHADO: leia {pasta / 'olhar.txt'} e olhe as folhas de quadros (PNG). Depois rode: escolher {pasta.name} --manter N")


def escolher(a):
    import tarefa
    from cortar import escolher as esc
    pasta = pasta_da_live(a.live)
    if not (pasta / "candidatos.txt").exists():
        sys.exit("Essa live ainda não terminou de ser escutada. Rode antes: preparar LINK")
    esc(pasta, a.manter, auto=a.auto, ignorar_trava=a.ignorar_trava)
    tarefa.limpar(pasta, "fazer")  # escolheu de novo: o próximo 'fazer' monta o que mudou
    tarefa.limpar(pasta, "traduzir")  # e as falas dos cortes novos precisam ser separadas de novo
    print(f"\nPróximo: fazer {pasta.name}")


def traduzir(a):
    """Separa as falas de cada corte pra IA traduzir pra PT-BR (live em outro idioma). A IA escreve os pt.json."""
    import tarefa
    from comum import precisa_traduzir
    if a.legenda:
        os.environ["CLIPPER_LEGENDA_IDIOMA"] = a.legenda
    pasta = pasta_da_live(a.live)
    if not (pasta / "escolhidos.json").exists():
        sys.exit("Nenhum corte escolhido ainda. Rode antes: escolher " + pasta.name)
    if a.refazer:
        tarefa.limpar(pasta, "traduzir")
    if not _rodar_trabalho(pasta, "traduzir", [str(pasta), a.modelo or "small"], a.esperar):
        return
    idioma = ler_json(pasta / "transcricao.json", {}).get("idioma")
    if not precisa_traduzir(idioma):
        print(f"\nNÃO PRECISA TRADUZIR: a live está em '{idioma}'. Rode: fazer {pasta.name}")
        return
    print(f"\nPRONTO PRA TRADUZIR: {pasta.name}")
    for i, _ in enumerate(ler_json(pasta / "escolhidos.json", {}).get("cortes") or [], 1):
        t = pasta / f"corte_{i:02d}"
        print(f"  corte {i}: leia {t / 'falas.txt'}  e escreva  {t / 'pt.json'}")
    print(f"  Depois rode: fazer {pasta.name}")


def telegram(a):
    """Manda os cortes pro SEU Telegram e escuta aprovar / rejeitar / pedir alteração (só o dono é atendido)."""
    import bot_telegram
    bot_telegram.executar(a)


def instagram(a):
    """Confere a conta e publica Reels APROVADOS no Telegram pela API oficial (só com --postar; sem ele é simulação)."""
    import instagram_api
    instagram_api.executar(a)


def marca(a):
    """Mostra ou define o @ da conta que vira marca d'água em todo vídeo (regra da skill)."""
    from comum import CONFIG, definir_marca, marca_perfil
    if a.perfil:
        print(f"Marca definida: {definir_marca(a.perfil)}  (fica em {CONFIG}). Todo vídeo montado daqui pra frente leva essa marca.")
    else:
        m = marca_perfil()
        print(f"Marca atual: {m}" if m else "Nenhuma marca definida ainda. Rode: marca @seu_perfil")


def _exigir_marca(sem_marca):
    """Regra: todo vídeo sai com a marca da conta. Sem marca definida, não monta (a menos que a pessoa peça --sem-marca)."""
    if sem_marca:
        os.environ["CLIPPER_SEM_MARCA"] = "1"
        return
    from comum import marca_perfil
    if not marca_perfil():
        sys.exit("\nFALTA A MARCA DA CONTA: todo vídeo leva o @ da conta como marca d'água.\n"
                 "Rode: marca @seu_perfil   (ou monte sem marca com --sem-marca)")


def fazer(a):
    _exigir_marca(a.sem_marca)
    if a.legenda:
        os.environ["CLIPPER_LEGENDA_IDIOMA"] = a.legenda
    from cortar import faltando_traducao
    pasta = pasta_da_live(a.live)
    if not (pasta / "escolhidos.json").exists():
        sys.exit("Nenhum corte escolhido ainda. Rode antes: escolher " + pasta.name)
    if faltando := faltando_traducao(pasta):
        sys.exit("\nPRECISO DA TRADUÇÃO DAS LEGENDAS (PT-BR) antes de montar:\n  " + "\n  ".join(faltando)
                 + f"\nRode: traduzir {pasta.name}, leia os falas.txt e escreva os pt.json (veja o SKILL.md).")
    # trabalho já concluído: reavalia (só refaz o que mudou: título, momento, tradução ou jeito de montar).
    # Só tira a marca de "feito"; se estiver rodando, o pid e o log ficam como estão.
    (pasta / ".fazer.feito").unlink(missing_ok=True)
    if _rodar_trabalho(pasta, "fazer", [str(pasta), a.modelo or ""], a.esperar):
        from comum import CORTES
        print(f"\nCORTES PRONTOS em {CORTES / pasta.name} (cada .mp4 tem um .txt com a legenda do post)")


def automatico(a):
    """Tudo sozinho, sem a IA ler nada: escuta, pega os trechos mais quentes e monta. Pra IA fraca ou com pressa.
    Sem a IA no meio não há tradução: as legendas ficam no idioma da live."""
    import tarefa
    _exigir_marca(a.sem_marca)
    os.environ["CLIPPER_LEGENDA_IDIOMA"] = "auto"
    from cortar import escolher as esc
    from ouvir import ler_live
    pasta = ler_live(a.link)
    if not _rodar_trabalho(pasta, "preparar", [str(pasta), "", "", "12"], a.esperar):
        return
    if not (pasta / "escolhidos.json").exists() or not (pasta / ".auto").exists():
        esc(pasta, a.quantos, auto=True)
        (pasta / ".auto").write_text("ok")
        tarefa.limpar(pasta, "fazer")
    if _rodar_trabalho(pasta, "fazer", [str(pasta), ""], a.esperar):
        from comum import CORTES
        print(f"\nCORTES PRONTOS em {CORTES / pasta.name}")


def _trabalho(a):
    """Roda em segundo plano (chamado pelo próprio clip.py)."""
    import tarefa
    pasta = Path(a.args[0])
    if a.nome == "preparar":
        from ouvir import trabalho_preparar
        modelo, idioma, trechos = (a.args[1] or None), (a.args[2] or None), int(a.args[3] or 12)
        refino = (a.args[4] if len(a.args) > 4 else "1") != "0"
        tarefa.executar(pasta, "preparar", lambda: trabalho_preparar(pasta, modelo, idioma, trechos, refino))
    elif a.nome == "olhar":
        from olhar import olhar as olha
        tarefa.executar(pasta, "olhar", lambda: olha(pasta, a.args[1] or "small"))
    elif a.nome == "traduzir":
        from cortar import traduzir as traduz
        tarefa.executar(pasta, "traduzir", lambda: traduz(pasta, a.args[1] or "small"))
    elif a.nome == "refinar":
        from ouvir import refinar as refina
        tarefa.executar(pasta, "refinar", lambda: refina(pasta, a.args[1] or "small"))
    elif a.nome == "fazer":
        from cortar import fazer as faz
        tarefa.executar(pasta, "fazer", lambda: faz(pasta, a.args[1] or "small"))


def status(_):
    if not TRABALHO.exists() or not any(TRABALHO.iterdir()):
        print("Nenhuma live ainda.")
        return
    for p in sorted(TRABALHO.iterdir()):
        if not p.is_dir():
            continue
        info = ler_json(p / "info.json", {})
        nb = len(list((p / "blocos").glob("bloco_*.txt")))
        nm = len(list((p / "momentos").glob("bloco_*.json")))
        esc = ler_json(p / "escolhidos.json", {}).get("cortes")
        prontos = len(list((CORTES / p.name).glob("corte_*.mp4")))
        rodando = [n for n in ("preparar", "refinar", "olhar", "traduzir", "fazer") if (p / f".{n}.pid").exists()]
        escutado = "sim" if (p / "candidatos.txt").exists() else f"{len(list((p / 'escuta').glob('*.json')))} partes"
        print(f"{p.name}\n  {info.get('title', '')}\n  escutada: {escutado} | escolhidos: "
              f"{len(esc) if esc else 0} | cortes prontos: {prontos}"
              + (f" | RODANDO AGORA: {', '.join(rodando)}" if rodando else ""))


def certificados():
    """o Python do python.org (Mac) não acha os certificados sozinho: usa os do certifi"""
    try:
        import certifi
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
        os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
    except ImportError:
        pass


def utf8():
    for f in (sys.stdout, sys.stderr):
        try:
            f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main():
    utf8()
    no_ambiente()
    certificados()
    ap = argparse.ArgumentParser(description="Clipper do GTA 6")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("instalar").set_defaults(f=instalar)
    b = sub.add_parser("buscar")
    b.add_argument("--quantas", type=int, default=7)
    b.add_argument("--dias", type=int, default=1, help="1 = só hoje, 2 = ontem e hoje (até 7)")
    b.add_argument("--termos", nargs="+", default=["GTA 6 live", "GTA 6 ao vivo", "GTA VI stream"])
    b.set_defaults(f=buscar)
    p = sub.add_parser("preparar")
    p.add_argument("links", nargs="+")
    p.add_argument("--modelo", default=None, help="modelo de escuta (padrão: base; tiny em PC fraco)")
    p.add_argument("--idioma", default=None, help="pt, en… (vazio = detecta sozinho)")
    p.add_argument("--trechos", type=int, default=20, help="quantos trechos quentes a IA vai ler (funil: 20 em vez de 12)")
    p.add_argument("--esperar", type=int, default=None, help="segundos acompanhando antes de devolver")
    p.add_argument("--sem-refino", action="store_true", help="não escuta de novo os trechos quentes com modelo melhor")
    p.set_defaults(f=preparar)
    r = sub.add_parser("refinar")
    r.add_argument("live")
    r.add_argument("--modelo", default=None, help="modelo do refino (padrão: small)")
    r.add_argument("--refazer", action="store_true", help="refaz mesmo que já tenha refinado")
    r.add_argument("--esperar", type=int, default=None)
    r.set_defaults(f=refinar)
    e = sub.add_parser("escolher")
    e.add_argument("live")
    e.add_argument("--manter", type=int, default=3)
    e.add_argument("--auto", action="store_true", help="ignora a IA e usa os trechos mais quentes")
    e.add_argument("--ignorar-trava", action="store_true", help="não descarta o que o 'olhar' marcou como NÃO PASSA")
    e.set_defaults(f=escolher)
    ol = sub.add_parser("olhar")
    ol.add_argument("live")
    ol.add_argument("--modelo", default=None, help="modelo da escuta dos momentos (padrão: small)")
    ol.add_argument("--esperar", type=int, default=None)
    ol.set_defaults(f=olhar)
    f = sub.add_parser("fazer")
    f.add_argument("live")
    f.add_argument("--modelo", default="small", help="modelo da legenda dos cortes")
    f.add_argument("--esperar", type=int, default=None)
    f.add_argument("--legenda", default=None, help="idioma da legenda (padrão pt; 'auto' = idioma da live)")
    f.add_argument("--sem-marca", action="store_true", help="monta sem a marca d'água da conta")
    f.set_defaults(f=fazer)
    tg = sub.add_parser("telegram")
    tg.add_argument("acao", choices=["configurar", "testar", "parear", "enviar", "ouvir", "status"])
    tg.add_argument("live", nargs="?", help="enviar: nome da live")
    tg.add_argument("--cortes", default=None, help="enviar: números dos cortes, ex.: 1,3 (padrão: todos)")
    tg.add_argument("--reenviar", action="store_true", help="enviar: manda de novo o que já foi enviado")
    tg.add_argument("--refazer", action="store_true", help="parear: troca o dono já pareado")
    tg.add_argument("--uma-vez", action="store_true", help="ouvir: processa o que chegou e sai")
    tg.set_defaults(f=telegram)
    ig = sub.add_parser("instagram")
    ig.add_argument("acao", choices=["configurar", "testar", "renovar", "publicar", "status"])
    ig.add_argument("clipe", nargs="?", help="publicar: número do clipe (o #N que o Telegram mostra)")
    ig.add_argument("--url", default=None, help="publicar: URL https pública do vídeo (a Meta baixa o arquivo dela)")
    ig.add_argument("--postar", action="store_true", help="publicar: publica de verdade (sem isso é só simulação)")
    ig.add_argument("--sem-credito", action="store_true", help="publicar: não coloca a linha de crédito na legenda")
    ig.add_argument("--refazer", action="store_true", help="publicar: descarta o contêiner antigo e sobe o vídeo de novo")
    ig.set_defaults(f=instagram)
    mk = sub.add_parser("marca")
    mk.add_argument("perfil", nargs="?", help="@ da conta (ex.: @seu_perfil); sem argumento mostra a marca atual")
    mk.set_defaults(f=marca)
    tr = sub.add_parser("traduzir")
    tr.add_argument("live")
    tr.add_argument("--modelo", default="small", help="modelo que separa as falas dos cortes")
    tr.add_argument("--legenda", default=None, help="idioma da legenda (padrão pt)")
    tr.add_argument("--refazer", action="store_true", help="separa as falas de novo")
    tr.add_argument("--esperar", type=int, default=None)
    tr.set_defaults(f=traduzir)
    t = sub.add_parser("automatico")
    t.add_argument("link")
    t.add_argument("--quantos", type=int, default=3)
    t.add_argument("--esperar", type=int, default=None)
    t.add_argument("--sem-marca", action="store_true", help="monta sem a marca d'água da conta")
    t.set_defaults(f=automatico)
    w = sub.add_parser("_trabalho")
    w.add_argument("nome")
    w.add_argument("args", nargs="*")
    w.set_defaults(f=_trabalho)
    sub.add_parser("status").set_defaults(f=status)
    a = ap.parse_args()
    if getattr(a, "esperar", 0) is None:
        import tarefa
        a.esperar = tarefa.ESPERA
    if a.f is not instalar:
        ffmpeg_embutido()
    a.f(a)


if __name__ == "__main__":
    main()
