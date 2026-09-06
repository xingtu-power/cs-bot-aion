"""引导卡引擎(重构版):通用状态机 + LLM 生成回复。

- 每轮回复文本由管道里的一次 `_llm.respond(...)` 生成(按用户语言,见 pipeline/llm)。
- 本模块只负责:当前步骤的 state_desc(给 LLM 的情景)、对用户输入的校验、
  状态推进(收集/确认/达成)、合规留资与救援工单落库。
- 不再有手写的 4 语言话术模板。
"""
import json, os, re
from . import config, compliance
from .emotion import score_emotion

INTENTS = ["product-inquiry", "dealer-lookup", "usage-guide", "emergency", "after-sales", "other"]
BUSINESS = {"product-inquiry", "dealer-lookup", "usage-guide", "emergency", "after-sales"}

# 每个意图的步骤: (desc, expect, advance, goal)
#  expect: question|concern|contact|confirm|consent_rescue|none
# 引导话术 = 每步的 desc(喂给 LLM 的英文策略指令), 已抽到外部数据文件 talk_scripts.json,
#   改话术只需编辑该文件 + 重启服务(无需改代码)。见 引导话术设计.md。
#   内置 _DEFAULT_CARD 仅作文件缺失/损坏时的兜底。
_DEFAULT_CARD = {
    "product-inquiry": [
        {"desc": "ANSWER the customer's question and RECOMMEND the full AION/GAC pure-electric lineup (AION UT, Y Plus, "
                 "RT, N60, V, 昊铂GT, 昊铂HL, AION LX) using the 'MODEL LINEUP' reference block in the FACTS. First "
                 "answer what was asked using the facts; then recommend 1-3 models best matching their stated need "
                 "(budget, body type, range, charging, family size, driver assist), give each a price range and a "
                 "one-line 'best for', then move toward connecting them to a local dealer / arranging a visit. Do not "
                 "pitch before answering. ACCURACY: quote ONLY specs/prices that literally appear in the facts / "
                 "MODEL LINEUP; NEVER invent a model, range, battery, price, trim, feature, or test-cycle label (keep "
                 "CLTC as CLTC). If a detail is not listed, say you do not have it.",
         "expect": "question", "advance": 1},
        {"desc": "Based on your previous answer/recommendation, clarify what matters most to the customer (range / space / price / smart-driving / budget), then recommend the best-matching AION model from the lineup and build purchase desire, then move toward arranging a dealer visit / test drive.",
         "expect": "concern", "advance": 2},
        {"desc": "Ask the customer to leave a phone or email (with privacy consent) so a local dealer can follow up to arrange a viewing/test drive. Frame it as personalized service, not a sales pitch.",
         "expect": "contact", "goal": True},
    ],
    "dealer-lookup": [
        {"desc": "ANSWER the customer's question first. Share the nearest dealers (name/city/address/phone from facts), build interest in visiting / test-driving the model, offer to book a visit, then ask for a contact to arrange it.",
         "expect": "contact", "goal": True},
    ],
    "usage-guide": [
        {"desc": "SOLVE first: give clear, step-by-step guidance from the facts, then ask if it was resolved. If not resolved, offer more troubleshooting or the AION hotline. Aim for customer satisfaction. This is AFTER-SALES: do NOT offer a dealer visit, a test drive, a booking, or ask for contact details; do not push sales.",
         "expect": "confirm", "goal": True},
    ],
    "emergency": [
{"desc": "SOLVE first: confirm the customer and passengers are safe and reassure them. Give the AION roadside-assistance hotline from the facts (state the exact number if present, e.g. a rescue-guide phone; if none, point them to the Emergency Rescue Guide). Then offer to dispatch a rescue ticket and ask for consent. Prioritize calm, clear, urgent support.",
         "expect": "consent_rescue", "goal": True},
    ],
    "after-sales": [
        {"desc": "SOLVE first: answer the customer's service/warranty question accurately from the facts. If the issue can be resolved online, resolve it and confirm satisfaction. Only if a service visit is needed, offer to book one and ask for a contact (for scheduling, not to push sales).",
         "expect": "contact", "goal": True},
    ],
    "other": [
        {"desc": "Greet, introduce yourself as the AION assistant, and guide the customer into a topic with a friendly menu of options (specs / dealers / usage / after-sales / emergency). Do not force sales.",
         "expect": "none", "advance": 0},
    ],
}


