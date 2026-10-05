# -*- coding: utf-8 -*-
"""skills:没有 ann_gate_card、没有 --approve(只许 card.py 里出现)、没有 session_facts;命令里的路径都加引号;
给用户的话过简化版用词检查;卡片示例的空过出卡时同样的检查;sync_skills 同步出来逐字节相同。"""
import io
import json
import os
import re
import subprocess
import sys

import pytest
import yaml

import card
import wording_check
from conftest import ROOT, USER_TEXT

SKILLS_DIR = os.path.join(ROOT, ".agents", "skills")
SKILLS = ["yunzhi", "task-planner", "evidence-card", "outline-cocreate", "cite-trace", "review-module",
          "yunzhi-card", "yunzhi-progress"]
BEGIN = "<!-- wording:begin"
END = "<!-- wording:end -->"
FENCE = "`" * 3
PATHISH = re.compile(r"[/\\]|\.(?:md|py|ya?ml|json|txt|html|docx|xlsx|pdf)$")


def text_of(skill):
    with io.open(os.path.join(SKILLS_DIR, skill, "SKILL.md"), encoding="utf-8") as f:
        return f.read()


def frontmatter(text):
    m = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    assert m, "no frontmatter"
    return yaml.safe_load(m.group(1))


def command_lines(text):
    """skill 里的命令:代码块里以 $PY 开头的行,以及行内代码里以 $PY 开头的那段。"""
    out = []
    in_block = False
    for line in text.split("\n"):
        if line.strip().startswith(FENCE):
            in_block = not in_block
            continue
        if in_block and line.strip().startswith("$PY"):
            out.append(line.strip())
    out += [m.group(1) for m in re.finditer(r"`(\$PY [^`]+)`", text)]
    return out


def unquoted_paths(cmd):
    bad = []
    for tok in re.findall(r'"[^"]*"|\S+', cmd):
        if tok.startswith('"'):
            continue
        if tok in ("$PY",) or tok.startswith("-"):
            continue
        if PATHISH.search(tok):
            bad.append(tok)
    return bad


def body_spans(text):
    """生成的「对用户说话」一节之外的「…」(skill 里「」标的是要说给用户、写进用户看的材料的话)。"""
    b, e = text.find(BEGIN), text.find(END)
    body = text[:b] + text[e + len(END):] if b >= 0 and e > b else text
    return [m.group(1) for m in re.finditer(r"「([^「」]*)」", body) if m.group(1)]


def prescribed_lines(text):
    """「这一步常说的几句」里每条的「…」。"""
    b, e = text.find(BEGIN), text.find(END)
    if b < 0:
        return []
    section = text[b:e]
    at = section.find("**这一步常说的几句**")
    if at < 0:
        return []
    bullets = [l for l in section[at:].split("\n") if l.startswith("- ")]
    return [s for l in bullets for s in re.findall(r"「([^「」]*)」", l) if s]


def card_examples(text):
    out, block = [], None
    for line in text.split("\n"):
        if block is None:
            if line.strip() == FENCE + "json":
                block = []
            continue
        if line.strip() == FENCE:
            out.append(json.loads("\n".join(block)))
            block = None
            continue
        block.append(line)
    assert block is None, "a json block is not closed"
    return out


def test_all_skills_exist_with_frontmatter():
    found = sorted(d for d in os.listdir(SKILLS_DIR) if os.path.isfile(os.path.join(SKILLS_DIR, d, "SKILL.md")))
    assert found == sorted(SKILLS)
    for s in SKILLS:
        fm = frontmatter(text_of(s))
        assert fm["name"] == s and isinstance(fm.get("description"), str) and len(fm["description"]) > 20


def test_review_module_runs_in_a_fork_on_workbuddy():
    assert frontmatter(text_of("review-module")).get("context") == "fork"


@pytest.mark.parametrize("skill", SKILLS)
def test_no_client_only_mechanisms(skill):
    t = text_of(skill)
    for banned in ("ann_gate_card", "--approve", "session_facts", "<toolkit>", "<project>", "python -X utf8"):
        assert banned not in t, "%s mentions %s" % (skill, banned)


def test_agents_md_has_no_client_only_mechanisms_and_defines_py():
    with io.open(os.path.join(ROOT, "AGENTS.md"), encoding="utf-8") as f:
        t = f.read()
    for banned in ("ann_gate_card", "--approve", "session_facts"):
        assert banned not in t
    assert "py -X utf8" in t and "python3 -X utf8" in t and "$PY" in t
    assert "toolkit/requirements.txt" in t


