"""每轮对话 trace 采集(旁路,零依赖)。

服务模型:ThreadingHTTPServer 每请求一线程,pipeline.chat() 同步执行,
故用**线程本地缓冲**承载当前轮采集器,子模块(llm/cards)无需改签名即可追加事件。

流程:
  pipeline.chat()(包装)  → trace.begin()            建立当前线程 recorder
  pipeline/llm/cards     → trace.step()/mark_llm()  追加阶段事件/LLM 细节
  pipeline.chat() 包装    → trace.flush(resp)        汇集会话/响应信息 → analytics.record_turn()
任何异常都被吞掉,绝不影响对话主流程。
"""
import difflib, threading, time, uuid
from . import analytics, config, debug

_local = threading.local()


def new_trace_id():
    return "trc_" + uuid.uuid4().hex[:12]


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Recorder:
    """一轮对话的采集器。字段均为可 JSON 化的基础类型。"""

    def __init__(self, session_id=None, message=""):
        self.traceId = new_trace_id()
        self.session_id = session_id
        self.round = None
        self.t = _now_iso()
        self.ts = int(time.time())
        self._t0 = time.time()
        self.user_msg = message or ""
        self.market = None
        self.language = None
        self.intent = None
        self.steps = []              # [{stage, detail, ms}]
        self.llm = {"callCount": 0, "retried": False, "failed": False,
                    "parseFail": False, "rawOutput": None, "promptLen": 0}
        self.issues = []             # 问题信号码
        self.latency = {}            # {"total": ms, ...}
        self.session = None          # 内部引用(flush 时读取状态,不序列化)
        self._flushed = False

    # ---- 阶段记录 ----
    def step(self, stage, detail=None, ms=None):
        try:
            self.steps.append({"stage": stage, "detail": detail or {}, "ms": ms})
        except Exception:
            pass

    def issue(self, code, detail=None):
        if code not in self.issues:
            self.issues.append(code)
        if detail:
            self.step("issue:" + code, {"note": str(detail)[:200]})

    def llm_call(self, ms=None):
        self.llm["callCount"] += 1
        self.latency.setdefault("llm", 0)
        if ms:
            self.latency["llm"] += ms

    def to_dict(self):
        """导出给 analytics.record_turn 的纯 dict(不含内部引用)。"""
        sess = self.session
        d = {
            "traceId": self.traceId, "sessionId": self.session_id or (sess.id if sess else None),
            "round": self.round, "t": self.t, "ts": self.ts,
            "userMsg": analytics.mask_pii(self.user_msg),
            "botReply": None, "market": self.market, "language": self.language,
            "intent": self.intent,
            "steps": self.steps, "llm": self.llm,
            "issues": sorted(set(self.issues)),
            "latencyMs": int(self.latency.get("total", 0)),
            "outcome": {},
        }
        # 会话级状态(写 a_sessions 摘要用)
        if sess is not None:
            _hist = getattr(sess, "history", None) or []
            _emos = [float(h.get("emotion_score", 0) or 0) for h in _hist
                     if h.get("emotion_score") is not None]
            d["session"] = {
                "userId": getattr(sess, "user_id", None),
                "createdAt": getattr(sess, "created_at", None),
                "endedAt": getattr(sess, "ended_at", None),
                "endedReason": getattr(sess, "ended_reason", None),
                "totalRounds": len(_hist) or getattr(sess, "total_rounds", 0),
                "resolved": bool(getattr(sess, "resolved", False)),
                "escalated": bool(getattr(sess, "escalated", False)),
                "leadCaptured": bool(getattr(sess, "lead_record", None) or getattr(sess, "lead_id", None)),
                "avgEmotion": round(sum(_emos) / len(_emos), 2) if _emos else 0,
            }
        return d


def begin(session_id=None, message=""):
    """开启当前线程的采集器,返回 recorder。已存在则复用(防御嵌套)。"""
    r = current()
    if r is None:
        r = Recorder(session_id=session_id, message=message)
        _local.r = r
    return r


