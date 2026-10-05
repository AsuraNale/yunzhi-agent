# -*- coding: utf-8 -*-
"""环境:Codex 沙盒里要能跑(10-03 端到端实测的两个卡点)、项目级 Codex 设置、临时文件夹只建在工作区里。"""
import ast
import json
import os
import shutil
import subprocess
import sys

import pytest

import yzlib
from conftest import ROOT

try:
    import tomllib
except ImportError:  # Python 3.10 及以下
    tomllib = None

DISABLED = ["documents@openai-primary-runtime", "pdf@openai-primary-runtime", "spreadsheets@openai-primary-runtime",
            "presentations@openai-primary-runtime", "template-creator@openai-primary-runtime",
            "computer-use@openai-bundled", "unified-computer-use@openai-bundled", "chrome@openai-bundled",
            "browser@openai-bundled", "google-calendar@openai-curated", "slack@openai-curated"]
KEPT = ["codex-app-tools@openai-bundled", "visualize@openai-bundled"]


def agent_tool_files():
    d = os.path.join(ROOT, "agent-tools")
    return [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(".py")]


def test_no_agent_tool_uses_system_temp_dirs():
    """看代码结构(不是全文搜字):没有 import tempfile,也没有调 mkdtemp / TemporaryDirectory / mkstemp。"""
    bad = []
    for path in agent_tool_files():
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                if "tempfile" in names:
                    bad.append((os.path.basename(path), "import tempfile"))
            if isinstance(node, ast.Attribute) and node.attr in ("mkdtemp", "TemporaryDirectory", "mkstemp", "NamedTemporaryFile"):
                bad.append((os.path.basename(path), node.attr))
    assert bad == []


def test_flow_path_toolkit_scripts_do_not_use_system_temp_dirs():
    scripts = ["card_check.py", "cite_check.py", "clarify_check.py", "commitments_check.py", "convert_docx.py",
               "convert_pdf.py", "convert_xlsx.py", "dossier_check.py", "outline_check.py", "pipeline_status.py",
               "pipeline_lib.py", "refs_add.py", "render_docx.py", "render_html.py", "review_result.py", "stamp.py"]
    for name in scripts:
        with open(os.path.join(ROOT, "toolkit", "scripts", name), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert not attrs & {"mkdtemp", "TemporaryDirectory", "mkstemp", "NamedTemporaryFile"}, name


def test_work_tmp_is_inside_the_workspace():
    path = yzlib.work_tmp("test-env")
    try:
        assert os.path.isdir(path) and os.path.dirname(path) == yzlib.TMP_ROOT
        assert yzlib.inside_workspace(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_tmp_root_is_gitignored():
    with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as f:
        lines = [l.strip() for l in f]
    assert "/.yz-tmp/" in lines and "/projects/" in lines and "projects/" not in lines


def test_env_check_runs_and_leaves_nothing_behind():
    before = set(os.listdir(yzlib.TMP_ROOT)) if os.path.isdir(yzlib.TMP_ROOT) else set()
    p = subprocess.run([sys.executable, "-X", "utf8", "-B", os.path.join(ROOT, "agent-tools", "env_check.py")],
                       capture_output=True, cwd=ROOT)
    out = json.loads(p.stdout.decode("utf-8"))
    assert p.returncode == 0 and out["ok"] is True
    assert [c["name"] for c in out["checks"]] == ["在工作区根目录下跑", "Python", "Python 组件", "写文件"]
    assert (set(os.listdir(yzlib.TMP_ROOT)) if os.path.isdir(yzlib.TMP_ROOT) else set()) == before


def test_env_check_reports_a_write_failure_in_plain_words(monkeypatch, capsys):
    import env_check

    def fail(prefix="tmp"):
        raise PermissionError("拒绝访问")
    monkeypatch.setattr(yzlib, "work_tmp", fail)
    assert env_check.main(["env_check.py"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and "沙盒" in out["say"] and "没有安装" not in out["say"]


def test_agents_md_tells_the_real_cause_when_python_cannot_run():
    with open(os.path.join(ROOT, "AGENTS.md"), encoding="utf-8") as f:
        text = f.read()
    sec = text[text.index("## 〇"):text.index("## 一")]
    assert "env_check.py" in sec and "沙盒" in sec and "不许说「没有安装 Python」" in sec


@pytest.mark.skipif(tomllib is None, reason="需要 Python 3.11 的 tomllib")
def test_project_codex_config():
    with open(os.path.join(ROOT, ".codex", "config.toml"), "rb") as f:
        cfg = tomllib.load(f)
    assert cfg["windows"]["sandbox"] == "unelevated"
    assert cfg["features"]["memories"] is False and cfg["features"]["default_mode_request_user_input"] is True
    assert cfg["sandbox_workspace_write"]["network_access"] is True
    plugins = cfg["plugins"]
    assert sorted(k for k, v in plugins.items() if v.get("enabled") is False) == sorted(DISABLED)
    assert not any(k in plugins and plugins[k].get("enabled") is False for k in KEPT)


def test_every_config_entry_has_a_reason_above_it():
    with open(os.path.join(ROOT, ".codex", "config.toml"), encoding="utf-8") as f:
        lines = f.read().split("\n")
    for i, line in enumerate(lines):
        if line.startswith("[plugins.") or line.split("=")[0].strip() in ("sandbox", "network_access", "memories"):
            j = i - 1
            assert lines[j].startswith("#") or lines[j - 1].startswith("#"), line