def approve_callers(source):
    """源码里真的把 --approve 当参数交给命令的调用(调用的参数里有一个列表 / 元组,里面有 "--approve" 这个元素)。
    第六轮:守门脚本 hook.py 里也有这几个字 —— 那是拦它的规则和理由,不是调用;按结构找,不按全文搜字。"""
    import ast
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            for arg in list(node.args) + [k.value for k in node.keywords]:
                if isinstance(arg, (ast.List, ast.Tuple)) and any(
                        isinstance(e, ast.Constant) and e.value == "--approve" for e in arg.elts):
                    found.append(node.lineno)
    return found


def test_approve_only_inside_card_py():
    tools = os.path.join(ROOT, "agent-tools")
    users = [f for f in sorted(os.listdir(tools)) if f.endswith(".py")
             and approve_callers(io.open(os.path.join(tools, f), encoding="utf-8").read())]
    assert users == ["card.py"]
    # 负控:守门脚本里有这几个字(拦它的规则),但不是调用;反过来,一个真的调用要被认出来
    hook_src = io.open(os.path.join(tools, "hook.py"), encoding="utf-8").read()
    assert "--approve" in hook_src and approve_callers(hook_src) == []
    assert approve_callers('run("stamp.py", ["x.md", "--approve", "--by", "u"])') == [1]


@pytest.mark.parametrize("skill", SKILLS)
def test_command_paths_are_quoted(skill):
    cmds = command_lines(text_of(skill))
    bad = [(c, unquoted_paths(c)) for c in cmds if unquoted_paths(c)]
    assert not bad, bad


def test_commands_were_found():
    assert sum(len(command_lines(text_of(s))) for s in SKILLS) >= 40


def test_user_facing_spans_pass_the_wording_check():
    count, bad = 0, []
    for s in SKILLS:
        for span in body_spans(text_of(s)):
            count += 1
            hits = wording_check.scan(span, USER_TEXT)
            if hits:
                bad.append("%s: 「%s」 → %s" % (s, span, [h["text"] for h in hits]))
    assert count >= 100, "only %d spans — extraction broken?" % count
    assert bad == []


def test_prescribed_lines_pass_the_wording_check():
    count, bad = 0, []
    for s in SKILLS:
        for line in prescribed_lines(text_of(s)):
            count += 1
            hits = wording_check.scan(line, USER_TEXT)
            if hits:
                bad.append("%s: 「%s」 → %s" % (s, line, [h["text"] for h in hits]))
    assert count >= 25, "only %d prescribed lines — extraction broken?" % count
    assert bad == []


FIELD_CAPS = {"report_name": 6, "thesis": 40, "question": 40, "changes": 60}


def test_card_examples_pass_the_same_checks_as_prepare():
    count, bad = 0, []
    for s in SKILLS:
        for ex in card_examples(text_of(s)):
            count += 1
            kind = ex.get("kind")
            assert kind in card.KINDS, "%s: example without a known kind" % s
            fields = []
            for k in ("report_name", "thesis", "why", "question", "changes"):
                if isinstance(ex.get(k), str):
                    fields.append((k, ex[k]))
            for i, o in enumerate(ex.get("options") or []):
                fields += [("options[%d].label" % i, o["label"]), ("options[%d].effect" % i, o["effect"])]
            for k, v in fields:
                cap = FIELD_CAPS.get(k) or (80 if k == "why" else 20 if k.endswith(".label") else 40)
                if len(v) > cap:
                    bad.append("%s %s: %s %d > %d" % (s, kind, k, len(v), cap))
                hits = wording_check.scan(v, USER_TEXT, k)
                if hits:
                    bad.append("%s %s: %s" % (s, kind, wording_check.describe(hits)))
            if kind == "report_type":
                bad += wording_check.quote_problems(ex["why"], USER_TEXT, "why", require=True)
    assert count >= 4
    assert bad == []


# ---- sync_skills

def run_sync(*args):
    p = subprocess.run([sys.executable, "-X", "utf8", "-B", os.path.join(ROOT, "agent-tools", "sync_skills.py")] + list(args),
                       capture_output=True)
    return p.returncode, p.stdout.decode("utf-8", "replace")


