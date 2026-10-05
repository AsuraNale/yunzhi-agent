# -*- coding: utf-8 -*-
"""第六轮(10-04):守门 hooks + 三处小修。每条拦 / 放行都有正反例;扫回复有负控(引用原话、否定句、成稿链接)。

输入的形状取自秧秧 10-04 用 CLI 0.160.0 + gpt-6.1-sol 实测的记录(scratchpad/hookexp/hook.log、hook2.log):
都有 session_id、turn_id、transcript_path、cwd、hook_event_name、model、permission_mode;UserPromptSubmit 有 prompt;
Pre/PostToolUse 有 tool_name、tool_input、tool_use_id(Post 另有 tool_response);Stop 有 last_assistant_message、stop_hook_active。
"""
import io
import json
import os
import re
import subprocess
import sys
import time

import pytest

import card
import env_check
import hook
import sessions
import wording_check
import yzlib
from conftest import MAIN, ROOT, jsonl, read, write
from yzlib import pl

HOOK = os.path.join(ROOT, "agent-tools", "hook.py")
SID = "01a10898-1a0a-7152-bcbf-c0f95b8fead2"
REPORT_FIELDS = {"report_name": "内参", "thesis": "下乡活动带动了县域公共充电设施增长",
                 "why": "你说「领导就想知道这钱花得值不值」，这需要一个判断。两种写法的区别在于要不要由这份报告来证明它。",
                 "recommend": "judge"}


def base(event, **kw):
    d = {"session_id": SID, "turn_id": "01a10898-turn", "transcript_path": "C:\\Users\\x\\.codex\\sessions\\rollout.jsonl",
         "cwd": ROOT, "hook_event_name": event, "model": "gpt-6.1-sol", "permission_mode": "default"}
    d.update(kw)
    return d


def pre(tool, tool_input, **kw):
    return hook.handle(base("PreToolUse", tool_name=tool, tool_input=tool_input, tool_use_id="call_1", **kw))


def denied(out):
    return bool(out) and out["hookSpecificOutput"]["permissionDecision"] == "deny"


def reason(out):
    return out["hookSpecificOutput"]["permissionDecisionReason"]


def say_prompt(text):
    """用户发了一条消息(守门脚本 UserPromptSubmit 记下)。"""
    assert hook.handle(base("UserPromptSubmit", prompt=text)) is None


def session_records():
    return sessions.read(SID)


def run_hook(payload=None, raw=None):
    """像 Codex 那样跑真的入口:标准输入给 JSON,标准输出只有那一段决定(或什么都没有)。"""
    data = raw if raw is not None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    p = subprocess.run([sys.executable, "-X", "utf8", "-B", HOOK], input=data, capture_output=True, cwd=ROOT)
    out = p.stdout.decode("utf-8").strip()
    return p.returncode, (json.loads(out) if out else None), p.stderr.decode("utf-8", "replace")


@pytest.fixture
def hooks_on(projects_root, monkeypatch):
    """守门脚本在跑:当前会话号设好(Codex 给助手跑的命令都带),会话记录里先有一条用户的话。"""
    monkeypatch.setenv("CODEX_THREAD_ID", SID)
    say_prompt("开始吧")
    time.sleep(0.02)
    return SID


# ================================================================ 配置与入口

def test_hooks_json_runs_the_hook_for_the_four_events():
    with io.open(os.path.join(ROOT, ".codex", "hooks.json"), encoding="utf-8") as f:
        cfg = json.load(f)["hooks"]
    assert sorted(cfg) == ["PostToolUse", "PreToolUse", "Stop", "UserPromptSubmit"]
    for event, groups in cfg.items():
        assert len(groups) == 1 and groups[0]["hooks"] == [{"type": "command", "command": "py -X utf8 agent-tools/hook.py"}]
        if event in ("PreToolUse", "PostToolUse"):
            assert groups[0]["matcher"] == "*"          # 10-04 实测过的写法;工具名在脚本里分
        else:
            assert "matcher" not in groups[0]


def test_hook_constants_agree_with_the_scripts():
    assert hook.CARD_CLOSED == yzlib.CARD_CLOSED
    assert set(hook.AGENT_STATES) <= set(pl.PIPELINE_STATES) and "planning" in hook.PLAN_STATES_OK


def test_hook_imports_only_the_standard_library_up_front():
    """每次工具调用都要跑:模块级不 import PyYAML、工具包、yzlib(只有检查改正本时才用 PyYAML)。"""
    import ast
    tree = ast.parse(read(HOOK))
    top = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            top.add(node.module.split(".")[0])
    assert top <= {"io", "json", "os", "re", "sys", "traceback", "sessions"}, top


def test_hook_never_blocks_on_its_own_errors(projects_root, monkeypatch):
    rc, out, _err = run_hook(raw=b"not json at all")
    assert rc == 0 and out is None
    log = os.path.join(projects_root, ".records", "hook-errors.log")
    assert "JSONDecodeError" in read(log)
    # 规则自己崩了(在进程里):照样放行,退出码 0
    monkeypatch.setattr(hook, "on_pre", lambda d: 1 / 0)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(base("PreToolUse", tool_name="Bash")).encode())))
    assert hook.main([]) == 0
    assert "ZeroDivisionError" in read(log)


