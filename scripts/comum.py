"""Coisas que todos os passos do clipper usam: pastas, tempo e chamadas de programas."""
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

CASA = Path(os.environ.get("CLIPPER_GTA6_HOME", Path.home() / "clipper-gta6"))
TRABALHO = CASA / "trabalho"   # áudio, transcrição e trechos de cada live
CORTES = CASA / "cortes"       # os vídeos prontos pra postar
MODELOS = CASA / "modelos"     # o detector de rosto (baixado no setup)
YUNET = MODELOS / "face_detection_yunet_2023mar.onnx"
YUNET_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
             "face_detection_yunet_2023mar.onnx")
YUNET_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"  # conferido em 2026-11-18
BLOCO_MIN = 12  # a IA lê a transcrição 12 minutos por vez


def modelo_confere():
    """O detector de rosto baixado tem que ser exatamente o arquivo conferido (a URL do GitHub aponta pra 'main')."""
    import hashlib
    try:
        return hashlib.sha256(YUNET.read_bytes()).hexdigest() == YUNET_SHA256
    except OSError:
        return False


def url_segura(url):
    """Só aceita http(s) sem espaços. Um 'link' começando com '-' viraria opção do yt-dlp (ex.: --exec)."""
    if not re.fullmatch(r"https?://[^\s\"'<>`|&;$]+", str(url or "")):
        sys.exit("\n  ✗ Isso não parece um link http(s) de live. Cole o link inteiro, começando com https://")
    return url


def slug(texto, limite=60):
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    t = re.sub(r"[^A-Za-z0-9]+", "-", t).strip("-").lower()
    return t[:limite].strip("-") or "live"


def hms(seg):
    seg = max(0, int(round(seg)))
    return f"{seg // 3600:02d}:{seg % 3600 // 60:02d}:{seg % 60:02d}"


def segundos(valor):
    """Aceita 3723, '3723', '1:02:03' ou '62:03'."""
    if isinstance(valor, (int, float)):
        return float(valor)
    partes = [float(p) for p in str(valor).strip().split(":")]
    total = 0.0
    for p in partes:
        total = total * 60 + p
    return total


def rodar(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def tem(programa):
    return shutil.which(programa) is not None


def ffmpeg_embutido():
    """Sem ffmpeg no computador, usa o ffmpeg que vem no pacote static-ffmpeg (baixado no instalar)."""
    if tem("ffmpeg") and tem("ffprobe"):
        return True
    try:
        import static_ffmpeg
        static_ffmpeg.add_paths(weak=True)
    except Exception:
        return False
    return tem("ffmpeg") and tem("ffprobe")


def ytdlp():
    """yt-dlp do ambiente do clipper (sempre atualizado pelo setup) ou o do sistema."""
    local = Path(sys.executable).parent / ("yt-dlp.exe" if os.name == "nt" else "yt-dlp")
    cmd = [str(local)] if local.exists() else [sys.executable, "-m", "yt_dlp"]
    # o YouTube está passando a exigir um JavaScript pra liberar os formatos: o instalar põe o Deno no ambiente
    deno = Path(sys.executable).parent / ("deno.exe" if os.name == "nt" else "deno")
    if deno.exists():
        cmd += ["--js-runtimes", f"deno:{deno}"]
    elif tem("deno") or tem("node"):
        cmd += ["--js-runtimes", "deno" if tem("deno") else "node"]
    return cmd


TRANCADA = ("confirm your age", "inappropriate for some users", "members-only", "join this channel",
            "subscriber", "private video", "requires authentication", "sign in to confirm")


def motivo_trancada(erro):
    """Se o yt-dlp recusou porque a live pede login (idade, membros, inscritos, privada), diz isso em português."""
    t = (erro or "").lower()
    if not any(k in t for k in TRANCADA):
        return None
    if "age" in t or "inappropriate" in t:
        return "essa live é trancada por idade (o YouTube pede login pra ver)"
    if "member" in t or "join this channel" in t or "subscriber" in t:
        return "essa live é só pra membros/inscritos do canal"
    return "essa live pede login (privada ou restrita)"


def ler_json(caminho, padrao=None):
    try:
        return json.loads(Path(caminho).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return padrao


def gravar_json(caminho, dados):
    Path(caminho).parent.mkdir(parents=True, exist_ok=True)
    Path(caminho).write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")


def pasta_da_live(nome):
    """Aceita o nome da pasta, um pedaço dele ou o link original."""
    if (TRABALHO / nome).is_dir():
        return TRABALHO / nome
    for p in sorted(TRABALHO.glob("*")):
        info = ler_json(p / "info.json", {})
        if nome in (info.get("url"), info.get("webpage_url")) or nome.lower() in p.name:
            return p
    sys.exit(f"Não achei a live '{nome}' em {TRABALHO}. Rode antes: clip.py preparar LINK")
