# -*- coding: utf-8 -*-
"""card.py 的修复(10-03 两轮复核):每条修复一个反例 + 改对后的对照。"""
import json
import os
import re
import shutil

import pytest

import card
import progress
import projects
import yzlib
from conftest import MAIN, jsonl, read, write
from yzlib import pl

TALENT = "长三角人才引进政策梳理"
FLOW_FIELDS = {"changes": "去掉拟定提纲这一步，写作按资料汇编的结构走。"}


def ans(card_id, label):
    return json.dumps({"answers": {card_id: {"answers": [label]}}}, ensure_ascii=False)


def plan(project):
    return pl.load_md(os.path.join(project, "task_plan.md"))


def log_types(project):
    return [e["type"] for e in jsonl(os.path.join(project, "records", "cards.jsonl"))]


# ---------------------------------------------------------------- A · 改流程确认之后,标记要消失

def test_flow_change_card_refused_once_confirmed(world):
    project = world("S2-flow")
    card.prepare("flow_change", MAIN, dict(FLOW_FIELDS))
    res, _ = card.answer(MAIN, "flow-change-v3", ans("flow-change-v3", "确认，按新流程走"))
    assert res["result"] == "approved"
    meta, body, _p = plan(project)
    assert not yzlib.flow_change_pending(meta, body)
    out, code = card.prepare("flow_change", MAIN, dict(FLOW_FIELDS))
    assert code == 1 and "确认记录对得上" in out["refuse"]


def test_flow_change_pending_needs_a_stale_approval(world):
    project = world("S2-flow")
    meta, body, _p = plan(project)
    assert yzlib.flow_change_pending(meta, body)                 # 改流程卡在等:三条都成立
    fresh = dict(meta, approval=dict(meta["approval"], approved_hash=pl.content_hash(meta, body)))
    assert yzlib.flow_change_logged(fresh)                       # 最后一条照旧是「改流程：」、v 等于 version
    assert not yzlib.flow_change_pending(fresh, body)            # 但确认记录对得上了 → 不在等


# ---------------------------------------------------------------- L · 第二张交付卡、改流程卡不能再记一次

def test_second_open_flow_change_card_does_not_record_again(world):
    project = world("S2-flow")
    card.prepare("flow_change", MAIN, dict(FLOW_FIELDS), card_id="flow-change-a")
    card.prepare("flow_change", MAIN, dict(FLOW_FIELDS), card_id="flow-change-b")
    res, _ = card.answer(MAIN, "flow-change-a", ans("flow-change-a", "确认，按新流程走"))
    assert res["result"] == "approved"
    first = plan(project)[0]["approval"]
    res, _ = card.answer(MAIN, "flow-change-b", ans("flow-change-b", "确认，按新流程走"))
    assert res["result"] == "changed" and "已经确认过了" in res["next"]
    assert plan(project)[0]["approval"] == first
    assert log_types(project)[-1] == "changed"


def test_second_open_delivery_card_does_not_record_again(world):
    project = world("S5")
    card.prepare("delivery", MAIN, {}, card_id="delivery-a")
    card.prepare("delivery", MAIN, {}, card_id="delivery-b")
    res, _ = card.answer(MAIN, "delivery-a", ans("delivery-a", "交付成稿"))
    assert res["result"] == "approved" and res["state"] == "delivered"
    record = read(os.path.join(project, "records", "delivery.json"))
    res, _ = card.answer(MAIN, "delivery-b", ans("delivery-b", "交付成稿"))
    assert res["result"] == "changed" and "交付过" in res["say"]
    assert read(os.path.join(project, "records", "delivery.json")) == record


def test_malformed_prepared_line_gives_a_clean_error(world, capsys):
    project = world("S1")
    yzlib.append_jsonl(os.path.join(project, "records", "cards.jsonl"),
                       {"type": "prepared", "card_id": "task-plan-v2", "kind": "task_plan", "options": "坏了", "data": {}})
    inbox = os.path.join(os.path.dirname(project), "_inbox")
    os.makedirs(inbox, exist_ok=True)
    write(os.path.join(inbox, "answer.txt"), ans("task-plan-v2", "确认，开始收集资料"))
    code = card.main(["card.py", "answer", MAIN, "--card", "task-plan-v2", "--answer-file", os.path.join(inbox, "answer.txt")])
    out = json.loads(capsys.readouterr().out)
    assert code == 2 and out["ok"] is False and "出卡记录读不出来" in out["error"]


