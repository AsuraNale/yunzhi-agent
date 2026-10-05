# -*- coding: utf-8 -*-
"""progress.py:S0–S6 各生成一页,关键数字与 pipeline_status / 文件一致;省掉提纲的流程显示「本项目不需要」;快照是片段。"""
import html
import io
import json
import os
import re

import pytest
import yaml

import progress
import yzlib
from conftest import MAIN, STAGES, read, write
from yzlib import pl


def page_text(path):
    s = read(path)
    s = re.sub(r"<style.*?</style>", "", s, flags=re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s))


def stance_counts_from_files(project):
    counts = {"support": 0, "counter": 0, "mixed": 0, "gap": 0}
    d = os.path.join(project, "cards")
    for fn in sorted(os.listdir(d)):
        meta = yaml.safe_load(read(os.path.join(d, fn)).split("---")[1])
        if not meta.get("deprecated"):
            counts[meta["stance"]] += 1
    return counts


@pytest.mark.parametrize("stage", STAGES)
def test_every_stage_makes_a_full_page_with_the_right_headline(world, stage):
    project = world(stage)
    path = progress.write_page(project)
    raw = read(path)
    assert raw.startswith("<!doctype html>")
    assert '<meta http-equiv="refresh" content="15">' in raw
    assert '<meta charset="utf-8">' in raw
    text = page_text(path)
    assert "None" not in text and "null" not in text        # 取不到就不显示,不写占位
    facts, _items, _ = yzlib.pipeline_facts(project) if os.path.isfile(os.path.join(project, "task_plan.md")) else ({}, [], None)
    state = facts.get("pipeline_status")
    if state is None:
        assert "第 1 步 明确任务" in text
    else:
        stage_id = yzlib.stage_of_state(state)
        assert "第 %d 步 %s" % (list(pl.STAGES).index(stage_id) + 1, yzlib.stage_name(stage_id)) in text
    for sid in pl.STAGES:
        assert yzlib.stage_name(sid) in text


def test_s1_three_things_come_from_task_plan(world):
    project = world("S1")
    text = page_text(progress.write_page(project))
    meta = pl.load_md(os.path.join(project, "task_plan.md"))[0]
    assert "要聊清的三件事 3 / 3" in text
    assert "研判型" in text and meta["scope_brief"] in text and pl.panel_format(meta) in text
    assert "等你确认任务计划" in text


def test_s0b_three_things_come_from_clarify_note(world):
    project = world("S0b")
    text = page_text(progress.write_page(project))
    data = yaml.safe_load(read(os.path.join(project, "clarify.yaml")))
    assert data["scope"] in text and data["format"] in text
    assert "要聊清的三件事 2 / 3" in text


def test_s3_card_numbers_match_the_files(world):
    project = world("S3")
    path = progress.write_page(project)
    text = page_text(path)
    c = stance_counts_from_files(project)
    assert "资料卡片 · 共 %d 张" % sum(c.values()) in text
    for key, word in pl.STANCE_LABELS.items():
        assert "%s %d" % (word, c[key]) in text
    # 构成条给读屏用的那句也得是同一组数(看不见的文字也是给用户的)
    aria = "，".join("%s %d" % (word, c[key]) for key, word in pl.STANCE_LABELS.items())
    assert 'aria-label="%s"' % aria in read(path)
    assert "初步结论：部分成立" in text


def test_s4_outline_numbers_match_the_files(world):
    project = world("S4")
    text = page_text(progress.write_page(project))
    from outline_check import parse_outline_md
    meta, nodes = parse_outline_md(os.path.join(project, "outline.md"))
    assert "合计 %d / 目标 %d 字" % (pl.outline_total_words(nodes), meta["total_words"]) in text
    assert "没有资料卡片支撑的节：%d 个" % len(pl.outline_unsupported(nodes)) in text


def test_s5_review_and_checks_match_the_files(world):
    project = world("S5")
    text = page_text(progress.write_page(project))
    with io.open(os.path.join(project, "review", "result.json"), encoding="utf-8") as f:
        r = json.load(f)
    c = r["counts"]
    assert "独立复核（第 %d 轮）：必须改的 %d · 说法收了 %d · 抽查无误 %d 段" % (
        r["round"], c["must_fix"], c["tone_down"], c["checked_ok"]) in text
    from test_card import cite_counts
    c = cite_counts(project)
    phrase = "交付前检查 %d / %d 通过" % (c["PASS"], c["total"]) + ("，另有 %d 项提醒" % c["WARN"] if c["WARN"] else "")
    assert phrase in text                                    # 提醒不算通过
    assert "第 4 步 撰写交付 · 等你确认交付" in text


def test_flow_without_outline_shows_not_needed(world):
    project = world("S2-flow")
    text = page_text(progress.write_page(project))
    assert re.search(r"3 拟定提纲 本项目不需要", text)
    assert "等你重新确认任务计划" in text
    assert "对不上" not in text          # 改流程卡在等,不是报警


def test_mismatch_is_shown_as_a_warning(world):
    project = world("S3")
    path = os.path.join(project, "task_plan.md")
    write(path, read(path).replace("资料卡片不超过 30 张", "资料卡片不超过 40 张"))   # 确认之后又改了任务计划
    text = page_text(progress.write_page(project))
    assert "进度记录和文件对不上" in text


def test_records_from_card_log_are_marked_as_kept_by_the_assistant(world):
    import card
    project = world("S1")
    card.prepare("task_plan", MAIN, {"outside": []})
    card.answer(MAIN, "task-plan-v2", '{"answers":{"task-plan-v2":{"answers":["确认，开始收集资料"]}}}')
    text = page_text(os.path.join(project, progress.PAGE_NAME))
    assert "确认与决定记录 · 由助手记录" in text
    assert "第 1 次确认 · 任务计划（第 2 版） · 你选了：确认，开始收集资料" in text


def test_snapshot_is_a_fragment_with_a_unique_root_and_a_visualize_line(world, tmp_path):
    project = world("S3")
    out_dir = str(tmp_path / "vis")
    path, line = progress.snapshot(project, out_dir)
    frag = read(path)
    low = frag.lower()
    assert "<!doctype" not in low and "<html" not in low and "<head" not in low and "<body" not in low
    root_id = re.match(r'<div id="([^"]+)">', frag).group(1)
    assert frag.count('id="%s"' % root_id) == 1 and ("#" + root_id) in frag
    assert line.startswith("visualize{") and "\n" not in line
    data = json.loads(line[len("visualize"):])
    assert os.path.normpath(data["path"]) == os.path.normpath(path) and os.path.isabs(data["path"])
    path2, _ = progress.snapshot(project, out_dir)
    assert re.match(r'<div id="([^"]+)">', read(path2)).group(1) != root_id


def test_file_url_keeps_chinese_and_escapes_spaces():
    url = progress.file_url(r"C:\Users\x\云织 Agent\projects\a#b\进度.html")
    assert url == "file:///C:/Users/x/云织%20Agent/projects/a%23b/进度.html"
