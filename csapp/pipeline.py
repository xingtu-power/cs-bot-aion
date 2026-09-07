"""端到端对话管道(Phase 1 骨架)。

一次 chat turn 的编排:会话恢复 → 语言检测 → 市场路由 → 意图识别(二段式)
→ 澄清/锁定 → 引导卡执行 → 持久化 → 返回响应。
"""
import re, difflib, time
from . import config, intent as intent_mod, cards as card_mod, compliance, debug, components as comp_mod
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


def chat(session_id=None, message=None, location=None, explicit_market=None, lang_hint=None, user_id=None):
    """一次对话轮。message 必填。返回响应 dict(与 API 对齐)。"""
    message = (message or "").strip()
    session = _store.get(session_id)
    if user_id:
        session.user_id = user_id  # 关联身份(设备/宿主用户)
    # 会话结束/空闲:刷新活动时间;若已超空闲期未结束 → 标记 idle 结束并重置为新一轮
    if not session.ended and session.last_active:
        try:
            import datetime as _dt
            _li = _dt.datetime.strptime(session.last_active, "%Y-%m-%dT%H:%M:%SZ") \
                       .replace(tzinfo=_dt.timezone.utc).timestamp()
        except Exception:
            _li = time.time()
        if time.time() - _li > config.TTL_RECENT:
            session.ended = True; session.ended_reason = "idle"; session.ended_at = session.last_active
            _reset_conversation(session)
            session.persist()
    session.touch()

    # 1) 语言检测(用于市场路由;回复语言由下拉框 lang_hint 决定)
    lang = detect_language(message, market=session.market, hint=lang_hint or session.language,
                           llm_detect=getattr(_llm, "detects_language", None))
    # 回复语言 = 下拉框所选(lang_hint);无下拉才回落到本次消息检测语言(不跟上次语言)
    reply_lang = lang_hint or lang
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

    # 1.5) 早期采集联系方式:任何阶段用户留了 phone/email 都先存到 session
    card_mod.try_collect_contact_early(session, message)

    # 2.5) 内容安全:prompt injection 检测(§8.3)
    if compliance.is_prompt_injection(message):
        session.intent = "other"
        session.persist()
        return _response(session, {"reply": _p_inject(reply_lang), "emotion": session.emotion_score,
                                   "intent": "other"}, reply_lang, market, market_source)

    # 3) 一次 LLM 调用:respond(消息 + 知识上下文 + step状态 + 对话语言 + 最近对话)
    BUSINESS = {"product-inquiry", "dealer-lookup", "usage-guide", "emergency", "after-sales"}
    context = kb.context(message, reply_lang, topk=3)
    # 经销商注入(脱敏:仅名称/地址/城市/距离;按坐标 nearest + 按门店名文本 match)
    dealer_part = kb.dealer_context(message, session.collected.get("lat"), session.collected.get("lng"), lang=reply_lang)
    if dealer_part:
        context = (context + "\n" if context else "") + dealer_part
    conv_lang = reply_lang
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
        state_desc = ("Determine the customer's intent and respond accordingly in ONE answer.\n"
                      "- Greeting / identity question: introduce yourself and guide.\n"
                      "- BEFORE-SALES (specs, features, price, finding a dealer, buying): answer the question in "
                      "detail using the facts, and you MAY gently offer to connect them with a dealer or arrange a "
                      "visit.\n"
                      "- AFTER-SALES (how to use / how to charge, a problem, service, emergency/rescue): SOLVE it in "
                      "detail using the facts — steps, cautions, notes. Then ask if it is resolved (or offer further "
                      "troubleshooting / the AION hotline). Do NOT offer a dealer visit, a test drive, a booking, or "
                      "ask for contact details — that is an unwanted sales push.\n"
                      "ALWAYS answer the specific question directly and in detail first; do NOT reply with a generic "
                      "'what would you like to know?'")
        r = _llm.respond(message, context, state_desc, conv_lang, history) if hasattr(_llm, "respond") else None
        if r:
            new_intent, conf, question, response = r
        else:
            new_intent, conf, _s, question = intent_mod.recognize(message, llm_confirm=None); response = ""

    raw_new_intent = new_intent  # LLM 原始意图(可能是自造标签)
    new_intent = intent_mod.normalize_intent(new_intent)  # LLM 变体 → 规范意图
    # 延续/确认标签:保留会话当前业务意图,避免降级成 other(否则"好的"后接不上话题)
    if intent_mod.is_continuation(raw_new_intent) and session.intent in BUSINESS:
        new_intent = session.intent
    # 紧急安全预检(强信号强制 emergency,覆盖 LLM 漏判)
    if intent_mod.emergency_hit(message):
        new_intent = "emergency"
        conf = max(conf, 0.95)
    # 纯问候兜底:LLM 偶发把问候误判成业务意图(如 after-sales),强校正回 other
    if intent_mod.greeting(message):
        new_intent = "other"
        conf = max(conf, 0.7)
    # 兜底:respond 失败/空回复时给非空简短回复,避免"哑巴"
    if not response:
        # Phase 2.4: fallback 也走知识缺失兜底(若意图是售前类) → 引导留资
        _fb = _fallback_text(new_intent, reply_lang)
        if new_intent in intent_mod.KNOWLEDGE_GAP_INTENTS:
            _fb += "\n\n" + (_knowledge_gap_lead(new_intent, reply_lang) or "")
            _phase24_gap = True   # 标记 Phase 2.4 path 也"追加了兜底"
        else:
            _phase24_gap = False
        response = _fb
    else:
        _phase24_gap = False
    session._answer_llm = response
    session._question = question

    _pre_intent = session.intent          # 锁定前的意图(用于判断是否"首轮")
    intent = session.intent
    if intent:
        # 已锁定:用户若跳到**另一个业务意图**则切换,避免死抠某一步
        if new_intent in BUSINESS and new_intent != intent:
            session.switch_intent(new_intent, conf)
        elif intent == "emergency" and not intent_mod.emergency_hit(message) and new_intent != "emergency":
            # 紧急卡对非紧急跟随消息不强粘:当前消息无紧急信号且 LLM 也未判紧急 → 按新意图退卡
            session.switch_intent(new_intent if new_intent in BUSINESS else "other", conf)
        reply = card_mod.run_card(session, message, kb, reply_lang)
    else:
        # 关键词纠正:LLM 的 respond 意图不稳,当关键词给出**置信的业务意图**且与 LLM 不一致时,用关键词意图。
        _ki, _kc, _s, _q = intent_mod.recognize(message, llm_confirm=None)
        if _ki in BUSINESS and _kc >= config.INTENT_CONFIDENCE_THRESHOLD and _ki != new_intent:
            new_intent, conf, scores = _ki, _kc, {"kw": True}
        if new_intent in BUSINESS:
            session.lock_intent(new_intent, conf)
            reply = card_mod.run_card(session, message, kb, reply_lang)
        elif new_intent == "other":
            # 打招呼/身份提问/闲聊:先回应介绍+引导
            session.lock_intent("other", conf)
            reply = card_mod.run_card(session, message, kb, reply_lang)
        elif intent_mod.is_ambiguous(conf):
            session.clarify_rounds += 1
            choice = _intent_by_choice(message)
            if choice:
                session.lock_intent(choice, 0.9)
                reply = card_mod.run_card(session, message, kb, reply_lang)
            elif session.clarify_rounds > config.CLARIFY_MAX_ROUNDS:
                # 先确认再转人工
                if not session.ask_confirm:
                    session.ask_confirm = True; session.escalate_reason = "clarify"
                    reply = {"reply": card_mod._nat(session, reply_lang, "confirm"), "emotion": session.emotion_score,
                             "intent": None, "confirm_escalate": True}
                elif card_mod._is_confirm(message):
                    session.escalated = True; session.ask_confirm = False; session.escalate_reason = None
                    reply = {"reply": _p_clarify(reply_lang), "emotion": session.emotion_score,
                             "intent": "other", "escalate": True, "escalate_reason": "clarify-timeout"}
                else:
                    session.ask_confirm = False; session.escalate_reason = None
                    reply = {"reply": _p_clarify(reply_lang), "emotion": session.emotion_score, "intent": None}
            else:
                reply = {"reply": _p_clarify(reply_lang), "emotion": session.emotion_score, "intent": None}
        else:
            session.lock_intent(new_intent, conf)
            reply = card_mod.run_card(session, message, kb, reply_lang)

    # 收紧版A: 首轮 + 业务意图 + 回复为空泛反问(未实际作答) → 二次调用强制作答一次,
    # 避免"麻烦再说一下 / 你想了解什么"这类空泛话。仅这一种罕见情况才多调一次。
    if (not _pre_intent) and session.intent in BUSINESS \
            and compliance.is_vague_reply(str(reply.get("reply", ""))) \
            and hasattr(_llm, "respond"):
        _strict_desc = (card_mod.state_desc(session.intent, session.step_index, message, session, kb)
                        + " ANSWER the customer's question directly and completely. Do NOT reply with a generic "
                          "'what would you like to know?' and do NOT ask the customer to clarify.")
        _r2 = _llm.respond(message, context, _strict_desc, reply_lang, history)
        if _r2 and len(_r2) > 3 and _r2[3]:
            session._answer_llm = _r2[3]
            session._question = _r2[2] if len(_r2) > 2 else session._question
            reply["reply"] = _r2[3]
            debug.record(evt="vague_reply_retried", intent=session.intent,
                         new=reply.get("reply", "")[:100])

    # 3.9) 情绪≥阈值 → 先确认再转人工
    # 先持久化本回合计算的情绪分(卡片/澄清路径已 score_emotion),否则 session.emotion_score 恒为 0,≥阈值永不触发
    if reply.get("emotion", 0) >= session.emotion_score:
        session.emotion_score = reply.get("emotion", 0)
    if not session.ask_confirm and session.emotion_score >= config.EMOTION_ESCALATE_SCORE \
            and not reply.get("escalate") and session.intent:
        session.ask_confirm = True; session.escalate_reason = "emotion"
        reply["reply"] = card_mod._nat(session, reply_lang, "confirm"); reply["confirm_escalate"] = True
        reply["emotion"] = session.emotion_score
    elif session.ask_confirm and session.escalate_reason == "emotion":
        if card_mod._is_confirm(message):
            session.escalated = True
            reply["escalate"] = True; reply["escalate_reason"] = "emotion"
            reply["reply"] = card_mod._nat(session, reply_lang, "handoff")
        session.ask_confirm = False; session.escalate_reason = None

    # 4) 回复质量(禁AI感 + 去重 + 长度硬限) → 输出过滤(禁区)
    raw_reply = _polish_reply(reply.get("reply", ""), session.history)
    clean_reply, flagged = compliance.output_filter(raw_reply)
    if flagged:
        clean_reply = clean_reply + "\n\n(Please refer to official AION policy for confirmed terms.)"
    # 品牌合规兜底:回复里出现其它品牌/竞品/比品牌 → 替换为知识缺失+留资引导
    if compliance.find_competitor(clean_reply):
        reply["reply"] = _no_knowledge_lead(reply_lang)
    else:
        # 车型防臆造:非 UT 车型不得出现其未提供的功率/扭矩/电池容量等参数(Phase 4.1 多语言)
        reply["reply"] = compliance.guard_model_facts(clean_reply, lang=reply_lang)

    # 4.5) Phase 2.3: 后处理知识缺失兜底 — 命中信号且未引导留资时,追加 _knowledge_gap_lead
    _new_reply, _kg_appended = _apply_knowledge_gap_lead(
        reply.get("reply", ""), session.intent, reply_lang, market)
    if _kg_appended:
        reply["reply"] = _new_reply
        debug.record(evt="knowledge_gap_lead_appended", intent=session.intent, lang=reply_lang)

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

    # 会话结束判定:达成终态 / 转人工 / 用户告别 / 无进展 → 标记 ended + 对话内提示
    if not session.ended:
        reason = None
        if session.target_reached:
            reason = "goal"
        elif session.escalated:
            reason = "escalated"
        elif re.search(config.GOODBYE_RE, message, re.I):
            reason = "goodbye"
        elif session.clarify_rounds >= config.NO_PROGRESS_MAX_CLARIFY:
            reason = "no_progress"   # 连续 ≥2 轮澄清/无业务进展 → 不再纠缠
        if reason:
            session.end(reason)
    if session.ended:
        notice = config.END_NOTICE.get(reply_lang, config.END_NOTICE["en"])
        reply["reply"] = (reply.get("reply", "") or "") + "\n\n" + notice
        reply["ended"] = True
        reply["ended_reason"] = session.ended_reason

    resp = _response(session, reply, reply_lang, market, market_source)
    resp["ended"] = bool(reply.get("ended"))
    resp["ended_reason"] = reply.get("ended_reason")
    # 引用来源(文字):检索到的文档/FAQ 来源(文档名+页码),供前端"引用"展示
    resp["citations"] = kb.citations() if kb else []
    # 回答配图:仅在"有必要"时展示真实插图(操作/步骤/救援意图),规格/经销商/政策等不配图
    resp["answer_images"] = kb.answer_images() if (kb and session.intent in config.ANSWER_IMAGE_INTENTS) else []
    # 只在真正收集到留资时才返回 consentVersion(避免纯问候也显示合规卡)
    lead_record = getattr(session, "lead_record", None)
    resp["consentVersion"] = compliance.CONSENT_VERSION if lead_record else None
    resp["leadRecord"] = lead_record
    # ----------- Lead Card 装配(components schema 给前端渲染) -----------
    # 触发: (a) 知识缺失兜底已追加 (b) lead_record 刚生成 (c) 售前/经销商首次 contact 步
    #       (d) 本会话/历史已采到联系方式但还没给过确认卡
    try:
        _first_contact = bool(
            session.intent in intent_mod.KNOWLEDGE_GAP_INTENTS
            and session.step_index == 0
            and lead_record is None
            and not (_kg_appended or _phase24_gap)
        )
        _has_shown_lead_card = getattr(session, "has_shown_lead_card", False)
        # 提前解析一次联系方式,用于触发判断
        _phone, _email, _ = comp_mod._resolve_lead_contact(
            session, db_history=lambda uid, mkt: db.find_recent_lead_by_user(uid, mkt)
        )
        _collected_contact = bool(_phone or _email)
        if comp_mod.should_attach_lead_card(
            intent=session.intent,
            kg_appended=bool(_kg_appended or _phase24_gap),
            lead_record_present=bool(lead_record),
            first_contact_step=_first_contact,
            collected_contact=_collected_contact,
            has_shown_lead_card=_has_shown_lead_card,
        ):
            card = comp_mod._build_lead_card(
                session, reply_lang, market,
                db_history=lambda uid, mkt: db.find_recent_lead_by_user(uid, mkt),
            )
            if card:
                resp["components"] = [card]
                session.has_shown_lead_card = True
                debug.record(evt="lead_card_attached", session=session.id,
                             type=card.get("type"), source=card.get("source", "input"))
            else:
                resp["components"] = []
        else:
            resp["components"] = []
    except Exception as _e:
        debug.record(evt="lead_card_error", err=str(_e)[:200])
        resp["components"] = []
    return resp


