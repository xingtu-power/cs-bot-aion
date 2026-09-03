"""自建库(SQLite)—— 设计 §6.3。零依赖,文件库。

表:leads / escalations / rescue_tickets(及 retentions 元数据)。
提供插入(幂等)+ 查询;PII 最小化 + dedupe_key。
生产可平滑换 Postgres(迁移同 Schema)。PII 加密列为 TODO(见 §7.3)。
"""
import os, json, sqlite3, time
from . import config

DB_PATH = os.path.join(config.ROOT, "data", "kd.db")


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  market TEXT NOT NULL,
  channel TEXT NOT NULL DEFAULT 'web',
  intent TEXT NOT NULL,
  name TEXT, phone TEXT, email TEXT,
  consent_at TEXT, consent_version TEXT,
  status TEXT DEFAULT 'new',
  raw_json TEXT, dedupe_key TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(dedupe_key, market)
);
CREATE TABLE IF NOT EXISTS escalations (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  emotion_score INTEGER,
  context_summary TEXT,
  timeline_json TEXT, collected_json TEXT,
  status TEXT DEFAULT 'open',
  handler TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rescue_tickets (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  market TEXT NOT NULL,
  channel TEXT NOT NULL DEFAULT 'chat',
  lat REAL, lng REAL,
  vehicle_model TEXT, plate TEXT,
  issue_summary TEXT,
  user_consent INTEGER NOT NULL,
  status TEXT DEFAULT 'open', created_at TEXT NOT NULL
);
"""

DB = None  # 模块级记录上次插入,便于幂等


def init_db():
    c = _conn()
    c.executescript(SCHEMA)
    c.commit()
    c.close()


# ---------- leads ----------
def insert_lead(lead_rec):
    """幂等:按 (dedupe_key, market) 去重;已存在则不重复插入。"""
    if not lead_rec or not lead_rec.get("leadId"):
        return None
    c = _conn()
    try:
        cur = c.execute("""
            INSERT OR IGNORE INTO leads
            (id, session_id, market, channel, intent, name, phone, email,
             consent_at, consent_version, status, raw_json, dedupe_key, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?, 'new', ?, ?, ?)""",
            (lead_rec["leadId"], lead_rec.get("sessionId"), lead_rec.get("market"),
             lead_rec.get("channel", "web"), lead_rec.get("intent"),
             lead_rec.get("name"), lead_rec.get("phone"), lead_rec.get("email"),
             lead_rec.get("consentAt"), lead_rec.get("consentVersion"),
             json.dumps(lead_rec, ensure_ascii=False),
             lead_rec.get("dedupeKey"), lead_rec.get("createdAt", _now())))
        c.commit()
        inserted = cur.rowcount > 0
    finally:
        c.close()
    return inserted


# ---------- escalations ----------
def insert_escalation(session, reason, context_summary, emotion_score):
    c = _conn()
    try:
        # 幂等:同 session 同 reason 只记一次
        ex = c.execute("SELECT id FROM escalations WHERE session_id=? AND reason=?",
                       (session.id, reason)).fetchone()
        if ex:
            return ex["id"]
        eid = "tkt_" + session.id[-8:] + "_" + reason[:6]
        c.execute("""
            INSERT OR IGNORE INTO escalations
            (id, session_id, reason, emotion_score, context_summary,
             timeline_json, collected_json, status, created_at)
            VALUES (?,?,?,?,?,?,?, 'open', ?)""",
            (eid, session.id, reason, emotion_score, context_summary,
             json.dumps(session.history, ensure_ascii=False)[:4000],
             json.dumps(session.collected, ensure_ascii=False), _now()))
        c.commit()
        c.close()
        return eid
    except sqlite3.IntegrityError:
        c.close()
        return None


# ---------- rescue tickets ----------
def insert_rescue_ticket(session, market, lat, lng, issue_summary, consent):
    c = _conn()
    try:
        ex = c.execute("SELECT id FROM rescue_tickets WHERE session_id=?",
                       (session.id,)).fetchone()
        if ex:
            return ex["id"]
        rid = session.rescue_ticket_id or ("rsq_" + session.id[-8:])
        session.rescue_ticket_id = rid
        c.execute("""
            INSERT OR IGNORE INTO rescue_tickets
            (id, session_id, market, channel, lat, lng, vehicle_model, plate,
             issue_summary, user_consent, status, created_at)
            VALUES (?,?,?, 'chat', ?,?,?,?,?,?, 'open', ?)""",
            (rid, session.id, market, lat, lng,
             session.collected.get("model"), None,
             issue_summary, 1 if consent else 0, _now()))
        c.commit()
        c.close()
        return rid
    except sqlite3.IntegrityError:
        c.close()
        return None


# ---------- 查询/报表 ----------
def count(table):
    c = _conn()
    n = c.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
    c.close()
    return n


def list_leads(status=None, limit=20):
    c = _conn()
    q = "SELECT * FROM leads"
    if status:
        q += " WHERE status=?"
    q += " ORDER BY created_at DESC LIMIT ?"
    rows = c.execute(q, (status, limit) if status else (limit,)).fetchall()
    c.close()
    return [dict(r) for r in rows]