def test_user_prompt_is_recorded_verbatim_and_never_blocks(projects_root):
    said = '1，判断标准里要把「透支效应」算进去 $HOME `x` "引号"\n第二行'
    rc, out, _err = run_hook(base("UserPromptSubmit", prompt=said))
    assert rc == 0 and out is None
    recs = session_records()
    assert len(recs) == 1 and recs[0]["prompt"] == said and recs[0]["event"] == "prompt"
    assert recs[0]["session_id"] == SID and recs[0]["turn_id"] == "01a10898-turn" and recs[0]["at"] and recs[0]["ts"]


# ================================================================ PostToolUse:原生卡交回的原文

@pytest.mark.parametrize("resp, want", [
    ('{"answers":{"task-plan-v2":{"answers":["先不确认"]}}}', {"answers": {"task-plan-v2": {"answers": ["先不确认"]}}}),
    ({"answers": {"task-plan-v2": {"answers": ["先不确认"]}}}, {"answers": {"task-plan-v2": {"answers": ["先不确认"]}}}),
    ({"output": '{"answers":{}}'}, {"answers": {}}),
    ([{"type": "text", "text": '{"answers":{"x":{"answers":["2"]}}}'}], {"answers": {"x": {"answers": ["2"]}}}),
])
def test_post_tool_use_records_the_native_answer(projects_root, resp, want):
    ti = {"questions": [{"header": "第 1 次确认", "id": "task-plan-v2", "question": "可以吗？", "options": []}]}
    assert hook.handle(base("PostToolUse", tool_name="request_user_input", tool_input=ti, tool_use_id="c", tool_response=resp)) is None
    rec = session_records()[-1]
    assert rec["event"] == "card_answer" and rec["card_id"] == "task-plan-v2" and rec["response"] == want


def test_post_tool_use_without_a_response_records_missing(projects_root):
    ti = {"questions": [{"id": "task-plan-v2"}]}
    hook.handle(base("PostToolUse", tool_name="request_user_input", tool_input=ti, tool_use_id="c"))
    hook.handle(base("PostToolUse", tool_name="request_user_input", tool_input=ti, tool_use_id="c",
                     tool_response="request_user_input is not supported in exec mode"))
    a, b = session_records()[-2:]
    assert a["response"] is None and a["response_state"] == "缺" and "raw" not in a
    assert b["response"] is None and b["response_state"] == "缺" and "exec mode" in b["raw"]
    # 别的工具的 PostToolUse 什么都不记
    hook.handle(base("PostToolUse", tool_name="Bash", tool_input={"command": "echo x"}, tool_response="x"))
    assert len(session_records()) == 2


# ================================================================ PreToolUse ①:选项卡

def test_native_card_must_match_an_open_card(world, hooks_on):
    project = world("S1")
    out, rc = card.prepare("task_plan", MAIN, {"outside": []})
    assert rc == 0
    ask = out["ask"]
    assert pre("request_user_input", ask) is None
    rec = session_records()[-1]
    assert rec["event"] == "card_asked" and rec["card_id"] == out["card_id"] and rec["project"] == MAIN
    # 按结构比:键的先后、JSON 的空白不算
    shuffled = json.loads(json.dumps({"questions": [dict(reversed(list(ask["questions"][0].items())))]}, indent=3))
    assert pre("request_user_input", shuffled) is None
    assert pre("request_user_input", json.dumps(ask, ensure_ascii=False)) is None         # tool_input 是一段 JSON 文字也认
    # 改了一个选项名 / 改了卡号 / 改了问题 → 拦
    changed = json.loads(json.dumps(ask, ensure_ascii=False))
    changed["questions"][0]["options"][0]["label"] = "确认"
    got = pre("request_user_input", changed)
    assert denied(got) and "原样用 ask" in reason(got)
    renamed = json.loads(json.dumps(ask, ensure_ascii=False))
    renamed["questions"][0]["id"] = "task-plan-v9"
    assert denied(pre("request_user_input", renamed))
    assert denied(pre("request_user_input", {"questions": [{"header": "Test", "id": "t1", "question": "Pick one",
                                                             "options": [{"label": "A", "description": "Select A."}]}]}))
    # 答过的卡:原来的 ask 也不放行了
    card.answer(MAIN, out["card_id"], json.dumps({"answers": {out["card_id"]: {"answers": ["先不确认"]}}}, ensure_ascii=False))
    got = pre("request_user_input", ask)
    assert denied(got) and "重新跑 card.py prepare" in reason(got)
    assert session_records()[-1]["event"] == "denied"


