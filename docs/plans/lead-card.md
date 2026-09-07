# 留资卡片（Lead Card · 前端可交互组件）

> 全局方案：bot 触发留资话术后（自然引导 / 知识缺失兜底），回传可交互卡片给前端 —— 用户可点击、可输入、可一键确认。
> 影响范围：bot 回复协议、server 端卡片装配、已有 `/api/v1/lead` 落库、前端组件抽象层。

---

## 0. 背景

**问题**：bot 经常引导用户"留个手机或邮箱"（自然留资 + 知识缺口兜底），但**只能让用户自由文本回复**。这导致：
- 用户留的电话/邮箱**在非 contact 步骤会被无视**（`_collect_contact` 只在 `expect=="contact"` 触发，卡片状态机外的话术采集不到号码）；
- 用户体验欠佳：bot 说"留手机"但只能重新打字，打完还要等 bot 自由识别；
- **没有任何前端表单/按钮**让用户一键授权确认或快速输入。

**用户要求**（2026-09-08 01:20 输入）：
> 我想在留资回复后，再给用户发一张留资卡片。方便用户点击，假如可以获取到用户的电话，那就发一个确认消息卡片；假如获取不到，就发一个用户输入电话或邮箱的卡片。

**用户补充说明**（2026-09-08 01:25）：
> "可以获取到用户手机"，是指**用户曾经在系统里留过的手机号**；除此外，bot 不应从对话里抓号码，**只能让用户自己留**。

**系统现状确认**：当前 cs-bot-aion 没有外部用户系统（无 `users` / `customers` / `accounts` 表），前端只有 `localStorage` 随机生成的访客 id（`u_xxx`）。访客跨会话留过的 `phone/email` 存在 `leads` 表里，按 `user_id` 可查到历史 → **本方案就以"访客跨会话历史"作为"已留"的唯一信号**，不接外部 CRM / IDP。后续真有用户系统时再替换数据源（抽象层后置）。

---

## 1. 现状盘点（端到端）

### 1.1 留资触发的两条路径

#### 路径 A：自然引导（卡片状态机的 contact 步）
```
用户提问 → 意图锁 → run_card.expect=="contact"
  → _collect_contact() 抓 phone/email → state.collected
  → _contact_valid() 按市场正则校验
  → 校验通过 → session.lead_id / lead_record 落库
  → target_reached=True → 前端"绿条已留资 + leadId"
```
✅ 已有链路，全栈通畅。**唯一缺口**：仅当走到 contact 步才采。

#### 路径 B：知识缺失兜底话术（post-processing，不走卡片状态机）
```
bot 回复命中 is_knowledge_gap 信号 →
  _apply_knowledge_gap_lead() 追加话术文本 →
  仅追加文本，**不**触发 _collect_contact / lead_id / lead_record
```
❌ **完全脱钩**：bot 提示用户留号了，但代码层连采都没采。

### 1.2 server→前端的现有契约

`/api/v1/chat` 返回（关键字段）：
```
{
  "reply": "<纯文本消息>",       ← 仅文本，无结构化
  "leadId": "lead_xxxx" | null,
  "leadRecord": {...} | null,
  "targetReached": bool,
  "state": {"collected": {"phone":..., "email":...}, "stepIndex":...},
  "consentVersion": "v1" | null
}
```
**没有 `components / cards / actions / quick_replies` 这类结构化字段**。

### 1.3 已有但**未被前端使用**的旁路端点

```
POST /api/v1/lead
  body: {sessionId, lead:{...}, consent, consentVersion}
  → server.py:129-141 → compliance.build_lead_record → db.insert_lead()
```
完全独立于会话状态机，**正好是"留资卡片直接提交"预留的天然挂接点**。当前没有任何前端调用。

FastAPI 版 `api.py:50-54` 是同样端点但**只回显未真正落库**（production 同步一致性问题）。

### 1.4 接缝点表

