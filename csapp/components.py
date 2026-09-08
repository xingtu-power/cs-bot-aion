"""UI 组件 schema (Lead Card 等)。
docs/plans/lead-card.md 设计。

为什么独立模块:
- 卡片 schema 装配与 pipeline 主流程解耦,未来 quick_replies / action_button 同结构。
- 4 语言文案集中,review/翻译都在一处。
- 测试独立:可纯 import 单测,不依赖整套 pipeline。

触发的 4 种状态(必填字段):
- lead_input   : 用户尚未留下联系方式 → 让用户输入
- lead_confirm : 本会话/跨会话已有联系方式 → 显示确认
"""
from __future__ import annotations
import re
from typing import Optional, Tuple


# ============== 4 语言文案字典 ==============
# 留资卡的字段标签 / 占位符 / 提交按钮文案 / 隐私同意
_LEAD_LABELS = {
    "zh": {
        "title":    "留个联系方式，专员为您跟进",
        "phone_label":   "手机号",
        "email_label":   "邮箱",
        "phone_ph":  "例:138 1234 5678",
        "email_ph":  "例:name@example.com",
        "submit":    "提交",
        "consent":   "提交即同意专员按当地市场回拨/回信，仅用于本次咨询",
        "success":   "已收到，专员将与您联系",
        "change_hint":   "如号码已变更,请告诉我新号",
        "phone_mask_tpl": "我们将以 {phone} 与您联系，专员将于 1 个工作日内回电",
        "email_mask_tpl": "我们将以 {email} 与您联系，专员将于 1 个工作日内回信",
    },
    "en": {
        "title":    "Leave your contact — a specialist will follow up",
        "phone_label":   "Phone",
        "email_label":   "Email",
        "phone_ph":  "e.g. +61 4 1234 5678",
        "email_ph":  "e.g. name@example.com",
        "submit":    "Submit",
        "consent":   "By submitting you agree the local team may call/email you for this inquiry only",
        "success":   "Got it — a specialist will reach out",
        "change_hint":   "If your number has changed, please tell me the new one.",
        "phone_mask_tpl": "We will reach you at {phone}. A specialist will follow up within 1 business day.",
        "email_mask_tpl": "We will email you at {email}. A specialist will reply within 1 business day.",
    },
    "th": {
        "title":    "ฝากข้อมูลติดต่อ — เจ้าหน้าที่จะติดตามให้",
        "phone_label":   "เบอร์โทรศัพท์",
        "email_label":   "อีเมล",
        "phone_ph":  "เช่น +66 8 1234 5678",
        "email_ph":  "เช่น name@example.com",
        "submit":    "ส่งข้อมูล",
        "consent":   "การส่งข้อมูลถือว่าคุณยินยอมให้เจ้าหน้าที่ติดต่อกลับตามข้อมูลที่ระบุ เฉพาะการสอบถามนี้เท่านั้น",
        "success":   "รับทราบ — เจ้าหน้าที่จะติดต่อกลับ",
        "change_hint":   "หากเบอร์โทรเปลี่ยน รบกวนแจ้งเบอร์ใหม่ให้ทราบด้วย",
        "phone_mask_tpl": "เจ้าหน้าที่จะติดต่อคุณที่ {phone} ภายใน 1 วันทำการ",
        "email_mask_tpl": "เจ้าหน้าที่จะส่งอีเมลถึงคุณที่ {email} ภายใน 1 วันทำการ",
    },
    "es": {
        "title":    "Deje su contacto — un especialista le hará seguimiento",
        "phone_label":   "Teléfono",
        "email_label":   "Correo",
        "phone_ph":  "ej. +34 612 345 678",
        "email_ph":  "ej. nombre@ejemplo.com",
        "submit":    "Enviar",
        "consent":   "Al enviar, acepta que el equipo local le contacte por esta consulta únicamente",
        "success":   "Recibido — un especialista se pondrá en contacto",
        "change_hint":   "Si su número ha cambiado, indíqueme el nuevo.",
        "phone_mask_tpl": "Le contactaremos al {phone}. Un especialista le hará seguimiento en 1 día hábil.",
        "email_mask_tpl": "Le escribiremos a {email}. Un especialista le responderá en 1 día hábil.",
    },
}