def test_card_from_before_the_hooks_must_be_prepared_again(world, hooks_on):
    project = world("S1")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    log_path = os.path.join(project, "records", "cards.jsonl")
    events = jsonl(log_path)
    events[-1].pop("ask")                          # 第六轮之前出的卡:出卡记录里没有 ask
    write(log_path, "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events))
    got = pre("request_user_input", out["ask"])
    assert denied(got) and "守门脚本装上之前出的" in reason(got)


def test_text_mode_card_cannot_use_the_native_tool(world, hooks_on, monkeypatch):
    world("S1")
    monkeypatch.setenv("YUNZHI_CARDS", "text")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    got = pre("request_user_input", out["ask"])
    assert denied(got) and "fallback_text" in reason(got)


def test_async_input_tool_is_always_refused(world, hooks_on):
    world("S1")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    got = pre("request_user_input_async", out["ask"])
    assert denied(got) and "request_user_input_async" in reason(got)


# ================================================================ PreToolUse ②:起子助手

@pytest.mark.parametrize("ti, ok", [
    ({"task_name": "review", "fork_turns": "none", "message": "gAAAAABqwrXA"}, True),
    ({"task_name": "review", "fork_turns": " none ", "message": "gAAAA"}, True),
    ({"task_name": "review", "fork_turns": "all", "message": "gAAAA"}, False),
    ({"task_name": "review", "message": "gAAAA"}, False),                 # 不写 = 默认 all
    ({"task_name": "review", "fork_turns": 3, "message": "gAAAA"}, False),
    ({"task_name": "review", "fork_turns": "None", "message": "gAAAA"}, False),
])
def test_spawn_needs_fork_turns_none(projects_root, ti, ok):
    got = pre("collaborationspawn_agent", ti)
    assert (got is None) is ok
    if not ok:
        assert '"none"' in reason(got)


# ================================================================ PreToolUse ③:shell

SHELL = [
    # stamp.py:写确认记录的参数、推进到不该推进的状态
    ('py -X utf8 "toolkit/scripts/stamp.py" "projects/x/task_plan.md" --approve --by 用户 --quote "好"', False),
    ('py -X utf8 "toolkit/scripts/stamp.py" "projects/x/task_plan.md" --replace-signed --why x', False),
    ('py -X utf8 -c "import stamp; stamp.main([\'s\', \'x.md\', \'--approve\'])"', False),
    ('py -X utf8 "toolkit/scripts/stamp.py" "projects/x/task_plan.md" --advance collecting', False),
    ('py -X utf8 "toolkit/scripts/stamp.py" "projects/x/task_plan.md" --advance delivered', False),
    ('py -X utf8 "toolkit/scripts/stamp.py" "projects/x/task_plan.md" --advance verifying', True),
    ('py -X utf8 "toolkit/scripts/stamp.py" "projects/x/task_plan.md" --advance gate2_awaiting', True),
    ('py -X utf8 "toolkit/scripts/stamp.py" "projects/x/dossier.md" --invalidate --why "补资料"', True),
    ('py -X utf8 "toolkit/scripts/stamp.py" "projects/x/dossier.md" --check', True),
    # 记录文件:只读放行,写 / 删 / 覆盖拦
    ('Set-Content -Path "projects/x/records/cards.jsonl" -Value "{}"', False),
    ('Add-Content projects\\x\\records\\messages.jsonl "hi"', False),
    ('"hi" >> projects/x/records/messages.jsonl', False),
    ('Remove-Item "projects/.records/sessions/abc.jsonl"', False),
    ('Copy-Item ".yz-tmp/x/a.json" "projects/x/records/delivery.json"', False),
    ('py -X utf8 -c "open(\'projects/x/records/cards.jsonl\',\'a\').write(\'x\')"', False),
    ('Get-Content -Raw -Encoding utf8 "projects/x/records/cards.jsonl"', True),
    ('Get-Content -Raw -Encoding utf8 "projects/x/records/cards.jsonl" 2>&1', True),
    ('py -X utf8 "agent-tools/card.py" note-user "x" --file "projects/x/records/_inbox.txt"', True),
    ('py -X utf8 "agent-tools/card.py" answer "x" --card task-plan-v2 --answer-file "projects/_inbox/x/answer.txt" 2>&1', True),
    # 工具与说明:写进去拦,读、从里面复制出来放行
    ('Set-Content -Path "agent-tools/card.py" -Value ""', False),
    ('"x" > AGENTS.md', False),
    ('Remove-Item -Recurse ".agents/skills/yunzhi"', False),
    ('py -X utf8 -c "open(\'toolkit/scripts/stamp.py\',\'w\').write(\'\')"', False),
    ('Copy-Item "toolkit/templates/themes/base.css" ".yz-tmp/x/base.css"', True),
    ('Remove-Item -Recurse -Force ".yz-tmp/x"', True),
    ('py -X utf8 "agent-tools/projects.py" list | Out-File -Encoding utf8 ".yz-tmp/x/list.txt"', True),
    ('Select-String -Path "toolkit/library/rules/redlines.md" -Pattern "口径" -Encoding utf8', True),
    # 先 cd 再跑脚本
    ('cd projects\\x; py -X utf8 ..\\..\\agent-tools\\card.py mode', False),
    ('Set-Location "C:\\Users\\x"; py -X utf8 "toolkit/scripts/card_check.py" "projects/x/cards"', False),
    ('Push-Location projects; py -X utf8 "../agent-tools/projects.py" list; Pop-Location', False),
    ('py -X utf8 "toolkit/scripts/card_check.py" "projects/x/cards"', True),
    ('cd C:\\Users\\x\\Documents', True),                                   # 不跑脚本的 cd 不管
    # 会话号、云织开关
    ('$env:CODEX_THREAD_ID = "fake"; py -X utf8 "agent-tools/card.py" mode', False),
    ("$env:YUNZHI_CARDS='text'", False),
    ('$env:YUNZHI_PROJECTS = "C:\\x"; py -X utf8 "agent-tools/projects.py" list', False),
    ('echo $env:CODEX_THREAD_ID', True),
]


@pytest.mark.parametrize("cmd, ok", SHELL)
def test_shell_rules(projects_root, cmd, ok):
    got = pre("Bash", {"command": cmd})
    assert (got is None) is ok, (cmd, got and reason(got))


def instructed_commands():
    """skills 与 AGENTS.md 里叫助手跑的每一条 $PY 命令(换成 Windows 的写法,占位符换成一个项目名)。"""
    files = [os.path.join(ROOT, "AGENTS.md")] + [os.path.join(ROOT, ".agents", "skills", d, "SKILL.md")
                                                 for d in sorted(os.listdir(os.path.join(ROOT, ".agents", "skills")))]
    cmds = []
    for path in files:
        for m in re.finditer(r"\$PY [^`\n]+", read(path)):
            cmd = m.group(0).replace("$PY", "py -X utf8").replace("<项目名>", "新能源").replace("<kind>", "task_plan")
            cmds.append((os.path.relpath(path, ROOT), cmd))
    return cmds


def test_every_instructed_command_passes_the_shell_guard(projects_root):
    cmds = instructed_commands()
    assert len(cmds) >= 40, len(cmds)
    assert any("--advance gate2_awaiting" in c for _f, c in cmds) and any("--invalidate" in c for _f, c in cmds)
    bad = [(f, c, reason(got)) for f, c in cmds for got in [pre("Bash", {"command": c})] if got]
    assert bad == []


def test_own_scripts_that_touch_protected_records_are_refused(projects_root, tmp_path):
    evil = tmp_path / "evil.py"
    evil.write_text("open('projects/.records/sessions/x.jsonl', 'a').write('{}')\n", encoding="utf-8")
    forge = tmp_path / "forge.py"
    forge.write_text("meta['approval'] = {'status': 'approved', 'approved_hash': 'x'}\n", encoding="utf-8")
    harmless = tmp_path / "count.py"
    harmless.write_text("print(len(open('projects/x/cards/S01.md', encoding='utf-8').read()))\n", encoding="utf-8")
    got = pre("Bash", {"command": 'py -X utf8 "%s"' % evil})
    assert denied(got) and "evil.py" in reason(got)
    assert denied(pre("Bash", {"command": "python %s --x 1" % forge}))
    assert pre("Bash", {"command": 'py -X utf8 "%s"' % harmless}) is None
    assert pre("Bash", {"command": 'py -X utf8 "agent-tools/card.py" mode'}) is None        # 云织自己的脚本不读
    # 把代码从管道喂给 Python,同 py -c
    assert denied(pre("Bash", {"command": "'open(\"projects/x/records/cards.jsonl\",\"a\").write(\"x\")' | py -"}))
    assert denied(pre("Bash", {"command": "'open(\"projects/.records/sessions/x.jsonl\",\"a\")' | py -X utf8"}))
    assert pre("Bash", {"command": 'Get-Content -Raw -Encoding utf8 "projects/x/cards/S01.md" | py -X utf8 "agent-tools/card.py" mode'}) is None


def test_shell_workdir_other_than_the_root_is_refused_for_scripts(projects_root):
    assert denied(pre("Bash", {"command": 'py -X utf8 "agent-tools/card.py" mode', "workdir": "projects/x"}))
    assert pre("Bash", {"command": 'py -X utf8 "agent-tools/card.py" mode', "workdir": ROOT}) is None
    assert pre("Bash", {"command": ["powershell.exe", "-Command", 'py -X utf8 "agent-tools/card.py" mode']}) is None
    assert denied(pre("Bash", {"command": ["powershell.exe", "-Command", "cd x; py -X utf8 agent-tools/card.py mode"]}))


# ================================================================ PreToolUse ④:apply_patch

def add_patch(path, text):
    return "*** Begin Patch\n*** Add File: %s\n%s\n*** End Patch" % (path, "\n".join("+" + l for l in text.split("\n")))


def replace_patch(path, old_line, new_lines, header=True):
    lines = read(path).split("\n")
    i = lines.index(old_line)
    hunk = (["@@"] if header else []) + [" " + l for l in lines[max(0, i - 2):i]] + ["-" + old_line] + \
        ["+" + l for l in new_lines] + [" " + l for l in lines[i + 1:i + 3]]
    return "*** Begin Patch\n*** Update File: %s\n%s\n*** End Patch" % (path, "\n".join(hunk))


@pytest.mark.parametrize("rel, ok", [
    ("projects/x/records/cards.jsonl", False), ("projects/x/records/messages.jsonl", False),
    ("projects/x/records/delivery.json", False), ("projects/.records/sessions/abc.jsonl", False),
    ("projects/.records/cards.jsonl", False), ("agent-tools/card.py", False), ("toolkit/scripts/stamp.py", False),
    (".agents/skills/yunzhi/SKILL.md", False), (".codebuddy/skills/yunzhi/SKILL.md", False), (".codex/hooks.json", False),
    ("wording/glossary.json", False), ("upstream/toolkit/AGENTS.md", False), ("AGENTS.md", False),
    ("projects/x/cards/S01.md", True), ("projects/x/records/_inbox.txt", True), ("projects/_inbox/x/answer.txt", True),
    ("projects/x/drafts/报告.md", True), ("projects/x/review/findings-1.json", True), ("projects/x/agents.md", True),
])
def test_patch_protected_paths(projects_root, rel, ok):
    got = pre("apply_patch", {"command": add_patch(rel, "x")})
    assert (got is None) is ok, (rel, got and reason(got))
    # 绝对路径、反斜杠也认
    absolute = os.path.join(ROOT, *rel.split("/"))
    assert (pre("apply_patch", {"command": add_patch(absolute.replace("/", "\\"), "x")}) is None) is ok


def test_patch_into_the_projects_folder_by_absolute_path(world, hooks_on):
    project = world("S1")
    target = os.path.join(project, "records", "cards.jsonl")
    assert denied(pre("apply_patch", {"command": add_patch(target, "{}")}))
    move = "*** Begin Patch\n*** Update File: %s\n*** Move to: %s\n@@\n-x\n+y\n*** End Patch" % (
        os.path.join(project, "notes.md"), os.path.join(project, "records", "messages.jsonl"))
    assert denied(pre("apply_patch", {"command": move}))


def test_patch_cannot_write_or_remove_a_confirmation(world, hooks_on):
    project = world("S1")          # 任务计划第 2 版等确认:approval 是空的
    plan = os.path.join(project, "task_plan.md")
    awaiting = "approval: {status: awaiting, approved_at: null, approved_by: null, approval_quote: null, approved_hash: null}"
    forged = ("approval: {status: approved, approved_at: '2026-10-04T10:00:00-04:00', approved_by: 用户, "
              "approval_quote: 好, approved_hash: 2d593129db98}")
    got = pre("apply_patch", {"command": replace_patch(plan, awaiting, [forged])})
    assert denied(got) and "--invalidate" in reason(got)
    # 改正文放行(不动确认记录)
    body_line = next(l for l in read(plan).split("\n") if l.startswith("> "))
    assert pre("apply_patch", {"command": replace_patch(plan, body_line, [body_line + "（补一句）"])}) is None


def test_patch_on_an_approved_plan(world, hooks_on):
    project = world("S3")          # 任务计划确认过(块状写法的 approval)
    plan = os.path.join(project, "task_plan.md")
    text = read(plan)
    status_line = "  status: approved"
    assert status_line in text.split("\n")
    got = pre("apply_patch", {"command": replace_patch(plan, status_line, ["  status: awaiting"])})
    assert denied(got)                                                   # 手改成等确认 = 丢了确认记录
    got = pre("apply_patch", {"command": "*** Begin Patch\n*** Delete File: %s\n*** End Patch" % plan})
    assert denied(got)
    log_line = "revision_log:"
    assert pre("apply_patch", {"command": replace_patch(plan, log_line, [log_line])}) is None   # 不动确认记录的编辑放行
    # 重写 frontmatter 时时刻去掉了引号(YAML 读成日期时间),值没变:不算改了确认记录
    at_line = "  approved_at: '2026-09-30T10:43:00-04:00'"
    assert pre("apply_patch", {"command": replace_patch(plan, at_line, ["  approved_at: 2026-09-30T10:43:00-04:00"])}) is None
    assert denied(pre("apply_patch", {"command": replace_patch(plan, at_line, ["  approved_at: 2026-10-04T10:43:00-04:00"])}))


def test_patch_new_task_plan(world, hooks_on):
    project = world("S0b")         # 还没有任务计划
    plan = os.path.join(project, "task_plan.md")
    good = "---\nkind: task_plan\nversion: 1\npipeline_status: planning\napproval: {status: draft, approved_at: null, " \
           "approved_by: null, approval_quote: null, approved_hash: null}\n---\n\n# 任务计划\n"
    assert pre("apply_patch", {"command": add_patch(plan, good)}) is None
    assert denied(pre("apply_patch", {"command": add_patch(plan, good.replace("status: draft", "status: approved"))}))
    got = pre("apply_patch", {"command": add_patch(plan, good.replace("planning", "collecting"))})
    assert denied(got) and "collecting" in reason(got)


def test_patch_cannot_move_the_plan_past_the_confirmation(world, hooks_on):
    project = world("S1")
    plan = os.path.join(project, "task_plan.md")
    got = pre("apply_patch", {"command": replace_patch(plan, "pipeline_status: gate1_awaiting", ["pipeline_status: collecting"])})
    assert denied(got) and "stamp.py --advance" in reason(got)
    assert pre("apply_patch", {"command": replace_patch(plan, "pipeline_status: gate1_awaiting",
                                                        ["pipeline_status: gate1_awaiting"])}) is None


def test_patch_that_does_not_apply_falls_back_to_the_added_lines(world, hooks_on):
    project = world("S1")
    plan = os.path.join(project, "task_plan.md")
    stray = "*** Begin Patch\n*** Update File: %s\n@@\n-这一行不在文件里\n+  approved_by: 用户\n*** End Patch" % plan
    assert denied(pre("apply_patch", {"command": stray}))
    harmless = "*** Begin Patch\n*** Update File: %s\n@@\n-这一行不在文件里\n+补一句\n*** End Patch" % plan
    assert pre("apply_patch", {"command": harmless}) is None


# ================================================================ Stop:最后一条回复

def stop(msg, active=False):
    return hook.handle(base("Stop", last_assistant_message=msg, stop_hook_active=active))


BLOCK = [
    "按[收集资料流程说明](/C:/Users/x/云织Agent/.agents/skills/evidence-card/SKILL.md)要求，有资料缺口就先问。",
    "我会使用资料卡片技能来整理这些资料。",
    "按选项卡流程，跳过后须停下等用户。",
    "按[选项卡流程](C:/Users/x/云织Agent/.agents/skills/yunzhi-card/SKILL.md)，跳过后须『停下等用户』。",
    "这一步我按工作指引先检查环境。",
    "我先读一下 agent-tools/card.py 再出卡。",
    "记录在 C:\\Users\\x\\云织Agent\\projects\\新能源\\records\\cards.jsonl 里。",
    "这个 skill 会帮我出卡。",
    "资料汇编（dossier）第 1 版写好了。",
    "资料卡片在 [这里](projects/新能源/cards/S01.md)。",
    "流程说明里写着要先确认。",
]
PASS = [
    "任务计划第 2 版写好了，右侧是全文，请你看一下。",
    "你说「我不要那种说明书式的报告」，所以成稿按下判断来写。",       # 用户原话(会话记录里有)
    "这次没有找到职业技能培训的分县数据，不按技能等级拆开说。",          # 否定句、研究里的「技能」
    "成稿已交付：[Word 版](projects/新能源/out/新能源汽车下乡与县域充电设施.docx)、[网页版](<file:///C:/Users/x/云织Agent/projects/新能源/out/报告.html>)。",
    "成稿在 projects/新能源/out/报告.docx，任务计划的网页在 <file:///C:/Users/x/云织Agent/projects/新能源/查看/任务计划.html>。",
    "原文见 https://www.gov.cn/zhengce/附件/某某通知.docx 第 3 页。",
    "这份材料出自《县域商业体系建设工作指引》，口径与统计局一致。",
    "上市公司的招股说明书里披露了充电桩的产能。",
    "你给的材料 projects/新能源/inputs/originals/2023县域充电调研.docx 已经转成文字。",
]


@pytest.mark.parametrize("msg", BLOCK)
def test_stop_sends_internal_talk_back_once(projects_root, msg):
    got = stop(msg)
    assert got and got["decision"] == "block" and "用研究上的话重说这条回复" in got["reason"], msg
    assert session_records()[-1]["event"] == "stop_blocked"
    # 打回过一次(stop_hook_active)就放行,记一笔
    assert stop(msg, active=True) is None
    assert session_records()[-1]["event"] == "stop_passed"


@pytest.mark.parametrize("msg", PASS)
def test_stop_negative_controls(projects_root, msg):
    say_prompt("我不要那种说明书式的报告")
    assert stop(msg) is None, hook.scan_reply(msg, hook.stop_user_texts(SID))


CARD_WORLDS = [("report_type", "S0b", REPORT_FIELDS), ("task_plan", "S1", {"outside": []}), ("dossier", "S3", {"outside": []}),
               ("outline", "S4", {}), ("delivery", "S5", {}), ("flow_change", "S2-flow", {"changes": "去掉拟定提纲这一步，写作按资料汇编的结构走。"}),
               ("decision", "S2", {"question": "2021 年的分县充电桩数据查不到。增速从哪年开始算？",
                                   "why": "查过省统计局和能源局，都没有公布 2021 年的分县数据。不定下来，活动前后的对比就没法算。",
                                   "options": [{"label": "从 2022 年开始算", "effect": "少一年，但全是分县数据。", "changes_plan": False},
                                               {"label": "用全省数据补 2021 年", "effect": "年份完整，但那段只能说全省。", "changes_plan": False}],
                                   "recommend": 1})]


@pytest.mark.parametrize("kind, stage, fields", CARD_WORLDS)
@pytest.mark.parametrize("mode", ["native", "text"])
def test_card_messages_and_says_pass_the_reply_scan(world, monkeypatch, kind, stage, fields, mode):
    """文字卡模式下这一轮最后一条回复就是 fallback_text:守门脚本扫回复时绝不能把卡打回去(助手会改卡上的字)。"""
    project = world(stage)
    if mode == "text":
        monkeypatch.setenv("YUNZHI_CARDS", "text")
    out, rc = card.prepare(kind, MAIN, json.loads(json.dumps(fields)))
    assert rc == 0, out
    users = yzlib.user_texts(project)
    for text in (out["message"], out["fallback_text"]):
        assert hook.scan_reply(text, users) == [], (kind, mode, hook.scan_reply(text, users))
    first = out["ask"]["questions"][0]["options"][0]["label"]
    res, _ = card.answer(MAIN, out["card_id"], first if mode == "text" else json.dumps(
        {"answers": {out["card_id"]: {"answers": [first]}}}, ensure_ascii=False))
    assert hook.scan_reply(res.get("say") or "", users) == [], res.get("say")


def test_stop_through_the_real_entry_point(projects_root):
    rc, out, _ = run_hook(base("Stop", last_assistant_message=BLOCK[0], stop_hook_active=False))
    assert rc == 0 and out["decision"] == "block"
    rc, out, _ = run_hook(base("Stop", last_assistant_message=PASS[0], stop_hook_active=False))
    assert rc == 0 and out is None


# ================================================================ card.py:守门脚本在跑时,回答和引文都要是用户真给的

def test_prepared_event_keeps_the_ask_and_a_fine_timestamp(world):
    project = world("S1")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    ev = jsonl(os.path.join(project, "records", "cards.jsonl"))[-1]
    assert ev["type"] == "prepared" and ev["ask"] == out["ask"] and isinstance(ev["ts"], float)


def test_chat_answer_must_match_a_prompt_after_the_card(world, hooks_on, monkeypatch):
    project = world("S1")
    monkeypatch.setenv("YUNZHI_CARDS", "text")
    say_prompt("1")                                       # 出卡之前说的「1」不算这张卡的回答
    time.sleep(0.02)
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    res, rc = card.answer(MAIN, out["card_id"], "1")
    assert rc == 1 and res["ok"] is False and "不要替他回答" in res["refuse"]
    assert pl.load_md(os.path.join(project, "task_plan.md"))[0]["approval"]["status"] == "awaiting"
    assert jsonl(os.path.join(project, "records", "cards.jsonl"))[-1]["type"] == "answer_refused"
    time.sleep(0.02)
    say_prompt("1")                                       # 用户真的回了
    res, rc = card.answer(MAIN, out["card_id"], "1")
    assert rc == 0 and res["result"] == "approved"
    assert jsonl(os.path.join(project, "records", "cards.jsonl"))[-1]["hook_check"] == "verified"


def test_native_answer_must_match_what_the_card_returned(world, hooks_on):
    project = world("S1")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    cid = out["card_id"]
    assert pre("request_user_input", out["ask"]) is None
    real = '{"answers":{"%s":{"answers":["先不确认"]}}}' % cid
    hook.handle(base("PostToolUse", tool_name="request_user_input", tool_input=out["ask"], tool_use_id="c", tool_response=real))
    forged = '{"answers":{"%s":{"answers":["确认，开始收集资料"]}}}' % cid
    res, rc = card.answer(MAIN, cid, forged)
    assert rc == 1 and "交回的原文" in res["refuse"]
    assert pl.load_md(os.path.join(project, "task_plan.md"))[0]["approval"]["status"] == "awaiting"
    res, rc = card.answer(MAIN, cid, real)
    assert rc == 0 and res["result"] == "not_approved"


def test_native_answer_without_any_record_is_refused(world, hooks_on):
    world("S1")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    res, rc = card.answer(MAIN, out["card_id"], '{"answers":{"%s":{"answers":["确认，开始收集资料"]}}}' % out["card_id"])
    assert rc == 1 and "没有经选项框问过" in res["refuse"]


def test_native_answer_when_the_response_was_not_captured(world, hooks_on):
    project = world("S1")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    cid = out["card_id"]
    assert pre("request_user_input", out["ask"]) is None            # 问过了,但交回的原文没记下(PostToolUse 没来)
    res, rc = card.answer(MAIN, cid, '{"answers":{"%s":{"answers":["确认，开始收集资料"]}}}' % cid)
    assert rc == 0 and res["result"] == "approved"
    assert jsonl(os.path.join(project, "records", "cards.jsonl"))[-1]["hook_check"] == "unverified"


def test_collapsed_native_card_is_verified_too(world, hooks_on):
    world("S1")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    cid = out["card_id"]
    pre("request_user_input", out["ask"])
    hook.handle(base("PostToolUse", tool_name="request_user_input", tool_input=out["ask"], tool_use_id="c",
                     tool_response='{"answers":{}}'))
    res, rc = card.answer(MAIN, cid, '{"answers":{}}')
    assert rc == 0 and res["result"] == "skipped"
    time.sleep(0.02)
    say_prompt("1，时间段再核一遍")                         # 收起之后回的数字:对得上用户原话才算
    res, rc = card.answer(MAIN, cid, "1，时间段再核一遍")
    assert rc == 0 and res["result"] == "approved"


def test_hooks_off_answers_as_before(world, monkeypatch):
    world("S1")
    monkeypatch.setenv("CODEX_THREAD_ID", SID)             # 有会话号,但守门脚本没跑(没有它的记录)
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    res, rc = card.answer(MAIN, out["card_id"], '{"answers":{"%s":{"answers":["先不确认"]}}}' % out["card_id"])
    assert rc == 0 and res["result"] == "not_approved"


def test_quotes_use_the_hook_record_when_hooks_run(world, hooks_on):
    project = world("S0b")
    said = "领导就想知道这钱花得值不值"
    # 这句只在助手记的消息记录里(示例项目自带),守门脚本没记过:守门脚本在跑时不认
    assert any(said in m["text"] for m in jsonl(os.path.join(project, "records", "messages.jsonl")))
    out, rc = card.prepare("report_type", MAIN, dict(REPORT_FIELDS))
    assert rc == 1 and "不是用户的原话" in out["refuse"]
    say_prompt("领导就想知道这钱花得值不值。乡镇的算，村里的不算。Word 就行。")
    out, rc = card.prepare("report_type", MAIN, dict(REPORT_FIELDS))
    assert rc == 0, out


def test_note_typed_on_a_native_card_counts_as_the_users_words(world, hooks_on):
    """原生卡上意见框里写的话不经对话(没有 UserPromptSubmit),守门脚本在卡上交回的原文里记着:引文照样认。"""
    world("S0b")
    note = "领导关心的是这笔钱花得值不值"
    fields = dict(REPORT_FIELDS, why="你说「%s」，这需要一个判断。" % note)
    out, rc = card.prepare("report_type", MAIN, dict(fields))
    assert rc == 1 and "不是用户的原话" in out["refuse"]
    ask = {"questions": [{"header": "需要你决定", "id": "decision-1", "question": "x？", "options": []}]}
    hook.handle(base("PostToolUse", tool_name="request_user_input", tool_input=ask, tool_use_id="c",
                     tool_response={"answers": {"decision-1": {"answers": ["2", "user_note: %s。" % note]}}}))
    out, rc = card.prepare("report_type", MAIN, dict(fields))
    assert rc == 0, out
    assert any(t.startswith(note) for t in hook.stop_user_texts(SID))   # 扫回复时也认


# ================================================================ 小修 1:链接的目标不扫英文词

@pytest.mark.parametrize("text", [
    "[Word 版](projects/新能源/out/新能源汽车下乡与县域充电设施.docx)",
    "[原文](https://www.gov.cn/zhengce/附件/某某通知.docx)",
    "原文：<https://www.gov.cn/zhengce/附件/某某通知.docx>",
])
def test_link_targets_are_not_scanned_for_english_words(text):
    assert wording_check.scan(text) == []


def test_link_rules_still_bite():
    assert [h["rule"] for h in wording_check.scan("[说明](.agents/skills/yunzhi/SKILL.md)")] == ["env:internal-link"]
    assert any(h["rule"] == "env:local-link" for h in wording_check.scan("[成稿](<file:///C:/x/out/报告.docx>)"))
    assert any("docx" in h["text"].lower() for h in wording_check.scan("成稿文件叫报告.docx"))   # 正文里的照扫
    # 守门脚本扫回复时放行的本机链接(link_ok 认的),卡片上照旧报
    ok = wording_check.scan("[成稿](<file:///C:/x/out/报告.docx>)", link_ok=lambda t: "/out/" in t)
    assert ok == []


# ================================================================ 小修 2:rg

def test_env_check_reports_rg(monkeypatch, capsys):
    import shutil
    real = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None if name == "rg" else real(name, *a, **k))
    env_check.main(["env_check.py"])
    out = json.loads(capsys.readouterr().out)
    assert out["rg"]["present"] is False and "Select-String" in out["next"] and "不要试 rg" in out["next"]
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: "C:\\tools\\rg.exe" if name == "rg" else real(name, *a, **k))
    env_check.main(["env_check.py"])
    out = json.loads(capsys.readouterr().out)
    assert out["rg"]["present"] is True and "Select-String" not in out["next"]


