# -*- coding: utf-8 -*-
"""projects.py status / next 与进度页的修复(10-03 两轮复核)。"""
import html
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

import card
import progress
import projects
import yzlib
from conftest import FIXTURES, MAIN, ROOT, STAGES, read, write
from yzlib import pl

TALENT = "长三角人才引进政策梳理"


def page_text(path):
    s = read(path)
    s = re.sub(r"<style.*?</style>", "", s, flags=re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s))


def approve_flow(world):
    project = world("S2-flow")
    card.prepare("flow_change", MAIN, {"changes": "去掉拟定提纲这一步，写作按资料汇编的结构走。"})
    res, _ = card.answer(MAIN, "flow-change-v3",
                         json.dumps({"answers": {"flow-change-v3": {"answers": ["确认，按新流程走"]}}}, ensure_ascii=False))
    assert res["result"] == "approved"
    return project


# ---------------------------------------------------------------- A · 改流程确认之后

def test_status_next_and_page_right_after_flow_change_approval(world):
    project = approve_flow(world)
    info = projects.status_of(project)
    assert info["flow_change_pending"] is False and info["consistent"] is True
    assert "重新确认任务计划" not in info["headline"]
    assert "改流程卡" not in projects.next_for(info)
    text = page_text(progress.write_page(project))
    assert "等你重新确认任务计划" not in text and "对不上" not in text
    assert re.search(r"3 拟定提纲 本项目不需要", text)


def test_real_mismatch_at_a_later_stage_is_no_longer_hidden(world):
    """改流程确认之后,最后一条 revision_log 还是「改流程：」、v 等于 version —— 修之前,往后每一步都被当成
    改流程卡在等:status 叫你再出改流程卡、进度页把真正的对不上藏起来。"""
    project = approve_flow(world)
    path = os.path.join(project, "task_plan.md")
    write(path, re.sub(r"pipeline_status: \S+", "pipeline_status: verifying", read(path)))   # 后面的一步,资料汇编却没确认过
    info = projects.status_of(project)
    assert info["flow_change_pending"] is False and info["consistent"] is False
    nxt = projects.next_for(info)
    assert "对不上" in nxt and "改流程卡" not in nxt
    assert "进度记录和文件对不上" in page_text(progress.write_page(project))


def test_pending_flow_change_with_another_mismatch_says_stop(world):
    project = world("S2-flow")
    d = os.path.join(project, "dossier.md")
    shutil.copyfile(os.path.join(FIXTURES, "S3", "projects", MAIN, "dossier.md"), d)
    text, n = re.subn(r"(?m)^approval:.*$", "approval: {status: approved, approved_hash: abc123def456, "
                      "approved_at: '2026-10-01T10:00:00-04:00', approved_by: 用户, approval_quote: x}", read(d), count=1)
    assert n == 1
    write(d, text)                          # 资料汇编的确认记录对不上它的内容:这是另一处真的对不上
    info = projects.status_of(project)
    assert info["flow_change_pending"] is True and info["other_problems"]
    assert "除此之外还有别的对不上" in projects.next_for(info)
    assert "进度记录和文件对不上" in page_text(progress.write_page(project))


def test_flow_change_plus_another_edit_to_the_plan_is_a_real_mismatch(world):
    """改流程之后又动了任务计划别处:不是一张出得来的改流程卡(出卡会退回「不只改了流程」),是真的对不上。"""
    project = world("S2-flow")
    path = os.path.join(project, "task_plan.md")
    write(path, read(path).replace("资料卡片不超过 30 张", "资料卡片不超过 40 张"))
    info = projects.status_of(project)
    assert info["flow_change_pending"] is False and info["consistent"] is False
    nxt = projects.next_for(info)
    assert "对不上" in nxt and "改流程卡" not in nxt
    out, code = card.prepare("flow_change", MAIN, {"changes": "去掉拟定提纲这一步，写作按资料汇编的结构走。"})
    assert code == 1 and "不只改了流程" in out["refuse"]


# ---------------------------------------------------------------- I · 整张进度页过用词检查

@pytest.mark.parametrize("stage", STAGES)
def test_every_fixture_page_scans_clean(world, stage):
    project = world(stage)
    rep = progress.page_report(project)
    assert "page_wording" not in rep, rep.get("page_wording")


def test_internal_words_from_materials_on_the_page_are_reported(world):
    project = world("S4")
    path = os.path.join(project, "outline.md")
    write(path, read(path).replace("## 二、县域充电设施是怎么变的", "## 二、dossier 里的变化"))
    rep = progress.page_report(project)
    assert "dossier" in rep["page_wording"] and "改那份材料" in rep["page_wording_next"]


# ---------------------------------------------------------------- K · 交付那一步

def test_no_waiting_for_delivery_without_the_delivery_points(world):
    project = world("S5")
    os.remove(os.path.join(project, "library", "delivery_commitments.md"))
    m = progress.model(project)
    d = next(e for e in m["stages"] if e["id"] == "delivery")
    assert d["status"] == "进行中" and "等你确认交付" not in m["headline"]
    assert "三点，助手还没写好" in page_text(progress.write_page(project))


def test_warnings_are_not_counted_as_passed(world, monkeypatch):
    project = world("S5")
    monkeypatch.setattr(progress, "cite_info", lambda p: {"total": 15, "pass": 13, "fail": 0, "warn": 2, "rc": 0})
    text = page_text(progress.write_page(project))
    assert "交付前检查 13 / 15 通过，另有 2 项提醒" in text
    assert card.checks_phrase({"pass": 15, "total": 15, "warn": 0}) == "15 / 15 通过"


def test_missing_out_folder_is_not_called_changed_after_review(world):
    project = world("S5")
    shutil.rmtree(os.path.join(project, "out"))
    text = page_text(progress.write_page(project))
    assert "成稿复核之后又改过" not in text and "成稿文件现在不在" in text


# ---------------------------------------------------------------- 14a · 综述型的进度页

def test_survey_page_uses_neutral_card_wording(world):
    project = world("S6", TALENT)
    path = os.path.join(project, "task_plan.md")
    write(path, re.sub(r"pipeline_status: \S+", "pipeline_status: collecting", read(path)))   # 让收集资料那一步展开
    c = card.stance_counts(project)
    text = page_text(progress.write_page(project))
    assert "背景资料 %d" % (c["total"] - c["gap"]) in text and "资料缺口 %d" % c["gap"] in text
    assert "支持判断" not in text and "不支持" not in text


# ---------------------------------------------------------------- 8 · 新建项目把文件夹建齐;B · 委托原话只从文件读

def test_new_project_creates_standard_folders(projects_root):
    out = projects.new_project("县级财政压力与基层公共服务", "想看看县级财政压力对基层公共服务的影响。", [])
    project = os.path.join(projects_root, out["project"])
    for sub in ("cards", "drafts", "out", "review", "library", "records", os.path.join("inputs", "originals")):
        assert os.path.isdir(os.path.join(project, sub)), sub
    assert projects.status_of(project)["consistent"] is True


def test_new_project_refuses_request_text_on_the_command_line(projects_root):
    p = subprocess.run([sys.executable, "-X", "utf8", "-B", os.path.join(ROOT, "agent-tools", "projects.py"), "new",
                        "--title", "x", "--request", "预算$100万"], capture_output=True, cwd=ROOT,
                       env=dict(os.environ, YUNZHI_PROJECTS=projects_root))
    assert p.returncode == 2 and not os.listdir(projects_root)
