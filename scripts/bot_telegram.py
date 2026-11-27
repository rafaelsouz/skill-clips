"""Telegram do clipper: manda os cortes pro SEU chat e escuta aprovar / rejeitar / pedir alteração.

Segurança (a base de tudo aqui):
  - O bot só obedece ao DONO: mensagem ou clique cujo remetente (from.id) é o dono, num chat PRIVADO com ele.
    Todo o resto (outros usuários, grupos, canais, inline) é descartado em silêncio (só um contador).
  - Sem dono pareado o programa não escuta (falha fechada). O pareamento usa um código aleatório de uso único.
  - Só polling (getUpdates): não abre porta nem usa ngrok/webhook.
  - O token fica no .env (fora do repositório, acesso restrito) e nunca é impresso nem vai pra log.
  - Texto do Telegram é DADO: botões só aceitam uma lista fechada de ações e o pedido de alteração é só registrado.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import re
import secrets
import subprocess
import sys
import time
from collections import Counter, deque
from datetime import datetime
from pathlib import Path

import requests

from comum import CASA, CONFIG, CORTES, gravar_json, ler_json, limpar_texto, pasta_da_live

ENV = CASA / ".env"
PASTA = CASA / "aprovacao"
OFFSET = PASTA / "offset.json"
LIMITE_BYTES = 49_000_000  # o bot oficial aceita até 50 MB por arquivo
ESPERA_LONGA = 25  # segundos de espera por novidade em cada pergunta (curta: se a conexão pendurar, volta logo)
TOKEN_RE = re.compile(r"\A\d{6,12}:[A-Za-z0-9_-]{30,}\Z")
_SEGREDO = re.compile(r"(?:bot)?\d{6,12}:[A-Za-z0-9_-]{25,}")
# único formato de botão aceito. \Z (e não $): o $ deixaria passar uma quebra de linha no fim
CB_RE = re.compile(r"\A(aprovar|rejeitar|alterar):(\d{1,6})\Z")
ESTADOS_FINAIS = ("aprovado", "rejeitado", "postado")  # decidido: um clique velho não desfaz (nem repete um post)


def redigir(texto):
    """Troca qualquer coisa parecida com um token por 'bot***' (erros e logs nunca mostram o token)."""
    return _SEGREDO.sub("bot***", str(texto))


# ---------------------------------------------------------------- segredos e configuração
def ler_env(caminho=None):
    out = {}
    try:
        linhas = Path(caminho or ENV).read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return out
    for linha in linhas:
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        k, _, v = linha.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def carregar_token():
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or ler_env().get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        sys.exit(f"\nFALTA O TOKEN DO BOT. Rode: telegram configurar   (o arquivo fica em {ENV})")
    if not TOKEN_RE.match(token):
        sys.exit("\nO TOKEN no .env não tem o formato esperado (não mostro o valor). Copie de novo do @BotFather.")
    return token


def restringir(caminho):
    """Só o seu usuário lê o arquivo (o .env tem o token)."""
    try:
        if os.name == "nt":
            usuario = os.environ.get("USERNAME", "")
            if usuario:
                subprocess.run(["icacls", str(caminho), "/inheritance:r", "/grant:r", f"{usuario}:(R,W)"],
                               capture_output=True, check=False)
        else:
            os.chmod(caminho, 0o600)
    except OSError:
        pass


def ler_dono():
    d = (ler_json(CONFIG, {}) or {}).get("telegram_dono") or {}
    uid = d.get("user_id")
    return uid if isinstance(uid, int) and not isinstance(uid, bool) else None


def salvar_dono(uid):
    dados = ler_json(CONFIG, {}) or {}
    dados["telegram_dono"] = {"user_id": uid, "pareado_em": datetime.now().isoformat(timespec="seconds")}
    gravar_json(CONFIG, dados)


# ---------------------------------------------------------------- cliente da Bot API
class TelegramErro(Exception):
    def __init__(self, msg, codigo=None, retry_after=None):
        super().__init__(msg)
        self.codigo, self.retry_after = codigo, retry_after


class Bot:
    def __init__(self, token):
        self._base = f"https://api.telegram.org/bot{token}"
        self._http = requests.Session()

    def __repr__(self):
        return "Bot(***)"

    def chamar(self, metodo, dados=None, arquivos=None, timeout=30):
        try:
            r = self._http.post(f"{self._base}/{metodo}", data=dados, files=arquivos, timeout=timeout)
            j = r.json()
        except (requests.RequestException, ValueError) as e:
            self._http.close()  # conexão nova na próxima: uma conexão pendurada não trava a escuta de novo
            # 'from None': a exceção original traz a URL (com o token) na mensagem
            raise TelegramErro(f"{metodo}: falha de rede ({type(e).__name__})") from None
        if not j.get("ok"):
            raise TelegramErro(f"{metodo}: {redigir(j.get('description', 'erro'))}", j.get("error_code"),
                               (j.get("parameters") or {}).get("retry_after"))
        return j["result"]

    def get_me(self):
        return self.chamar("getMe", timeout=20)

    def send_text(self, chat, texto, markup=None):
        dados = {"chat_id": str(chat), "text": texto[:4000], "parse_mode": "HTML", "disable_web_page_preview": "true"}
        if markup:
            dados["reply_markup"] = json.dumps(markup)
        return self.chamar("sendMessage", dados)

    def send_video(self, chat, caminho, legenda, markup, largura, altura, duracao):
        dados = {"chat_id": str(chat), "caption": legenda, "parse_mode": "HTML", "supports_streaming": "true",
                 "width": str(largura), "height": str(altura), "duration": str(int(duracao)),
                 "reply_markup": json.dumps(markup)}
        with open(caminho, "rb") as f:
            return self.chamar("sendVideo", dados, {"video": (Path(caminho).name, f, "video/mp4")}, timeout=900)

    def updates(self, offset, timeout=50):
        dados = {"offset": str(offset), "timeout": str(timeout),
                 "allowed_updates": json.dumps(["message", "callback_query"])}
        return self.chamar("getUpdates", dados, timeout=timeout + 15)

    def responder_clique(self, id_clique, texto=""):
        return self.chamar("answerCallbackQuery", {"callback_query_id": id_clique, "text": texto[:190]})

    def trocar_botoes(self, chat, id_msg, markup):
        return self.chamar("editMessageReplyMarkup", {"chat_id": str(chat), "message_id": str(id_msg),
                                                      "reply_markup": json.dumps(markup)})

    def webhook_info(self):
        return self.chamar("getWebhookInfo", timeout=20)

    def apagar_webhook(self):
        return self.chamar("deleteWebhook", {"drop_pending_updates": "false"}, timeout=20)


# ---------------------------------------------------------------- AUTORIZAÇÃO (só o dono)
def _int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def autorizado(update, dono):
    """True só se a mensagem ou o clique vem DO dono (from.id), num chat PRIVADO com ele (chat.type e chat.id).
    O LiveCut só olhava o chat.id; aqui o remetente e o tipo do chat também contam."""
    if not isinstance(update, dict) or not _int(dono):
        return False
    if isinstance(update.get("callback_query"), dict):
        origem, msg = update["callback_query"].get("from"), update["callback_query"].get("message")
    elif isinstance(update.get("message"), dict):
        msg = update["message"]
        origem = msg.get("from")
    else:
        return False
    if not isinstance(origem, dict) or not isinstance(msg, dict) or not isinstance(msg.get("chat"), dict):
        return False
    chat = msg["chat"]
    return (_int(origem.get("id")) and origem["id"] == dono and origem.get("is_bot") is not True
            and chat.get("type") == "private" and _int(chat.get("id")) and chat["id"] == dono)


# ---------------------------------------------------------------- fila e eventos (estado em arquivo)
class Fila:
    def __init__(self, pasta=None):
        self.pasta = Path(pasta or PASTA)
        self.arq, self.eventos = self.pasta / "fila.json", self.pasta / "eventos.jsonl"

    def _ler(self):
        d = ler_json(self.arq, None)
        d = d if isinstance(d, dict) else {}
        d.setdefault("proximo", 1)
        d.setdefault("clipes", {})
        d.setdefault("aguardando_texto", None)
        return d

    def _gravar(self, d):
        self.pasta.mkdir(parents=True, exist_ok=True)
        tmp = self.arq.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.arq)

    def novo(self, **dados):
        d = self._ler()
        i = d["proximo"]
        d["proximo"] = i + 1
        d["clipes"][str(i)] = {"estado": "aguardando", **dados}
        self._gravar(d)
        return i

    def clipe(self, i):
        return self._ler()["clipes"].get(str(i))

    def todos(self):
        return {int(k): v for k, v in self._ler()["clipes"].items()}

    def mudar(self, i, **campos):
        d = self._ler()
        c = d["clipes"].get(str(i))
        if c is None:
            return None
        c.update(campos)
        self._gravar(d)
        return c

    def aguardando_texto(self):
        return self._ler()["aguardando_texto"]

    def definir_aguardando_texto(self, i):
        d = self._ler()
        d["aguardando_texto"] = i
        self._gravar(d)

    def registrar(self, **ev):
        self.pasta.mkdir(parents=True, exist_ok=True)
        with open(self.eventos, "a", encoding="utf-8") as f:
            f.write(json.dumps({"quando": datetime.now().isoformat(timespec="seconds"), **ev}, ensure_ascii=False) + "\n")


def botoes(clip_id):
    return {"inline_keyboard": [
        [{"text": "✅ Aprovar", "callback_data": f"aprovar:{clip_id}"},
         {"text": "🗑 Rejeitar", "callback_data": f"rejeitar:{clip_id}"}],
        [{"text": "✏️ Pedir alteração", "callback_data": f"alterar:{clip_id}"}]]}


def _rotulo(texto):
    return {"inline_keyboard": [[{"text": texto, "callback_data": "noop"}]]}


AJUDA = ("<b>Clipper</b>: eu só falo com você.\n"
         "Toque nos botões de cada vídeo: ✅ Aprovar, 🗑 Rejeitar ou ✏️ Pedir alteração (depois escreva o que quer mudar).\n"
         "/status: situação dos clipes · /cancelar: cancela um pedido de alteração · /ajuda: esta mensagem")


class Escutador:
    """Recebe os updates, descarta quem não é o dono e executa só as ações da lista fechada."""

    def __init__(self, bot, dono, fila=None, agora=time.time):
        self.bot, self.dono, self.fila, self.agora = bot, dono, fila or Fila(), agora
        self.estranhos, self.ultimo_aviso = 0, agora()
        self.recentes = deque()

    def processar(self, update):
        if not autorizado(update, self.dono):
            self._estranho()
            return "ignorado"
        if self._excedeu():
            return "limite"
        if isinstance(update.get("callback_query"), dict):
            return self._clique(update["callback_query"])
        return self._mensagem(update["message"])

    # -- proteção
    def _estranho(self):
        self.estranhos += 1  # só um contador: nada do conteúdo nem da identidade de quem tentou fica guardado
        if self.estranhos and self.agora() - self.ultimo_aviso >= 3600:
            n, self.estranhos, self.ultimo_aviso = self.estranhos, 0, self.agora()
            self._seguro(self.bot.send_text, self.dono, f"⚠️ {n} tentativa(s) de outras pessoas falarem com este bot "
                                                       "foram ignoradas na última hora.")

    def _excedeu(self):
        t = self.agora()
        while self.recentes and t - self.recentes[0] > 60:
            self.recentes.popleft()
        self.recentes.append(t)
        return len(self.recentes) > 30

    def _seguro(self, fn, *args):
        try:
            return fn(*args)
        except TelegramErro as e:
            print(f"  (aviso do Telegram: {redigir(e)})", flush=True)
            return None

    # -- clique em botão
    def _clique(self, cq):
        msg = cq["message"]
        chat_id, id_msg = msg["chat"]["id"], msg.get("message_id")
        data = cq.get("data")
        if data == "noop":
            self._seguro(self.bot.responder_clique, cq.get("id", ""), "")
            return "noop"
        m = CB_RE.match(data) if isinstance(data, str) else None
        if not m:
            self._seguro(self.bot.responder_clique, cq.get("id", ""), "Ação inválida")
            return "invalido"
        acao, clip_id = m.group(1), int(m.group(2))
        texto, rotulo = self._decidir(chat_id, clip_id, acao)
        self._seguro(self.bot.responder_clique, cq.get("id", ""), texto)
        if rotulo and id_msg is not None:
            self._seguro(self.bot.trocar_botoes, chat_id, id_msg, _rotulo(rotulo))
        return acao

    def _decidir(self, chat_id, clip_id, acao):
        c = self.fila.clipe(clip_id)
        if not c:
            return "Esse clipe não existe mais.", None
        if c["estado"] in ESTADOS_FINAIS:  # já decidido: não muda de novo (idempotente)
            return f"Esse clipe já estava {c['estado']}.", None
        if acao in ("aprovar", "rejeitar"):
            estado = "aprovado" if acao == "aprovar" else "rejeitado"
            self.fila.mudar(clip_id, estado=estado)
            if self.fila.aguardando_texto() == clip_id:
                self.fila.definir_aguardando_texto(None)
            self.fila.registrar(clip=clip_id, evento=estado)
            return ("✅ Aprovado" if acao == "aprovar" else "🗑 Rejeitado"), ("✅ Aprovado" if acao == "aprovar" else "🗑 Rejeitado")
        self.fila.mudar(clip_id, estado="alterar_aguardando_texto")  # alterar
        self.fila.definir_aguardando_texto(clip_id)
        self.fila.registrar(clip=clip_id, evento="alteracao_solicitada")
        self._seguro(self.bot.send_text, chat_id, f"✏️ O que você quer mudar no clipe #{clip_id}? Responda com uma "
                                                  "mensagem (até 500 caracteres) ou /cancelar.")
        return "Escreva o que quer mudar", "✏️ Aguardando o seu texto"

    # -- mensagem de texto
    def _mensagem(self, msg):
        texto = msg.get("text")
        if not isinstance(texto, str) or not texto.strip():
            return "ignorado"
        chat_id = msg["chat"]["id"]
        primeira = texto.strip().split()[0]
        cmd = primeira.split("@")[0].lower() if primeira.startswith("/") else ""
        if cmd in ("/start", "/ajuda", "/help"):
            self._seguro(self.bot.send_text, chat_id, AJUDA)
            return "ajuda"
        if cmd == "/status":
            cont = Counter(c["estado"] for c in self.fila.todos().values())
            linhas = [f"{k}: {v}" for k, v in sorted(cont.items())] or ["nenhum clipe na fila"]
            self._seguro(self.bot.send_text, chat_id, "<b>Fila</b>\n" + "\n".join(linhas))
            return "status"
        if cmd == "/cancelar":
            alvo = self.fila.aguardando_texto()
            if alvo:
                self.fila.mudar(alvo, estado="aguardando")
                self.fila.definir_aguardando_texto(None)
                self._seguro(self.bot.send_text, chat_id, "Cancelado.")
            return "cancelar"
        alvo = self.fila.aguardando_texto()
        if not alvo:
            self._seguro(self.bot.send_text, chat_id, "Use os botões do vídeo ou /ajuda.")
            return "sem_contexto"
        pedido = limpar_texto(texto, 500)  # dado, nunca comando: só é registrado
        if not pedido:
            self._seguro(self.bot.send_text, chat_id, "Não entendi o texto. Escreva de novo ou /cancelar.")
            return "vazio"
        self.fila.mudar(alvo, estado="alteracao_pedida", pedido=pedido)
        self.fila.definir_aguardando_texto(None)
        self.fila.registrar(clip=alvo, evento="alteracao_pedida", texto=pedido)
        self._seguro(self.bot.send_text, chat_id, f"✏️ Registrei o pedido para o clipe #{alvo}. Eu aviso quando refizer.")
        return "alteracao"


# ---------------------------------------------------------------- pareamento (descobre o SEU id sem bot de terceiros)
class Pareamento:
    """Código aleatório de uso único: quem não vê o terminal não vira dono. 5 minutos e 3 tentativas erradas."""

    def __init__(self, janela=300, max_erros=3, agora=time.time, codigo=None):
        self.codigo = codigo or f"{secrets.randbelow(10 ** 8):08d}"
        self.agora, self.expira, self.erros, self.max_erros = agora, agora() + janela, 0, max_erros

    def tentar(self, update):
        if self.agora() > self.expira:
            return "expirou", None
        if self.erros >= self.max_erros:
            return "esgotado", None
        msg = update.get("message") if isinstance(update, dict) else None
        if not isinstance(msg, dict) or not isinstance(msg.get("from"), dict) or not isinstance(msg.get("chat"), dict):
            return "ignorar", None
        origem, chat = msg["from"], msg["chat"]
        if not (chat.get("type") == "private" and _int(origem.get("id")) and origem["id"] == chat.get("id")
                and origem.get("is_bot") is not True):
            return "ignorar", None
        texto = msg.get("text")
        partes = texto.strip().split() if isinstance(texto, str) else []
        if len(partes) != 2 or partes[0].split("@")[0].lower() != "/start":
            return "ignorar", None
        if hmac.compare_digest(partes[1].encode("utf-8", "ignore"), self.codigo.encode()):
            return "ok", origem["id"]
        self.erros += 1
        return ("esgotado" if self.erros >= self.max_erros else "errado"), None


# ---------------------------------------------------------------- polling
def ler_offset(arq=None):
    v = (ler_json(arq or OFFSET, {}) or {}).get("offset")
    return v if _int(v) else None


def gravar_offset(valor, arq=None):
    gravar_json(arq or OFFSET, {"offset": valor})


def descartar_atrasadas(bot, arq=None):
    """Primeira partida: joga fora o que ficou acumulado (o Telegram guarda até 24 h), pra ninguém ver mensagens velhas."""
    ups = bot.updates(-1, 0)
    off = ups[-1]["update_id"] + 1 if ups else (ler_offset(arq) or 0)
    gravar_offset(off, arq)
    return off


def preparar_polling(bot):
    if (bot.webhook_info() or {}).get("url"):
        print("Havia um webhook configurado neste bot; vou desligar (o clipper usa só polling, sem porta aberta).")
        bot.apagar_webhook()


def ouvir(bot, dono, uma_vez=False, fila=None, arq_offset=None):
    esc = Escutador(bot, dono, fila)
    offset = ler_offset(arq_offset)
    if offset is None:
        offset = descartar_atrasadas(bot, arq_offset)
    espera = 5
    print("Ouvindo o Telegram (só o dono é atendido). Ctrl+C para parar." if not uma_vez else "Lendo o que chegou…", flush=True)
    while True:
        try:
            ups = bot.updates(offset, 0 if uma_vez else ESPERA_LONGA)
            espera = 5
        except TelegramErro as e:
            if e.codigo in (401, 404):
                sys.exit("\nO Telegram recusou o token (revogado ou errado). Gere outro no @BotFather e atualize o .env.")
            if e.codigo == 409:
                sys.exit("\nOutro programa já está lendo este bot (ou há um webhook). Feche o outro e tente de novo.")
            print(f"  (erro: {redigir(e)}; tento de novo em {espera}s)", flush=True)
            if uma_vez:
                return
            time.sleep(espera)
            espera = min(60, espera * 2)
            continue
        for u in ups:
            offset = u["update_id"] + 1
            try:
                r = esc.processar(u)
            except Exception as e:  # noqa: BLE001  (um update ruim não derruba a escuta)
                r = f"erro ({type(e).__name__})"
            if r not in ("ignorado", "noop"):
                print(f"  {datetime.now():%H:%M:%S} {r}", flush=True)
        if ups:
            gravar_offset(offset, arq_offset)
        if uma_vez:
            return


# ---------------------------------------------------------------- envio dos cortes
def _dims(arq):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height:format=duration", "-of", "json", str(arq)],
                           capture_output=True, text=True, timeout=30)
        j = json.loads(r.stdout)
        return int(j["streams"][0]["width"]), int(j["streams"][0]["height"]), float(j["format"]["duration"])
    except Exception:  # noqa: BLE001
        return 1080, 1920, 0.0


def montar_legenda(clip_id, titulo, post, credito):
    e = html.escape
    post = post.strip()
    while True:
        texto = (f"🎬 <b>{e(titulo)}</b> · #{clip_id}\n\n📋 Legenda do post (toque para copiar):\n"
                 f"<code>{e(post)}</code>\n\n<i>{e(credito)}</i>")
        if len(texto) <= 1000 or len(post) < 40:
            return texto[:1020]
        post = post[: max(20, len(post) - 60)].rstrip() + "…"


def enviar(bot, dono, nome_da_live, cortes=None, reenviar=False, fila=None):
    fila = fila or Fila()
    pasta = pasta_da_live(nome_da_live)
    cdir = CORTES / pasta.name
    arquivos = sorted(cdir.glob("corte_*.mp4"))
    if cortes:
        arquivos = [a for a in arquivos if int(a.stem.split("_")[1]) in cortes]
    if not arquivos:
        sys.exit("Nenhum corte encontrado em " + str(cdir))
    existentes = fila.todos()
    enviados = 0
    for mp4 in arquivos:
        idx = int(mp4.stem.split("_")[1])
        st = mp4.stat()
        chave = f"{pasta.name}/{idx}/{st.st_size}/{int(st.st_mtime)}"
        if not reenviar and any(c.get("chave") == chave and c["estado"] != "substituido" for c in existentes.values()):
            print(f"  corte {idx}: já enviado (use --reenviar para mandar de novo)")
            continue
        if st.st_size > LIMITE_BYTES:
            print(f"  corte {idx}: {st.st_size / 1e6:.0f} MB passa do limite de 50 MB do Telegram. Refaça com 'fazer'.")
            continue
        partes = (mp4.with_suffix(".txt").read_text(encoding="utf-8").split("\n\n", 2) + ["", "", ""])[:3]
        titulo, post, credito = (p.strip() for p in partes)
        clip_id = fila.novo(live=pasta.name, corte=idx, arquivo=str(mp4), chave=chave, titulo=limpar_texto(titulo, 120))
        larg, alt, dur = _dims(mp4)
        legenda = montar_legenda(clip_id, titulo, post, credito)
        for tentativa in range(3):
            try:
                bot.send_video(dono, mp4, legenda, botoes(clip_id), larg, alt, dur)
                break
            except TelegramErro as e:
                if tentativa == 2:
                    fila.mudar(clip_id, estado="erro_envio")
                    print(f"  corte {idx}: não consegui enviar ({redigir(e)})")
                    break
                time.sleep(e.retry_after or 5 * (tentativa + 1))
        if fila.clipe(clip_id)["estado"] == "erro_envio":
            continue
        for k, c in existentes.items():  # a versão nova substitui a antiga do mesmo corte
            if c.get("live") == pasta.name and c.get("corte") == idx and c["estado"] in (
                    "aguardando", "alteracao_pedida", "alterar_aguardando_texto"):
                fila.mudar(k, estado="substituido")
        fila.registrar(clip=clip_id, evento="enviado", live=pasta.name, corte=idx)
        enviados += 1
        print(f"  corte {idx}: enviado como clipe #{clip_id}")
    print(f"\n{enviados} vídeo(s) no seu Telegram. Para receber as respostas rode: telegram ouvir")


# ---------------------------------------------------------------- comandos
def cmd_configurar():
    CASA.mkdir(parents=True, exist_ok=True)
    if not ENV.exists():
        ENV.write_text("# Segredos do clipper. NAO compartilhe e NAO cole no chat.\nTELEGRAM_BOT_TOKEN=\n", encoding="utf-8")
    restringir(ENV)
    print(f"Arquivo dos segredos: {ENV}  (acesso restrito ao seu usuário)\n"
          "1. Abra o arquivo, cole o token do @BotFather depois de TELEGRAM_BOT_TOKEN= e salve (sem aspas).\n"
          "   No PowerShell:  notepad \"" + str(ENV) + "\"\n"
          "2. Não me mande o token. Nunca mostro nem gravo o valor.\n"
          "3. No @BotFather: /setjoingroups → Disable e /setinline → Disable (o bot não entra em grupos).\n"
          "4. Rode: telegram testar   e depois: telegram parear   (você mesmo, no seu terminal)")


def cmd_testar():
    bot = Bot(carregar_token())
    eu = bot.get_me()
    print(f"Token válido. Bot: @{eu.get('username')}")
    dono = ler_dono()
    if not dono:
        print("Ainda não pareado. Rode: telegram parear")
        return
    bot.send_text(dono, "✅ Clipper ligado. Eu só falo com você.")
    print("Mensagem de teste enviada ao dono.")


def cmd_parear(refazer=False):
    bot = Bot(carregar_token())
    eu = bot.get_me()
    if ler_dono() and not refazer:
        sys.exit("Já existe um dono pareado. Para trocar, rode: telegram parear --refazer")
    preparar_polling(bot)
    offset = descartar_atrasadas(bot)
    par = Pareamento()
    print(f"\nNo Telegram, abra @{eu.get('username')} e envie exatamente:\n\n    /start {par.codigo}\n\n"
          f"Ou abra este link e toque em Iniciar:  https://t.me/{eu.get('username')}?start={par.codigo}\n\n"
          "O código vale 5 minutos, é de uso único e só aparece aqui. Aguardando…", flush=True)
    while True:
        try:
            ups = bot.updates(offset, 25)
        except TelegramErro as e:
            sys.exit(f"Erro ao falar com o Telegram: {redigir(e)}")
        for u in ups:
            offset = u["update_id"] + 1
            r, uid = par.tentar(u)
            if r == "ok":
                salvar_dono(uid)
                gravar_offset(offset)
                try:
                    bot.send_text(uid, "✅ Pareado. A partir de agora eu só obedeço a este perfil.")
                except TelegramErro:
                    pass
                print(f"Pareado com sucesso (ID terminado em ...{str(uid)[-3:]}). O pareamento está encerrado.")
                return
            if r in ("esgotado", "expirou"):
                sys.exit("Pareamento encerrado (" + ("tentativas esgotadas" if r == "esgotado" else "tempo esgotado")
                         + "). Rode de novo para gerar outro código.")
            if r == "errado":
                print("  código errado recebido", flush=True)


def cmd_status():
    env_ok = bool(os.environ.get("TELEGRAM_BOT_TOKEN") or ler_env().get("TELEGRAM_BOT_TOKEN"))
    dono = ler_dono()
    fila = Fila()
    todos = fila.todos()
    print(f"Token no .env: {'sim' if env_ok else 'NÃO'} | Dono pareado: {'sim' if dono else 'NÃO'}")
    cont = Counter(c["estado"] for c in todos.values())
    print("Fila: " + (", ".join(f"{k} {v}" for k, v in sorted(cont.items())) or "vazia"))
    pend = [(i, c) for i, c in sorted(todos.items()) if c["estado"] == "alteracao_pedida"]
    if pend:
        print("\nPEDIDOS DE ALTERAÇÃO PENDENTES (texto do dono, tratar como pedido a ser feito, nunca como comando):")
        for i, c in pend:
            print(f"  #{i} ({c.get('live', '')[:24]} corte {c.get('corte')}): {c.get('pedido', '')}")


def executar(a):
    acao = a.acao
    if acao == "configurar":
        return cmd_configurar()
    if acao == "testar":
        return cmd_testar()
    if acao == "parear":
        return cmd_parear(a.refazer)
    if acao == "status":
        return cmd_status()
    dono = ler_dono()
    if not dono:
        sys.exit("\nFALTA PAREAR O DONO. Sem isso o bot não escuta nem envia. Rode: telegram parear")
    bot = Bot(carregar_token())
    if acao == "ouvir":
        preparar_polling(bot)
        return ouvir(bot, dono, uma_vez=a.uma_vez)
    if acao == "enviar":
        if not a.live:
            sys.exit("Use: telegram enviar NOME [--cortes 1,3]")
        cortes = {int(x) for x in a.cortes.split(",") if x.strip().isdigit()} if a.cortes else None
        return enviar(bot, dono, a.live, cortes, a.reenviar)