def _load_card():
    """从外部数据文件 talk_scripts.json 加载引导卡;缺失/损坏则回退内置默认并告警。

    文件按意图覆盖默认(文件缺某意图则用默认), 改话术只需编辑该文件 + 重启。
    """
    path = os.path.join(os.path.dirname(__file__), "talk_scripts.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for intent, steps in data.items():
            assert isinstance(steps, list), f"{intent}: steps not a list"
            for st in steps:
                assert isinstance(st, dict) and "desc" in st and "expect" in st, f"{intent}: invalid step"
        merged = dict(_DEFAULT_CARD)
        merged.update(data)
        return merged
    except Exception as e:  # noqa: BLE001
        import warnings
        warnings.warn(f"talk_scripts.json load failed, using built-in defaults: {e}")
        return dict(_DEFAULT_CARD)


CARD = _load_card()


def _slot_expect(expect):
    """该 expect 是否需要用户输入(可被追问累计)。"""
    return expect in ("contact", "concern", "confirm", "consent_rescue")


# 判断用户是否在"提供有用/相关的信息"(城区/时间/地点/预约意向等),而非敷衍/答非所问。
# 用于让槽位计数语义感知: 只要用户在提供有用信息,就不计入"多次没拿到信息",避免误转人工。
_COOP_RE = re.compile(
    r"(朝阳|海淀|丰台|东城|西城|浦东|[\u4e00-\u9fa5]{1,3}区|[\u4e00-\u9fa5]{2,6}市|省|县|"
    r"街道|路\s*[\d\-]*号|店铺|门店|"
    r"上午|下午|晚上|今天|明天|后天|下周|本周|周一|周二|周三|周四|周五|周六|周日|星期|周末|"
    r"[\d]{1,2}点|[\d]{1,2}号|时间|中午|"
    r"morning|afternoon|evening|tomorrow|o'clock|weekend|"
    r"预约|到店|看车|试驾|方便|有空|地址|位置|地点|"
    r"booking|visit|test drive|available|location|address|district)", re.I)


def _is_cooperative(text):
    """用户消息是否在提供有用/相关信息(合作中);否则视为敷衍(可累计为失败)。"""
    t = (text or "").strip()
    if len(t) < 3:
        return False
    if _COOP_RE.search(t):
        return True
    if re.search(r"[\d@.]{6,}", t):   # 尝试给联系方式(手机号/邮箱)——即使格式无效也视为合作
        return True
    return False


# 敷衍/无信息量的短答(不得当作"提供了信息")
_DEFLECT_RE = re.compile(
    r"^(嗯+|哦+|啊+|随便|不知道|不清楚|都行|都可以|无所谓|行|好|ok|yes|哦哦|嗯嗯|唔|emm|没啥|"
    r"uh|hmm|idk|not sure)[\s。！？!?~～]*$", re.I)


def _is_deflect(text):
    return bool(_DEFLECT_RE.search((text or "").strip()))


# 用户表示"还没解决/仍有问题"(使用说明类) —— 应继续排查,不算"未确认"
_STILL_RE = re.compile(
    r"(还是[^。！？!?]{0,3}(不行|不对|不正常|异常|有问题|响|没|坏)|"
    r"仍然[^。！？!?]{0,3}(不行|有问题|异常|没)|"
    r"还没(好|解决|修好)|没(有)?(解决|修好|关掉)|还有(问题|异常|故障)|"
    r"still\s+(not|broken|problem|doesn|hav|showing)|not\s+(working|fixed|yet|solved|ok)|"
    r"there'?s\s+still|ยังไม่|ยังมีปัญหา|ไม่หาย)", re.I)


def _still_unresolved(text):
    return bool(_STILL_RE.search(text or ""))


# 槽位超限/无法满足时的交接兜底(非卡片引导话术;与 pipeline 的 inject/clarify 兜底一致)
# 默认模板仅为 LLM 不可用时的回退;实际优先用 _nat() 由 LLM 生成自然话术。
_HANDOFF = {
    "en": "Alright — I'm connecting you to a human AION specialist now; they'll take it from here.",
    "th": "ได้เลยครับ ผมกำลังเชื่อมต่อให้คุณกับผู้เชี่ยวชาญ AION แล้ว",
    "es": "Perfecto — le estoy conectando con un especialista de AION ahora mismo.",
    "zh": "好的，我这就为您转接 AION 人工专员，稍后专员会继续为您处理。",
}


_CONFIRM_TEXT = {
    "zh": "我这边可能还差一点信息才能帮您处理得更准确，需要我为您转接一位 AION 人工专员继续帮您吗？",
    "en": "I might need a bit more to make sure I get this right for you — would you like me to connect you to a human AION specialist?",
    "th": "ผมอาจต้องขอข้อมูลเพิ่มอีกนิดเพื่อช่วยคุณได้แม่นขึ้น ต้องการให้ผมเชื่อมต่อกับผู้เชี่ยวชาญ AION หรือไม่ครับ?",
    "es": "Puede que necesite un poco más para ayudarle con precisión. ¿Quiere que le conecte con un especialista de AION humano?"}
_CONFIRM_WORDS = re.compile(r"(转人工|人工|客服专员|要|需要|好|是|确认|对|yes|yeah|ok|sure|ใช่|sí|si|confirm|转接|专员)", re.I)
# 否定/婉拒:绝不能当成"确认转人工"
_DECLINE_RE = re.compile(
    r"(不需要|不用了|不用转|不用人工|不要人工|不要转|别转人工|千万别|无需|算了|不用|不要|"
    r"no need|no thanks|not (necessary|needed)|never mind|don'?t|dont)", re.I)


def _confirm_text(lang):
    return _CONFIRM_TEXT.get(lang, _CONFIRM_TEXT["en"])


def _is_confirm(msg):
    m = msg or ""
    if _DECLINE_RE.search(m):
        return False
    return bool(_CONFIRM_WORDS.search(m))


def _handoff(lang):
    return _HANDOFF.get(lang, _HANDOFF["en"])


def _nat(session, lang, kind):
    """用 LLM 生成自然的"确认转人工/转接话术";失败回退温暖模板。kind='confirm'|'handoff'。"""
    llm = getattr(session, "_llm", None) if session is not None else None
    if llm and hasattr(llm, "respond"):
        try:
            if kind == "confirm":
                sd = ("You have been unable to get the info you need from the customer. Warmly and naturally, in a short "
                      "empathetic way, ask whether they would like you to connect them to a human AION specialist. "
                      "Do NOT mention you are an AI; be natural, not robotic.")
            else:
                sd = ("Hand the customer over to a human AION specialist. Warmly confirm you are connecting them now, "
                      "briefly reassure them, and say a specialist will help shortly. Natural and short; do NOT mention AI.")
            r = llm.respond("(escalation)", "", sd, lang, "")
            if r and len(r) > 3 and r[3]:
                return r[3]
        except Exception:
            pass
    return _confirm_text(lang) if kind == "confirm" else _handoff(lang)


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
        if market == "THA":
            pat = config.LEAD_PHONE_TH
        elif market == "AU":
            pat = config.LEAD_PHONE_AU
        elif market == "CN":
            pat = config.LEAD_PHONE_CN
        else:
            pat = config.LEAD_PHONE_ANY
        if re.match(pat, ph):
            return True
    if em and "@" in em:
        return True
    return False


def _check(expect, session, message, kb):
    """校验用户消息是否符合当前步骤期望输入。返回 (received, goal_hit)。"""
    text = message or ""
    # 联系方式校验:用**用户市场**(如 CN)而非 KB 回退市场(CN→AU),否则中文号被按澳号规则误拒,
    # 导致"联系方式一直未满足"→ 误触发转人工确认。
    market = session.market or kb.market
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
        # 语义: 用户给了任何实质性偏好/相关信息 → 也算提供了关注点(不判失败/不升级)
        if len(text.strip()) >= 3 and not _is_deflect(text):
            session.collected["concern"] = session.collected.get("concern") or "other"
            return True, False
        return False, False
    if expect == "confirm":
        low = text.lower()
        # 用户表示还没解决/仍有问题 → 有效回应,继续排查(不判失败/不升级)
        if _still_unresolved(low):
            return True, False
        if any(w in low for w in ["yes", "ok", "solved", "解决了", "好了", "没问题", "没事了",
                                  "谢谢", "ได้", "ใช่", "ช่วยแล้ว", "是", "帮助", "worked"]):
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
    elif sd["expect"] in ("contact", "concern", "confirm") and not received:
        # 主流程"反复确认": 用户给的信息无效/不对 → 自然指出问题并要正确信息,绝不提转人工
        desc += (" The customer's last message did not provide valid/complete info for this step. If it looks "
                 "malformed (e.g. a phone number in the wrong format, or an answer that doesn't match), politely "
                 "point out what seems off and ask them to provide the correct info. If they gave a different but "
                 "meaningful detail, acknowledge it. Keep guiding; do NOT offer to transfer to a human.")
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

    # 槽位追问上限(售前框架参考):需用户输入的槽位,若一直未满足则累计;超限转人工,避免反复讨要。
    # 若用户最终给出了有效信息(槽位已满足),则清除遗留的"是否转人工"确认,转而正常推进。
    if received and session.ask_confirm and session.escalate_reason == "slot":
        session.ask_confirm = False; session.escalate_reason = None
    if _slot_expect(sd["expect"]) and not received:
        asks = getattr(session, "step_asks", None) or {}
        key = f"{intent}:{step}"
        # 语义感知: 用户在提供有用信息(城区/时间/地点/预约意向) → 视为合作,重置计数(不升级);
        # 只有持续提供不了有用信息(敷衍/短答)才累计到"多次没拿到信息→转人工确认"。
        if _is_cooperative(message):
            asks[key] = 0
        else:
            asks[key] = asks.get(key, 0) + 1
        session.step_asks = asks
        max_ask = sd.get("max_ask", config.SLOT_MAX_ASK)
        # 只在首次超限时转人工一次(=);之后不再重复交接,让新的问题能被回答/切意图(避免粘死)
        # 已询问"是否转人工" → 先处理用户答复(无论 asks 是否 == max)
        if session.ask_confirm and session.escalate_reason == "slot":
            if _is_confirm(message):
                session.escalated = True
                session.ask_confirm = False; session.escalate_reason = None
                emotion = score_emotion(message, intent)
                result = {"reply": _nat(session, lang, "handoff"), "emotion": emotion, "intent": intent,
                          "collected": session.collected, "advance": False,
                          "escalate": True, "escalate_reason": "slot"}
                return result
            session.ask_confirm = False; session.escalate_reason = None  # 未确认 → 放弃升级
        # 常规槽位:首次耗尽 → 询问是否转人工
        if asks[key] == max_ask and not session.ask_confirm:
            session.ask_confirm = True; session.escalate_reason = "slot"
            emotion = score_emotion(message, intent)
            result = {"reply": _nat(session, lang, "confirm"), "emotion": emotion, "intent": intent,
                      "collected": session.collected, "advance": False, "confirm_escalate": True}
            return result

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
