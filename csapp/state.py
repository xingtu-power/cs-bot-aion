"""会话状态机(Phase 1)。

对 v0.3 §2.2 的会话状态对象建模 + 匿名会话 ID + 断点续聊。
每个会话一个 JSON 文件,支持任意(匿名)session id 恢复。
字段与设计文档状态对象对齐(含主/次意图、情绪分)。
"""
import json, os, time, uuid
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
        self.clarify_rounds = 0
        self.total_rounds = 0
        self.escalated = False
        self.resolved = False
        self.target_reached = False
        self.rescue_ticket_id = None
        self.anonymous = True
        self.lead_id = None
        self.created_at = now
        self.updated_at = now
        self.history = []                  # 每轮 [{user, bot, intent, score}]

    # ---- 序列化 ----
    def to_dict(self):
        # 排除下划线开头的瞬态属性(如 _llm、_question),避免不可序列化/污染状态
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}

    @classmethod
    def from_dict(cls, d):
        s = cls.__new__(cls)
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
        path = os.path.join(self.dir, f"{session_id}.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return Session.from_dict(json.load(f))
        return Session(session_id or new_session_id())

    def save(self, session):
        session.persist()
