"""咨询效果分析库(SQLite,零依赖)。

独立于 data/kd.db 与会话 JSON 的旁路分析存储:
  - a_sessions : 会话级事实(每轮 upsert 汇总)
  - a_turns    : 每轮结构化 trace(用户消息 → 模型回复中间流转)

写入侧只在 trace.flush() 调用(由 pipeline.chat() 包装触发),任何失败都被吞掉,
绝不影响对话主流程。读取侧只服务 /api/v1/admin/*(只读)。
"""
import hashlib, json, os, re, sqlite3, threading, time
from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS a_sessions (
  session_id   TEXT PRIMARY KEY,
  user_hash    TEXT,            -- userId sha1 前 12,可聚合不可反查
  market TEXT, language TEXT, intent_main TEXT,
  created_at TEXT, ended_at TEXT, ended_reason TEXT,
  total_rounds INTEGER, resolved INTEGER, escalated INTEGER,
  lead_captured INTEGER, avg_emotion REAL,
  flags_json TEXT, created_ts INTEGER, updated_ts INTEGER
);
CREATE TABLE IF NOT EXISTS a_turns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trace_id TEXT UNIQUE,
  session_id TEXT NOT NULL,
  round_idx  INTEGER NOT NULL,
  t TEXT, market TEXT, language TEXT, intent TEXT,
  user_msg TEXT, bot_reply TEXT,
  trace_json TEXT,              -- 单轮全量 trace
  issues TEXT,                  -- 逗号分隔问题信号码
  outcome TEXT, latency_ms INTEGER,
  ts INTEGER
);
CREATE INDEX IF NOT EXISTS idx_turn_sess  ON a_turns(session_id, round_idx);
CREATE INDEX IF NOT EXISTS idx_turn_ts    ON a_turns(ts);
CREATE INDEX IF NOT EXISTS idx_turn_issue ON a_turns(issues);
CREATE INDEX IF NOT EXISTS idx_sess_ts    ON a_sessions(created_ts);
CREATE INDEX IF NOT EXISTS idx_turn_market ON a_turns(market);
CREATE INDEX IF NOT EXISTS idx_turn_intent ON a_turns(intent);
"""

_ISSUES = ("llm_failed", "llm_retry", "llm_parse_fail", "empty_reply", "knowledge_gap",
           "vague_reply", "clarify_loop", "user_repeat", "escalated", "guard_injection",
           "guard_competitor", "guard_output_filter", "guard_model_facts", "slow",
           "no_progress")


def _conn():
    os.makedirs(os.path.dirname(config.ANALYTICS_DB), exist_ok=True)
    conn = sqlite3.connect(config.ANALYTICS_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


_io_lock = threading.Lock()
_inited = False


def init_db():
    """幂等建表(线程安全,只执行一次)。"""
    global _inited
    if _inited:
        return
    with _io_lock:
        if _inited:
            return
        c = _conn()
        try:
            c.executescript(SCHEMA)
            c.commit()
        finally:
            c.close()
        _inited = True


# ---------------- PII 掩码(入库前) ----------------
_RE_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,253}\.[A-Za-z]{2,24}")
_RE_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d\s.\-]{6,}\d)(?!\d)")


def mask_pii(text):
    """把邮箱与类手机号(≥8 位数字串)替换为掩码,入库前调用。"""
    if not text:
        return ""
    t = _RE_EMAIL.sub("***@***", str(text))
    t = _RE_PHONE.sub("***", t)
    return t


def user_hash(user_id):
    if not user_id:
        return None
    return hashlib.sha1(str(user_id).encode("utf-8")).hexdigest()[:12]


def trunc(s, n):
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


# ---------------- 写入 ----------------
def record_turn(rec):
    """写入一轮 trace + upsert 会话摘要。rec 为 trace.Recorder 导出 dict。"""
    if not rec or not rec.get("traceId") or not rec.get("sessionId"):
        return
    init_db()
    c = _conn()
    try:
        ts = rec.get("ts") or int(time.time())
        issues = rec.get("issues") or []
        issue_str = ",".join(sorted(set(issues)))
        with c:
            c.execute("""
                INSERT OR REPLACE INTO a_turns
                (trace_id, session_id, round_idx, t, market, language, intent,
                 user_msg, bot_reply, trace_json, issues, outcome, latency_ms, ts)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (rec.get("traceId"), rec.get("sessionId"), rec.get("round", 0),
                 rec.get("t"), rec.get("market"), rec.get("language"), rec.get("intent"),
                 trunc(rec.get("userMsg") or "", config.TRACE_MSG_MAX * 2),
                 trunc(rec.get("botReply") or "", config.TRACE_MSG_MAX * 2),
                 json.dumps(rec, ensure_ascii=False),
                 issue_str, json.dumps(rec.get("outcome") or {}),
                 rec.get("latencyMs"), ts))
            sess = rec.get("session") or {}
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            c.execute("""
                INSERT INTO a_sessions
                (session_id, user_hash, market, language, intent_main, created_at, ended_at,
                 ended_reason, total_rounds, resolved, escalated, lead_captured, avg_emotion,
                 flags_json, created_ts, updated_ts)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(session_id) DO UPDATE SET
                  market=excluded.market, language=excluded.language,
                  intent_main=CASE WHEN excluded.intent_main IS NOT NULL THEN excluded.intent_main
                                   ELSE a_sessions.intent_main END,
                  ended_at=excluded.ended_at, ended_reason=excluded.ended_reason,
                  total_rounds=excluded.total_rounds, resolved=excluded.resolved,
                  escalated=excluded.escalated, lead_captured=excluded.lead_captured,
                  avg_emotion=excluded.avg_emotion, flags_json=excluded.flags_json,
                  updated_ts=excluded.updated_ts""",
                (rec.get("sessionId"), user_hash(sess.get("userId")),
                 rec.get("market"), rec.get("language"), rec.get("intent"),
                 sess.get("createdAt") or now, sess.get("endedAt"),
                 sess.get("endedReason"), sess.get("totalRounds", rec.get("round", 0)),
                 1 if sess.get("resolved") else 0,
                 1 if (sess.get("escalated") or rec.get("outcome", {}).get("escalated")) else 0,
                 1 if sess.get("leadCaptured") else 0,
                 sess.get("avgEmotion", 0), json.dumps(sess.get("flags") or {}),
                 sess.get("createdTs") or ts, ts))
    except Exception:
        # 旁路:写失败绝不影响对话
        pass
    finally:
        c.close()


