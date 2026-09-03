#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
泰文 CMap 还原层(修复车主手册内嵌字体 AngsanaNew 的错误码位映射)。

背景:泰文车主手册把若干泰文组合符号提取成了 ASCII 字符(如 คู่มือ->คู-มือ,
ชาร์จ->ชาร7จ, เปิด->เปKด)。已由干净语料(救援/快速/配置表)确认每个映射。
本模块只替换「两边都是泰文字符」的 ASCII 符号(避免误伤真实数字/页码),
并保留低置信字符为可选。

用法:
  import sys; sys.path.insert(0,'tools')
  from thai_restore import restore_text, restore_segment
"""

import re

# 高置信映射(干净语料确证):ASCII 符号 -> 正确泰文组合符号
CONFIRMED = {
    "-": "\u0e48",   # ่ MAI EK       (คู่มือ, อย่าง, จะ)
    "7": "\u0e4c",   # ์ THANTHAKHAT  (ชาร์จ, ฟังก์ชั่น)
    "K": "\u0e34",   # ิ SARA I       (เปิด, ปิด)
    "<": "\u0e31",   # ั MAI HAN AKAT (ฟังก์ชั่น)
    "T": "\u0e34",   # ิ SARA I       (เปิด/ปิด 另一 glyph)
    "g": "\u0e49",   # ้ MAI THO      (ไฟฟ้า)
    "P": "\u0e49",   # ้ MAI THO      (ป้องกัน)
    "f": "\u0e48",   # ่ MAI EK       (ปุ่ม)
    "#": "\u0e49",   # ้ MAI THO      (ห้าม, ต้อง, ได้)
    "\x18": "\u0e48",# ่ MAI EK       (ส่วน, คู่มือ, รุ่น)
    "\x81": "\u0e48",# ่ MAI EK       (ปุ่ม)
    "\x11": "\u0e4c",# ์ THANTHAKHAT  (ชาร์จ, รถยนต์, อุปกรณ์)
}

# 低置信/可能涉及真实数字与歧义:默认不启用,需显式开启
LOW_CONFIDENCE = {
    "B": "\u0e35",  # ี SARA II       (เปียก? 存疑)
    "Y": "\u0e34",  # ิ (歧义:在某些词为 ั)
    "o": "\u0e4a",  # ๊ MAI TRI       (โต๊ะ)
    "4": "\u0e48",  # ่ (存在真实数字 4 风险)
    "2": "\u0e49",  # ้ (存在真实数字 2 风险)
}

# 泰文字符区间
_THAI = r"[\u0e00-\u0e7f]"


def _is_thai(ch):
    return "\u0e00" <= ch <= "\u0e7f"


def restore_segment(text, include_low=False, conservative=True):
    """还原一段泰文。

    conservative=True 时,符号必须夹在两个泰文字符之间(默认,避免伤及真实数字/页码)。
    """
    if not text:
        return text
    keep = dict(CONFIRMED)
    if include_low:
        keep.update(LOW_CONFIDENCE)
    chars = list(text)
    n = len(chars)
    out = []
    for i, ch in enumerate(chars):
        # 仅当 ch 是被替换符号 且前后都是泰文字符
        if ch in keep:
            prev = chars[i - 1] if i > 0 else ""
            nxt = chars[i + 1] if i + 1 < n else ""
            if (not conservative) or (_is_thai(prev) and _is_thai(nxt)):
                out.append(keep[ch])
                continue
        out.append(ch)
    return "".join(out)


def restore_text(text, include_low=False):
    return restore_segment(text, include_low=include_low)
