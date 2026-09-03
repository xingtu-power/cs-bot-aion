"""情绪评分(Phase 1 骨架,对应设计 §2.3 量化规则)。

基于 信号词/语气/内容风险 加权计算 1-5 情绪分。
"""
import re

# 情绪词(多语言)
ANGER_WORDS = ["angry", "furious", "terrible", "useless", "complaint", "hate",
               "愤怒", "投诉", "太差", "垃圾", "气死",
               "โกรธ", "แย่มาก", "ไม่พอใจ"]
RISK_WORDS = ["accident", "trapped", "not safe", "danger", "cannot move", "stuck",
              "stranded", "blocked", "hurt", "injury",
              "事故", "被困", "危险", "无法移动",
              "อุบัติเหต", "ติด", "อันตราย"]
EMERGENCY_HINT = ["emergency", "breakdown", "won't start", "rescue",
                  "救援", "故障", "抛锚", "ฉุกเฉิน", "รถเสีย"]


def score_emotion(text: str, intent: str = None) -> int:
    lower = text.lower()
    s = 0
    for w in ANGER_WORDS:
        if w in lower:
            s += 1
            break
    for w in RISK_WORDS:
        if w in lower:
            s += 2
            break
    # 语气:全大写或连续感叹号
    if text.isupper() and len(text) > 6:
        s += 1
    if re.search(r"[!！]{2,}", text):
        s += 1
    # 紧急意图加权
    if intent == "emergency" or any(w in lower for w in EMERGENCY_HINT):
        s = int(s * 1.5)
    return min(5, max(0, s))
