# -*- coding: utf-8 -*-
"""第五轮(10-04,桌面版 26.930 + GPT-6.1 Sol 实测、命令行全程试跑之后)的修复:每条一个反例 + 改对后的对照。

H1 「数字或选项名 + 一句话」= 选这一项 + 意见 · H2 卡被收起不等于不要了 · H3 对用户说话不带出内部东西 ·
M1 对话快照停用 · M2 推荐项放第一 · M3 归档脚本、项目里不放脚本 · M4 取来的资料另放 fetched/ · M5 只用会等回答的选项框 ·
L1 卡片回答不记两遍 · L2 提纲第一版就出卡 · L3 交付卡前的数字由脚本读 · L4 新建项目各用各的暂存文件夹。
"""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

import archive
import card
import fetches
import projects
import wording_check
import yzlib
from conftest import MAIN, ROOT, jsonl, read, write
from yzlib import pl

SKIPPED = '{"answers":{}}'
REPORT_FIELDS = {"report_name": "内参", "thesis": "下乡活动带动了县域公共充电设施增长",
                 "why": "你说「领导就想知道这钱花得值不值」，这需要一个判断。两种写法的区别在于要不要由这份报告来证明它。",
                 "recommend": "judge"}
DECISION_FIELDS = {"question": "2021 年的分县充电桩数据查不到。增速从哪年开始算？",
                   "why": "查过省统计局和能源局，都没有公布 2021 年的分县数据。不定下来，活动前后的对比就没法算。",
                   "options": [{"label": "从 2022 年开始算", "effect": "少一年，但全是分县数据，结论最站得住。", "changes_plan": False},
                               {"label": "用全省数据补 2021 年", "effect": "年份完整，但 2021 年那段只能说全省，不能说县。", "changes_plan": False},
                               {"label": "改成梳理情况", "effect": "不下判断，只梳理现状。", "changes_plan": True}]}


def ans(card_id, *strings):
    return json.dumps({"answers": {card_id: {"answers": list(strings)}}}, ensure_ascii=False)


def approval(project, doc="task_plan.md"):
    return pl.load_md(os.path.join(project, doc))[0].get("approval") or {}


def log(project):
    return jsonl(os.path.join(project, "records", "cards.jsonl"))


def messages(project):
    return [m["text"] for m in jsonl(os.path.join(project, "records", "messages.jsonl"))]


def skill(name):
    return read(os.path.join(ROOT, ".agents", "skills", name, "SKILL.md"))


def agents():
    return read(os.path.join(ROOT, "AGENTS.md"))


def report_options():
    opts = card.options_from(card.REPORT_OPTIONS)
    opts[0]["label"] = opts[0]["base"] + " · 推荐"
    return opts


# ================================================================ H1 · 数字或选项名 + 一句话

ACCEPT = [  # (回答, 选的第几项(从 1 数), 附的意见)
    ("1", 1, None), ("２", 2, None), ("选 2", 2, None), ("第2个", 2, None),
    ("1，判断标准里要把透支效应算进去", 1, "判断标准里要把透支效应算进去"),
    ("2，另外把各地进展的差距也写进去", 2, "另外把各地进展的差距也写进去"),
    ("1,好", 1, "好"), ("1、好", 1, "好"), ("1. 好", 1, "好"), ("1：好", 1, "好"), ("1；好", 1, "好"), ("1 好", 1, "好"),
    ("选1，好", 1, "好"), ("选择2，好", 2, "好"), ("第2个，好", 2, "好"), ("1）好", 1, "好"),
    ("梳理情况（综述型），另外把各地进展的差距也写进去", 2, "另外把各地进展的差距也写进去"),
    ("下判断（研判型） · 推荐", 1, None),
]
REJECT = ["2023年的数据也要", "3个方面都要写", "3 个方面都要写", "4，好", "我选1吧", "1.5倍的增长要写进去", "1、2都要", "先不定吧",
          "2 年的数据都要看"]          # 空白后面紧跟量词(「个」「项」「号」之外的:年、成、倍……)是在说数量


@pytest.mark.parametrize("text, pick, note", ACCEPT)
def test_reply_reader_accepts(text, pick, note):
    assert card.read_reply(text, report_options())[0::2] == (pick - 1, note)


