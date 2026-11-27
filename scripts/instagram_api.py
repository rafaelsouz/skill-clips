"""Instagram (API oficial): confere a conta e publica Reels já APROVADOS no Telegram.

Dois tipos de login, escolhidos pelo prefixo do token:
  - EAA... = LOGIN UNIFICADO / Facebook Login for Business (host graph.facebook.com, token de usuário de sistema, sem
    página do Facebook). A conta vem de /me/assigned_instagram_accounts e o arquivo local sobe pelo upload resumível
    (rupload.facebook.com): não precisa hospedar o vídeo.
  - IG...  = Login do Instagram (host graph.instagram.com). A Meta baixa o vídeo de uma URL pública (--url).

Segurança:
  - O token fica no .env (fora do repositório, acesso restrito), vai só em cabeçalho e nunca é impresso, gravado em log
    nem incluído em erro (a exceção original é descartada: ela traz a URL). O endereço de upload é conferido antes de
    mandar o token pra ele (só rupload.facebook.com).
  - Só publica clipe que você APROVOU no Telegram, no máximo uma vez (estado "postado"), e só com --postar.
    Sem --postar é uma simulação: valida tudo, manda o vídeo à Meta e para antes de publicar.
  - O aviso de permissões em excesso (ex.: anúncios) aparece no 'testar'.
"""
from __future__ import annotations

import ipaddress
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

import bot_telegram as bt
from comum import CONFIG, TRABALHO, gravar_json, ler_json, limpar_texto, marca_perfil

BASE_IG = "https://graph.instagram.com"
BASE_FB = "https://graph.facebook.com"
UPLOAD_PREFIXO = "https://rupload.facebook.com/ig-api-upload/"
VERSAO = "v27.0"  # a documentação atual usa essa versão; muda com INSTAGRAM_API_VERSION no .env
LIMITE_LEGENDA = 2200
MAX_HASHTAGS = 30
LIMITE_BYTES = 300_000_000  # tamanho máximo de Reels segundo a Meta
PERMISSOES_NECESSARIAS = {"instagram_basic", "instagram_content_publish"}
TOKEN_RE = re.compile(r"\A[A-Za-z0-9_.\-]{30,}\Z")
_QS = re.compile(r"(access_token=)[^&\s\"']+")
_LONGO = re.compile(r"[A-Za-z0-9_\-]{80,}")


def redigir_ig(texto, *segredos):
    """Tira o token (e qualquer string longa parecida) de mensagens de erro e logs."""
    t = str(texto)
    for s in segredos:
        if s:
            t = t.replace(s, "***")
    return bt.redigir(_LONGO.sub("***", _QS.sub(r"\1***", t)))


class InstagramErro(Exception):
    def __init__(self, msg, codigo=None, subcodigo=None):
        super().__init__(msg)
        self.codigo, self.subcodigo = codigo, subcodigo


# ---------------------------------------------------------------- segredos e configuração
def ler_valor_env(chave):
    return (os.environ.get(chave) or bt.ler_env().get(chave) or "").strip()


def carregar_token():
    token = ler_valor_env("INSTAGRAM_ACCESS_TOKEN")
    if not token:
        sys.exit(f"\nFALTA O TOKEN DO INSTAGRAM. Rode: instagram configurar   (o arquivo fica em {bt.ENV})")
    if not TOKEN_RE.match(token):
        sys.exit("\nO token do Instagram no .env não tem o formato esperado (não mostro o valor). Gere de novo no painel.")
    return token


def atualizar_env(chave, valor, caminho=None):
    """Troca (ou acrescenta) uma linha do .env sem mexer nas outras e mantém o acesso restrito."""
    caminho = Path(caminho or bt.ENV)
    linhas = caminho.read_text(encoding="utf-8-sig").splitlines() if caminho.exists() else []
    novas, achou = [], False
    for linha in linhas:
        if "=" in linha and not linha.strip().startswith("#") and linha.split("=", 1)[0].strip() == chave:
            novas.append(f"{chave}={valor}")
            achou = True
        else:
            novas.append(linha)
    if not achou:
        novas.append(f"{chave}={valor}")
    tmp = caminho.with_suffix(".tmp")
    tmp.write_text("\n".join(novas) + "\n", encoding="utf-8")
    bt.restringir(tmp)
    os.replace(tmp, caminho)


