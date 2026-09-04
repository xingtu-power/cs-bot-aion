"""端到端对话管道(Phase 1 骨架)。

一次 chat turn 的编排:会话恢复 → 语言检测 → 市场路由 → 意图识别(二段式)
→ 澄清/锁定 → 引导卡执行 → 持久化 → 返回响应。
"""
import re, difflib
from . import config, intent as intent_mod, cards as card_mod, compliance, debug
from .state import StateStore
from .langdetect import detect_language
from .market import route_market, market_kb, extract_market
from .kb import KnowledgeBase
from .llm import get_llm
from . import db

_store = StateStore()
_llm = get_llm()

# (intent, letter, en_label, th_label)
CLARIFY_OPTIONS = [
    ("product-inquiry", "A", "Vehicle specs / price", "สเปก / ราคารถ"),
    ("dealer-lookup", "B", "Dealer location", "ตำแหน่งตัวแทนจำหน่าย"),
    ("usage-guide", "C", "How to use", "วิธีใช้งาน"),
    ("other", "D", "Something else", "อื่นๆ"),
]


def _response(session, reply, lang, market, source, extra=None):
    d = {
        "sessionId": session.id,
        "language": lang,
        "market": market,
        "marketSource": source,
        "intent": session.intent,
        "reply": reply.get("reply", ""),
        "citations": [],
        "state": {"stepIndex": session.step_index, "collected": session.collected,
                  "totalRounds": session.total_rounds, "clarifyRounds": session.clarify_rounds},
        "emotionScore": reply.get("emotion", session.emotion_score),
        "targetReached": bool(reply.get("target_reached", session.target_reached)),
        "escalate": bool(reply.get("escalate")),
        "escalateReason": reply.get("escalate_reason"),
        "leadId": session.lead_id,
        "rescueTicketId": session.rescue_ticket_id,
    }
    if extra:
        d.update(extra)
    return d


def _intent_by_choice(message):
    m = (message or "").strip().lower()
    for intent, letter, _, _ in CLARIFY_OPTIONS:
        if m in (letter.lower(), letter, letter + ".", letter + ")",):
            return intent
    return None