@pytest.mark.parametrize("text", REJECT)
def test_reply_reader_rejects(text):
    assert card.read_reply(text, report_options())[0] is None


@pytest.mark.parametrize("mode", ["native", "text"])
def test_report_type_number_plus_note(world, monkeypatch, mode):
    project = world("S0b")
    if mode == "text":
        monkeypatch.setenv("YUNZHI_CARDS", "text")
    card.prepare("report_type", MAIN, dict(REPORT_FIELDS))
    said = "2，另外把各地进展的差距也写进去"
    res, _ = card.answer(MAIN, "report-type", said if mode == "text" else ans("report-type", said))
    assert res["result"] == "selected" and res["selected"] == "survey" and res["note"] == "另外把各地进展的差距也写进去"
    assert "另外把各地进展的差距也写进去" in res["next"] and res["say"]
    assert log(project)[-1]["shape"] == "number+note" and said in messages(project)
    # 反例:数字后面紧跟数字 → 只是意见,没选
    card.prepare("report_type", MAIN, dict(REPORT_FIELDS))
    res, _ = card.answer(MAIN, "report-type", "2023年的数据也要" if mode == "text" else ans("report-type", "2023年的数据也要"))
    assert res["result"] == "note" and res["selected"] is None


@pytest.mark.parametrize("mode", ["native", "text"])
def test_decision_number_plus_note(world, monkeypatch, mode):
    world("S2")
    if mode == "text":
        monkeypatch.setenv("YUNZHI_CARDS", "text")
    out, rc = card.prepare("decision", MAIN, json.loads(json.dumps(DECISION_FIELDS)))
    assert rc == 0, out
    cid = out["card_id"]
    said = "3，范围也写清楚"
    res, _ = card.answer(MAIN, cid, said if mode == "text" else ans(cid, said))
    assert res["result"] == "selected" and res["selected"] == 3 and res["changes_plan"] is True and "范围也写清楚" in res["next"]
    out, _ = card.prepare("decision", MAIN, json.loads(json.dumps(DECISION_FIELDS)))
    res, _ = card.answer(MAIN, out["card_id"], "我选1吧" if mode == "text" else ans(out["card_id"], "我选1吧"))
    assert res["result"] == "note" and res["selected"] is None


@pytest.mark.parametrize("mode", ["native", "text"])
def test_confirm_card_number_plus_note(world, monkeypatch, mode):
    project = world("S1")
    if mode == "text":
        monkeypatch.setenv("YUNZHI_CARDS", "text")
    card.prepare("task_plan", MAIN, {"outside": []})
    # 「2，……」= 先不确认 + 意见:什么记录都不写
    said = "2，时间段再看看"
    res, _ = card.answer(MAIN, "task-plan-v2", said if mode == "text" else ans("task-plan-v2", said))
    assert res["result"] == "not_approved" and res["choice"] == "not_yet" and res["note"] == "时间段再看看"
    assert approval(project).get("status") != "approved"
    # 「1，……」= 确认 + 意见:确认记录里的原话是意见(≤40 字),整段意见照记
    card.prepare("task_plan", MAIN, {"outside": []})
    note = "时间段改成 2021 年开始其余可以，另外请把乡镇站点的口径在卡片上写清楚，方便我抽查。"
    said = "1，" + note
    res, _ = card.answer(MAIN, "task-plan-v2", said if mode == "text" else ans("task-plan-v2", said))
    assert res["result"] == "approved" and res["note"] == note and len(note) > 40
    ap = approval(project)
    assert ap["status"] == "approved" and ap["approval_quote"] == note[:40]
    assert log(project)[-1]["note"] == note and log(project)[-1]["shape"] == "number+note"


def test_explicit_not_yet_with_a_yes_word_is_not_looks_like_yes(world):
    # 「2，好的」是明明白白选了「先不确认」(再附一句);只有没选选项、光写「好的」才是「像是想确认」
    world("S1")
    card.prepare("task_plan", MAIN, {"outside": []})
    res, _ = card.answer(MAIN, "task-plan-v2", ans("task-plan-v2", "2，好的"))
    assert res["result"] == "not_approved" and res["choice"] == "not_yet" and "looks_like_yes" not in res
    card.prepare("task_plan", MAIN, {"outside": []})
    res, _ = card.answer(MAIN, "task-plan-v2", ans("task-plan-v2", "好的"))
    assert res["result"] == "not_approved" and res.get("looks_like_yes") is True


