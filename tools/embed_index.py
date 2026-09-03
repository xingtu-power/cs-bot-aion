#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线构建向量语义索引(e5-large),存 kb/<market>/vectors/。
对象:文档段 + FAQ(主语言问题+答案)。规格/经销商保持结构化精确查询。
用法: python3 tools/embed_index.py <market> ...   (如 THA)
"""
import os, sys, json
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "pylib"))
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HOME", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "hf_cache"))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "tools", "emb_cache")
MODEL = "intfloat/multilingual-e5-large"


def embed_texts(model, texts):
    return np.array([np.array(x) for x in model.embed(["passage: " + t for t in texts])])


def build(market):
    model = None
    # 收集对象:文档段 + FAQ(主语言)
    texts, ids = [], []
    ddir = os.path.join(ROOT, "kb", market, "docs")
    for fn in sorted(os.listdir(ddir)):
        if fn.endswith(".jsonl"):
            for l in open(os.path.join(ddir, fn), encoding="utf-8"):
                r = json.loads(l)
                texts.append(r["content"]); ids.append(r["id"])
    faq_path = os.path.join(ROOT, "kb", market, "faq", "faq.jsonl")
    for l in open(faq_path, encoding="utf-8"):
        r = json.loads(l)
        texts.append(f"Q: {r['question']}\nA: {r['answer']}"); ids.append("faq:" + r["id"])
    # 去重
    seen = set(); uni = []
    for t, i in zip(texts, ids):
        if i in seen: continue
        seen.add(i); uni.append((t, i))
    texts = [t for t, _ in uni]; ids = [i for _, i in uni]
    print(f"[{market}] building index for {len(texts)} chunks with e5-large ...")
    from fastembed import TextEmbedding
    model = TextEmbedding(model_name=MODEL, cache_dir=CACHE)
    embs = embed_texts(model, texts)
    out_dir = os.path.join(ROOT, "kb", market, "vectors")
    os.makedirs(out_dir, exist_ok=True)
    np.savez(os.path.join(out_dir, "vectors.npz"), emb=embs)
    json.dump(ids, open(os.path.join(out_dir, "ids.json"), "w"))
    json.dump(texts, open(os.path.join(out_dir, "texts.json"), "w"), ensure_ascii=False)
    print(f"[{market}] saved {len(texts)} chunks x {embs.shape[1]} -> {out_dir}")


if __name__ == "__main__":
    for m in sys.argv[1:] or ["THA", "AU"]:
        build(m)