# ============== 手机号部分隐藏 ==============
# 13812345678     → 138****5678          (中国 11 位, 中间 4 位 * 隐藏)
# +86 13812345678 → +86 138****5678      (带 + 国家码保留)
# +61412345678    → +61 4123****56       (澳洲 10 位手机, 中间 4 位 *)
# 1234567 (>=7)   → 12*****67            (7 位刚好, 中间 5 位 *)
def _mask_phone(phone) -> str:
    if not phone:
        return ""
    p = phone.strip().replace(" ", "").replace("-", "")
    digits = re.sub(r"\D", "", p)
    if not digits or len(digits) < 7:
        return phone or ""
    # 拆分 country + body:只对显式 + 开头剥离国家码
    if p.startswith("+"):
        # 优先级:
        #   - digits=11 且首字符为 '1': 北美 +1 + 10 位 body (415/212/...)
        #   - cc_len=2 (中/英/澳/西 等主流) + body ∈ [9,12]
        #   - cc_len=3 (罕见)
        #   - cc_len=1 兜底
        cc_len_used = None
        if len(digits) == 11 and digits.startswith("1"):
            cc_len_used = 1
        for cc_len in (2, 3, 1):
            if cc_len_used is not None:
                break
            r = len(digits) - cc_len
            if 9 <= r <= 12:
                cc_len_used = cc_len; break
        if cc_len_used is None:
            cc_len_used = max(1, len(digits) - 11)
        body = digits[cc_len_used:]
        country = "+" + digits[:cc_len_used]
    else:
        country = ""
        body = digits
    n = len(body)
    # 通用规则:head = max(2, n-8), tail = max(2, n-7), middle = n - head - tail
    # 中国 11 位 → head=3, tail=4, middle=4
    # 10 位     → head=3, tail=4, middle=3
    # 7 位      → head=2, tail=2, middle=3
    head = body[:3] if n >= 7 else body[: max(1, n // 2)]
    tail = body[-4:] if n >= 7 else body[-max(1, n // 2):]
    middle = "*" * max(0, n - len(head) - len(tail))
    return f"{country} {head}{middle}{tail}".strip()


# ============== 邮箱部分隐藏 ==============
# zhangsan@example.com  → zh******@example.com
# ab@example.com        → ab@example.com    (本地 <3 时不遮,太短没必要)
# A.B@qq.com            → A.****@qq.com    (保留第 1 个字符 + 后续明文段)
def _mask_email(email: str) -> str:
    if not email or "@" not in email:
        return email or ""
    local, _, domain = email.strip().partition("@")
    if not local or len(local) < 3:
        # 单字符或 2 字符用户名(常见内部邮箱):全部保留,或前 1 + *
        if len(local) >= 1:
            return f"{local[0]}{'*' * (len(local) - 1)}@{domain}"
        return email
    # 标准遮蔽:保留前 2 + (len-2) 个 *
    return f"{local[:2]}{'*' * (len(local) - 2)}@{domain}"


# ============== 提取 specialist hint 文案 ==============
def _specialist_hint(labels: dict, phone_masked: str, email_masked: str) -> str:
    """根据'哪个联系方式能用'选模板。电话缺失就用邮箱模板,都没有用 phone 占位。"""
    if phone_masked:
        return labels["phone_mask_tpl"].format(phone=phone_masked)
    if email_masked:
        return labels.get("email_mask_tpl", labels["phone_mask_tpl"]).format(email=email_masked)
    return labels.get("phone_mask_tpl", "")


# ============== 数据源解析 ==============
# 返回 (phone, email, source) — source ∈ "collected" | None
def _resolve_lead_contact(session) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """仅看本会话 collected 是否已记录到 phone/email。

    2026-09 起:不再跨会话查询 leads 历史(user_id → leads 表)。
    原因:用户场景要求"会话粒度"的留资状态,跨会话会让用户对新会话感到
    "怎么已经留过?",体验干扰。等真有此需求时再用 dedupeKey 或显式
    「复用上次的联系方式」交互开启。"""
    ph = (session.collected.get("phone") or "").strip() or None
    em = (session.collected.get("email") or "").strip() or None
    if ph or em:
        return ph, em, "collected"
    return None, None, None


# ============== 卡片装配 ==============
def build_confirm_card(phone: Optional[str], email: Optional[str], source: str,
                       lang: str, session_id: str = "now") -> Optional[dict]:
    """纯数据版本:不依赖 session,直接根据 phone/email/lang 返回 confirm schema。"""
    if not (phone or email):
        return None
    lang = (lang or "en").lower()
    if lang not in _LEAD_LABELS:
        lang = "en"
    labels = _LEAD_LABELS[lang]
    phone_masked = _mask_phone(phone) if phone else None
    email_masked = _mask_email(email) if email else None
    hint = _specialist_hint(labels, phone_masked or "", email_masked or "")
    return {
        "id":     f"lead-card-{session_id}",
        "lang":   lang,
        "title":  labels["title"],
        "type":   "lead_confirm",
        "source": source,
        "phoneMasked": phone_masked,
        "emailMasked":  email_masked,
        "email":  email,
        "specialistHint": hint,
        "changeHint":  labels["change_hint"],
        "phone":  phone,   # 完整号码供前端落库;UI 默认只显示 masked
    }


def _build_lead_card(session, reply_lang: str, market: Optional[str] = None) -> Optional[dict]:
    """主入口:根据当前会话状态返回一张 lead 卡片 schema。

    返回 dict(id, type, lang, ...),return None 表示"不发卡片"。
    """
    lang = (reply_lang or "en").lower()
    if lang not in _LEAD_LABELS:
        lang = "en"
    labels = _LEAD_LABELS[lang]
    phone, email, source = _resolve_lead_contact(session)
    base = {
        "id":   f"lead-card-{session.id}",
        "lang": lang,
        "title": labels["title"],
    }
    if phone or email:
        # 已留过 → 确认卡
        phone_masked = _mask_phone(phone) if phone else None
        email_masked = _mask_email(email) if email else None
        hint = _specialist_hint(labels, phone_masked or "", email_masked or "")
        return {
            **base,
            "type":  "lead_confirm",
            "source": source,                  # "collected"
            "phoneMasked": phone_masked,
            "emailMasked":  email_masked,
            "email":     email,
            "specialistHint": hint,
            "changeHint":  labels["change_hint"],
            "phone": phone if phone else None,    # 完整号码供前端落库
        }
    # 未留 → 输入卡
    return {
        **base,
        "type":        "lead_input",
        "fields":      ["phone", "email"],   # 至少 phone,可选 email
        "labels": {
            "phone":      labels["phone_label"],
            "email":      labels["email_label"],
            "submit":     labels["submit"],
            "consent":    labels["consent"],
            "phone_ph":   labels["phone_ph"],
            "email_ph":   labels["email_ph"],
        },
        "consentVersion": "v1",
    }


# ============== 触发点决策 ==============
def should_attach_lead_card(intent: str, kg_appended: bool, lead_record_present: bool,
                            first_contact_step: bool, collected_contact: bool = False,
                            has_shown_lead_card: bool = False,
                            on_contact_step: bool = False) -> bool:
    """在 pipeline.chat() 的 reply 装配阶段判断是否要附 lead card。

    触发点:
    1) 当前处于 contact 步(引导卡索要联系方式)且用户尚未留资 → input
    2) 知识缺失兜底已追加 / 首次 contact 步 → 若已采到号码给 confirm,否则给 input
    3) lead_record 已存在(用户刚提交) → confirm
    4) 本会话/历史已采到号码且当前售前意图仍缺一张确认卡 → confirm
    """
    if has_shown_lead_card:
        return False      # 一轮会话只显示一次,避免重复
    if lead_record_present:
        return True
    if collected_contact:
        # 售前类意图且已有联系方式,给确认卡;其它意图不发
        return bool(intent and intent in ("product-inquiry", "dealer-lookup", "after-sales"))
    # 当前引导卡正在 ask contact,或知识缺口/首次 contact 场景 → 发 input
    if on_contact_step or kg_appended or first_contact_step:
        return True
    return False