def ler_conta():
    return (ler_json(CONFIG, {}) or {}).get("instagram") or {}


def salvar_conta(**campos):
    dados = ler_json(CONFIG, {}) or {}
    conta = dados.get("instagram") or {}
    conta.update(campos)
    dados["instagram"] = conta
    gravar_json(CONFIG, dados)


def escolher_conta(contas, marca=""):
    """Com uma conta só, é ela. Com várias, vale a que tem o @ da marca d'água; senão pede pra definir."""
    if len(contas) == 1:
        return contas[0]
    for c in contas:
        if marca and c["username"].lower() == marca.lower():
            return c
    sys.exit("\nO token enxerga mais de uma conta (" + ", ".join("@" + c["username"] for c in contas)
             + "). Defina a marca com o @ da conta certa: marca @perfil")


# ---------------------------------------------------------------- cliente da API
class Instagram:
    def __init__(self, token, versao=None, http=None, dormir=time.sleep):
        self._token = token
        self.unificado = token.startswith("EAA")  # login unificado / Facebook: graph.facebook.com
        self.base = BASE_FB if self.unificado else BASE_IG
        self.versao = versao or ler_valor_env("INSTAGRAM_API_VERSION") or VERSAO
        self._http = http or requests.Session()
        self._dormir = dormir

    def __repr__(self):
        return "Instagram(***)"

    def _req(self, metodo, caminho, params=None, dados=None, com_versao=True, timeout=60):
        url = f"{self.base}/{self.versao}/{caminho.lstrip('/')}" if com_versao else f"{self.base}/{caminho.lstrip('/')}"
        try:
            r = self._http.request(metodo, url, params=params, data=dados, timeout=timeout,
                                   headers={"Authorization": f"Bearer {self._token}"})
            j = r.json()
        except (requests.RequestException, ValueError) as e:
            # 'from None': a exceção original traz a URL
            raise InstagramErro(f"{metodo} {caminho.split('/')[-1]}: falha de rede ({type(e).__name__})") from None
        return self._tratar(metodo, caminho, r, j)

    def _tratar(self, metodo, caminho, r, j):
        if isinstance(j, dict) and isinstance(j.get("error"), dict):
            er = j["error"]
            raise InstagramErro(f"{metodo} {caminho.split('/')[-1]}: {redigir_ig(er.get('message', 'erro'), self._token)} "
                                f"(código {er.get('code')})", er.get("code"), er.get("error_subcode"))
        if not r.ok:
            raise InstagramErro(f"{metodo} {caminho.split('/')[-1]}: HTTP {r.status_code}")
        return j

    def contas(self):
        """Contas do Instagram que o token enxerga: [{'user_id', 'username'}]."""
        if self.unificado:
            itens = (self._req("GET", "me/assigned_instagram_accounts", params={"fields": "id,username"}).get("data") or [])
        else:
            j = self._req("GET", "me", params={"fields": "user_id,username"})
            itens = [{"id": j.get("user_id"), "username": j.get("username")}]
        out = [{"user_id": str(i["id"]), "username": str(i["username"])} for i in itens
               if isinstance(i, dict) and i.get("id") and i.get("username")]
        if not out:
            raise InstagramErro("nenhuma conta do Instagram encontrada para este token (a permissão instagram_basic "
                                "está concedida e a conta foi atribuída ao app?)")
        return out

    def me(self):
        return self.contas()[0]

    def permissoes(self):
        """Permissões concedidas ao token (só no login unificado/Facebook)."""
        if not self.unificado:
            return None
        j = self._req("GET", "me/permissions")
        return sorted(p["permission"] for p in (j.get("data") or []) if p.get("status") == "granted")

    def limite(self, user_id):
        j = self._req("GET", f"{user_id}/content_publishing_limit", params={"fields": "quota_usage,config"})
        d = (j.get("data") or [{}])[0]
        return {"usado": d.get("quota_usage"), "total": ((d.get("config") or {}).get("quota_total"))}

    def criar_conteiner(self, user_id, video_url, legenda, share_to_feed=True):
        """Contêiner com URL pública do vídeo (a Meta baixa o arquivo dela)."""
        j = self._req("POST", f"{user_id}/media", dados={"media_type": "REELS", "video_url": video_url,
                                                          "caption": legenda,
                                                          "share_to_feed": "true" if share_to_feed else "false"})
        if not j.get("id"):
            raise InstagramErro("media: a Meta não devolveu o id do contêiner")
        return str(j["id"])

    def criar_conteiner_resumivel(self, user_id, legenda):
        """Contêiner para upload do arquivo local (login unificado/Facebook). Devolve (id, uri de upload)."""
        j = self._req("POST", f"{user_id}/media", dados={"upload_type": "resumable", "media_type": "REELS",
                                                          "caption": legenda})
        if not j.get("id") or not j.get("uri"):
            raise InstagramErro("media: a Meta não devolveu o contêiner e o endereço de upload")
        if not str(j["uri"]).startswith(UPLOAD_PREFIXO):  # o token só vai pra rupload.facebook.com
            raise InstagramErro("a Meta devolveu um endereço de upload inesperado; não enviei nada")
        return str(j["id"]), str(j["uri"])

    def _bytes_enviados(self, conteiner):
        try:
            st = self._req("GET", conteiner, params={"fields": "video_status"})
            return int((((st.get("video_status") or {}).get("uploading_phase") or {}).get("bytes_transferred")) or 0)
        except (InstagramErro, ValueError, TypeError):
            return 0

    def enviar_arquivo(self, uri, caminho, tentativas=3):
        """Sobe o mp4 local pelo upload resumível. Se a rede cair, retoma do ponto que a Meta recebeu."""
        if not str(uri).startswith(UPLOAD_PREFIXO):
            raise InstagramErro("endereço de upload inesperado; não enviei nada")
        conteiner = str(uri).rstrip("/").split("/")[-1]
        tamanho, offset = Path(caminho).stat().st_size, 0
        for tentativa in range(tentativas):
            try:
                with open(caminho, "rb") as f:
                    f.seek(offset)
                    r = self._http.request("POST", uri, data=f, timeout=900,
                                           headers={"Authorization": f"OAuth {self._token}", "offset": str(offset),
                                                    "file_size": str(tamanho)})
                    j = r.json()
            except (requests.RequestException, ValueError) as e:
                if tentativa == tentativas - 1:
                    raise InstagramErro(f"upload: falha de rede ({type(e).__name__})") from None
                offset = self._bytes_enviados(conteiner)
                self._dormir(5 * (tentativa + 1))
                continue
            if isinstance(j, dict) and j.get("success"):
                return
            if isinstance(j, dict) and isinstance(j.get("error"), dict):
                self._tratar("POST", "upload", r, j)
            info = (j.get("debug_info") if isinstance(j, dict) else None) or {}
            if info.get("retriable") and tentativa < tentativas - 1:
                offset = self._bytes_enviados(conteiner)
                self._dormir(5 * (tentativa + 1))
                continue
            tipo = str(info.get("type") or f"HTTP {r.status_code}")
            raise InstagramErro(f"upload recusado pela Meta ({tipo}: {redigir_ig(info.get('message', 'sem detalhe'), self._token)})",
                                codigo=tipo)

    def status(self, conteiner):
        return self._req("GET", conteiner, params={"fields": "status_code,status"})

    def aguardar(self, conteiner, tentativas=40, intervalo=10):
        """Espera o vídeo ficar pronto (FINISHED). ERROR/EXPIRED encerram com erro."""
        for _ in range(tentativas):
            st = self.status(conteiner)
            codigo = st.get("status_code")
            if codigo in ("FINISHED", "PUBLISHED"):
                return codigo
            if codigo in ("ERROR", "EXPIRED"):
                raise InstagramErro(f"o vídeo foi recusado pela Meta ({codigo}): {redigir_ig(st.get('status', ''), self._token)}")
            self._dormir(intervalo)
        raise InstagramErro("a Meta ainda está processando o vídeo; tente de novo em alguns minutos")

    def publicar(self, user_id, conteiner):
        j = self._req("POST", f"{user_id}/media_publish", dados={"creation_id": conteiner})
        if not j.get("id"):
            raise InstagramErro("media_publish: a Meta não devolveu o id do post")
        return str(j["id"])

    def permalink(self, media_id):
        try:
            return self._req("GET", media_id, params={"fields": "permalink"}).get("permalink")
        except InstagramErro:
            return None

    def renovar(self):
        if self.unificado:
            raise InstagramErro("token de usuário de sistema (login unificado): não renova por aqui; "
                                "gere de novo no painel se parar de funcionar")
        j = self._req("GET", "refresh_access_token", com_versao=False,
                      params={"grant_type": "ig_refresh_token", "access_token": self._token})
        if not j.get("access_token"):
            raise InstagramErro("refresh_access_token: sem token novo na resposta")
        return j["access_token"], j.get("expires_in")


