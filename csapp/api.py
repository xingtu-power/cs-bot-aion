"""HTTP API(Phase 1 骨架,对应设计 §6.2)。

FastAPI 暴露 /api/v1/{chat,lead,escalate,feedback}。
启动:PYTHONPATH=. uvicorn csapp.api:app --port 8000
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "pylib"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import time
from . import pipeline

app = FastAPI(title="AION 海外智能客服", version="0.1.0")


class ChatRequest(BaseModel):
    sessionId: Optional[str] = None
    message: str = Field(..., min_length=1)
    location: Optional[Dict[str, float]] = None        # {"lat":..,"lng":..}
    market: Optional[str] = None                       # 手动指定市场
    langHint: Optional[str] = None                     # 语言提示


class LeadRequest(BaseModel):
    sessionId: Optional[str] = None
    lead: Dict[str, Any] = {}
    consent: bool = False
    consentVersion: Optional[str] = None


class EscalateRequest(BaseModel):
    sessionId: Optional[str] = None
    reason: str
    contextSummary: Optional[str] = None


@app.post("/api/v1/chat")
def chat(req: ChatRequest):
    t0 = time.time()
    resp = pipeline.chat(session_id=req.sessionId, message=req.message,
                         location=req.location, explicit_market=req.market,
                         lang_hint=req.langHint)
    resp["latency_ms"] = int((time.time() - t0) * 1000)
    return resp


@app.post("/api/v1/lead")
def lead(req: LeadRequest):
    # 骨架:留资落库接口(简化)。生产落自建库 leads 表。
    return {"ok": True, "leadId": req.lead.get("leadId") or ("lead_" + (req.sessionId or "")[-8:]),
            "consent": req.consent, "consentVersion": req.consentVersion}


@app.post("/api/v1/escalate")
def escalate(req: EscalateRequest):
    # 骨架:转人工事件(简化)。生产落 escalations 表。
    return {"ok": True, "ticketId": "tkt_" + (req.sessionId or "x")[-8:], "reason": req.reason}


@app.post("/api/v1/feedback")
def feedback(sessionId: Optional[str] = None, rating: int = 0, comment: Optional[str] = None):
    return {"ok": True, "rating": rating}


@app.get("/health")
def health():
    return {"ok": True}