| # | 接缝点 | 当前 | 目标 |
|---|---|---|---|
| S1 | `/api/v1/chat` 响应 schema | 无结构化卡片字段 | 新增 `components: []` 数组 |
| S2 | pipeline 在何时拼卡片 | 没拼过 | reply 后处理：留资话术触发后，自动拼 lead card |
| S3 | `_collect_contact` 触发时机 | 仅 `expect=="contact"` 时 | **前移到任何 step**：每次 pipeline.chat 都先扫用户消息提取 phone/email |
| S4 | state.collected 是否已拿到电话 | pipeline 层可查 | **组件装配层根据这个 flag 选 lead_input / lead_confirm** |
| S5 | 前端是否有 `renderComponent()` 抽象 | 没有 | 新增：按 schema 渲染表单 / 确认卡 |
| S6 | POST `/api/v1/lead` 是否落库 | server.py 落库 ✅；api.py 没落库 ❌ | FastAPI 版也接 `db.insert_lead` |
| S7 | 前端是否会调 `/api/v1/lead` | 不会 | lead_input 提交按钮 → 调它 → reload 显示 lead_confirm |
| S8 | 已有 lead_id 时是否换卡 | N/A | 用户已留号 → 自动切到 lead_confirm（不再发 form） |

---

## 2. 设计目标

| # | 目标 | 度量 |
|---|---|---|
| G1 | 任何留资话术触发后，前端都能拿到一张可交互卡片 | E2E：泰国问题回复后，response.components 含 lead_card |
| G2 | 已知电话时不再问，节省交互成本 | 单测：state.collected.phone=非空 → 卡片 type=lead_confirm |
| G3 | 用户在任意步骤随手留的号立即被采集 | 单测：非 contact 步输入号 → state.collected.phone 被填 |
| G4 | POST `/api/v1/lead` 真正双库落库 | E2E：调接口 → db.leads 有新行 |
| G5 | 旧 reply-only 客户端不被破坏 | 兼容：`components` 字段缺省为 `[]`，旧前端无感 |
| G6 | 全 4 语言（zh/en/th/es）一致体验 | 文案/placeholder 按 conv_lang 翻 |

---

## 3. 方案分层（前-中-后 + 配套）

```
前端 ──────► 后端 ──────► SQLite
│            │              │
│ 前层（S5）   中层（S2/S3/S4）  后层（S6/S7）
│ 渲染卡片    拼卡片+采集       落库
└────────────────────────────────────────┘
```

### 3.1 前层：后端契约 + 前端渲染

**S1 · 响应 schema 新增 `components`**：
```jsonc
{
  "reply": "...",                       // 现有
  "components": [                       // 新增，缺省 []
    {
      "type": "lead_input",             // 或 "lead_confirm"
      "id": "lead-card-sess_afc",       // 用于去重 / 替换
      "lang": "th",
      "fields": ["phone", "email"],
      "labels": {                       // 多语言
        "phone": "เบอร์โทรศัพท์",
        "email": "อีเมล",
        "submit": "ส่งข้อมูล",
        "consent": "ฉันยินยอมให้ GAC/AION เก็บข้อมูลติดต่อกลับ",
        "phonePlaceholder": "เช่น 0812345678",
        "emailPlaceholder": "name@example.com"
      },
      "policyUrl": "/privacy",          // 隐私协议链接（可后置先留接口）
      "consentVersion": "v1"
    }
  ],
  // lead_confirm 版：
  {
    "type": "lead_confirm",
    "id": "...",
    "lang": "th",
    "phone": "0812345678",              // 回显（部分隐藏：138****8888）
    "email": null,
    "specialistHint": "เจ้าหน้าที่ GAC/AION จะติดต่อกลับภายใน 1 วันทำการ",
    "leadId": "lead_xxxx"
  }
}
```

