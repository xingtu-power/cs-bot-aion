"""意图识别(Phase 1 骨架)。

二段式:①关键词快筛(语言无关,多语言关键词表) ②LLM 语义确认(可选,可插拔)。
无 LLM 时以关键词置信度作为候选,低于阈值进入澄清。
"""
import re, unicodedata
from . import config

# 意图清单(与设计 §3.1 一致)
INTENTS = ["product-inquiry", "dealer-lookup", "usage-guide",
           "emergency", "after-sales", "other"]

# 延续/确认类标签:LLM 视为"继续当前话题",不应被归一成 other
CONTINUATION_LABELS = ("accept_offer_continue", "agree", "confirm", "acknowledge",
                       "customer_acknowledgement", "continue", "yes", "ack", "好的", "同意")

def is_continuation(label):
    s = (label or "").strip().lower().replace(" ", "_")
    return s in CONTINUATION_LABELS

# LLM 返回意图 → 规范意图 的别名(LLM 可能用变体/中文)
_INTENT_ALIAS = {
    "product": "product-inquiry", "product-inquiry": "product-inquiry", "product_inquiry": "product-inquiry",
    "spec": "product-inquiry", "spec_query": "product-inquiry", "产品咨询": "product-inquiry", "产品": "product-inquiry",
    "dealer": "dealer-lookup", "dealer-lookup": "dealer-lookup", "dealer_lookup": "dealer-lookup",
    "经销商": "dealer-lookup", "dealership": "dealer-lookup",
    "usage": "usage-guide", "usage-guide": "usage-guide", "usage_guide": "usage-guide",
    "how-to": "usage-guide", "how_to": "usage-guide", "使用": "usage-guide", "使用指南": "usage-guide",
    "emergency": "emergency", "rescue": "emergency", "紧急": "emergency", "故障": "emergency",
    "after-sales": "after-sales", "after_sales": "after-sales", "service": "after-sales",
    "warranty": "after-sales", "售后": "after-sales", "维修": "after-sales",
    "other": "other", "chat": "other", "greeting": "other", "闲聊": "other", "打招呼": "other",
    "none": "other", "": "other",
}


def normalize_intent(i):
    """把 LLM 返回的意图(可能是自造标签或其它语言)规整成 6 个标准 ID;子串匹配更鲁棒。"""
    if not i:
        return "other"
    s = str(i).strip().lower()
    direct = _INTENT_ALIAS.get(s) or _INTENT_ALIAS.get(s.replace(" ", "_"))
    if direct:
        return direct
    if any(k in s for k in ["dealer", "经销商", "dealership", "showroom", "contact", "location",
                            "address", "city", "nearest", "find", "postal", "试驾", "附近",
                            "booking", "appointment", "schedule", "reservation", "visit", "预约", "到店"]):
        return "dealer-lookup"
    if any(k in s for k in ["warran", "service", "after", "售后", "保修", "质保", "保养", "维修",
                            "maintenance", "repair", "part"]):
        return "after-sales"
    if any(k in s for k in ["emerg", "rescue", "breakdown", "故障", "无法启动", "紧急", "事故",
                            "arranca", "won't start", "cannot start", "启动不了", "打不着"]):
        return "emergency"
    if any(k in s for k in ["spec", "range", "product", "model", "trim", "config", "intro",
                            "introduction", "equip", "autonom", "battery", "power", "seat",
                            "price", "cost", "pricing", "finance", "payment", "installment",
                            "delivery", "stock", "availability", "colour", "color", "feature",
                            "配置", "续航", "车型", "型号", "规格", "版本", "价格", "多少钱", "颜色"]):
        return "product-inquiry"
    if any(k in s for k in ["usage", "how to", "charge", "使用", "充电", "guide", "cargar", "ชาร์จ"]):
        return "usage-guide"
    return "other"