@pytest.mark.parametrize("text", REJECT[:5])
def test_confirm_card_rejected_shapes_stay_notes(world, text):
    project = world("S1")
    card.prepare("task_plan", MAIN, {"outside": []})
    res, _ = card.answer(MAIN, "task-plan-v2", ans("task-plan-v2", text))
    assert res["result"] == "not_approved" and res["choice"] is None and res["note"] == text
    assert approval(project).get("status") != "approved"


def test_option_labels_cannot_start_with_a_number_and_punctuation(world):
    world("S2")
    fields = json.loads(json.dumps(DECISION_FIELDS))
    fields["options"][0]["label"] = "1，从 2022 年开始算"
    out, rc = card.prepare("decision", MAIN, fields)
    assert rc == 1 and "数字 + 标点" in out["refuse"]
    fields["options"][0]["label"] = "2022 年起算"                  # 数字后面紧跟数字的不算编号,照收
    out, rc = card.prepare("decision", MAIN, fields)
    assert rc == 0, out


# ================================================================ H2 · 卡被收起不等于不要了

def test_skipped_say_covers_both_cases_and_lists_the_numbers(world, monkeypatch):
    world("S1")
    card.prepare("task_plan", MAIN, {"outside": []})
    res, _ = card.answer(MAIN, "task-plan-v2", SKIPPED)
    assert res["result"] == "skipped"
    assert "你点了跳过，或者太久没操作被自动收起" in res["say"] and "直接回复选项前面的数字" in res["say"]
    assert "1 确认，开始收集资料" in res["say"] and "2 先不确认" in res["say"] and "「确认任务计划」" in res["say"]
    monkeypatch.setenv("YUNZHI_CARDS", "text")
    card.prepare("task_plan", MAIN, {"outside": []})
    res, _ = card.answer(MAIN, "task-plan-v2", "跳过")
    assert res["say"].startswith("好，这张卡先放一边。") and "直接回复选项前面的数字" in res["say"] and "自动收起" not in res["say"]


def test_native_cards_warn_about_auto_collapse_text_cards_do_not(world, monkeypatch):
    world("S1")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    assert card.NATIVE_COLLAPSE_NOTE in out["message"] and card.NATIVE_COLLAPSE_NOTE not in out["fallback_text"]
    monkeypatch.setenv("YUNZHI_CARDS", "text")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    assert card.NATIVE_COLLAPSE_NOTE not in out["message"] and card.NATIVE_COLLAPSE_NOTE not in out["fallback_text"]


def test_number_after_collapse_answers_the_card_once(world):
    project = world("S1")
    card.prepare("task_plan", MAIN, {"outside": []})
    card.answer(MAIN, "task-plan-v2", SKIPPED)
    out, _ = card.note_user(MAIN, "1")                              # 记用户消息时提示:像是在回答收起的卡
    assert out["looks_like_answer"]["card_id"] == "task-plan-v2" and "card.py\" answer" in out["next"]
    res, _ = card.answer(MAIN, "task-plan-v2", "1")
    assert res["result"] == "approved" and approval(project)["status"] == "approved"
    assert log(project)[-1].get("after_skip") is True
    assert messages(project).count("1") == 1                        # note-user 记过的那一句不再记
    with pytest.raises(yzlib.UsageError):                            # 只此一次
        card.answer(MAIN, "task-plan-v2", "1")


def test_after_collapse_only_one_more_answer(world):
    world("S1")
    card.prepare("task_plan", MAIN, {"outside": []})
    card.answer(MAIN, "task-plan-v2", SKIPPED)
    res, _ = card.answer(MAIN, "task-plan-v2", "2，时间段再看看")
    assert res["result"] == "not_approved"
    with pytest.raises(yzlib.UsageError):
        card.answer(MAIN, "task-plan-v2", "1")


def test_after_collapse_other_messages_are_just_chat(world):
    project = world("S1")
    card.prepare("task_plan", MAIN, {"outside": []})
    card.answer(MAIN, "task-plan-v2", SKIPPED)
    out, _ = card.note_user(MAIN, "顺便问一下，这轮补贴是哪个部门牵头的？")
    assert "looks_like_answer" not in out
    with pytest.raises(yzlib.UsageError):
        card.answer(MAIN, "task-plan-v2", "顺便问一下，这轮补贴是哪个部门牵头的？")
    assert approval(project).get("status") != "approved"


