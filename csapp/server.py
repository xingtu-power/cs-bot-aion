"""零依赖 HTTP 服务(Phase 1 骨架,基于标准库 http.server)。

实现设计 §6.2 的 /api/v1/{chat,lead,escalate} 最小可用接口。
启动:python -m csapp.server --port 8000
生产可换用 FastAPI(见 api.py,需 pip 安装 fastapi/uvicorn)。
"""
import json, time, os, re, hashlib
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
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
        if p == "/api/v1/sessions":
            from .state import StateStore as _SS
            qs = urlparse(self.path).query
            uid = None
            for kv in qs.split("&"):
                if kv.startswith("userId="):
                    uid = kv.split("=")[1]
            return self._json(200, {"sessions": _SS().list_for_user(uid or None, 30)})
        if p == "/api/v1/session":
            from .state import StateStore as _SS
            qs = urlparse(self.path).query
            sid = None
            for kv in qs.split("&"):
                if kv.startswith("sessionId="):
                    sid = kv.split("=")[1]
            s = _SS().get_session(sid) if sid else None
            if not s:
                return self._json(404, {"success": False, "error": "session not found"})
            return self._json(200, {"success": True, "session": {
                "sessionId": s.get("id"), "market": s.get("market"), "language": s.get("language"),
                "intent": s.get("intent"), "ended": bool(s.get("ended")),
                "endedReason": s.get("ended_reason"), "totalRounds": s.get("total_rounds", len(s.get("history", []))),
                "history": s.get("history", [])}})
        if p == "/api/v1/kb/image":
            import re as _re
            from . import config as _cfg
            qs = urlparse(self.path).query
            mkt = v = pg = fi = None
            for kv in qs.split("&"):
                if "=" in kv:
                    k, val = kv.split("=", 1)
                    if k == "mkt": mkt = val
                    elif k == "v": v = val
                    elif k == "p": pg = val
                    elif k == "f": fi = val
            # 校验市场/版本/页/图索引,防目录穿越
            if mkt and v and pg and mkt.upper() in ("AU", "THA") \
                    and _re.fullmatch(r"[\w.\-]+", v) and pg.isdigit():
                fname = f"{v}_p{int(pg)}"
                if fi is not None and fi.isdigit():
                    fname += f"__f{int(fi)}"
                base = os.path.join(_cfg.KB_ROOT, mkt.upper(), "images", fname)
                fp = base + ".jpg"
                ctype = "image/jpeg"
                if not os.path.exists(fp):
                    fp = base + ".png"; ctype = "image/png"   # 兼容旧 png
                return self._file(fp, ctype)
            return self._json(404, {"error": "image not found"})
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
                lang_hint=body.get("langHint"), user_id=body.get("userId"))
            r["latency_ms"] = int((time.time() - t0) * 1000)
            return self._json(200, r)
        if path == "/api/v1/lead":
            db.init_db()
            # 支持两种调用形态:
            # 1) 嵌套: {"lead":{phone,email,market,intent,name}, sessionId, consent, consentVersion}
            # 2) 平铺: {phone, email, market, intent, name, sessionId, userId, consentVersion, consentAt}
            lead = body.get("lead", {}) or {}
            _ph = lead.get("phone") or body.get("phone")
            _em = lead.get("email") or body.get("email")
            _mk = lead.get("market") or body.get("market") or "AU"
            _it = lead.get("intent") or body.get("intent")
            _nm = lead.get("name") or body.get("name")
            # 生成稳定 dedupe_key:(user_phone_email_market) 的 sha1 前 12 位,空值用占位避免 NULL 撞唯一索引
            import hashlib
            _key_src = "|".join([
                (body.get("userId") or "").strip(),
                (re.sub(r"\s+", "", _ph or "")).strip() or "_",
                (re.sub(r"\s+", "", _em or "")).strip().lower() or "_",
                (_mk or "_").strip(),
            ])
            _ddk = hashlib.sha1(_key_src.encode("utf-8")).hexdigest()[:12]
            rec = {
                "leadId":  lead.get("leadId") or ("lead_" + hashlib.sha1((body.get("sessionId") or "x").encode()).hexdigest()[:8]),
                "sessionId": body.get("sessionId"),
                "market":  _mk,
                "channel": "web",
                "intent":  _it,
                "name":    _nm,
                "phone":   _ph,
                "email":   _em,
                "consentAt": body.get("consentAt") or (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) if body.get("consent") else None),
                "consentVersion": body.get("consentVersion", "v1"),
                "userId":  body.get("userId"),
                "dedupeKey": _ddk,
                "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            ok = db.insert_lead(rec)
            # 直接返回 lead_confirm schema,前端用其原地替换 input 卡为新的确认消息
            try:
                from . import components as _cm
                _lang = body.get("lang") or body.get("langHint") or "en"
                _confirm = _cm.build_confirm_card(_ph, _em, "collected", _lang,
                                                   session_id=rec.get("sessionId") or "now")
            except Exception as _e:
                _confirm = None
            # ====== 标记该会话"已留资成功" ======
            # 同一会话后续 chat 不再发任何 lead card(input 与 confirm 都不发),
            # 避免每次新问题都弹已留提示打扰用户。
            try:
                from .state import StateStore as _SS
                _sid = body.get("sessionId")
                if _sid:
                    _ss = _SS()
                    _s = _ss.get(_sid)
                    if _s:
                        _s.collected["phone"] = _ph or _s.collected.get("phone")
                        _s.collected["email"] = _em or _s.collected.get("email")
                        _s.has_shown_lead_card = True   # 已留过 → 后续 chat 不再发 lead card
                        # 落回 history:让重开会话能看到这条 lead_confirm 卡
                        if _confirm:
                            _s.append_components([_confirm], intent="lead-submit")
                        _ss.save(_s)
            except Exception:
                pass  # 不影响 lead 落库的主流程
            return self._json(200, {"ok": True, "leadId": rec["leadId"],
                                    "inserted": bool(ok), "dedupeKey": _ddk,
                                    "leadConfirm": _confirm})
        if path == "/api/v1/escalate":
            db.init_db()
            return self._json(200, {"ok": True, "ticketId": "tkt_" + (body.get("sessionId") or "x")[-8:],
                                    "reason": body.get("reason")})
        if path == "/api/v1/session/end":
            from .state import StateStore as _SS
            sid = body.get("sessionId")
            if not sid:
                return self._json(400, {"error": "sessionId required"})
            ss = _SS()
            if not ss.get_session(sid):
                return self._json(404, {"success": False, "error": "session not found"})
            reason = body.get("reason") or "user_closed"
            changed = ss.mark_ended(sid, reason)
            return self._json(200, {"success": True, "sessionId": sid,
                                    "ended": True, "endedReason": reason, "changed": bool(changed)})
        return self._json(404, {"error": "not found"})

    def log_message(self, *a):
        pass


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", type=str, default="127.0.0.1",
                    help="绑定地址;容器/公网部署传 0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    # 预热:启动时加载共享 e5 模型 + 向量索引,避免首条消息付 ~30s
    try:
        print("预热向量模型/索引(第一次约 30s)...")
        from . import kb as _kb
        _kb.warmup()
    except Exception as e:
        print("warmup skipped:", e)
    # 定期清理:删除超过 ARCHIVE_DAYS 的会话存档
    try:
        from . import config as _cfg
        from .state import StateStore as _SS
        _n = _SS().cleanup(_cfg.ARCHIVE_DAYS)
        print(f"清理过期会话:删除了 {_n} 个")
        _nn = _SS().cleanup_none_files()
        if _nn:
            print(f"清理脏文件(None*.json):删除了 {_nn} 个")
        # 后台 idle 巡检:每 60s 把超过 TTL_RECENT 未活动且未结束的会话标为 idle
        import threading
        def _idle_loop():
            while True:
                try:
                    _SS().idle_mark(_cfg.TTL_RECENT)
                except Exception:
                    pass
                time.sleep(60)
        t = threading.Thread(target=_idle_loop, daemon=True)
        t.start()
        print("后台 idle 巡检已启动(每 60s)")
    except Exception as e:
        print("cleanup skipped:", e)
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"csapp server on http://{args.host}:{args.port} (threaded)")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
