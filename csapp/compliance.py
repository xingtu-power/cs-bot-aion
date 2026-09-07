"""数据合规与内容安全(Phase 2,对应设计 §7/§8)。

- 合规披露:留资前的 PDPA(泰国)/Privacy Act(澳洲)告知条款(按市场语言)。
- 留资记录:consent_at/consent_version + 最小化 + 去重键(PII 归一无明文存储)。
- 内容安全:禁区清单(价格/质保承诺不许自由发挥)、输出过滤、prompt injection 检测。
"""
import re, time, hashlib, unicodedata

CONSENT_VERSION = "v1"

# 合规披露(按市场语言;骨架以 en/th 覆盖)
CONSENT_TEXT = {
    "AU": {"en": "By sending a contact, you agree that AION / GAC may contact you about your enquiry. "
                 "We handle your details in line with the Australian Privacy Act (APPs).",
           "th": "ส่งข้อมูลติดต่อถือว่าคุณยินยอมให้ AION / GAC ติดต่อเกี่ยวกับการสอบถามของคุณ "
                 "เราจัดการข้อมูลตามพรบ.คุ้มครองข้อมูลส่วนบุคคล (PDPA)",
           "es": "Al enviar un contacto, acepta que AION / GAC pueda contactarle sobre su consulta. "
                 "Tratamos sus datos conforme a la Ley de Privacidad australiana (APPs).",
           "zh": "提交联系方式即表示您同意 AION / 广汽（GAC）就您的咨询与您联系。"
                 "我们将依照《澳大利亚隐私法》(APPs) 处理您的个人信息。"},
    "THA": {"en": "By sending a contact, you agree that AION / GAC may contact you about your enquiry. "
                  "We handle your details in line with the Personal Data Protection Act (PDPA).",
            "th": "ส่งข้อมูลติดต่อถือว่าคุณยินยอมให้ AION / GAC ติดต่อเกี่ยวกับการสอบถามของคุณ "
                  "เราจัดการข้อมูลตามพรบ.คุ้มครองข้อมูลส่วนบุคคล (PDPA)",
            "es": "Al enviar un contacto, acepta que AION / GAC pueda contactarle sobre su consulta. "
                  "Tratamos sus datos conforme a la Ley de Protección de Datos Personales (PDPA).",
            "zh": "提交联系方式即表示您同意 AION / 广汽（GAC）就您的咨询与您联系。"
                  "我们将依照《个人数据保护法》(PDPA) 处理您的个人信息。"},
}


def consent_message(market="AU", lang="en"):
    m = CONSENT_TEXT.get(market, CONSENT_TEXT["AU"])
    return m.get(lang, m.get("en"))


def _norm_phone(phone):
    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:] if len(digits) >= 10 else digits


def dedupe_key(market, phone, email=""):
    """归一化去重键:市场 + 手机后10位 或 邮箱小写。"""
    if phone:
        return f"{market}:{_norm_phone(phone)}"
    if email:
        return f"{market}:{email.strip().lower()}"
    return None


def build_lead_record(session, market, email=None, phone=None, consent=True):
    """构建合规留资记录(最小化 + consent 元数据 + 去重键)。"""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return {
        "leadId": "lead_" + session.id[-8:],
        "sessionId": session.id,
        "userId": getattr(session, "user_id", None),
        "market": market,
        "channel": "web",
        "intent": session.intent,
        "email": (email or "").strip(),
        "phone": (phone or "").strip(),
        "consent": bool(consent),
        "consentVersion": CONSENT_VERSION,
        "consentAt": now if consent else None,
        "dedupeKey": dedupe_key(market, phone, email),
        "createdAt": now,
    }


# ---------------- 内容安全 ----------------
# 禁区:价格/优惠/质保承诺等只允许引用原话,禁止 LLM 自行承诺(设计 §8.1)
FORBIDDEN_PATTERNS = [
    (r"(?i)guarantee(d)?\s+(a|the)?\s*\d{2,}", "warranty-numeric-claim"),
    (r"(?i)\b(free|discount|rebate|offer|promo)\b", "promo-claim"),
    (r"(?i)you (will|won|can) (definitely|certainly|absolutely)", "overpromise"),
    (r"(?i)\b(guarantee|warranty)\b.*\b(5|10|3)\s*years?\b", "warranty-length-claim"),
]

# prompt injection 试探(引导 AI 越权/泄露/角色扮演)
INJECTION_PATTERNS = [
    r"(?i)(ignore|disregard) (previous|prior|all) (instructions|prompt)",
    r"(?i)(system|developer|assistant) (?=(message|prompt|instructions|role))",
    r"(?i)reveal (your|the) (system|prompt|instructions|api|key)",
    r"(?i)pretend (you are|to be)",
    r"(?i)show (me|us) (the )?other market",
    r"(?i)what (are|is) your (secret|hidden) (instructions|prompt)",
]


def is_prompt_injection(text):
    for pat in INJECTION_PATTERNS:
        if re.search(pat, text):
            return True
    return False


def find_forbidden(reply):
    """返回命中的禁区类别列表。"""
    hits = []
    for pat, label in FORBIDDEN_PATTERNS:
        if re.search(pat, reply):
            hits.append(label)
    return hits


def output_filter(reply):
    """输出侧过滤:命中禁区则提示以官方为准;返回 (clean_reply, flagged)。"""
    hits = find_forbidden(reply)
    if hits:
        return reply, True
    return reply, False