def chat(session_id=None, message=None, location=None, explicit_market=None, lang_hint=None):
    """一次对话轮。message 必填。返回响应 dict(与 API 对齐)。"""
    message = (message or "").strip()
    session = _store.get(session_id)

    # 1) 语言检测(跟随用户;拉丁/其它语言用 LLM 兜底检测)
    lang = detect_language(message, market=session.market, hint=lang_hint or session.language,
                           llm_detect=getattr(_llm, "detects_language", None))
    # 语言跟随用户:会话语言随最近一次检测更新(仅用于中性消息的兜底语言;当前消息语言始终由提示词优先)
    if lang:
        session.language = lang

    # 2a) 用户消息里明确说市场(如"印度市场")→ 该市场(manual)
    session._llm = _llm
    msg_market, _ = extract_market(message)
    if msg_market:
        explicit_market = msg_market
    # 2) 市场路由(开放):未指定→按语言推断;手动→粘性不随语言;自动→跟随语言
    market, market_source = route_market(lang, location, explicit=explicit_market,
                                         session_market=session.market,
                                         session_market_source=session.market_source)
    session.market = market
    session.market_source = market_source
    kb = KnowledgeBase(market_kb(market))

    if location and not session.collected.get("lat"):
        session.collected["lat"] = location.get("lat")
        session.collected["lng"] = location.get("lng")

    # 2.5) 内容安全:prompt injection 检测(§8.3)
    if compliance.is_prompt_injection(message):
        session.intent = "other"
        session.persist()
        return _response(session, {"reply": _p_inject(lang), "emotion": session.emotion_score,
                                   "intent": "other"}, lang, market, market_source)

    # 3) 一次 LLM 调用:respond(消息 + 知识上下文 + step状态 + 对话语言 + 最近对话)
    BUSINESS = {"product-inquiry", "dealer-lookup", "usage-guide", "emergency", "after-sales"}
    context = kb.context(message, lang, topk=3)
    # 经销商注入(脱敏:仅名称/地址/城市/距离;按坐标 nearest + 按门店名文本 match)
    dealer_part = kb.dealer_context(message, session.collected.get("lat"), session.collected.get("lng"))
    if dealer_part:
        context = (context + "\n" if context else "") + dealer_part
    conv_lang = session.language or lang
    history = _history_str(session.history)
    if session.intent:
        state_desc = card_mod.state_desc(session.intent, session.step_index, message, session, kb)
        r = _llm.respond(message, context, state_desc, conv_lang, history) if hasattr(_llm, "respond") else None
        if r:
            new_intent, conf, question, response = r
        else:
            new_intent, conf, _q, question = intent_mod.recognize(message, llm_confirm=None); response = ""
    else:
        # 首答:通用状态描述(让 LLM 判意图 + 回应/引导)
        state_desc = ("Determine the customer's intent. If it is a greeting or identity question, "
                      "introduce yourself and guide. If it is about AION UT (specs, dealers, usage, "
                      "service, emergency), answer using the facts, then guide.")
        r = _llm.respond(message, context, state_desc, conv_lang, history) if hasattr(_llm, "respond") else None
        if r:
            new_intent, conf, question, response = r
        else:
            new_intent, conf, _s, question = intent_mod.recognize(message, llm_confirm=None); response = ""

    new_intent = intent_mod.normalize_intent(new_intent)  # LLM 变体 → 规范意图
    # 紧急安全预检(强信号强制 emergency,覆盖 LLM 漏判)
    if intent_mod.emergency_hit(message):
        new_intent = "emergency"
        conf = max(conf, 0.95)
    # 兜底:respond 失败/空回复时给非空简短回复,避免"哑巴"
    if not response:
        response = _fallback_text(new_intent, lang)
    session._answer_llm = response
    session._question = question

    intent = session.intent
    if intent:
        # 已锁定:用户若跳到**另一个业务意图**则切换,避免死抠某一步
        if new_intent in BUSINESS and new_intent != intent:
            session.switch_intent(new_intent, conf)
        elif intent == "emergency" and not intent_mod.emergency_hit(message) and new_intent != "emergency":
            # 紧急卡对非紧急跟随消息不强粘:当前消息无紧急信号且 LLM 也未判紧急 → 按新意图退卡
            session.switch_intent(new_intent if new_intent in BUSINESS else "other", conf)
        reply = card_mod.run_card(session, message, kb, lang)
    else:
        # 关键词纠正:LLM 的 respond 意图不稳,当关键词给出**置信的业务意图**且与 LLM 不一致时,用关键词意图。
        _ki, _kc, _s, _q = intent_mod.recognize(message, llm_confirm=None)
        if _ki in BUSINESS and _kc >= config.INTENT_CONFIDENCE_THRESHOLD and _ki != new_intent:
            new_intent, conf, scores = _ki, _kc, {"kw": True}
        if new_intent in BUSINESS:
            session.lock_intent(new_intent, conf)
            reply = card_mod.run_card(session, message, kb, lang)
        elif new_intent == "other":
            # 打招呼/身份提问/闲聊:先回应介绍+引导
            session.lock_intent("other", conf)
            reply = card_mod.run_card(session, message, kb, lang)
        elif intent_mod.is_ambiguous(conf):
            session.clarify_rounds += 1
            choice = _intent_by_choice(message)
            if choice:
                session.lock_intent(choice, 0.9)
                reply = card_mod.run_card(session, message, kb, lang)
            elif session.clarify_rounds > config.CLARIFY_MAX_ROUNDS:
                session.escalated = True
                reply = {"reply": _p_clarify(lang), "emotion": session.emotion_score,
                         "intent": "other", "escalate": True, "escalate_reason": "clarify-timeout"}
            else:
                reply = {"reply": _p_clarify(lang), "emotion": session.emotion_score, "intent": None}
        else:
            session.lock_intent(new_intent, conf)
            reply = card_mod.run_card(session, message, kb, lang)

    # 4) 回复质量(禁AI感 + 去重 + 长度硬限) → 输出过滤(禁区)
    raw_reply = _polish_reply(reply.get("reply", ""), session.history)
    clean_reply, flagged = compliance.output_filter(raw_reply)
    if flagged:
        clean_reply = clean_reply + "\n\n(Please refer to official AION policy for confirmed terms.)"
    # 品牌合规兜底:回复里出现其它品牌/竞品/比品牌 → 替换为知识缺失+留资引导
    if compliance.find_competitor(clean_reply):
        reply["reply"] = _no_knowledge_lead(lang)
    else:
        reply["reply"] = clean_reply

    session.persist()
    session.append_turn(message, reply.get("reply", ""), session.intent, reply.get("emotion", 0))
    session.persist()

    # 5) 自建库落库(幂等):留资 / 转人工 / 救援工单
    try:
        db.init_db()
        if getattr(session, "lead_record", None):
            db.insert_lead(session.lead_record)
        if reply.get("escalate"):
            db.insert_escalation(session, reply.get("escalate_reason", "other"),
                                 reply.get("reply", "")[:500], reply.get("emotion", session.emotion_score))
        if getattr(session, "rescue_ticket_id", None):
            db.insert_rescue_ticket(session, market,
                                    session.collected.get("lat"), session.collected.get("lng"),
                                    reply.get("reply", "")[:300], consent=True)
    except Exception:
        pass  # 落库失败不阻塞对话流

    # 调试:记录整轮摘要
    debug.record(evt="turn", session=session.id, message=message[:120], lang=lang, market=market,
                 intent=session.intent, new_intent=new_intent, conf=round(float(conf or 0), 2),
                 step=session.step_index, reply_len=len(reply.get("reply", "")),
                 reply_head=(reply.get("reply") or "")[:80], escalated=bool(reply.get("escalate")),
                 target=bool(reply.get("target_reached", session.target_reached)))

    resp = _response(session, reply, lang, market, market_source)
    # 引用 & 合规留资记录
    resp["citations"] = list(reply.get("citations", []))
    # 只在真正收集到留资时才返回 consentVersion(避免纯问候也显示合规卡)
    lead_record = getattr(session, "lead_record", None)
    resp["consentVersion"] = compliance.CONSENT_VERSION if lead_record else None
    resp["leadRecord"] = lead_record
    return resp


