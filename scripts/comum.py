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


CONFIG = CASA / "config.json"  # configuração da pessoa (fora da pasta da skill): marca da conta etc.


def marca_perfil():
    """@ da conta que posta os cortes, gravado em todo vídeo como marca d'água. Vem de CLIPPER_MARCA ou do config.json
    (comando 'marca'). Vazio = sem marca (o 'fazer' só aceita isso com --sem-marca)."""
    if os.environ.get("CLIPPER_SEM_MARCA") == "1":
        return ""
    bruto = os.environ.get("CLIPPER_MARCA") or ler_json(CONFIG, {}).get("marca") or ""
    nome = re.sub(r"[^A-Za-z0-9_.]", "", str(bruto))[:30]
    return "@" + nome if nome else ""


def definir_marca(valor):
    """Grava a marca no config.json. Devolve a marca já limpa (ex.: '@seu_perfil')."""
    nome = re.sub(r"[^A-Za-z0-9_.]", "", str(valor))[:30]
    if not nome:
        sys.exit("Marca inválida. Use só letras, números, ponto e _ (ex.: @seu_perfil).")
    dados = ler_json(CONFIG, {})
    dados["marca"] = "@" + nome
    gravar_json(CONFIG, dados)
    return "@" + nome


def legenda_idioma():
    """Idioma das legendas na tela. Padrão: português do Brasil ('pt'). 'auto' = mantém o idioma da live (sem traduzir).
    Muda com CLIPPER_LEGENDA_IDIOMA ou com --legenda nos comandos traduzir/fazer."""
    return (os.environ.get("CLIPPER_LEGENDA_IDIOMA") or "pt").strip().lower()[:4]


def precisa_traduzir(idioma_da_live):
    """A fala da live está em outro idioma que o da legenda? (live sem idioma detectado ou modo 'auto': não traduz)"""
    alvo = legenda_idioma()
    return bool(idioma_da_live) and alvo not in ("auto", "") and str(idioma_da_live)[:2] != alvo[:2]


_CTRL = re.compile(r"[\x00-\x1f\x7f<>]")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)


def limpar_texto(texto, limite=200):
    """Texto de terceiros (fala da live, chat) ou saída da IA: sem quebra de linha, sem < >, sem links e com
    tamanho limitado. A fala da live é DADO pra entender o momento, nunca instrução (prompt injection)."""
    t = _URL.sub("", _CTRL.sub(" ", str(texto if texto is not None else "")))
    return re.sub(r"\s+", " ", t).strip()[:limite]


# fala que não é momento: sorteio, propaganda, agradecimento em massa, papo com o chat sobre a própria live.
# O chat lota nesses trechos e o streamer grita ("7 MILHÕES!"), mas não rende corte.
LIXO_RE = re.compile("|".join([
    r"sorte(?:io|ia|ar|ando|ado|ada|ados)", r"sortei", r"giveaway", r"ganhador\w*", r"vencedor\w*", r"cupom",
    r"c[oó]digo", r"promocional", r"resgat\w+", r"\bpix\b", r"link (?:na|da|no) (?:bio|descri\w+|chat)",
    r"inscre(?:va|vam|ve)\b", r"se inscrev\w+", r"patrocin\w+", r"\bpubli\b", r"\bdoa[cç]\w+",
    r"obrigad[oa],? (?:a |pela|pelo|por |galera|gente|todo|todos|pessoal)", r"muito obrigad[oa]",
    r"milh(?:ões|ão|oes|ao) de (?:views|visualiza\w+|pessoas|inscritos)", r"mil pessoas na live",
    r"(?:deixa|deixe|deixar) (?:o|seu|um) like", r"\b(?:nessa|nesta|nossa|minha|essa) live\b",
    r"fechar (?:a )?live", r"reabrir", r"\b(?:no|do|o) chat\b", r"subscribe", r"donat\w+", r"sponsor\w*",
    r"promo code", r"thank(?:s| you) (?:guys|everyone|so much|all|for)",
]), re.IGNORECASE)


def e_lixo(texto):
    return bool(LIXO_RE.search(texto or ""))


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