# ---------------- 空泛反问检测(用于收紧版A: 首次未实际作答则二次调用修正) ----------------
# 只在"LLM 没回答用户具体问题、反而问用户想了解什么"时命中(强特征词);
# 刻意**不**命中卡片设计的合法澄清(如 "您更看重续航还是价格?")。
_VAGUE_RE = re.compile(
    r"(麻烦再说一下|请再说一下|再说一下|可以帮您解答|想了解什么|想咨询什么|想了解哪|想咨询哪|"
    r"您的具体需求|告诉我您的需求|您想问什么|您想了解哪方面|您想咨询哪方面|"
    r"what would you like to know|what can i help you with|please clarify|"
    r"could you (please )?clarify|is there anything specific|"
    r"which specific (topic|aspect|model|area)|i can help you with|how can i assist you)", re.I)


def is_vague_reply(text):
    """判断回复是否为"空泛反问"(未回答实际问题)。"""
    return bool(_VAGUE_RE.search(text or ""))


# ---------------- 车型信息防臆造(非 UT 车型) ----------------
# 知识库里除 AION UT 外,其它车型只有用户提供的清单信息;功率/扭矩/电池容量/马力等详细参数一律没有。
# 若回复把这类参数写给非 UT 车型 -> 该句强制替换为"暂无确切信息,建议联系授权经销商/官方热线核实"。
_NON_UT_RE = re.compile(r"(AION\s+Y\s*Plus|AION\s*RT|AION\s*N60|AION\s*V\b|昊铂\s*GT|昊铂\s*HL|AION\s*LX|Hyper\s*GT|Hyper\s*HL)", re.I)
_FAB_SPEC_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:kw|kw\b|马力|n·m|nm\b|牛·米|kwh\b|度\b|扭矩|功率)", re.I)
_FAB_REPL = {"zh": "（该具体参数目前暂无确切信息，建议联系授权经销商或官方热线核实）",
            "en": "(This specific spec is not available at the moment — please contact an authorized dealer or the official hotline.)",
            "th": "(ข้อมูลจำเพาะนี้ยังไม่มีในขณะนี้ กรุณาติดต่อตัวแทนจำหน่ายที่ได้รับอนุญาตหรือสายด่วนอย่างเป็นทางการ)",
            "es": "(Este dato concreto no está disponible por ahora: contacte con un concesionario autorizado o la línea oficial.)"}


def _fab_repl(lang):
    return _FAB_REPL.get(lang, _FAB_REPL["en"])


def guard_model_facts(reply, lang="zh"):
    """非 UT 车型不得出现其未提供的详细参数(功率/扭矩/电池容量等);否则替换该句。"""
    if not reply or not _NON_UT_RE.search(reply):
        return reply
    segs = re.split(r"(?<=[。！？!?])", reply)
    out = []
    for seg in segs:
        if _NON_UT_RE.search(seg) and _FAB_SPEC_RE.search(seg):
            out.append(_fab_repl(lang))
        else:
            out.append(seg)
    return "".join(out)


# ---------------- 回复质量:禁 AI 感 / 机器感表达 ----------------
# 参考售前通用框架 Self-check:删除「根据查询结果/系统显示/工具返回」等机器感表达、
# 删除暴露 AI/机器人身份的表达。
AI_REVEAL_PATTERNS = [
    r"(?i)\baccording to (the )?(search results?|data|information|results?)\b[,\s]*",
    r"(?i)\b(the system shows?|system query shows?|search results? show|the results? show)\b[,\s]*",
    r"(?i)\bbased on (the )?(search results?|data|information)\b[,\s]*",
    r"(?i)\b(as an? ai( assistant)?|i('m| am) an? ai|i am a robot|as a language model)\b[,\s]*",
    r"根据(查询结果|系统显示|搜索结果|工具返回|门店信息|查询到的信息)[，,]*",
    r"系统(查询)?显示[，,]*",
    r"(作为)?(一个)?(ai|人工智能)(助手)?[，,]*",
]


def strip_ai_phrases(text):
    """去掉 AI/机器人身份与机器感表达;返回 (clean_text, changed)。"""
    clean = text
    changed = False
    for pat in AI_REVEAL_PATTERNS:
        new = re.sub(pat, "", clean)
        if new != clean:
            changed = True
            clean = new
    return clean.strip(), changed


# ---------------- 品牌合规:禁提/禁比其它品牌 ----------------
# 输出侧兜底:检测回复中是否出现其它品牌/竞品话术,命中则由管道替换为知识缺失+留资引导。
COMPETITOR_PATTERNS = [
    r"(?i)\b(BYD|Tesla|Nissan|MG|Geely|GWM|Haval|Chery|Toyota|Honda|Hyundai|Kia|Volkswagen|BMW|Mercedes|"
    r"Audi|Peugeot|Renault|Ford|Chevrolet|Volvo|Polestar|Zeekr)\b",
    r"(?i)(other brand|another brand|competitor|other manufacturer|look at other brands|rival)",
    r"(其他品牌|别的品牌|其它品牌|竞品|别的车型品牌|其他车型品牌)",
]


def find_competitor(reply):
    """回复中是否提到其它品牌/竞品/比品牌。返回 True 表示命中(需改走留资引导兜底)。"""
    for pat in COMPETITOR_PATTERNS:
        if re.search(pat, reply):
            return True
    return False


# ---------------- 回复质量:长度硬限 ----------------
def truncate_reply(text, max_chars):
    """超限时在句读边界截断,保留完整句意;返回截断后的文本。"""
    if not text:
        return text
    if len(text) <= max_chars:
        return text
    piece = text[:max_chars]
    # 在句读处回退到最近一句边界
    for sep in ("。", "！", "？", ". ", "! ", "? ", " …"):
        idx = piece.rfind(sep)
        if idx > 0:
            return piece[: idx + len(sep)].rstrip()
    return piece.rstrip()
