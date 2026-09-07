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
    },
}


# ============== 手机号部分隐藏 ==============
# 13812345678 → 138****5678  (+86/+61/+66/+34 国家码保留)
def _mask_phone(phone: str) -> str:
    if not phone:
        return ""
    p = phone.strip().replace(" ", "").replace("-", "")
    # 找首位数字串作为号码本体
    m = re.search(r"(\+?\d[\d\- ]*)", p)
    if not m:
        return phone
    digits = re.sub(r"\D", "", m.group(1))
    if len(digits) < 7:
        return phone   # 太短不遮
    # 国家码:开头的非 0 串;本体:剩余
    cc_match = re.match(r"(\+?\d{1,3})", digits)
    country = cc_match.group(1) if cc_match else ""
    body = digits[len(country):]
    if len(body) >= 7:
        head = body[:3]; tail = body[-4:]; middle = "*" * (len(body) - 7)
        masked_body = f"{head}{middle}{tail}"
    else:
        # 本体太短,只显示前 2 + 后 2
        masked_body = f"{body[:2]}{'*' * max(0, len(body) - 4)}{body[-2:]}" if len(body) >= 4 else body
    return f"{country} {masked_body}".strip()


# ============== 数据源解析 ==============
# 返回 (phone, email, source) — source ∈ "collected" | "history" | None
def _resolve_lead_contact(session, db_history=None) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """优先本会话 collected,然后跨会话 leads 历史。"""
    ph = (session.collected.get("phone") or "").strip() or None
    em = (session.collected.get("email") or "").strip() or None
    if ph or em:
        return ph, em, "collected"
    if db_history and getattr(session, "user_id", None):
        rec = db_history(session.user_id, getattr(session, "market", None)) or {}
        ph2 = rec.get("phone")
        em2 = rec.get("email")
        if ph2 or em2:
            return (ph2 or "").strip() or None, (em2 or "").strip() or None, "history"
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
    masked = _mask_phone(phone) if phone else None
    hint = labels["phone_mask_tpl"].format(phone=(masked or email or ""))
    return {
        "id":     f"lead-card-{session_id}",
        "lang":   lang,
        "title":  labels["title"],
        "type":   "lead_confirm",
        "source": source,
        "phoneMasked": masked,
        "email":  email,
        "specialistHint": hint,
        "changeHint":  labels["change_hint"],
        "phone":  phone,
    }


def _build_lead_card(session, reply_lang: str, market: Optional[str] = None,
                     db_history=None) -> Optional[dict]:
    """主入口:根据当前会话状态返回一张 lead 卡片 schema。

    返回 dict(id, type, lang, ...),return None 表示"不发卡片"。
    """
    lang = (reply_lang or "en").lower()
    if lang not in _LEAD_LABELS:
        lang = "en"
    labels = _LEAD_LABELS[lang]
    phone, email, source = _resolve_lead_contact(session, db_history=db_history)
    base = {
        "id":   f"lead-card-{session.id}",
        "lang": lang,
        "title": labels["title"],
    }
    if phone or email:
        # 已留过 → 确认卡
        masked = _mask_phone(phone) if phone else None
        # 仅在匿名式回显号码(已 mask);原始 phone 字段也带上(前端 hover 等可用,但默认 UI 只显示 masked)
        hint = labels["phone_mask_tpl"].format(phone=(masked or email or ""))
        return {
            **base,
            "type":  "lead_confirm",
            "source": source,                  # "collected" / "history"
            "phoneMasked": masked,
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
                            has_shown_lead_card: bool = False) -> bool:
    """在 pipeline.chat() 的 reply 装配阶段判断是否要附 lead card。

    触发点:
    1) 知识缺失兜底已追加 / 首次 contact 步 → 若已采到号码给 confirm,否则给 input
    2) lead_record 已存在(用户刚提交) → confirm
    3) 本会话/历史已采到号码且当前售前意图仍缺一张确认卡 → confirm
    """
    if has_shown_lead_card:
        return False      # 一轮会话只显示一次,避免重复
    if lead_record_present:
        return True
    if collected_contact:
        # 售前类意图且已有联系方式,给确认卡;其它意图不发
        return bool(intent and intent in ("product-inquiry", "dealer-lookup", "after-sales"))
    if kg_appended or first_contact_step:
        return True       # 还没留,发 lead_input
    return False
