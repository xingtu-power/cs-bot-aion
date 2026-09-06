"""知识库访问层(Phase 1 骨架 + 向量语义检索)。

复用 Phase 0 的 kb(配置/文档/经销商/FAQ)+ tools/search.py 的检索。
新增:VectorRetriever(文档+FAQ 的向量语义检索,离线建索引、在线单条查询快)。
结构化(规格/经销商)保持精确查询。
"""
import os, json, sys, re
import numpy as np
from . import config

# 复用 Phase 0 检索逻辑
sys.path.insert(0, os.path.join(config.ROOT, "tools"))
import search as _search

MODEL = "intfloat/multilingual-e5-large"


_MODEL = None  # 模块级共享 e5 模型单例,所有 VectorRetriever 复用,避免每次加载


def _get_model():
    global _MODEL
    if _MODEL is None:
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        os.environ.setdefault("HF_HOME", os.path.join(config.ROOT, "tools", "hf_cache"))
        sys.path.insert(0, os.path.join(config.ROOT, "tools", "pylib"))
        from fastembed import TextEmbedding
        _MODEL = TextEmbedding(model_name=MODEL, cache_dir=os.path.join(config.ROOT, "tools", "emb_cache"))
    return _MODEL


class VectorRetriever:
    """加载 kb/<market>/vectors 的向量索引,做余弦 top-k;无索引则返回空。"""

    def __init__(self, market="AU"):
        self.market = market
        self._store = None
        self._load()

    def _load(self):
        d = os.path.join(config.KB_ROOT, self.market, "vectors")
        if not os.path.exists(os.path.join(d, "vectors.npz")):
            self._store = None
            return
        self._store = {
            "embs": np.load(os.path.join(d, "vectors.npz"))["emb"],
            "ids": json.load(open(os.path.join(d, "ids.json"))),
            "texts": json.load(open(os.path.join(d, "texts.json"))),
        }

    def _model_load(self):
        return _get_model()

    def search(self, query, topk=4):
        if self._store is None:
            return []
        try:
            q = np.array(list(self._model_load().embed(["query: " + query]))[0])
        except Exception:
            return []
        sims = self._store["embs"] @ q
        idx = np.argsort(-sims)[:topk]
        return [{"id": self._store["ids"][i], "text": self._store["texts"][i],
                 "score": float(sims[i])} for i in idx]


def warmup():
    """服务启动时预热:加载共享 e5 模型 + 两市场向量索引,避免首条消息付 30s。"""
    for m in ("THA", "AU"):
        vr = VectorRetriever(m)
        if vr._store is not None:
            vr._model_load()  # 加载共享模型(一次)


