# 咨询效果分析面板 + 每轮 Trace 采集 — 设计方案

> 目标：对用户咨询历史做**效果分析**，反哺优化智能客服。
> 交付两个能力：① 展示"用户 ↔ AI 回复"完整历史；② 展示每轮 AI 回复的
> Trace（用户消息 → 模型回复之间各阶段的流转情况）。
> 分析面板为**独立页面**（独立路由 `/admin`，独立 HTML），与对外访客聊天页完全隔离，访客页不加入口。
>
> 已确认决策：同服务独立路由 `/admin`（口令保护）｜新建独立分析库表（不随 90 天会话清理）
> ｜效果评估首版用确定性规则信号（零额外 LLM 成本）。

---

## 1. 现状盘点（动工前的基线）

### 1.1 会话与数据现状

| 项 | 现状 | 位置 |
|---|---|---|
| 会话+历史 | 每会话一个 JSON，history 每轮仅 `{user, bot, intent, emotion_score, components, t}`，**无中间过程** | `data/sessions/{sessionId}.json`，`csapp/state.py` |
| 业务结果 | SQLite：`leads / escalations / rescue_tickets` | `data/kd.db`，`csapp/db.py` |
| 调试日志 | 平铺事件流，无 trace_id/会话轮次关联键，LLM raw 截断前 600 字符 | `data/debug.jsonl`，`csapp/debug.py` |
| 保留策略 | 会话 JSON 90 天清理；debug.jsonl 无上限追加 | `config.ARCHIVE_DAYS` |
| 访客 UI | 单一 `index.html`：左侧历史分组列表 + 点开只读回放 + 已结束禁输入 | `csapp/static/index.html` |
| 管理/分析 UI | **不存在** | — |

### 1.2 现有接口（只读/写入均面向访客）

`/api/v1/chat`、`/api/v1/sessions?userId=`、`/api/v1/session?sessionId=`、`/api/v1/session/end`、
`/api/v1/lead`、`/api/v1/escalate`、`/api/v1/stats`、`/api/v1/debug`。

### 1.3 一次对话轮已存在的中间链路（代码中已发生、但未落盘）

```
①会话恢复/空闲判定 → ②语言检测(lang/reply_lang) → ③消息提取市场+市场路由(market, market_source)
→ ④早采联系方式 + 是否已留资 → ⑤prompt injection 检测(命中直接短路)
→ ⑥RAG kb.context(topk=3) + 经销商上下文 → ⑦LLM respond(消息+上下文+state_desc+语言+最近历史)
→ 返回 {intent, conf, question, response} → ⑧意图规整(normalize/延续保持/紧急预检/问候校正)
→ ⑨空回复兜底(知识缺失话术) → ⑩意图分支:切换/锁定/澄清(轮数计数)/卡片状态机 run_card
→ ⑪空泛反问二次重试(罕见第二次 LLM 调用) → ⑫情绪≥阈值先确认转人工
→ ⑬回复质量(禁AI感/去重/截断) + 输出过滤 + 竞品兜底 + 车型事实护栏
→ ⑭知识缺失追加/已留资话术清洗 → ⑮结束判定 → ⑯组装(citations/answer_images/lead卡)+append_turn+落库
```

**结论：从用户消息到模型回复的"流转"逻辑都在 `pipeline.chat()`（`csapp/pipeline.py`）及
`llm.respond()`（`csapp/llm.py`）、`cards.run_card()`（`csapp/cards.py`）中，但没有任何结构化记录。**
这是"效果为什么差"无法定位的根本缺口。效果差的根因几乎全部藏在这些阶段里：
LLM 判错意图 / RAG 没召回 / 澄清转圈 / 护栏误伤 / 解析失败 / 知识缺失被兜底成留资等。

---

## 2. 总体架构

```
┌──────────── 生产运行侧(改造点,旁路采集,不影响对话) ────────────┐
│  pipeline.chat() / llm.respond() / cards.run_card() 埋点       │
│   → trace 收集器(csapp/trace.py, 每请求线程级缓冲)             │
│   → 每轮结构化 trace: {阶段steps+耗时, LLM原始输出, RAG命中,    │
│                        意图规整链, 卡片/槽位, 护栏命中,         │
│                        自动问题信号 issues}                    │
│   → 落盘 data/analytics.db(独立分析库)                         │
└───────────────────────────┬────────────────────────────────────┘
                            ▼
┌──────────── 独立分析面板(只读 · 访客页无入口) ─────────────────┐
│  csapp/static/admin.html + /admin 路由 + /api/v1/admin/*       │
│  ① 概览:KPI + 问题信号分布 + Top 知识缺口                      │
│  ② 会话检索/过滤 → ③ 会话详情                                  │
│     上: 逐轮对话流(用户/AI 气泡, 忠实回放)                     │
│     下: 每轮可展开的 Trace 时间线(语言→市场→RAG→LLM→意图→      │
│          卡片→护栏→终文, 每跳耗时, 命中信号标徽章)             │
└────────────────────────────────────────────────────────────────┘
```

