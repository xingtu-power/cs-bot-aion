"""市场路由(Phase 1 骨架 · 开放多市场)。

规则:
- 市场是开放的(任意市场码),不只 AU/THA。
- 用户**未指定**市场 → 按输入语言推断(market=auto,跟随语言变化)。
- 用户**明确指定**市场 → 该市场(market=manual),**不随语言切换**,除非再次明确切换。
- 只有 AU/THA 有专属知识库;其它市场回退 AU 英文库(知识可扩容)。
"""
from . import config

# 语言 → 市场(开放;默认 AU)
LANG2MKT = {"th": "THA", "ms": "MY", "id": "ID", "es": "ES", "fr": "FR",
            "zh": "CN", "en": "AU", "vi": "VN", "de": "DE", "pt": "PT",
            "it": "IT", "ru": "RU", "ja": "JP", "ko": "KR", "ar": "AE", "nl": "NL"}
DEFAULT_MKT = "AU"


def infer_market(language=None):
    return LANG2MKT.get((language or "").lower(), DEFAULT_MKT)


# 常见市场关键词(多语言:中/英/泰/西等),用于从消息中提取用户明确说的市场
MARKET_KEYWORDS = {
    "AU": ["australia", "澳洲", "澳大利亚", "ออสเตรเลีย"],
    "THA": ["thailand", "泰国", "ไทย", "thai"],
    "CN": ["china", "中国", "จีน"],
    "ES": ["spain", "西班牙", "españa", "españa"],
    "FR": ["france", "法国", "ฝรั่งเศส"],
    "MY": ["malaysia", "马来西亚", "มาเลเซีย"],
    "ID": ["indonesia", "印尼", "อินโดนีเซีย"],
    "IN": ["india", "印度", "อินเดีย", "भारत"],
    "US": ["usa", "united states", "america", "美国", "สหรัฐ"],
    "DE": ["germany", "德国", "เยอรมนี"],
    "VN": ["vietnam", "越南", "เวียดนาม"],
}


def extract_market(text):
    """从消息中提取用户明确指定的市场(如 '印度市场'→IN)。返回 (market, kw) 或 (None, None)。"""
    if not text:
        return None, None
    low = text.lower()
    for mkt, kws in MARKET_KEYWORDS.items():
        for kw in kws:
            if kw and kw in low:
                return mkt.upper(), kw
    return None, None


def route_market(language=None, location=None, explicit=None,
                 session_market=None, session_market_source=None):
    """返回 (market, source)。

    source: 'manual'(用户明确指定,粘性) / 'auto'(跟随语言)。
    """
    if explicit and str(explicit).strip():
        return str(explicit).strip().upper(), "manual"
    if session_market:
        if session_market_source == "manual":
            return session_market, "manual"          # 手动市场:粘性,不随语言变
        return infer_market(language), "auto"        # 自动市场:跟随当前语言
    return infer_market(language), "auto"


def market_kb(market):
    """该市场用哪个 kb 目录。仅 AU/THA 有专属库,其余回退 AU 英文库。"""
    return "THA" if str(market).upper() == "THA" else "AU"


def language_for_market(market):
    return "th" if str(market).upper() == "THA" else "en"
