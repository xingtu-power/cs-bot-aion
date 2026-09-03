#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PDF 提取器 (Phase 0, ETL A)
读取 kb/raw/manifest/sources.json 中 kind=pdf 的源,逐页提取文字,
写入 kb/{market}/docs/<version>.jsonl(每行一个 segment,带元数据)。
跳过近空白页(封面/空白)。
仅提取文字层,不 OCR —— 已确认全部 PDF 为文本型。
"""
import json, os, re, sys, hashlib
import fitz  # PyMuPDF

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "kb/raw/manifest/sources.json")

MIN_PAGE_CHARS = 40  # 低于此阈值视为空白/封面页,跳过


def clean_text(t: str) -> str:
    t = t.replace("\u00a0", " ").replace("\u3000", " ")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def seg_id(market, doc_type, version, page):
    return f"{market}-{doc_type}-{version}-p{page:04d}".lower()


def main():
    manifest = json.load(open(MANIFEST, encoding="utf-8"))
    docs = [s for s in manifest["sources"] if s["kind"] == "pdf"]
    summary = []
    for s in docs:
        src = os.path.join(ROOT, s["source_file"])
        if not os.path.exists(src):
            print(f"[SKIP] missing: {src}")
            continue
        doc = fitz.open(src)
        n = doc.page_count
        rows = []
        for i in range(n):
            raw = doc[i].get_text()
            text = clean_text(raw)
            if len(text) < MIN_PAGE_CHARS:
                continue
            rows.append({
                "id": seg_id(s["market"], s["doc_type"], s["version"], i),
                "market": s["market"],
                "doc_name": s["doc_name"],
                "doc_type": s["doc_type"],
                "language": s["language"],
                "version": s["version"],
                "effective_date": s["effective_date"],
                "source": s["source_file"],
                "page": i,  # 0-based
                "page_no": i + 1,  # 1-based (给用户看的页码)
                "content": text,
                "chars": len(text),
            })
        doc.close()
        out_dir = os.path.join(ROOT, "kb", s["market"], "docs")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{s['version']}.jsonl")
        with open(out_path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        summary.append({
            "doc_type": s["doc_type"], "market": s["market"],
            "version": s["version"], "pages_total": n, "segments": len(rows),
            "chars": sum(r["chars"] for r in rows), "out": out_path,
        })
        print(f"[OK] {s['market']}/{s['doc_type']:10s} ver={s['version']}: "
              f"pages={n} segments={len(rows)} chars={sum(r['chars'] for r in rows)}")

    # write summary
    sum_path = os.path.join(ROOT, "kb/raw/manifest/pdf_extract_summary.json")
    json.dump(summary, open(sum_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\nSaved extract summary -> {sum_path}")
    print(f"Total doc sets: {len(summary)}, total segments: {sum(x['segments'] for x in summary)}")


if __name__ == "__main__":
    main()
