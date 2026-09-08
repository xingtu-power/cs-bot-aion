"""知识库访问层(Phase 1 骨架 + 向量语义检索)。

复用 Phase 0 的 kb(配置/文档/经销商/FAQ)+ tools/search.py 的检索。
新增:VectorRetriever(文档+FAQ 的向量语义检索,离线建索引、在线单条查询快)。
结构化(规格/经销商)保持精确查询。
"""
import glob, os, json, sys, re
import numpy as np
from . import config

# 广汽纯电全系车型清单(产品咨询推荐用):作为可引用事实注入 context,LLM 据此推荐 AION 各车型。
# 价格单位: 人民币万元; 全系弹匣电池; 北方冬季 CLTC 实际约 6-7 折。
# ⚠️ 严格约束: 只能引用以下列出的数值/特性; 严禁臆造/补充任何车型的续航、电池、价格、配置、
#   测试循环标签(如把 CLTC 说成 WLTP)、以及未列出的车型。缺失即说"暂无该信息"。
_MODEL_LINEUP = (
    "MODEL LINEUP (GAC Group 广汽集团 · 三品牌: 传祺 GAC Motor + AION 埃安 + HYPTEC 昊铂, 全系弹匣电池, 价格为人民币万元, 北方冬季 CLTC 实际约 6-7 折) — "
    "STRICT: beyond this MODEL LINEUP, the other detailed facts (power/torque/battery kWh/dimensions) are for AION UT "
    "ONLY. For every model OTHER than AION UT, this MODEL LINEUP is the ONLY allowed data source; do NOT apply AION UT "
    "specs (e.g. 150kW/210N·m/60kWh) to any other model. Quote ONLY these listed values; NEVER invent or extrapolate any "
    "model range/battery/power/torque/price/feature, do NOT copy a spec from one model onto another, do NOT change the "
    "test-cycle label (keep CLTC as CLTC), and do NOT mention any model not listed. If a requested detail is not listed, "
    "say you do not have it. "
    "NOTE (重要): 本 MODEL LINEUP 是**通用车型概览**(中国定位), 并非任一市场(含泰国)的在售清单。 若客户问某市场在售哪些车型/配置、或任何 facts 里没有的具体明细, 视为**知识缺口**: 如实说暂时没有该确切清单, 并**引导留资**(请他留手机/邮箱, 由当地 GAC Group 专员跟进给准确清单), 不要拿通用清单当作该市场在售。\n"
    "POSITIVE-FRAMING (关键): 介绍每款车只讲它的定位/优势(适合的人群/价格带/驾驶场景), 绝不要主动说缺点或用\"缺点/缺陷/不足\"这类词。 "
    "即使客户问\"这车有什么缺点/不足\", 也要用**正向设计定位**来迂回表达, 例如: \"这是一款为城市通勤设计的紧凑车型, 适合 1-2 人日常使用; "
    "如果经常 4 人或以上乘坐, 空间会相对紧凑；电池容量是按该价位定位来匹配的, 比更大尺寸车型小是正常的。\" "
    "不要列出任何硬伤式的缺点清单。: "
    "7-10万(城市代步/家用入门): AION UT 6.98-10.18万小型两厢, CLTC 320-530km, 轴距2750mm, 好开好停/空间越级/宜通勤接娃; "
    "AION Y Plus 8.68万起紧凑SUV, CLTC 430-510km, 后排大沙发/放平当床/90°大门, 家用爆款. "
    "10-14万(家用主力/智驾强): AION RT 9.98-12.38万中型轿跑, CLTC 605-710km, 车长4865mm, 大后备箱, 支持换电, 3C快充, 高速NOA标配; "
    "AION N60 10.68-12.68万紧凑SUV, CLTC 410/510/610km, 全系激光雷达+4D毫米波, 副驾零重力, 同级智驾硬件领先/性价比高——12万左右强烈推荐; "
    "AION V 10.98-14.18万家用SUV, 空间均衡/底盘舒适/外放电, 求稳家用. "
    "18万以上(昊铂高端): 昊铂GT 约20万纯电轿跑, 800V/后驱/零百5.5s/CLTC710km/风阻0.197/前双叉臂+后多连杆; "
    "昊铂HL 22万起中大六/七座SUV, 纯电/增程, 空气悬架, 家庭长途; AION LX 28万起旗舰SUV, 全铝底盘/双电机四驱/大空间豪华. "
    "快速抄作业: <9万市区代步→UT; 家庭大空间能躺平→Y Plus; 大轿车长续航跑高速→RT; 12万激光雷达智驾SUV→N60(强烈推荐); 重底盘轿跑20万→昊铂GT; 多孩6-7座→昊铂HL. "
    "提示: 冬季优先选 CLTC≥500km 版本; 有家充优先纯电; 长途无家充可看换电版本(RT).")

