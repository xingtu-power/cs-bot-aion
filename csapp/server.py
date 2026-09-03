"""零依赖 HTTP 服务(Phase 1 骨架,基于标准库 http.server)。

实现设计 §6.2 的 /api/v1/{chat,lead,escalate} 最小可用接口。
启动:python -m csapp.server --port 8000
生产可换用 FastAPI(见 api.py,需 pip 安装 fastapi/uvicorn)。
"""
import json, time, os
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from . import pipeline, db


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def _file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except Exception:
            return self._json(404, {"error": "not found"})
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/" or p == "/index.html":
            return self._file(os.path.join(os.path.dirname(__file__), "static", "index.html"),
                              "text/html; charset=utf-8")
        if p.startswith("/static/"):
            fp = os.path.join(os.path.dirname(__file__), "static", p[len("/static/"):])
            ct = "text/css" if p.endswith(".css") else ("application/javascript" if p.endswith(".js") else "text/plain")
            return self._file(fp, ct)
        if p == "/health":
            return self._json(200, {"ok": True})
        if p == "/api/v1/debug":
            from . import debug as _dbg
            return self._json(200, {"debug": _dbg.read(), "file": _dbg.LOG, "enabled": _dbg.ENABLED})
        if p == "/api/v1/stats":
            db.init_db()
            return self._json(200, {"leads": db.count("leads"),
                                    "escalations": db.count("escalations"),
                                    "rescue_tickets": db.count("rescue_tickets")})
        if p == "/api/v1/leads":
            db.init_db()
            qs = urlparse(self.path).query
            status = None
            for kv in qs.split("&"):
                if kv.startswith("status="):
                    status = kv.split("=")[1]
            return self._json(200, {"leads": db.list_leads(status, 50)})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self._read_body()
        except Exception:
            return self._json(400, {"error": "bad json"})
        if path == "/api/v1/chat":
            t0 = time.time()
            r = pipeline.chat(
                session_id=body.get("sessionId"), message=body.get("message", ""),
                location=body.get("location"), explicit_market=body.get("market"),
                lang_hint=body.get("langHint"))
            r["latency_ms"] = int((time.time() - t0) * 1000)
            return self._json(200, r)
        if path == "/api/v1/lead":
            db.init_db()
            lead = body.get("lead", {})
            rec = {"leadId": lead.get("leadId") or ("lead_" + (body.get("sessionId") or "x")[-8:]),
                   "sessionId": body.get("sessionId"), "market": lead.get("market", "AU"),
                   "channel": "web", "intent": lead.get("intent"),
                   "name": lead.get("name"), "phone": lead.get("phone"), "email": lead.get("email"),
                   "consentAt": None if not body.get("consent") else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "consentVersion": body.get("consentVersion", "v1"),
                   "dedupeKey": lead.get("dedupeKey"),
                   "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            ok = db.insert_lead(rec)
            return self._json(200, {"ok": True, "leadId": rec["leadId"], "inserted": bool(ok)})
        if path == "/api/v1/escalate":
            db.init_db()
            return self._json(200, {"ok": True, "ticketId": "tkt_" + (body.get("sessionId") or "x")[-8:],
                                    "reason": body.get("reason")})
        return self._json(404, {"error": "not found"})

    def log_message(self, *a):
        pass


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    # 预热:启动时加载共享 e5 模型 + 向量索引,避免首条消息付 ~30s
    try:
        print("预热向量模型/索引(第一次约 30s)...")
        from . import kb as _kb
        _kb.warmup()
    except Exception as e:
        print("warmup skipped:", e)
    httpd = HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"csapp server on http://127.0.0.1:{args.port}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
