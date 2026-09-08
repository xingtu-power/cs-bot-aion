"""回填存量会话到分析库(一次性工具)。

用法:python3 tools/backfill_traces.py [--days 90]

来源:data/sessions/*.json(会话 JSON,仅含最终文本/意图层)。
说明:存量数据没有内部阶段记录,回填的 trace 为**部分 trace**(steps 为空、
      标记 partial),用于历史效果统计;只有新采集(改造后)的轮次才有完整链路。
"""
import json, os, sys, time, glob, uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from csapp import config, analytics  # noqa: E402

STATE_DIR = config.STATE_DIR
BACKFILL_DAYS = 90  # 只回填最近 N 天(旧的没必要)


def _ts_of(iso):
    try:
        import datetime as dt
        v = dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        return int(v.timestamp())
    except Exception:
        return int(time.time())


def main():
    cutoff = time.time() - BACKFILL_DAYS * 86400
    files = glob.glob(os.path.join(STATE_DIR, "*.json"))
    if not files:
        print("无可回填会话:", STATE_DIR)
        return
    n_sess = n_turns = 0
    for fp in files:
        if os.path.getmtime(fp) < cutoff:
            continue
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        sid = d.get("id")
        if not sid:
            continue
        hist = d.get("history") or []
        base = {
            "sessionId": sid,
            "market": d.get("market"),
            "language": d.get("language"),
            "intent": d.get("intent"),
            "issues": [],
            "llm": {"callCount": 0, "retried": False, "failed": False,
                    "parseFail": False, "rawOutput": None, "promptLen": 0},
            "steps": [{"stage": "partial", "detail": {"note": "存量回填,无内部过程记录"},
                       "ms": None}],
            "latencyMs": 0,
            "session": {
                "userId": d.get("user_id"),
                "createdAt": d.get("created_at"),
                "endedAt": d.get("ended_at"),
                "endedReason": d.get("ended_reason"),
                "totalRounds": d.get("total_rounds", len(hist)),
                "resolved": bool(d.get("resolved")),
                "escalated": bool(d.get("escalated")),
                "leadCaptured": bool(d.get("lead_id") or d.get("lead_record")),
                "avgEmotion": float(d.get("emotion_score") or 0),
            },
        }
        for i, t in enumerate(hist, start=1):
            if not (t.get("user") or t.get("bot")):
                continue
            rec = dict(base)
            rec.update({
                "traceId": "trc_bkf_" + uuid.uuid4().hex[:10],
                "round": i,
                "t": t.get("t") or d.get("created_at"),
                "ts": _ts_of(t.get("t") or d.get("created_at")),
                "userMsg": analytics.mask_pii(t.get("user") or ""),
                "botReply": analytics.mask_pii(t.get("bot") or ""),
                "intent": t.get("intent") or d.get("intent"),
                "outcome": {"ended": bool(d.get("ended")), "endedReason": d.get("ended_reason"),
                            "escalated": bool(d.get("escalated")),
                            "emotionScore": t.get("emotion_score", 0)},
            })
            analytics.record_turn(rec)
            n_turns += 1
        n_sess += 1
    print(f"回填完成:会话 {n_sess} 个,轮次 {n_turns} 条 → {config.ANALYTICS_DB}")


if __name__ == "__main__":
    main()