def current():
    return getattr(_local, "r", None)


def step(stage, detail=None, ms=None):
    r = current()
    if r:
        r.step(stage, detail, ms)


def issue(code, detail=None):
    r = current()
    if r:
        r.issue(code, detail)


def llm_call(ms=None):
    r = current()
    if r:
        r.llm_call(ms)


def mark_llm(**kw):
    """llm.py 内部补充字段(retried/failed/parseFail/rawOutput/promptLen)。"""
    r = current()
    if not r:
        return
    for k, v in kw.items():
        if k in r.llm:
            r.llm[k] = v
        elif k == "rawOutput":
            r.llm["rawOutput"] = v
    if kw.get("rawOutput") is not None:
        r.llm["rawOutput"] = analytics.trunc(
            analytics.mask_pii(kw.get("rawOutput")), config.TRACE_RAW_MAX)
    if kw.get("retried"):
        r.llm["retried"] = True
        r.issue("llm_retry")
    if kw.get("failed"):
        r.llm["failed"] = True
        r.issue("llm_failed")
    if kw.get("parseFail"):
        r.llm["parseFail"] = True
        r.issue("llm_parse_fail")


def finish(resp):
    """由 flush 调用:从响应 dict + 会话对象补全本轮终态字段。"""
    r = current()
    if not r or r._flushed:
        return
    r._flushed = True
    sess = r.session
    if resp:
        r.market = resp.get("market")
        r.language = resp.get("language")
        r.intent = resp.get("intent")
        r.botReply = analytics.mask_pii(resp.get("reply") or "")
        out = r.to_dict()["outcome"]
        out.update({
            "ended": bool(resp.get("ended")),
            "endedReason": resp.get("ended_reason"),
            "escalated": bool(resp.get("escalate")),
            "escalateReason": resp.get("escalate_reason"),
            "targetReached": bool(resp.get("targetReached")),
            "emotionScore": resp.get("emotionScore"),
            "citationCount": len(resp.get("citations") or []),
            "answerImageCount": len(resp.get("answer_images") or []),
            "componentTypes": [c.get("type") for c in (resp.get("components") or []) if c],
        })
        # 由响应推导的问题信号
        if out["escalated"] and out.get("escalateReason"):
            r.issue("escalated", out.get("escalateReason"))
        if out.get("endedReason") == "no_progress":
            r.issue("no_progress")
    # 会话级信号(消息文本与延迟)
    if sess:
        r.round = len(getattr(sess, "history", None) or []) or 0
        # user_repeat:与本会话前面的用户消息近似重复 → 首答疑似未解决
        hist = getattr(sess, "history", None) or []
        prev = [h.get("user", "") for h in hist[:-1] if h.get("user")]
        msg = (r.user_msg or "").strip()
        if len(msg) >= 8 and prev:
            best = max((difflib.SequenceMatcher(None, msg, p).ratio() for p in prev), default=0)
            if best >= 0.88:
                r.issue("user_repeat", round(best, 2))
        if sess.total_rounds >= 2 and getattr(sess, "clarify_rounds", 0) >= 2:
            r.issue("clarify_loop")
    r.latency["total"] = int((time.time() - r._t0) * 1000)
    if r.latency.get("total", 0) >= config.SLOW_MS:
        r.issue("slow")
    # 把终态 issues 里 llm_failed 衍生 empty_reply 等情况归类(以 steps 已有为准,不重复加)


def flush(resp):
    """结束本轮:补全终态 → 落分析库 → 清理线程缓冲。旁路,任何异常都不抛出。"""
    try:
        finish(resp)
        r = current()
        if r is None:
            return
        _local.r = None
        analytics.record_turn(r.to_dict())
    except Exception as e:
        try:
            debug.record(evt="trace_flush_err", err=str(e)[:200])
        except Exception:
            pass
