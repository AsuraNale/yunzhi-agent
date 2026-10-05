---
kind: dossier
version: 1
updated_at: '2026-10-02T16:05:00-04:00'
genre: argument
approval: {status: awaiting, approved_at: null, approved_by: null, approval_quote: null, approved_hash: null}
dashboard:
  cards_count: 18
  steps_used: 64
  session: "0192f1c4-6b2f-7d5e-8000-00000000c0de"
  session_picked_by: "DSH_SESSION_ID(harness 注入)"
  captured_at: "2026-10-02T19:58:00.000Z"
  budget_ref: task_plan.budget
claims:
  - {id: C1, strength: medium_strong, evidence: [S02, S08], rule_trace: "独立来源2·本地闭环1·指标不完整→中强", override_reason: null}
  - {id: C2, strength: medium_strong, evidence: [S03, S05, S01], rule_trace: "独立来源3·本地闭环1·指标不完整→中强", override_reason: null}
  - {id: C3, strength: medium, evidence: [S07, S13], rule_trace: "独立来源2·正反并存→中", override_reason: null}
  - {id: C4, strength: weak, evidence: [S09, S06, S04], rule_trace: "缺可观察经营指标·与其他政策分不开→弱", override_reason: null}
  - {id: C5, strength: medium, evidence: [S10, S11, S12], rule_trace: "独立来源3·指标不完整→中", override_reason: null}
rules_candidates: [R1, R2, R3, R4, R5]
gate_verdict: {outcome: B, reason: "参加活动的县增速确实更高，但同期的农村电网改造是装桩的前提，贡献分不开。", counterexample_criteria: "未参加县增速与参加县相当，或新增主要来自服务区与电网项目"}
gaps:
  - {id: G1, what: 2021 年分县充电桩数, state: gap, owner: null}
  - {id: G2, what: 乡镇桩使用率, state: gap, owner: null}
revision_log: []
---

# 资料汇编

## 定义与口径 {#header}

沿用任务计划第 2 版的操作定义：县域公共充电设施指县级行政区内对社会开放的充电桩，含乡镇，不含村；参加县指列入下乡活动三批名单的县。证据等级按 A / A− / B 三级。

## 结论层 {#claims}

- C1 参加县的公共桩增速高于未参加县（证据较充分，S02、S08）。
- C2 新增的主要是公共桩，且有一部分到了乡镇（证据较充分，S03、S05、S01）。
- C3 新建的公共桩用起来了，但有一部分闲置（证据一般，S07、S13）。
- C4 下乡活动是县域装桩的主要推动力（证据不足，S09、S06、S04）。
- C5 农网改造与服务区建设同期推动了县域装桩（证据一般，S10、S11、S12）。

## 边界层 {#boundaries}

- R1 只适用于 H 省平原地区的县；山区县样本太少。
- R2 不能推到全国。
- R3 不能说「翻了一番」：2021 年没有分县数据。
- R4 参加县增速只来自 3 个地市，不能当全省数字用。
- R5 不能把服务区站点算成活动的成绩。
- 机构自报的销量数字只作背景，不作证据。

## 内容层 {#content}

| 比较项 | 参加县 | 未参加县 |
|---|---|---|
| 公共桩年均增速（2022—2024） | 41% | 23% |
| 资料来源 | S02 | S02 |

## 闸门层 {#gate}

预设的判定：参加县增速明显更高且新增主要是公共桩为「成立」；增速更高但主要来自其他政策为「部分成立」；没有明显差别为「不成立」。对照资料，判为「部分成立」：参加活动的县增速确实更高，但同期的农村电网改造是装桩的前提，贡献分不开。反例纳入标准：未参加县增速与参加县相当，或新增主要来自服务区与电网项目。

## 下一步层 {#next}

- G1 2021 年分县充电桩数：查过省统计局与能源局，均未公布（gap）。10-01 你决定增速从 2022 年开始算。
- G2 乡镇桩使用率：只有两个地市的零散报道（gap）。

## 索引层 {#index}