# ---------------------------------------------------------------- validação e legenda
def url_publica_ok(url):
    """A Meta baixa o vídeo desta URL: tem que ser https, pública e sem credenciais nem IP/localhost."""
    if not isinstance(url, str) or len(url) > 2000 or not re.fullmatch(r"[\x21-\x7e]+", url):
        return False
    p = urlparse(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password:
        return False
    host = p.hostname.lower()
    if host == "localhost" or host.endswith((".local", ".internal", ".localhost", ".lan")) or "." not in host:
        return False
    try:
        ipaddress.ip_address(host)
        return False  # IP direto não
    except ValueError:
        return True


def montar_legenda(post, credito=None):
    """Legenda do post: texto + crédito, até 2200 caracteres e 30 hashtags (limites do Instagram)."""
    texto = limpar_texto(post, 3000)
    achadas = re.findall(r"#\w+", texto)
    for extra in achadas[MAX_HASHTAGS:]:
        texto = texto.replace(extra, "", 1)
    texto = re.sub(r"\s+", " ", texto).strip()
    cred = f"\n\n{limpar_texto(credito, 80)}" if credito else ""
    if len(texto) + len(cred) > LIMITE_LEGENDA:
        texto = texto[: LIMITE_LEGENDA - len(cred) - 1].rstrip() + "…"
    return texto + cred


def avisos_do_video(caminho, dims=None):
    """Problemas que impedem (lista 'erros') ou merecem aviso ('avisos') antes de mandar o vídeo à Meta."""
    erros, avisos = [], []
    p = Path(caminho)
    if not p.is_file() or p.suffix.lower() != ".mp4":
        return ["o vídeo precisa ser um arquivo .mp4 que exista"], avisos
    if p.stat().st_size > LIMITE_BYTES:
        erros.append("o vídeo passa de 300 MB (limite da Meta)")
    larg, alt, dur = dims or bt._dims(p)
    if dur and dur > 90:
        avisos.append(f"duração de {dur:.0f} s: confira o limite atual de Reels na documentação")
    if larg and alt and abs(larg / alt - 9 / 16) > 0.02:
        avisos.append(f"proporção {larg}x{alt}: Reels é 9:16 (1080x1920)")
    return erros, avisos


# cópias do vídeo para o Instagram: a Meta recusou os 1080x1920 de 45 s ou mais (ProcessingFailedError) e aceitou os mesmos
# clipes em 720p e 540p. O Instagram reduz o Reels para ~720p de qualquer jeito. O arquivo original não é tocado.
NIVEIS = [(720, 1280, 21, "3000k", "6000k"), (540, 960, 24, "1500k", "3000k")]


def preparar_copia(mp4, destino, nivel=0):
    """Gera a cópia do corte para subir no Instagram (nivel 0 = 720x1280, 1 = 540x960). Devolve o caminho."""
    larg, alt, crf, maxrate, buf = NIVEIS[nivel]
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    saida = destino / f"{Path(mp4).stem}_ig{larg}.mp4"
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4), "-vf", f"scale={larg}:{alt}:flags=lanczos",
                        "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-maxrate", maxrate, "-bufsize", buf,
                        "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                        "-movflags", "+faststart", str(saida)], capture_output=True)
    if r.returncode != 0 or not saida.exists():
        raise InstagramErro("não consegui preparar a cópia do vídeo para o Instagram (o ffmpeg falhou)")
    return saida