原则：全部沿用项目"标准库零依赖"技术栈；采集为**旁路**，任何失败都不影响主对话；
分析面板**只读**，写库仅在采集侧发生。

---

## 3. 采集层：每轮 turn 结构化 trace

### 3.1 采集方式（对现有逻辑改动最小）

- 新增 `csapp/trace.py`：`TraceRecorder`（线程级缓冲，`start()/step()/flush()`）+ 落库封装。
  服务为 `ThreadingHTTPServer`（每请求一线程、`chat()` 同步执行），线程本地缓冲天然成立。
- `pipeline.chat()` 重命名内部实现为 `_chat()`，外层保留同名 `chat()` 作包装：
  `begin trace → try: return _chat(...) → finally: flush trace`。
  这样 `_chat()` 内**任何早退路径**（如 injection 短路、错误）都不会丢 trace。
- `llm.respond()` / `cards.run_card()` 等子模块通过 `trace.step()` 向当前线程缓冲追加事件，
  **不改函数签名**（仅在各函数内部加 1~3 行埋点），并对每阶段记录 `ms` 耗时。
- 同步在 `debug.py` 里为相关事件带上 `sessionId+round` 关联键，保留 debug.jsonl 兼容性。

### 3.2 单轮 trace 结构（示例）

```json
{
  "traceId": "trc_xxxx", "sessionId": "sess_xxxx", "round": 3, "t": "2026-09-08T10:00:00Z",
  "userMsg": "...", "botReply": "...",
  "steps": [
    {"stage": "lang",       "detail": {"detected": "zh", "replyLang": "zh"}, "ms": 1},
    {"stage": "market",     "detail": {"market": "AU", "source": "auto"}, "ms": 2},
    {"stage": "injection",  "detail": {"hit": false}, "ms": 1},
    {"stage": "rag",        "detail": {"topDocs": [{"docId": "...", "score": 0.82}], "dealerHits": 0}, "ms": 45},
    {"stage": "llm",        "detail": {"mode": "deepseek", "callCount": 1, "parseOk": true,
                                       "promptLen": 5200, "rawOutput": "<截断前800字>"}, "ms": 6100},
    {"stage": "intent",     "detail": {"llmRaw": "prod", "normalized": "product-inquiry",
                                       "conf": 0.9, "locked": true, "corrections": []}, "ms": 0},
    {"stage": "card",       "detail": {"stepIndex": 0, "expect": null, "ok": true}, "ms": 3},
    {"stage": "guards",     "detail": {"stripAi": true, "dedup": false, "truncate": false,
                                       "outputFilter": false, "competitor": false,
                                       "modelFacts": false, "kgAppended": true}, "ms": 2},
    {"stage": "end",        "detail": {"ended": false}, "ms": 0}
  ],
  "llm":     {"callCount": 1, "retried": false, "failed": false, "parseFail": false},
  "intent":  {"final": "product-inquiry", "conf": 0.9, "emotionScore": 0,
              "clarifyRounds": 0, "totalRounds": 3},
  "citations": [...], "components": [{"type": "lead_input"}],
  "outcome": {"ended": false, "endedReason": null, "escalated": false},
  "latencyMs": {"total": 6200, "llm": 6100, "rag": 45},
  "issues": ["knowledge_gap"]
}
```

要点：
- LLM 原始 prompt/输出**截断后**落库（定位问题的命门：看模型当时收到什么、回了什么）；
- 用户联系方式等 PII **掩码后**再入库（复用 `components._mask_phone/_mask_email` 思路）；
- 每条 trace 由 `flush` 时统一落库，**采集失败绝不抛向对话主流程**（try/except 吞掉并记 debug）。

### 3.3 确定性问题信号（首版，零额外成本）

