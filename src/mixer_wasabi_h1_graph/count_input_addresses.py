"""Conta os endereços de ENTRADA únicos das transações Wasabi em transactions/.

Uso:
    python count_input_addresses.py transactions/ [--out addresses.txt] [--debug]

Aceita arquivos JSON sem extensão. Suporta os formatos comuns:
  - blockchain.info rawtx:  inputs[].prev_out.addr
  - explorer-gateway:       inputs[].address
  - esplora (blockstream):  vin[].prevout.scriptpubkey_address
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def input_addresses(tx: dict):
    """Gera os endereços de cada input de uma tx (ignora inputs sem endereço)."""
    inputs = tx.get("inputs", tx.get("vin", []))
    for i in inputs:
        po = i.get("prev_out") or i.get("prevout") or i.get("prevOut") or {}
        addr = (
            i.get("address")
            or i.get("addr")
            or po.get("addr")
            or po.get("address")
            or po.get("scriptpubkey_address")
        )
        if addr:
            yield addr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--out", type=Path, default=Path("input_addresses.txt"))
    ap.add_argument("--debug", action="store_true", help="mostra a estrutura da 1ª tx")
    args = ap.parse_args()

    files = sorted(p for p in args.folder.iterdir() if p.is_file())
    if not files:
        sys.exit(f"Nenhum arquivo em {args.folder}")

    per_addr = Counter()  # endereço -> nº de txs Wasabi em que aparece como input
    inputs_per_tx = []
    bad = no_addr = 0

    for k, p in enumerate(files):
        try:
            tx = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            bad += 1
            print(f"[aviso] {p.name}: {e}", file=sys.stderr)
            continue
        if k == 0 and args.debug:
            print("Chaves da 1ª tx:", list(tx.keys()))
            ins = tx.get("inputs", tx.get("vin", []))
            print(
                "Exemplo de input:", json.dumps(ins[0] if ins else None, indent=2)[:600]
            )
        addrs = list(input_addresses(tx))
        if not addrs:
            no_addr += 1
        inputs_per_tx.append(len(addrs))
        per_addr.update(set(addrs))  # set: conta 1x por tx

    unique = sorted(per_addr)
    args.out.write_text("\n".join(unique) + "\n")

    total = sum(inputs_per_tx)
    reused = sum(1 for c in per_addr.values() if c > 1)
    n = len(inputs_per_tx)
    print(
        f"Arquivos lidos:            {len(files)} (com erro: {bad}, sem endereço de input: {no_addr})"
    )
    print(f"Inputs (com endereço):     {total}")
    print(f"Endereços de entrada únicos: {len(unique)}")
    print(f"Endereços em >1 tx Wasabi: {reused}")
    if n:
        s = sorted(inputs_per_tx)
        print(
            f"Inputs por tx: min={s[0]} mediana={s[n // 2]} max={s[-1]} média={total / n:.1f}"
        )
    for naptime in (10, 1, 0.2):
        print(
            f"  estimativa só 1ª página, sleep={naptime}s: {len(unique) * naptime / 3600:.1f} h"
        )
    print(f"Lista salva em {args.out}")


if __name__ == "__main__":
    main()