# 关键词表:意图 -> 多语言关键词。命中按语言无关处理(中/英/泰)。
INTENT_KEYWORDS = {
    "product-inquiry": ["spec", "range", "price", "cost", "how much", "colour", "color", "battery",
                        "version", "trim", "seat",
                        "续航", "配置", "价格", "车型", "电池", "座位", "多少钱", "颜色", "多少钱",
                        "สเปก", "ราคา", "แบตเตอร", "รุ่น", "วิ่ง", "ไกล", "ที่นั่ง", "ขนาด",
                        "autonomia", "precio", "bateria", "especificacion", "equipamiento", "version",
                        "cuanto", "cuesta", "color", "cuantos"],
    "dealer-lookup": ["dealer", "where to buy", "showroom", "test drive", "address", "nearby",
                      "经销商", "哪里买", "试驾", "地址", "最近",
                      "โชว์รูม", "ตัวแทน", "ทดลองขับ", "ที่ไหน", "ซื้อ",
                      "concesionario", "distribuidor", "dónde", "prueba", "tienda", "probar", "cerca"],
    "usage-guide": ["how to", "charge", "start", "charge port", "charging",
                    "怎么", "如何", "充电", "启动", "使用",
                    "ชาร์จ", "สตาร", "ใช้", "วิธี",
                    "como", "cargar", "cargo", "carga", "usar", "encender", "funciona", "paso"],
    "emergency": ["breakdown", "won't start", "emergency", "rescue", "accident",
                  "not working", "dead", "help",
                  "抛锚", "无法启动", "救援", "故障", "事故", "紧急",
                  "ฉุกเฉิน", "รถเสีย", "ช่วยเหลือ", "สตารทไม่ติด", "อุบัติเหต",
                  "no arranca", "averia", "emergencia", "auxilio", "accidente", "atascado", "ayuda"],
    "after-sales": ["service", "warranty", "maintenance", "repair", "part",
                    "售后", "保养", "质保", "维修", "配件",
                    "บริการ", "รับประกัน", "บำรุง", "ซอม", "อะไหล",
                    "garantia", "mantenimiento", "servicio", "reparacion", "recambio"],
    "other": ["hi", "hello", "thanks", "你好", "谢谢", "สวัสดี", "ขอบคุณ"],
}


# 紧急强信号(命中即判 emergency,优先于一切)
EMERGENCY_STRONG = ["won't start", "wont start", "cannot start", "can't start", "cant start",
                    "broken down", "broke down", "breakdown", "not start", "emergency", "rescue",
                    "accident", "cannot move", "danger", "dead battery", "battery is dead", "dead",
                    "stuck", "stranded", "no arranca", "no enciende",
                    "无法启动", "启动不了", "打不着", "抛锚", "救援", "事故", "故障", "坏了",
                    "ฉุกเฉิน", "รถเสีย", "สตาร์ทไม่ติด", "สตารทไม่ติด", "สตารทไม", "อุบัติเหต",
                    "ช่วยเหลือ", "สตาร์ทไม่ติด"]


def _normalize(text: str) -> str:
    t = unicodedata.normalize("NFKD", text)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"(?<=[\u0E00-\u0E7F])-(?=[\u0E00-\u0E7F])", "", t)  # 处理泰文 CMap 声调退化
    return re.sub(r"\s+", " ", t.lower().strip())


# 归一化后的关键词(避免泰文声调导致匹配失败)
KEYWORDS_NORM = {
    intent: [_normalize(w) for w in words]
    for intent, words in INTENT_KEYWORDS.items()
}
EMERGENCY_STRONG_NORM = [_normalize(w) for w in EMERGENCY_STRONG]

# 业务关键词全集(排除 other),用于判断"是否纯问候/闲聊"
_BUSINESS_NORM = set()
for _i, _ws in KEYWORDS_NORM.items():
    if _i != "other":
        _BUSINESS_NORM.update(_ws)

# 纯问候词(首词命中即视为打招呼;用词边界避免 "hi" 命中 this/high)
_GREET_FIRST = {"hi", "hello", "hey", "hiya", "howdy", "yo", "hola", "bonjour",
                "morning", "afternoon", "evening",
                "你好", "您好", "嗨", "哈喽", "สวัสดี"}


