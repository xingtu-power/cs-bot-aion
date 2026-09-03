#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性烘焙 FAQ 的 4 语言版本(zh/en/th/es),存 kb/<market>/faq/faq_localized.json。
结果:每条 FAQ 的 answer 有 zh/en/th/es 四版(原市场语言为原生;其余由 LLM 一次性生成后固化)。
运行时(客服)不再调 LLM —— 满足四种语言低延迟。
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from csapp.llm import DeepSeekLLM

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
llm = DeepSeekLLM()
LANGS = {"zh": "Simplified Chinese", "en": "English", "th": "Thai", "es": "Spanish"}


def build(market):
    path = os.path.join(ROOT, "kb", market, "faq", "faq.jsonl")
    items = [json.loads(l) for l in open(path, encoding="utf-8")]
    native = "en" if market == "AU" else "th"
    result = {}
    for it in items:
        result[it["id"]] = {native: it["answer"]}
    to_gen = [l for l in LANGS if l != native]
    for lang in to_gen:
        lines = [f"{it['id']}: {it['question']} => {result[it['id']][native]}" for it in items]
        prompt = (f"Translate each FAQ answer below into {LANGS[lang]}. Keep all numbers/units/facts. "
                  f"Output ONLY a JSON object mapping id to the translated ANSWER.\n\n" + "\n".join(lines))
        out = llm._run(prompt, timeout=180)
        if not out:
            print(f"  {market}/{lang}: FAILED"); continue
        start = out.find("{")
        try:
            data = json.loads(out[start:out.rfind("}") + 1])
        except Exception as e:
            print(f"  {market}/{lang}: parse err {e}"); continue
        got = 0
        for it in items:
            ans = data.get(it["id"])
            if ans:
                result[it["id"]][lang] = ans; got += 1
        print(f"  {market}/{lang}: {got}/{len(items)}")
    out_dir = os.path.join(ROOT, "kb", market, "faq")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "faq_localized.json"), "w", encoding="utf-8") as f:
        json.dump({"market": market, "langs": list(LANGS), "items": result}, f, ensure_ascii=False, indent=2)
    print("saved", market, "->", len(result), "items x", list(LANGS))


if __name__ == "__main__":
    for m in sys.argv[1:] or ["AU", "THA"]:
        build(m)
