"""语言检测(Phase 1 骨架 + 多语扩展)。

设计:语言跟随用户。脚本检测覆盖 th/zh;拉丁字母语言(英/马/印尼)用高区分度
功能词判别;无法确定时用 llm_detect 兜底(支持任意语言) → 其它语言走 LLM 本地化。
"""
import re
from . import config

# 英/马/印尼 判别用词(高区分度)
_EN_WORDS = ["what", "how", "is", "the", "and", "of", "to", "i", "you", "my", "car",
             "battery", "range", "warranty", "service", "charge", "dealer", "price",
             "spec", "help", "want", "need", "test", "drive", "where", "much", "can"]
# 马来/印尼 共享功能词(命中即属 马/印尼语系)
_MS_ID_WORDS = ["berapa", "bagaimana", "apakah", "adakah", "saya", "anda", "ini", "itu",
                "dan", "untuk", "dengan", "boleh", "tolong", "harga", "spesifikasi",
                "bateri", "cas", "caj", "kereta", "mobil", "kenderaan", "mesin",
                "sila", "mana", "apa", "kenapa", "mahu", "mau", "ingin", "guna", "cara"]
# 用于区分 马来 vs 印尼(弱启发,仅作倾向)
_MS_ONLY = ["kereta", "sila", "menggunakan", "adakah", "berapakah", "mahu"]
_ID_ONLY = ["mobil", "kendaraan", "cara", "mohon", "silahkan", "saya ingin", "kendaraan"]
# 西班牙语高频词(含去重音变体,便于无重音输入)
_ES_WORDS = ["cual", "como", "cuanto", "cuanta", "para", "con", "quiero", "necesito",
             "precio", "autonomia", "bateria", "cargar", "carga", "coche", "automovil",
             "prueba", "donde", "cuando", "hay", "mucho", "esta", "este", "una", "un",
             "concesionario", "mantenimiento", "garantia", "kilometros", "es", "el", "la"]


def _count(low, words):
    """整词计数(\\b 边界),避免短词子串误伤(如 est 不匹配 es)。"""
    n = 0
    for w in words:
        if re.search(r"\b" + re.escape(w) + r"\b", low):
            n += 1
    return n


def detect_language(text, market=None, hint=None, llm_detect=None):
    """返回语言代码(th/en/zh/ms/id/es 或其它)。

    - **跟随当前消息**:先从 text 检测;有明确信号(泰/中/英/马印/西词)则返回。
    - 仅当消息无语言信号(如纯数字/符号)才用 hint 保持上下文。
    - 未命中内置词表 → LLM 检测(支持任意语言) → 其它语言走 LLM 本地化。
    """
    if not text:
        return hint or "en"
    stripped = re.sub(r"[\d\s\W_]+", "", text)
    thai = len(re.findall(r"[\u0e00-\u0e7f]", stripped))
    cjk = len(re.findall(r"[\u4e00-\u9fff]", stripped))
    latin = len(re.findall(r"[a-zA-Z]", stripped))
    if thai > 0 and thai >= latin:
        return "th"
    if cjk > 0 and cjk >= latin:
        return "zh"
    if latin > 0:
        low = text.lower()
        en = _count(low, _EN_WORDS)
        msid = _count(low, _MS_ID_WORDS)
        if msid >= 2 and msid > en:
            msonly = _count(low, _MS_ONLY)
            idonly = _count(low, _ID_ONLY)
            if idonly > msonly:
                return "id"
            if msonly > idonly:
                return "ms"
            if llm_detect:
                return llm_detect(text) or "ms"
            return "ms"
        es = _count(low, _ES_WORDS)
        if es >= 2 and es > en and es > msid:
            return "es"
        if en >= 2:
            return "en"
        if llm_detect:
            return llm_detect(text) or (hint or "en")
        return hint or "en"
    return hint or "en"