**S5 · 前端 `renderComponent(comp)`**：
```js
function renderComponent(c) {
  const box = document.createElement('div');
  box.className = 'card card-' + c.type;
  if (c.type === 'lead_input')  box.appendChild(buildLeadInput(c));
  if (c.type === 'lead_confirm') box.appendChild(buildLeadConfirm(c));
  document.getElementById('messages').appendChild(box);
  // 同一 id 的旧卡先 remove，避免重复
}

function buildLeadInput(c) {
  const f = document.createElement('form');
  // ... input.phone, input.email, consent.checkbox, btn.submit
  f.onsubmit = async (e) => {
    e.preventDefault();
    if (!f.consent.checked) return;
    await fetch('/api/v1/lead', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        sessionId: SESSION_ID,
        lead: { phone: f.phone.value, email: f.email.value },
        consent: true,
        consentVersion: c.consentVersion
      })
    });
    // 本地乐观更新：替换为 lead_confirm
    RENDER({'type':'lead_confirm', 'lang':c.lang, 'phone':f.phone.value, 'email':f.email.value, 'specialistHint':'...', 'leadId':'pending'});
  };
}

function buildLeadConfirm(c) {
  const mask = maskPhone(c.phone);
  // ... 显示 "我们将以 138****8888 与您联系，专员将于 1 个工作日内回电"
}
```

### 3.2 中层：后端装配 + 采集前移

**S4 · 卡片类型选择（pipeline 层 / 改自 `csapp/components.py`）**：
```python
# pipeline.py 留资话术追加完成后（_apply_knowledge_gap_lead 之后）
# 或 run_card 返回 contact 步之后；任一"留资触发"路径都走这里：
def _resolve_lead_contact(session, db, market):
    """返回 (phone, email, source) —— source∈{"collected","history","none"}。
    - collected: 本会话当前 collected 已填
    - history:   本会话没填，但同一 user_id 在 leads 表里历史留过（去重取最近一条）
    - none:      都没有，需要用户输入
    """
    phone = (session.collected.get("phone") or "").strip() or None
    email = (session.collected.get("email") or "").strip() or None
    if phone or email:
        return phone, email, "collected"
    # 跨会话历史
    user_id = getattr(session, "user_id", None)
    if user_id and market:
        hist = db.find_recent_lead_by_user(user_id, market)
        if hist and (hist.get("phone") or hist.get("email")):
            return (hist.get("phone") or None), (hist.get("email") or None), "history"
    return None, None, "none"


def _build_lead_card(session, db, market, reply_lang):
    phone, email, source = _resolve_lead_contact(session, db, market)
    base = {"id": f"lead-card-{session.id}", "lang": reply_lang}

    if phone or email:
        # 已拿到（任一来源） → 确认卡
        return [{**base, "type": "lead_confirm",
                 "phone": phone, "email": email,
                 "source": source,           # 调试/前端可读
                 "specialistHint": _SPECIALIST_HINT[reply_lang],
                 "leadId": getattr(session, "lead_id", None)}]
    # 未拿到 → 输入卡
    return [{**base, "type": "lead_input",
             "fields": ["phone", "email"],
             "labels": _LEAD_LABELS[reply_lang],
             "consentVersion": "v1"}]
```

**S9 · `db.find_recent_lead_by_user()` 新接口（`csapp/db.py`）**：
```python
def find_recent_lead_by_user(user_id, market, limit=1):
    """按 user_id + market 查 leads 表最近 N 条；返回 list[{phone,email,consent_at,...}]。
    SELECT phone, email, consent_at, intent, created_at
    FROM leads
    WHERE user_id = ? AND market = ?
    ORDER BY created_at DESC
    LIMIT ?;
    """
```

- 只读，不写。配合现有 `dedupe_key` 不冲突。
- 用户**没**注册系统的当下，这就是"已留"的唯一信号。后期接外部用户系统时，整个函数体替换为 HTTP 调用即可（pipeline / components 调用面不变）。

**S3 · `_collect_contact` 前移**：
```python
# pipeline.chat() 入口处，意图识别 + run_card 之前
# 先扫一次用户消息，任何 step 都能采
def chat(...):
    # ...现有语言检测...
    # 新增：先尝试采集联系方式（不依赖 expect）
    from . import cards as cards_mod
    cards_mod.try_collect_contact_early(session, message)
    # ...继续原流程
```

