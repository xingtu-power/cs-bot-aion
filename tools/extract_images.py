#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PDF → 页内真实插图 提取器(知识库图片引用/展示用)

只抽取**页内的真实插图**(渲染图区域),而不是整页截图。
- 用 PyMuPDF get_image_info(xrefs=True) 拿每张内嵌图片的放置矩形(bbox)。
- 只有 bbox 尺寸达到 FIG_MIN 的才算"真插图"(过滤小图标/logo/分隔线)。
- 裁剪后存 kb/{market}/images/<version>_p<page_no>__f<idx>.jpg(JPEG Q90, 体积小、效果几乎无损)。

用法: python tools/extract_images.py [dpi] [过滤子串...]
   dpi 默认 150; 过滤如 "AU/owner" 只抽 AU 车主手册。
依赖: fitz (PyMuPDF)。若未装: pip install --target tools/pylib pymupdf
注: 会先清空 kb/{market}/images/ 再重抽(避免旧的整页截图残留)。
"""
import glob, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools", "pylib"))  # 让 import fitz 在本工作区可用
import fitz

MANIFEST = os.path.join(ROOT, "kb/raw/manifest/sources.json")
FIG_DPI = 150
FIG_MIN_W = 100   # bbox 宽度(点)下限,过滤图标/装饰
FIG_MIN_H = 80    # bbox 高度(点)下限

# 非内容页(目录/索引/前言/封面):不做插图候选(其内容页若有真插图也会被尺寸过滤)
_NAV_MARKERS = ("index", "contents", "foreword", "preface", "how to read this manual",
                "notices to users", "arrange them in the order", "i-n-d-e-x",
                "目录", "索引", "前言", "序言")


def is_nav(text: str) -> bool:
    t = (text or "").lower()
    if any(m in t for m in _NAV_MARKERS):
        return True
    return len(re.findall(r"\.{3,}\s*\d+", t)) >= 3


def extract_figures(page, page_no: int):
    """取某页的真实插图:(文件名后缀, pixmap) 列表;按放置位置排序。"""
    out = []
    try:
        infos = page.get_image_info(xrefs=True)
    except Exception:
        return out
    # 只保留唯一的放置矩形(同一 xref 可能多处放置)
    seen = set()
    for info in infos:
        bbox = info.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        rect = fitz.Rect(*bbox)
        key = (round(rect.x0), round(rect.y0), round(rect.x1), round(rect.y1))
        if key in seen:
            continue
        seen.add(key)
        w, h = rect.width, rect.height
        if w < FIG_MIN_W or h < FIG_MIN_H:
            continue  # 小图标/logo/分隔线
        try:
            pix = page.get_pixmap(clip=rect, dpi=FIG_DPI)
        except Exception:
            continue
        out.append((f"__f{len(out)}.jpg", pix))
    return out


def main():
    args = [a for a in sys.argv[1:] if a]
    dpi = FIG_DPI
    if args and args[0].isdigit():
        dpi = int(args[0]); args = args[1:]
    only = set(args)

    manifest = json.load(open(MANIFEST, encoding="utf-8"))
    docs = [s for s in manifest["sources"] if s["kind"] == "pdf"]
    total = 0
    cleared = set()
    for s in docs:
        tag = f"{s['market']}/{s['version']}"
        if only and not any(o in tag for o in only):
            continue
        src = os.path.join(ROOT, s["source_file"])
        if not os.path.exists(src):
            print(f"[SKIP] missing: {src}")
            continue
        images_dir = os.path.join(ROOT, "kb", s["market"], "images")
        os.makedirs(images_dir, exist_ok=True)
        # 每市场只清空一次,避免后处理的来源覆盖前一个(jpg + 旧 png 都清)
        if s["market"] not in cleared:
            for old in glob.glob(os.path.join(images_dir, "*.*")):
                try:
                    os.remove(old)
                except Exception:
                    pass
            cleared.add(s["market"])

        doc = fitz.open(src)
        n_fig = 0
        n_pages_with_fig = 0
        for i in range(doc.page_count):
            page = doc[i]
            page_no = i + 1  # 1-based,与引用 page_no 一致
            text = " ".join(page.get_text().split())
            if len(text) < 40 or is_nav(text):
                continue
            figs = extract_figures(page, page_no)
            if not figs:
                continue
            n_pages_with_fig += 1
            for suffix, pix in figs:
                out = os.path.join(images_dir, f"{s['version']}_p{page_no}{suffix}")
                pix.save(out, jpg_quality=90)
                n_fig += 1
        doc.close()
        print(f"[OK] {tag}: pages_with_figures={n_pages_with_fig} figures={n_fig} -> {images_dir}", flush=True)
        total += n_fig
    print("TOTAL figures:", total, flush=True)


if __name__ == "__main__":
    main()