def _fallback_text(intent, lang):
    """respond 失败/空时的兜底简短回复(按语言)。"""
    m = {"en": "I can help with AION UT. Could you rephrase, or ask about specs, dealers, or how to use it?",
         "zh": "我可以帮您解答 AION UT 的问题，麻烦再说一下？您可以问配置、经销商或使用方法。",
         "th": "ฉันช่วยเรื่อง AION UT ได้ รบกวนลองใหม่ หรือสอบถามสเปก ตัวแทนจำหน่าย หรือการใช้งาน",
         "es": "Puedo ayudarle con el AION UT. ¿Podría reformular? Pregunte por especificaciones, concesionarios o uso."}
    return m.get(lang, m["en"])


# ---------------- Phase 2.2: 知识缺失兜底话术(售前类引导留资) ----------------
_KNOWLEDGE_GAP_LEAD = {
    "product-inquiry": {
        "zh": "针对该市场的具体在售车型和价格，我们这边暂时没有更详细的信息。为了给您更准确的回复，我可以帮您联系当地 AION 专员，您方便留个手机或邮箱，让专员按当地实际为您跟进吗?",
        "en": "I do not have the exact on-sale models and pricing for your market here. To give you an accurate answer, may I connect you with a local AION specialist? If you share your phone or email, they can follow up with the right local information.",
        "th": "ดิฉันไม่มีข้อมูลรุ่นและราคาที่วางจำหน่ายในพื้นที่ของคุณ ขอเชื่อมต่อคุณกับผู้เชี่ยวชาญ AION ในท้องถิ่น หากคุณฝากเบอร์โทรหรืออีเมล เจ้าหน้าที่จะติดตามด้วยข้อมูลที่ถูกต้องให้ครับ/ค่ะ",
        "es": "No tengo aquí la lista exacta de modelos y precios disponibles en su mercado. Para darle una respuesta precisa, ¿puedo ponerle en contacto con un especialista local de AION? Si comparte su teléfono o correo, el equipo local le dará la información correcta.",
    },
    "dealer-lookup": {
        "zh": "针对您所在区域的授权经销商名单，我们这边暂时没有更详细的信息。为了帮您找到最近的门店，我可以为您对接当地 AION 专员，您方便留个手机或邮箱，专员按当地门店为您跟进吗?",
        "en": "I do not have the exact list of authorized dealers for your area here. To help you find the nearest store, may I connect you with a local AION specialist? If you share your phone or email, the local team will follow up with the dealer details.",
        "th": "ดิฉันไม่มีรายชื่อตัวแทนจำหน่ายที่ได้รับอนุญาตในพื้นที่ของคุณ ขอเชื่อมต่อคุณกับผู้เชี่ยวชาญ AION ในท้องถิ่น หากคุณฝากเบอร์โทรหรืออีเมล เจ้าหน้าที่จะส่งรายชื่อตัวแทนจำหน่ายที่ใกล้คุณให้ครับ/ค่ะ",
        "es": "No tengo aquí la lista exacta de concesionarios autorizados en su zona. Para ayudarle a encontrar la tienda más cercana, ¿puedo ponerle en contacto con un especialista local de AION? Si comparte su teléfono o correo, el equipo local le enviará los detalles del concesionario.",
    },
    "after-sales": {
        "zh": "针对您当地的具体服务流程/配件库存/预约时段，我们这边暂时没有更详细信息。为了给您更准确的安排，我可以为您对接当地 AION 服务中心，您方便留个手机或邮箱，让当地服务专员为您跟进吗?",
        "en": "I do not have the exact local service procedure, parts availability or appointment slots here. To give you an accurate arrangement, may I connect you with a local AION service center? If you share your phone or email, the local service team will follow up.",
        "th": "ดิฉันไม่มีข้อมูลขั้นตอนการบริการ/อะไหล่/ช่วงเวลานัดหมายในพื้นที่ของคุณ ขอเชื่อมต่อคุณกับศูนย์บริการ AION ในท้องถิ่น หากคุณฝากเบอร์โทรหรืออีเมล เจ้าหน้าที่บริการจะติดตามให้ครับ/ค่ะ",
        "es": "No tengo aquí el procedimiento exacto de servicio, disponibilidad de recambios ni horarios de cita en su zona. Para darle una atención precisa, ¿puedo ponerle en contacto con un centro de servicio AION local? Si comparte su teléfono o correo, el equipo local le hará el seguimiento.",
    },
    # usage-guide / emergency 不补留资引导:售后类走 hotline,紧急救援走 rescue
}