`try_collect_contact_early()` 用同一套 `_COLLECT_RE` 正则但跳过 `_contact_valid` 强制校验（宽松采，由前端提交时再校验）。

**S2 · 何时拼卡片**：
- 任一留资触发路径都走 `_build_lead_card`：
  1. `_apply_knowledge_gap_lead` 触发（兜底话术追加成功）
  2. `run_card` 返回的某步 `expect=="contact"`（自然留资步）
- 一个意图一轮只发一张 lead 卡片（id 用 session.id 锚定，前端去重）
- 拼好的卡片放进 `resp["components"]`

### 3.3 后层：API 落库 + 前端提交

**S6 · FastAPI 版 `/api/v1/lead` 落库**：
```python
# api.py — 把"回显"换成 db.insert_lead（同 server.py:129-141 逻辑）
@app.post("/api/v1/lead")
async def api_lead(req: Request):
    body = await req.json()
    rec = build_lead_record(
        session_id=body.get("sessionId"),
        market=...,
        phone=body.get("lead",{}).get("phone"),
        email=body.get("lead",{}).get("email"),
        consent=body.get("consent", False),
        consent_version=body.get("consentVersion", "v1"),
    )
    db.insert_lead(rec)
    return {"ok": True, "leadId": rec["leadId"]}
```

**S7 · 前端提交即触发**：`buildLeadInput` 的 fetch 已在 §3.1 给出。

### 3.4 配套

- **P1 · `dedupe_key` 双重落库去重**：`db.insert_lead` 已按 `dedupe_key + market` 去重（`db.py:72-94`），同一 session 重复提交不会被插多行。
- **P2 · consent 文本按 lang 翻**：复用 `_FAB_REPL`/`_NO_KNOWLEDGE_LEAD` 的 4 语言 dict 模式。
- **P3 · 隐私协议页 `/privacy`**：占位路由，后期补。
- **P4 · 卡片样式**：与现有绿色 "ok" 条同色调，深色背景上文字用浅色 / 反之亦然（按 IDE theme）。
- **P5 · 反 XSS / 反重复渲染**：`renderComponent` 用 `esc()` 跟现有气泡一致；同一 session.id 的卡片去重。

---

## 4. 改动清单（建议按 commit 拆分）

| Phase | commit | 文件 | 内容 |
|---|---|---|---|
| 0 | `docs(plan): lead card 方案` | `docs/plans/lead-card.md` | 本文件 |
| 1.0 | `feat(db): find_recent_lead_by_user(user_id, market)` | `csapp/db.py` | **新接口 S9**：按 user_id 查最近 leads；先具体后抽象 |
| 1.1 | `feat(components): 新建模块 _build_lead_card + 4 语言字典` | `csapp/components.py` | `new` 整个模块；`_resolve_lead_contact` + `_build_lead_card`；`source="collected"/"history"/"none"` |
| 1.2 | `feat(pipeline): components 字段进响应 + 触发挂载点` | `csapp/pipeline.py` | `_response` 加 `components: []`；调用 `_build_lead_card` 装配；触发于 (a) `_apply_knowledge_gap_lead` 追加成功 (b) `run_card` 返回 `expect=="contact"` |
| 1.3 | `feat(cards): try_collect_contact_early 前移到 pipeline 入口` | `csapp/cards.py`、`csapp/pipeline.py` | `try_collect_contact_early()` 让本会话任何 step 都能采到 |
| 1.4 | `feat(api): api.py /api/v1/lead 接 db.insert_lead` | `csapp/api.py` | 与 server.py:129-141 同落库逻辑 |
| 2.1 | `feat(ui): renderComponent 抽象 + lead_input / lead_confirm` | `csapp/static/index.html` | 新增 `renderComponent()`；表单 + 确认卡 + consent 复选 |
| 2.2 | `feat(ui): session.id 去重 + 提交后本地乐观更新` | `csapp/static/index.html` | `renderComponent` id 去重；提交后前端本地替换为 `lead_confirm` |
| 3.1 | `test: lead card 单测 + E2E` | `tools/` 或脚本 | `_build_lead_card` 4 lang × 3 source；`find_recent_lead_by_user` mock；E2E 6 场景 |

