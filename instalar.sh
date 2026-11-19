#!/bin/bash
# Instala o Clipper do GTA 6 no Mac/Linux A PARTIR DESTA PASTA. Versão blindada:
#   - NÃO baixa a skill da internet (a cópia local é a única fonte)
#   - NÃO executa script remoto (nada de "curl ... | sh")
#   - os pacotes vêm de requirements.txt, em versões fixas
# Uso:  bash ~/.claude/skills/clipper-gta6/instalar.sh
set -e
AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PY=""
for c in /Library/Frameworks/Python.framework/Versions/3.1[0-3]/bin/python3 python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 13) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "✗ Não achei o Python 3.10 a 3.13. Instale o oficial em https://www.python.org/downloads/"
  echo "  e rode este arquivo de novo."
  exit 1
fi
echo "✓ Python: $PY"
"$PY" "$AQUI/scripts/clip.py" instalar
