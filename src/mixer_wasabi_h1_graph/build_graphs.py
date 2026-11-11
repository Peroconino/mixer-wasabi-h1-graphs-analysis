#!/usr/bin/env python3
"""Clusteriza com H1 os endereços de entrada das txs Wasabi e gera os dois grafos.

Uso:
    python build_graphs.py transactions/ --cache cache_addr --sample sample.txt --out out/

Saídas em out/:
  graph_before.graphml / graph_after_h1.graphml                  (completos)
  graph_before_connected.graphml / graph_after_h1_connected.graphml  (sem vértices isolados; use estes no Gephi)
  componentes.png   maiores componentes, antes (endereços) x depois (clusters H1)
  clusters.csv, summary.json

  - Grafo 1: vértice = endereço, aresta a->b = existe tx pré-Wasabi com a como input e b como output
  - Grafo 2: vértice = cluster H1, aresta = pagamentos agregados entre clusters

Regras:
  * H1 (inputs de uma mesma tx = mesmo dono) só é aplicada a txs PRÉ-Wasabi.
    Nunca às próprias CoinJoins (inputs de donos diferentes).
  * De cada histórico de endereço, ficam só txs com blockHeight < altura da tx Wasabi
    em que o endereço entrou; descartam-se txs da base Wasabi e txs com cara de
    CoinJoin (>= --cj-min inputs E >= --cj-min outputs).
  * Peso da aresta a->b: n_tx = nº de txs; value = soma do valor do output b nessas txs
    (a tx não diz quanto de a foi para b; é o valor recebido por b).
  * Origem de cada entrada Wasabi = tx mais recente anterior à Wasabi que tem o endereço como output.
"""
import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx


class UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def draw_components(g1, g2, cid, top, path):
    comps = sorted(nx.weakly_connected_components(g1), key=len, reverse=True)
    comps = [c for c in comps if len(c) > 1][:top]
    if not comps:
        return
    fig, axes = plt.subplots(len(comps), 2, figsize=(12, 5.5 * len(comps)), squeeze=False)
    for row, comp in enumerate(comps):
        sub1 = g1.subgraph(comp)
        sub2 = g2.subgraph({cid[a] for a in comp})
        ax = axes[row][0]
        nx.draw_networkx(sub1, pos=nx.spring_layout(sub1, seed=1), ax=ax, with_labels=False,
                         node_size=18, width=0.5, arrows=False, alpha=0.9,
                         node_color=["#e8833a" if sub1.nodes[n].get("wasabi_input") else "#4a7fb5"
                                     for n in sub1])
        ax.set_title(f"Antes: {sub1.number_of_nodes()} endereços, {sub1.number_of_edges()} arestas")
        ax = axes[row][1]
        nx.draw_networkx(sub2, pos=nx.spring_layout(sub2, seed=1), ax=ax, with_labels=False,
                         node_size=[18 + 25 * (sub2.nodes[n]["size"] ** 0.5) for n in sub2],
                         width=0.5, arrows=False, alpha=0.9,
                         node_color=["#e8833a" if sub2.nodes[n]["n_wasabi_inputs"] else "#4a7fb5"
                                     for n in sub2])
        ax.set_title(f"Depois (H1): {sub2.number_of_nodes()} clusters, {sub2.number_of_edges()} arestas")
        for a in axes[row]:
            a.axis("off")
    fig.text(0.5, 0.995, "laranja = entrada Wasabi / cluster com entrada Wasabi; azul = outros; "
             "tamanho (depois) = nº de endereços no cluster", ha="center", va="top", fontsize=9)
    fig.tight_layout(rect=(0, 0, 1, 0.985))
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--cache", type=Path, default=Path("cache_addr"))
    ap.add_argument("--sample", type=Path, default=Path("sample.txt"))
    ap.add_argument("--out", type=Path, default=Path("out"))
    ap.add_argument("--cj-min", type=int, default=10)
    ap.add_argument("--top", type=int, default=4, help="nº de componentes no PNG")
    args = ap.parse_args()
    args.out.mkdir(exist_ok=True)

    # --- 1. txs Wasabi: hashes de toda a base; entradas só da amostra
    all_wasabi = set()
    txs = {}
    for p in sorted(f for f in args.folder.iterdir() if f.is_file()):
        t = json.loads(p.read_text())
        all_wasabi.add(t["hash"])
        txs[p.name] = t
    names = [n.strip() for n in args.sample.read_text().split() if n.strip()] \
        if args.sample.exists() else list(txs)
    wasabi_height = {}     # endereço -> menor altura Wasabi em que é input
    for n in names:
        t = txs[n]
        for i in t["inputs"]:
            a = (i.get("prev_out") or {}).get("addr")
            if a:
                h = t["block_height"]
                wasabi_height[a] = min(h, wasabi_height.get(a, h))
    wasabi_inputs = set(wasabi_height)

    def kind_of(t):
        if t["txId"] in all_wasabi:
            return "wasabi_base"
        if len(t["inputs"]) >= args.cj_min and len(t["outputs"]) >= args.cj_min:
            return "coinjoin_outro"
        return "comum"

    # --- 2. histórico pré-Wasabi + origem de cada entrada
    kept, stats = {}, Counter()
    discarded = defaultdict(set)
    origin = {}
    for a, h in wasabi_height.items():
        f = args.cache / f"{a}.json"
        if not f.exists():
            stats["enderecos_sem_cache"] += 1
            origin[a] = "origem_nao_encontrada"
            continue
        d = json.loads(f.read_text())
        stats["enderecos_truncados"] += bool(d.get("truncated"))
        best = None
        for t in d["transactions"]:
            bh = t.get("blockHeight")
            pre = (not t.get("mempool")) and bh is not None and bh < h
            k = kind_of(t)
            if pre and any(o.get("address") == a for o in t["outputs"]):
                if best is None or bh > best[0]:
                    best = (bh, k)
            tid = t["txId"]
            if tid in kept:
                continue
            if not pre:
                discarded["posterior_ou_mempool"].add(tid)
            elif k == "wasabi_base":
                discarded["wasabi"].add(tid)
            elif k == "coinjoin_outro":
                discarded["tipo_coinjoin"].add(tid)
            else:
                kept[tid] = t
        origin[a] = best[1] if best else "origem_nao_encontrada"
    for k, s in discarded.items():
        stats[f"tx_distintas_descartadas_{k}"] = len(s)
    stats["tx_pre_wasabi_usadas"] = len(kept)

    # --- 3. H1 + grafo 1
    uf = UF()
    g1 = nx.DiGraph()
    for a in wasabi_inputs:
        uf.find(a)
        g1.add_node(a, wasabi_input=True, kind="entrada_wasabi", origem=origin[a])
    for t in kept.values():
        ins = [i["address"] for i in t["inputs"] if i.get("address")]
        outs = [(o["address"], o["value"]) for o in t["outputs"] if o.get("address")]
        for a in ins[1:]:
            uf.union(ins[0], a)
        for a in ins:
            uf.find(a)
            if a not in g1:
                g1.add_node(a, wasabi_input=False, kind="outro", origem="")
        for b, v in outs:
            uf.find(b)
            if b not in g1:
                g1.add_node(b, wasabi_input=False, kind="outro", origem="")
            for a in set(ins):
                if g1.has_edge(a, b):
                    g1[a][b]["n_tx"] += 1
                    g1[a][b]["value"] += v
                else:
                    g1.add_edge(a, b, n_tx=1, value=v)

    # --- 4. clusters
    members = defaultdict(list)
    for a in g1.nodes:
        members[uf.find(a)].append(a)
    order = sorted(members.values(), key=lambda m: -len(m))
    cid = {}
    for k, m in enumerate(order):
        for a in m:
            cid[a] = f"C{k:05d}"
    for a in g1.nodes:
        g1.nodes[a]["cluster"] = cid[a]

    # --- 5. grafo 2
    g2 = nx.DiGraph()
    intra = Counter()
    for k, m in enumerate(order):
        nw = sum(a in wasabi_inputs for a in m)
        g2.add_node(f"C{k:05d}", size=len(m), n_wasabi_inputs=nw,
                    kind="com_entrada_wasabi" if nw else "outro")
    for a, b, e in g1.edges(data=True):
        ca, cb = cid[a], cid[b]
        if ca == cb:
            intra[ca] += e["n_tx"]
            continue
        if g2.has_edge(ca, cb):
            g2[ca][cb]["n_tx"] += e["n_tx"]
            g2[ca][cb]["value"] += e["value"]
        else:
            g2.add_edge(ca, cb, n_tx=e["n_tx"], value=e["value"])
    for c, n in intra.items():
        g2.nodes[c]["intra_payments"] = n

    # --- 6. financiadores que pagam >= 2 entradas Wasabi (antes x depois da H1)
    pay_b, pay_a = defaultdict(set), defaultdict(set)
    for a, b in g1.edges:
        if b in wasabi_inputs and a != b:
            pay_b[a].add(b)
            pay_a[cid[a]].add(b)
    grp_b = [s for s in pay_b.values() if len(s) >= 2]
    grp_a = [s for s in pay_a.values() if len(s) >= 2]
    ligadas_b = set().union(*grp_b) if grp_b else set()
    ligadas_a = set().union(*grp_a) if grp_a else set()

    # --- 7. exportação
    nx.write_graphml(g1, args.out / "graph_before.graphml")
    nx.write_graphml(g2, args.out / "graph_after_h1.graphml")
    iso1 = list(nx.isolates(g1))
    iso2 = list(nx.isolates(g2))
    g1c, g2c = g1.copy(), g2.copy()
    g1c.remove_nodes_from(iso1)
    g2c.remove_nodes_from(iso2)
    nx.write_graphml(g1c, args.out / "graph_before_connected.graphml")
    nx.write_graphml(g2c, args.out / "graph_after_h1_connected.graphml")
    draw_components(g1, g2, cid, args.top, args.out / "componentes.png")
    with open(args.out / "clusters.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["cluster", "size", "n_wasabi_inputs", "addresses"])
        for k, m in enumerate(order):
            w.writerow([f"C{k:05d}", len(m), sum(a in wasabi_inputs for a in m), " ".join(m)])

    sizes = [len(m) for m in order]
    summary = {
        "txs_wasabi_amostra": len(names),
        "enderecos_entrada_wasabi": len(wasabi_inputs),
        "origem_das_entradas_wasabi": dict(Counter(origin.values())),
        **stats,
        "grafo1_vertices": g1.number_of_nodes(), "grafo1_arestas": g1.number_of_edges(),
        "grafo1_isolados": len(iso1),
        "grafo2_vertices": g2.number_of_nodes(), "grafo2_arestas": g2.number_of_edges(),
        "grafo2_isolados": len(iso2),
        "reducao_vertices_pct": round(100 * (1 - g2.number_of_nodes() / max(1, g1.number_of_nodes())), 2),
        "clusters_com_mais_de_1_endereco": sum(s > 1 for s in sizes),
        "maior_cluster": sizes[0] if sizes else 0,
        "clusters_com_>1_entrada_wasabi": sum(1 for m in order if sum(a in wasabi_inputs for a in m) > 1),
        "pagamentos_intra_cluster": sum(intra.values()),
        "financiadores_que_pagam_2mais_entradas_ANTES_H1": len(grp_b),
        "financiadores_que_pagam_2mais_entradas_DEPOIS_H1": len(grp_a),
        "entradas_wasabi_ligadas_por_financiador_ANTES_H1": len(ligadas_b),
        "entradas_wasabi_ligadas_por_financiador_DEPOIS_H1": len(ligadas_a),
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    for k, v in summary.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
