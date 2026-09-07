# 知识缺失兜底策略（Knowledge-Gap Fallback）

> 全局方案，覆盖所有意图、所有语言、所有渠道。
> 分支：`feat/knowledge-gap-fallback`（基于 `b33140b`，待方案确认后开始实施）

---

## 0. 背景

**触发问题**：用户问"泰国市场卖哪些车型"，LLM 回复"我们暂时没有该市场的确切清单。建议您直接联系泰国当地授权经销商确认"。把球踢回用户，**没有任何留资引导**，线索直接丢失。

**截图问题根因（逐层溯源）**：

1. 用户问"泰国市场卖哪些车型" → 意图识别为 `product-inquiry`（正确）
2. `kb/THA/` 下没有 `models.json`，`kb.context()` 检索不到泰国具体在售车型
3. `_DEFAULT_CARD["product-inquiry"][0].desc`（卡片策略）生效，prompt 注入：
   > ACCURACY: ... If a detail is not listed, **say you do not have it**.
4. 卡片策略位于 `prompt` 最前部 `Situation:` 字段，权重最高
5. LLM 遵循卡片策略输出"say you do not have it"风格的话
6. 实际回复被"ACCURACY"约束主导，**没有走全局 prompt 里的 "offer to connect them with an AION specialist" 兜底**

**根本问题**：**卡片策略（消极：say you do not have it）和全局 prompt（积极：offer to connect）互相矛盾**，LLM 选了更具体的卡片策略。

**用户要求**：全局方案，不只改一处、不只改一种语言。

---

## 1. 现状盘点：所有"知识缺失"接缝点

按代码位置列出每一条可能产生"我不知道/暂无/建议联系经销商"回复的代码路径：

| # | 位置 | 触发条件 | 当前回复 | 兜底？ | 多语言？ |
|---|---|---|---|---|---|
| 1 | `pipeline._fallback_text` | LLM 失败/返回空 | "麻烦再说一下，您可以问配置、经销商或使用方法" | ❌ 无留资引导 | ✅ 4 语言 |
| 2 | `pipeline._no_knowledge_lead` | `compliance.find_competitor` 命中 | "我可以帮您转接 AION 专员，或者您方便留个联系方式吗？" | ✅ 留资引导 | ✅ 4 语言 |
| 3 | `pipeline._polish_reply` 后 | LLM 回复整体 | 去除重/去 AI 感/截断 | ❌ 不补兜底 | ✅ |
| 4 | `pipeline` 知识库检索空 | `kb.context()` 返回 `vec=[]` 时 | 走 faq 兜底，**无信号传回 pipeline** | ❌ LLM 看不到"完全无答案"信号 | n/a |
| 5 | `pipeline` step_desc 走 `_DEFAULT_CARD` | LLM 实际生成回复 | 取决于卡片策略 | ❌（见 #6） | n/a |
| 6 | `cards._DEFAULT_CARD["product-inquiry"][0].desc` | 售前产品咨询 | "If a detail is not listed, **say you do not have it**" | ❌ **明确禁止引导留资** | n/a |
| 7 | `cards._DEFAULT_CARD["dealer-lookup"][0].desc` | 经销商查询 | "Share the nearest dealers ... build interest ... offer to book" | ✅ 引导 | n/a |
| 8 | `cards._DEFAULT_CARD["usage-guide"][0].desc` | 使用方法 | "If the facts do not clearly cover that exact component ... say you do not have that specific info" | ❌ 不引导（售后的定位） | n/a |
| 9 | `cards._DEFAULT_CARD["after-sales"][0].desc` | 售后服务 | "Only if a service visit is needed, offer to book one" | ⚠️ 弱引导（条件严格） | n/a |
| 10 | `cards._DEFAULT_CARD["other"][0].desc` | 闲聊 | "Greet ... guide the customer into a topic" | ❌ 不引导 | n/a |
| 11 | `cards._HANDOFF` / `_CONFIRM_TEXT` | 转人工确认 | 4 语言温暖话术 | ✅ 转人工 | ✅ 4 语言 |
| 12 | `compliance.guard_model_facts` + `_FAB_REPL` | 非 UT 车型出现功率/扭矩/电池容量 | "（该具体参数目前暂无确切信息，建议联系授权经销商或官方热线核实）" | ⚠️ 留资但**只在中文** | ❌ 仅中文 |
| 13 | `compliance.is_vague_reply` + 二次 LLM | "麻烦再说一下"等空泛反问 | 二次调用 LLM 强制作答 | ⚠️ 没用，可能还是空 | n/a |
| 14 | `llm.respond` 全局 prompt | 通用兜底指令 | "If the facts do not cover the customer's request ... politely say you don't have that information, and offer to connect them with an AION specialist or ask them to leave contact details for a follow-up" | ✅ 留资（**但被 step_desc 覆盖**） | ✅ |
| 15 | `llm.respond` `MODEL ACCURACY` | 非 UT 车型参数缺失 | "If a requested spec is not in the facts, state that you do not have that information rather than guessing" | ❌ 不引导 | n/a |
| 16 | `intent.py` | 知识缺失检测 | **无任何机制** | ❌ | n/a |
| 17 | `cards._nat` (LLM 生成自然话术) | 确认转人工 | 调用 LLM 生成 | 取决于 LLM | n/a |

