#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把泰文 CMap 还原结果固化进 kb/THA/docs。
对每个 segment 应用 restore_segment,写回 content 并标记 cmap_restored=true。
这样下游(检索/embedding/LLM)自动使用干净的泰文。
"""
import os, json, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
from thai_restore import restore_segment

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "kb", "THA", "docs")

for fn in sorted(os.listdir(DOCS)):
    if not fn.endswith(".jsonl"):
        continue
    path = os.path.join(DOCS, fn)
    rows = [json.loads(l) for l in open(path, encoding="utf-8")]
    changed = 0
    for r in rows:
        r2 = restore_segment(r["content"])
        if r2 != r["content"]:
            r["content"] = r2
            r["cmap_restored"] = True
            changed += 1
        else:
            r.setdefault("cmap_restored", False)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[OK] {fn}: {len(rows)} segs, {changed} restored")
print("\nDone. 泰文知识库已使用干净文本。")