def test_internal_errors_print_json_not_a_traceback(capsys):
    def boom(argv):
        raise KeyError("x")
    assert yzlib.run_main(boom, []) == 3
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and "脚本出错了" in out["error"] and "KeyError" in out["error"]


# ---------------------------------------------------------------- D · 认不出就关卡;数字只认文字卡

def test_unclear_answer_closes_the_card(world):
    project = world("S1")
    card.prepare("task_plan", MAIN, {"outside": []})
    res, _ = card.answer(MAIN, "task-plan-v2", '{"result":"ok"}')
    assert res["result"] == "unclear"
    assert projects.pending_card(project) is None
    with pytest.raises(yzlib.UsageError):
        card.answer(MAIN, "task-plan-v2", ans("task-plan-v2", "确认，开始收集资料"))
    assert (plan(project)[0].get("approval") or {}).get("status") != "approved"


def test_numbers_count_on_native_and_text_cards(world):
    # 第五轮 H1(10-04 实测:桌面版原生卡上的选项有编号):原生卡上用户写「2」也是选第 2 项,和文字卡一样
    project = world("S1")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    assert out["mode"] == "native"
    res, _ = card.answer(MAIN, "task-plan-v2", ans("task-plan-v2", "2"))     # 原生卡上手写「2」= 先不确认
    assert res["result"] == "not_approved" and res["choice"] == "not_yet" and res["note"] is None
    assert (plan(project)[0].get("approval") or {}).get("status") != "approved"
    out, _ = card.prepare("task_plan", MAIN, {"outside": []}, mode="text")   # 选项框用不了:按文字卡重新准备
    assert out["mode"] == "text" and "文字卡" in out["mode_reason"]
    assert jsonl(os.path.join(project, "records", "cards.jsonl"))[-1]["mode"] == "text"
    res, _ = card.answer(MAIN, "task-plan-v2", "1")
    assert res["result"] == "approved"


def test_mode_option_rejects_anything_but_text(world):
    world("S1")
    with pytest.raises(yzlib.UsageError):
        card.prepare("task_plan", MAIN, {"outside": []}, mode="native")


# ---------------------------------------------------------------- E · 选项名

DEC = {"question": "2021 年的分县数据查不到。增速从哪年开始算？", "why": "查过省统计局和能源局，都没有公布 2021 年的分县数据。不定下来，活动前后的对比就没法算。"}


def decision(*labels, recommend=None):
    f = dict(DEC, options=[{"label": l, "effect": "会这样做。", "changes_plan": i == 1} for i, l in enumerate(labels)])
    if recommend:
        f["recommend"] = recommend
    return f


@pytest.mark.parametrize("labels, rec, why", [
    (("改成梳理情况", "改成 梳理情况"), None, "都能叫"),
    (("从 2022 年开始算 · 助手建议", "用全省数据补 2021 年"), None, "别自己写"),
    (("跳过", "用全省数据补 2021 年"), None, "跳过"),
    (("2", "用全省数据补 2021 年"), None, "数字"),
    (("第 1 个", "用全省数据补 2021 年"), None, "数字"),
])
def test_bad_option_labels_are_refused(world, labels, rec, why):
    world("S2")
    out, code = card.prepare("decision", MAIN, decision(*labels, recommend=rec))
    assert code == 1 and why in " ".join(out["problems"]), out


def test_good_labels_pass_and_keep_changes_plan(world):
    world("S2")
    out, code = card.prepare("decision", MAIN, decision("从 2022 年开始算", "改成梳理情况", recommend=1))
    assert code == 0, out
    cid = out["card_id"]
    res, _ = card.answer(MAIN, cid, ans(cid, "改成 梳理情况"))      # 空格不同也认得出是第 2 项
    assert res["result"] == "selected" and res["selected"] == 2 and res["changes_plan"] is True


# ---------------------------------------------------------------- F · 资料卡片、交付三点的指纹

def test_dossier_card_not_signed_when_cards_change(world):
    project = world("S3")
    out, _ = card.prepare("dossier", MAIN, {"outside": []})
    p = os.path.join(project, "cards", "S01.md")
    write(p, read(p) + "\n")
    res, _ = card.answer(MAIN, out["card_id"], ans(out["card_id"], "确认，开始拟提纲"))
    assert res["result"] == "changed"
    assert (pl.load_md(os.path.join(project, "dossier.md"))[0].get("approval") or {}).get("status") != "approved"