**结论**：兜底机制碎片化、互相矛盾、缺多语言、缺后处理保险。需要**统一重构**。

---

## 2. 全局方案：前-中-后三层 + 配套改动

### 2.1 第 1 层：LLM 行为规范（前置，治本）

**目标**：消除 prompt 内部矛盾，让 LLM 在所有意图下都有"知识缺失时该做什么"的明确指令。

| 改动 | 位置 | 内容 |
|---|---|---|
| 1.1 | `cards._DEFAULT_CARD` 全部 6 张卡片 | 把每张卡片的 `desc` 中"say you do not have it / say you do not have that specific info"等消极兜底，改为**显式的"缺失时做 X"指令**。X 按意图分类：<br>• **product-inquiry / dealer-lookup / after-sales** → "share what you do know → acknowledge the gap → offer to connect with local dealer and ask for contact"<br>• **usage-guide** → "explain the gap → offer further troubleshooting OR AION hotline (no sales push)"<br>• **emergency** → 已有 rescue 兜底（保持）<br>• **other** → 维持闲聊引导（保持） |
| 1.2 | `csapp/talk_scripts.json` 全部 6 张卡片 | 同步 JSON 文件内容（JSON 优先级高于内置默认） |
| 1.3 | `llm.respond` 全局 prompt | 把"MODEL ACCURACY"那段里的"state that you do not have that information rather than guessing"**升级为**"state that you do not have it, AND follow the state-specific fallback in Situation"（让 LLM 明确把兜底权交给 state_desc） |
| 1.4 | `llm.respond` 全局 prompt 开头加新规则 | `KNOWLEDGE-GAP POLICY (highest priority after Situation): For every intent, the Situation block above defines the required fallback when the FACTS do not cover what the customer asked. ALWAYS follow the Situation's fallback, never just say "I do not have it" without taking the next step the Situation prescribes.` |

**第 1 层效果**：LLM 行为规范统一，prompt 内部一致，不再有"卡片说没说 vs 全局说留资"的矛盾。

---

### 2.2 第 2 层：后处理兜底（后置保险）

**目标**：无论 LLM 怎么说，命中"知识缺失"信号时强制走统一兜底。

| 改动 | 位置 | 内容 |
|---|---|---|
| 2.1 | `intent.py` 新增 | `is_knowledge_gap(text, lang)` 4 语言正则检测 LLM 回复里的"知识缺失"信号：<br>• 中：暂时没有 / 暂无 / 不清楚 / 无法确认 / 需确认 / 建议联系经销商<br>• 英：don't have / no information / cannot confirm / please contact / refer to<br>• 泰/西：等价表达 |
| 2.2 | `pipeline.py` 新增 | `_knowledge_gap_lead(lang, intent, market)` 多语言模板，按意图返回不同引导话术：<br>• **售前类**（product-inquiry / dealer-lookup / after-sales）→ 引导留资（手机/邮箱），强调"专员按当地市场跟进"<br>• **售后类**（usage-guide）→ 引导重述/转人工/hotline，不留资<br>• **其它**（other）→ 不变 |
| 2.3 | `pipeline.py` 主流程 | 在 `_polish_reply` 之后、`session.persist()` 之前加：<br>```python<br>if intent_mod.is_knowledge_gap(reply["reply"], reply_lang):<br>    gap_lead = _knowledge_gap_lead(reply_lang, session.intent, market)<br>    if gap_lead and not _already_pitching_lead(reply["reply"]):  # 避免重复<br>        reply["reply"] = reply["reply"] + "\n\n" + gap_lead<br>```<br>**关键**：先检测 LLM 回复**没**说"留手机/邮箱"才追加（避免 LLM 已经引导了还重复追加） |
| 2.4 | `pipeline._fallback_text` | 同步：fallback 时也走 `_knowledge_gap_lead`，不再只是"麻烦再说一下" |

