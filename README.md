# cs-bot-aion · AION 海外智能客服

> 意图驱动 · 语言跟随 · 知识可溯源

面向 **泰国 / 澳洲**（及更多市场）的 **AION 埃安** 新能源车主与潜客的 **RAG 智能客服**：意图路由 + 状态机做引导转化，向量语义检索 + LLM 生成回答，知识内容按市场隔离、多语言原生。

- **P0** 问题解决 · **P1** 留资+预约转化 · **P2** 资料下载/咨询答疑
- 单轮**一次 LLM 调用**生成 `{意图, 置信度, 是否提问, 回复}`，回复**严格跟随用户当前消息语言**

---

## ✦ 功能特性

- **意图驱动**：LLM 判意图 → `normalize_intent` 规整 → 紧急安全预检(`emergency_hit`) → 关键词纠正 → 卡片状态机引导转化。
- **语言跟随**：回复按**用户当前消息语言**生成（中/英/泰/西原生，其它语言 LLM 兜底），不写死语言码；用户切语言即跟切。
- **知识可溯源**：检索来自本地化知识库，可回看每条回答的知识来源；FAQ/规格/文档/经销商分库管理。
- **RAG 向量语义检索**：`intfloat/multilingual-e5-large`（1024 维）离线索引 + 在线 top-k；模型共享单例 + 启动预热。
- **卡片状态机**：无需手写 4 语言模板，每卡只有 `state_desc`(给 LLM 的情景) + `expect`(校验模式) + 步骤/目标；校验/推进/合规留资/救援均为确定性 Python 逻辑。
- **市场路由**：开放任意市场码；按语言推断 / 手动粘性 / 消息里提取市场（如"印度市场"→IN）；仅 AU/THA 有专属知识库，其余回退 AU 库。
- **多语言**：中/英/泰/西为原生版本；FAQ 4 语言烘焙；泰文经 CMap 声调还原。
- **合规与安全**：PDPA/Privacy Act 同意条款、留资最小化 + 去重、prompt injection 检测、输出禁区过滤、联系方式保护。
- **回复质量护栏**（单次 LLM 调用内完成）：长度硬限、禁 AI 感/机器感、话术去重。
- **槽位收集护栏**：需输入槽位累计追问，超限转人工；已收集有效槽位不重问。
- **可观测**：debug 状态**默认开**，记录每轮 LLM 输入/原始/结果/重试到 `data/debug.jsonl`。

## ✦ 一次对话的流程

```
用户消息 → ① 会话恢复 → ② 语言检测 → ③ 市场路由 → ④ 内容安全(prompt injection)
        → ⑤ 一次 LLM respond(消息+向量上下文+步骤+对话语言+历史)
             → {intent, conf, question, response}  语言跟随当前消息
        → ⑥ 意图规整 + 紧急预检 + 关键词纠正 → 分流(切换/锁定/澄清)
        → ⑦ 卡片状态机 run_card(校验/推进/合规留资/救援) + 槽位超限转人工
        → ⑧ 回复质量(禁AI感/去重/长度) + 输出过滤 → 落库 + 持久化 + 返回
```

## ✦ 快速开始

**环境**：Python 3.9+；建议 Python 3.9。

```bash
# 1) 网页 Demo(零依赖,内置聊天窗,启动前 warmup 向量 ~30s)
python -m csapp.server --port 8020
# 浏览器打开 http://127.0.0.1:8020/  —— 免登录聊天窗,市场下拉 AU/THA/CN/ES/…/auto

# 2) 交互式 CLI(默认 AU 英文)
python -m csapp.cli --market AU

# 3) HTTP API(JSON)
curl -X POST http://127.0.0.1:8020/api/v1/chat -H 'Content-Type: application/json' \
  -d '{"message":"What is the range of the AION UT?"}'
# 数据查询:GET /api/v1/stats、/api/v1/leads、/api/v1/debug、/health
```

> 端口默认 8000，用 `--port` 指定。

### 依赖与配置

