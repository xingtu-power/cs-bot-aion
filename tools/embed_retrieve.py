#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
嵌入检索模块(Phase 0 验证 · fastembed/onnxruntime 版,无 torch)
用同一个多语言嵌入模型(sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2)
比较两种泰文检索策略:
  A 原语检索(direct): 泰文 query 嵌入,检索泰文 doc 嵌入。
  B 翻译兜底(translate): 泰文 query + doc 译为英文后嵌入检索(需 MT,见 translate())。
缓存嵌入到 tools/emb_cache,避免重复计算。
用法:
  python3 tools/embed_retrieve.py build        # 构建 A/B 索引
  python3 tools/embed_retrieve.py eval         # 在泰文评测集上对比
"""
import os, sys, json, hashlib
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools", "pylib"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HOME", os.path.join(ROOT, "tools", "hf_cache"))

EMB_CACHE = os.path.join(ROOT, "tools", "emb_cache")
MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
os.makedirs(EMB_CACHE, exist_ok=True)

_model = None


def model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(model_name=MODEL_NAME, cache_dir=EMB_CACHE)
    return _model


def embed(texts):
    return [np.array(x) for x in model().embed(texts)]


def load_segments(market):
    segs = []
    ddir = os.path.join(ROOT, "kb", market, "docs")
    for fn in sorted(os.listdir(ddir)):
        if fn.endswith(".jsonl"):
            for line in open(os.path.join(ddir, fn), encoding="utf-8"):
                segs.append(json.loads(line))
    segs.sort(key=lambda s: s["id"])
    return segs


def _cache(_name, segs):
    key = hashlib.sha1("|".join(s["id"] for s in segs).encode()).hexdigest()[:12]
    npz = os.path.join(EMB_CACHE, f"{_name}-{len(segs)}-{key}.npz")
    meta = os.path.join(EMB_CACHE, f"{_name}-{len(segs)}-{key}.ids")
    return npz, meta


def build_index(_name, market, strategy, force=False):
    """strategy: 'thai' (A 原始泰文 doc) / 'en' (B 英译 doc)"""
    segs = load_segments(market)
    npz, meta = _cache(_name, segs)
    if os.path.exists(npz) and not force:
        return np.load(npz)["emb"], json.load(open(meta)), segs
    if strategy == "en":
        texts = translate([s["content"] for s in segs])
    else:
        texts = [s["content"] for s in segs]
    # paraphrase-multilingual-MiniLM 是语义相似模型,无需 query:/passage: 前缀
    embs = np.array(embed([t for t in texts]))
    np.savez(npz, emb=embs)
    json.dump([s["id"] for s in segs], open(meta, "w"))
    print(f"  built {_name}-{strategy} index: {len(segs)} segs, dim={embs.shape[1]}")
    return embs, [s["id"] for s in segs], segs


# ---------- 翻译(策略 B 用) ----------
_translator = None


def translate(texts):
    """泰->英翻译。若可用 argos-translate 则用之;否则返回 None(策略 B 受限)。"""
    global _translator
    try:
        import argostranslate.package as pkg
        import argostranslate.translate as tr
    except Exception:
        print("  [translate] argos-translate 不可用,策略 B 受限")
        return None
    if _translator is None:
        pkg.update_package_index()
        avail = pkg.get_available_packages()
        cand = next((p for p in avail if p.from_code == "th" and p.to_code == "en"), None)
        if cand is None:
            return None
        pkg.install_from_path(cand.download())
        _translator = tr.get_translation_from_codes("th", "en")
    return [_translator.translate(t[:500]) for t in texts]


def encode_query(q):
    return embed([q])[0]


def retrieve(embs, qemb, topk=5):
    sims = embs @ qemb
    idx = np.argsort(-sims)[:topk]
    return [(int(i), float(sims[i])) for i in idx]


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "build"
    market = sys.argv[2] if len(sys.argv) > 2 else "THA"
    if mode == "build":
        ea, ia, _ = build_index("thai", market, "thai")
        print("done. A shape:", ea.shape)
    elif mode == "eval":
        ev = json.load(open(os.path.join(ROOT, "tools", "eval_thai.json"), encoding="utf-8"))
        ea, ia, _ = build_index("thai", market, "thai")
        eb = ib = None
        try:
            eb, ib, _ = build_index("en", market, "en")
        except Exception as e:
            print("  B index build skipped:", e)
        print("\n== 评测(A 泰语直查 vs B 泰译英后查)@top5 ==")
        stats = {"A": [], "B": []}
        for q in ev["queries"]:
            rel = set(q["relevant"])
            ra = retrieve(ea, encode_query(q["query"]), topk=5)
            hit_a = [ia[i] for i, _ in ra if ia[i] in rel]
            stats["A"].append(len(hit_a))
            line_a = f"[{q['id']}] {q['query']}\n   A(direct): {len(hit_a)}/5  top={[ia[i] for i,_ in ra[:3]]}"
            if eb is not None:
                qen = translate([q["query"]])[0]
                rb = retrieve(eb, encode_query(qen), topk=5)
                hit_b = [ib[i] for i, _ in rb if ib[i] in rel]
                stats["B"].append(len(hit_b))
                line_a += f"\n   B(trans) : {len(hit_b)}/5  top={[ib[i] for i,_ in rb[:3]]}"
            print(line_a)

        def recall(v):
            return round(sum(min(x, 5) for x in v) / (5 * len(v)), 3)
        print("\n== 汇总 ==")
        print("A 原语 recall@5:", recall(stats["A"]), "| 平均命中/查询:", round(sum(stats["A"]) / len(stats["A"]), 2))
        if stats["B"]:
            print("B 翻译 recall@5:", recall(stats["B"]), "| 平均命中/查询:", round(sum(stats["B"]) / len(stats["B"]), 2))


if __name__ == "__main__":
    main()
