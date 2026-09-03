#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
经销商表 ETL (Phase 0, ETL C)
读取 manifest 中 kind=xlsx & doc_type=dealer 的源,输出:
  kb/{market}/dealers/dealers.jsonl   每行一家店(标准化字段)
  kb/{market}/dealers/manifest.json   元数据
处理:
  - 澳表:剔除空行;经纬度优先从 Google Maps 链接解析(文本列有错位),兜底解析文本;
          电话归一化;店铺类型/州/市已是英文。
  - 泰表:直接映射,电话归一化(去 `-`/空格)。
"""
import json, os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "_lib"))
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "kb/raw/manifest/sources.json")


def norm_phone(p) -> str:
    if p is None or (isinstance(p, float) and pd.isna(p)):
        return ""
    s = re.sub(r"[^\d]", "", str(p))
    return s


def parse_latlng_text(t) -> tuple:
    """从 'Latitude: x, Longitude: y' 文本解析 (lat,lng)。"""
    if t is None or (isinstance(t, float) and pd.isna(t)):
        return (None, None)
    s = str(t)
    lat = re.search(r"[Ll]atitude[：:]\s*(-?[\d.]+)", s)
    lng = re.search(r"[Ll]ongitude[：:]\s*(-?[\d.]+)", s)
    if lat and lng:
        return (float(lat.group(1)), float(lng.group(1)))
    return (None, None)


def parse_maps_link(u) -> tuple:
    """从 Google Maps 链接 @lat,lng 解析。"""
    m = re.search(r"@(-?[\d.]+),(-?[\d.]+)", str(u or ""))
    if m:
        return (float(m.group(1)), float(m.group(2)))
    return (None, None)


def main():
    manifest = json.load(open(MANIFEST, encoding="utf-8"))
    srcs = [s for s in manifest["sources"] if s["kind"] == "xlsx" and s["doc_type"] == "dealer"]
    for s in srcs:
        src = os.path.join(ROOT, s["source_file"])
        df = pd.read_excel(src, sheet_name=0, header=0, engine="openpyxl")
        # 找非空主键列(店名/名称)
        if s["market"] == "AU":
            rows = build_au(df, s)
        else:
            rows = build_tha(df, s)
        out_dir = os.path.join(ROOT, "kb", s["market"], "dealers")
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "dealers.jsonl"), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump({"market": s["market"], "version": s["version"],
                       "effective_date": s["effective_date"], "source": s["source_file"],
                       "count": len(rows),
                       "avg_lat": round(sum(r["lat"] for r in rows if r["lat"]) / len([r for r in rows if r["lat"]]), 5) if any(r["lat"] for r in rows) else None,
                       "doc_name": s["doc_name"]}, f, ensure_ascii=False, indent=2)
        with_latlng = sum(1 for r in rows if r["lat"] is not None)
        print(f"[OK] {s['market']}/dealers ver={s['version']}: rows={len(rows)} with_latlng={with_latlng}")


def build_au(df, s):
    # 首列=州,二=市,三=店名,四=地址,五=类型,六=经纬度文本,七=maps链接,八=电话,九=test driving
    df = df.dropna(subset=[df.columns[0], df.columns[2]])  # 剔除州或店名为空的行
    rows = []
    for _, r in df.iterrows():
        lat = lng = None
        lat, lng = parse_maps_link(r.iloc[6])          # 优先取 maps 链接(准确)
        if lat is None:
            lat, lng = parse_latlng_text(r.iloc[5])    # 兜底取经纬度文本
        name = str(r.iloc[2]).strip() if not pd.isna(r.iloc[2]) else ""
        if not name:
            continue
        rows.append({
            "market": "AU",
            "name": name,
            "state": str(r.iloc[0]).strip() if not pd.isna(r.iloc[0]) else "",
            "city": str(r.iloc[1]).strip() if not pd.isna(r.iloc[1]) else "",
            "address": str(r.iloc[3]).strip() if not pd.isna(r.iloc[3]) else "",
            "type": str(r.iloc[4]).strip() if not pd.isna(r.iloc[4]) else "",
            "phone": norm_phone(r.iloc[7]),
            "lat": lat,
            "lng": lng,
            "maps_link": str(r.iloc[6]).strip() if not pd.isna(r.iloc[6]) else "",
            "test_driving": "y" if str(r.iloc[8]).strip().upper() == "Y" else "",
            "source_ver": s["version"],
        })
    return rows


def build_tha(df, s):
    rows = []
    for _, r in df.iterrows():
        name = str(r["network_name"]).strip() if not pd.isna(r["network_name"]) else ""
        if not name:
            continue
        lat = float(r["latitude"]) if not pd.isna(r["latitude"]) else None
        lng = float(r["longitude"]) if not pd.isna(r["longitude"]) else None
        rows.append({
            "market": "THA",
            "name": name,
            "state": str(r["region_code"]).strip() if not pd.isna(r["region_code"]) else "",
            "city": "",
            "address": str(r["address"]).strip() if not pd.isna(r["address"]) else "",
            "type": "Standard",   # 泰表未标类型,默认
            "phone": norm_phone(r["contact_phone"]),
            "lat": lat,
            "lng": lng,
            "contact_name": str(r["contact_name"]).strip() if not pd.isna(r["contact_name"]) else "",
            "network_code": str(r["network_code"]).strip() if not pd.isna(r["network_code"]) else "",
            "hours": r["access_time"] if not pd.isna(r["access_time"]) else "",
            "source_ver": s["version"],
        })
    return rows


if __name__ == "__main__":
    main()
