# -*- coding: utf-8 -*-
"""v1.1.0 修的缺陷各一条回归测试(独立核 2026-09-11 的 C-1～C-5),
以及当时查出的测试缺口(T-1、T-2)。

跑法: python -X utf8 -m pytest -q test_v110_fixes.py

⚠️ C-* 这几条必须在修之前的脚本上**是红的** —— 否则说明没测到缺陷。
它们只经命令行调用脚本(不 import 新增的函数),所以可以原样拿到旧脚本上跑,
对照一次红、一次绿。
"""
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


NL = chr(10)
# 外面的 DSH_* 不许漏进子进程(见 test_pipeline_status.py 同名一段的理由)。
for _k in ("DSH_HOME", "DSH_SESSION_ID", "DSH_SESSION_JSONL"):
    os.environ.pop(_k, None)

PY_ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
PROVENANCE = {"session": "session-a",
              "session_picked_by": "DSH_SESSION_ID(harness 注入)",
              "captured_at": "2026-09-04T12:00:00.000Z"}


def run_cite(project):
    r = subprocess.run([sys.executable, "-X", "utf8", os.path.join(HERE, "cite_check.py"),
                        "--project", project, "--skip-stamps"],
                       capture_output=True, env=PY_ENV, timeout=120)
    return (r.stdout + r.stderr).decode("utf-8", "replace")


def run_status(project):
    r = subprocess.run([sys.executable, "-X", "utf8", os.path.join(HERE, "pipeline_status.py"),
                        project], capture_output=True, env=PY_ENV, timeout=120)
    return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")


def line_of(text, item):
    """按**项名**取一行(`预算仪表:` 与 `预算仪表·步数:` 是两项)。"""
    for line in text.split(NL):
        if item + ":" in line:
            return line.strip()
    return "(输出里没有「%s」这一项)" % item


def write_raw(path, frontmatter_lines, body="正文。"):
    """逐字写 frontmatter —— write_md 会替字符串加引号,而这里要的正是不加引号的形态。"""
    with io.open(path, "w", encoding="utf-8", newline=NL) as f:
        f.write("---" + NL + NL.join(frontmatter_lines) + NL + "---" + NL + NL + body + NL)


def minimal_project(tmp, plan_fm, dossier_fm, facts=None):
    """够 cite_check 跑到 ⑧ 与 ⑩ 的最小项目。frontmatter 逐行给。"""
    write_raw(os.path.join(tmp, "task_plan.md"), plan_fm)
    write_raw(os.path.join(tmp, "dossier.md"), dossier_fm)
    with io.open(os.path.join(tmp, "references.yaml"), "w", encoding="utf-8", newline=NL) as f:
        f.write("refs: []" + NL)
    for d in ("drafts", "out", "cards"):
        os.makedirs(os.path.join(tmp, d), exist_ok=True)
    if facts is not None:
        with io.open(os.path.join(tmp, "out", "session_facts.json"), "w",
                     encoding="utf-8", newline=NL) as f:
            f.write(facts if isinstance(facts, str) else json.dumps(facts, ensure_ascii=False))
    return tmp


def dossier_lines(dashboard_lines):
    return ["title: 汇编", "version: 1", "dashboard:"] + ["  " + l for l in dashboard_lines]


PLAN_500 = ["title: 计划", "version: 1", "budget:", "  steps_max: 500"]


