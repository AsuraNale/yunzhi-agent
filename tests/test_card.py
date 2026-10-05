# -*- coding: utf-8 -*-
"""card.py:七种卡在对应阶段出得来;每类退回各有反例与改对后的对照;回答的三种真实形状;没回答就没有确认记录。"""
import json
import os

import pytest

import card
import yzlib
from conftest import MAIN, jsonl, read, write
from yzlib import pl

PICKED = '{"answers":{"task-plan-v2":{"answers":["确认，开始收集资料"]}}}'
TYPED = '{"answers":{"task-plan-v2":{"answers":["时间段改成 2021 年开始"]}}}'
SKIPPED = '{"answers":{}}'
REPORT_FIELDS = {"report_name": "内参", "thesis": "下乡活动带动了县域公共充电设施增长",
                 "why": "你说「领导就想知道这钱花得值不值」，这需要一个判断。两种写法的区别在于要不要由这份报告来证明它。",
                 "recommend": "judge"}
DECISION_FIELDS = {"question": "2021 年的分县充电桩数据查不到。增速从哪年开始算？",
                   "why": "查过省统计局和能源局，都没有公布 2021 年的分县数据。不定下来，活动前后的对比就没法算。",
                   "options": [{"label": "从 2022 年开始算", "effect": "少一年，但全是分县数据，结论最站得住。", "changes_plan": False},
                               {"label": "用全省数据补 2021 年", "effect": "年份完整，但 2021 年那段只能说全省，不能说县。", "changes_plan": False},
                               {"label": "改成梳理情况", "effect": "不下判断，只梳理现状。", "changes_plan": True}],
                   "recommend": 1}


def plan_meta(project):
    meta, _body, problem = pl.load_md(os.path.join(project, "task_plan.md"))
    assert problem is None
    return meta


def approval(project, doc="task_plan.md"):
    meta, _b, _p = pl.load_md(os.path.join(project, doc))
    return meta.get("approval") or {}


def cards_log(project):
    return jsonl(os.path.join(project, "records", "cards.jsonl"))