def cleanup(retention_days=None):
    """删除超过保留期的 trace/会话摘要。返回删除行数。"""
    retention_days = retention_days or config.ANALYTICS_RETENTION_DAYS
    cutoff = int(time.time()) - retention_days * 86400
    init_db()
    c = _conn()
    try:
        n1 = c.execute("DELETE FROM a_turns WHERE ts < ?", (cutoff,)).rowcount
        n2 = c.execute("DELETE FROM a_sessions WHERE updated_ts < ?", (cutoff,)).rowcount
        c.commit()
        return n1 + n2
    finally:
        c.close()


# ---------------- 读取(只读,服务 admin API) ----------------
def _iso_to_ts(s):
    try:
        import datetime as dt
        v = dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        return int(v.timestamp())
    except Exception:
        try:
            return int(s)
        except Exception:
            return None


def _filters(from_ts=None, to_ts=None, market=None, language=None, intent=None):
    """拼 a_turns 的 WHERE 子句与参数。from_ts/to_ts 为 ISO 或 epoch。"""
    where, args = [], []
    if from_ts:
        v = _iso_to_ts(from_ts)
        if v is not None:
            where.append("ts >= ?"); args.append(v)
    if to_ts:
        v = _iso_to_ts(to_ts)
        if v is not None:
            where.append("ts <= ?"); args.append(v)
    if market:
        where.append("market = ?"); args.append(str(market).upper())
    if language:
        where.append("language = ?"); args.append(str(language).lower())
    if intent:
        where.append("intent = ?"); args.append(str(intent).lower())
    return (" AND ".join(where), args) if where else ("1=1", [])