| 项 | 说明 |
|---|---|
| LLM | DeepSeek 直接 API(`https://api.deepseek.com`)。key 取 `DEEPSEEK_API_KEY` 环境变量或 `~/.dsh/.credentials.yaml`；失败重试 3x，兜底 `dsh --profile headless` |
| 向量模型 | `intfloat/multilingual-e5-large`。首次需从 `HF_ENDPOINT`(默认 `https://hf-mirror.com`)下载约 2.3G 到 `tools/emb_cache`；**若模型不可用则自动退化为关键词 FAQ + 结构化规格** |
| numpy | 自带 `tools/pylib`(vendored)，或 `pip install numpy` |
| 运行模式 | `CSAPP_LLM_MODE=deepseek\|rule\|none`(默认 `deepseek`)；`rule` 无需 key 即可跑通 |
| 调试 | `CSAPP_DEBUG=0` 关(默认开)；日志 `data/debug.jsonl` |
| 回复质量 | `CSAPP_REPLY_DAILY_MAX`(220)、`CSAPP_REPLY_MAX_CHARS`(300)、`CSAPP_REPEAT_SIM_THRESHOLD`(0.8)、`CSAPP_SLOT_MAX_ASK`(3) |

## ✦ 目录结构

```
cs-bot-aion/
├── csapp/                 # 后端包(零依赖 HTTP server)
│   ├── server.py          #   HTTP 服务(/、/api/v1/*、/health、warmup)
│   ├── pipeline.py        #   端到端编排(含回复质量 _polish_reply)
│   ├── llm.py             #   LLM 抽象(respond/classify/localize;直接 API+重试+headless)
│   ├── cards.py           #   卡片状态机(含槽位追问上限转人工)
│   ├── kb.py              #   知识检索(向量+结构化)+ VectorRetriever + warmup
│   ├── intent.py          #   意图(normalize/emergency_hit/关键词)
│   ├── market.py          #   市场路由(开放/粘性/语言推断/消息提取)
│   ├── langdetect.py      #   语言检测(脚本+关键词+LLM 兜底)
│   ├── compliance.py      #   合规+内容安全+回复质量(strip_ai_phrases/truncate)
│   ├── db.py              #   自建库(SQLite:leads/escalations/rescue_tickets)
│   ├── state.py           #   会话状态机(匿名 id+断点续聊)
│   ├── debug.py           #   debug 日志(默认开)
│   └── static/index.html  #   原生单页聊天窗(零构建)
├── kb/{THA,AU}/           # 知识库(规格/文档/经销商/FAQ + 4 语言 + 向量索引 .npz)
├── tools/                 # ETL/评测/向量索引/FAQ 烘焙/泰文 CMap 还原
│   ├── embed_index.py     #   离线构建向量索引
│   ├── build_faq_localized.py  #   FAQ 4 语言烘焙
│   ├── eval_intent_ml.py  #   多语言意图评测
│   └── thai_restore.py    #   泰文 CMap 声调还原
├── 智能客服系统设计文档-v0.4.md   # 产品文档(现行 as-built)
├── 售前智能客服通用框架.md          # 行业无关的护栏框架(参考)
└── data/                  # 运行态数据(会话/业务库/debug,不入库)
```

## ✦ 当前指标

| 指标 | 值 |
|---|---|
| 多语言意图准确率 | **100%**(57 条评测集:中/英/泰/西 × 6 意图) |
| 单轮延迟 | ~1-2s(直接 API + 共享 e5 模型) |
| 泰文向量检索 recall@5 | e5-large 0.186 vs MiniLM 0.129(+44%) |
| 语言 | 中/英/泰/西 原生;其它 LLM 兜底 |
| 市场 | 开放(任意码);语言推断/手工粘性/消息提取 |

## ✦ 文档

- **产品文档**：[`智能客服系统设计文档-v0.4.md`](智能客服系统设计文档-v0.4.md)（现行 as-built；v0.3 为设计稿）
- **护栏框架**：[`售前智能客服通用框架.md`](售前智能客服通用框架.md)（行业无关版，作为安全/合规/转化护栏参考）
- 阶段报告：`Phase0_REPORT.md`、`PHASE1_REPORT.md`、`PHASE1_LLM_W23_REPORT.md`、`THAI_RESTORE_REPORT.md`、`REFACTOR_VECTOR_REPORT.md`、`INTENT_CALIBRATION_REPORT.md`

## ✦ 备注

- 知识内容来自泰国/澳洲 AION 官方手册、配置表、经销商数据；索引由 `tools/embed_index.py` 构建并随仓库内置（clone 即可用向量检索，无需重建）。
- 业务落库于 `data/kd.db`(SQLite)；会话状态存 `data/sessions/`（运行时生成，不入库）。
- 生产可换用 FastAPI 版（`csapp/api.py`，需 `pip install fastapi uvicorn`）。
