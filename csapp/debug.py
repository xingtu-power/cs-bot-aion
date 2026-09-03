"""调试日志(默认开启)。记录每轮对话的详细处理,便于排查空回复/误判等问题。

- 默认 DEBUG(CSAPP_DEBUG=1);设为 0 关闭。
- 记录到 data/debug.jsonl(append),每条一个 JSON。
- 提供 read() 供 /api/v1/debug 返回最近日志。
"""
import os, json, time, threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "data", "debug.jsonl")
ENABLED = os.environ.get("CSAPP_DEBUG", "1") != "0"
_lock = threading.Lock()


def record(**obj):
    if not ENABLED:
        return
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with _lock:
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **obj},
                                   ensure_ascii=False) + "\n")
    except Exception:
        pass


def read(n=60):
    if not os.path.exists(LOG):
        return []
    lines = open(LOG, encoding="utf-8").read().splitlines()
    return [json.loads(l) for l in lines[-n:] if l.strip()]