def permissoes_em_excesso(concedidas):
    """Permissões do token além das de publicar (anúncios, mensagens...): quanto menos, menor o estrago se vazar."""
    return sorted(set(concedidas or []) - PERMISSOES_NECESSARIAS - {"public_profile"})


# ---------------------------------------------------------------- publicar (só o que foi aprovado, uma vez)
def publicar_clipe(ig, user_id, clip_id, url=None, postar=False, com_credito=True, fila=None, dims=None, preparar=None,
                   refazer=False):
    fila = fila or bt.Fila()
    clip = fila.clipe(clip_id)
    if not clip:
        raise InstagramErro(f"o clipe #{clip_id} não existe na fila (ele foi enviado pelo Telegram?)")
    if clip.get("estado") == "postado" or (clip.get("instagram") or {}).get("estado") == "publicado":
        raise InstagramErro(f"o clipe #{clip_id} já foi publicado: não publico duas vezes")
    if clip.get("estado") != "aprovado":
        raise InstagramErro(f"o clipe #{clip_id} está '{clip.get('estado')}': só publico o que você APROVOU no Telegram")
    if url:
        if not url_publica_ok(url):
            raise InstagramErro("a --url precisa ser https, pública, sem IP nem localhost nem senha")
        modo = "url"
    elif ig.unificado:
        modo = "arquivo"  # upload resumível: o arquivo local sobe direto, sem hospedar
    else:
        raise InstagramErro("FALTA UMA URL PÚBLICA do vídeo: com o login do Instagram use --url https://... "
                            "(a Meta baixa o arquivo desse endereço). O login unificado não precisa de URL.")
    mp4 = Path(clip.get("arquivo", ""))
    erros, avisos = avisos_do_video(mp4, dims)
    if erros:
        raise InstagramErro("; ".join(erros))
    txt = mp4.with_suffix(".txt")
    partes = (txt.read_text(encoding="utf-8").split("\n\n", 2) + ["", "", ""])[:3] if txt.exists() else ["", "", ""]
    info = ler_json(TRABALHO / str(clip.get("live", "")) / "info.json", {}) or {}
    canal = info.get("uploader") or info.get("channel")
    legenda = montar_legenda(partes[1], f"Crédito: {canal}" if (com_credito and canal) else None)

    ig_estado = {} if refazer else dict(clip.get("instagram") or {})  # --refazer: descarta o contêiner antigo e sobe de novo
    limite = ig.limite(user_id)
    if limite["usado"] is not None and limite["total"] and limite["usado"] >= limite["total"]:
        raise InstagramErro(f"a cota de publicação das últimas 24 h acabou ({limite['usado']}/{limite['total']})")

    pasta_copias = bt.PASTA / "instagram" / f"clipe_{clip_id}"
    preparar = preparar or preparar_copia
    feitas = {}

    def novo_conteiner(nivel=0):
        qualidade = None
        if modo == "url":
            c = ig.criar_conteiner(user_id, url, legenda)
        else:
            if nivel not in feitas:  # cópia 720p (ou 540p) só para o Instagram; o original fica como está
                feitas[nivel] = preparar(mp4, pasta_copias, nivel)
            copia = feitas[nivel]
            qualidade = f"{NIVEIS[nivel][0]}p"
            c, uri = ig.criar_conteiner_resumivel(user_id, legenda)
            ig_estado.update(estado="conteiner_criado", conteiner=c, modo=modo, qualidade=qualidade)
            fila.mudar(clip_id, instagram=ig_estado)
            try:
                ig.enviar_arquivo(uri, copia)
            except InstagramErro:
                ig_estado.pop("conteiner", None)  # contêiner sem vídeo não serve: a próxima tentativa cria outro
                ig_estado["estado"] = "upload_falhou"
                fila.mudar(clip_id, instagram=ig_estado)
                raise
        ig_estado.update(estado="conteiner_criado", conteiner=c, modo=modo, qualidade=qualidade,
                         criado_em=datetime.now().isoformat(timespec="seconds"))
        fila.mudar(clip_id, instagram=ig_estado)
        fila.registrar(clip=clip_id, evento="instagram_conteiner", modo=modo, qualidade=qualidade)
        return c

    def criar_conteiner():
        """No upload do arquivo: 720p (2 tentativas, a recusa às vezes é passageira) e, se a Meta continuar recusando o
        processamento, 540p."""
        ultimo = None
        for nivel in range(len(NIVEIS)):
            for _ in range(2):
                try:
                    return novo_conteiner(nivel)
                except InstagramErro as e:
                    if modo == "url" or e.codigo != "ProcessingFailedError":
                        raise
                    ultimo = e
        raise ultimo

    cid = ig_estado.get("conteiner")
    reutilizado = bool(cid)
    cid = cid or criar_conteiner()
    try:
        situacao = ig.aguardar(cid)
    except InstagramErro as e:
        if not (reutilizado and ("EXPIRED" in str(e) or "ERROR" in str(e))):  # contêiner antigo venceu (24 h): cria outro
            raise
        cid = criar_conteiner()
        situacao = ig.aguardar(cid)
    if not postar:
        ig_estado["estado"] = "pronto_para_postar"
        fila.mudar(clip_id, instagram=ig_estado)
        return {"simulado": True, "conteiner": cid, "legenda": legenda, "avisos": avisos, "limite": limite, "modo": modo,
                "qualidade": ig_estado.get("qualidade")}

    media_id = None
    if situacao != "PUBLISHED":  # PUBLISHED = já foi publicado antes (nunca posta duas vezes)
        try:
            media_id = ig.publicar(user_id, cid)
        except InstagramErro:
            # a resposta pode ter se perdido com o post já feito: confere antes de dizer que falhou
            if ig.status(cid).get("status_code") != "PUBLISHED":
                raise
    link = ig.permalink(media_id) if media_id else None
    ig_estado.update(estado="publicado", media_id=media_id, link=link, publicado_em=datetime.now().isoformat(timespec="seconds"))
    fila.mudar(clip_id, estado="postado", instagram=ig_estado)
    fila.registrar(clip=clip_id, evento="postado_no_instagram", link=link)
    if pasta_copias.parent == bt.PASTA / "instagram":  # a cópia temporária não é mais necessária
        shutil.rmtree(pasta_copias, ignore_errors=True)
    return {"simulado": False, "media_id": media_id, "link": link, "legenda": legenda, "avisos": avisos, "modo": modo,
            "qualidade": ig_estado.get("qualidade")}


