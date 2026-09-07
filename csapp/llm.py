"""LLM 抽象层(Phase 1 骨架)。

可插拔:rule | deepseek | none。规则/模板作为兜底,保证骨架无 key 也能跑通。
deepseek 提供用 dsh --profile headless 做语义确认/翻译(尽力而为,失败则回退规则)。
"""
import os, subprocess, json, re, time
from . import config, debug


class BaseLLM:
    mode = "none"

    def classify(self, text, context=""):
        """返回 (intent, confidence, question, answer) 或 None。"""
        return None

    def confirm_intent(self, text):
        """返回 (intent, confidence) 或 None。"""
        return None

    def detects_language(self, text):
        """返回语言代码或 None(LLM 兜底用)。"""
        return None

    def localize(self, text, lang):
        return text

    def generates_reply(self, text, context):
        return None


class RuleLLM(BaseLLM):
    mode = "rule"

    def classify(self, text, context=""):
        return None  # 规则模式不生成,交由关键词

    def confirm_intent(self, text):
        # 规则兜底:不覆盖关键词判断,返回 None 交由 intent.py 用关键词结果
        return None

    def detects_language(self, text):
        return None

    def generates_reply(self, text, context):
        return None


def _extract_json(text):
    """鲁棒地从 LLM 输出中提取第一个合法 JSON 对象。
    兼容 markdown ``` 围栏、前后缀说明、多余结尾;取 first valid JSON object。"""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[\w]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    start = t.find("{")
    if start == -1:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(t[start:])
        return obj
    except Exception:
        try:
            return json.loads(t[start:t.rfind("}") + 1])
        except Exception:
            return None