def cite_counts(project):
    """独立跑一遍交付前检查(不经 card.py),数 PASS / WARN / 总数。"""
    import shutil
    tmp = yzlib.work_tmp("test-cite")
    try:
        out = os.path.join(tmp, "c.json")
        yzlib.run_toolkit("cite_check.py", ["--project", project, "--json", out])
        with open(out, encoding="utf-8") as f:
            items = json.load(f)["items"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    items = [i for i in items if i["check"] != "预算仪表·步数"]   # 只有客户端做得了的那一项不算(agent 版取不到步数)
    return {"total": len(items), "PASS": sum(1 for i in items if i["status"] == "PASS"),
            "WARN": sum(1 for i in items if i["status"] == "WARN")}


# ---------------------------------------------------------------- 七种卡在对应阶段都出得来

def test_report_type_card_at_s0b(world):
    world("S0b")
    out, code = card.prepare("report_type", MAIN, dict(REPORT_FIELDS))
    assert code == 0, out
    assert out["card_id"] == "report-type"
    q = out["ask"]["questions"][0]
    assert q["question"] == "这份内参是要对「下乡活动带动了县域公共充电设施增长」下一个明确判断，还是先把情况梳理清楚？"
    assert [o["label"] for o in q["options"]] == ["下判断（研判型） · 推荐", "梳理情况（综述型）", "先不定"]
    assert "**为什么问这个**" in out["message"] and "你说「领导就想知道这钱花得值不值」" in out["message"]


def test_task_plan_card_at_s1_reads_numbers_from_files(world):
    project = world("S1")
    out, code = card.prepare("task_plan", MAIN, {"outside": []})
    assert code == 0, out
    meta = plan_meta(project)
    assert out["card_id"] == "task-plan-v%d" % meta["version"]
    assert out["ask"]["questions"][0]["question"].split("\n")[0] == "任务计划第 %d 版可以定下来，开始收集资料吗？" % meta["version"]
    _p, _n, items = pl.commitment_problems(pl.load_md(os.path.join(project, "task_plan.md"))[1])
    for label, it in zip(card.POINT_LABELS, items):
        assert "%s：%s" % (label, it["content"]) in out["message"]
    assert "第 1 次确认 · 任务计划" in out["message"]
    assert "这一轮读过的项目以外的文件（助手自报）：0 个" in out["message"]
    assert "我读过" not in out["message"]                    # 卡上一个口吻:助手
    assert "判定标准（成立 / 部分成立 / 不成立）" in out["message"]


def test_decision_card_at_s2(world):
    world("S2")
    out, code = card.prepare("decision", MAIN, json.loads(json.dumps(DECISION_FIELDS)))
    assert code == 0, out
    labels = [o["label"] for o in out["ask"]["questions"][0]["options"]]
    assert labels == ["从 2022 年开始算 · 助手建议", "用全省数据补 2021 年", "改成梳理情况"]
    assert out["ask"]["questions"][0]["options"][2]["description"].endswith("会改任务计划，再请你确认。")
    assert "需要你决定 · 收集资料" in out["message"]


def test_flow_change_card_at_s2_flow(world):
    world("S2-flow")
    out, code = card.prepare("flow_change", MAIN, {"changes": "去掉拟定提纲这一步，写作按资料汇编的结构走。"})
    assert code == 0, out
    assert out["card_id"] == "flow-change-v3"
    assert "明确任务 → 收集资料 → 撰写交付" in out["message"]
    assert "判断、范围、口径都没动。" in out["message"]


def test_dossier_card_at_s3_counts_cards_from_files(world):
    project = world("S3")
    out, code = card.prepare("dossier", MAIN, {"outside": []})
    assert code == 0, out
    # 独立数一遍卡片(不经 card.py)
    import yaml
    counts = {"support": 0, "counter": 0, "mixed": 0, "gap": 0}
    for fn in os.listdir(os.path.join(project, "cards")):
        meta = yaml.safe_load(read(os.path.join(project, "cards", fn)).split("---")[1])
        if not meta.get("deprecated"):
            counts[meta["stance"]] += 1
    total = sum(counts.values())
    assert "资料卡片 %d 张：支持判断 %d · 不支持 %d · 背景资料 %d · 资料缺口 %d" % (
        total, counts["support"], counts["counter"], counts["mixed"], counts["gap"]) in out["message"]
    assert "「部分成立」：" in out["message"]
    assert out["ask"]["questions"][0]["question"].split("\n")[0] == "资料汇编第 1 版可以定下来，开始拟提纲吗？"


def test_outline_card_at_s4(world):
    project = world("S4")
    out, code = card.prepare("outline", MAIN, {})
    assert code == 0, out
    from outline_check import parse_outline_md
    meta, nodes = parse_outline_md(os.path.join(project, "outline.md"))
    assert "合计 %d 字（目标 %d 字）" % (pl.outline_total_words(nodes), meta["total_words"]) in out["message"]
    assert "没有资料卡片支撑的节：%d 个" % len(pl.outline_unsupported(nodes)) in out["message"]


def test_delivery_card_at_s5(world):
    project = world("S5")
    out, code = card.prepare("delivery", MAIN, {})
    assert code == 0, out
    assert out["card_id"] == "delivery-1"
    with open(os.path.join(project, "review", "result.json"), encoding="utf-8") as f:
        counts = json.load(f)["counts"]
    assert "独立复核：必须改的 %d 处，说法收了 %d 处" % (counts["must_fix"], counts["tone_down"]) in out["message"]
    c = cite_counts(project)
    phrase = "%d / %d 通过" % (c["PASS"], c["total"]) + ("，另有 %d 项提醒" % c["WARN"] if c["WARN"] else "")
    assert "交付前检查：%s" % phrase in out["message"]
    assert "全部通过" not in out["message"]                  # 提醒不算通过,不说「全部通过」


def test_option_labels_are_unique_on_every_card():
    for spec in card.CONFIRM_OPTIONS.values():
        labels = [label for _k, label, _d in spec]
        assert len(set(labels)) == len(labels)
        assert "确认" not in labels   # 一个「确认」两个字太像意见框里会写的话
    assert len({label for _k, label, _d in card.REPORT_OPTIONS}) == 3


# ---------------------------------------------------------------- 每类退回:反例 + 改对后的对照

def test_refuse_over_length_then_ok(world):
    world("S0b")
    bad = dict(REPORT_FIELDS, thesis="下乡活动" * 11)   # 44 字 > 40
    out, code = card.prepare("report_type", MAIN, bad)
    assert code == 1 and "超过上限 40 字" in out["refuse"]
    out, code = card.prepare("report_type", MAIN, dict(REPORT_FIELDS))
    assert code == 0, out


def test_refuse_incomplete_points_then_ok(world):
    project = world("S1")
    path = os.path.join(project, "task_plan.md")
    good = read(path)
    lines = good.split("\n")
    bad = "\n".join(l for l in lines if not l.startswith("3. **最可能出错的地方**"))
    write(path, bad)
    out, code = card.prepare("task_plan", MAIN, {"outside": []})
    assert code == 1 and "不齐全" in out["refuse"]
    write(path, good)
    out, code = card.prepare("task_plan", MAIN, {"outside": []})
    assert code == 0, out


def test_refuse_missing_outside_self_report_then_ok(world):
    world("S1")
    out, code = card.prepare("task_plan", MAIN, {})
    assert code == 1 and "outside" in out["refuse"]
    out, code = card.prepare("task_plan", MAIN, {"outside": ["D:\\研究项目\\旧稿\\2023县域充电调研（旧稿）.docx"]})
    assert code == 0 and "（助手自报）：1 个" in out["message"]
    assert "  - 2023县域充电调研（旧稿）" in out["message"]       # 卡上写文件的名字,不写路径
    assert "D:" not in out["message"] and ".docx" not in out["message"] and "研究项目" not in out["message"]


def test_refuse_quote_not_from_user_then_ok(world):
    world("S0b")
    bad = dict(REPORT_FIELDS, why="你说「领导只想知道花得值不值」，这需要一个判断。")
    out, code = card.prepare("report_type", MAIN, bad)
    assert code == 1 and "不是用户的原话" in out["refuse"]
    out, code = card.prepare("report_type", MAIN, dict(REPORT_FIELDS))
    assert code == 0


def test_refuse_banned_wording_with_user_term_then_ok(world):
    world("S2")
    bad = json.loads(json.dumps(DECISION_FIELDS))
    bad["why"] = "预注册的闸门被触发了，不定下来就没法往下做。"
    out, code = card.prepare("decision", MAIN, bad)
    assert code == 1
    assert "预注册" in out["refuse"] and "事先写好判定标准" in out["refuse"]
    out, code = card.prepare("decision", MAIN, json.loads(json.dumps(DECISION_FIELDS)))
    assert code == 0


def test_refuse_delivery_when_checks_fail_then_ok(world):
    project = world("S5")
    refs = os.path.join(project, "references.yaml")
    good = read(refs)
    import yaml
    data = yaml.safe_load(good)
    data["entries"] = data["entries"][1:]          # 删掉一条 → 底稿里有悬空的占位,交付前检查不过
    write(refs, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    out, code = card.prepare("delivery", MAIN, {})
    assert code == 1 and "交付前检查没有全过" in out["refuse"]
    write(refs, good)
    out, code = card.prepare("delivery", MAIN, {})
    assert code == 0, out


def test_refuse_delivery_when_review_is_not_for_current_draft_then_ok(world):
    project = world("S5")
    html = [p for _r, p in pl.deliverable_paths(project) if p.endswith(".html")][0]
    good = read(html)
    write(html, good + "\n<!-- 复核之后又动过 -->\n")
    out, code = card.prepare("delivery", MAIN, {})
    assert code == 1 and "对不上当前成稿" in " ".join(out["problems"])
    write(html, good)
    out, code = card.prepare("delivery", MAIN, {})
    assert code == 0, out


def test_refuse_delivery_when_must_fix_open_then_ok(world):
    project = world("S5")
    rpath = os.path.join(project, "review", "result.json")
    good = read(rpath)
    result = json.loads(good)
    result["findings"].append({"severity": "must_fix", "round": result["round"], "status": "open",
                               "location": "第三节第二段", "original": "带动全省翻番", "supported": "只支持参加县增速更高",
                               "fix": "改成参加县增速更高"})
    result["counts"] = pl.review_counts(result["findings"])
    write(rpath, json.dumps(result, ensure_ascii=False, indent=1))
    out, code = card.prepare("delivery", MAIN, {})
    assert code == 1 and "必须改" in out["refuse"]
    write(rpath, good)
    out, code = card.prepare("delivery", MAIN, {})
    assert code == 0, out


def test_refuse_flow_change_that_changes_more_than_flow_then_ok(world):
    project = world("S2-flow")
    path = os.path.join(project, "task_plan.md")
    good = read(path)
    write(path, good.replace("scope_brief: H 省县级", "scope_brief: H 省地级"))
    out, code = card.prepare("flow_change", MAIN, {"changes": "去掉拟定提纲这一步，写作按资料汇编的结构走。"})
    assert code == 1 and "不只改了流程" in out["refuse"]
    write(path, good)
    out, code = card.prepare("flow_change", MAIN, {"changes": "去掉拟定提纲这一步，写作按资料汇编的结构走。"})
    assert code == 0, out


def test_refuse_confirm_card_before_awaiting_state(world):
    project = world("S1")
    path = os.path.join(project, "task_plan.md")
    write(path, read(path).replace("pipeline_status: gate1_awaiting", "pipeline_status: planning"))
    out, code = card.prepare("task_plan", MAIN, {"outside": []})
    assert code == 1 and "--advance gate1_awaiting" in out["refuse"]


# ---------------------------------------------------------------- 回答:Codex 的三种真实形状

def test_answer_picked_option_writes_record_and_advances(world):
    project = world("S1")
    out, code = card.prepare("task_plan", MAIN, {"outside": []})
    assert code == 0
    assert approval(project).get("status") != "approved"
    res, code = card.answer(MAIN, "task-plan-v2", PICKED)
    assert code == 0 and res["result"] == "approved", res
    ap = approval(project)
    assert ap["status"] == "approved" and ap["approved_by"] == "用户" and ap["approval_quote"] == "确认，开始收集资料"
    meta, body, _ = pl.load_md(os.path.join(project, "task_plan.md"))
    assert ap["approved_hash"] == pl.content_hash(meta, body)
    assert meta["pipeline_status"] == "collecting"
    facts, items, _ = yzlib.pipeline_facts(project)
    assert not [i for i in items if i["status"] == "FAIL"]
    log = cards_log(project)
    assert [e["type"] for e in log] == ["prepared", "answered"]
    assert log[-1]["choice"] == "confirm" and log[-1]["raw"] == PICKED and log[-1]["recorded_by"] == "助手"


def test_answer_typed_note_is_not_yet_and_writes_no_record(world):
    project = world("S1")
    card.prepare("task_plan", MAIN, {"outside": []})
    res, code = card.answer(MAIN, "task-plan-v2", TYPED)
    assert code == 0 and res["result"] == "not_approved" and res["note"] == "时间段改成 2021 年开始"
    assert approval(project).get("status") != "approved"
    assert plan_meta(project)["pipeline_status"] == "gate1_awaiting"
    last = cards_log(project)[-1]
    assert last["type"] == "answered" and last["choice"] is None and last["note"] == "时间段改成 2021 年开始"
    msgs = jsonl(os.path.join(project, "records", "messages.jsonl"))
    assert msgs[-1]["text"] == "时间段改成 2021 年开始"


def test_answer_skipped_writes_no_record_and_logs_skipped(world):
    project = world("S1")
    card.prepare("task_plan", MAIN, {"outside": []})
    res, code = card.answer(MAIN, "task-plan-v2", SKIPPED)
    assert code == 0 and res["result"] == "skipped"
    assert "确认任务计划" in res["say"]
    assert approval(project).get("status") != "approved"
    assert plan_meta(project)["pipeline_status"] == "gate1_awaiting"
    assert cards_log(project)[-1]["type"] == "skipped"


@pytest.mark.parametrize("raw", [
    '{"answers":{"task-plan-v2":{"answers":[]}}}',
    '{"answers":{"task-plan-v2":{"answers":["确认，开始收集资料","先不确认"]}}}',
    '{"answers":{"another-card":{"answers":["卡号对不上，回答又不是选项名"]}}}',
    '{"answers":{"another-card":{"answers":["确认，开始收集资料"]},"third-card":{"answers":["先不确认"]}}}',
    '{"result":"ok"}',
    '[]',
    '',
])
def test_answer_unknown_shapes_are_unclear_and_stored_raw(world, raw):
    project = world("S1")
    card.prepare("task_plan", MAIN, {"outside": []})
    res, code = card.answer(MAIN, "task-plan-v2", raw)
    assert code == 0 and res["result"] == "unclear"
    assert approval(project).get("status") != "approved"
    last = cards_log(project)[-1]
    assert last["type"] == "unclear" and last["raw"] == raw


def test_report_type_shapes(world):
    world("S0b")
    card.prepare("report_type", MAIN, dict(REPORT_FIELDS))
    res, _ = card.answer(MAIN, "report-type", '{"answers":{"report-type":{"answers":["下判断（研判型） · 推荐"]}}}')
    assert res["result"] == "selected" and res["selected"] == "judge" and res["genre"] == "argument"
    card.prepare("report_type", MAIN, dict(REPORT_FIELDS))
    res, _ = card.answer(MAIN, "report-type", '{"answers":{"report-type":{"answers":["先写一版再说"]}}}')
    assert res["result"] == "note" and res["selected"] is None and res["note"] == "先写一版再说"
    card.prepare("report_type", MAIN, dict(REPORT_FIELDS))
    res, _ = card.answer(MAIN, "report-type", SKIPPED)
    assert res["result"] == "skipped" and res["selected"] is None


def test_decision_shapes(world):
    world("S2")
    out, _ = card.prepare("decision", MAIN, json.loads(json.dumps(DECISION_FIELDS)))
    cid = out["card_id"]
    res, _ = card.answer(MAIN, cid, '{"answers":{"%s":{"answers":["改成梳理情况"]}}}' % cid)
    assert res["result"] == "selected" and res["selected"] == 3 and res["changes_plan"] is True
    out, _ = card.prepare("decision", MAIN, json.loads(json.dumps(DECISION_FIELDS)))
    cid = out["card_id"]
    res, _ = card.answer(MAIN, cid, '{"answers":{"%s":{"answers":["两种都写进去"]}}}' % cid)
    assert res["result"] == "note" and res["selected"] is None


def test_looks_like_yes_note_still_writes_no_record(world):
    project = world("S1")
    card.prepare("task_plan", MAIN, {"outside": []})
    res, _ = card.answer(MAIN, "task-plan-v2", '{"answers":{"task-plan-v2":{"answers":["确认"]}}}')
    assert res["result"] == "not_approved" and res.get("looks_like_yes") is True
    assert approval(project).get("status") != "approved"


# ---------------------------------------------------------------- 文字卡模式

def test_text_card_mode(world, monkeypatch):
    project = world("S1")
    monkeypatch.setenv("YUNZHI_CARDS", "text")
    out, code = card.prepare("task_plan", MAIN, {"outside": []})
    assert code == 0 and out["mode"] == "text"
    assert "1. 确认，开始收集资料：" in out["fallback_text"] and "2. 先不确认：" in out["fallback_text"]
    assert "回复选项前面的数字" in out["fallback_text"] and "文字卡模式" in out["next"]
    res, _ = card.answer(MAIN, "task-plan-v2", "时间段改成 2021 开始")
    assert res["result"] == "not_approved" and approval(project).get("status") != "approved"
    card.prepare("task_plan", MAIN, {"outside": []})
    res, _ = card.answer(MAIN, "task-plan-v2", "跳过")
    assert res["result"] == "skipped"
    card.prepare("task_plan", MAIN, {"outside": []})
    res, _ = card.answer(MAIN, "task-plan-v2", "１")          # 全角数字也认
    assert res["result"] == "approved" and approval(project)["status"] == "approved"


def test_text_card_mode_by_flag_file_and_label_reply(world):
    project = world("S3")
    open(os.path.join(os.path.dirname(project), ".text-cards"), "w").close()
    out, _ = card.prepare("dossier", MAIN, {"outside": []})
    assert out["mode"] == "text"
    res, _ = card.answer(MAIN, out["card_id"], "确认，开始拟提纲")
    assert res["result"] == "approved" and res["state"] == "outlining"


# ---------------------------------------------------------------- 没回答就没有确认记录;材料变了不签

def test_no_answer_no_record(world):
    project = world("S3")
    before = read(os.path.join(project, "dossier.md"))
    out, code = card.prepare("dossier", MAIN, {"outside": []})
    assert code == 0
    assert read(os.path.join(project, "dossier.md")) == before
    assert approval(project, "dossier.md").get("status") != "approved"
    assert plan_meta(project)["pipeline_status"] == "gate2_awaiting"
    assert [e["type"] for e in cards_log(project) if e.get("kind") == "dossier"] == ["prepared"]


def test_changed_material_is_not_signed(world):
    project = world("S1")
    card.prepare("task_plan", MAIN, {"outside": []})
    path = os.path.join(project, "task_plan.md")
    write(path, read(path).replace("资料卡片不超过 30 张", "资料卡片不超过 40 张"))
    res, _ = card.answer(MAIN, "task-plan-v2", PICKED)
    assert res["result"] == "changed"
    assert approval(project).get("status") != "approved"


def test_answer_needs_a_prepared_card(world):
    world("S1")
    with pytest.raises(yzlib.UsageError):
        card.answer(MAIN, "task-plan-v2", PICKED)


def test_note_user_appends_verbatim(world):
    project = world("S1")
    out, code = card.note_user(MAIN, "第三节太干，要细一点。")
    assert code == 0
    assert jsonl(os.path.join(project, "records", "messages.jsonl"))[-1]["text"] == "第三节太干，要细一点。"


def test_flow_change_confirm_keeps_state_and_restores_consistency(world):
    project = world("S2-flow")
    _f, items, _ = yzlib.pipeline_facts(project)
    assert [i for i in items if i["status"] == "FAIL"]        # 改流程卡在等:任务计划确认之后又改过,开场检查报对不上是预期的
    out, code = card.prepare("flow_change", MAIN, {"changes": "去掉拟定提纲这一步，写作按资料汇编的结构走。"})
    assert code == 0
    labels = [o["label"] for o in out["ask"]["questions"][0]["options"]]
    res, _ = card.answer(MAIN, "flow-change-v3", '{"answers":{"flow-change-v3":{"answers":["%s"]}}}' % labels[0])
    assert res["result"] == "approved"
    assert plan_meta(project)["pipeline_status"] == "collecting"
    _f, items, _ = yzlib.pipeline_facts(project)
    assert not [i for i in items if i["status"] == "FAIL"]


def test_flow_change_not_yet_tells_how_to_restore(world):
    world("S2-flow")
    card.prepare("flow_change", MAIN, {"changes": "去掉拟定提纲这一步，写作按资料汇编的结构走。"})
    res, _ = card.answer(MAIN, "flow-change-v3", '{"answers":{"flow-change-v3":{"answers":["先不确认"]}}}')
    assert res["result"] == "not_approved"
    assert "version 改回 2" in res["next"] and "v 写 3" in res["next"]


def test_full_confirm_chain_through_delivery(world):
    """S5 交付卡点了「交付」→ 已交付、交付记录在;pipeline_status 一致。"""
    project = world("S5")
    out, code = card.prepare("delivery", MAIN, {})
    assert code == 0
    res, _ = card.answer(MAIN, out["card_id"], '{"answers":{"%s":{"answers":["交付成稿"]}}}' % out["card_id"])
    assert res["result"] == "approved" and res["state"] == "delivered"
    assert plan_meta(project)["pipeline_status"] == "delivered"
    assert os.path.isfile(os.path.join(project, "records", "delivery.json"))
    _f, items, _ = yzlib.pipeline_facts(project)
    assert not [i for i in items if i["status"] == "FAIL"]