**第 2 层效果**：硬保险。即使 LLM 漏了，pipeline 也接管。

---

### 2.3 第 3 层：知识库侧结构化数据（中长期，治根）

**目标**：把"事实缺失"从"软缺失"变成"硬数据"，让 LLM 有得说。

| 改动 | 位置 | 内容 |
|---|---|---|
| 3.1 | `kb/THA/models.json`（新建） | 泰国在售车型清单：AION UT 420/500、Y Plus、昊铂 GT 等，含价格区间、上市状态、本地化亮点 |
| 3.2 | `kb/AU/models.json`（新建） | 澳洲在售车型清单 |
| 3.3 | `kb.py` `KnowledgeBase.context()` | 末尾追加：<br>```python<br>mpath = os.path.join(config.KB_ROOT, self.market, "models.json")<br>if os.path.exists(mpath):<br>    parts.append("[本市场在售车型清单]\n" + open(mpath, encoding="utf-8").read())<br>``` |
| 3.4 | `cards._DEFAULT_CARD["product-inquiry"][0].desc` | ACCURACY 规则升级：<br>`Quote ONLY values that literally appear in the facts OR the [本市场在售车型清单] block. The market-specific list is authoritative for that market.` |

**第 3 层效果**：从根上消除"该市场在售车型"这类知识缺失。

---

### 2.4 配套改动

| 改动 | 位置 | 内容 |
|---|---|---|
| 4.1 | `compliance.guard_model_facts` 的 `_FAB_REPL` | 多语言化（中英泰西），不只中文 |
| 4.2 | `csapp/static/index.html` 消息气泡 | 当 `reply` 里包含引导留资的强信号时，气泡下方显示"📞 留资"快捷按钮（可选） |
| 4.3 | `pipeline.END_NOTICE` | 不动 |
| 4.4 | `cards._HANDOFF` / `_CONFIRM_TEXT` | 不动（已是 4 语言） |

---

## 3. 三层依赖关系与推进顺序

```
[1.1~1.4 卡片 + 全局 prompt 改造]   ← 改动最小、影响最大
              ↓
[2.1~2.4 后处理兜底]               ← 保险丝，依赖 1.x 的指令
              ↓
[3.1~3.4 结构化数据]               ← 长期，最准确
```

**推进顺序**：

1. **先 1.x + 2.x 一并改**（一次提交，覆盖 6 个意图 + 4 语言后处理兜底）
2. **用 deepseek 模式跑 5~10 条真实问题**（泰国/澳洲在售车型、配置、经销商、使用方法、紧急救援等），确认 LLM 行为符合预期
3. **2.x 后处理兜底**在 rule 模式也能验证（mock LLM 回复，命中后处理）
4. **3.x 数据**作为单独 PR，运营/产品补完数据后再上

---

## 4. 影响范围（**全 6 个意图**）

| 意图 | 知识缺失时当前行为 | 改造后行为 |
|---|---|---|
| product-inquiry | "暂无该信息" | 先给已知信息 + 引导留资 |
| dealer-lookup | 已有引导（保持） | 已有引导（保持） |
| usage-guide | "暂无该信息" | 解释 + 转人工/hotline（不留资） |
| emergency | 已有 rescue 引导（保持） | 已有 rescue 引导（保持） |
| after-sales | 弱引导 | 增强引导（明确"先回答后留资"） |
| other | 闲聊引导（保持） | 闲聊引导（保持） |

---

## 5. 实施清单（待方案确认后开始）

### Phase 1：前置 prompt 改造（必做）

- [ ] **1.1a** 改 `cards._DEFAULT_CARD["product-inquiry"]` 三步 desc
- [ ] **1.1b** 改 `cards._DEFAULT_CARD["dealer-lookup"]`（微调，标准化）
- [ ] **1.1c** 改 `cards._DEFAULT_CARD["after-sales"]`（增强引导）
- [ ] **1.1d** 改 `cards._DEFAULT_CARD["usage-guide"]`（明确 hotline/转人工）
- [ ] **1.1e** 保持 `cards._DEFAULT_CARD["emergency"]` 不动
- [ ] **1.1f** 保持 `cards._DEFAULT_CARD["other"]` 不动
- [ ] **1.2** 同步 `csapp/talk_scripts.json` 全部 6 张卡片
- [ ] **1.3** 升级 `llm.respond` MODEL ACCURACY 段
- [ ] **1.4** 在 `llm.respond` 开头加 KNOWLEDGE-GAP POLICY 规则

