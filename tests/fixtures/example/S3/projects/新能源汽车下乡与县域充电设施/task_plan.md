---
kind: task_plan
version: 2
updated_at: '2026-09-30T10:40:00-04:00'
genre: argument
report_name: 内参
scope_brief: H 省县级，含乡镇，不含村 · 2021–2025 年
pipeline_status: gate2_awaiting
approval:
  status: approved
  approved_at: '2026-09-30T10:43:00-04:00'
  approved_by: 研究员
  approval_quote: 时间段改成 2021 开始，其余可以。
  approved_hash: 2d593129db98
  signature: v1.ed25519.5016c05c42474424.ZkaZeJ-51vFILdgQi7p5pEy3YGvi-AEJqnI-dcU3pm2ZYzQEV2gUBFJbF-HgAmKmt4QpoBdKrZ6dfTs-LBnxAQ
budget:
  cards_max: 30
  steps_max: null
  words_max: 8000
evidence_standard:
  inherit: true
  primary_tiers: null
  scope_note: null
classification_frame: null
constraints:
  words_max: 8000
  formats:
  - docx
  deadline: null
last_turn_at: '2026-10-02T16:05:00-04:00'
revision_log:
- v: 2
  at: '2026-09-30T10:40:00-04:00'
  who: agent
  what: 时间段改为 2021–2025 年
  why: 你说时间段改成 2021 开始
---
# 任务计划

## 原始请求 {#request}

> 想弄清楚这几年的新能源汽车下乡活动，到底有没有带动县里的公共充电桩建设。要一份给厅里的内参，八千字左右，最后要有政策建议。

## 文体判定 {#genre_rationale}

报告类型卡上你选了「下判断」：这份内参要回答下乡活动有没有带动县里的公共充电桩建设，所以按研判型写，找资料之前先定判定标准。

## 可证伪命题 {#thesis}

2021–2025 年，H 省新能源汽车下乡活动带动了县域公共充电设施增长。

## 假设 {#hypotheses}

- 强一点的说法：下乡活动是县域公共充电设施增长的主要推动力。
- 弱一点的说法：下乡活动在部分县起了促进作用。

## 预注册结论空间 {#preregistration}

| outcome | 判定条件 | 对应表述 |
|---|---|---|
| A | 参加活动的县，活动后两年公共充电设施增速明显高于没参加的县，且新增的主要是公共桩 | 成立 |
| B | 增速更高，但主要来自其他政策（如农村电网改造、公路服务区建设） | 部分成立 |
| C | 参加与没参加的县增速没有明显差别 | 不成立 |

## 证据对接窗口条件 {#evidence_gate}

如果拿不到分县的充电设施数据，停下来和你商量，改成综述型。

## 概念定义表 {#definitions}

| 概念 | 操作定义 | 不算作证据的情形 |
|---|---|---|
| 县域公共充电设施 | 县级行政区内对社会开放的充电桩，含乡镇，不含村 | 私人桩、单位内部桩 |
| 参加县 | 列入下乡活动三批名单的县 | 只参加过一次展销的县 |

## 项目口径 {#scope}

- 地域：H 省，县级行政区（含乡镇，不含村）。
- 时间：2021–2025 年。
- 对象：公共充电桩（不含私人桩）。

## 证据标准 {#evidence_standard}

继承纪律库：一手优先，A / A− / B 三级。

## 约束 {#constraints}

约 8000 字，Word 版，最后要有政策建议。

## 预算与止损 {#budget}

资料卡片不超过 30 张；超了先停下来问你。

## 要你认的三件事 {#commitments}

1. **改动内容**：按你的意见，时间段从 2022–2025 改成 2021–2025；其余没有改动。
2. **最薄弱的依据**：分县的充电桩数据可能只有部分地市公布过。
3. **最可能出错的地方**：把私人桩和公共桩的数字混在一起。我会在每张资料卡片上标清口径，你可以抽查。