def _session_filters(from_ts=None, to_ts=None, market=None, language=None, intent=None,
                     issue=None, ended=None):
    where, args = ["1=1"], []
    if from_ts:
        v = _iso_to_ts(from_ts)
        if v is not None:
            where.append("a_sessions.updated_ts >= ?"); args.append(v)
    if to_ts:
        v = _iso_to_ts(to_ts)
        if v is not None:
            where.append("a_sessions.updated_ts <= ?"); args.append(v)
    if market:
        where.append("a_sessions.market = ?"); args.append(str(market).upper())
    if language:
        where.append("a_sessions.language = ?"); args.append(str(language).lower())
    if intent:
        where.append("a_sessions.intent_main = ?"); args.append(str(intent).lower())
    if ended is not None:
        if ended:
            where.append("a_sessions.ended_reason IS NOT NULL")
        else:
            where.append("a_sessions.ended_reason IS NULL")
    if issue:
        where.append("EXISTS (SELECT 1 FROM a_turns t2 WHERE t2.session_id = a_sessions.session_id"
                     " AND t2.issues LIKE ?)")
        args.append("%" + issue + "%")
    return " AND ".join(where), args


def summary(from_ts=None, to_ts=None, market=None, language=None, intent=None):
    """概览聚合:会话/轮次/结果/信号分布。"""
    init_db()
    c = _conn()
    try:
        wf, af = _filters(from_ts, to_ts, market, language, intent)
        row = c.execute(
            "SELECT COUNT(*) n, COUNT(DISTINCT session_id) sess, "
            "AVG(latency_ms) avg_ms FROM a_turns WHERE " + wf, af).fetchone()
        kpi = {"turns": row["n"], "sessions": row["sess"],
               "avgLatencyMs": round(row["avg_ms"] or 0)}
        # 分桶延迟(近似 p50/p95)
        lat = [r["latency_ms"] for r in c.execute(
            "SELECT latency_ms FROM a_turns WHERE latency_ms IS NOT NULL AND " + wf, af)]
        if lat:
            lat.sort()
            n = len(lat)
            kpi["latencyP50"] = lat[int(n * .5)]
            kpi["latencyP95"] = lat[min(n - 1, int(n * .95))]
            kpi["slowTurns"] = sum(1 for v in lat if v >= config.SLOW_MS)
        else:
            kpi["latencyP50"] = kpi["latencyP95"] = kpi["slowTurns"] = 0
        # 结果分布(按 round 数=1 的会话摘要近似:直接查 a_sessions)
        ws, as_ = _session_filters(from_ts, to_ts, market, language, intent)
        srow = c.execute("SELECT COUNT(*) n, SUM(resolved) resolved, SUM(escalated) escalated,"
                         " SUM(lead_captured) leads,"
                         " AVG(total_rounds) avg_rounds, AVG(avg_emotion) avg_emo"
                         " FROM a_sessions WHERE " + ws, as_).fetchone()
        kpi["sessionsEnded"] = c.execute("SELECT COUNT(*) n FROM a_sessions WHERE " + ws
                                         + " AND ended_reason IS NOT NULL", as_).fetchone()["n"]
        kpi["resolvedSessions"] = srow["resolved"] or 0
        kpi["escalatedSessions"] = srow["escalated"] or 0
        kpi["leadSessions"] = srow["leads"] or 0
        kpi["avgRounds"] = round(srow["avg_rounds"] or 0, 1)
        kpi["avgEmotion"] = round(srow["avg_emo"] or 0, 2)
        # 结束原因分布
        kpi["endReasons"] = [dict(r) for r in c.execute(
            "SELECT ended_reason r, COUNT(*) n FROM a_sessions WHERE " + ws
            + " AND ended_reason IS NOT NULL GROUP BY ended_reason ORDER BY n DESC", as_)]
        # 问题信号分布(a_turns)
        kpi["issues"] = {}
        for code in _ISSUES:
            kpi["issues"][code] = c.execute(
                "SELECT COUNT(*) n FROM a_turns WHERE issues LIKE ? AND " + wf,
                af + ["%" + code + "%"]).fetchone()["n"]
        # Top 知识缺口:信号含 knowledge_gap 的轮,按用户消息原文聚合
        kpi["topGaps"] = [dict(r) for r in c.execute(
            "SELECT intent, user_msg m, COUNT(*) n FROM a_turns WHERE issues LIKE '%knowledge_gap%'"
            " AND " + wf + " GROUP BY intent, user_msg ORDER BY n DESC LIMIT 15", af)]
        return kpi
    finally:
        c.close()


