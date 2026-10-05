# -*- coding: utf-8 -*-
"""照 skills 写的命令行走一遍:新建项目 → 记用户消息 → 工作笔记 → 报告类型卡 → 任务计划 → 第 1 次确认(文字卡模式)。
命令全部用子进程跑(和 agent 在终端里跑的一样),交给脚本的文字都经 projects/_inbox/ 的文件。"""
import io
import json
import os
import re
import subprocess
import sys

from conftest import FIXTURES, MAIN, ROOT, read, write

REQUEST = "想弄清楚这几年的新能源汽车下乡活动，到底有没有带动县里的公共充电桩建设。要一份给厅里的内参，八千字左右，最后要有政策建议。"


def run(env, *args, expect=0):
    cmd = [sys.executable, "-X", "utf8", "-B"] + list(args)
    p = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True)
    out = p.stdout.decode("utf-8", "replace")
    assert p.returncode == expect, (args, p.returncode, out, p.stderr.decode("utf-8", "replace"))
    return out


def run_json(env, *args, expect=0):
    return json.loads(run(env, *args, expect=expect))


def test_new_project_to_first_confirmation_in_text_card_mode(tmp_path):
    root = tmp_path / "projects"
    shared = root / "_inbox"
    inbox = shared / MAIN               # 第五轮 L4:每个项目自己一个暂存文件夹 projects/_inbox/<项目名>/
    inbox.mkdir(parents=True)
    env = dict(os.environ, YUNZHI_PROJECTS=str(root), YUNZHI_CARDS="text", PYTHONIOENCODING="utf-8")

    # 直接放在共用的 projects/_inbox/ 下面的委托原话不收(两个对话同时新建项目会互相覆盖),文件也不动
    write(str(shared / "request.md"), REQUEST)
    bad = run_json(env, "agent-tools/projects.py", "new", "--title", MAIN, "--request-file", str(shared / "request.md"), expect=2)
    assert "projects/_inbox/<项目名>/request.md" in bad["error"] and (shared / "request.md").exists()
    os.remove(str(shared / "request.md"))

    # 新建项目:委托原话经项目自己的暂存文件夹交给脚本,读完就删;暂存文件夹空了也删掉
    write(str(inbox / "request.md"), REQUEST)
    out = run_json(env, "agent-tools/projects.py", "new", "--title", MAIN, "--request-file", str(inbox / "request.md"))
    project = os.path.join(str(root), out["project"])
    assert not (inbox / "request.md").exists() and not inbox.exists()
    assert out["say"].startswith("项目建好了：%s" % MAIN)
    assert read(os.path.join(project, "inputs", "request.md")) == REQUEST
    inbox.mkdir(parents=True)
    assert run_json(env, "agent-tools/projects.py", "status", MAIN)["skill"] == "task-planner"

    # 新建项目时标准文件夹都建好了(生成成稿的脚本不会自己建 out/;取来的资料另放 fetched/)
    for sub in ("cards", "drafts", "out", "review", "library", "records", "fetched/originals", "fetched/converted"):
        assert os.path.isdir(os.path.join(project, sub)), sub
    # 用户第二条消息:原话先写进文件再记(命令行里写原话会被 PowerShell 改掉);研究范围和成稿形式记进工作笔记
    said = "领导就想知道这钱花得值不值。乡镇的算，村里的不算。Word 就行。预算$100万，`先别管`。\n"
    inbox_msg = os.path.join(project, "records", "_inbox.txt")
    write(inbox_msg, said)
    run_json(env, "agent-tools/card.py", "note-user", MAIN, "--file", inbox_msg)
    assert not os.path.exists(inbox_msg)
    msgs = [json.loads(l) for l in io.open(os.path.join(project, "records", "messages.jsonl"), encoding="utf-8")]
    assert msgs[-1]["text"] == said.strip()                 # 逐字,只去掉文件末尾的换行
    run(env, "agent-tools/card.py", "note-user", MAIN, "领导就想知道", expect=2)   # 命令行里的原话不收
    write(os.path.join(project, "clarify.yaml"), "scope: 县级，含乡镇，不含村\nformat: Word 版\n")
    run(env, "toolkit/scripts/clarify_check.py", os.path.join(project, "clarify.yaml"))

    # 报告类型卡(文字卡模式):回 1 = 下判断
    write(str(inbox / "fields.json"), json.dumps({
        "kind": "report_type", "report_name": "内参", "thesis": "下乡活动带动了县域公共充电设施增长",
        "why": "你说「领导就想知道这钱花得值不值」，这需要一个判断。", "recommend": "judge"}, ensure_ascii=False))
    card = run_json(env, "agent-tools/card.py", "prepare", "report_type", MAIN, "--fields-file", str(inbox / "fields.json"))
    assert card["mode"] == "text" and "1. 下判断（研判型） · 推荐：" in card["fallback_text"]
    write(str(inbox / "answer.txt"), "1\n")
    ans = run_json(env, "agent-tools/card.py", "answer", MAIN, "--card", "report-type", "--answer-file", str(inbox / "answer.txt"))
    assert ans["result"] == "selected" and ans["genre"] == "argument"
    assert not (inbox / "answer.txt").exists()

    # 写任务计划(拿示例项目第 2 版的正文当作 agent 写好的样子),自查,推进到等确认
    plan = read(os.path.join(FIXTURES, "S1", "projects", MAIN, "task_plan.md"))
    plan = re.sub(r"pipeline_status: \S+", "pipeline_status: planning", plan)
    plan = re.sub(r"approval:\n(?:  [^\n]*\n)+", "", plan)
    write(os.path.join(project, "task_plan.md"), plan)
    run(env, "toolkit/scripts/commitments_check.py", os.path.join(project, "task_plan.md"))
    run(env, "toolkit/scripts/stamp.py", os.path.join(project, "task_plan.md"), "--advance", "gate1_awaiting")

    # 第 1 次确认:先回一句意见(= 先不确认),再回 1(= 确认)
    card = run_json(env, "agent-tools/card.py", "prepare", "task_plan", MAIN, "--outside", "none")
    assert card["card_id"] == "task-plan-v2" and "这一轮读过的项目以外的文件（助手自报）：0 个" in card["fallback_text"]
    assert "你回复 1 之后，由助手写下确认记录。" in card["fallback_text"] and "你点「确认」后" not in card["fallback_text"]
    # 第四轮:文字卡上不放 file:/// 地址,写看得懂的位置;没带 --pane-open 就不说「右侧已打开」
    assert os.path.isfile(card["view"]["page"]) and "file:///" not in card["fallback_text"]
    assert "（全文在项目文件夹的「查看」文件夹里，网页「任务计划」）" in card["fallback_text"] and "右侧已打开" not in card["fallback_text"]
    write(str(inbox / "answer.txt"), "时间段再看看")
    ans = run_json(env, "agent-tools/card.py", "answer", MAIN, "--card", "task-plan-v2", "--answer-file", str(inbox / "answer.txt"))
    assert ans["result"] == "not_approved"
    run_json(env, "agent-tools/card.py", "prepare", "task_plan", MAIN, "--outside", "none")
    # 文字卡模式下 agent 也会先 note-user 记下这条回复:同一句不记两遍
    write(inbox_msg, "1\n")
    run_json(env, "agent-tools/card.py", "note-user", MAIN, "--file", inbox_msg)
    write(str(inbox / "answer.txt"), "1\n")
    ans = run_json(env, "agent-tools/card.py", "answer", MAIN, "--card", "task-plan-v2", "--answer-file", str(inbox / "answer.txt"))
    assert ans["result"] == "approved" and ans["state"] == "collecting"
    msgs = [json.loads(l)["text"] for l in io.open(os.path.join(project, "records", "messages.jsonl"), encoding="utf-8")]
    assert msgs[-1] == "1" and msgs[-2] != "1" and all(m == m.strip() for m in msgs)   # 同一句只记一次
    assert not any(x == y for x, y in zip(msgs, msgs[1:]))
    assert ans["progress_url"].startswith("file:///") and "换回进度页" in ans["next"]

    st = run_json(env, "agent-tools/projects.py", "status", MAIN)
    assert st["consistent"] is True and st["skill"] == "evidence-card" and st["progress_url"].startswith("file:///")
    status_out = run(env, "toolkit/scripts/pipeline_status.py", project)
    assert "task_plan=approved" in status_out

    # 进度页与快照
    page = run_json(env, "agent-tools/progress.py", MAIN, "--page")
    assert os.path.isfile(page["page"])
    line = run(env, "agent-tools/progress.py", MAIN, "--snapshot", "--out-dir", str(tmp_path / "vis")).strip()
    assert line.startswith("visualize{") and os.path.isfile(json.loads(line[len("visualize"):])["path"])
    log = [json.loads(l) for l in io.open(os.path.join(project, "records", "cards.jsonl"), encoding="utf-8")]
    assert [e["type"] for e in log] == ["prepared", "answered", "prepared", "answered", "prepared", "answered"]