def test_after_collapse_a_newer_card_wins(world):
    project = world("S1")
    card.prepare("task_plan", MAIN, {"outside": []})
    card.answer(MAIN, "task-plan-v2", SKIPPED)
    out, rc = card.prepare("decision", MAIN, json.loads(json.dumps(DECISION_FIELDS)))
    assert rc == 0, out
    note, _ = card.note_user(MAIN, "1")
    assert "looks_like_answer" not in note
    with pytest.raises(yzlib.UsageError):
        card.answer(MAIN, "task-plan-v2", "1")
    assert approval(project).get("status") != "approved"


def test_after_collapse_changed_material_is_not_signed(world):
    project = world("S1")
    card.prepare("task_plan", MAIN, {"outside": []})
    card.answer(MAIN, "task-plan-v2", SKIPPED)
    path = os.path.join(project, "task_plan.md")
    write(path, read(path).replace("资料卡片不超过 30 张", "资料卡片不超过 40 张"))
    res, _ = card.answer(MAIN, "task-plan-v2", "1")
    assert res["result"] == "changed" and approval(project).get("status") != "approved"


# ================================================================ H3 · 对用户说话不带出内部东西

@pytest.mark.parametrize("text, internal", [
    ("按[收集资料流程说明](/C:/Users/x/云织Agent/.agents/skills/evidence-card/SKILL.md)要求先问", True),
    ("见[记录](projects/a/records/cards.jsonl)", True),
    ("见[脚本](agent-tools/card.py)", True),
    ("[Word 版](/C:/Users/x/云织Agent/projects/a/out/a.docx)", False),
    ("[任务计划](projects/a/查看/任务计划.html)", False),
    ("见[官网](https://www.mofcom.gov.cn/a.html)", False),
])
def test_wording_check_blocks_links_to_internal_files(text, internal):
    rules = [h["rule"] for h in wording_check.scan(text)]
    assert ("env:internal-link" in rules) is internal


def test_internal_link_on_a_card_is_refused_then_ok(world):
    project = world("S1")
    path = os.path.join(project, "task_plan.md")
    keep = read(path)
    m = re.search(r"\| A \| ([^|]+) \| 成立 \|", keep)
    write(path, keep.replace(m.group(0), "| A | %s，见[流程说明](.agents/skills/task-planner/SKILL.md) | 成立 |" % m.group(1).strip(), 1))
    out, rc = card.prepare("task_plan", MAIN, {"outside": []})
    assert rc == 1 and "内部文件的链接" in out["refuse"]
    write(path, keep)
    out, rc = card.prepare("task_plan", MAIN, {"outside": []})
    assert rc == 0, out


def test_agents_md_forbids_talking_about_the_instructions():
    a = agents()
    for phrase in ("不提 skill、说明、指引、流程文件、规矩", "不贴 `.agents/`、`agent-tools/`、`toolkit/`、`records/`",
                   "用研究上的理由说", "可以给链接的只有这三样", "全靠你守这一条"):
        assert phrase in a, phrase


def test_scripts_give_say_for_what_the_agent_relays(world, capsys):
    project = world("S2")
    out, _ = card.prepare("decision", MAIN, json.loads(json.dumps(DECISION_FIELDS)))
    res, _ = card.answer(MAIN, out["card_id"], ans(out["card_id"], "从 2022 年开始算"))
    assert res["say"] == "好，就按「从 2022 年开始算」来。"
    projects.main(["projects.py", "status", MAIN])
    st = json.loads(capsys.readouterr().out)
    assert st["say"] == "现在在第 2 步：收集资料，正在收集资料。"
    # 连着三次没准备好:给用户的那句也由脚本给
    for _ in range(3):
        bad, rc = card.prepare("decision", MAIN, {"question": "没有问号", "why": "x", "options": []})
    assert rc == 1 and bad.get("tell_user") and bad["say"].startswith("这张卡我还没准备好")


# ================================================================ M1 · 对话快照停用