# ─────────────────────────────────────────────────────────────── C-1
class C1QuotedLimitsAreNumbers(unittest.TestCase):
    """C-1:budget 侧没做类型归一 —— `steps_max: '10'` 原来 TypeError 崩,⑧ 两行全无。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v110-c1-")

    def test_quoted_limits_are_compared_as_numbers(self):
        facts = dict(PROVENANCE, steps_used=30)
        minimal_project(
            self.tmp,
            ["title: 计划", "version: 1", "budget:", "  steps_max: '10'", "  cards_max: '5'"],
            dossier_lines(["cards_count: 7", "steps_used: 30"]
                          + ["%s: %s" % (k, json.dumps(v, ensure_ascii=False))
                             for k, v in PROVENANCE.items()]),
            facts)
        out = run_cite(self.tmp)
        self.assertNotIn("Traceback", out, out)
        line = line_of(out, "预算仪表")
        self.assertIn("WARN", line, out)
        self.assertIn("超限", line)
        self.assertIn("steps 30>10", line)
        self.assertIn("cards 7>5", line)

    def test_an_unreadable_limit_is_not_within_budget(self):
        minimal_project(
            self.tmp,
            ["title: 计划", "version: 1", "budget:", "  steps_max: 很多"],
            dossier_lines(["cards_count: 0", "steps_used: 30"]))
        out = run_cite(self.tmp)
        self.assertNotIn("Traceback", out, out)
        line = line_of(out, "预算仪表")
        self.assertIn("WARN", line, out)
        self.assertIn("steps_max", line)
        self.assertNotIn("PASS", line)


# ─────────────────────────────────────────────────────────────── C-2
class C2NonMappingDashboardOrBudget(unittest.TestCase):
    """C-2:`dashboard` / `budget` 写成字符串 → 原来 AttributeError 崩。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v110-c2-")

    def test_a_string_dashboard_is_reported_not_raised(self):
        minimal_project(self.tmp, PLAN_500,
                        ["title: 汇编", "version: 1", "dashboard: 74 张卡,212 步"])
        out = run_cite(self.tmp)
        self.assertNotIn("Traceback", out, out)
        step = line_of(out, "预算仪表·步数")
        self.assertIn("WARN", step, out)
        self.assertIn("不是映射", step)
        # ⑩ 也读 dashboard(declared_card_counts):崩在那里同样没有输出。
        self.assertIn("自述统计一致", out)

    def test_a_string_budget_is_reported_not_raised(self):
        minimal_project(self.tmp, ["title: 计划", "version: 1", "budget: 不设上限"],
                        dossier_lines(["cards_count: 0", "steps_used: 5"]))
        out = run_cite(self.tmp)
        self.assertNotIn("Traceback", out, out)
        line = line_of(out, "预算仪表")
        self.assertIn("WARN", line, out)
        self.assertIn("不是映射", line)

    def test_pipeline_status_survives_a_string_dashboard(self):
        """pipeline_status 的面板计数也经 declared_card_counts 读 dashboard。"""
        write_raw(os.path.join(self.tmp, "task_plan.md"),
                  ["title: 计划", "version: 1", "pipeline_status: collecting",
                   "approval:", "  status: awaiting"])
        write_raw(os.path.join(self.tmp, "dossier.md"),
                  ["title: 汇编", "version: 1", "dashboard: 74 张卡"])
        code, out = run_status(self.tmp)
        self.assertNotIn("Traceback", out, out)
        self.assertIn("状态:collecting", out)


