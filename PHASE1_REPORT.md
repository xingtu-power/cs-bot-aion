# Phase 1 · 骨架跑通 执行报告

> 日期:2026-09-01 · 状态:**骨架已端到端跑通(AU/THA)** · 关联设计:v0.3 §6 / §9 Phase 1

## 一、已交付的骨架(`csapp/` 包)

| 模块 | 职责 | 验证 |
|---|---|---|
| `state.py` | 会话状态机(匿名 id + JSON 持久化 + 断点续聊) | ✅ |
| `langdetect.py` | 语言检测(脚本识别:泰/英/中,语言跟随用户) | ✅ |
| `market.py` | 市场路由(双维度,可手动切换) | ✅ |
| `intent.py` | 意图识别(二段式:关键词 + LLM 语义确认,**紧急优先**) | ✅ |
| `emotion.py` | 情绪评分(§2.3 量化:信号词/语气/风险/紧急加权) | ✅ |
| `cards.py` | 引导卡引擎(6 张卡,状态→话术→收集→达成/失败) | ✅ |
| `kb.py` | 知识检索(复用 Phase 0 kb:规格/FAQ/文档/经销商) | ✅ |
| `llm.py` | LLM 抽象(rule/deepseek 可插拔,无 key 用规则兜底) | ✅ |
| `pipeline.py` | 端到端编排 + 响应(与 API 对齐) | ✅ |
| `server.py` | **零依赖** http.server API;`/api/v1/{chat,lead,escalate}` | ✅ |
| `api.py` | FastAPI 版(生产形态,需 pip 装 fastapi/uvicorn) | 代码就绪 |
| `cli.py` | 交互式 CLI | ✅ |

**运行**:
```bash
python -m csapp.cli --market AU                 # 交互 CLI
python -m csapp.server --port 8000              # HTTP API(零依赖)
curl -X POST localhost:8000/api/v1/chat -d '{"message":"What is the range of AION UT?"}'
```

## 二、端到端验证结果(AU 英文)

```
[flow] "What is the range of the AION UT?" -> intent=product-inquiry
       BOT: The AION UT range ... is 430 (WLTP) ...        # 真实规格,非编造
[flow] "range"            -> concern=range 已记录,索要联系方式
[flow] "0412345678"       -> target=true, phone 校验通过(澳 04 开头),lead_id 生成
```

- 延迟:**~152ms/轮**(规则版),远低于 DoD 的 p95 < 3s。
- 紧急:"My car won't start" → intent=emergency(优先插队),输出安全 + 救援热线、`yes` 同意后建救援工单。
- 泰文:"AION UT มีที่นั่งกี่ที่นั่ง" → market=THA / lang=th,FAQ 答"5 座",泰文回复。
- 澄清:歧义消息给出 A/B/C/D 选项(多语言),选 A → product-inquiry 并继续。

## 三、DoD 核对(诚实说明)

| Phase 1 DoD | 状态 |
|---|---|
| 单市场(AU 英文)端到端跑通 | ✅ |
| 意图识别 / 情绪 / 语言检测 / 市场路由 / 引导卡 / 断点续聊 | ✅ 骨架可用 |
| 意图识别准确率 ≥90% | 🔶 **未达标测** —— 当前为关键词基线(规则),需接入 LLM 语义确认 + 黄金评测集测 |
| 问题解决率 ≥70% | 🔶 未测(需 LLM + 评测集) |
| 响应 p95 < 3s | ✅(实测 ~152ms) |

> **说明**:骨架把"端到端链路"跑通了;但 DoD 的**准确率类指标依赖 LLM 语义确认与黄金评测集**,属评估环节(下一步接 LLM + 评测集)。

## 四、架构要点

- **LLM 可插拔**:默认 `rule`(无 key 跑通);设 `CSAPP_LLM_MODE=deepseek` + `DEEPSEEK_API_KEY` 才启用大模型语义确认/生成。
- **知识可溯源**:答案来自 Phase 0 的配置表/FAQ/经销商(带引用),不是编造。
- **紧急优先**:`emergency` 强信号直接接管,优先级高于一切流程。
- **语言跟随用户**:检测并跟随用户语言回复;内部状态用结构化英文存储。

## 五、下一步(Phase 2 高价值意图打磨)

- 接入 **LLM 语义确认 + 黄金评测集**,把意图准确率/问题解决率测到达标。
- **文本修复/OCR**:补泰文 CMap 还原层,让泰文知识对 LLM/embedding 都干净。
- 文档检索从"轻量关键词"升级为 embedding(泰文直查,已验证模型可取)。
- 引用展示、不确定兜底、内容安全护栏(§8)。