| 信号码 | 含义 | 触发点 |
|---|---|---|
| `llm_failed` / `llm_retry` / `llm_parse_fail` / `empty_reply` | 模型调用失败/重试/输出解析失败/空回复被兜底 | llm.py `_run`/`respond` |
| `knowledge_gap` | 回答不上 → 知识缺失话术兜底（含意图） | pipeline ⑨⑭ |
| `vague_reply` | 首轮空泛反问被二次重试 | pipeline ⑪ |
| `clarify_loop` | 连续 ≥N 轮澄清无进展（阈值取 `NO_PROGRESS_MAX_CLARIFY`） | pipeline ⑩/⑮ |
| `user_repeat` | 用户用相似措辞重复提问（首答未解决） | 文本相似度 vs 前几轮 user 消息 |
| `escalated` | 转人工（reason：emotion / clarify-timeout / round-limit） | pipeline ⑫/⑮ |
| `guard_*` | 护栏命中：注入/竞品/输出过滤/车型事实纠错/已留资话术清洗 | pipeline ⑤⑬⑭ |
| `slow` | 总延迟或 LLM 阶段超阈值（如 total>8s） | latency 分段 |

信号同时冗余成可筛选的列（见 §4 表结构），并可下钻到具体 trace。

---

## 4. 存储层：独立分析库

- 新文件 `data/analytics.db`，独立于 `kd.db` 与会话 JSON（**不随 90 天清理**），
  新模块 `csapp/analytics.py`（沿用 db.py 的 sqlite3 零依赖写法；retention 单独配置，默认 180 天可调）。

```sql
-- 会话级事实(会话结束时 upsert)
CREATE TABLE IF NOT EXISTS a_sessions (
  session_id TEXT PRIMARY KEY,
  user_hash   TEXT,            -- userId sha1 前 12,可聚合不可反查
  market TEXT, language TEXT, intent_main TEXT,
  created_at TEXT, ended_at TEXT, ended_reason TEXT,
  total_rounds INTEGER, resolved INTEGER, escalated INTEGER, lead_captured INTEGER,
  avg_emotion REAL, flags_json TEXT,
  created_ts INTEGER            -- 检索/清理用 epoch
);

-- 每轮 trace(核心表)
CREATE TABLE IF NOT EXISTS a_turns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trace_id TEXT UNIQUE,
  session_id TEXT NOT NULL,
  round_idx INTEGER NOT NULL,
  t TEXT, market TEXT, language TEXT, intent TEXT,
  user_msg TEXT, bot_reply TEXT,
  trace_json TEXT,              -- §3.2 全量结构
  issues TEXT,                  -- 逗号分隔信号码,便于 LIKE/IN 过滤
  outcome TEXT, latency_ms INTEGER,
  ts INTEGER
);
CREATE INDEX IF NOT EXISTS idx_turn_sess  ON a_turns(session_id, round_idx);
CREATE INDEX IF NOT EXISTS idx_turn_ts    ON a_turns(ts);
CREATE INDEX IF NOT EXISTS idx_turn_issue ON a_turns(issues);
CREATE INDEX IF NOT EXISTS idx_sess_ts    ON a_sessions(created_ts);
```

- **旧数据回填工具** `tools/backfill_traces.py`：扫描 `data/sessions/*.json` + 合并 `debug.jsonl`
  （按会话与轮次序启发式对齐），对历史会话生成"部分 trace"（只含 user/bot/意图/结果层，
  内部阶段标 `partial: true`），一次性灌入分析库。

---

## 5. 服务层：独立只读 API

沿用 `server.py` 标准库 handler，新增（全部只读；需口令）：

| 路由 | 说明 |
|---|---|
| `GET /admin` | 独立分析面板 HTML（未授权 → 登录页） |
| `POST /api/v1/admin/login` | 校验 token（环境变量 `CSAPP_ADMIN_TOKEN`），下发会话 cookie |
| `GET /api/v1/admin/summary?from&to&market&lang&intent` | KPI + 问题信号分布 + Top 知识缺口（按 intent 聚合 `knowledge_gap` 轮 + 文本聚类） |
| `GET /api/v1/admin/sessions?q&market&lang&from&to&issue&ended&page&size` | 分页会话列表（带会话级摘要列） |
| `GET /api/v1/admin/sessions/{id}` | 会话详情：meta + 逐轮 `{user, bot, t, intent, issues, trace 摘要}` |
| `GET /api/v1/admin/turns/{traceId}` | 单轮完整 trace（展开原始输出等） |
| `GET /api/v1/admin/export?from&to` | JSON/CSV 导出（P2） |

鉴权：未设 `CSAPP_ADMIN_TOKEN` 且非 localhost 访问 → 403（默认只允许本机/内网调试）；
`/admin` 与 admin API 全部**不加到** `index.html`（访客页无入口、无链接）。

---

## 6. 展示层：独立分析面板 `/admin`

- `csapp/static/admin.html`：**完全独立于 index.html** 的单个 HTML（自有 CSS/JS，无代码共享耦合）。
- 三个视图（左侧导航切换）：