def list_sessions(from_ts=None, to_ts=None, market=None, language=None, intent=None,
                  issue=None, ended=None, q=None, page=1, size=20):
    init_db()
    c = _conn()
    try:
        ws, as_ = _session_filters(from_ts, to_ts, market, language, intent, issue, ended)
        # 文本搜索:命中任一 user 消息的会话
        if q:
            ws += " AND a_sessions.session_id IN (SELECT DISTINCT session_id FROM a_turns"
            ws += " WHERE user_msg LIKE ? OR bot_reply LIKE ?)"
            as_ += ["%" + q + "%", "%" + q + "%"]
        total = c.execute("SELECT COUNT(*) n FROM a_sessions WHERE " + ws, as_).fetchone()["n"]
        rows = c.execute(
            "SELECT a_sessions.*,"
            " (SELECT COUNT(*) FROM a_turns t WHERE t.session_id = a_sessions.session_id) turn_count,"
            " (SELECT GROUP_CONCAT(DISTINCT issues) FROM a_turns t WHERE t.session_id = a_sessions.session_id"
            "  AND issues != '') issue_all,"
            " (SELECT user_msg FROM a_turns t WHERE t.session_id = a_sessions.session_id"
            "  ORDER BY round_idx ASC LIMIT 1) first_msg,"
            " (SELECT bot_reply FROM a_turns t WHERE t.session_id = a_sessions.session_id"
            "  ORDER BY round_idx DESC LIMIT 1) last_reply"
            " FROM a_sessions WHERE " + ws + " ORDER BY a_sessions.updated_ts DESC LIMIT ? OFFSET ?",
            as_ + [size, (page - 1) * size]).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["issues"] = sorted({i for i in (d.pop("issue_all") or "").split(",") if i})
            d.pop("flags_json", None)
            out.append(d)
        return {"total": total, "page": page, "size": size, "sessions": out}
    finally:
        c.close()


def get_session(session_id):
    """会话详情:摘要 + 逐轮(含 trace_json)。"""
    init_db()
    c = _conn()
    try:
        s = c.execute("SELECT * FROM a_sessions WHERE session_id=?", (session_id,)).fetchone()
        if not s:
            return None
        meta = dict(s)
        meta.pop("flags_json", None)
        turns = []
        for t in c.execute("SELECT * FROM a_turns WHERE session_id=? ORDER BY round_idx ASC",
                           (session_id,)):
            d = dict(t)
            d["issues"] = [i for i in (d.get("issues") or "").split(",") if i]
            try:
                d["trace"] = json.loads(d.pop("trace_json") or "{}")
            except Exception:
                d["trace"] = {}
            turns.append(d)
        return {"session": meta, "turns": turns}
    finally:
        c.close()


def get_turn(trace_id):
    init_db()
    c = _conn()
    try:
        t = c.execute("SELECT * FROM a_turns WHERE trace_id=?", (trace_id,)).fetchone()
        if not t:
            return None
        d = dict(t)
        d["issues"] = [i for i in (d.get("issues") or "").split(",") if i]
        try:
            d["trace"] = json.loads(d.pop("trace_json") or "{}")
        except Exception:
            d["trace"] = {}
        return d
    finally:
        c.close()


def export_turns(from_ts=None, to_ts=None, market=None, language=None, intent=None, limit=5000):
    """导出过滤范围内的轮次明细(只读,给 admin CSV 导出)。"""
    init_db()
    c = _conn()
    try:
        wf, af = _filters(from_ts, to_ts, market, language, intent)
        rows = c.execute(
            "SELECT t, session_id, round_idx, market, language, intent, issues, latency_ms,"
            " user_msg, bot_reply FROM a_turns WHERE " + wf +
            " ORDER BY ts DESC LIMIT ?", af + [limit]).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()
