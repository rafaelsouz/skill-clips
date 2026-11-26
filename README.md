# Clipper do GTA 6

Uma skill grátis pro Claude Code que transforma lives longas de streamers em cortes verticais prontos pra postar.

Você manda o link de uma live (YouTube, Twitch ou Kick), ou pede as maiores lives de GTA 6 do dia, e recebe:

- o corte vertical (1080×1920): câmera do streamer em cima, jogo embaixo
- título de gancho e legenda palavra por palavra
- um `.txt` com a legenda do post, as hashtags e o crédito da live

## O que ela faz, em 4 passos

1. **Baixa só o áudio.** Uma live de 4 horas fica com algumas centenas de MB, não um vídeo gigante.
2. **Escuta a live inteira no seu computador.** Escreve tudo que o streamer fala e marca onde ele ficou alto
   (grito, surto) e, no YouTube, onde o chat acelerou.
3. **Separa os trechos mais quentes e a IA escolhe.** O script acha onde o streamer gritou, o chat acelerou e teve
   reação; a IA lê só esses trechos (rápido até na IA de graça) e dá nota pra cada momento.
4. **Monta os cortes.** Baixa só os minutos escolhidos, acha a câmera do streamer sozinha e coloca a legenda.

### Melhorias desta cópia (inspiradas no livecut-ai, licença MIT)

- **Filtro de sorteio, propaganda e agradecimento:** trechos assim saem com nota baixa e as falas ganham a marca `[META]`.
- **Escuta em duas passadas:** a live inteira com modelo leve e, depois, só os trechos quentes de novo com o `small`
  (`clip refinar NOME` refaz numa live já preparada).
- **Texto de terceiros higienizado:** a IA lê fala sem `<` `>`, links e quebras de linha, com aviso de que é dado.
- **Chat em paralelo:** o replay do chat baixa junto com a escuta; se for enorme, usa o pedaço já baixado.
- **Cortes de 20 a 90 s**, sempre com a preparação do lance dentro.
- **Telegram (aprovação):** `clip telegram configurar | testar | parear | enviar NOME | ouvir | status`. O bot manda o
  corte pro seu privado com botões ✅ Aprovar, 🗑 Rejeitar e ✏️ Pedir alteração e **só atende você** (id pareado e chat
  privado). Só polling, sem ngrok. Testes de segurança sem rede: `python scripts/teste_telegram.py`.
- **Instagram (API oficial):** `clip instagram configurar | testar | renovar | publicar N [--url URL] [--postar] | status`.
  Aceita o token do login unificado (`EAA`, o arquivo sobe direto, sem hospedar) e o do login do Instagram (`IG`, com
  `--url`). Só publica o que você aprovou no Telegram, uma vez, e só com `--postar` (sem ele é simulação). Testes sem
  rede: `python scripts/teste_instagram.py`.
- **Marca da conta em todo vídeo** (regra): `clip marca @seu_perfil` define o @ que vira marca d'água, centralizada na
  parte de baixo e translúcida (anticópia); o `fazer` não monta sem ela (`--sem-marca` desliga). A marca fica em `~/clipper-gta6/config.json`, fora desta pasta.
- **Volume normalizado a -14 LUFS** (pico ≤ -1 dBTP) e **bitrate limitado** (corte cabe em ~46 MB, abaixo do limite de
  50 MB do Telegram). A legenda não fica mais parada durante pausas de fala.
- **Legenda, título e post em PT-BR por padrão**, também em live em inglês: `clip traduzir NOME` separa as falas, a IA
  escreve a tradução (`pt.json`) e `clip fazer NOME` monta. `--legenda en` ou `auto` muda o idioma. `buscar --dias 2`
  acha as lives de ontem e hoje.

## Instalação

Você precisa do **Claude Code** (pode ser com a sua assinatura do Claude ou de graça, com o Ollama).

### Jeito de graça (Ollama) — precisa de 16 GB de memória ou mais

1. Baixe o Ollama em https://ollama.com/download e abra. Clique em **Continue** e depois em
   **"No thanks, I'll use Ollama locally"** (não precisa de conta).
2. Abra as **Settings** do Ollama (no Mac: menu Ollama → Settings…) e arraste o **Context length** de 4k para **64k**.
3. Feche o navegador e os outros programas (a IA usa quase toda a memória).
4. Abra o terminal (Mac: Command + Espaço → "Terminal"; Windows: Iniciar → "Windows PowerShell") e digite:
   ```
   mkdir cortes-gta
   cd cortes-gta
   ollama launch claude --model qwen3.5
   ```
   Com 32 GB ou mais, use `qwen3.5:27b`. Na primeira vez ele baixa a IA (~7,6 GB): o **Yes** já vem marcado, é só Enter.
5. Na pergunta "Is this a project you trust?", aperte a **seta pra baixo** até **"Yes, I trust this folder"** e Enter
   (a opção marcada de início é "No, exit"). Avisos amarelos como "isn't recognized" e "Not logged in" são normais.

### Com a assinatura do Claude

Baixe o app em https://claude.com/download (plano Pro ou maior), entre na aba **Code** e escolha uma pasta.
Sem terminal. Os cortes saem mais rápido e as escolhas ficam bem melhores.

### Colocando a skill (e atualizando)

> **Cópia local blindada.** Esta pasta é a única fonte: os instaladores não baixam a skill nem rodam script remoto,
> os pacotes estão em versões fixas (`requirements.txt`) e `verificar.py` confere se algum arquivo foi alterado.
> A versão original (baixa `main` do GitHub e roda `irm | iex`) foi removida de propósito.

Confira a integridade e instale a partir desta pasta:

- **Windows (PowerShell):** `python "$HOME\.claude\skills\clipper-gta6\verificar.py"` e depois
  `& "$HOME\.claude\skills\clipper-gta6\instalar.ps1"`
- **Mac/Linux:** `python3 ~/.claude/skills/clipper-gta6/verificar.py` e depois `bash ~/.claude/skills/clipper-gta6/instalar.sh`

Atualizar = trocar os arquivos aqui, **à mão e lendo o que mudou**, e depois rodar `verificar.py --gravar`.

### Avisos

- Live trancada por idade ou só pra membros não baixa (é trava do YouTube). O `buscar` já pula essas.
- No jeito de graça, em computador mais simples, cada corte demora mais. É normal, deixa rodando.

## Usando

- "corta essa live: LINK"
- "acha as maiores lives de GTA 6 de hoje e corta"
- "faz 5 cortes dessa live: LINK"
- IA se enrolando? "faz os cortes no modo automático: LINK" (tudo sozinho, títulos mais simples)

Live longa demora pra escutar (no modo de graça, o seu computador faz tudo). O trabalho roda sozinho em segundo
plano e guarda o progresso: se a IA mostrar "AINDA TRABALHANDO", é só deixar ela continuar.

Os cortes ficam na pasta `clipper-gta6/cortes` da sua pasta de usuário.

## Precisa ter

- Mac, Windows ou Linux
- Python 3.10 ou mais novo (se faltar, o instalador baixa um sozinho)
- ffmpeg: a skill baixa sozinha
- uns 2 GB livres pros modelos de escuta, mais o áudio das lives
- a live precisa ter acabado (live ainda ao vivo não dá pra cortar) e não pode ser trancada por idade/membros

## Respeite os streamers

Corte só quem libera cortes e mantenha o crédito que vem no `.txt` de cada corte.
