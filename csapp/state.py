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

    def append_turn(self, user, bot, intent, emotion, components=None):
        """追加一轮会话记录。components: 同轮 bot 消息里出现的 UI 卡片 schema 列表
        (lead_input / lead_confirm 等),用于重开会话时一并回放。"""
        self.history.append({
            "user": user, "bot": bot, "intent": intent,
            "emotion_score": emotion,
            "components": components or [],
            "t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })

    def append_components(self, components, intent=None):
        """追加一条纯 components 的 history(无 user/bot 文本)。

        用于 /api/v1/lead 提交后写回服务端返回的 leadConfirm 卡,
        确保重开会话时 lead 确认卡仍然渲染。"""
        self.history.append({
            "user": "", "bot": "", "intent": intent,
            "emotion_score": 0,
            "components": components or [],
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
    """按 session id 存取,支持断点续聊。

    进程级摘要缓存:
      list_for_user() 以前每次都 glob+open 所有 JSON 文件,N 次磁盘 IO。
      现所有 StateStore 实例共享一份摘要缓存,以"目录 signature"
      (文件数 + 所有 mtime 总和) 作为失效键。
      任一会话被 save() 后 signature 必然变化 → 缓存失效,下次 list 重扫。

    注: 缓存用模块级全局 _SHARE_CACHE,跨实例共享。
        实际生产环境若有多进程或多目录,自然隔离 (每个进程的 StateStore
        会先扫自己的目录,数据无交叉)。
    """

    # 模块级共享缓存 — 所有 StateStore 实例共享
    _SHARED_CACHE = {}        # dirpath → {sid → summary dict (+ _mtime_ns)}
    _SHARED_SIG = {}          # dirpath → (n_files, total_mtime_ns)

    def __init__(self, directory=None):
        self.dir = directory or config.STATE_DIR

    @classmethod
    def _dir_signature(cls, dirpath):
        """目录级 signature:文件数 + 所有 .json 文件 mtime_ns 之和。
        任何写入都会让 mtime 改变,自然失效。比 per-file mtime track 省事。"""
        sig = cls._SHARED_SIG.get(dirpath)
        # 廉价 fast-path:文件数够小(< N=threshold),即使算 sig 也便宜
        # 改用每次算 sig,但只算一次 (server 单进程反复请求,目录稳定)
        n = 0; ts = 0
        try:
            for name in os.listdir(dirpath):
                if not name.endswith(".json") or name.startswith("None"):
                    continue
                try:
                    ts += os.stat(os.path.join(dirpath, name)).st_mtime_ns
                    n += 1
                except OSError:
                    continue
        except OSError:
            pass
        sig = (n, ts)
        cls._SHARED_SIG[dirpath] = sig
        return sig

    @staticmethod
    def _summary_for(path, mtime_ns):
        """open 1 个文件 + 提取轻量摘要字段。"""
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            return None
        history = d.get("history", [])
        first = ""
        last_user_msg = ""
        last_bot_msg = ""
        for t in history:
            if t.get("user"):
                first = first or t["user"]
                last_user_msg = t["user"]
            if t.get("bot"):
                last_bot_msg = t["bot"]
        if last_bot_msg:
            last_message, last_message_role = last_bot_msg, "bot"
        elif last_user_msg:
            last_message, last_message_role = last_user_msg, "user"
        elif first:
            last_message, last_message_role = first, "user"
        else:
            last_message, last_message_role = "", "user"
        if len(last_message) > 60:
            last_message = last_message[:60] + "…"
        return {
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
            "totalRounds": d.get("total_rounds", len(history)),
            "_mtime_ns": mtime_ns,         # 内部用,strip_meta 时去掉
        }

    @staticmethod
    def _strip_meta(s):
        return {k: v for k, v in s.items() if not k.startswith("_")}

    def _ensure_index(self):
        """惰性重建摘要索引。仅当 signature 改变时扫盘。"""
        sig = self._dir_signature(self.dir)
        cache_pool = self._SHARED_CACHE
        cache = cache_pool.get(self.dir)
        if cache is not None:
            cached_sig = self._SHARED_SIG.get((self.dir, "cached"))
            if cached_sig == sig:
                return
        # 重建/增量更新缓存
        new_cache = {}
        try:
            for name in os.listdir(self.dir):
                if not name.endswith(".json") or name.startswith("None"):
                    continue
                sid = name[:-5]
                path = os.path.join(self.dir, name)
                try:
                    mtime_ns = os.stat(path).st_mtime_ns
                except OSError:
                    continue
                cached = (cache or {}).get(sid)
                if cached and cached.get("_mtime_ns") == mtime_ns:
                    new_cache[sid] = cached     # mtime 未变 → 复用旧 summary (省 json.load)
                    continue
                s = self._summary_for(path, mtime_ns)
                if s:
                    new_cache[sid] = s
        except OSError:
            pass
        cache_pool[self.dir] = new_cache
        # 记录当前 cache 的 signature
        self._SHARED_SIG[(self.dir, "cached")] = sig

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
        """列出会话摘要(按 user_id 过滤;user_id 为空返回最近 limit 条)。按 updated_at 倒序。

        性能:首次调用扫全部文件 + 建摘要缓存;后续命中缓存,
        仅当目录 signature 变化(有新会话被 save)时才增量或全量重扫。
        旧实现每次 glob+open 所有 .json — 97 个文件 ≈ 3.9s,
        缓存命中后 < 20ms。
        """
        self._ensure_index()
        cache_pool = self._SHARED_CACHE
        rows = [self._strip_meta(s) for s in cache_pool.get(self.dir, {}).values()]
        if user_id:
            rows = [r for r in rows if r.get("userId") == user_id]
        rows.sort(key=lambda r: r.get("updatedAt") or "", reverse=True)
        return rows[:limit]

    def save(self, session):
        session.persist()
        # 写盘后让共享缓存 signature 失效,下次 list_for_user 重新扫描
        self._SHARED_SIG.pop(self.dir, None)
        self._SHARED_SIG.pop((self.dir, "cached"), None)

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
