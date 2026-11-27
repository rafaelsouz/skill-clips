# Versões do prompt (SKILL.md)

O "prompt" do clipper é o `SKILL.md`: as regras que a IA segue para escolher os momentos. Cada versão tem uma **tag
git** anotada. As ferramentas (scripts) ficam em commits separados, para dar para trocar só o texto.

| Versão | Tag | O que é |
|---|---|---|
| v1 | `prompt-v1` | Escolha só por leitura do `candidatos.txt`, "2 momentos a mais", duração 20–90 s ("na dúvida, mais longo"), nota < 60 "nem escreva". |
| v2 | `prompt-v2` | Olhar antes de escolher, trava de qualidade, cortar miolo morto, duração 20–45 s, quantidade honesta, funil maior. **Nota mínima continua 60.** |

## O que mudou da v1 para a v2 (e por quê)

Motivo: na live Gameplayrj, de 5 cortes só 2 serviam. Os outros tinham jogo parado, silêncio de 10–22 s ou nada
acontecendo na tela; a escolha era feita só pelo texto.

| Tema | v1 | v2 |
|---|---|---|
| Olhar o vídeo | só lê a transcrição | `olhar NOME` mede jogo parado, silêncio e tempo morto e gera folha de quadros; a IA **abre a folha** (2.2a) |
| Qualidade | nada impede jogo parado | trava: parado ≤ 20%, silêncio ≤ 6 s, tempo morto ≤ 5 s; o `escolher` descarta o reprovado |
| Ação visível | não exigido | só vale momento com algo acontecendo na tela **e** reação do streamer |
| Miolo morto | silêncio ficava no corte | `cortar_miolo` tira o silêncio do meio com corte seco |
| Duração | 20–90 s, "na dúvida mais longo" | 20–45 s, ~10 s de contexto, abre onde a tensão começa, fecha após a reação |
| Quantidade | "2 a mais", completa até N | entrega só o que passou, máx. 1 extra, **não enche com fraco**, pode devolver menos que N |
| Funil | 12 trechos no `preparar` | 20 trechos (`--trechos`), pode subir para 30 |
| Nota mínima | 60 | **60 (não mudou)**; agora o código também descarta < 60 |

Obs.: a mudança do funil (12 → 20) está no **código** (`clip.py`), não no texto; ela fica mesmo voltando o prompt.

Arquivos novos de apoio (v2): `scripts/olhar.py`, `scripts/teste_selecao.py`. Alterados: `cortar.py`, `clip.py`.

## Como voltar para a v1 (só o texto do prompt)

As ferramentas novas ficam e não atrapalham: sem o texto da v2 a IA simplesmente não chama `olhar`. O `escolher`
continua descartando nota < 60.

```powershell
cd "$HOME\.claude\skills\clipper-gta6"
git checkout prompt-v1 -- SKILL.md
python verificar.py --gravar     # regrava os hashes de integridade
```

Para voltar à v2 depois: `git checkout prompt-v2 -- SKILL.md` e `python verificar.py --gravar`.

Para desfazer **tudo** da v2 (texto e ferramentas), volte ao commit da v1: `git checkout prompt-v1` (modo leitura) ou,
para manter no `main`, `git revert` dos commits posteriores a `69cdf32`.

Ver as diferenças: `git diff prompt-v1 prompt-v2 -- SKILL.md`.

## Como avaliar a v2

Rode uma live nova pelo fluxo completo e compare com a v1 pelo mesmo critério: quantos cortes você aprovaria / quantos
foram entregues. Se a v2 entregar poucos demais, o ajuste é nos limites de `scripts/olhar.py` (`PARADO_MAX`,
`SILENCIO_MAX`, `MORTO_MAX`), não em baixar a nota.
