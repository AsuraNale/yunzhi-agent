# -*- coding: utf-8 -*-
"""v1.2.1 · 「要聊清的三件事」的来源(规格 §⑨-4)与复核结果 hash 的整串匹配(§⑨-2)。

- 工作笔记 clarify.yaml:格式判据(两个键、各 ≤30 字、可缺)每条都会失败;读不出时应用当它没有;命令行退出码;
- 任务计划的 scope_brief:commitments_check 对任务计划一并核;
- 面板三格的取法:没有任务计划时取笔记,有了就全取任务计划;成稿形式由 words_max 与 formats 拼成(D07);
- 规格 §⑨-4 自己的示例照这些判据是对的;
- _HEX64_RE 整串匹配:64 位十六进制后面多一个换行符不再算合法。

跑法:python -X utf8 -m pytest -q test_v121_clarify.py
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLKIT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import pipeline_lib as L  # noqa: E402

NL = chr(10)
FENCE = "`" * 3
for _k in ("DSH_HOME", "DSH_SESSION_ID", "DSH_SESSION_JSONL"):
    os.environ.pop(_k, None)
PY_ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
SPEC = os.path.join(TOOLKIT, "04-正本规格-v1.md")
D06 = {"scope": "县级，含乡镇，不含村", "format": "Word 版"}
D07_SCOPE = "H 省县级，含乡镇，不含村 · 2021–2025 年"


def run(script, *args):
    r = subprocess.run([sys.executable, "-X", "utf8", os.path.join(HERE, script)] + list(args),
                       capture_output=True, env=PY_ENV, timeout=120)
    return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(text, bytes) else "w"
    with (io.open(path, mode) if mode == "wb" else io.open(path, mode, encoding="utf-8", newline="")) as f:
        f.write(text)


def yaml_text(mapping):
    return "".join("%s: %s%s" % (k, v, NL) for k, v in mapping.items())


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v121-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.note = os.path.join(self.tmp, L.CLARIFY_FILE)


# ═════════════════════════════════════════════════════════ 工作笔记 clarify.yaml
class ClarifyNoteFormat(Tmp):

    def read(self, text):
        write(self.note, text)
        return L.read_clarify(self.note)

    def test_good_notes(self):
        for text, items in [
            (yaml_text(D06), D06),                                                   # D06:两件都答了
            ("scope: 县级，含乡镇，不含村" + NL, {"scope": D06["scope"], "format": None}),   # 只答了一件
            ("scope: null" + NL + "format: Word 版" + NL, {"scope": None, "format": "Word 版"}),
            ("", {"scope": None, "format": None}),                                   # 空文件 = 一件都没答
            (chr(0xFEFF) + yaml_text(D06), D06),                                     # BOM 不算内容
        ]:
            with self.subTest(text=text[:20]):
                data, problem = self.read(text)
                self.assertIsNone(problem)
                self.assertEqual(L.clarify_problems(data), [])
                self.assertEqual(L.clarify_items(data), items)

    def test_the_cap_is_30_code_points_and_inclusive(self):
        data, _ = self.read("scope: " + "范" * 30 + NL)
        self.assertEqual(L.clarify_problems(data), [])
        data, _ = self.read("scope: " + chr(0x20000) * 30 + NL)                     # 四字节的字也算一个
        self.assertEqual(L.clarify_problems(data), [])
        data, _ = self.read("format: " + "式" * 31 + NL)
        self.assertTrue(any("31 字,超过上限 30" in p for p in L.clarify_problems(data)))
        self.assertEqual(L.clarify_items(data)["format"], None)                     # 超了的那格按「还没答」
        self.assertEqual(L.CLARIFY_CAP, 30)

    def test_each_format_rule_can_fail(self):
        bad = {
            "不认识的键": yaml_text(dict(D06, report_type="研判型")),
            "值是数字": "format: 8000" + NL,
            "值是列表": "format: [docx]" + NL,
            "值是空的": "scope: ''" + NL,
            "值只有空白": "scope: '   '" + NL,
            "整个是列表": "- 县级" + NL + "- Word 版" + NL,
        }
        for name, text in bad.items():
            with self.subTest(name):
                data, problem = self.read(text)
                found = [problem] if problem else L.clarify_problems(data)
                self.assertTrue(found and found[0], name)

    def test_unreadable_notes_are_treated_as_absent(self):
        for name, text in {"YAML 写坏": "scope: [县级" + NL, "不是 UTF-8": "scope: 县级".encode("gbk")}.items():
            with self.subTest(name):
                data, problem = self.read(text)
                self.assertIsNone(data)
                self.assertTrue(problem)
                self.assertNotIn("县级", problem)                                   # 说明不引内容
                self.assertEqual(L.clarify_panel(self.tmp)["scope"], None)


class ClarifyCheckCommandLine(Tmp):

    def test_exit_codes_and_json(self):
        write(self.note, yaml_text(D06))
        report = os.path.join(self.tmp, "c.json")
        code, out = run("clarify_check.py", self.note, "--json", report)
        self.assertEqual(code, 0, out)
        self.assertIn("研究范围:县级，含乡镇，不含村", out)
        with io.open(report, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["facts"], D06)
        write(self.note, yaml_text(dict(D06, report_type="研判型")))
        self.assertEqual(run("clarify_check.py", self.note)[0], 1)
        write(self.note, "scope: [县级" + NL)
        code, out = run("clarify_check.py", self.note)
        self.assertEqual(code, 1, out)
        self.assertIn("读不出来", out)
        code, out = run("clarify_check.py", os.path.join(self.tmp, "nope.yaml"))
        self.assertEqual(code, 2, out)


# ═════════════════════════════════════════════════════════ 任务计划里的对应字段
def plan(**front):
    lines = ["---", "kind: task_plan", "version: 1"]
    lines += ["%s: %s" % (k, v) for k, v in front.items()]
    lines += ["---", "# 任务计划", "", "## 要你认的三件事 {#commitments}", "",
              "1. **改动内容**：这是第 1 版。", "2. **最薄弱的依据**：分县数据不全。", "3. **最可能出错的地方**：私人桩与公共桩混在一起。", ""]
    return NL.join(lines)


class TaskPlanFields(Tmp):

    def test_scope_brief_is_checked_with_the_three_points(self):
        path = os.path.join(self.tmp, "task_plan.md")
        write(path, plan(scope_brief=D07_SCOPE))
        self.assertEqual(run("commitments_check.py", path)[0], 0)
        for name, text in {"缺": plan(), "空": plan(scope_brief="''"), "31 字": plan(scope_brief="范" * 31)}.items():
            with self.subTest(name):
                write(path, text)
                code, out = run("commitments_check.py", path)
                self.assertEqual(code, 1, out)
                self.assertIn("研究范围摘要", out)
        write(path, plan(scope_brief="范" * 30))
        self.assertEqual(run("commitments_check.py", path)[0], 0)
        self.assertEqual(L.scope_brief_problems({"kind": "dossier"}), [])         # 只核任务计划

    def test_the_format_cell_is_joined_from_words_and_formats(self):
        cases = [
            ({"words_max": 8000, "formats": ["docx"]}, "约 8000 字 · Word 版"),     # D07
            ({"words_max": 8000, "formats": ["html"]}, "约 8000 字 · 网页版"),
            ({"words_max": 8000, "formats": ["docx", "html"]}, "约 8000 字 · 网页版和 Word 版"),
            ({"formats": ["html", "docx"]}, "网页版和 Word 版"),
            ({"words_max": 8000}, "约 8000 字"),
            ({"words_max": 8000, "formats": ["pdf"]}, "约 8000 字"),
            ({"words_max": True, "formats": ["docx"]}, "Word 版"),
            ({"words_max": 0, "formats": "docx"}, None),
            ({"words_max": "8000"}, None),
            ({}, None),
        ]
        for constraints, want in cases:
            with self.subTest(constraints=constraints):
                self.assertEqual(L.panel_format({"constraints": constraints}), want)
        self.assertIsNone(L.panel_format({"constraints": "8000 字"}))
        self.assertIsNone(L.panel_format(None))


class PanelCells(Tmp):

    def test_before_the_task_plan_the_note_after_it_the_task_plan(self):
        self.assertEqual(L.clarify_panel(self.tmp),                               # D05:什么都还没答
                         {"source": "clarify", "report_type": None, "scope": None, "format": None})
        write(self.note, yaml_text(D06))
        self.assertEqual(L.clarify_panel(self.tmp),                               # D06
                         {"source": "clarify", "report_type": None, "scope": D06["scope"], "format": "Word 版"})
        write(os.path.join(self.tmp, "task_plan.md"),
              plan(genre="argument", scope_brief=D07_SCOPE, constraints="{words_max: 8000, formats: [docx]}"))
        self.assertEqual(L.clarify_panel(self.tmp),                               # D07:三格都从任务计划取
                         {"source": "task_plan", "report_type": "研判型", "scope": D07_SCOPE,
                          "format": "约 8000 字 · Word 版"})

    def test_a_task_plan_never_falls_back_to_the_note(self):
        write(self.note, yaml_text(D06))
        tp = os.path.join(self.tmp, "task_plan.md")
        write(tp, plan(genre="survey"))                                            # v1.2.1 之前写的:没有 scope_brief
        self.assertEqual(L.clarify_panel(self.tmp),
                         {"source": "task_plan", "report_type": "综述型", "scope": None, "format": None})
        write(tp, plan(scope_brief="范" * 31))
        self.assertIsNone(L.clarify_panel(self.tmp)["scope"])
        write(tp, "---" + NL + "kind: [task_plan" + NL + "---" + NL)            # frontmatter 读不出
        self.assertEqual(L.clarify_panel(self.tmp),
                         {"source": "task_plan", "report_type": None, "scope": None, "format": None})


# ═════════════════════════════════════════════════════════ 规格 §⑨-4 的示例
class SpecSection94(unittest.TestCase):

    def test_the_note_example_is_valid_and_gives_the_d06_cells(self):
        with io.open(SPEC, encoding="utf-8") as f:
            text = f.read()
        section = text[text.index("### ⑨-4"):text.index("## 修订记录")]
        start = section.index(FENCE + "yaml" + NL) + len(FENCE + "yaml" + NL)
        block = section[start:section.index(FENCE, start)]
        tmp = tempfile.mkdtemp(prefix="v121-spec-")
        self.addCleanup(shutil.rmtree, tmp, True)
        write(os.path.join(tmp, L.CLARIFY_FILE), block)
        data, problem = L.read_clarify(os.path.join(tmp, L.CLARIFY_FILE))
        self.assertIsNone(problem)
        self.assertEqual(L.clarify_problems(data), [])
        self.assertEqual(L.clarify_items(data), D06)
        # 规格里写的成稿形式例子,照参考实现拼出来就是那一句
        self.assertIn("`约 8000 字 · Word 版`", section)
        self.assertIn("`网页版和 Word 版`", section)
        self.assertIn(D07_SCOPE, section)


# ═════════════════════════════════════════════════════════ §⑨-2 的 hash 整串匹配
class ReviewHashIsTheWholeString(unittest.TestCase):

    def result(self, digest):
        return {"kind": "review_result", "format": 1, "round": 1, "reviewed_at": "2026-10-05T15:20:00-04:00",
                "report": "review/report-1.md", "files": {"out/稿.html": digest},
                "counts": {"must_fix": 0, "tone_down": 0, "citation_or_format": 0, "checked_ok": 0},
                "findings": []}

    def test_only_64_lowercase_hex_and_nothing_after(self):
        self.assertEqual(L.review_result_problems(self.result("a" * 64)), [])
        for digest in ("a" * 64 + NL, "a" * 64 + chr(13) + NL, NL + "a" * 64, "a" * 63, "a" * 65, "A" * 64,
                       "a" * 63 + "g", " " + "a" * 64):
            with self.subTest(digest=repr(digest)):
                self.assertTrue(L.review_result_problems(self.result(digest)))


# ═════════════════════════════════════════════════════════ 首页项目卡那一行(D03)与 report_name
D03_LINE = "研判型 · 内参约 8000 字 · Word 版"
D03_NO_NAME = "综述型 · 约 3000 字 · Word 版"


class ReportNameAndHomeLine(Tmp):

    def plan_file(self, **front):
        write(os.path.join(self.tmp, "task_plan.md"), plan(**front))

    def test_report_name_is_optional_and_capped_at_6(self):
        self.assertEqual(L.report_name_problems({"kind": "task_plan"}), [])                    # 可缺
        self.assertEqual(L.report_name_problems({"kind": "task_plan", "report_name": None}), [])
        for good in ("内参", "研究报告", "六个字的叫法", " 内参 "):
            with self.subTest(good=good):
                self.assertEqual(L.report_name_problems({"kind": "task_plan", "report_name": good}), [])
                self.assertEqual(L.report_name_of({"report_name": good}), good.strip())
        for bad in ("七个字的成稿叫法", "", "  ", 8000, ["内参"]):
            with self.subTest(bad=bad):
                self.assertTrue(L.report_name_problems({"kind": "task_plan", "report_name": bad}))
                self.assertIsNone(L.report_name_of({"report_name": bad}))
        self.assertEqual(L.report_name_problems({"kind": "dossier", "report_name": "七个字的成稿叫法"}), [])
        self.assertEqual(L.REPORT_NAME_CAP, 6)

    def test_commitments_check_reports_a_bad_name_and_accepts_none(self):
        path = os.path.join(self.tmp, "task_plan.md")
        write(path, plan(scope_brief=D07_SCOPE))                                                # 没写:照样有效
        self.assertEqual(run("commitments_check.py", path)[0], 0)
        write(path, plan(scope_brief=D07_SCOPE, report_name="内参"))
        self.assertEqual(run("commitments_check.py", path)[0], 0)
        write(path, plan(scope_brief=D07_SCOPE, report_name="七个字的成稿叫法"))
        code, out = run("commitments_check.py", path)
        self.assertEqual(code, 1, out)
        self.assertIn("成稿叫法", out)

    def test_the_d03_lines(self):
        self.assertEqual(L.home_line(self.tmp), "类型未定")                                     # 还在聊需求
        write(self.note, yaml_text(D06))
        self.assertEqual(L.home_line(self.tmp), "类型未定")                                     # 笔记不上首页
        self.plan_file(genre="argument", report_name="内参", constraints="{words_max: 8000, formats: [docx]}")
        self.assertEqual(L.home_line(self.tmp), D03_LINE)
        self.plan_file(genre="survey", constraints="{words_max: 3000, formats: [docx]}")
        self.assertEqual(L.home_line(self.tmp), D03_NO_NAME)

    def test_the_joining_rules(self):
        cases = [
            ({"genre": "argument", "report_name": "内参", "constraints": "{formats: [docx]}"}, "研判型 · 内参 · Word 版"),
            ({"genre": "argument", "report_name": "内参", "constraints": "{words_max: 8000}"}, "研判型 · 内参约 8000 字"),
            ({"genre": "argument"}, "研判型"),
            ({"genre": "survey", "constraints": "{words_max: 3000, formats: [html, docx]}"}, "综述型 · 约 3000 字 · 网页版和 Word 版"),
            ({"genre": "essay", "report_name": "内参", "constraints": "{words_max: 8000}"}, "类型未定 · 内参约 8000 字"),
            ({"genre": "argument", "report_name": "七个字的成稿叫法", "constraints": "{words_max: 8000}"}, "研判型 · 约 8000 字"),
        ]
        for front, want in cases:
            with self.subTest(front=front):
                self.plan_file(**front)
                self.assertEqual(L.home_line(self.tmp), want)
        write(os.path.join(self.tmp, "task_plan.md"), "---" + NL + "kind: [task_plan" + NL + "---" + NL)
        self.assertEqual(L.home_line(self.tmp), "类型未定")

    def test_report_name_is_a_substantive_field_of_the_content_hash(self):
        """实质字段:写了就进内容 hash;算法本身不变(金标准 hash 由 test_v112_fixes 逐份核)。"""
        meta = {"kind": "task_plan", "version": 1, "genre": "argument"}
        body = "# 任务计划" + NL
        self.assertNotIn("report_name", L.PROCESS_FIELDS)
        self.assertNotEqual(L.content_hash(meta, body), L.content_hash(dict(meta, report_name="内参"), body))
        self.assertNotEqual(L.content_hash(meta, body), L.content_hash(dict(meta, scope_brief=D07_SCOPE), body))
        # 过程字段照旧不进 hash —— 对照
        self.assertEqual(L.content_hash(meta, body), L.content_hash(dict(meta, pipeline_status="planning"), body))

    def test_the_spec_shows_the_same_lines(self):
        with io.open(SPEC, encoding="utf-8") as f:
            text = f.read()
        section = text[text.index("### ⑨-4"):text.index("## 修订记录")]
        for line in (D03_LINE, D03_NO_NAME, "`类型未定`", "`内参约 8000 字`"):
            self.assertIn(line, section)
        self.assertIn("report_name: 内参", text[text.index("## ① task_plan.md"):text.index("## ② cards")])


if __name__ == "__main__":
    unittest.main()
