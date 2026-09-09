"""零依赖 HTTP 服务(Phase 1 骨架,基于标准库 http.server)。

实现设计 §6.2 的 /api/v1/{chat,lead,escalate} 最小可用接口。
启动:python -m csapp.server --port 8000
生产可换用 FastAPI(见 api.py,需 pip 安装 fastapi/uvicorn)。
"""
import json, time, os, re, hashlib, secrets
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote
from . import pipeline, db, config


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------- /admin 访问控制(独立分析面板,与访客页完全隔离) ----------
    def _admin_ok(self):
        """有口令则校验 X-Admin-Token;无口令仅放行本机回环地址。"""
        tok = self.headers.get("X-Admin-Token", "")
        if config.ADMIN_TOKEN:
            return bool(tok) and secrets.compare_digest(tok, config.ADMIN_TOKEN)
        host = (self.client_address[0] if self.client_address else "") or ""
        return host in ("127.0.0.1", "::1", "localhost")

    @staticmethod
    def _qs(path):
        """解析 query string → dict(首个同名参数)。"""
        d = {}
        for kv in urlparse(path).query.split("&"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                d.setdefault(k, unquote(v))
        return d

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
        # ---- 独立分析面板(只读,与访客聊天页隔离;访客页无任何入口) ----
        # 说明: /admin 页面壳本身不含业务数据,放行;真正的数据都在
        # /api/v1/admin/* 之后,那里做严格口令校验。这样无口令的访问者
        # 也能看到页面上的登录框输入 token,而不是裸 403 JSON。
        if p == "/admin":
            return self._file(os.path.join(os.path.dirname(__file__), "static", "admin.html"),
                              "text/html; charset=utf-8")
        if p.startswith("/api/v1/admin/"):
            if not self._admin_ok():
                return self._json(403, {"error": "forbidden"})
            from . import analytics as _an
            q = self._qs(self.path)
            try:
                if p == "/api/v1/admin/summary":
                    return self._json(200, {"success": True, "summary": _an.summary(
                        from_ts=q.get("from"), to_ts=q.get("to"), market=q.get("market"),
                        language=q.get("lang"), intent=q.get("intent"))})
                if p == "/api/v1/admin/sessions":
                    try:
                        page = max(1, int(q.get("page", 1))); size = min(100, max(1, int(q.get("size", 20))))
                    except Exception:
                        page, size = 1, 20
                    ended = None
                    if q.get("ended") in ("1", "true", "yes"):
                        ended = True
                    elif q.get("ended") in ("0", "false", "no"):
                        ended = False
                    return self._json(200, {"success": True, **_an.list_sessions(
                        from_ts=q.get("from"), to_ts=q.get("to"), market=q.get("market"),
                        language=q.get("lang"), intent=q.get("intent"), issue=q.get("issue"),
                        ended=ended, q=q.get("q"), page=page, size=size)})
                if p == "/api/v1/admin/session":
                    s = _an.get_session(q.get("sessionId"))
                    if not s:
                        return self._json(404, {"success": False, "error": "not found"})
                    return self._json(200, {"success": True, **s})
                if p == "/api/v1/admin/turn":
                    t = _an.get_turn(q.get("traceId"))
                    if not t:
                        return self._json(404, {"success": False, "error": "not found"})
                    return self._json(200, {"success": True, "turn": t})
                if p == "/api/v1/admin/export":
                    import io, csv as _csv
                    rows = _an.export_turns(from_ts=q.get("from"), to_ts=q.get("to"),
                                            market=q.get("market"), language=q.get("lang"),
                                            intent=q.get("intent"), limit=5000)
                    buf = io.StringIO()
                    w = _csv.writer(buf)
                    keys = ["t", "session_id", "round_idx", "market", "language", "intent",
                            "issues", "latency_ms", "user_msg", "bot_reply"]
                    w.writerow(keys)
                    for r in rows:
                        w.writerow([r.get(k, "") for k in keys])
                    body = buf.getvalue().encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/csv; charset=utf-8")
                    self.send_header("Content-Disposition", 'attachment; filename="analytics_export.csv"')
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return None
            except Exception as e:
                return self._json(500, {"success": False, "error": str(e)[:200]})
            return self._json(404, {"error": "not found"})
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
            # 市场解析:显式 market > 会话实际 market > 空。
            # 前端市场下拉"自动"时 body 不带 market,若一律兜底 AU,中文会话(CN)
            # 的 138…/泰会话(THA)的 08… 手机号会被 AU 规则误拒。
            _mk = str(lead.get("market") or body.get("market") or "").strip().upper()
            if not _mk:
                try:
                    from .state import StateStore as _SS
                    _sid = body.get("sessionId")
                    if _sid:
                        _s = _SS().get(_sid)
                        _mk = str(getattr(_s, "market", None) or "").strip().upper()
                except Exception:
                    _mk = ""
            _mk_rec = _mk or "AU"     # 入库落一个确定市场(AU 兜底)
            _it = lead.get("intent") or body.get("intent")
            _nm = lead.get("name") or body.get("name")
            # ===== 联系方式格式校验 — 防止脏数据入 leads 表 =====
            # 校验用真实市场;仍为空(既无 body 也无会话)时走 ANY 通用规则(7-15 位),
            # 而不是错按 AU 规则拒掉合法国际号码。
            from . import cards as _cards_v
            _ok_v, _err = _cards_v.validate_contact(_ph, _em, _mk)
            if not _ok_v:
                return self._json(400, {"ok": False, "error": "invalid_contact",
                                        "errorKey": _err, "field": _err.split("_")[0]})
            # 生成稳定 dedupe_key:(user_phone_email_market) 的 sha1 前 12 位,空值用占位避免 NULL 撞唯一索引
            import hashlib
            _key_src = "|".join([
                (body.get("userId") or "").strip(),
                (re.sub(r"\s+", "", _ph or "")).strip() or "_",
                (re.sub(r"\s+", "", _em or "")).strip().lower() or "_",
                (_mk_rec or "_").strip(),
            ])
            _ddk = hashlib.sha1(_key_src.encode("utf-8")).hexdigest()[:12]
            rec = {
                "leadId":  lead.get("leadId") or ("lead_" + hashlib.sha1((body.get("sessionId") or "x").encode()).hexdigest()[:8]),
                "sessionId": body.get("sessionId"),
                "market":  _mk_rec,
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
                        # 把 history 中**最后一个**含 lead_input 的 turn 的 components
                        # 替换为 [lead_confirm],重开会话时就只看到 confirm 卡,
                        # 不会再看到当初弹出来的 input 卡。
                        if _confirm and _s.history:
                            for _t in reversed(_s.history):
                                _comps = _t.get("components") or []
                                if any(c.get("type") == "lead_input" for c in _comps):
                                    _t["components"] = [_confirm]
                                    break
                        _ss.save(_s)
            except Exception:
                pass  # 不影响 lead 落库的主流程
            # ====== 分析台同步:卡片留资成功后补写会话摘要留资位 ======
            # /api/v1/lead 不产生对话轮,record_turn 不会触发;不同步则看板
            # 「留资会话」永远看不到这条 lead。旁路,失败静默。
            try:
                from . import analytics as _an
                _an.mark_lead(body.get("sessionId"), meta={
                    "userId": body.get("userId"), "market": _mk_rec, "intent": _it,
                    "language": body.get("lang") or body.get("langHint"),
                })
            except Exception:
                pass
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
    # 在接收请求前升级持久化数据库；失败时直接停止启动。
    db.init_db()
    # 分析库(旁路):建表 + 清理超过保留期的旧 trace
    try:
        from . import analytics as _an
        _an.init_db()
        _n = _an.cleanup()
        if _n:
            print(f"分析库清理:删除了 {_n} 条过期记录")
        _an.resync_ended()   # 把状态库已结束但分析库未同步的会话补齐
        _an.resync_leads()   # 把 leads 库已留资但分析库未标记的会话补齐
    except Exception as e:
        print("analytics init skipped:", e)
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