# ─────────────────────────────────────────────────────────────── C-3
class C3UnquotedCapturedAt(unittest.TestCase):
    """C-3:dossier 里不加引号的 `captured_at` 被 YAML 读成 datetime ——
    原来报「没记」(字段明明在),而且**绕过了不同源 FAIL**。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v110-c3-")

    def _project(self, captured_in_dossier):
        facts = dict(PROVENANCE, steps_used=255)
        minimal_project(
            self.tmp, PLAN_500,
            dossier_lines(["cards_count: 0", "steps_used: 255",
                           "session: session-a",
                           'session_picked_by: "DSH_SESSION_ID(harness 注入)"',
                           "captured_at: " + captured_in_dossier]),   # ← 不加引号
            facts)

    def test_same_instant_unquoted_passes(self):
        self._project("2026-09-04T12:00:00.000Z")
        line = line_of(run_cite(self.tmp), "预算仪表·步数")
        self.assertIn("PASS", line)

    def test_another_instant_unquoted_is_still_a_fail(self):
        self._project("2026-09-04T12:01:00.000Z")
        line = line_of(run_cite(self.tmp), "预算仪表·步数")
        self.assertIn("FAIL", line)
        self.assertIn("captured_at:", line)


# ─────────────────────────────────────────────────── session_facts 夹具(旧布局)
def zstd_log(path, events):
    """一份单帧 zstd 会话日志(用 node 压,与读的一侧同源)。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    raw = path + ".src.jsonl"
    with io.open(raw, "w", encoding="utf-8", newline="") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + NL)
    script = ("const fs=require('fs'),z=require('zlib');"
              "fs.writeFileSync(process.argv[2], z.zstdCompressSync(fs.readFileSync(process.argv[1])));")
    r = subprocess.run(["node", "-e", script, "--", raw, path], capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    os.remove(raw)


def call(name, args):
    return {"type": "tool/call", "data": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def header(sid, parent=None):
    return {"type": "session", "id": sid, "parentSession": parent,
            "origin": "subagent" if parent else None}


class SessionFactsCase(unittest.TestCase):
    """旧布局 `~/.dsh/sessions/<组>/<会话目录>/session.jsonl.zstd` —— 新旧脚本都认。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v110-sf-")
        self.project = os.path.join(self.tmp, "proj")
        os.makedirs(self.project)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(self.home, ".dsh", "sessions", "tag"))
        self.inside = os.path.join(self.project, "task_plan.md")

    def log(self, sid, events):
        zstd_log(os.path.join(self.home, ".dsh", "sessions", "tag", sid, "session.jsonl.zstd"), events)

    def run_facts(self, json_out=None, extra_env=None, extra_args=None):
        env = dict(os.environ, USERPROFILE=self.home, HOME=self.home)
        env.update(extra_env or {})
        argv = ["node", os.path.join(HERE, "session_facts.mjs"), "--project", self.project]
        if json_out is not None:
            argv += ["--json", json_out]
        argv += list(extra_args or [])
        r = subprocess.run(argv, capture_output=True, env=env, timeout=120)
        return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")


# ─────────────────────────────────────────────────────────────── C-4
class C4JsonWriteFailureIsAnError(SessionFactsCase):
    """C-4:`--json` 写失败原来只打警告、rc 0 —— 调用方以为 json 可用。"""

    def test_an_unwritable_json_path_exits_nonzero_after_printing(self):
        self.log("session-a", [call("read", {"file_path": self.inside}),
                               {"type": "step/start", "data": {}}])
        blocker = os.path.join(self.tmp, "占位文件")
        with io.open(blocker, "w", encoding="utf-8") as f:
            f.write("x")
        code, text = self.run_facts(json_out=os.path.join(blocker, "facts.json"))
        self.assertNotEqual(code, 0, text)
        self.assertIn("会话 session-a", text, "结果应当照样打印出来")

    def test_a_stale_json_that_cannot_be_overwritten_is_not_left_silently(self):
        """同一路径上留着上一轮的 json、这一轮写不进去:rc≠0,且要么删掉、要么明说它是旧的。"""
        self.log("session-a", [call("read", {"file_path": self.inside})])
        stale = os.path.join(self.tmp, "facts.json")
        with io.open(stale, "w", encoding="utf-8") as f:
            f.write(json.dumps({"steps_used": 999, "note": "上一轮"}))
        os.chmod(stale, stat.S_IREAD)
        try:
            code, text = self.run_facts(json_out=stale)
        finally:
            if os.path.exists(stale):
                os.chmod(stale, stat.S_IREAD | stat.S_IWRITE)
        if sys.platform != "win32" and code == 0:
            self.skipTest("本平台上只读文件照样能被覆盖写入,造不出写失败")
        self.assertNotEqual(code, 0, text)
        self.assertTrue(not os.path.exists(stale) or "旧文件" in text, text)


# ─────────────────────────────────────────────────────────────── C-5
class C5InvalidInjectionDoesNotFallBack(SessionFactsCase):
    """C-5:注入的 DSH_SESSION_JSONL / DSH_SESSION_ID 无效时,原来无警告回落到启发式、rc 0。"""

    def setUp(self):
        super().setUp()
        # 一份「启发式本来会选中」的日志:回落就会拿它凑数。
        self.log("session-a", [call("read", {"file_path": self.inside}),
                               {"type": "step/start", "data": {}}])
        self.out = os.path.join(self.tmp, "facts.json")

    def _assert_refused(self, code, text):
        self.assertEqual(code, 3, text)
        self.assertIn("注入的会话变量无效", text)
        self.assertNotIn("会话 session-a", text, "回落到启发式了")
        with io.open(self.out, encoding="utf-8") as f:
            data = json.loads(f.read())
        self.assertIsNone(data["session"])
        self.assertIsNone(data["steps_used"])
        self.assertTrue(data.get("session_pick_error"))

    def test_an_unknown_session_id_is_refused(self):
        code, text = self.run_facts(self.out, {"DSH_SESSION_ID": "session-not-here"})
        self._assert_refused(code, text)

    def test_a_missing_session_file_is_refused(self):
        code, text = self.run_facts(self.out, {"DSH_SESSION_JSONL": os.path.join(self.tmp, "没有.jsonl")})
        self._assert_refused(code, text)

    def test_the_session_flag_does_not_rescue_an_invalid_injection(self):
        code, text = self.run_facts(self.out, {"DSH_SESSION_ID": "session-not-here"},
                                    ["--session", "session-a"])
        self._assert_refused(code, text)


# ─────────────────────────────────────────────────────────────── T-1
class T1DescendantChainsAreMerged(SessionFactsCase):
    """T-1:后代层(孙代理)两种链的归并。

    原来的测试只走根:根的目录名与逻辑 id 不同时能并,但**子代理**的目录名与
    逻辑 id 不同、孙代理又分别用这两种 id 指回它时,没有任何测试。
    子代理 `session-k` 的 header id 是 `logical-k`(与目录名不同)。
    """

    def _tree(self, grandchild_parent):
        far = os.path.join(self.tmp, "桌面", "孙代理读的.md")
        self.log("session-p", [header("session-p"), call("read", {"file_path": self.inside})])
        self.log("session-k", [header("logical-k", parent="session-p"),
                               {"type": "step/start", "data": {}}])
        self.log("session-g", [header("session-g", parent=grandchild_parent),
                               {"type": "step/start", "data": {}},
                               call("read", {"file_path": far})])
        out = os.path.join(self.tmp, "facts.json")
        code, text = self.run_facts(out, {"DSH_SESSION_ID": "session-p"})
        self.assertEqual(code, 0, text)
        with io.open(out, encoding="utf-8") as f:
            return far, json.loads(f.read()), text

    def test_grandchild_pointing_at_the_childs_directory_id(self):
        far, data, text = self._tree("session-k")
        self.assertEqual(set(data["subagent_sessions"]), {"session-k", "session-g"}, text)
        self.assertIn(far, [r["path"] for r in data["outside_reads"]], text)
        self.assertEqual(data["steps_incl_subagents"], 2)

    def test_grandchild_pointing_at_the_childs_logical_id(self):
        far, data, text = self._tree("logical-k")
        self.assertEqual(set(data["subagent_sessions"]), {"session-k", "session-g"}, text)
        self.assertIn(far, [r["path"] for r in data["outside_reads"]], text)
        self.assertEqual(data["steps_incl_subagents"], 2)


# ─────────────────────────────────────────────────────────────── T-2
class T2UnreadableJsonAndBoolVersion(unittest.TestCase):
    """T-2:⑧ 的「读不出」分支、pipeline_status 的 bool 排除,原来都没有测试。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v110-t2-")

    def _unreadable(self, content):
        minimal_project(self.tmp, PLAN_500,
                        dossier_lines(["cards_count: 0", "steps_used: 30"]), facts=content)
        return line_of(run_cite(self.tmp), "预算仪表·步数")

    def test_a_json_that_does_not_parse_is_unreadable_not_absent(self):
        line = self._unreadable("{这不是 json")
        self.assertIn("WARN", line)
        self.assertIn("读不出", line)
        self.assertNotIn("不存在", line)

    def test_a_json_that_is_not_an_object_is_unreadable_not_absent(self):
        line = self._unreadable("[255]")
        self.assertIn("WARN", line)
        self.assertIn("读不出", line)
        self.assertNotIn("不存在", line)

    def test_pipeline_status_does_not_take_a_boolean_for_a_version(self):
        """`version: true` —— isinstance(True, int) 为真,不排除 bool 就不报。"""
        write_raw(os.path.join(self.tmp, "task_plan.md"),
                  ["title: 计划", "version: 1", "pipeline_status: planning",
                   "approval:", "  status: awaiting"])
        write_raw(os.path.join(self.tmp, "dossier.md"),
                  ["title: 汇编", "version: true", "approval:", "  status: draft"])
        code, out = run_status(self.tmp)
        self.assertNotIn("Traceback", out, out)
        self.assertIn("dossier.md: version 不是整数(现为 True)", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
