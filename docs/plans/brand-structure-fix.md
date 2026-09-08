# 品牌结构纠正方案 · GAC 三品牌（传祺 / AION / HYPTEC）

> 日期:2026-09-08 · 状态:**待评审** · 关联:用户确认的品牌事实

---

## 1. 背景

用户确认广汽集团（GAC Group）旗下有 **3 个并列的乘用车品牌**：

| 品牌 | 中文名 | 官方英文名 | 定位 |
|---|---|---|---|
| GAC | 传祺 | GAC Motor / Trumpchi | 传统主力品牌 |
| AION | 埃安 | AION | 新能源品牌 |
| HYPTEC | 昊铂 | HYPTEC | 高端新能源品牌 |

而当前代码把品牌结构理解成了 **2 品牌 + GAC 当母公司**，与事实不符，需要纠正。

---

## 2. 现状盘点（含证据）

代码当前的错误理解（在 4 个文件里表述高度一致）：

```
❌ 现状（错误）
GAC（广汽国际）= parent brand（母品牌/母公司）
  ├── AION（埃安）      = sub-brand
  └── Hyper（昊铂）      = sub-brand
```

| # | 位置 | 原文证据 | 问题 |
|---|---|---|---|
| 1 | `csapp/llm.py:165` | `"You are a GAC (广汽国际) customer-support bot serving the AION and Hyper brands."` | 漏传祺；昊铂英文错（Hyper→HYPTEC） |
| 2 | `csapp/llm.py:183` | `"you represent GAC (广汽国际), a parent brand; AION and Hyper are GAC sub-brands."` | 把 GAC 当母品牌，层级错 |
| 3 | `csapp/kb.py:16` | `"MODEL LINEUP (GAC 广汽国际 · AION + 昊铂Hyper …)"` | 同上，2 品牌 |
| 4 | `csapp/cards.py:89`（内置兜底）+ `csapp/talk_scripts.json:49`（实际生效） | `"introduce yourself as the GAC assistant (AION / Hyper are GAC sub-brands)"` | 同上 |
| 5 | `csapp/kb.py` `_MODEL_LINEUP`（15-36 行） | 车型清单仅 AION（UT/Y Plus/RT/N60/V/LX）+ 昊铂（GT/HL） | **无传祺任何车型** |
| 6 | `csapp/compliance.py:134` `_NON_UT_RE` | `(AION Y Plus|AION RT|AION N60|AION V|昊铂GT|昊铂HL|AION LX|Hyper GT|Hyper HL)` | 防臆造正则缺传祺车型 |

### 无需改（已正确）

- `csapp/compliance.py:190` `COMPETITOR_PATTERNS` 竞品清单：**未含**传祺/HYPTEC/昊铂 —— 正确（自家品牌不是竞品）。但改完后需回归确认 `find_competitor` 不会把「传祺/HYPTEC」误判为竞品触发留资兜底。

---

## 3. 目标品牌结构（正确认知）

```
✅ 目标（正确）
广汽集团 GAC Group（母公司，非品牌，仅作集团背书）
  ├── GAC 传祺（GAC Motor / Trumpchi）＝ 品牌①
  ├── AION 埃安 ＝ 品牌②
  └── HYPTEC 昊铂 ＝ 品牌③
```

统一品牌认知表述（拟用于全部 4 处）：
> 英文：`GAC Group (广汽集团) has three passenger-vehicle brands: GAC Motor (传祺), AION (埃安), and HYPTEC (昊铂).`

---

## 4. 关键决策点（需用户拍板，直接影响改法）

### D1. 传祺是否进入海外客服的推荐范围？

| 选项 | 含义 | 影响 |
|---|---|---|
| **A. 3 品牌全量推荐** ✅（已选定） | 客服把传祺、AION、昊铂都作为可推荐品牌 | 需补传祺海外车型数据（外部依赖，见 §5.2）；改动面最大 |
| B. 认知正确 + 主推 AION/昊铂 | 客服知道传祺是集团品牌、自我介绍正确，但海外推荐聚焦 AION（主力）+ 昊铂（高端） | 只需改品牌结构（§5.1），传祺车型数据可后补 |
| C. 维持 AION 单品牌 | 项目本名就是「AION 客服」，只把昊铂英文改对 | 改动最小，但未回应「3 品牌」事实 |

**已选定 A（2026-09-08）**：传祺、AION、昊铂三品牌全量进入推荐范围。品牌结构纠正（子项 1-3）先行落地；传祺车型数据（子项 4-5）待用户提供清单后补齐。

### D2. 客服自我介绍口径（"other" 卡的打招呼）

现状：`GAC assistant (AION / Hyper are GAC sub-brands)`。

拟改为（随 D1 而定）：
- 若 D1=A：`广汽集团旗下传祺/AION/昊铂三大品牌的客服`
- 若 D1=B：`广汽集团（GAC Group）客服，主要为您服务 AION（埃安）与 HYPTEC（昊铂）车型`（传祺一句带过）

### D3. 昊铂英文名：Hyper → HYPTEC（无争议，官方名）

---

## 5. 方案设计

### 5.1 品牌结构纠正（确定性改动，不依赖外部数据）

统一修改以下 4 处，口径一致：