class KnowledgeBase:
    def __init__(self, market="AU"):
        self.market = market
        self._si = None   # spec index 缓存
        self._faq = None
        self._vec = None   # 向量检索器缓存(避免重复加载模型)
        self._docmeta = None   # id -> {doc_name,page} 缓存
        self._citations = []   # 本轮检索到的来源
        self._rescue_cache = None

    # ---- 规格 ----
    def spec(self, concepts, variant=None):
        """按概念查配置表,返回 {field, variants/value,...}。"""
        r = _search.query_spec(self.market, concepts, variant)
        return r

    def faq(self, query, topk=2, lang="en", intent=None):
        if self._faq is None:
            self._faq = [json.loads(l) for l in open(
                os.path.join(config.KB_ROOT, self.market, "faq", "faq.jsonl"), encoding="utf-8")]
            # 本地化答案(zh/es),key=id -> {lang: answer}
            lp = os.path.join(config.KB_ROOT, self.market, "faq", "faq_localized.json")
            self._loc = json.load(open(lp, encoding="utf-8"))["items"] if os.path.exists(lp) else {}
        else:
            self._loc = getattr(self, "_loc", {})
        q = _search.tokenize(query)
        candidates = self._faq if intent is None else [it for it in self._faq if it.get("intent") == intent]
        scored = []
        for it in candidates:
            c = set(_search.tokenize(it["question"])) | set(it["keywords"])
            score = len(q & c)
            if score > 0:
                scored.append((score, it))
        scored.sort(key=lambda x: x[0], reverse=True)
        out = []
        for _, it in scored[:topk]:
            if lang in ("zh", "es"):
                loc = self._loc.get(it["id"], {}).get(lang)
                if loc:
                    it = dict(it); it["answer"] = loc
            out.append(it)
        return out

    def search_docs(self, query, doc_type=None, topk=4):
        return _search.search_docs(self.market, query, doc_type, topk)

    def nearest_dealers(self, lat, lng, topk=3):
        return _search.nearest_dealers(self.market, lat, lng, topk)

    _DEALER_LABEL = {"zh": "经销商", "en": "Dealer", "es": "Concesionario", "th": "ตัวแทนจำหน่าย"}

    def dealer_context(self, query="", lat=None, lng=None, topk=3, lang="en"):
        """把经销商(名称/地址/城市/距离)拼成给 LLM 的上下文;严格脱敏:不给电话/联系人/编码。
        - 有坐标 → 按 routed market 的 nearest(距离)。
        - match 跨市场(THA+AU),按名称/地址/城市别名(曼谷→Bangkok)命中,并标注市场。
        - label 按语言本地化。"""
        lb = self._DEALER_LABEL.get(lang, "Dealer")
        lines = []
        seen = set()
        if lat is not None and lng is not None:
            for r in _search.nearest_dealers(self.market, lat, lng, topk):
                nm = r.get("name") or ""
                if not nm or nm in seen:
                    continue
                seen.add(nm)
                d = r.get("_dist")
                dist = f", 约 {d:.1f} km" if d is not None else ""
                ph = f"，电话 {r['phone']}" if r.get("phone") else ""
                lines.append(f"- {lb} {nm} ({self.market}) — {r.get('address','')}{dist}{ph}")
        for r in _search.match_dealers(query, topk):
            nm = r.get("name") or ""
            if not nm or nm in seen:
                continue
            seen.add(nm)
            mkt = r.get("_market", self.market)
            ph = f"，电话 {r['phone']}" if r.get("phone") else ""
            lines.append(f"- {lb} {nm} ({mkt}) — {r.get('address','')}{ph}")
        return "\n".join(lines) if lines else ""

    # 规格概念 → 多语言标签(中/西;en/th 用字段原始名)
    _CONCEPT_LABEL = {
        "range": {"zh": "续航（综合工况）", "es": "Autonomía (condiciones integrales)"},
        "battery": {"zh": "电池容量", "es": "Capacidad de la batería"},
        "power": {"zh": "最大功率", "es": "Potencia máxima"},
        "seats": {"zh": "座位数", "es": "Número de asientos"},
        "dimensions": {"zh": "尺寸", "es": "Dimensiones"},
        "ac-charging": {"zh": "交流充电功率", "es": "Carga AC"},
        "dc-charging": {"zh": "直流快充功率", "es": "Carga rápida DC"},
    }

    def answer(self, query, topk=4, lang="en"):
        """面向回答的取数:先 FAQ(精确),再规格(数值),最后文档(检索)。返回列表。
        lang 用于规格值标签本地化(zh/es);FAQ 为语句文案,由回复侧处理。"""
        out = []
        for it in self.faq(query, 1, lang=lang):
            out.append({"kind": "faq", "text": it["answer"], "src": it["source"], "id": it["id"]})
        for concept in ("range", "battery", "power", "seats", "dimensions", "ac-charging", "dc-charging"):
            s = self.spec([concept])
            if s and "value" in s:
                label = self._CONCEPT_LABEL.get(concept, {}).get(lang, s["field"])
                out.append({"kind": "spec", "text": f"{label}: {s['value']}", "src": "spec", "concept": concept})
        for s, dt, pg, ch, c in self.search_docs(query, topk=topk):
            out.append({"kind": "doc", "text": c, "src": dt, "page": pg})
        return out

    def _doc_meta(self):
        """id -> {doc_name, page_no} 缓存(文档块 id 形如 tha-owner-260515-p0001)。"""
        if self._docmeta is None:
            self._docmeta = {}
            ddir = os.path.join(config.KB_ROOT, self.market, "docs")
            if os.path.isdir(ddir):
                for fn in os.listdir(ddir):
                    if not fn.endswith(".jsonl"):
                        continue
                    for l in open(os.path.join(ddir, fn), encoding="utf-8"):
                        try:
                            r = json.loads(l)
                        except Exception:
                            continue
                        self._docmeta[r.get("id")] = {"doc": r.get("doc_name"), "page": r.get("page_no")}
        return self._docmeta

    def _citation_for(self, cid):
        if not cid:
            return None
        if str(cid).startswith("faq:"):
            return {"doc": "FAQ", "page": None, "source": cid.split(":", 1)[1]}
        m = self._doc_meta().get(cid)
        if m:
            return {"doc": m["doc"], "page": m["page"], "source": cid}
        return {"doc": cid, "page": None, "source": cid}

    def citations(self):
        """本轮检索到的来源列表(前端"引用"展示用)。"""
        return list(self._citations)

    def _rescue_texts(self):
        if self._rescue_cache is None:
            self._rescue_cache = []
            ddir = os.path.join(config.KB_ROOT, self.market, "docs")
            if os.path.isdir(ddir):
                for fn in sorted(os.listdir(ddir)):
                    if fn.startswith("rescue") and fn.endswith(".jsonl"):
                        for l in open(os.path.join(ddir, fn), encoding="utf-8"):
                            try:
                                r = json.loads(l)
                            except Exception:
                                continue
                            self._rescue_cache.append((r.get("page_no"), r.get("content", "")))
                        break
        return self._rescue_cache

    def _rescue_hotline(self):
        """从 rescue 手册里提取第一条 热线/SOS 行(如有)。"""
        for pg, c in self._rescue_texts():
            for m in re.finditer(r"(?:0[0-9]{8,10}|1[0-9]{9}|SOS[^\n.]{0,30}|hotline[^\n.]{0,30}|โทร[0-9 ]{5,})", c, re.I):
                v = m.group(0).strip()
                if re.search(r"\d{4,}|SOS", v) or (pg and v):
                    return v
        return ""

    def context(self, query, lang="en", topk=3):
        """RAG 上下文:向量语义 top-k(文档+FAQ) + 结构化规格,拼成给 LLM 的上下文串。
        向量索引未建时回退关键词 FAQ + 规格。"""
        parts = []
        # 1) 向量语义检索(文档+FAQ),按 query 相关性(缓存复用)
        if self._vec is None:
            self._vec = VectorRetriever(self.market)
        vec = self._vec.search(query, topk=topk + 1)
        self._citations = []
        for v in vec:
            src = self._citation_for(v["id"])
            if src:
                self._citations.append(src)
                loc = f" [来源: {src['doc']}" + (f" P{src['page']}" if src.get("page") else "") + "]"
            else:
                loc = ""
            parts.append(f"-{loc} {v['text'][:500]}")
        # 2) 结构化规格(精确)
        for concept in ("range", "battery", "power", "seats", "dimensions", "ac-charging", "dc-charging"):
            s = self.spec([concept])
            if s and "value" in s:
                label = self._CONCEPT_LABEL.get(concept, {}).get(lang, s["field"])
                parts.append(f"- {label}: {s['value']}")
        # 3) 兜底:关键词 FAQ(向量未命中时)
        if not vec:
            for it in self.faq(query, topk, lang=lang):
                parts.append(f"- {it['answer']}")
        # 3.5) 紧急/救援:把 rescue 手册里的热线/SOS 注入,确保 LLM 有得说
        if any(k in query.lower() for k in ("救援", "紧急", "rescue", "emergency", "hotline", "roadside", "ฉุกเฉิน")):
            hl = self._rescue_hotline()
            if hl:
                self._citations.append({"doc": "Emergency Rescue Guide", "page": None, "source": "rescue-hotline"})
                parts.append(f"- [来源: Emergency Rescue Guide] {hl}")
        return "\n".join(parts)