def test_delivery_card_not_signed_when_points_change(world):
    project = world("S5")
    out, _ = card.prepare("delivery", MAIN, {})
    p = os.path.join(project, "library", "delivery_commitments.md")
    write(p, read(p).replace("41%", "四成"))
    res, _ = card.answer(MAIN, out["card_id"], ans(out["card_id"], "交付成稿"))
    assert res["result"] == "changed"
    assert plan(project)[0]["pipeline_status"] == "verifying"
    assert not os.path.exists(os.path.join(project, "records", "delivery.json"))


# ---------------------------------------------------------------- G · 写确认记录失败:单独记,不进确认记录

def test_failed_stamp_is_logged_as_error_and_kept_off_the_page(world, monkeypatch):
    project = world("S1")
    card.prepare("task_plan", MAIN, {"outside": []})
    real = yzlib.run_toolkit

    def fake(script, args, timeout=600):
        if script == "stamp.py" and "--approve" in args and ".yz-tmp" not in str(args[0]):
            return 1, "", "写不进去"
        return real(script, args, timeout)
    monkeypatch.setattr(yzlib, "run_toolkit", fake)
    res, _ = card.answer(MAIN, "task-plan-v2", ans("task-plan-v2", "确认，开始收集资料"))
    assert res["result"] == "error"
    assert log_types(project)[-1] == "error"
    assert progress.model(project)["records"] == []


# ---------------------------------------------------------------- H · 三点、判定标准、初步结论的理由也核引文

def test_fake_quote_in_points_is_refused_then_real_quote_passes(world):
    project = world("S1")
    path = os.path.join(project, "task_plan.md")
    good = read(path)
    old = "1. **改动内容**：按你的意见，时间段从 2022–2025 改成 2021–2025；其余没有改动。"
    assert old in good
    write(path, good.replace(old, "1. **改动内容**：你说「时间段从 2019 年开始」，所以改了时间段。"))
    out, code = card.prepare("task_plan", MAIN, {"outside": []})
    assert code == 1 and "不是用户的原话" in out["refuse"]
    write(path, good.replace(old, "1. **改动内容**：你说「八千字左右」，篇幅按这个写；其余没有改动。"))
    out, code = card.prepare("task_plan", MAIN, {"outside": []})
    assert code == 0, out


def test_fake_quote_in_criteria_row_is_refused(world):
    project = world("S1")
    path = os.path.join(project, "task_plan.md")
    good = read(path)
    old = "| C | 参加与没参加的县增速没有明显差别 | 不成立 |"
    write(path, good.replace(old, "| C | 委托里「两类县一样快」 | 不成立 |"))
    out, code = card.prepare("task_plan", MAIN, {"outside": []})
    assert code == 1 and "判定标准第 3 行" in out["refuse"]


def test_fake_quote_in_gate_reason_is_refused(world):
    project = world("S3")
    path = os.path.join(project, "dossier.md")
    good = read(path)
    old = 'reason: "参加活动的县增速确实更高，但同期的农村电网改造是装桩的前提，贡献分不开。"'
    assert old in good
    write(path, good.replace(old, 'reason: "你说「电网改造才是主因」，所以判部分成立。"'))
    out, code = card.prepare("dossier", MAIN, {"outside": []})
    assert code == 1 and "初步结论的理由" in out["refuse"]


# ---------------------------------------------------------------- I · 卡上列出的节名、项目外文件的名字

def test_unsupported_section_titles_on_outline_card_are_scanned(world):
    project = world("S4")
    path = os.path.join(project, "outline.md")
    good = read(path)
    old = "## 五、政策建议 {id: s5, words: 1600}\n- point: 补乡镇、管闲置、补数据\n- task: 提三到五条建议\n- evidence: [S13, S18, S17]"
    assert old in good
    write(path, good.replace(old, "## 五、argument 收尾 {id: s5, words: 1600}\n- point: 补乡镇、管闲置、补数据\n- task: 提三到五条建议\n- evidence: []\n- evidence_mode: pending_evidence"))
    out, code = card.prepare("outline", MAIN, {})
    assert code == 1 and "没有资料卡片支撑的节 1" in out["refuse"]
    write(path, good.replace(old, "## 五、政策建议 {id: s5, words: 1600}\n- point: 补乡镇、管闲置、补数据\n- task: 提三到五条建议\n- evidence: []\n- evidence_mode: pending_evidence"))
    out, code = card.prepare("outline", MAIN, {})
    assert code == 0, out
    assert "  - 五、政策建议" in out["message"]