### 6.1 概览（效果看板）
- 筛选条：时间范围 / 市场 / 语言 / 意图 / 是否带问题信号；
- KPI 卡：会话数、消息轮数、结束率与结束原因分布、**解决率（goal/已结束且非负面）**、
  留资率、转人工率、无进展率、LLM 失败率、平均与 P95 延迟（总 / LLM / RAG 分段）；
- 信号排行：各类问题码数量与占比 → 点击下钻到会话列表；
- Top 知识缺口：`knowledge_gap` 按意图聚合的 Top 词条 → 输出"补 KB/FAQ"清单依据。

### 6.2 会话检索
- 按 user 摘要 / 市场 / 语言 / 意图 / 结束原因 / 含某信号过滤，分页表格，点行进详情。

### 6.3 会话详情（核心：历史 + Trace）
- **上半区**：逐轮对话流——用户气泡 + AI 回复气泡（含时间、意图、情绪、citations、组件卡），
  忠实回放会话 history；顶部会话摘要条（市场/语言/意图/结束原因/是否留资转人工/情绪轨迹）；
- **每轮回复下方**一条可折叠 **Trace 时间线**：
  `用户消息 → 语言检测 → 市场路由 → 注入检测 → RAG命中(top3+score) → LLM(展开:prompt摘要/原始输出/解析成败/重试) → 意图规整(原始→规整→校正) → 卡片步骤/槽位 → 护栏命中 → 最终文案(截断对比)`；
  每跳显示耗时；命中 `issues` 的跳点标红/黄徽章；支持**只看问题轮**的过滤（定位"这轮为什么答得差"）。
- 阶段可视化用纯 DOM/CSS（时间线纵向列 + 徽章），零图表库依赖。

---

## 7. 效果评估口径（首版）

- 自动信号见 §3.3，全部来自代码内确定性判定，**不引入额外 LLM 评审调用**；
- 优化动作清单（分析产出的"改哪里"）：
  1. Top 知识缺口 → 补 KB/FAQ（当前缺失集中在哪些 intent/问题）；
  2. 澄清/重复追问热点 → 调引导话术（`talk_scripts.json` / `intent.py` 阈值）；
  3. `guard_*` 高频 → 检查护栏规则是否误伤 / 提示词是否要收紧；
  4. `llm_parse_fail / vague_reply` 高频 → 检查 `llm.respond` 输出格式与提示词；
  5. 分段延迟高 → RAG vs LLM 性能定位（`latencyMs` 分段）。

---

## 8. 分期与验收

| 期 | 内容 | 验收 |
|---|---|---|
| **P0 采集** | `trace.py` + `analytics.py` 建表 + pipeline/llm/cards 埋点 + debug 关联键 + 旧数据回填工具 | 每轮对话后分析库多一条 trace；采集异常不影响对话；造数脚本可验证字段完整 |
| **P1 面板 v1** | `/admin` 路由 + 登录 + 会话检索 + 会话详情（历史回放 + 逐轮 trace 时间线） | 满足核心诉求：能看用户/AI 历史，能展开看任一 AI 回复的中间流转 |
| **P2 分析** | summary KPI + 信号聚合 + Top 知识缺口 + 导出 | 概览页给出可执行的"改哪里"清单 |
| **P3 闭环** | 面板内对问题轮打标/回写 → 沉淀知识缺口增补单与话术修订建议 | 优化项进入 KB/话术变更流程 |

> 建议 P0+P1 一次交付（用户可立即用上"历史 + Trace"查看能力），P2/P3 随后。

---

## 9. 变更文件清单（预估）

| 文件 | 变更 |
|---|---|
| `csapp/trace.py` | 新增：采集器 + 落库 |
| `csapp/analytics.py` | 新增：分析库表结构与读写 |
| `csapp/pipeline.py` | `chat()` 拆 `_chat()` + 包装；约 13 处阶段埋点 |
| `csapp/llm.py` | `respond()`/`_run()` 埋点（raw/重试/失败） |
| `csapp/cards.py` | `run_card()` 结果埋点（步骤/校验） |
| `csapp/debug.py` | 事件带 `sessionId+round` 关联键 |
| `csapp/server.py` | `/admin` 路由 + admin API + 口令校验 |
| `csapp/config.py` | `CSAPP_ADMIN_TOKEN` / 分析库保留天数 |
| `csapp/static/admin.html` | 新增：独立分析面板 |
| `tools/backfill_traces.py` | 新增：旧会话/debug 回填 |
| `deploy/README.md` | 部署说明（token 环境变量、保留策略） |