class DeepSeekLLM(BaseLLM):
    """通过 dsh --profile headless 调用大模型(尽力而为)。"""

    mode = "deepseek"

    @staticmethod
    def _load_key():
        # 优先环境变量,否则读 web 写入的凭据
        k = os.environ.get("DEEPSEEK_API_KEY", "")
        if k:
            return k
        cred = os.path.expanduser("~/.dsh/.credentials.yaml")
        try:
            c = open(cred).read()
            m = re.search(r'DEEPSEEK_API_KEY\s*[:=]\s*[\'"]?([^\'"\n#]+)', c)
            return m.group(1).strip() if m else ""
        except Exception:
            return ""

    _API = "https://api.deepseek.com/chat/completions"

    def _api_call(self, prompt, timeout=60):
        """直接调用 DeepSeek(OpenAI 兼容)API,免去每消息 boot agent 的开销(~0.7s)。"""
        import urllib.request
        key = self._load_key()
        body = json.dumps({"model": "deepseek-chat",
                           "messages": [{"role": "user", "content": prompt}],
                           "temperature": 0.3,
                           "max_tokens": 1024}).encode()
        req = urllib.request.Request(self._API, data=body,
                                     headers={"Authorization": "Bearer " + key,
                                              "Content-Type": "application/json"})
        r = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
        return r["choices"][0]["message"]["content"].strip()

    def _run(self, prompt, timeout=90):
        key = self._load_key()
        if not key:
            return None
        # 优先直接 API(快);失败重试 N 次(退避),再回退 dsh headless
        last_err = None
        for attempt in range(3):
            try:
                out = self._api_call(prompt, timeout=60)
                if out:
                    return out
            except Exception as e:
                last_err = e
                debug.record(evt="llm_api_retry", attempt=attempt + 1, err=str(e)[:120])
                time.sleep(0.6 * (attempt + 1))
        debug.record(evt="llm_api_failed", err=str(last_err)[:120])
        env = dict(os.environ)
        env["DSH_HOME"] = "/Users/mlhs/Documents/aigo/客服系统/tools/dsh_home"
        env["DEEPSEEK_API_KEY"] = key
        try:
            out = subprocess.run(["dsh", "--profile", "headless", prompt],
                                 capture_output=True, text=True, timeout=timeout, env=env)
            return out.stdout.strip() or None
        except Exception:
            return None

    def classify(self, text, context=""):
        """一次 LLM 调用,合并:意图 / 置信度 / 是否在提问 / (若提问)顺带生成回答。
        context: 本地化知识库上下文(RAG retrieval 结果)。
        返回 (intent, confidence, question, answer) 或 None。answer 仅当 question=true 时给出。"""
        prompt = ("You are an AION car customer-support bot. Given a customer message and some facts, "
                  "produce: (1) intent in [product-inquiry, dealer-lookup, usage-guide, emergency, after-sales, other]; "
                  "(2) question: whether the user is asking a question or making a request that deserves a direct answer, "
                  "vs merely answering the bot's prompt; "
                  "(3) if question is true, answer: write a concise, friendly answer in the user's language, "
                  "using ONLY the facts below (do not invent numbers/claims). If question is false, set answer to null.\n\n"
                  f"Facts:\n{context or '(none)'}\n\nMessage: {text}\n\n"
                  'Reply JSON {"intent":"...","confidence":0.0,"question":true|false,"answer":null|"..."} only.')
        out = self._run(prompt)
        if not out:
            return None
        try:
            d = _extract_json(out)
            if not d:
                return None
            return (d.get("intent"), float(d.get("confidence", 0.0)),
                    bool(d.get("question", True)), d.get("answer"))
        except Exception:
            return None

    def respond(self, message, context="", state_desc="", conv_lang="en", history=None):
        """每轮一次 LLM 调用:返回 {intent, confidence, question, response}。
        - state_desc: 当前卡片步骤的描述。
        - history: 最近对话(用于理解追问,避免重复介绍)。
        - 语言: 按用户消息语言回复;无语言则沿用 conv_lang。不写死语言码。"""
        prompt = (f"You are a GAC (广汽国际) customer-support bot serving the AION and Hyper brands.\n"
                  f"KNOWLEDGE-GAP POLICY (highest priority after Situation): For every intent, the Situation block below "
                  f"defines the required fallback when the FACTS do not cover what the customer asked. ALWAYS follow the "
                  f"Situation's fallback, never just say \"I do not have it\" without taking the next step the "
                  f"Situation prescribes. This applies to ALL intents (product-inquiry, dealer-lookup, usage-guide, "
                  f"emergency, after-sales, other).\n"
                  f"Situation: {state_desc or 'handle the customer message.'}\n"
                  f"Reply in {conv_lang} — the language the customer has selected. This is the ONLY language to use "
                  f"for your reply, regardless of the language the customer's message is written in and regardless of "
                  f"the conversation so far. Even if the customer writes in a different language, still reply in "
                  f"{conv_lang}. Do not switch language based on the message.\n"
                  f"Do NOT repeat things you already said in the conversation. Understand follow-up questions in context.\n"
                  f"Respond to what the customer actually said (answer a question, or continue guiding as needed). "
f"ANSWER FIRST, THEN GUIDE: if the customer asks a specific question (e.g. how to charge, the range, where a "
f"dealer is, how to do something), give a clear, detailed, SELF-CONTAINED text answer using the facts — include "
f"the steps, cautions and notes. Only a diagram is supplementary; the text must fully answer on its own. NEVER "
f"reply with a generic \"What would you like to know?\" / \"Please clarify\" / \"You can ask about specs, dealers "
"or usage\" for a clear question — answer what was asked. "
f"BRAND: you represent GAC (广汽国际), a parent brand; AION and Hyper are GAC sub-brands.\n"
f"POSITIVE-FRAMING: never volunteer a model's disadvantages. If the customer asks about downsides, frame them as "
f"design/positioning choices (e.g. a compact city car is designed for 1-2 occupants — 4+ means less space; the battery "
f"is sized to the price point), never list flaws or use 'disadvantage/缺点'.\n"
f"SPEC STANDARD: when you quote range/performance, report ALL standards present in the facts for the same model (e.g. "
f"AION UT WLTP 430km / NEDC 500km), each with its exact label (WLTP/NEDC/CLTC). Do NOT claim a value 'is not' another "
f"standard or deny a standard exists — the same car is quoted under different test cycles across markets. Only quote "
f"standards actually given in the facts; if the facts omit one, you do not need to mention it, but never say it doesn't "
f"exist.\n"
f"DEALER LIST: list ONLY the dealers present in the facts with their details; do NOT state a count that differs from "
f"the items you list, and do NOT leave empty numbered items.\n"
f"ANSWER THE EXACT QUESTION: give instructions/info ONLY for the exact thing asked. If the facts do not clearly cover "
f"that exact component/action (e.g. front hood vs tailgate), say you do not have that specific info rather than giving "
f"instructions for a different thing.\n"
f"Use ONLY the facts below; do not invent numbers or claims.\n"f"If the customer replies '好的'/'yes'/'OK' to confirm a topic you just offered or were explaining, "
f"immediately expand that topic using the facts (or ask which one if you offered several) — do NOT just "
f"acknowledge or change the subject.\n"
                  f"Dealers: if the facts list a dealer, you may share its name, address and phone number so the "
                  f"customer can contact it directly — this is public dealer info, provide it proactively when asked. "
                  f"Only reference dealers that actually appear in the facts; if the customer names a store that is NOT "
                  f"in the facts, say you do not have that store's details — never invent a dealer.\n"
                  f"Only recommend AION / GAC products. NEVER name, recommend, compare with, or send the customer "
                  f"to any other brand or competitor, and never say 'other brand' / 'another brand'. If the facts "
                  f"do not cover the customer's request, or AION does not offer what they asked: do NOT guess, do NOT "
                  f"cite other brands — politely say you don't have that information, and offer to connect them with an "
                  f"AION specialist or ask them to leave contact details for a follow-up.\n"
                  f"Stay within AION / GAC automotive topics. If the customer asks about something clearly unrelated "
                  f"to cars (e.g. weather, news, sports, cooking, personal life): do NOT answer it as a real topic. "
                  f"Briefly acknowledge it, then SMOOTHLY steer back to AION with a natural bridge and guide them into "
                  f"a relevant topic. For example, if they ask about the weather, respond like: 'Good weather is great "
                  f"for a family trip — the AION UT is spacious and comfortable; may I recommend a family-friendly AION "
                  f"model for you?' Keep the bridge natural and in the customer's language.\n"
                  f"Be concise and natural: keep the reply within about {config.REPLY_DAILY_MAX} characters, "
                  f"never exceed {config.REPLY_MAX_CHARS}.\n"
                  f"NEVER reveal that you are an AI, robot, assistant or language model; do not use phrases like "
                  f"'according to the search results', 'the system shows', 'based on the data'.\n"
                  f"If a previous reply already covered this, phrase it differently or answer more briefly — "
                  f"do not repeat the same sentences verbatim.\n"
                   f"BEFORE-SALES vs AFTER-SALES: before-sales = specs, price, finding a dealer, buying. after-sales = "
                   f"how to use / how to charge, a problem, service, emergency. For AFTER-SALES, SOLVE it and confirm it "
                   f"is resolved (or offer further troubleshooting / the hotline); do NOT offer a dealer visit, a test "
                   f"drive, a booking, or ask for contact details. For BEFORE-SALES, after answering you MAY offer to "
                   f"connect with a dealer / arrange a visit.\n"
                   f"MODEL ACCURACY (critical): quote ONLY values that literally appear in the facts (facts / MODEL "
                   f"LINEUP). The detailed spec facts (power, torque, battery kWh, dimensions, charging) belong "
                   f"ONLY to AION UT. For any model OTHER than AION UT (Y Plus, RT, N60, V, 昊铂GT, 昊铂HL, AION LX), "
                   f"the ONLY allowed data source is the MODEL LINEUP reference — NEVER apply AION UT's detailed "
                   f"specs (e.g. 150kW / 210N·m / 60kWh) to any other model. NEVER invent, infer, or fill in any "
                   f"model's range, battery, price, trim, feature, dimension, power, torque, or availability. Do NOT "
                   f"copy or transfer a spec from one model to another. Each model's values are ONLY those listed "
                   f"under that model's own name. Do NOT change the test-cycle label (keep 'CLTC' as CLTC). Do NOT "
                   f"mention any AION model not in the facts. If a requested spec is not in the facts, follow the "
                   f"KNOWLEDGE-GAP FALLBACK defined in the Situation block above (do NOT just say 'I do not have "
                   f"that information rather than guessing').\n\n")
        if history:
            prompt += f"Recent conversation:\n{history}\n\n"
        prompt += (f"Facts:\n{context or '(none)'}\n\nCustomer message: {message}\n\n"
                   'Reply JSON {"intent":"product-inquiry|dealer-lookup|usage-guide|emergency|after-sales|other","confidence":0.0,"question":true|false,"response":"..."} only.')
        debug.record(evt="llm_respond_input", message=message, conv_lang=conv_lang,
                     state_desc=(state_desc or "")[:160], ctx_len=len(context or ""),
                     hist_len=len(history or ""))
        out = self._run(prompt)
        debug.record(evt="llm_respond_raw", raw=(out or "")[:600])
        if not out:
            return None
        try:
            d = _extract_json(out)
            if not d:
                raise ValueError("no valid json")
            res = (d.get("intent"), float(d.get("confidence", 0.0)),
                   bool(d.get("question", True)), d.get("response") or "")
            debug.record(evt="llm_respond_ok", intent=res[0], conf=res[1], q=res[2],
                         resp_len=len(res[3] or ""))
            return res
        except Exception:
            debug.record(evt="llm_respond_parse_fail", raw=(out or "")[:300])
            return None

    def confirm_intent(self, text):
        """兼容旧接口:仅判意图/置信度/是否提问(不生成回答)。"""
        r = self.classify(text, "")
        return (r[0], r[1], r[2]) if r else None

    def detects_language(self, text):
        """LLM 检测任意语言(ISO 639-1),供其它语言兜底。失败返回 None。"""
        out = self._run("Detect the language of this customer message. "
                        "Reply ONLY the ISO 639-1 language code "
                        "(e.g., en, th, zh, ms, id, vi, fr, de, ar...). \nMessage: " + text)
        if out:
            m = re.search(r"\b([a-z]{2})\b", out.lower())
            return m.group(1) if m else None
        return None

    def translate(self, text):
        # 泰/英等翻译(预留;用 LLM)
        out = self._run(f"Translate to English, output only the translation:\n{text}")
        return out

    # 常用语言名(供 localize 提示);未知语言名直接传 code,LLM 通常能理解
    _LANG_LABEL = {"zh": "Simplified Chinese", "th": "Thai", "en": "English",
                   "ms": "Malay", "id": "Indonesian", "es": "Spanish"}

    def localize(self, text, lang):
        """把客服回复本地化成用户语言(保留所有事实/数字)。失败返回原文。"""
        label = self._LANG_LABEL.get(lang, lang)
        prompt = (f"Rewrite this customer-support reply in {label}. "
                  f"Keep all facts, figures, options and citations' meaning. "
                  f"Output ONLY the rewritten text, no explanation:\n{text}")
        out = self._run(prompt)
        return out or text

    def answer(self, question, context, lang="en"):
        """RAG 生成回答:用本地化知识库上下文,按用户语言生成,只基于给定事实。失败返回 None。"""
        label = self._LANG_LABEL.get(lang, lang)
        prompt = (f"You are an AION customer-support assistant. Answer the customer in {label}. "
                  f"Use ONLY the facts below; do not invent numbers or claims. Be concise and friendly.\n\n"
                  f"Facts:\n{context}\n\nCustomer question: {question}\n\nAnswer:")
        out = self._run(prompt)
        return (out or "").strip() or None


def get_llm(mode=None):
    mode = mode or config.LLM_MODE
    if mode == "deepseek":
        return DeepSeekLLM()
    if mode == "rule":
        return RuleLLM()
    return BaseLLM()