# ---------------------------------------------------------------- comandos
def cmd_configurar():
    bt.CASA.mkdir(parents=True, exist_ok=True)
    if not ler_valor_env("INSTAGRAM_ACCESS_TOKEN") and "INSTAGRAM_ACCESS_TOKEN" not in bt.ler_env():
        with open(bt.ENV, "a", encoding="utf-8") as f:
            f.write(("" if not bt.ENV.exists() or bt.ENV.read_text(encoding="utf-8-sig").endswith("\n") else "\n")
                    + "INSTAGRAM_ACCESS_TOKEN=\n")
    bt.restringir(bt.ENV)
    print(f"Arquivo dos segredos: {bt.ENV}  (acesso restrito ao seu usuário)\n"
          "1. Abra o arquivo, cole o token do painel da Meta depois de INSTAGRAM_ACCESS_TOKEN= e salve (sem aspas).\n"
          f"   No PowerShell:  notepad \"{bt.ENV}\"\n"
          "2. Não me mande o token. Nunca mostro nem gravo o valor.\n"
          "3. Rode: instagram testar   (só lê: confere o token, mostra o @ da conta e a cota; não posta nada)")


def cmd_testar():
    ig = Instagram(carregar_token())
    marca = marca_perfil().lstrip("@").lower()
    conta = escolher_conta(ig.contas(), marca)
    tipo = "login unificado" if ig.unificado else "login do Instagram"
    salvar_conta(user_id=conta["user_id"], username=conta["username"], tipo=tipo)
    print(f"Token válido ({tipo}). Conta: @{conta['username']}")
    if marca and marca != conta["username"].lower():
        print(f"ATENÇÃO: a marca d'água é @{marca}, mas o token é da conta @{conta['username']}. Confira se é a conta certa.")
    perms = ig.permissoes()
    if perms is not None:
        faltam = sorted(PERMISSOES_NECESSARIAS - set(perms))
        print("Permissão de publicar: " + ("OK" if not faltam else "FALTA " + ", ".join(faltam)))
        extra = permissoes_em_excesso(perms)
        if extra:
            print("ATENÇÃO: o token tem permissões além do necessário (" + ", ".join(extra) + "). Se vazar, quem o tiver pode "
                  "usá-las. Crie uma configuração só com instagram_basic e instagram_content_publish e gere outro token.")
    try:
        lim = ig.limite(conta["user_id"])
        print(f"Cota de publicação (24 h): {lim['usado']}/{lim['total']}")
    except InstagramErro as e:
        print(f"(não consegui ler a cota: {e})")
    if ig.unificado:
        print("Token de usuário de sistema: não renova por aqui; se parar de funcionar, gere de novo no painel.")
        return
    cfg = ler_conta()
    if not cfg.get("token_desde"):
        salvar_conta(token_desde=datetime.now().isoformat(timespec="seconds"))
        print("O token vale 60 dias. Anotei hoje como referência; rode 'instagram renovar' antes de 50 dias.")
    else:
        dias = (datetime.now() - datetime.fromisoformat(cfg["token_desde"])).days
        print(f"Token em uso há ~{dias} dias (vale 60; renove antes de 50 com 'instagram renovar').")


