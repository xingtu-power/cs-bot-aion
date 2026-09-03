#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
检索/验证 harness (Phase 0)
证明"知识可检索":配置精确查询、文档检索(含泰文音调归一化)、经销商距离排序、FAQ 匹配。
用法:python3 tools/search.py [market]
"""
import json, os, re, sys, unicodedata, math

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------- 归一化 ----------
def thai_norm(text: str) -> str:
    """NFKD + 去掉组合记号(声调/元音上标),并处理泰文手册提取时的 '-' 声调退化。"""
    t = unicodedata.normalize("NFKD", text)
    # 去除组合记号(如 ่ ้ ๊ ็ ิ ี ึ ื ุ ู —— 这些是 combining marks,NFKD 后变成 combining)
    t = "".join(c for c in t if not unicodedata.combining(c))
    # 泰文字符之间的 '-' 是声调退化残留,去掉
    t = re.sub(r"(?<=[\u0E00-\u0E7F])-(?=[\u0E00-\u0E7F])", "", t)
    t = re.sub(r"\s+", " ", t.lower().strip())
    return t


def norm_key(text: str) -> str:
    t = thai_norm(text)
    t = re.sub(r"[()（）【】\[\]{}.,_×*\-/\\|]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


# ---------- 配置精确查询(多语言别名映射,语言无关) ----------
# 概念 -> 各语言字段关键词(中/英/泰),命中任一即视为该概念
FIELD_ALIAS = {
    "range":       ["range", "km", "driving", "续航", "续航里程", "ระยะทาง", "วิ่ง", "ขับขี่"],
    "battery":     ["battery", "capacity", "kwh", "电池", "容量", "แบตเตอร"],
    "power":       ["power", "kw", "กำลัง", "功率"],
    "torque":      ["torque", "nm", "แรงบิด", "扭矩"],
    "seats":       ["seat", "seating", "ที่นั่ง", "座位"],
    "dimensions":  ["length", "width", "height", "dimension", "ความยาว", "ขนาด", "尺寸"],
    "ac-charging": ["ac charging", "ac", "ชาร์จ", "ac充电"],
    "dc-charging": ["dc fast", "dc charging", "dcชาร์จ", "dc充电", "dc快充"],
    "warranty":    ["warranty", "guarantee", "รับประกัน", "质保"],
    "service":     ["service", "maintenance", "serv", "บริการ", "保养", "บำรุง"],
}


def query_spec(market: str, concepts: list, variant=None):
    """concepts: 概念名列表(如 ['range'] 或 ['battery','capacity'])。语言无关。
    对每个概念展开别名,匹配字段归一化文本;返回匹配字段及其变体值。"""
    idx = json.load(open(os.path.join(ROOT, "kb", market, "spec", "spec_index.json"), encoding="utf-8"))
    best = None
    for concept in concepts:
        aliases = FIELD_ALIAS.get(concept, [concept])
        expanded = [a for a in set(aliases) | {concept} if a]
        scored = []
        for v in idx["fields"].values():
            nfn = norm_key(v["field"])
            if not nfn:
                continue
            hits = sum(1 for a in expanded if a in nfn)
            if hits > 0:
                # 命中数越高越具体;同分取字段名较短(更贴近具体项)
                scored.append((hits, len(v["field"]), v))
        if not scored:
            continue
        scored.sort(key=lambda x: (-x[0], x[1]))
        best = scored[0][2]
        break
    if not best:
        return None
    vals = best["variants"]
    if variant and variant in vals:
        return {"field": best["field"], "value": vals[variant],
                "category": best["category"], "unit": best["unit"]}
    return {"field": best["field"], "value": list(vals.values())[0],
            "variants": vals, "category": best["category"], "unit": best["unit"]}


# ---------- 文档检索(词/字符大gram + 泰文归一化) ----------
def tokenize(t: str):
    t = norm_key(t)
    lat = re.findall(r"[a-z0-9]+", t)
    # 泰文/中文无空格 -> 用字符大gram
    thai = re.findall(r"[\u0E00-\u0E7F]+", t)
    grams = []
    for seg in thai:
        if len(seg) <= 2:
            grams.append(seg)
        else:
            grams += [seg[i:i+2] for i in range(len(seg) - 1)]
    return set(lat) | set(grams)


def search_docs(market: str, query: str, doc_type=None, topk=5):
    q_tokens = tokenize(query)
    out_dir = os.path.join(ROOT, "kb", market, "docs")
    results = []
    fname = doc_type if doc_type else None
    for fn in os.listdir(out_dir):
        if not fn.endswith(".jsonl"):
            continue
        for line in open(os.path.join(out_dir, fn), encoding="utf-8"):
            r = json.loads(line)
            if fname and doc_type != r["doc_type"]:
                continue
            d_tokens = tokenize(r["content"])
            score = len(q_tokens & d_tokens)
            if score > 0:
                results.append((score, r["doc_type"], r["page_no"], r["chars"], r["content"][:160]))
    results.sort(key=lambda x: (x[0], -x[3]), reverse=True)
    return results[:topk]


# ---------- 经销商距离 ----------
def haversine(lat1, lng1, lat2, lng2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*R*math.asin(math.sqrt(a))


def nearest_dealers(market: str, lat, lng, topk=3):
    rows = [json.loads(l) for l in open(os.path.join(ROOT, "kb", market, "dealers", "dealers.jsonl"), encoding="utf-8")]
    ok = [r for r in rows if r["lat"] is not None and r["lng"] is not None]
    for r in ok:
        r["_dist"] = haversine(lat, lng, r["lat"], r["lng"])
    ok.sort(key=lambda r: r["_dist"])
    return ok[:topk]


# ---------- FAQ 匹配 ----------
def match_faq(market: str, query: str, topk=2):
    q_tokens = tokenize(query)
    items = [json.loads(l) for l in open(os.path.join(ROOT, "kb", market, "faq", "faq.jsonl"), encoding="utf-8")]
    scored = []
    for it in items:
        corpus = norm_key(it["question"] + " " + " ".join(it["keywords"]))
        c_tokens = tokenize(it["question"]) | set(it["keywords"])
        score = len(q_tokens & c_tokens)
        if score > 0:
            scored.append((score, it))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [it for _, it in scored[:topk]]


# ---------- 演示 ----------
DEMOS = {
    "AU": [
        ("spec", ["range"]), ("spec", ["battery", "capacity"]), ("spec", ["warranty"]),
        ("docs", "how to charge the car"),
        ("docs", "towing the vehicle"),
        ("dealer", -33.8688, 151.2093),  # Sydney
        ("faq", "what is the warranty"),
        ("faq", "how do i charge"),
    ],
    "THA": [
        ("spec", ["range"]), ("spec", ["battery"]),
        ("docs", "ชาร์จแบตเตอรี่"),
        ("docs", "คู่มือ เจ้าของ"),
        ("dealer", 13.7563, 100.5018),  # Bangkok
        ("faq", "รับประกัน"),
    ],
}


def run(market):
    print("="*76)
    print(f"MARKET = {market}")
    for d in DEMOS[market]:
        k = d[0]
        if k == "spec":
            r = query_spec(market, d[1])
            print(f"\n[spec] keywords={d[1]}")
            if r:
                if "variants" in r:
                    print(f"   field: {r['field']}  -> {r['variants']}")
                else:
                    print(f"   field: {r['field']}  -> {r['value']}  (unit={r['unit']})") if r["value"] else None
                    if not r["value"]:
                        print(f"   field: {r['field']} -> value={r['value']}")
            else:
                print("   (nil)")
        elif k == "docs":
            r = search_docs(market, d[1], topk=4)
            print(f"\n[docs] query='{d[1]}'")
            for s, dt, pg, ch, c in r:
                print(f"   score={s:2d} {dt:10s} p{pg:3d} [{ch}ch] {c}")
        elif k == "dealer":
            r = nearest_dealers(market, d[1], d[2], topk=3)
            print(f"\n[dealer] from ({d[1]},{d[2]})")
            for x in r:
                print(f"   {x['name']:28s} {x['city']:12s} {x['_dist']:6.1f}km  {x['phone']}")
        elif k == "faq":
            r = match_faq(market, d[1], topk=2)
            print(f"\n[faq] query='{d[1]}'")
            for x in r:
                print(f"   [{x['id']}] {x['question']}  (intent={x['intent']})")


if __name__ == "__main__":
    markets = sys.argv[1:] or ["AU", "THA"]
    for m in markets:
        run(m)
