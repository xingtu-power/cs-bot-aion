#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用强多语言检索模型(intfloat/multilingual-e5-large)评估泰文文档检索。
对照 MiniLM 基线(recall@5≈0.129)。E5 需 query:/passage: 前缀。
索引缓存在 emb_cache/e5-<market>.npz(避免每次重算)。
"""
import os, sys, json
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "pylib"))
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HOME", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "hf_cache"))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "tools", "emb_cache")
MODEL = "intfloat/multilingual-e5-large"
os.makedirs(CACHE, exist_ok=True)
_model = None


def model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(model_name=MODEL, cache_dir=CACHE)
    return _model


def embed(texts, prefix=""):
    return [np.array(x) for x in model().embed([prefix + t for t in texts])]


def load_segs(market):
    segs = []
    for fn in sorted(os.listdir(os.path.join(ROOT, "kb", market, "docs"))):
        if fn.endswith(".jsonl"):
            for l in open(os.path.join(ROOT, "kb", market, "docs", fn), encoding="utf-8"):
                segs.append(json.loads(l))
    segs.sort(key=lambda s: s["id"])
    return segs


def build(market):
    segs = load_segs(market)
    npz = os.path.join(CACHE, f"e5-{market}.npz")
    ids = [s["id"] for s in segs]
    if os.path.exists(npz):
        print("use cached e5 index...")
        return np.load(npz)["emb"], ids
    print(f"embedding {len(segs)} segs with e5-large (this takes ~1-2 min)...")
    embs = np.array(embed([s["content"] for s in segs], "passage: "))
    np.savez(npz, emb=embs)
    return embs, ids


def main():
    market = sys.argv[1] if len(sys.argv) > 1 else "THA"
    ev = json.load(open(os.path.join(ROOT, "tools", "eval_thai.json"), encoding="utf-8"))
    embs, ids = build(market)
    print(f"index {len(ids)} x {embs.shape[1]}")
    tot = 0
    for q in ev["queries"]:
        rel = set(q["relevant"])
        qemb = embed([q["query"]], "query: ")[0]
        sims = embs @ qemb
        idx = np.argsort(-sims)[:5]
        hit = [ids[i] for i in idx if ids[i] in rel]
        tot += len(hit)
        print(f"  [{q['id']}] hit={len(hit)}/5")
    n = len(ev["queries"])
    print(f"\ne5-large 泰语直查 recall@5 = {tot/(5*n):.3f} | 平均命中/查询 {tot/n:.2f}")
    print("对照: MiniLM 泰语直查 recall@5 = 0.129")


if __name__ == "__main__":
    main()