- R001 [《H 省能源发展年度报告 2025》（虚构）](https://example.org/h-province/energy-report-2025.pdf)
- R002 [《H 省三地市县域充电设施建设通报（2025）》（虚构）](https://example.org/h-province/county-charging-notice-2025.pdf)
- R003 [《H 省三地市县域充电设施建设通报（2025）附表》（虚构）](https://example.org/h-province/county-charging-notice-2025-annex.pdf)
- R004 [《H 省新能源汽车下乡活动总结（2024）》（虚构）](https://example.org/h-province/nev-countryside-summary-2024.pdf)
- R005 [《H 省能源发展年度报告 2025》第 4 章（虚构）](https://example.org/h-province/energy-report-2025-ch4.pdf)
- R006 [《关于支持新能源汽车下乡配套充电设施建设的通知》（虚构）](https://example.org/h-province/subsidy-notice-2023.pdf)
- R007 [《H 省充电设施运行监测季报（2024 年第四季度）》（虚构）](https://example.org/h-province/charging-monitor-2024q4.pdf)
- R008 [《H 省县域充电设施建设通报汇总表》（虚构）](https://example.org/h-province/county-charging-summary.xlsx)
- R009 [《H 省新能源汽车下乡活动总结（2024）》第三部分（虚构）](https://example.org/h-province/nev-countryside-summary-2024-part3.pdf)
- R010 [《H 省农村电网巩固提升工程进展情况》（虚构）](https://example.org/h-province/rural-grid-progress-2024.pdf)
- R011 [《H 省交通运输发展统计公报 2024》（虚构）](https://example.org/h-province/transport-bulletin-2024.pdf)
- R012 [《H 省三地市县域充电设施建设通报（2025）》附表注（虚构）](https://example.org/h-province/county-charging-notice-2025-notes.pdf)
- R013 [《H 省充电设施运行监测季报（2024 年第四季度）》表 7（虚构）](https://example.org/h-province/charging-monitor-2024q4-t7.pdf)
- R014 [《关于公布新能源汽车下乡活动参加县名单的通知》（虚构）](https://example.org/h-province/county-list-2024.pdf)
- R015 [某车企 2024 年县域市场新闻稿（虚构）](https://example.org/h-province/carmaker-press-2024.html)
- R016 [《H 省县域充电设施建设规划（2021—2025）》（虚构）](https://example.org/h-province/county-charging-plan-2021.pdf)
- R017 [《A 市统计公报 2024》（虚构）](https://example.org/h-province/a-city-bulletin-2024.pdf)
- R018 [《B 市县域充电设施建设专报》（虚构）](https://example.org/h-province/b-city-charging-brief.pdf)
- R019 [《C 市乡镇充电站建设情况》（虚构）](https://example.org/h-province/c-city-township-stations.html)
- R020 [《H 省新能源汽车推广应用年报 2024》（虚构）](https://example.org/h-province/nev-annual-2024.pdf)
- R021 [《A 市交通运输局 2024 年工作总结》（虚构）](https://example.org/h-province/a-city-transport-2024.pdf)
- R022 [《H 省财政厅 2023 年专项资金下达通知》（虚构）](https://example.org/h-province/special-funds-2023.pdf)
- R023 [《H 省充电设施运行监测季报（2022 年第四季度）》（虚构）](https://example.org/h-province/charging-monitor-2022q4.pdf)
- R024 [《H 省县域充电设施建设现场会材料》（虚构）](https://example.org/h-province/county-charging-meeting-2024.html)
- R025 [《H 省县级政府绩效考核指标说明（2023）》（虚构）](https://example.org/h-province/county-assessment-2023.pdf)
- R026 [《H 省充电设施运行监测季报（2025 年第三季度）》（虚构）](https://example.org/h-province/charging-monitor-2025q3.pdf)

## 要你认的三件事 {#commitments}

1. **改动内容**：这是第 1 版。
2. **最薄弱的依据**：「参加县增速 41%」只来自 3 个地市的数据。
3. **最可能出错的地方**：把结论写过界。只适用于 H 省平原地区的县；不能推到全国；不能说「翻了一番」，因为 2021 年没有分县数据。
