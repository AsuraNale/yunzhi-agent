# -*- coding: utf-8 -*-
"""projects.py:新建项目(委托原话逐字、材料四态照实写)、列出、看状态、改名、数卡片。"""
import io
import json
import os

import projects
import yzlib
from conftest import FIXTURES, MAIN, jsonl, read, write
from yzlib import pl

REQUEST = "想弄清楚这几年的新能源汽车下乡活动，到底有没有带动县里的公共充电桩建设。\n要一份给厅里的内参，八千字左右，最后要有政策建议。\n"


def test_new_project_keeps_request_and_reports_four_states(projects_root, tmp_path):
    originals = os.path.join(FIXTURES, "S0", "projects", MAIN, "inputs", "originals")
    docx = [os.path.join(originals, f) for f in os.listdir(originals) if f.endswith(".docx")][0]
    xlsx = [os.path.join(originals, f) for f in os.listdir(originals) if f.endswith(".xlsx")][0]
    old = tmp_path / "旧格式.doc"
    old.write_bytes(b"\xd0\xcf\x11\xe0 not converted")
    empty = tmp_path / "空的说明.txt"
    empty.write_text("   \n", encoding="utf-8")
    broken = tmp_path / "坏掉的.docx"
    broken.write_bytes(b"this is not a zip file")
    out = projects.new_project("新能源汽车下乡与县域充电设施", REQUEST,
                               [docx, xlsx, str(old), str(empty), str(broken), str(tmp_path / "不存在.pdf")])
    project = os.path.join(projects_root, out["project"])
    with io.open(os.path.join(project, "inputs", "request.md"), "rb") as f:
        assert f.read() == REQUEST.encode("utf-8")
    states = {m["name"]: m["state"] for m in out["materials"]}
    assert states[os.path.basename(docx)] == "ok" and states[os.path.basename(xlsx)] == "ok"
    assert states["旧格式.doc"] == "unsupported" and states["空的说明.txt"] == "empty"
    assert states["坏掉的.docx"] == "failed" and states["不存在.pdf"] == "failed"
    index = read(os.path.join(project, "inputs", "materials.md"))
    assert "旧格式.doc：这是旧版 Word 格式" in index and "坏掉的.docx：自动转换没有成功，原文件已保存" in index
    assert "不存在.pdf" not in index                       # 找不到的文件没有添加
    assert os.path.isfile(os.path.join(project, "进度.html")) and out["progress_url"].startswith("file:///")
    assert read(os.path.join(projects_root, ".current")).strip() == out["project"]


def test_list_status_and_rename(projects_root):
    out = projects.new_project("县级财政压力与基层公共服务", "想看看县级财政压力对基层公共服务的影响。", [])
    lst = projects.cmd_list()
    assert lst["count"] == 1 and lst["projects"][0]["name"] == out["project"]
    info = projects.status_of(os.path.join(projects_root, out["project"]))
    assert info["state"] is None and info["skill"] == "task-planner" and info["consistent"] is True
    r = projects.rename_project(out["project"], "县级财政与公共服务")
    assert r["changed"] and os.path.isdir(os.path.join(projects_root, "县级财政与公共服务"))
    assert not os.path.exists(os.path.join(projects_root, out["project"]))


def test_status_skill_for_each_stage(world):
    import shutil
    for stage, skill in (("S1", "task-planner"), ("S2", "evidence-card"), ("S3", "evidence-card"),
                         ("S4", "outline-cocreate"), ("S5", "cite-trace"), ("S6", "cite-trace")):
        project = world(stage)
        info = projects.status_of(project)
        assert info["skill"] == skill, stage
        assert info["consistent"] is True, (stage, info["problems"])
        shutil.rmtree(project)


def test_flow_change_pending_is_reported(world):
    project = world("S2-flow")
    info = projects.status_of(project)
    assert info["flow_change_pending"] is True and info["consistent"] is False
    assert "改流程卡还在等用户" in projects.next_for(info)


def test_count_uses_live_cards(world, capsys):
    project = world("S3")
    code = projects.main(["projects.py", "count", MAIN])
    data = json.loads(capsys.readouterr().out)
    assert code == 0 and data["cards"] == pl.count_live_cards(os.path.join(project, "cards"))
