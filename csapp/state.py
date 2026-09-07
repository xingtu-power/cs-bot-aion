"""会话状态机(Phase 1)。

对 v0.3 §2.2 的会话状态对象建模 + 匿名会话 ID + 断点续聊。
每个会话一个 JSON 文件,支持任意(匿名)session id 恢复。
字段与设计文档状态对象对齐(含主/次意图、情绪分)。
"""
import glob, json, os, time, uuid
from . import config


def new_session_id() -> str:
    return "sess_" + uuid.uuid4().hex[:12]


class Session:
    """一个有状态的会话。字段随轮次更新并持久化。"""

    def __init__(self, session_id=None):
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.id = session_id or new_session_id()
        self.market = None                 # THA|AU|null
        self.market_source = None          # auto|manual
        self.language = None               # th|en|zh|...
        self.intent = None                 # 当前锁定的意图
        self.intent_main = None
        self.intent_secondary = []
        self.confidence = 0.0              # 当前意图置信度
        self.emotion_score = 0             # 1-5 情绪分
        self.step_index = 0                # 引导卡步骤
        self.collected = {"phone": None, "email": None, "model": None, "concern": None}
        self.step_asks = {}              # 槽位追问计数(键:"intent:step",跨轮保留)
        self.clarify_rounds = 0
        self.total_rounds = 0
        self.escalated = False
        self.resolved = False
        self.target_reached = False
        self.rescue_ticket_id = None
        self.ask_confirm = False           # 是否正处在"确认是否转人工"状态
        self.escalate_reason = None        # 触发转人工的原因(澄清/槽位/情绪)
        self.ended = False                 # 会话是否已结束
        self.ended_at = None              # 结束时间戳
        self.ended_reason = None          # goal|escalated|goodbye|idle
        self.last_active = now            # 最近活动时间
        self.anonymous = True
        self.user_id = None             # 若关联到身份设备/宿主用户
        self.lead_id = None
        self.has_shown_lead_card = False   # UI: 本会话是否已展示过 lead card
        self.created_at = now
        self.updated_at = now
        self.history = []                  # 每轮 [{user, bot, intent, score}]

    # ---- 序列化 ----
    def to_dict(self):
        # 排除下划线开头的瞬态属性(如 _llm、_question),避免不可序列化/污染状态
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}

    @classmethod
    def from_dict(cls, d):
        # 以默认初始化补齐所有字段(兼容旧 schema:新增字段如 ended/ask_confirm 缺失时不报错),
        # 再用记录值覆盖。
        s = cls(d.get("id"))
        s.__dict__.update(d)
        return s

    def persist(self):
        self.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        path = os.path.join(config.STATE_DIR, f"{self.id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    def append_turn(self, user, bot, intent, emotion):
        self.history.append({
            "user": user, "bot": bot, "intent": intent,
            "emotion_score": emotion,
            "t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })

    # ---- 状态规则(来自设计 v0.3 §2.2)----
    def lock_intent(self, intent, confidence, secondary=None):
        """意图一旦确认,本会话除非明确切换否则不重新识别。"""
        self.intent = intent
        self.confidence = confidence
        self.intent_main = intent
        self.intent_secondary = secondary or []
        self.step_index = 0

    def switch_intent(self, intent, confidence):
        """用户明确切换意图:重置步骤与相关字段。"""
        self.intent = intent
        self.intent_main = intent
        self.confidence = confidence
        self.step_index = 0
        # 仅保留与新车意图无关的旧收集字段?此处骨架保留但不重置用户已给信息(更友好)
        self.clarify_rounds = 0

    def bump_round(self, emotion_score=None):
        self.total_rounds += 1
        if emotion_score is not None:
            self.emotion_score = emotion_score

    def touch(self):
        """刷新最近活动时间(活动即未空闲)。"""
        self.last_active = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def end(self, reason, ended_at=None):
        """标记会话结束(幂等)。会 persist 到磁盘。"""
        if self.ended:
            return False
        self.ended = True
        self.ended_reason = reason
        self.ended_at = ended_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.updated_at = self.ended_at
        self.persist()
        return True

    def should_escalate(self):
        # 转人工条件:轮数/澄清超限 / 用户要求 / 高情绪(在此判断情绪分)
        if self.emotion_score >= config.EMOTION_ESCALATE_SCORE:
            return True, "emotion-high"
        if self.clarify_rounds >= config.CLARIFY_MAX_ROUNDS:
            return True, "clarify-timeout"
        if self.total_rounds >= config.ESCAPE_INTENTS_CALLOUT:
            return True, "round-limit"
        return False, None


class StateStore:
    """按 session id 存取,支持断点续聊。"""

    def __init__(self, directory=None):
        self.dir = directory or config.STATE_DIR

    def get(self, session_id):
        # 短路:空 id 不读盘,直接返回新 Session(避免拼出 None.json 路径污染新会话)
        if not session_id:
            return Session()
        path = os.path.join(self.dir, f"{session_id}.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return Session.from_dict(json.load(f))
        return Session(session_id)

    def save(self, session):
        session.persist()

    def cleanup(self, archive_days):
        """定期清理:删除超过 archive_days 的会话文件(存档过期)。返回删除数。"""
        import glob, os, time as _t
        cutoff = _t.time() - archive_days * 86400
        n = 0
        for p in glob.glob(os.path.join(self.dir, "*.json")):
            try:
                if os.path.getmtime(p) < cutoff:
                    os.remove(p); n += 1
            except Exception:
                pass
        return n

    def list_for_user(self, user_id=None, limit=30):
        """列出会话摘要(按 user_id 过滤;user_id 为空返回最近 limit 条)。按 updated_at 倒序。"""
        rows = []
        for p in glob.glob(os.path.join(self.dir, "*.json")):
            try:
                with open(p, encoding="utf-8") as f:
                    d = json.load(f)
            except Exception:
                continue
            if user_id and d.get("user_id") != user_id:
                continue
            first = ""
            last_user_msg = ""
            last_bot_msg = ""
            for t in d.get("history", []):
                if t.get("user"):
                    first = first or t["user"]
                    last_user_msg = t["user"]
                if t.get("bot"):
                    last_bot_msg = t["bot"]
            # 最后一条消息:同轮内 bot 后写,所以优先 bot(时序最后);fallback 到 last user
            if last_bot_msg:
                last_message = last_bot_msg; last_message_role = "bot"
            elif last_user_msg:
                last_message = last_user_msg; last_message_role = "user"
            elif first:
                last_message = first; last_message_role = "user"
            last_message = (last_message[:60] + "…") if len(last_message) > 60 else last_message
            rows.append({
                "sessionId": d.get("id"),
                "userId": d.get("user_id"),
                "title": (first[:40] if first else (d.get("intent") or "new session")),
                "lastMessage": last_message,
                "lastMessageRole": last_message_role,
                "market": d.get("market"),
                "language": d.get("language"),
                "intent": d.get("intent"),
                "ended": bool(d.get("ended")),
                "endedReason": d.get("ended_reason"),
                "createdAt": d.get("created_at"),
                "updatedAt": d.get("updated_at") or d.get("created_at") or "",
                "totalRounds": d.get("total_rounds", len(d.get("history", []))),
            })
        rows.sort(key=lambda r: r["updatedAt"], reverse=True)
        return rows[:limit]

    def get_session(self, session_id):
        """返回单个会话 dict(含 history),供前端恢复显示;不存在或空 id 返回 None。"""
        if not session_id:
            return None
        path = os.path.join(self.dir, f"{session_id}.json")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def mark_ended(self, session_id, reason):
        """接受外部结束(如用户关闭):加载会话→end(reason)→persist。返回 ended 是否新置位。"""
        if not session_id:
            return False
        s = self.get(session_id)
        if not s:
            return False
        changed = s.end(reason)
        if changed:
            s.persist()
        return changed

    def cleanup_none_files(self):
        """启动时清理磁盘上残留的 None.json / 空 id 命名的脏文件(防御性)。"""
        n = 0
        for p in glob.glob(os.path.join(self.dir, "None*.json")):
            try:
                os.remove(p); n += 1
            except Exception:
                pass
        return n

    def idle_mark(self, ttl):
        """后台巡检:把超过 ttl 秒未活动且未结束的会话标为 idle。返回标记数量。"""
        import datetime as dt
        now = dt.datetime.now(dt.timezone.utc)
        n = 0
        for p in glob.iglob(os.path.join(self.dir, "*.json")):
            try:
                with open(p, encoding="utf-8") as f:
                    d = json.load(f)
                if d.get("ended"):
                    continue
                la = d.get("last_active")
                if not la:
                    continue
                t = dt.datetime.strptime(la, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
                if (now - t).total_seconds() > ttl:
                    s = Session.from_dict(d)
                    s.end("idle")
                    s.persist()
                    n += 1
            except Exception:
                continue
        return n