---

## 5. 验收

### 5.1 单测
- `_build_lead_card(intent, lang)` × 4 lang × 2 状态（电话有/无）：产出 `lead_input` 或 `lead_confirm` schema 全字段非空
- `try_collect_contact_early`：随机话术（"我的手机 13812345678"）→ state.collected.phone 被填；纯文本不变
- `_SPECIALIST_HINT`/`_LEAD_LABELS` 4 语言字典完整

### 5.2 E2E curl（rule 模式）
1. **泰国产品问题 + 本会话没留过** → reply 包含兜底话术 → `response.components: [{type:"lead_input"}]`
2. **本会话给邮箱**（任意 step） → `state.collected.email` 填入 → components 仍 lead_input（缺 phone 不能算"已留"）
3. **本会话给电话** → `components: [{type:"lead_confirm", source:"collected", phone:"138****8888"}]`
4. **新会话 + 跨会话历史**（先用 user_id=X 留过号，POST `/api/v1/lead` 落库）→ 新提问触发留资卡 → `components: [{type:"lead_confirm", source:"history", phone:"138****8888"}]`（即便新会话没填）
5. **POST `/api/v1/lead` 双通道落库** → SQLite `leads` 表新增一行；`api.py` 与 `server.py` 落库字段一致
6. **自然 contact 步回归** → bot 走到 contact 步 → lead_id 正常 + components 也发 lead_confirm（**两者并存**，不冲突）
7. **跨会话号码变更** → lead_confirm 引导文案里"如号码有变，请告诉我新号" → 用户直接在新会话自由输入 → 覆盖旧号

### 5.3 兼容性
- 旧 client：`components` 缺省为 `[]`，reply 文本不变，无破坏
- 旧 cards：`_collect_contact` 在 contact 步的行为**不变**（前移是叠加，不会替换 contact 步的采集）

---

## 6. 风险与回滚

| 风险 | 缓解 |
|---|---|
| 前端渲染 XSS / 样式跑偏 | `esc()` 与现有气泡一致；多语言文案用安全字符集 |
| 同一 session 重复拼卡片 | id 用 session.id 锚定；前端按 id 去重；后端用 `seen_card` 标记 |
| `_collect_contact` 前移误采无关数字 | 复用 `_CONTACT_RE` + `_contact_valid`；自由文本里的订单号、年份不会落进 collected.phone（要过市场正则） |
| `_apply_knowledge_gap_lead` 已发留资文本 + 后端再发 lead_card → 重复引导 | reply 文本（"留手机邮箱"）与卡片（UI 入口"点击填写或确认"）**语义不重叠**，各有分工 |
| 跨会话复用过期号码 | lead_confirm 文案加"如号码有变，请告诉我新号"；新会话用户自由输入即可覆盖 |
| `find_recent_lead_by_user` 查表失败 / db 未初始化 | 静默 catch → fallback 到 `"none"` 路径（发 lead_input），不阻断主流程 |
| consent 文本翻译不到位 | 先复用现有 `_NO_KNOWLEDGE_LEAD` 文风，运营/法务后续替换 |
| privacy 链接 `policyUrl` 暂缺 | 占位 `/privacy`，前端 href 占空，先不强制 |

**回滚**：每个 Phase 单独 commit + branch，单 revert 即可。

---

## 6. 风险与回滚