def test_sync_skills_makes_byte_identical_copies(tmp_path):
    target = str(tmp_path / "codebuddy" / "skills")
    os.makedirs(os.path.join(target, "stale-skill"))
    with open(os.path.join(target, "stale-skill", "SKILL.md"), "w") as f:
        f.write("old")
    code, out = run_sync("--target", target)
    assert code == 0, out
    assert not os.path.exists(os.path.join(target, "stale-skill"))
    for s in SKILLS:
        a = open(os.path.join(SKILLS_DIR, s, "SKILL.md"), "rb").read()
        b = open(os.path.join(target, s, "SKILL.md"), "rb").read()
        assert a == b
    assert run_sync("--target", target, "--check")[0] == 0
    p = os.path.join(target, "yunzhi", "SKILL.md")
    data = open(p, "rb").read()
    open(p, "wb").write(data + b"\n")
    assert run_sync("--target", target, "--check")[0] == 1


# ---- 10-03 两轮复核:说明和 skills 里必须写明的规矩

def agents_md():
    with io.open(os.path.join(ROOT, "AGENTS.md"), encoding="utf-8") as f:
        return f.read()


def all_docs():
    return {"AGENTS.md": agents_md(), **{s: text_of(s) for s in SKILLS}}


def test_user_messages_go_through_a_file():
    a = agents_md()
    assert 'note-user "<项目名>" --file "projects/<项目名>/records/_inbox.txt"' in a
    assert "不要用 echo" in a
    for name, text in all_docs().items():
        assert not re.search(r'note-user "<项目名>" "<原文>"', text), name


def test_card_skill_opens_the_material_and_brings_the_progress_page_back():
    # 10-03:材料换进右侧、答完换回进度页,都由脚本改写右侧那一页;这份 skill 不再打开右侧
    t = text_of("yunzhi-card")
    assert "view.py --pane" in t and "progress.py --page --pane" in t and "换回进度页" in t and "右侧已打开" in t
    assert "open_in_codex" not in t


def test_card_skill_fallback_reprepares_in_text_mode_and_unclear_closes():
    t = text_of("yunzhi-card")
    assert "--mode text" in t and "这张卡关了" in t


def test_snapshot_is_paused_in_every_command():
    # 第五轮 M1:没有一条命令再带 --snapshot-dir 或 --snapshot(生成快照的脚本还在,流程里不走它)
    found = 0
    for name, text in all_docs().items():
        for cmd in command_lines(text):
            found += 1
            assert "--snapshot" not in cmd, (name, cmd)
    assert found >= 40
    p = text_of("yunzhi-progress")
    assert "这一版停用" in p and "26.930" in p and "右侧" in p


def test_agents_md_rules_from_the_end_to_end_run():
    a = agents_md()
    assert "成稿只用 toolkit 的脚本做" in a and "不用文档插件" in a                     # 插件不接管写稿
    assert "Get-Content -Raw -Encoding utf8" in a                                      # 5.1 读 UTF-8 乱码
    assert "平台自己的文件也算项目以外" in a and "memories" in a                        # 平台文件算项目以外
    assert "从来不写进任务计划" in a                                                    # 工具出错不进材料
    assert "`stamp.py --advance` 只对 `task_plan.md` 跑" in a
    assert "写进那份材料三点" not in a                                                  # 旧规矩(把检查器问题写进三点)删掉了
    assert ".yz-tmp/" in a


def test_tool_failures_never_go_into_points():
    assert "脚本出错、工具报错、命令跑不起来这类事，从来不写进任务计划和三点" in text_of("task-planner")
    assert "三点只写研究本身的事" in text_of("evidence-card")
    assert "三点只写成稿本身的事" in text_of("cite-trace")


def test_advance_only_ever_targets_the_task_plan():
    for name, text in all_docs().items():
        for m in re.finditer(r'stamp\.py"? "([^"]+)" --advance', text):
            assert m.group(1).endswith("task_plan.md"), (name, m.group(0))


def test_fetch_state_ok_only_after_opening_the_page():
    assert "`ok` 只给**打开并读过**的网页或文件" in text_of("evidence-card")
    assert "只在搜索结果里看到的摘要不算取到" in agents_md()


def test_archive_keeps_rules_consistent_with_the_dossier():
    assert "rules/README.md 写的条数和 `rules_candidates` 一致" in text_of("cite-trace")


def test_approximate_word_counts_map_to_that_number():
    t = text_of("task-planner")
    assert "「两千字左右」写 2000" in t and "不写 2200" in t


def test_opening_mentions_the_step_and_the_progress_on_the_right():
    t = text_of("task-planner")
    assert "一共四步，现在是第 1 步明确任务" in t and "右侧能看到进度" in t


def test_card_skill_states_the_voice_and_the_report_type_slot():
    t = text_of("yunzhi-card")
    assert "卡上提到助手一律是「助手」" in t and "不算引用用户" in t