def test_answer_without_snapshot_dir_gives_no_visualize(world):
    world("S1")
    card.prepare("task_plan", MAIN, {"outside": []})
    res, _ = card.answer(MAIN, "task-plan-v2", ans("task-plan-v2", "确认，开始收集资料"))
    assert "visualize" not in res and "visualize" not in res["next"]


# ================================================================ M2 · 推荐项放第一

def test_recommended_report_type_comes_first_and_numbers_follow(world, monkeypatch):
    world("S0b")
    monkeypatch.setenv("YUNZHI_CARDS", "text")
    fields = dict(REPORT_FIELDS, recommend="survey")
    out, rc = card.prepare("report_type", MAIN, fields)
    assert rc == 0, out
    assert out["ask"]["questions"][0]["options"][0]["label"] == "梳理情况（综述型） · 推荐"
    assert "1. 梳理情况（综述型） · 推荐：" in out["fallback_text"] and "2. 下判断（研判型）：" in out["fallback_text"]
    res, _ = card.answer(MAIN, "report-type", "1")
    assert res["selected"] == "survey" and res["genre"] == "survey"


def test_recommended_decision_option_comes_first_selected_keeps_field_order(world):
    world("S2")
    fields = json.loads(json.dumps(DECISION_FIELDS))
    fields["recommend"] = 3
    out, rc = card.prepare("decision", MAIN, fields)
    assert rc == 0, out
    labels = [o["label"] for o in out["ask"]["questions"][0]["options"]]
    assert labels == ["改成梳理情况 · 助手建议", "从 2022 年开始算", "用全省数据补 2021 年"]
    res, _ = card.answer(MAIN, out["card_id"], ans(out["card_id"], "1"))       # 卡上第 1 项 = fields 里的第 3 项
    assert res["selected"] == 3 and res["changes_plan"] is True
    out, _ = card.prepare("decision", MAIN, fields)
    res, _ = card.answer(MAIN, out["card_id"], ans(out["card_id"], "2"))
    assert res["selected"] == 1


# ================================================================ M3 · 归档脚本;项目文件夹里不放脚本

def deliver(world):
    project = world("S5")
    out, rc = card.prepare("delivery", MAIN, {})
    assert rc == 0, out
    res, _ = card.answer(MAIN, out["card_id"], ans(out["card_id"], "交付成稿"))
    assert res["result"] == "approved", res
    return project, res


def test_delivery_runs_the_archive_and_check_passes(world):
    project, res = deliver(world)
    assert res["archived"]["ok"] is True and "已经存进项目" in res["say"]
    lib = os.path.join(project, "library")
    import yaml
    src = yaml.safe_load(read(os.path.join(lib, "sources.yaml")))
    gaps = yaml.safe_load(read(os.path.join(lib, "gaps.yaml")))
    assert src["kind"] == "sources_snapshot" and len(src["entries"]) == 26
    assert gaps["kind"] == "gaps_snapshot" and [g["id"] for g in gaps["gaps"]] == ["G1", "G2"]
    assert sorted(os.listdir(os.path.join(lib, "rules"))) == ["R1.md", "R2.md", "R3.md", "R4.md", "R5.md", "README.md"]
    assert "共 5 条" in read(os.path.join(lib, "rules", "README.md"))
    assert "只适用于 H 省平原地区的县" in read(os.path.join(lib, "rules", "R1.md"))
    assert archive.check(project) == []
    assert "归档：来源 26 条、资料缺口 2 个、红线候选 5 条" in read(os.path.join(project, "PROGRESS.md"))


def test_archive_check_fails_when_the_archive_is_incomplete(world):
    project, _ = deliver(world)
    rule = os.path.join(project, "library", "rules", "R3.md")
    os.remove(rule)
    assert any("R3" in p for p in archive.check(project))
    assert archive.run(project)["ok"] is True and archive.check(project) == []
    src = os.path.join(project, "library", "sources.yaml")
    write(src, read(src).replace("- id: R0", "- id: X0", 1))
    assert any("sources.yaml" in p for p in archive.check(project))


def test_archive_refuses_before_delivery(world):
    project = world("S4")
    out = archive.run(project)
    assert out["ok"] is False and "还没交付" in out["problems"][0]
    assert archive.check(project) and not os.path.isfile(os.path.join(project, "library", "sources.yaml"))


