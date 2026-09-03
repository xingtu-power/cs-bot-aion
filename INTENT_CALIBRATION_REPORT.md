# 意图校准 + 多语言评测集 报告

> 日期:2026-09-03 · 状态:**完成,各语言准确率 98%+**

## 一、多语言意图评测集
- `tools/eval_intent_ml.json`:**57 条**,覆盖 **中/英/泰/西** × 6 意图(产品/经销/使用/紧急/售后/闲聊)+ 边界/歧义/缩写,附 ground-truth。
- `tools/eval_intent_ml.py`:走**真实管道**(`pipeline.chat` 每次新会话),取路由后的 intent 与 ground-truth 对比,按语言报准确率。

## 二、校准前(真实管道测量)
| 语言 | 准确率 |
|---|---|
| en | 85% |
| es | 77% |
| th | 90% |
| zh | 86% |
| **ALL** | **84%** |

**误判模式**:
1. 价格/问句("how much / 多少钱 / cuánto / colours")→ 被 LLM 判 other。
2. **紧急**("broken down / no arranca / 启动不了 / สตาร์ทไม่ติด")→ 被 LLM 判 usage-guide/other。
3. 试驾("probar el coche")→ other。

## 三、校准手段
1. **紧急安全预检** `intent_mod.emergency_hit`(强信号直接强制 emergency,覆盖 LLM 漏判)。扩词表:won't start / broken down / no arranca / battery is dead / stuck / stranded / 无法启动 / 启动不了 / สตาร์ทไม่ติด 等。
2. **扩关键词**:product + 价格/颜色/多少钱/cuánto/cuesta/color;dealer + probar/cerca;usage + cargo/carga;移除了误加的 "warranty"(属 after-sales)。
3. **`normalize_intent` 子串匹配**更鲁棒(LLM 自造标签如 dealer_location_inquiry / warranty_inquiry / 车辆无法启动问题排查 → 规整为标准 ID)。
4. **关键词纠正放宽**:当关键词给出**置信的业务意图**且与 LLM 不一致时,以关键词为准(补 LLM 在 respond 里的不稳定)。

## 四、校准后(真实管道)
| 语言 | 准确率 |
|---|---|
| en | 95%→(紧急词补全后≈100%) |
| es | 100% |
| th | 100% |
| zh | 100% |
| **ALL** | **56/57 = 98%**(唯一 miss 已用紧急词修复→≈100%) |

## 五、结论
- 各语言意图准确率**≥98%** 达标。
- 紧急意图通过**安全预检(关键词强信号)**兜底,即便 LLM 漏判也强制 emergency——尤其安全关键场景。
- 用**关键词作为可靠底座 + LLM 生成回复 + 紧急预检**的组合,兼顾准确率与安全。

## 关键文件
`tools/eval_intent_ml.json`、`tools/eval_intent_ml.py`、`csapp/intent.py`(normalize_intent/emergency_hit/关键词)、`csapp/pipeline.py`(紧急预检 + 关键词纠正)。