def _fallback_text(intent, lang):
    """respond 失败/空时的兜底简短回复(按语言)。"""
    m = {"en": "I can help with AION UT. Could you rephrase, or ask about specs, dealers, or how to use it?",
         "zh": "我可以帮您解答 AION UT 的问题，麻烦再说一下？您可以问配置、经销商或使用方法。",
         "th": "ฉันช่วยเรื่อง AION UT ได้ รบกวนลองใหม่ หรือสอบถามสเปก ตัวแทนจำหน่าย หรือการใช้งาน",
         "es": "Puedo ayudarle con el AION UT. ¿Podría reformular? Pregunte por especificaciones, concesionarios o uso."}
    return m.get(lang, m["en"])


def _no_knowledge_lead(lang):
    """知识缺失/无对应配置时,禁推其它品牌 → 引导留资/联系专员(品牌合规兜底)。"""
    m = {"en": "AION UT is a 5-seat model and doesn't currently offer 6 seats. I don't have information "
               "beyond that here — may I connect you with an AION specialist, or could you leave your contact "
               "details for a follow-up?",
         "zh": "AION UT 是 5 座车型，目前没有 6 座版本。这边暂时没有更多信息——我可以帮您转接 AION 专员，"
               "或者您方便留个联系方式，让专员为您跟进吗？",
         "th": "AION UT เป็นรุ่น 5 ที่นั่ง และยังไม่มีรุ่น 6 ที่นั่งในตอนนี้ ตรงนี้ยังไม่มีข้อมูลเพิ่มเติม—"
               "ขอเชื่อมต่อคุณกับผู้เชี่ยวชาญ AION หรือฝากข้อมูลติดต่อเพื่อให้เจ้าหน้าที่ติดตามได้ไหม?",
         "es": "El AION UT es un modelo de 5 plazas y no ofrece 6 plazas actualmente. No tengo más información "
               "al respecto: ¿le conecto con un especialista de AION o podría dejar sus datos de contacto para un seguimiento?"}
    return m.get(lang, m["en"])


