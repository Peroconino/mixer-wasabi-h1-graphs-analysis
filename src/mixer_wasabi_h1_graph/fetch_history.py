"""Sorteia N transações Wasabi e baixa o histórico (com cache) dos endereços de entrada.

Uso:
    python fetch_history.py transactions/ --n 10 --sleep 1 [--include-reused]
    BLOCKCHAIN_API_KEY=... python fetch_history.py transactions/ --n 40

A amostra usa semente fixa e é um prefixo de uma ordem embaralhada estável:
a amostra de N=40 contém a de N=10, e o cache em disco evita rebaixar.
"""

import argparse
import json
import os
import random
import sys
import time
from collections import Counter
from pathlib import Path

import requests

URL = "https://api.blockchain.info/explorer-gateway-kt/btc/address/transactions"
PAGE = 50


def input_addrs(tx):
    return [
        i["prev_out"]["addr"]
        for i in tx.get("inputs", [])
        if i.get("prev_out", {}).get("addr")
    ]


def post(session, addr, offset, headers, retries=6):
    for attempt in range(retries):
        try:
            r = session.post(
                URL,
                headers=headers,
                timeout=30,
                json={"address": addr, "limit": PAGE, "offset": offset},
            )
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                wait = min(60, 2**attempt * 2)
                print(f"  HTTP {r.status_code}, esperando {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            print(
                f"  HTTP {r.status_code} para {addr}: {r.text[:120]}", file=sys.stderr
            )
            return None
        except (requests.RequestException, ValueError) as e:
            time.sleep(2**attempt)
            print(f"  erro {e}", file=sys.stderr)
    return None


def fetch_address(session, addr, cache: Path, sleep, max_pages, headers):
    f = cache / f"{addr}.json"
    if f.exists():
        return json.loads(f.read_text()), False
    txs, offset, truncated = [], 0, False
    for page in range(max_pages):
        data = post(session, addr, offset, headers)
        if data is None or "transactions" not in data:
            return (
                None,
                True,
            )  # falhou: NÃO grava cache, tenta de novo na próxima execução
        txs.extend(data["transactions"])
        if len(data["transactions"]) < PAGE:
            break
        offset += PAGE
        time.sleep(sleep)
    else:
        truncated = True  # bateu no teto de páginas
    out = {"address": addr, "truncated": truncated, "transactions": txs}
    f.write_text(json.dumps(out))
    time.sleep(sleep)
    return out, True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--max-pages", type=int, default=3)
    ap.add_argument("--cache", type=Path, default=Path("cache_addr"))
    ap.add_argument(
        "--include-reused",
        action="store_true",
        help="inclui endereços que aparecem em >1 tx Wasabi (de toda a base)",
    )
    args = ap.parse_args()

    files = sorted(p for p in args.folder.iterdir() if p.is_file())
    order = files[:]
    random.Random(args.seed).shuffle(order)
    sample = order[: args.n]
    Path("sample.txt").write_text("\n".join(p.name for p in sample) + "\n")

    addrs = set()
    for p in sample:
        addrs.update(input_addrs(json.loads(p.read_text())))
    if args.include_reused:
        cnt = Counter()
        for p in files:
            cnt.update(set(input_addrs(json.loads(p.read_text()))))
        addrs.update(a for a, c in cnt.items() if c > 1)
    addrs = sorted(addrs)

    args.cache.mkdir(exist_ok=True)
    headers = {"Content-Type": "application/json"}
    if os.environ.get("BLOCKCHAIN_API_KEY"):
        headers["X-Explorer-Auth-Key"] = os.environ["BLOCKCHAIN_API_KEY"]

    cached = sum((args.cache / f"{a}.json").exists() for a in addrs)
    print(f"{len(sample)} txs Wasabi | {len(addrs)} endereços | {cached} já em cache")

    session = requests.Session()
    ok = failed = trunc = 0
    t0 = time.time()
    for k, a in enumerate(addrs, 1):
        data, _ = fetch_address(
            session, a, args.cache, args.sleep, args.max_pages, headers
        )
        if data is None:
            failed += 1
        else:
            ok += 1
            trunc += data["truncated"]
        if k % 25 == 0 or k == len(addrs):
            print(
                f"[{k}/{len(addrs)}] ok={ok} falhas={failed} truncados={trunc} "
                f"({time.time() - t0:.0f}s)"
            )
    print("Rode de novo para tentar as falhas." if failed else "Concluído.")


if __name__ == "__main__":
    main()
