#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""意图识别准确率评测:规则基线 vs LLM 语义确认。"""
import json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from csapp import intent as IM
from csapp.llm import DeepSeekLLM, RuleLLM

cases = json.load(open(ROOT + "/tools/eval_intent.json", encoding="utf-8"))["cases"]


def run(rule=True):
    llm = RuleLLM() if rule else DeepSeekLLM()
    ok = 0
    rows = []
    for c in cases:
        pred, conf, _ = IM.recognize(c["message"], llm_confirm=llm.confirm_intent if not rule else None)
        hit = pred == c["intent"]
        ok += hit
        rows.append((c["id"], c["intent"], pred, hit))
    return ok, len(cases), rows


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "rule"
    ok, n, rows = run(rule=(mode == "rule"))
    print(f"{mode}: 准确率 = {ok}/{n} = {ok/n:.1%}")
    for rid, truth, pred, hit in rows:
        if not hit:
            print(f"   MISS {rid}: truth={truth} pred={pred}")
