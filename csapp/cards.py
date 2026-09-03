"""引导卡引擎(重构版):通用状态机 + LLM 生成回复。

- 每轮回复文本由管道里的一次 `_llm.respond(...)` 生成(按用户语言,见 pipeline/llm)。
- 本模块只负责:当前步骤的 state_desc(给 LLM 的情景)、对用户输入的校验、
  状态推进(收集/确认/达成)、合规留资与救援工单落库。
- 不再有手写的 4 语言话术模板。
"""
import re
from . import config, compliance
from .emotion import score_emotion

INTENTS = ["product-inquiry", "dealer-lookup", "usage-guide", "emergency", "after-sales", "other"]
BUSINESS = {"product-inquiry", "dealer-lookup", "usage-guide", "emergency", "after-sales"}

# 每个意图的步骤: (desc, expect, advance, goal)
#  expect: question|concern|contact|confirm|consent_rescue|none
CARD = {
    "product-inquiry": [
        {"desc": "Answer the customer's AION UT product/spec question using the facts.",
         "expect": "question", "advance": 1},
        {"desc": "Ask what matters most (range / space / price), then move toward connecting them to a dealer.",
         "expect": "concern", "advance": 2},
        {"desc": "Ask the customer to leave a phone or email (with privacy consent) so a local dealer can connect.",
         "expect": "contact", "goal": True},
    ],
    "dealer-lookup": [
        {"desc": "Share the nearest dealers (from facts) and offer to book a visit. Ask for a contact to arrange it.",
         "expect": "contact", "goal": True},
    ],
    "usage-guide": [
        {"desc": "Give the steps to solve the customer's problem, then ask if it was resolved.",
         "expect": "confirm", "goal": True},
    ],
    "emergency": [
        {"desc": "Confirm the customer is safe, give the roadside hotline (from facts) and offer to dispatch a rescue ticket; ask for consent.",
         "expect": "consent_rescue", "goal": True},
    ],
    "after-sales": [
        {"desc": "Answer the customer's service/warranty question (from facts); offer to book a visit or get a contact.",
         "expect": "contact", "goal": True},
    ],
    "other": [
        {"desc": "Greet, introduce yourself as the AION assistant, and guide the customer into a topic.",
         "expect": "none", "advance": 0},
    ],
}


def _collect_contact(session, text):
    t = text.strip().lower()
    email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", t)
    phone = re.search(r"\d{4,}", t)
    if email:
        session.collected["email"] = email.group(0)
    if phone:
        session.collected["phone"] = re.sub(r"\D", "", phone.group(0))
    return bool(session.collected.get("phone") or session.collected.get("email"))


def _contact_valid(session, market):
    ph = session.collected.get("phone") or ""
    em = session.collected.get("email") or ""
    if ph:
        pat = config.LEAD_PHONE_TH if market == "THA" else config.LEAD_PHONE_AU
        if re.match(pat, ph):
            return True
    if em and "@" in em:
        return True
    return False


def _check(expect, session, message, kb):
    """校验用户消息是否符合当前步骤期望输入。返回 (received, goal_hit)。"""
    text = message or ""
    market = kb.market
    if expect == "contact":
        _collect_contact(session, text)
        if _contact_valid(session, market):
            return True, True
        return False, False
    if expect == "concern":
        low = text.lower()
        for k, words in (("range", ["range", "续航", "ระยะทาง", "วิ่ง"]),
                         ("space", ["space", "size", "room", "空间", "พื้นที่"]),
                         ("price", ["price", "cost", "ราคา", "价格"])):
            if any(w in low for w in words):
                session.collected["concern"] = k
                return True, False
        return False, False
    if expect == "confirm":
        if any(w in text.lower() for w in ["yes", "ok", "solved", "谢谢", "ได้", "ใช่", "ช่วยแล้ว", "是", "帮助"]):
            return True, True
        return False, False
    if expect == "consent_rescue":
        if any(w in text.lower() for w in ["yes", "send rescue", "dispatch", "ใช่", "ช่วย", "ส่ง", "是", "帮忙"]):
            session.rescue_ticket_id = "rsq_" + session.id[-8:]
            return True, True
        return False, False
    # question / none: 视为推进到下一步(不置达成,因为不是收集输入)
    return True, False


def state_desc(intent, step_index, message, session, kb):
    """构建给 LLM 的情景描述(state_desc),并内部校验输入是否已满足。"""
    steps = CARD.get(intent, CARD["other"])
    step = min(step_index or 0, len(steps) - 1)
    sd = steps[step]
    desc = sd["desc"]
    received, _ = _check(sd["expect"], session, message, kb)
    if received and sd["expect"] in ("contact", "concern", "confirm", "consent_rescue"):
        desc += " The customer just provided the requested information."
    elif intent in BUSINESS or sd["expect"] == "question":
        # 提示:客户可能仍在提问,应优先回应
        desc += " If the customer is asking a new question, answer it first before guiding."
    return desc


def run_card(session, message, kb, lang):
    """执行当前意图的引导卡:校验输入、推进状态、合规落库;回复文本取 session._answer_llm。"""
    intent = session.intent or "other"
    steps = CARD.get(intent, CARD["other"])
    step = min(session.step_index or 0, len(steps) - 1)
    sd = steps[step]
    received, goal_hit = _check(sd["expect"], session, message, kb)

    # 合规留资:收集联系方式且有效
    if received and sd.get("goal") and sd["expect"] == "contact":
        session.lead_id = "lead_" + session.id[-8:]
        session.lead_record = compliance.build_lead_record(
            session, kb.market,
            email=session.collected.get("email"),
            phone=session.collected.get("phone"), consent=True)

    # 状态推进
    if received and sd.get("goal"):
        session.target_reached = True
        session.resolved = True
    elif received:
        session.step_index = sd.get("advance", step + 1)

    emotion = score_emotion(message, intent)
    response = getattr(session, "_answer_llm", None) or ""
    result = {"reply": response, "emotion": emotion, "intent": intent,
              "collected": session.collected, "advance": bool(received)}
    if received and sd.get("goal"):
        result["target_reached"] = True
    return result