# 跨标准规格参考: 同一车型在不同市场用不同测试循环标注(WLTP/NEDC/CLTC), 应报全标准, 不要否认任一标准。
_MODEL_SPECS = (
    "CROSS-STANDARD reference (仅 AION UT; 以 AU/THA 规格表为准, 同一车型不同市场用不同测试循环, 应报全标准, 不要说某标准不存在): "
    "AION UT 综合续航: WLTP 430km / NEDC 500km(500 Premium) / NEDC 420km(420 Standard); "
    "AION UT 电池容量: 60kWh(500 Premium / AU) / 50.27kWh(420 Standard); "
    "AION UT 充电: 交流 11kW, 直流快充 30-80% 约 24 分钟.")

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
                        self._docmeta[r.get("id")] = {"doc": r.get("doc_name"), "page": r.get("page_no"),
                                                      "text": r.get("content", "")}
        return self._docmeta

    def _citation_for(self, cid):
        if not cid:
            return None
        if str(cid).startswith("faq:"):
            return {"doc": "FAQ", "page": None, "source": cid.split(":", 1)[1], "image": None}
        m = self._doc_meta().get(cid)
        if m:
            return {"doc": m["doc"], "page": m["page"], "source": cid, "image": None}
        return {"doc": cid, "page": None, "source": cid, "image": None}

    def figures_for(self, cid):
        """返回某内容页抽取出的**真实插图** URL 列表(按放置顺序);无则空列表。

        插图文件由 tools/extract_images.py 裁剪页内图区域生成,命名
        <version>_p<page_no>__f<idx>.jpg;文档块 id 形如 au-owner-owner_0811-p0002(内嵌 0-based 页码)。
        """
        try:
            parts = str(cid).split("-")
            if len(parts) < 4:
                return []
            mkt = parts[0].upper()
            version = parts[-2]
            page_no = int(parts[-1][1:]) + 1
            d = os.path.join(config.KB_ROOT, mkt, "images")
            fs = sorted(glob.glob(os.path.join(d, f"{version}_p{page_no}__f*.jpg")))
            return [f"/api/v1/kb/image?mkt={mkt}&v={version}&p={page_no}&f={os.path.basename(f).split('__f')[-1].split('.')[0]}"
                    for f in fs]
        except Exception:
            return []

    @staticmethod
    def _is_nav_page(text):
        """是否为 目录/索引/前言/封面 这类非内容页(不适合作为配图)。"""
        t = (text or "").lower()
        if any(m in t for m in ("index", "contents", "foreword", "preface",
                                "how to read this manual", "notices to users",
                                "arrange them in the order", "i-n-d-e-x",
                                "目录", "索引", "前言", "序言")):
            return True
        # 目录/索引特征:大量 "标题............数字" 点线
        if len(re.findall(r"\.{3,}\s*\d+", t)) >= 3:
            return True
        return False

    @staticmethod
    def _howto_score(text):
        """内容页"操作/步骤"倾向评分,用于挑最像"如何做"的配图页(而非警告/手册首页)。"""
        t = (text or "").lower()
        s = 0
        if "the following steps" in t or "observe the following" in t or "as follows:" in t:
            s += 3
        if re.search(r"(?:^|\s)\d{1,2}[\.\)]\s", t):           # 编号步骤 1. 2. 3.
            s += 2
        if any(k in t for k in ("how to", "step", "press", "open", "connect", "turns on",
                                "charger", "charge", "switch", "button", "adjust", "setting")):
            s += 1
        return s

    def answer_images(self):
        """返回本轮回答的最佳配图(**真实插图**)URL 列表;无则空列表。

        跳过 目录/索引/前言/封面 等非内容页;在内容页里按"操作/步骤"倾向评分,
        挑最像讲解/图解的那页,返回该页裁剪出的插图(如 AC 充电步骤图)。
        """
        dm = self._doc_meta()
        best, best_score = None, -1
        for c in self._citations:
            cid = c.get("source")
            if not cid or str(cid).startswith("faq:"):
                continue
            figs = self.figures_for(cid)
            if not figs:
                continue
            text = dm.get(cid, {}).get("text", "")
            if self._is_nav_page(text):
                continue
            sc = self._howto_score(text)
            if sc > best_score:          # 严格大于:同分保留更靠前(更高相关)的
                best_score, best = sc, figs
        return best or []

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

    def _market_models_text(self, mpath):
        """解析 kb/<mkt>/models.json → 易读文本给 LLM(作为该市场权威来源)。"""
        try:
            data = json.load(open(mpath, encoding="utf-8"))
            market = data.get("market", "")
            updated = data.get("lastUpdated", "")
            lines = [f"[本市场在售车型清单] ({market} · {updated})"]
            lines.append("未列出的车型不代表不在售；以下以登记为准:")
            for m in data.get("models", []):
                name = m.get("name", "")
                variants = m.get("variants") or []
                v = ",".join(variants) if isinstance(variants, list) else str(variants)
                line = f"- {name}"
                if v and v.strip() not in ("", "-", "[]"):
                    line += f" | 版本: {v}"
                if m.get("priceRange"):
                    line += f" | 价格: {m['priceRange']}"
                if m.get("availability"):
                    line += f" | 状态: {m['availability']}"
                if m.get("note"):
                    line += f" | 亮点: {m['note']}"
                lines.append(line)
            return "\n".join(lines)
        except Exception as e:
            return f"[本市场在售车型清单] (待补全: 解析失败 {e})"

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
        # 全系车型清单(产品咨询推荐用):作为可引用的事实注入,LLM 可据此推荐 AION 各车型。
        # 市场在售车型清单(若提供): 解析后拼成易读文本作为该市场权威来源注入
        mpath = os.path.join(config.KB_ROOT, self.market, "models.json")
        if os.path.exists(mpath):
            parts.append(self._market_models_text(mpath))
        parts.append(_MODEL_SPECS)
        parts.append(_MODEL_LINEUP)
        return "\n".join(parts)