def emergency_hit(text: str) -> bool:
    """强紧急信号命中(安全优先,用于管道预检覆盖 LLM)。"""
    norm = _normalize(text)
    return any(w in norm for w in EMERGENCY_STRONG_NORM)


def greeting(text: str) -> bool:
    """是否为纯打招呼/问候(无业务内容)。

    用于把 LLM 偶发把问候误判成业务意图(如 after-sales)校正回 other。
    规则:去掉首尾标点;短(<24);不含任何业务关键词;首词是问候词。
    例:"hi"/"hello there"/"good morning"/"你好"/"สวัสดี" → True;
       "你好,续航多少"/"hi, what is the range" → False(含业务意图)。
    """
    t = (text or "").strip().lower()
    t = t.strip(" \t\r\n!,.。！？?~～")
    if not t or len(t) > 24:
        return False
    if any(w in t for w in _BUSINESS_NORM):
        return False
    first = re.split(r"[\s,;]+", t)[0]
    if first in _GREET_FIRST:
        return True
    if t.startswith(("good morning", "good afternoon", "good evening")):
        return True
    return False


def recognize(text: str, llm_confirm=None, **kw):
    """返回 (best_intent, confidence, scored_dict, question)。

    question: 是否在提问/提要求(应先回应)。由 LLM 判定;关键词兜底默认 False。
    llm_confirm: 可选的 callable(text)->(intent, confidence, question)。
    """
    norm = _normalize(text)
    # 紧急优先(设计:emergency 优先于一切流程):强信号直接判定
    if any(w in norm for w in EMERGENCY_STRONG_NORM):
        return ("emergency", 0.95, {"emergency": 5}, True)

    # 准确率优先:LLM 语义确认(优先,即便有/无关键词命中也跑),失败才回退关键词
    if llm_confirm:
        try:
            r = llm_confirm(text)
            if isinstance(r, (tuple, list)) and len(r) >= 2:
                llm_intent, llm_conf = r[0], r[1]
                question = r[2] if len(r) >= 3 else True
                if llm_intent in INTENTS and llm_conf is not None:
                    return (llm_intent, round(float(llm_conf), 3), {"llm": True}, bool(question))
        except Exception:
            pass

    # 关键词兜底(LLM 不可用/无 key 时)
    scores = {}
    for intent, words in KEYWORDS_NORM.items():
        hits = sum(1 for w in words if w and w in norm)
        if hits:
            scores[intent] = hits
    if not scores:
        return ("other", 0.5, {}, False)
    best = max(scores, key=scores.get)
    conf = min(1.0, 0.5 + 0.15 * scores[best])
    return (best, round(conf, 3), scores, False)


def is_ambiguous(confidence: float, threshold: float = None) -> bool:
    return confidence < (threshold or config.INTENT_CONFIDENCE_THRESHOLD)


# ---- 知识缺口检测(兜底留资用) ----
# 回复中出现"没有/暂无/说不清/建议联系经销商"等"我不知道"信号 => 知识缺口。
# 用于第2层硬保险: 命中且该意图应留资、且回复还没引导留资时, 由 pipeline 追加留资话术。
_KB_GAP_RE = re.compile(
    r"(暂时没有|暂无|不清楚|无法确认|无法确定|需确认|没有.{0,3}该|该市场.{0,6}(没有|暂时)|"
    r"建议.{0,4}联系|请您.{0,4}联系|未能提供|无法提供|查询不到|查不到|"
    r"don'?t have|no information|cannot confirm|can'?t confirm|no data|not available|we do not have|we don'?t|"
    r"please contact|refer to|there is no|i don'?t have|"
    r"ไม่มีข้อมูล|ไม่ทราบ|ไม่มีราย|ไม่สามารถยืนยัน|โปรดติดต่อ|ยังไม่มี|ไม่มีใน|"
    r"no tenemos|no dispongo|no hay información|no podemos confirmar|por favor contacte|no está disponible|no tengo info)", re.I)


def is_knowledge_gap(text):
    """回复是否为"知识缺失/不知道"信号(4 语言)。"""
    return bool(_KB_GAP_RE.search(text or ""))
