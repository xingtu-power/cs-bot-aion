#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多语言意图准确率评测:走真实管道(pipeline.chat,每次新会话),取路由后的 intent。"""
import json, os, sys
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from csapp import pipeline

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
cases = json.load(open(os.path.join(ROOT, "tools", "eval_intent_ml.json"), encoding="utf-8"))["cases"]


def run():
    by_lang = defaultdict(lambda: {"ok": 0, "total": 0, "miss": []})
    for c in cases:
        r = pipeline.chat(session_id=None, message=c["message"])
        pred = r["intent"] or "other"
        truth = c["intent"]
        ok = pred == truth
        L = by_lang[c["lang"]]
        L["total"] += 1
        if ok:
            L["ok"] += 1
        else:
            L["miss"].append((c["message"], truth, pred))
    print("=== 多语言意图准确率 ===")
    for lang in sorted(by_lang):
        L = by_lang[lang]
        acc = L["ok"] / max(1, L["total"])
        print(f"{lang}: {L['ok']}/{L['total']} = {acc:.0%}")
        for msg, truth, pred in L["miss"]:
            print(f"    MISS [{msg[:28]}] truth={truth} pred={pred}")
    tot_ok = sum(v["ok"] for v in by_lang.values()); tot = sum(v["total"] for v in by_lang.values())
    print(f"\nALL: {tot_ok}/{tot} = {tot_ok/tot:.0%}")


if __name__ == "__main__":
    run()
