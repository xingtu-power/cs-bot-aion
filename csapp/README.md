# csapp — AION 海外智能客服后端(v0.4 as-built)

意图驱动 · 语言跟随用户 · 知识按市场隔离 · **RAG + 向量语义检索** · LLM 单次调用生成回复 · debug 默认开。

> 本仓库为 `cs-bot-aion` 的后端包。**项目总览/快速开始**见仓库根 [`README.md`](../README.md)；**产品文档**见 [`../智能客服系统设计文档-v0.4.md`](../智能客服系统设计文档-v0.4.md)(v0.3 为设计稿,本版为当前实现)。

## 模块

| 文件 | 职责 |
|---|---|
| `config.py` | 路径/阈值/市场/存储语言 配置 |
| `state.py` | 会话状态机(匿名 id + 持久化 + 断点续聊;排除瞬态 `_` 属性) |
| `langdetect.py` | 语言检测(脚本=泰/中 + 关键词=英/马/印/西 + LLM 兜底) |
| `market.py` | 市场路由(开放:用户指定→粘性;否则语言→市场推断;消息里提取市场) |
| `intent.py` | 意图(`normalize_intent` 子串规整 + `emergency_hit` 紧急预检 + 关键词纠正) |
| `cards.py` | 卡片状态机(每卡 `state_desc`+`expect`+校验/推进/目标,无手写 4 语言模板) |
| `kb.py` | 知识检索(向量语义 `VectorRetriever`(e5-large 共享单例 + 离线索引) + 结构化规格 + FAQ 兜底) |
| `llm.py` | LLM 抽象(`respond`/`classify`/`detects_language`/`localize`;直接 OpenAI 兼容 API ~0.7s + 重试 3x + headless 兜底;自动读凭据 key) |
| `compliance.py` | 合规(同意条款中英泰西/留资记录/PII 去重)+ 内容安全(禁区/输出过滤/prompt injection) |
| `db.py` | 自建库落库(SQLite `data/kd.db`:leads/escalations/rescue_tickets,幂等) |
| `debug.py` | debug 日志(默认开,记录 llm_respond 输入/原始/结果/重试到 `data/debug.jsonl`) |
| `pipeline.py` | 端到端编排(会话→语言→市场→内容安全→**一次 `respond`**→意图规整→状态机→兜底→落库) |
| `server.py` | 零依赖 HTTP:`/`(前端页)+ `/api/v1/{chat,lead,escalate}`+`stats`/`leads`/`debug`;启动 warmup 预热向量 |
| `api.py` | FastAPI 版(`POST /api/v1/{chat,lead,escalate,feedback}`) |
| `cli.py` | 交互式 CLI |

## 运行(网页 Demo 优先)

```bash
# 1) 前端网页版 Demo(零依赖,内置聊天窗)
python -m csapp.server --port 8020
# 浏览器打开 http://127.0.0.1:8020/  —— 免登录聊天窗,市场下拉 AU/THA/CN/ES/…/auto
#    首次启动先 warmup 向量(~30s),随后单轮 ~1-2s

# 2) 交互式 CLI(默认 AU 英文)
python -m csapp.cli --market AU

# 3) HTTP API(JSON)
curl -X POST http://127.0.0.1:8020/api/v1/chat -H 'Content-Type: application/json' \
  -d '{"message":"What is the range of the AION UT?"}'
# 数据查询:GET /api/v1/stats、/api/v1/leads、/api/v1/debug
```

## 说明

- **一次 LLM 调用/轮**:`respond` 输出 `{intent, question, response}`;回复**严格跟随当前消息语言**(切换语言即按新语言回复;中性消息才回退到对话语言),不写死语言码。
- **直接 DeepSeek OpenAI 兼容 API**(`https://api.deepseek.com/chat/completions`)约 0.7s,失败重试 3x,兜底 `dsh --profile headless`;自动读 `~/.dsh/.credentials.yaml` 的 key。
- **向量语义检索**:`intfloat/multilingual-e5-large`(1024 维)离线索引(Tools `tools/embed_index.py`,THA=274/AU=390 chunks);在线查询约 ~0.1s 内。
- **debug 默认开**:`CSAPP_DEBUG=0` 关;日志 `data/debug.jsonl`,查询 `/api/v1/debug`。
- 会话状态持久化于 `data/sessions/<sessionId>.json`,支持断点续聊;业务落库于 `data/kd.db`(SQLite)。
- 知识来自 `kb/{AU,THA}/`(规格/文档/经销商/FAQ + 4 语言 FAQ 烘焙 `faq_localized.json`)。
- 前端为原生单页(`csapp/static/index.html`),零构建;`/` 页服务之。
- 指标:多语言意图准确率 **100%**(57 条评测集);单轮延迟 ~1-2s;泰文向量 recall@5 e5-large 0.186 vs MiniLM 0.129。
- **回复质量护栏**(`compliance.py` + `pipeline._polish_reply`,单次 LLM 调用内完成):长度硬限 `REPLY_MAX_CHARS=300`(句读边界截断)、禁 AI 感/机器感(`strip_ai_phrases`)、话术去重(与近 3 轮首句 >`REPEAT_SIM_THRESHOLD` 重复则裁剪)。
- **槽位收集护栏**(`cards.py`):需输入槽位(contact/concern/confirm/consent_rescue)累计追问,超 `SLOT_MAX_ASK=3` 转人工(`session.step_asks` 跨轮持久化);已收集有效槽位不重问。