def cmd_renovar():
    ig = Instagram(carregar_token())
    try:
        novo, expira = ig.renovar()
    except InstagramErro as e:
        sys.exit(f"\n{e}")
    if not TOKEN_RE.match(novo):
        sys.exit("A Meta devolveu um token em formato inesperado; não gravei nada.")
    atualizar_env("INSTAGRAM_ACCESS_TOKEN", novo)
    salvar_conta(token_desde=datetime.now().isoformat(timespec="seconds"))
    print(f"Token renovado e gravado no .env (válido por ~{int(expira or 0) // 86400} dias).")


def cmd_status():
    tem = bool(ler_valor_env("INSTAGRAM_ACCESS_TOKEN"))
    conta = ler_conta()
    print(f"Token no .env: {'sim' if tem else 'NÃO'} | Conta conferida: "
          f"{('@' + conta['username'] + ' (' + conta.get('tipo', '?') + ')') if conta.get('username') else 'ainda não (rode instagram testar)'}")
    for i, c in sorted(bt.Fila().todos().items()):
        ig = c.get("instagram")
        if ig:
            print(f"  #{i} ({c.get('live', '')[:24]} corte {c.get('corte')}): {ig.get('estado')}" + (f" {ig['link']}" if ig.get("link") else ""))


def executar(a):
    acao = a.acao
    if acao == "configurar":
        return cmd_configurar()
    if acao == "testar":
        return cmd_testar()
    if acao == "renovar":
        return cmd_renovar()
    if acao == "status":
        return cmd_status()
    if acao == "publicar":
        if not a.clipe:
            sys.exit("Use: instagram publicar NUMERO_DO_CLIPE [--url https://...] [--postar]")
        ig = Instagram(carregar_token())
        user_id = ler_conta().get("user_id") or escolher_conta(ig.contas(), marca_perfil().lstrip("@"))["user_id"]
        try:
            r = publicar_clipe(ig, user_id, int(a.clipe), a.url, postar=a.postar, com_credito=not a.sem_credito,
                               refazer=a.refazer)
        except InstagramErro as e:
            sys.exit(f"\nNÃO PUBLIQUEI: {e}")
        for aviso in r.get("avisos", []):
            print(f"  aviso: {aviso}")
        print("\nLegenda que vai no post:\n" + "-" * 40 + f"\n{r['legenda']}\n" + "-" * 40)
        if r["simulado"]:
            print(f"\nSIMULAÇÃO OK: o vídeo foi aceito pela Meta (contêiner {r['conteiner']}, modo {r['modo']}"
                  + (f", cópia {r['qualidade']}" if r.get("qualidade") else "") + "), nada foi publicado.\n"
                  f"Para publicar de verdade: instagram publicar {a.clipe}" + (f" --url {a.url}" if a.url else "") + " --postar")
        else:
            print("\nPUBLICADO." + (f" Link: {r['link']}" if r.get("link") else ""))
