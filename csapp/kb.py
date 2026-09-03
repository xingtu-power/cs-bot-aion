"""知识库访问层(Phase 1 骨架 + 向量语义检索)。

复用 Phase 0 的 kb(配置/文档/经销商/FAQ)+ tools/search.py 的检索。
新增:VectorRetriever(文档+FAQ 的向量语义检索,离线建索引、在线单条查询快)。
结构化(规格/经销商)保持精确查询。
"""
import os, json, sys
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

    def context(self, query, lang="en", topk=3):
        """RAG 上下文:向量语义 top-k(文档+FAQ) + 结构化规格,拼成给 LLM 的上下文串。
        向量索引未建时回退关键词 FAQ + 规格。"""
        parts = []
        # 1) 向量语义检索(文档+FAQ),按 query 相关性(缓存复用)
        if self._vec is None:
            self._vec = VectorRetriever(self.market)
        vec = self._vec.search(query, topk=topk + 1)
        for v in vec:
            parts.append(f"- {v['text'][:500]}")
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
        return "\n".join(parts)
