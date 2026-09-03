#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置表 ETL (Phase 0, ETL B)
读取 manifest 中 kind=xlsx & doc_type=spec 的源,输出:
  kb/{market}/spec/specs.jsonl   每行一条 (model, variant, category, field, value, unit)
  kb/{market}/spec/spec_index.json 依据 field 精确查找的嵌套索引
  kb/{market}/spec/manifest.json 该市场配置元数据
支持中/英/泰字段原样保留;额外抽取单位;生成归一化搜索键。
"""
import json, os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")) if False else None
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "_lib"))
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "kb/raw/manifest/sources.json")


def norm_key(text: str) -> str:
    """归一化搜索键:小写、去空白/标点/单位括号,用于粗匹配。"""
    t = text.lower()
    t = re.sub(r"[()（）【】\[\]{}.,_×*\-/\\|]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def extract_unit(field: str) -> str:
    m = re.search(r"[（(]([^）)]*(?:มิลลิเมตร|mm|km|kW|Nm|kWh|V|kg|cm|L|s|%|ดัน)[^）)]*)[）)]", field, re.I)
    return m.group(1) if m else ""


def main():
    manifest = json.load(open(MANIFEST, encoding="utf-8"))
    specs = [s for s in manifest["sources"] if s["kind"] == "xlsx" and s["doc_type"] == "spec"]

    for s in specs:
        src = os.path.join(ROOT, s["source_file"])
        df = pd.read_excel(src, sheet_name=0, header=0, engine="openpyxl")
        # 第一列 category,第二列 field,其余为变体
        cat_col, field_col = df.columns[0], df.columns[1]
        variant_cols = list(df.columns[2:])
        # 车型固定为 AION UT(当前项目唯一车型);doc_name 含则取,否则默认
        model = "AION UT"
        m = re.search(r"AION\s*UT", s["doc_name"])
        if m:
            model = m.group(0)
        # 泰文配置表 variant 列名含车型前缀(如 AION UT 500 Premium) -> 提取变体名
        rows = []
        for _, r in df.iterrows():
            if pd.isna(r[field_col]):
                continue
            field = str(r[field_col]).strip()
            if not field:
                continue
            unit = extract_unit(field)
            for vc in variant_cols:
                val = r[vc]
                val = "" if pd.isna(val) else str(val).strip()
                variant = str(vc).strip()
                # 去掉列车名中的车型前缀,只留变体名
                variant = re.sub(r"^AION\s*UT\s*", "", variant, flags=re.I).strip()
                rows.append({
                    "model": model,
                    "variant": variant,
                    "category": str(r[cat_col]).strip() if not pd.isna(r[cat_col]) else "",
                    "field": field,
                    "field_norm": norm_key(field),
                    "value": val,
                    "unit": unit,
                })
        out_dir = os.path.join(ROOT, "kb", s["market"], "spec")
        os.makedirs(out_dir, exist_ok=True)
        # specs.jsonl
        with open(os.path.join(out_dir, "specs.jsonl"), "w", encoding="utf-8") as f:
            for x in rows:
                f.write(json.dumps(x, ensure_ascii=False) + "\n")
        # index: field_norm -> {category, unit, values:{variant: value}}
        index = {}
        for x in rows:
            k = x["field_norm"]
            index.setdefault(k, {"category": x["category"], "unit": x["unit"],
                                 "field": x["field"], "model": x["model"],
                                 "variants": {}})
            index[k]["variants"][x["variant"]] = x["value"]
        with open(os.path.join(out_dir, "spec_index.json"), "w", encoding="utf-8") as f:
            json.dump({"market": s["market"], "model": model, "version": s["version"],
                       "fields": index}, f, ensure_ascii=False, indent=2)
        with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump({"market": s["market"], "model": model, "version": s["version"],
                       "effective_date": s["effective_date"], "source": s["source_file"],
                       "variants": list(dict.fromkeys(x["variant"] for x in rows)),
                       "category_count": len(set(x["category"] for x in rows)),
                       "rows": len(rows),
                       "doc_name": s["doc_name"]}, f, ensure_ascii=False, indent=2)
        print(f"[OK] {s['market']}/spec ver={s['version']}: rows={len(rows)} "
              f"variants={list(dict.fromkeys(x['variant'] for x in rows))} "
              f"categories={len(set(x['category'] for x in rows))}")
        # 打印几个示例
        print("   sample:")
        for x in rows[:3]:
            print("     ", x["field"], "=", {k: v for k, v in x.items()})


if __name__ == "__main__":
    main()