def test_env_check_names_missing_parts_by_purpose(monkeypatch, capsys):
    monkeypatch.setattr(env_check, "check_packages", lambda: {"name": "Python 组件", "ok": False,
                                                               "missing": ["python-docx", "pypdf"], "detail": "缺"})
    env_check.main(["env_check.py"])
    out = json.loads(capsys.readouterr().out)
    assert "生成 Word 版" in out["say"] and "python-docx" not in out["say"]
    assert hook.scan_reply(out["say"]) == []


def test_agents_md_rg_and_hooks():
    a = read(os.path.join(ROOT, "AGENTS.md"))
    assert "Select-String" in a and "不要试 rg" in a
    sec = a[a.index("## 九、守门"):]
    for phrase in ("agent-tools/hook.py", "**没信任时它根本不跑：一切照这份说明做，规矩一条不变。**", "**被拦了**",
                   "照理由改了重来", "只看得到每轮最后一条回复", "回答文件一律照原文一字不改写"):
        assert phrase in sec, phrase


# ================================================================ 小修 3:反面证据归成了什么

def dossier_prepare(fields=None):
    return card.prepare("dossier", MAIN, dict(fields or {}, outside=[]))


def set_found(project, found):
    path = os.path.join(project, "dossier.md")
    text = read(path)
    new, n = re.subn(r"found: \[[^\]]*\]", "found: [%s]" % ", ".join(found), text, count=1)
    assert n == 1
    write(path, new)