def test_scripts_in_the_project_block_cards_then_ok(world):
    project = world("S1")
    stray = os.path.join(project, "records", "read_source.py")
    write(stray, "print('x')\n")
    out, rc = card.prepare("task_plan", MAIN, {"outside": []})
    assert rc == 1 and "records/read_source.py" in out["refuse"] and ".yz-tmp/" in out["refuse"]
    os.remove(stray)
    out, rc = card.prepare("task_plan", MAIN, {"outside": []})
    assert rc == 0, out


def test_docs_point_to_the_archive_script():
    assert "agent-tools/archive.py" in skill("cite-trace") and "--check` 退出码 0" in skill("cite-trace")
    assert "不自己写归档的脚本" in skill("cite-trace")
    assert ".yz-tmp/<项目名>/" in agents() and "不放进项目文件夹" in agents()


# ================================================================ M4 · 取来的资料另放 fetched/

PAGE = "<html><head><style>p{}</style></head><body><h1>2025 年汽车以旧换新通知</h1><p>补贴标准：报废更新每辆最高 2 万元。</p></body></html>"


def test_fetches_save_stores_and_converts_under_fetched(world, tmp_path):
    project = world("S2")
    materials = read(os.path.join(project, "inputs", "materials.md"))
    page = tmp_path / "通知.html"
    page.write_text(PAGE, encoding="utf-8")
    rec, moved = fetches.save(project, str(page), "https://example.gov.cn/notice.html", "2025 年汽车以旧换新通知")
    assert moved is False and rec["state"] == "ok" and rec["stored"] == "fetched/originals/通知.html"
    text = read(os.path.join(project, rec["outputs"][0]))
    assert rec["outputs"][0].startswith("fetched/converted/") and "报废更新每辆最高 2 万元" in text and "p{}" not in text
    assert "https://example.gov.cn/notice.html" in read(os.path.join(project, "fetched", "index.md"))
    assert read(os.path.join(project, "inputs", "materials.md")) == materials        # 用户材料清单不变


def test_files_in_inputs_that_the_user_did_not_give_block_cards(world, tmp_path):
    project = world("S2")
    stray = os.path.join(project, "inputs", "originals", "乘联会2025年12月.html")
    write(stray, PAGE)
    out, rc = card.prepare("decision", MAIN, json.loads(json.dumps(DECISION_FIELDS)))
    assert rc == 1 and "inputs/originals/乘联会2025年12月.html" in out["refuse"] and "fetches.py\" save" in out["refuse"]
    rec, moved = fetches.save(project, stray, "https://example.org/cpca-2025-12.html")   # 放错了地方:挪到 fetched/
    assert moved is True and not os.path.exists(stray) and os.path.isfile(os.path.join(project, rec["stored"]))
    out, rc = card.prepare("decision", MAIN, json.loads(json.dumps(DECISION_FIELDS)))
    assert rc == 0, out


def test_word_lock_files_in_inputs_do_not_block_cards(world):
    project = world("S2")
    lock = os.path.join(project, "inputs", "originals", "~$23县域充电调研（旧稿）.docx")   # 用户在 Word 里开着自己的材料
    write(lock, "lock")
    out, rc = card.prepare("decision", MAIN, json.loads(json.dumps(DECISION_FIELDS)))
    assert rc == 0, out


def test_list_says_how_many_projects(world, capsys):
    world("S1")
    world("S1", "长三角人才引进政策梳理")
    projects.main(["projects.py", "list"])
    assert json.loads(capsys.readouterr().out)["say"] == "你现在有 2 个项目，接着做哪一个？"


def test_evidence_skill_says_where_fetched_files_go():
    t = skill("evidence-card")
    assert "fetches.py\" save" in t and "fetched/" in t and "不放进 `inputs/`" in t and "不自己写转换脚本" in t


# ================================================================ M5 · 只用会等回答的选项框

def test_only_the_blocking_card_tool(world):
    assert "不用 `request_user_input_async`" in agents() and "不用 `request_user_input_async`" in skill("yunzhi-card")
    world("S1")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    assert "不用 request_user_input_async" in out["next"]


# ================================================================ L1 · 卡片回答不记两遍