def _knowledge_gap_lead(intent, lang, market=None):
    """知识缺失兜底话术。按意图返回 4 语言模板;售前类引导留资,售后/紧急返回 None。"""
    if not intent or intent not in intent_mod.KNOWLEDGE_GAP_INTENTS:
        return None
    table = _KNOWLEDGE_GAP_LEAD.get(intent, {})
    return table.get((lang or "en").lower(), table.get("en"))


def _apply_knowledge_gap_lead(reply_text, intent, lang, market=None):
    """Phase 2.3: 后处理兜底 — 若 LLM 回复命中知识缺失信号且还没引导留资,追加兜底话术。
    返回 (new_text, appended_flag)。"""
    if not reply_text or not intent or intent not in intent_mod.KNOWLEDGE_GAP_INTENTS:
        return reply_text, False
    if not intent_mod.is_knowledge_gap(reply_text, lang):
        return reply_text, False
    if intent_mod.already_pitching_lead(reply_text):
        return reply_text, False   # LLM 已经引导了,避免重复追加
    lead = _knowledge_gap_lead(intent, lang, market)
    if not lead:
        return reply_text, False
    return (reply_text.rstrip() + "\n\n" + lead), True


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


def _reset_conversation(session):
    """会话结束/空闲后,重置为新一轮(保留 id/user_id/market/language)。"""
    session.intent = None; session.intent_main = None; session.intent_secondary = []
    session.step_index = 0
    session.collected = {"phone": None, "email": None, "model": None, "concern": None}
    session.step_asks = {}; session.clarify_rounds = 0; session.total_rounds = 0
    session.escalated = False; session.resolved = False; session.target_reached = False
    session.ask_confirm = False; session.escalate_reason = None; session.emotion_score = 0
    session.lead_id = None; session.rescue_ticket_id = None; session.lead_record = None
    session.has_shown_lead_card = False
    session.history = []


def _history_str(history, n=4):
    """把最近几轮对话格式化成字符串,给 LLM 连贯上下文。"""
    if not history:
        return ""
    lines = []
    for h in history[-n:]:
        lines.append(f"User: {h.get('user','')}\nBot: {h.get('bot','')}")
    return "\n".join(lines)


def _p_inject(reply_lang):
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