@pytest.mark.parametrize("path, shown", [
    ("D:\\研究项目\\旧稿\\2023县域充电调研（旧稿）.docx", "2023县域充电调研（旧稿）"),
    ("C:\\work\\task_plan.md", card.OUTSIDE_GENERIC),
    ("/Users/x/a1.txt", card.OUTSIDE_GENERIC),
    ("C:\\Users\\x\\.codex\\memories\\MEMORY.md", "MEMORY"),
])
def test_outside_files_show_a_human_name(path, shown):
    assert card.outside_name(path) == shown


# ---------------------------------------------------------------- C · 卡上写「右侧已打开」,附点开看

def test_confirm_cards_open_the_material_and_link_it(world):
    project = world("S3")
    # 第四轮:「右侧已打开」只在这个对话里挂过右侧时写(--pane-open);这里模拟挂过的情况,没挂过的见 test_round4
    out, code = card.prepare("dossier", MAIN, {"outside": []}, pane_open=True)
    assert code == 0
    page = out["view"]["page"]
    assert os.path.isfile(page) and page.endswith(os.path.join("查看", "资料汇编.html"))
    assert out["view_url"] == progress.file_url(page)
    assert "- 资料汇编第 1 版（右侧已打开 · [点开看](<%s>)）" % out["view_url"] in out["message"]
    cards_page = os.path.join(project, "查看", "资料卡片.html")
    assert "（[点开看](<%s>)）" % progress.file_url(cards_page) in out["message"]
    # 右侧那一页已经换成这份材料(10-03 实测:每打开一次就多一个标签,所以不再让 agent 去打开)
    assert yzlib.pane_kind(project) == "view:dossier" and out["pane_shows"] == "view:dossier"
    assert out["pane_url"] == progress.file_url(os.path.join(project, "右侧.html"))
    assert "资料汇编第 1 版" in read(os.path.join(project, "右侧.html"))
    assert out["next"].startswith("右侧那一页已经换成这份材料") and "open_in_codex" not in out["next"]


def test_answer_puts_the_progress_page_back(world):
    project = world("S1")
    card.prepare("task_plan", MAIN, {"outside": []})
    assert yzlib.pane_kind(project) == "view:task_plan"
    res, _ = card.answer(MAIN, "task-plan-v2", ans("task-plan-v2", "确认，开始收集资料"))
    assert res["progress_url"].endswith("%E8%BF%9B%E5%BA%A6.html") or res["progress_url"].endswith("进度.html")
    assert yzlib.pane_kind(project) == "progress" and res["pane_shows"] == "progress"
    assert "云织进度" in read(os.path.join(project, "右侧.html"))
    assert res["next"].startswith("右侧已经换回进度页") and "open_in_codex" not in res["next"]


# ---------------------------------------------------------------- 快照:只在回答之后、只在可视化目录

def test_snapshot_never_goes_into_the_card_message(world):
    world("S1")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    assert "visualize" not in out["message"] and "visualize" not in out["fallback_text"]


def test_snapshot_comes_with_the_answer(world, tmp_path):
    world("S1")
    card.prepare("task_plan", MAIN, {"outside": []})
    vis = str(tmp_path / "vis")
    res, _ = card.answer(MAIN, "task-plan-v2", ans("task-plan-v2", "确认，开始收集资料"), snapshot_dir=vis)
    assert res["visualize"].startswith("visualize{") and os.path.isfile(res["snapshot"])
    assert "最后一条回复" in res["next"]


def test_snapshot_is_never_written_inside_a_project(world):
    project = world("S5")
    out, _ = card.prepare("delivery", MAIN, {})
    before = sorted(os.listdir(os.path.join(project, "out")))
    res, _ = card.answer(MAIN, out["card_id"], ans(out["card_id"], "交付成稿"), snapshot_dir=os.path.join(project, "out"))
    assert res["result"] == "approved"
    assert sorted(os.listdir(os.path.join(project, "out"))) == before
    assert "visualize" not in res and res.get("snapshot_skipped")
    with pytest.raises(yzlib.UsageError):
        progress.snapshot(project, os.path.join(project, "out"))
    with pytest.raises(yzlib.UsageError):
        progress.snapshot(project, os.path.join(yzlib.ROOT, "projects", "x"))


# ---------------------------------------------------------------- 12 · 用户消息不记两遍、不带换行