def test_answer_then_note_user_records_once(world, monkeypatch):
    project = world("S1")
    monkeypatch.setenv("YUNZHI_CARDS", "text")
    card.prepare("task_plan", MAIN, {"outside": []})
    card.answer(MAIN, "task-plan-v2", "2")                        # answer 先记了(带卡号)
    out, _ = card.note_user(MAIN, "2\n")                           # note-user 后跑:不再记
    assert out["recorded"] == 0 and out.get("already")
    assert messages(project).count("2") == 1
    # 下一张卡用户又回「2」:那是另一条消息,照记
    card.prepare("task_plan", MAIN, {"outside": []})
    card.note_user(MAIN, "2")
    card.answer(MAIN, "task-plan-v2", "2")
    assert messages(project).count("2") == 2


def test_native_typed_note_then_note_user_records_once(world):
    project = world("S0b")
    card.prepare("report_type", MAIN, dict(REPORT_FIELDS))
    said = "2，另外把各地进展的差距也写进去"
    card.answer(MAIN, "report-type", ans("report-type", said))
    card.note_user(MAIN, said)
    assert messages(project).count(said) == 1


# ================================================================ L2 · 提纲第一版就出卡

def test_outline_skill_cards_the_first_version():
    t = skill("outline-cocreate")
    body = t.split("\n---\n", 1)[1]                     # 正文(不算开头的 description)
    assert "**第一版写好就出提纲卡**" in body and "就是共创的一轮" in body
    assert "我出一版你批一版" not in t and "没有的话我就请你确认提纲" not in t and "用户说可以了：出提纲卡" not in t


# ================================================================ L3 · 交付卡前的数字由脚本读

def test_delivery_message_carries_counts_read_from_the_files(world):
    project = world("S5")
    out, rc = card.prepare("delivery", MAIN, {})
    assert rc == 0, out
    # 独立数一遍:按底稿和参考文献清单,用生成脚本的同一套算法
    import render_html
    _meta, body = pl.read_md(os.path.join(project, "drafts", MAIN + ".md"))
    entries = pl.load_references(os.path.join(project, "references.yaml"))
    ph_no, numbered = pl.number_entries(body, entries)
    _t, body = pl.take_doc_title(body)
    toc = render_html.parse_blocks(body, ph_no, dict(numbered))[1]
    line = "篇幅：%d 节 · 文内引用 %d 处 · 参考文献 %d 条" % (len(toc), pl.count_inline_citations(body, entries), len(numbered))
    assert line == "篇幅：5 节 · 文内引用 42 处 · 参考文献 26 条"
    assert line in out["message"] and line in out["fallback_text"] and line in out["ask"]["questions"][0]["question"]
    assert "不用自己报数" in skill("cite-trace")


# ================================================================ L4 · 新建项目各用各的暂存文件夹

def test_staged_request_must_not_sit_in_the_shared_inbox(projects_root):
    shared = os.path.join(projects_root, "_inbox")
    assert projects.staged_request_problem(os.path.join(shared, "request.md"))
    assert projects.staged_request_problem(os.path.join(shared, MAIN, "request.md")) is None


def test_new_project_cleans_its_staging_folder(projects_root, capsys):
    stage = os.path.join(projects_root, "_inbox", MAIN)
    os.makedirs(stage)
    write(os.path.join(stage, "request.md"), "想弄清楚县里的充电桩建设。")
    write(os.path.join(stage, "访谈记录.txt"), "乡镇的算，村里的不算。")
    code = projects.main(["projects.py", "new", "--title", MAIN, "--request-file", os.path.join(stage, "request.md"),
                          "--material", os.path.join(stage, "访谈记录.txt")])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and not os.path.exists(stage)
    assert os.path.isfile(os.path.join(projects_root, MAIN, "inputs", "originals", "访谈记录.txt"))
    assert out["say"] == "项目建好了：%s（名字不合适可以随时改）。你给的材料已转成可读的文字。" % MAIN


def test_card_commands_use_the_project_inbox(world):
    world("S1")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    assert out["answer_file"] == "projects/_inbox/%s/answer.txt" % MAIN and out["answer_file"] in out["next"]
    for name in ("yunzhi", "yunzhi-card", "task-planner", "evidence-card"):
        assert not re.search(r"projects/_inbox/(?:request\.md|fields\.json|answer\.txt)", skill(name)), name