def test_dossier_card_shows_the_counter_search_counts(world):
    world("S3")
    out, rc = dossier_prepare()
    assert rc == 0, out
    assert "反向检索找到 4 条，归为不支持 4 条" in out["message"]
    assert "反向检索找到 4 条，归为不支持 4 条" in out["ask"]["questions"][0]["question"]


def test_counter_finds_all_filed_as_background_need_a_reason(world):
    project = world("S3")
    mixed = sorted(fn[:-3] for fn in os.listdir(os.path.join(project, "cards"))
                   if re.search(r"^stance: mixed$", read(os.path.join(project, "cards", fn)), re.M))
    set_found(project, mixed)
    out, rc = dossier_prepare()
    assert rc == 1 and "counter_reasons" in out["refuse"] and "削弱核心判断任何一部分的数据都归不支持" in out["refuse"]
    # 第八轮:一句总的理由不够,每张没归为不支持的卡各写一句(卡上逐条显示)
    reasons = {u: "只讲政策出台经过和统计口径，不涉及装桩增速的方向。" for u in mixed}
    out, rc = dossier_prepare({"counter_reasons": reasons})
    assert rc == 0, out
    assert "反向检索找到 3 条，归为不支持 0 条" in out["message"]
    assert out["message"].count("→ 归为背景资料：只讲政策出台经过和统计口径") == 3
    out, rc = dossier_prepare({"counter_reasons": dict(reasons, **{mixed[0]: "x" * 41})})
    assert rc == 1 and "超过上限 40 字" in out["refuse"]


def test_counter_search_must_list_real_cards(world):
    project = world("S3")
    set_found(project, ["S99"])
    out, rc = dossier_prepare()
    assert rc == 1 and "S99" in out["refuse"]
    path = os.path.join(project, "dossier.md")
    write(path, re.sub(r", found: \[[^\]]*\]", "", read(path), count=1))
    out, rc = dossier_prepare()
    assert rc == 1 and "要写 found" in out["refuse"]
    set_found_text = read(path).replace("已做成不支持的卡'}", "已做成不支持的卡', found: []}", 1)
    write(path, set_found_text)
    out, rc = dossier_prepare()
    assert rc == 0 and "反向检索找到 0 条，归为不支持 0 条" in out["message"]


def test_evidence_skill_states_the_counter_rule():
    t = read(os.path.join(ROOT, ".agents", "skills", "evidence-card", "SKILL.md"))
    assert "削弱核心判断任何一部分的数据，都归不支持" in t and "只有和判断无关的" in t
    assert "found: [S11, S12]" in t and "counter_reasons" in t