| 风险 | 缓解 |
|---|---|
| 前端渲染 XSS / 样式跑偏 | `esc()` 与现有气泡一致；多语言文案用安全字符集；卡片 div 与现有 ok 条同样的 CSS hook |
| 同一 session 重复拼卡片 | id 用 session.id 锚定；前端按 id 去重；后端 `_build_lead_card` 用 `seen_card` 标记位 |
| `_collect_contact` 前移误采无关数字 | 复用 `_CONTACT_RE` 现有正则 + 市场校验（`_contact_valid`）；自由文本里的订单号、年份不会被误识为电话 |
| `_apply_knowledge_gap_lead` 已发留资文本 + 后端再发 lead_card，造成重复引导 | 卡片文本与 reply 兜底话术分开：reply 含话术（"留手机邮箱"），卡片是 UI 入口（"点击填写或确认"），**不要把卡片文案塞进 reply** |
| FastAPI api.py 与 ThreadingHTTPServer server.py 双通道 | 两条路都要走 `db.insert_lead`，单测验证两边落库一致 |
| consent 文本翻译不到位 | 先复用现有 `_NO_KNOWLEDGE_LEAD` 文风，运营/法务后续替换 |
| privacy 链接 `policyUrl` 暂缺 | 占位 `/privacy`，前端 href 占空，先不强制 |

**回滚**：每个 Phase 单独 commit + branch，单 revert 即可。

---

## 7. 当前状态

- **方案文件**：`docs/plans/lead-card.md`（本文件）
- **代码状态**：未动，等用户拍板
- **服务**：`http://127.0.0.1:8020` 仍在跑（task `lgQ8E8`）

---

## 8. 待用户决策

回答前请翻完本文。关键设计选择需要在动手前明确：

### 决策 A：模块边界 ✅ 已锁
`csapp/components.py`（独立模块），将来扩展 quick_replies、action_button 同结构。

### 决策 B：采集前移的边界 ✅ 已锁
pipeline.chat 入口（在 LLM/意图/run_card **之前**）采一次：覆盖范围最大，且不影响后续流程。

### 决策 C：卡片呈现位置 ✅ 已锁
(i) 紧跟 reply 末尾追加（用户体验自然、不破坏 reply 文本已读的语义）。

### 决策 D：已知电话的回显方式 ✅ 已锁
(i) 部分隐藏 `138****8888` + 文案"专员将于 1 个工作日内与您联系"。

### 决策 E：lead_confirm 来源（**新增**）
phone/email 怎么算"已留"，优先看哪个源？
- ✅ **已锁**：
  - 来源 1：本会话 `state.collected.phone/email`（前移 `_collect_contact` 填入）
  - 来源 2：跨会话 `leads` 表按 `user_id + market` 查最近一条（`find_recent_lead_by_user`）
  - 数据源不接外部 CRM / IDP（暂留 mock 化 ladder，先具体后抽象）
- 任一来源非空 → 发 lead_confirm（响应里标 `source: "collected" / "history"`）
- 都为空 → 发 lead_input

### 决策 F：多语言文案来源 ✅ 已锁
4 语言文案我**起草一版**，commit 后你 review、修订单独 commit 替换。

### 决策 G：分支命名 ✅ 已锁
`feat/lead-card` 基于 `main` HEAD = `91c76ff`（含刚合并的 knowledge-gap-fallback）。

### 决策 H（新增）：FastAPI / Threading 双通道
`api.py:50-54` 当前是回显未落库。这次一起改成真正落库吗？
- ✅ **已锁**：一起改（保持双通道一致）

---

## 9. 反向说明 / 非目标

- ❌ **不接外部用户系统**（CRM / IDP）。本方案"已留"信号 = 跨会话 `leads` 表历史查到非空。后续真有用户系统时，整体替换 `find_recent_lead_by_user` 实现即可，pipeline / components 调用面不变。
- ❌ **不做**完整的 CRM 双向同步：POST `/api/v1/lead` 仅落 `leads` 表。
- ❌ **不做**前端多轮卡片叠加：每个 session 一轮只允许出现一张 lead card，由 session.id 锚定。
- ❌ **不动**现有卡片状态机（cards.py）的 contact 步行为；本方案是**叠加**，不是替换。
- ❌ **不修改** LLM prompt（`_knowledge_gap_lead` 模板也不动），知识缺失 + lead card 是两个独立接缝点。
- ❌ **不考虑**微信 / WhatsApp 等其他渠道：仅 web 前端。
