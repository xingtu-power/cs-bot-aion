"""全局配置(Phase 1 骨架)。

集中管理路径、阈值、规则。生产环境应从环境变量/配置文件加载,
此处先用常量并支持环境变量覆盖。
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB_ROOT = os.path.join(ROOT, "kb")
STATE_DIR = os.path.join(ROOT, "data", "sessions")   # 会话持久化目录(改到项目 data 下)

os.makedirs(STATE_DIR, exist_ok=True)

# ---- 市场 ----
MARKETS = ["AU", "THA"]
DEFAULT_MARKET = "AU"

# ---- 意图识别阈值 ----
INTENT_CONFIDENCE_THRESHOLD = 0.6   # 低于此值进入澄清
CLARIFY_MAX_ROUNDS = 3              # 澄清轮数上限,超限先确认再转人工
ESCAPE_INTENTS_CALLOUT = 1000       # 轮数硬上限(防御粘住/刷量)

# ---- 情绪 ----
EMOTION_ESCALATE_SCORE = 4          # 情绪分 >= 4 转人工
EMOTION_ESCALATE_CONSECUTIVE = 3    # 连续两轮 >= 3 也转人工
EMOTION_GAP = 3                     # 紧急意图下 >=3 即转人工

# ---- 回复质量护栏(售前通用框架参考) ----
REPLY_DAILY_MAX = int(os.environ.get("CSAPP_REPLY_DAILY_MAX", "220"))   # 日常建议长度(提示词目标)
REPLY_MAX_CHARS = int(os.environ.get("CSAPP_REPLY_MAX_CHARS", "300"))   # 绝对上限(硬截断)
REPEAT_SIM_THRESHOLD = 0.80          # 与近几轮 bot 回复相似度 > 此值判重复

# ---- 槽位收集护栏 ----
SLOT_MAX_ASK = 4                     # 单槽位追问上限,超限先确认再转人工(避免反复要/轻易切)

# ---- 回答配图 ----
# 仅在"有必要"时展示示意图:操作/步骤类(usage-guide)与救援(emergency)
# 才返回 answer_image;规格/经销商/售后政策/闲聊等其他回答不配图。
ANSWER_IMAGE_INTENTS = {"usage-guide", "emergency"}

# ---- 会话结束策略 ----
TTL_RECENT = int(os.environ.get("CSAPP_TTL_RECENT", "1800"))     # 空闲多少秒视为过期(30分钟)
ARCHIVE_DAYS = int(os.environ.get("CSAPP_ARCHIVE_DAYS", "90"))   # 存档保留多少天(3个月)后清理
END_NOTICE = {"zh": "本次咨询已结束，感谢使用。如需继续请开启新会话。",
              "en": "This session has ended. Thank you. Please start a new conversation to continue.",
              "th": "สิ้นสุดการสนทนานี้แล้ว ขอบคุณ หากต้องการต่อ กรุณาเริ่มบทสนทนาใหม่",
              "es": "Esta sesión ha finalizado. Gracias. Para continuar, inicie una nueva conversación."}
GOODBYE_RE = r"(再见|拜拜|结束会话|会话结束|告别|sayonara|\bbye\b|\bgoodbye\b|感谢|谢谢|结束)"

# ---- 语言检测 ----
# 脚本 -> 语言 的强启发式(覆盖目标市场):泰文脚本->th,中文->zh,拉丁->en/其它,
# 再结合市场上下文修正。
SUPPORTED_LANGS = ["th", "en", "zh"]

# ---- 引导卡 ----
MAX_STEPS = 12                      # 单卡最大引导步骤
LEAD_PHONE_TH = r"^09\d{8}$"        # 泰 09 开头 10 位
LEAD_PHONE_AU = r"^04\d{8}$"        # 澳 04 开头 10 位
LINE_EMAIL = r"@"                    # 邮箱含 @ 即视为有效

# ---- 存储语言(内部统一) ----
STORAGE_LANG = "en"

# ---- LLM 抽象 ----
# deepseek: 用大模型语义确认(准确率高,~6s/次);无 key 时自动回退关键词。
# 想快速/无 key 运行可设 CSAPP_LLM_MODE=rule。
LLM_MODE = os.environ.get("CSAPP_LLM_MODE", "deepseek")  # rule|deepseek|none
