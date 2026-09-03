# 重构 + 向量语义检索 报告

> 日期:2026-09-02 · 状态:**完成**

## 一、架构重构:每轮一次 LLM 生成回复(去掉手写 4 语言模板)

原先:卡片引擎用**手写的 4 语言模板**生成引导话术,易漏译(如"中文输数字回英文"bug)。
现改为:**每轮一次 LLM 调用**,生成回复文本;卡片只留状态机。

**单次 LLM 调用** `_llm.respond(message, context, state_desc, conv_lang)` → `{intent, question, response}`:
- `context`: 知识上下文(向量检索 + 结构化规格)。
- `state_desc`: 当前卡片步骤的英文情景描述(给 LLM,非客户可见)。
- `conv_lang`: 对话语言提示,配合指令"**按用户语言回复;若无语言则沿用对话语言**"。

**关键**:prompt **不写死语言码**;LLM 从用户消息/对话语境自己判断语言。彻底避免模板漏译。

**卡片引擎(状态机)保留**:步骤推进、收集校验(手机/邮箱/关注点/确认/救援同意)、达成目标、合规留资与救援工单落库。**回复文本全由 LLM 生成**。

**实测**:中/英/西/泰 全部跟随用户语言;产品流程"回答→关注点→手机号→达成";"你是谁"→自我介绍;"无法启动"→紧急。

## 二、向量语义检索

**为什么**:关键词检索跨语言弱(如中文"轮胎"查不到泰文轮胎 FAQ)。向量语义能跨语言/同义命中。

**方案(结构化 + 向量混合)**:
| 数据 | 检索 |
|---|---|
| 文档(车主手册/救援/快速指南) | **向量语义**(e5-large) |
| FAQ | **向量语义** |
| 规格(配置表) | **结构化精确** |
| 经销商(经纬度) | **距离排序**(保留) |

**实现**:
- `tools/embed_index.py`:离线用 `intfloat/multilingual-e5-large`(Thai 好,recall 0.186 vs MiniLM 0.129)建索引,存 `kb/<market>/vectors/`。THA=274 chunks、AU=390 chunks × dim 1024。
- `csapp/kb.py` → `VectorRetriever`:加载索引,在线**单条查询 embed + 余弦 top-k**(快);模型懒加载并缓存。
- `kb.context(query, lang)`: **向量 top-k(文档+FAQ) + 结构化规格** 混合;索引未建时自动回退关键词。

**实测**:泰文"แรงดันลมยาง"→精确命中轮胎压力 FAQ;泰文"เกิดเหตุฉุกเฉิน"→紧急 FAQ。各语言 RAG 回答质量提升。

## 三、延迟说明(诚实)
- 每轮 **1 次 LLM 调用**(意图 + 是否提问 + 回复生成),约 6~8s——准确率优先的代价。
- 向量检索:首次加载模型 ~30s(单市场一次),之后每次查询 ~0.1s。
- 生产建议:LLM + 向量在 GPU 上,在线检索 CPU 亦可;索引离线构建。

## 四、关键文件
`csapp/llm.py`(respond)、`csapp/cards.py`(通用状态机,无模板)、`csapp/pipeline.py`(单次 respond 流)、`csapp/kb.py`(VectorRetriever + context)、`tools/embed_index.py`、`tools/build_faq_localized.py`(FAQ 4 语言烘焙)。

## 下一步
- intent "other" 偶尔(如泰文轮胎)需补关键词/校准。
- 索引版本管理(知识更新需重 embed)。
- 生产切 GPU + e5-large;在线 CPU 亦可行。
