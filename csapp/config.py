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
CLARIFY_MAX_ROUNDS = 2              # 澄清轮数上限,超限转人工
ESCAPE_INTENTS_CALLOUT = 1000       # 轮数硬上限(防御粘住/刷量)

# ---- 情绪 ----
EMOTION_ESCALATE_SCORE = 4          # 情绪分 >= 4 转人工
EMOTION_ESCALATE_CONSECUTIVE = 3    # 连续两轮 >= 3 也转人工
EMOTION_GAP = 3                     # 紧急意图下 >=3 即转人工

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