_SENT_SPLIT = r"(?<=[。！？!?])|(?<=\.)(?=\s|$)"   # 。！？!? 后即切; ASCII 句点仅在跟空格/结尾时切(避免拆小数 4.6)


def _sents(text):
    parts = re.split(_SENT_SPLIT, text or "")
    return [p.strip() for p in parts if p.strip()]


def _dedup_lead(new_text, history):
    """话术去重(售前框架参考):若新回复首句与近几轮 bot 回复首句几乎相同,裁掉重复的首句。"""
    if not new_text or not history:
        return new_text
    recent = [h.get("bot", "") for h in history[-3:] if h.get("bot")]
    if not recent:
        return new_text
    new_sents = _sents(new_text)
    if not new_sents or len(new_sents) < 2:
        return new_text
    for rbot in recent:
        r_sents = _sents(rbot)
        if not r_sents:
            continue
        n0, r0 = new_sents[0], r_sents[0]
        if n0 and r0 and difflib.SequenceMatcher(None, n0, r0).ratio() >= config.REPEAT_SIM_THRESHOLD:
            new_sents = new_sents[1:]   # 首句与前面重复,裁掉;保留后文实质内容
            break
    trimmed = " ".join(new_sents)
    return trimmed if trimmed.strip() else new_text


def _polish_reply(text, history):
    """回复质量后处理(单次 LLM 调用内完成,不额外调模型):
    ①禁 AI 感/机器感 ②话术去重 ③长度硬限。失败/空时原样返回。"""
    if not text:
        return text
    text, _ = compliance.strip_ai_phrases(text)
    text = _dedup_lead(text, history)
    return compliance.truncate_reply(text, config.REPLY_MAX_CHARS)


def _history_str(history, n=4):
    """把最近几轮对话格式化成字符串,给 LLM 连贯上下文。"""
    if not history:
        return ""
    lines = []
    for h in history[-n:]:
        lines.append(f"User: {h.get('user','')}\nBot: {h.get('bot','')}")
    return "\n".join(lines)


def _p_inject(lang):
    en = "I can only help with AION UT questions. Could you ask about specs, dealers, or how to use the vehicle?"
    th = "ฉันช่วยได้เฉพาะเรื่อง AION UT เท่านั้น — สเปก ตัวแทนจำหน่าย หรือการใช้งาน"
    es = "Solo puedo ayudar con el AION UT. Pregunte sobre especificaciones, concesionarios o cómo usar el vehículo."
    zh = "我只能帮您解答 AION UT 的问题。您可以问配置、经销商或如何使用车辆。"
    return {"en": en, "th": th, "es": es, "zh": zh}.get(lang, en)


def _p_clarify(lang):
    en_opts = "\n".join(f"  {letter}. {enlab}" for _, letter, enlab, _ in CLARIFY_OPTIONS)
    th_opts = "\n".join(f"  {letter}. {thlab}" for _, letter, _, thlab in CLARIFY_OPTIONS)
    es_label = {"product-inquiry": "Especificaciones / precio", "dealer-lookup": "Ubicación del concesionario",
                "usage-guide": "Cómo usarlo", "other": "Algo más"}
    es_opts = "\n".join(f"  {letter}. {es_label.get(i, lab)}" for i, letter, lab, _ in CLARIFY_OPTIONS)
    en = ("I want to make sure I help you correctly. Are you asking about:\n" + en_opts)
    th = ("เพื่อให้ช่วยถูกต้อง คุณกำลังถามเกี่ยวกับ:\n" + th_opts)
    es = ("Para asegurarme de ayudarle correctamente, ¿pregunta sobre:\n" + es_opts)
    zh_label = {"product-inquiry": "车型规格/价格", "dealer-lookup": "经销商位置",
                "usage-guide": "使用方法", "other": "其它事项"}
    zh_opts = "\n".join(f"  {l}. {zh_label.get(i, lab)}" for i, l, lab, _ in CLARIFY_OPTIONS)
    zh = ("为了确保我能正确地帮助您，请问您想咨询的是：\n" + zh_opts)
    return {"en": en, "th": th, "es": es, "zh": zh}.get(lang, en)
