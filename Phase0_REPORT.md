# Phase 0 · 知识地基 执行报告

> 日期:2026-09-01 · 状态:**已跑通,元数据完整,可检索** · 关联设计:v0.3 §5/§9 Phase 0

---

## 1. 构建成果

```
kb/                           # 知识库(market 隔离)
├─ raw/manifest/
│  ├─ sources.json            # 源素材清单(market/doc_type/version/effective_date)
│  ├─ pdf_extract_summary.json# PDF 提取统计
│  └─ kb_manifest.json        # 各市场汇总(文档/规格/经销商/FAQ)
├─ AU/
│  ├─ docs/                   # 文档段(owner/rescue/quickstart) 3 文件
│  ├─ spec/                   # 结构化配置表
│  ├─ dealers/                # 结构化经销商
│  └─ faq/                    # FAQ
└─ THA/                       # 同上,市场隔离

tools/
├─ _lib/                      # openpyxl(读取 xlsx)
├─ extract_pdf.py             # ETL A:PDF -> 逐页 segment
├─ etl_config.py              # ETL B:配置表 xlsx -> 结构化
├─ etl_dealers.py             # ETL C:经销商 xlsx -> 结构化(含修复)
├─ build_faq.py               # FAQ 生成(答案从真实规格取值)
└─ search.py                  # 检索/验证 harness(配置/文档/经销商/FAQ)
```

### 产出统计

| 维度 | AU | THA |
|---|---|---|
| 文档 PDF | 3(6 → 全部文本型) | 3 |
| 文档 segment | 370 | 256 |
| 文档字符 | 414,249 | 328,687 |
| 配置行数 | 248(9 类/2 变体) | 256(9 类/2 变体) |
| 经销商 | 38(31 有坐标) | 70(63 有坐标) |
| FAQ | 20 | 18 |

---

## 2. DoD(退出标准)核对

| Phase 0 条目 | 状态 | 说明 |
|---|---|---|
| PDF 前置检查:是否文本型 | ✅ **已确认全部文本型** | 早期疑为扫描件,深采样证实有完整文字层;无需 OCR |
| 10 个 PDF 清洗入库 | ✅(实为 6 个 PDF) | 已切分(626 segment),带 page/chapter/version/effective_date |
| 配置表结构化 | ✅ | 车型×配置项,9 类,跨语言别名可查 |
| 经销商表入库(含经纬度) | ✅ | 含 Haversine 距离排序 |
| 高频 FAQ(首批 30-50) | ✅ 38 条 | 答案从真实规格取值,可溯源 |
| 多语言验证:语言检测 + 检索策略 | 🔶 部分 | 泰文检索策略(原语/翻译)需嵌入模型对比,已在 search.py 用归一化关键词证明「原语检索」可达;翻译策略留 Phase 0 尾 |

**结论:知识已可检索、元数据完整、泰文 CMap 缺陷已定位并有归一化方案。Phase 0 主体完成。**

---

## 3. 关键发现(重要)

### F1. 泰文车主手册 CMap 声调退化(重点)
- 泰文手册(227p)内嵌字体把**声调号(่ ้ ๊ ็ 等)映射为错位字符**:PyMuPDF→`-`(U+002D),PyPDF2→`�`(U+FFFD)。
- 影响:218/221 段受影响,5414 处。如 `คู่มือ`→`คู-มือ`。**词干/元音骨架保留,语义仍在**。
- 救援指南(33p)、快速指南基本干净(仅 2/1 处),**关键安全文档不受影响**。
- **方案(已实现)**:检索层 `thai_norm()`(NFKD 去组合记号 + 处理 `-` 退化)使 `คู่มือ` 与 `คู-มือ` 都归一化为 `คมือ`,匹配成功。
- **不急于 OCR**:泰文 OCR 自身有精度风险且昂贵;向量检索对退化文本鲁棒。真正需要时再对 227p 手册做 OCR 增强。

### F2. 澳洲经销商数据质量(源数据问题)
- 原始 410 行,仅 **38 家有效**,其余全为空行 → ETL 已剔除。
- **经纬度文本列部分错误**(如 `Longitude: 51.19` 应为 `151.19`;`-151.00` 符号错误)→ 已改为**优先从 Google Maps 链接解析坐标**,仅 38 家中 31 家有坐标,剩余 7 家需人工补。
- 店铺类型已是英文(Standard/showroom);州/市英文。电话已归一化。

### F3. 跨语言规格查询(已解决)
- 配置表字段按市场语言存储(泰/英),用户任意语言提问需映射到字段。
- 已建 `FIELD_ALIAS` 概念→多语言关键词映射 + 命中数打分,实现语言无关检索。
  - `range`:AU→430 (WLTP),THA→NEDC 500/420(**两市场续航标准不同,NEDC vs WLTP**)
  - `battery`:AU→60 kWh,THA→60/50.27 kWh

### F4. 其它
- 配置表 model 字段有一处 model 提取 bug(泰表 doc_name 无 "AION UT" 导致回退为 "泰国")→ 已修正为默认 AION UT。
- 泰表经销商电话 `02-027-8875` 为 9 位,疑数据问题,待查。

---

## 4. 检索验证示例(来自 search.py)

```
AU:
[spec] range   -> Range Under Comprehensive Working Conditions(km) = {Premium:430 (WLTP), Luxury:430 (WLTP)}
[spec] battery -> Battery Capacity(kWh) = {Premium:60, Luxury:60}
[dealer] from Sydney -> GAC Alexandria 5.0km / GAC Burwood 9.9km / GAC Bankstown 20.2km
[faq] warranty -> What is the warranty for the AION UT? (after-sales)

THA:
[spec] range   -> ระยะทางที่สามารถขับขี่ได้ NEDC (กิโลเมตร) = {500 Premium:500, 420 Standard:420}
[docs] คู่มือ เจ้าของ -> owner manual 相关页(คำนำ / AION APP / เจ้าของรถ)
[dealer] from Bangkok -> GAC สีลม ซอย 9 4.5km / GAC รัชดา-ท่าพระ 5.6km / GAC ตลิ่งชัน-ราชพฤกษ์ 6.0km
[faq] รับประกัน -> การรับประกันของ AION UT? (after-sales)
```

---

## 5. 已知限制 / 下一步

- **文档检索为轻量关键词打分**(非 BM25/向量):`how to charge` 首次命中偏泛,精度不足。这是检索质量层面,建议 Phase 0 尾/Phase 1 引入 embeddings(泰文原语 vs 翻译兜底对比)替代。
- **泰文 7 家澳经销商缺坐标**、泰表 9 位电话待核。
- **FAQ 答案为模板+自动取值**,质保/服务类指到手册,需运营按官方政策补全精确条款。
- **未做语言检测**与**泰语检索策略对比实验**(需 embedding 模型,属验证项)。
