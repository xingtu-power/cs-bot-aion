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