| 文件 | 位置 | 改动 |
|---|---|---|
| `csapp/llm.py` | 165 行 | 开场白改为「GAC Group 三品牌」表述 |
| `csapp/llm.py` | 183 行 `BRAND:` | 删除「parent brand / sub-brands」层级，改为三品牌并列 |
| `csapp/kb.py` | 16 行 `_MODEL_LINEUP` 标题 | `GAC 广汽国际 · AION + 昊铂Hyper` → 三品牌表述；`Hyper` → `HYPTEC` |
| `csapp/talk_scripts.json` | 49 行 `other.desc` | 自我介绍改为三品牌口径 |
| `csapp/cards.py` | 89 行 `_DEFAULT_CARD` 兜底 | 与 json 保持同步 |

### 5.2 传祺车型数据补充（外部依赖，独立成项）

- 若 D1=A，需用户提供传祺在泰/澳的**在售车型清单**（车型名 + 价格带 + 定位/亮点 + 上市状态），格式参考现有 `_MODEL_LINEUP` 的 AION/昊铂条目。
- 同步补 `compliance.py:134` `_NON_UT_RE`：加入传祺车型名，纳入「非 UT 车型不得臆造详细参数」的防臆造保护。
- 传祺若无详细规格，仅列 MODEL LINEUP 概览（与昊铂 GT/HL 同待遇），详细参数（功率/扭矩/电池）一律「暂无信息」。

### 5.3 竞品清单回归（改动后必做）

确认 `COMPETITOR_PATTERNS` 不含「传祺 / Trumpchi / GAC Motor / HYPTEC / 昊铂」，避免 `find_competitor` 误伤自家品牌。当前已正确，仅需回归验证，无需改代码（除非未来加传祺车型后 LLM 回复带出「GAC Motor」触发误判，届时在正则加白名单）。

---

## 6. 实施清单（每个子项单独 commit，便于 revert）

- [x] **子项 1**：`llm.py` 品牌结构纠正（165/183 行 + classify/answer 旧接口，三品牌并列 + HYPTEC）
- [x] **子项 2**：`kb.py` `_MODEL_LINEUP` 标题纠正（三品牌 + HYPTEC）
- [x] **子项 3**：`talk_scripts.json` + `cards.py` 兜底「other」卡自我介绍纠正
- [x] **子项 3.5（回归补充）**：`compliance.py` 同意条款（AION/GAC→GAC Group）+ 防臆造正则 `_NON_UT_RE`（Hyper→HYPTEC）+ `static/index.html` 四语言问候语 + `README.md` 同步
- [ ] **子项 4**：（条件）`compliance.py:134` `_NON_UT_RE` 补传祺车型（待传祺车型数据）
- [ ] **子项 5**：（条件）传祺车型清单入库 `_MODEL_LINEUP`（待用户提供数据）
- [x] **子项 6**：竞品清单回归验证（✅ 传祺/HYPTEC 未被误伤；评测集跑通待 numpy 就绪）

> 子项 1-3 是核心（必做）；子项 4-5 依赖 D1 决策与外部数据；子项 6 收尾。

---

## 7. 验收方式

1. **代码级**：全库搜索确认无残留「parent brand」「sub-brand」「Hyper brands」「昊铂Hyper」等错误表述；`HYPTEC` 拼写统一。
2. **行为级**（`CSAPP_LLM_MODE=rule` 无 key 也能跑，但品牌表述在 prompt 层，需 deepseek 验证）：
   - 问「你是谁」→ 自我介绍含三品牌正确口径（或 B 口径）。
   - 问「传祺有哪些车」→ 按 D1 决策正确回应（B：认知正确 + 引导回 AION/昊铂；A：列出传祺车型）。
   - 问「昊铂 GT 马力多少」→ 走 `_NON_UT_RE` 防臆造兜底（不编造参数）。
3. **回归**：`tools/eval_intent_ml.py` 意图评测仍 ≥98%；跑一遍 `python -m csapp.cli --market AU` 冒烟。

---

## 8. 风险与回滚

| 风险 | 影响 | 缓解 |
|---|---|---|
| 品牌表述改错导致 LLM 自我介绍混乱 | 高（直面用户） | 4 处口径统一；先改 json（生效层）+ py 兜底，双处同步 |
| 传祺车型数据不准确（D1=A 时） | 中（误导用户） | 严格「只引用清单内数值」约束沿用；详细参数一律「暂无信息」 |
| `find_competitor` 误伤传祺/HYPTEC | 中（自家品牌被当竞品 → 误触发留资兜底） | 子项 6 回归；必要时加白名单 |
| 品牌结构改了但车型清单没跟上 | 低 | 分项 commit，可独立 revert |

**回滚**：每个子项独立 commit，出错 `git revert <子项 commit>` 即可；无需整体回退。

---

## 9. 当前状态

- [x] D1 已拍板：**A. 3 品牌全量推荐**（2026-09-08）
- [x] D2 自我介绍口径：随 A，采用「广汽集团旗下传祺/AION/昊铂三大品牌」口径
- [x] D3 昊铂英文：Hyper → HYPTEC
- [x] 子项 1-3 + 3.5（品牌结构纠正 + 遗漏修正）已落地 commit（分支 `feat/brand-structure-fix`）
- [x] 子项 6 竞品回归验证通过
- [ ] 传祺海外车型清单（外部数据，待用户提供 → 子项 4-5）
- [ ] 意图评测冒烟（待 numpy 就绪后跑 `tools/eval_intent_ml.py`）