def test_user_messages_are_stripped_and_not_doubled(world):
    project = world("S1")
    msgs = os.path.join(project, "records", "messages.jsonl")
    # 第三轮:用户连着两张卡都回「1」,第二个「1」被当成重复吞掉了 —— note-user 不按字去重,每一条都记(去掉首尾空白)
    card.note_user(MAIN, "1\n")
    card.note_user(MAIN, "1")
    assert [m["text"] for m in jsonl(msgs)][-2:] == ["1", "1"]
    # 回答卡片时才比一次,只在这张卡的范围里比:这张卡出了之后 note-user 已经记过同一句,就不再记
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    note = "时间段改成 2021 开始。"
    card.note_user(MAIN, note + "\n")
    card.answer(MAIN, out["card_id"], ans(out["card_id"], note))
    assert [m["text"] for m in jsonl(msgs)].count(note) == 1
    # 卡上意见框里写的(没经过 note-user),照记一条,标上是哪张卡
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    card.answer(MAIN, out["card_id"], ans(out["card_id"], note))
    rows = jsonl(msgs)
    assert [m["text"] for m in rows].count(note) == 2 and rows[-1].get("card") == out["card_id"]


# ---------------------------------------------------------------- 14a · 综述型不说「支持判断 / 不支持」

def test_survey_dossier_card_uses_neutral_wording(world):
    project = world("S6", TALENT)
    plan_path = os.path.join(project, "task_plan.md")
    write(plan_path, re.sub(r"pipeline_status: \S+", "pipeline_status: gate2_awaiting", read(plan_path)))
    d = os.path.join(project, "dossier.md")
    write(d, re.sub(r"approval:\n(?:  [^\n]*\n)+", "", read(d)))      # 资料汇编改回等确认
    # 照第三轮的规矩补齐(客户端生成器不写):原文摘录;资料汇编列了缺口,用户在决定卡上选过一次
    from conftest import record_decision_answer, upgrade_cards
    upgrade_cards(project, with_direction=False)
    record_decision_answer(project, "decision-gap", "先写已有的部分", yzlib.now_iso())
    counts = card.stance_counts(project)
    out, code = card.prepare("dossier", TALENT, {"outside": []})
    assert code == 0, out
    assert "资料卡片 %d 张：背景资料 %d · 资料缺口 %d" % (counts["total"], counts["total"] - counts["gap"], counts["gap"]) in out["message"]
    assert "支持判断" not in out["message"] and "不支持" not in out["message"]


# ---------------------------------------------------------------- 16 · 文字卡的页脚

def test_text_mode_footer_says_reply_1(world, monkeypatch):
    world("S1")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    assert "你点「确认」后，由助手写下确认记录。" in out["message"]
    assert "你回复 1 之后，由助手写下确认记录。" in out["fallback_text"] and "你点「确认」" not in out["fallback_text"]
    monkeypatch.setenv("YUNZHI_CARDS", "text")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    assert "你回复 1 之后" in out["message"] and "你点「确认」" not in out["message"]


def test_confirm_option_is_always_number_one():
    for spec in card.CONFIRM_OPTIONS.values():
        assert spec[0][0] in ("confirm", "deliver")


# ---------------------------------------------------------------- 卡上一个口吻:助手

def test_fixed_card_text_never_says_wo():
    fixed = [card.OUTSIDE, card.OUTSIDE_NOTE, card.OUTSIDE_GENERIC, card.FOOTER_CONFIRM, card.FOOTER_DELIVERY,
             card.CHANGES_PLAN_NOTE, card.UNCHANGED, card.VIEW_OPEN, card.VIEW_LINK, card.FALLBACK_TAIL]
    fixed += list(card.TEXT_FOOTERS.values()) + list(card.RECOMMEND_MARK.values())
    fixed += [x for spec in card.CONFIRM_OPTIONS.values() for o in spec for x in o[1:]]
    fixed += [x for o in card.REPORT_OPTIONS for x in o[1:]]
    assert [s for s in fixed if "我" in s] == []


# ---------------------------------------------------------------- M · 弃用的卡不算

def test_deprecated_cards_are_left_out_everywhere(world, capsys):
    project = world("S3")
    p = os.path.join(project, "cards", "S05.md")
    write(p, read(p).replace("kind: card\n", "kind: card\ndeprecated: true\n", 1))
    live = pl.count_live_cards(os.path.join(project, "cards"))
    assert card.stance_counts(project)["total"] == live == 17
    out, code = card.prepare("dossier", MAIN, {"outside": []})
    assert code == 0 and "资料卡片 %d 张：" % live in out["message"]
    assert "资料卡片 · 共 %d 张" % live in re.sub(r"<[^>]+>", " ", read(progress.write_page(project)))
    projects.main(["projects.py", "count", MAIN])
    assert json.loads(capsys.readouterr().out)["cards"] == live
