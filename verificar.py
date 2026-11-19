#!/usr/bin/env python3
"""Confere se algum arquivo da skill foi alterado desde a última revisão (SHA256SUMS).

  python verificar.py            confere: IGUAL ou DIFERENTE (sai com erro se algo mudou, sumiu ou apareceu)
  python verificar.py --gravar   grava a lista de hashes atual (faça SÓ depois de ler o que mudou)
"""
import hashlib
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
LISTA = AQUI / "SHA256SUMS"
IGNORAR = {"SHA256SUMS", ".gitignore"}


def hashes():
    out = {}
    for p in sorted(AQUI.rglob("*")):
        rel = p.relative_to(AQUI).as_posix()
        if p.is_file() and not {"__pycache__", ".git"} & set(p.parts) and rel not in IGNORAR:
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def main():
    atual = hashes()
    if "--gravar" in sys.argv:
        LISTA.write_text("".join(f"{h}  {n}\n" for n, h in atual.items()), encoding="utf-8")
        print(f"Gravei {len(atual)} hashes em {LISTA.name}.")
        return
    if not LISTA.exists():
        sys.exit("Sem SHA256SUMS. Rode com --gravar depois de revisar os arquivos.")
    ref = dict(reversed(l.split("  ", 1)) for l in LISTA.read_text(encoding="utf-8").splitlines() if l.strip())
    ref = {n: h for n, h in ref.items()}
    problemas = ([f"ALTERADO: {n}" for n in atual if n in ref and atual[n] != ref[n]]
                 + [f"NOVO (não revisado): {n}" for n in atual if n not in ref]
                 + [f"SUMIU: {n}" for n in ref if n not in atual])
    if problemas:
        print("DIFERENTE do que foi revisado:\n  " + "\n  ".join(problemas))
        sys.exit(1)
    print(f"IGUAL: {len(atual)} arquivos conferem com o que foi revisado.")


if __name__ == "__main__":
    main()