### Phase 2：后处理保险（必做）

- [ ] **2.1** `intent.py` 加 `is_knowledge_gap(text, lang)` 4 语言正则
- [ ] **2.2** `pipeline.py` 加 `_knowledge_gap_lead(lang, intent, market)` 多语言模板
- [ ] **2.3** `pipeline.py` 主流程加 hit detection + 追加逻辑
- [ ] **2.4** `pipeline._fallback_text` 同步走 `_knowledge_gap_lead`

### Phase 3：数据补全（中长期）

- [ ] **3.1** 建 `kb/THA/models.json`
- [ ] **3.2** 建 `kb/AU/models.json`
- [ ] **3.3** `kb.py` `KnowledgeBase.context()` 注入市场车型清单
- [ ] **3.4** product-inquiry desc ACCURACY 升级

### Phase 4：配套

- [ ] **4.1** `compliance._FAB_REPL` 多语言化
- [ ] **4.2** 前端"📞 留资"快捷按钮（可选，待确认）

---

## 6. 验收方式

### 6.1 Rule 模式（本地可测）

1. Mock LLM 回复："我们暂时没有该市场的确切清单。建议您联系当地授权经销商。"
2. 调用 `pipeline.chat()`，验证返回 reply 末尾追加了 `_knowledge_gap_lead("zh", "product-inquiry", "THA")`
3. 验证重复检测：mock LLM 回复"目前 X 车型在泰国上市，建议联系 XX 专员电话或邮箱"，**不**追加

### 6.2 Deepseek 模式（真实场景）

跑以下 5~10 个真实问题，对照预期回复：

| # | 用户输入 | 意图 | 预期回复 |
|---|---|---|---|
| 1 | "泰国市场卖哪些车型？" | product-inquiry | 给全球阵容 + 坦诚 + 引导留手机/邮箱 |
| 2 | "Y Plus 在泰国卖多少钱？" | product-inquiry | 全球价 + 引导留资 |
| 3 | "AION V 的经销商在哪？" | dealer-lookup | 已有引导（保持） |
| 4 | "我的车启动不了" | emergency | 救援引导（保持） |
| 5 | "空调怎么除雾？" | usage-guide | 解释 + 转人工/hotline |
| 6 | "保养怎么做？" | after-sales | 回答 + 引导预约 |
| 7 | "蓝牙怎么连？" | usage-guide | 解释步骤 |
| 8 | "墨西哥卖哪些车？" | product-inquiry | 全球阵容 + 引导留资 |
| 9 | "我车开不了，需要救援"（英文） | emergency | 救援引导（保持） |
| 10 | "我的车在哪儿买配件？"（泰文） | after-sales | 回答 + 引导预约 |

### 6.3 多语言验证

每条问题用 zh / en / th / es 各跑一遍，确认：
- 回复语言跟随用户
- 留资引导话术对应语言
- 不出现中文漏到英文用户的情况

---

## 7. 风险与回滚

| 风险 | 缓解 |
|---|---|
| LLM 漏遵循 prompt 指令 | 第 2 层后处理兜底作为保险 |
| 正则误判"我暂时没有预算" | 限制只在 product-inquiry / dealer-lookup / after-sales 触发；严格关键词；二次校验 contact-pitching 状态 |
| LLM 已经在引导留资 + 第 2 层又追加 | `_already_pitching_lead()` 检测"留手机/邮箱/联系"等词避免重复 |
| 多语言话术翻译不到位 | 复用现有 `_no_knowledge_lead` 模板的 4 语言结构，统一文风 |
| 改动 6 张卡片破坏现有流程 | 每个卡片改动单独 commit，便于 revert |

**回滚策略**：每个 Phase 单独 commit + 分支独立（`feat/knowledge-gap-fallback`），出问题 `git revert <commit>` 即可。

---

## 8. 当前状态

- **分支**：`feat/knowledge-gap-fallback`（基于 `b33140b`）
- **当前 HEAD**：`b33140b`（已 revert `cc363b2`）
- **状态**：待方案确认后开始实施 Phase 1
- **下一动作**：用户确认 1.x + 2.x 一并改 → 在 `feat/knowledge-gap-fallback` 上执行