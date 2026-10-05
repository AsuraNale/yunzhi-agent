# -*- coding: utf-8 -*-
"""v1.1.1 · 第一批修补单(2026-09-29)工具包 Tb1–Tb3 的回归测试。
Tb4(签名载荷样例与文档声称的覆盖面一致)在 test_stages_signoff.py 的
SignoffPayload 里,挨着它核的那份样例。

跑法: python -X utf8 -m pytest -q test_v111_fixes.py

⚠️ 同 test_v110_fixes.py:标了「回归」的每条必须在修之前的脚本上**是红的** ——
否则说明没测到缺陷。它们都只用旧脚本里本来就有的入口(命令行;Tb2 另有两条直接调
`pipeline_status.check` 与 `Report.to_json`),可以原样拿到旧脚本上跑,对照一次红、
一次绿(2026-09-29 对 88cb321 的脚本跑过:回归条全红)。标了「控制组」的在新旧脚本上
都该是绿的:它们钉住「修的时候没把正常的路也堵了」。
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from pipeline_lib import read_md, write_md  # noqa: E402  只用来造样例、读回

NL = chr(10)
# 外面的 DSH_* 不许漏进子进程(见 test_pipeline_status.py 同名一段的理由)。
for _k in ("DSH_HOME", "DSH_SESSION_ID", "DSH_SESSION_JSONL"):
    os.environ.pop(_k, None)
PY_ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")

SIG1 = "v1.ed25519.0123456789abcdef.T0xELVNJR05BVFVSRS1PTkU"
SIG2 = "v1.ed25519.0123456789abcdef.TkVXLVNJR05BVFVSRS1UV08"
AT = "2026-09-29T14:03:11-04:00"


def run(script, *args):
    r = subprocess.run([sys.executable, "-X", "utf8", os.path.join(HERE, script)] + list(args),
                       capture_output=True, env=PY_ENV, timeout=120)
    return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")


def raw(path):
    with io.open(path, "rb") as f:
        return f.read()


def new_doc(path, kind, pipeline_status=None):
    """一份刚写好、等确认的正本(与 stamp.py 写出来的形态相同)。"""
    meta = {"kind": kind, "title": kind, "version": 1}
    if pipeline_status:
        meta["pipeline_status"] = pipeline_status
    meta["approval"] = {"status": "awaiting", "approved_at": None, "approved_by": None,
                        "approval_quote": None, "approved_hash": None}
    meta["revision_log"] = []
    write_md(path, meta, NL + "# " + kind + NL + NL + "正文若干。" + NL)
    return path


def new_plan(tmp, pipeline_status="gate1_awaiting"):
    return new_doc(os.path.join(tmp, "task_plan.md"), "task_plan", pipeline_status)


def write_progress(tmp):
    with io.open(os.path.join(tmp, "PROGRESS.md"), "w", encoding="utf-8", newline=NL) as f:
        f.write("进行中" + NL)


def edit_text(path, pattern, replacement):
    """按行改原文(write_md 会替字符串加引号,而这里要的正是手改出来的形态)。"""
    with io.open(path, encoding="utf-8") as f:
        text = f.read()
    new, n = re.subn(pattern, replacement, text, flags=re.M)
    assert n == 1, "样例里没找到要改的那一行:%s" % pattern
    with io.open(path, "w", encoding="utf-8", newline=NL) as f:
        f.write(new)


# ═════════════════════════════════════════════════════════════════ Tb1
class Tb1SignedRecordIsNotOverwrittenSilently(unittest.TestCase):
    """Tb1:已签名的确认记录上,不带签名再跑一次 `stamp.py --approve`,原来签名被
    静默覆盖、没有任何痕迹(T 验收·中)。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v111-tb1-")
        self.addCleanup(shutil.rmtree, self.tmp, True)   # 积压清理复核:原来每跑一次留下一批临时目录
        self.plan = new_plan(self.tmp)

    def sign(self, signature, quote="按这个版本走"):
        h = run("stamp.py", self.plan, "--hash")[1].strip()
        return run("stamp.py", self.plan, "--approve", "--by", "研究员", "--quote", quote,
                   "--at", AT, "--expect-hash", h, "--signature", signature)

    def approval(self):
        return read_md(self.plan)[0]["approval"]

    def unsigned(self, *extra):
        return run("stamp.py", self.plan, "--approve", "--by", "agent", "--quote", "好", *extra)

    # ── 回归
    def test_unsigned_approve_over_a_signed_record_is_refused(self):
        code, out = self.sign(SIG1)
        self.assertEqual(code, 0, out)
        before = raw(self.plan)
        h = run("stamp.py", self.plan, "--hash")[1].strip()
        for extra in ([], ["--at", AT], ["--at", AT, "--expect-hash", h]):
            with self.subTest(extra=extra):
                code, out = self.unsigned(*extra)
                self.assertEqual(code, 2, out)
                self.assertEqual(raw(self.plan), before, "拒绝落章却动了盘上的字节")
                self.assertIn("签名", out)
        self.assertEqual(self.approval()["signature"], SIG1)

    def test_a_signature_counts_whatever_the_status_says(self):
        """签名在,就是带签名的记录 —— 手改成 status: awaiting 也一样拦。"""
        meta, body = read_md(self.plan)
        meta["approval"] = dict(meta["approval"], signature=SIG1)
        write_md(self.plan, meta, body)
        before = raw(self.plan)
        code, out = self.unsigned()
        self.assertEqual(code, 2, out)
        self.assertEqual(raw(self.plan), before)

    def test_the_explicit_flag_replaces_it_and_keeps_the_old_record(self):
        self.assertEqual(self.sign(SIG1)[0], 0)
        signed = dict(self.approval())
        why = "应用打不开,先人工落章"
        code, out = self.unsigned("--replace-signed", "--why", why)
        self.assertEqual(code, 0, out)
        meta = read_md(self.plan)[0]
        self.assertEqual(meta["approval"]["status"], "approved")
        self.assertNotIn("signature", meta["approval"])
        entry = meta["revision_log"][-1]
        self.assertEqual(entry["prev_approval"], signed, "旧记录要整条、逐字进 revision_log")
        self.assertEqual(entry["why"], why)
        # 这条 revision_log 不许把下一次落章绊倒(版本守卫:max(v) > version 就拒)。
        self.assertEqual(run("stamp.py", self.plan, "--check")[0], 0)
        code, out = self.sign(SIG2)
        self.assertEqual(code, 0, out)

    def test_the_flag_needs_a_reason(self):
        self.assertEqual(self.sign(SIG1)[0], 0)
        before = raw(self.plan)
        for extra in (["--replace-signed"], ["--replace-signed", "--why", ""]):
            with self.subTest(extra=extra):
                code, out = self.unsigned(*extra)
                self.assertEqual(code, 2, out)
                self.assertEqual(raw(self.plan), before)

    def test_a_signed_reconfirmation_does_not_drop_the_old_record_either(self):
        """应用重新确认(新记录带签名)照常写 —— 但旧签名同样不许无痕消失。"""
        self.assertEqual(self.sign(SIG1)[0], 0)
        old = dict(self.approval())
        code, out = self.sign(SIG2, quote="再确认一次")
        self.assertEqual(code, 0, out)
        meta = read_md(self.plan)[0]
        self.assertEqual(meta["approval"]["signature"], SIG2)
        self.assertEqual(meta["revision_log"][-1]["prev_approval"], old)
        self.assertEqual(meta["revision_log"][-1]["prev_approval"]["signature"], SIG1)

    # ── 控制组
    def test_control_unsigned_records_are_replaced_as_before(self):
        """旧记录没有签名时,--approve 与 v1.1.0 相同:照写,不加 revision_log。"""
        for _ in range(2):
            code, out = self.unsigned()
            self.assertEqual(code, 0, out)
        self.assertEqual(read_md(self.plan)[0]["revision_log"], [])

    def test_control_invalidate_then_approve_is_the_normal_path(self):
        """作废(旧记录挪进 revision_log)之后,不带签名的 --approve 照常可写。"""
        self.assertEqual(self.sign(SIG1)[0], 0)
        code, out = run("stamp.py", self.plan, "--invalidate", "--why", "补一条口径")
        self.assertEqual(code, 0, out)
        meta, body = read_md(self.plan)
        meta["version"] = 2               # 作废那条 revision_log 写的是 v2:先升版再落章
        write_md(self.plan, meta, body)
        code, out = self.unsigned()
        self.assertEqual(code, 0, out)


# ═════════════════════════════════════════════════════════════════ Tb2
class Tb2PipelineStatusSurvivesDatesAndScalarApproval(unittest.TestCase):
    """Tb2:任务计划里 `approval.approved_at` 不加引号(YAML 读成日期)时
    pipeline_status 崩溃;`approval: approved` 写成标量也崩(C 验收·中、T 验收·低)。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v111-tb2-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.plan = new_plan(self.tmp)
        write_progress(self.tmp)
        code, out = run("stamp.py", self.plan, "--approve", "--by", "研究员", "--quote", "好",
                        "--at", AT)
        self.assertEqual(code, 0, out)
        code, out = run("stamp.py", self.plan, "--advance", "gate1_approved")
        self.assertEqual(code, 0, out)

    def status(self):
        """→ (退出码, 人读输出, --json 的内容)。不许崩,--json 必须写出来。"""
        out_json = os.path.join(self.tmp, "status.json")
        if os.path.exists(out_json):
            os.remove(out_json)
        code, out = run("pipeline_status.py", self.tmp, "--json", out_json)
        self.assertNotIn("Traceback", out, out)
        self.assertTrue(os.path.exists(out_json), "--json 没写出来:" + out)
        with io.open(out_json, encoding="utf-8") as f:
            text = f.read()
        self.assertTrue(text.strip(), "--json 是空文件:" + out)
        return code, out, json.loads(text)

    # ── 回归
    def test_an_unquoted_approved_at_is_read_as_the_string_it_spells(self):
        for written, expected in (("2026-09-29T14:03:11-04:00", "2026-09-29T14:03:11-04:00"),
                                  ("2026-09-29 14:03:11", "2026-09-29T14:03:11"),
                                  ("2026-09-29", "2026-09-29")):
            with self.subTest(written=written):
                edit_text(self.plan, r"^  approved_at: .*$", "  approved_at: " + written)
                code, out, data = self.status()
                self.assertEqual(code, 0, out)
                stamp = data["facts"]["stamps"]["task_plan.md"]
                self.assertEqual(stamp["status"], "approved")
                self.assertEqual(stamp["approved_at"], expected)
                self.assertIsInstance(stamp["approved_at"], str)

    def test_a_date_anywhere_in_the_facts_does_not_stop_the_json(self):
        """印章块里别的键写成日期(approved_hash: 2026-09-29),--json 照样写出。"""
        edit_text(self.plan, r"^  approved_hash: .*$", "  approved_hash: 2026-09-29")
        code, out, data = self.status()
        self.assertEqual(code, 1, out)            # hash 对不上 → stale → FAIL,这是对的
        self.assertEqual(data["facts"]["stamps"]["task_plan.md"]["approved_hash"], "2026-09-29")

    def test_the_facts_hold_text_at_the_source(self):
        """facts 在源头就归一(docs/stages.md:`stamps[…]` 的 approved_at / approved_hash
        不会是日期),不只靠下面那条序列化的兜底。"""
        import pipeline_status
        from pipeline_lib import Report
        edit_text(self.plan, r"^  approved_at: .*$", "  approved_at: " + AT)
        edit_text(self.plan, r"^  approved_hash: .*$", "  approved_hash: 2026-09-29")
        rep = Report("pipeline_status")
        pipeline_status.check(self.tmp, rep)
        stamp = rep.facts["stamps"]["task_plan.md"]
        self.assertEqual((stamp["approved_at"], stamp["approved_hash"]), (AT, "2026-09-29"))

    def test_report_json_writes_dates_as_text(self):
        """`Report.to_json` 本身对日期也崩(app-core 验收时记下的同一缺陷的第二处)。
        pipeline_status 已在 facts 里先归一;这一条钉住序列化这一层,免得以后哪个
        新 facts 字段直接放进 YAML 读出来的日期,--json 又整份消失。"""
        from datetime import date, datetime, timedelta, timezone
        from pipeline_lib import Report
        rep = Report("x")
        rep.add("一致性", "PASS", "ok")
        rep.facts = {"d": date(2026, 9, 29),
                     "t": datetime(2026, 9, 29, 14, 3, 11, tzinfo=timezone(timedelta(hours=-4)))}
        facts = json.loads(rep.to_json())["facts"]
        self.assertEqual(facts, {"d": "2026-09-29", "t": "2026-09-29T14:03:11-04:00"})

    def test_a_scalar_approval_is_a_readable_error_and_json_is_still_written(self):
        cases = (("task_plan.md", "approval: approved"),
                 ("task_plan.md", "approval: [approved]"),
                 ("dossier.md", "approval: approved"))
        for doc, line in cases:
            with self.subTest(doc=doc, line=line):
                self.tmp = tempfile.mkdtemp(prefix="v111-tb2-scalar-")
                self.addCleanup(shutil.rmtree, self.tmp, True)
                plan = new_plan(self.tmp, pipeline_status="gate1_approved")
                write_progress(self.tmp)
                if doc == "dossier.md":
                    # 任务计划正常确认过;坏的是资料汇编那一份的确认记录
                    code, out = run("stamp.py", plan, "--approve", "--by", "研究员", "--quote", "好")
                    self.assertEqual(code, 0, out)
                    new_doc(os.path.join(self.tmp, "dossier.md"), "dossier")
                edit_text(os.path.join(self.tmp, doc), r"^approval:\n(?:  .*\n)+", line + NL)
                code, out, data = self.status()
                self.assertEqual(code, 1, out)
                fail = [l for l in out.split(NL) if l.startswith("[FAIL] 印章:") and doc in l]
                self.assertTrue(fail, out)
                self.assertIn("不是键值映射", fail[0])
                self.assertEqual(data["facts"]["stamps"][doc]["status"], "draft",
                                 "读不出的确认记录不许算作已确认")
                self.assertIn(("印章", "FAIL"),
                              [(i["check"], i["status"]) for i in data["items"]])

    def test_a_last_turn_at_without_a_timezone_is_a_warning_not_a_crash(self):
        """同一脚本、同一类:`last_turn_at` 不带时区(含不加引号的日期),原来 TypeError 崩。"""
        for written in ("2026-09-29 10:00:00", "'2026-09-29T10:00:00'", "2026-09-29"):
            with self.subTest(written=written):
                edit_text(self.plan, r"^last_turn_at: .*$", "last_turn_at: " + written)
                code, out, data = self.status()
                self.assertEqual(code, 0, out)
                self.assertIn("[WARN] 时间:", out)
                self.assertIsNone(data["facts"]["hours_since_last_turn"])

    def test_cite_check_and_stamp_check_report_a_scalar_approval(self):
        """同一缺陷的另两处读者:cite_check ⑥ / ⑪ 与 stamp.py --check。"""
        with io.open(os.path.join(self.tmp, "references.yaml"), "w", encoding="utf-8",
                     newline=NL) as f:
            f.write("entries: []" + NL)
        os.makedirs(os.path.join(self.tmp, "cards"))
        new_doc(os.path.join(self.tmp, "dossier.md"), "dossier")
        for doc in ("task_plan.md", "dossier.md"):
            edit_text(os.path.join(self.tmp, doc), r"^approval:\n(?:  .*\n)+",
                      "approval: approved" + NL)
        code, out = run("cite_check.py", "--project", self.tmp)
        self.assertNotIn("Traceback", out, out)
        self.assertIn("交付预告按钮", out)
        six = [l for l in out.split(NL) if l.startswith("[FAIL] 三印章: task_plan.md")]
        self.assertTrue(six and "不是键值映射" in six[0], out)
        code, out = run("stamp.py", self.plan, "--check")
        self.assertEqual(code, 1, out)
        self.assertNotIn("Traceback", out, out)
        # 应用的兜底读法按这一行认「不是已确认」(app-core signoff.mjs toolkitRecordStatus,
        # 多行模式:这一行以右括号结尾)
        self.assertRegex(out, r"(?m)印章无效\(status=None\)\s*$")


# ═════════════════════════════════════════════════════════════════ Tb3
PROVENANCE = {"session": "session-a",
              "session_picked_by": "DSH_SESSION_ID(harness 注入)",
              "captured_at": "2026-09-04T12:00:00.000Z"}


def write_raw(path, frontmatter_lines, body="正文。"):
    with io.open(path, "w", encoding="utf-8", newline=NL) as f:
        f.write("---" + NL + NL.join(frontmatter_lines) + NL + "---" + NL + NL + body + NL)


def budget_project(tmp, limits, dashboard, json_steps=30):
    """够 cite_check 跑到 ⑧ 的最小项目。limits / dashboard 是 `键: 原文` 的映射。"""
    write_raw(os.path.join(tmp, "task_plan.md"),
              ["title: 计划", "version: 1", "budget:"]
              + ["  %s: %s" % kv for kv in limits.items()])
    write_raw(os.path.join(tmp, "dossier.md"),
              ["title: 汇编", "version: 1", "dashboard:"]
              + ["  %s: %s" % kv for kv in dashboard.items()]
              + ["  %s: %s" % (k, json.dumps(v, ensure_ascii=False))
                 for k, v in PROVENANCE.items()])
    with io.open(os.path.join(tmp, "references.yaml"), "w", encoding="utf-8", newline=NL) as f:
        f.write("refs: []" + NL)
    for d in ("drafts", "out", "cards"):
        os.makedirs(os.path.join(tmp, d), exist_ok=True)
    with io.open(os.path.join(tmp, "out", "session_facts.json"), "w", encoding="utf-8",
                 newline=NL) as f:
        f.write(json.dumps(dict(PROVENANCE, steps_used=json_steps), ensure_ascii=False))


def cite(tmp):
    code, out = run("cite_check.py", "--project", tmp, "--skip-stamps")
    return out


def line_of(text, item):
    """按**项名**取一行(`预算仪表:` 与 `预算仪表·步数:` 是两项)。"""
    for line in text.split(NL):
        if item + ":" in line:
            return line.strip()
    return "(输出里没有「%s」这一项)" % item


class Tb3OnlyAsciiDigitsAreNumbers(unittest.TestCase):
    """Tb3:cite_check ⑧ 的 `_num` 用 `isdigit()` —— 引号里的上标 `'²'` 让它崩
    (`'²'.isdigit()` 为真而 `int('²')` 抛错;T 验收·低)。改成只认 ASCII 数字。"""

    LIMITS = {"steps_max": "500", "cards_max": "50"}
    DASH = {"cards_count": "0", "steps_used": "30"}

    def check(self, limits=None, dashboard=None, json_steps=30):
        tmp = tempfile.mkdtemp(prefix="v111-tb3-")
        self.addCleanup(shutil.rmtree, tmp, True)
        budget_project(tmp, dict(self.LIMITS, **(limits or {})),
                       dict(self.DASH, **(dashboard or {})), json_steps)
        out = cite(tmp)
        self.assertNotIn("Traceback", out, out)
        self.assertIn("交付预告按钮", out, "cite_check 没跑完")
        return out

    # ── 回归
    def test_a_quoted_superscript_does_not_crash_the_budget_check(self):
        for where in ("steps_used", "cards_count", "steps_max", "cards_max"):
            with self.subTest(where=where):
                if where.endswith("_max"):
                    out = self.check(limits={where: "'²'"})
                    self.assertIn("%s='²' 不是整数" % where, line_of(out, "预算仪表"))
                else:
                    out = self.check(dashboard={where: "'²'"})
                    self.assertIn("WARN", line_of(out, "预算仪表" if where == "cards_count"
                                                  else "预算仪表·步数"))

    def test_other_non_ascii_digits_are_not_numbers_either(self):
        """`'①'` 同样会崩;`'７'`(全角)`'٣'`(阿拉伯-印度数字)`int()` 收,但它们不是
        ASCII 数字 —— 原来被当成 7 / 3,与机器取值「一致」而报 PASS。"""
        for text, machine in (("'①'", 1), ("'７'", 7), ("'٣'", 3)):
            with self.subTest(text=text):
                out = self.check(dashboard={"steps_used": text}, json_steps=machine)
                line = line_of(out, "预算仪表·步数")
                self.assertIn("WARN", line, out)
                self.assertIn("未取到", line)

    def test_a_non_integer_in_session_facts_is_null_not_unreadable(self):
        """session_facts.json 里 steps_used 为 `"²"`:原来 `_num` 在 try 里抛错,
        被说成「json 读不出」;它是合法 json,只是那个数不是整数。"""
        out = self.check(json_steps="²")
        line = line_of(out, "预算仪表·步数")
        self.assertIn("不是整数", line, out)
        self.assertNotIn("读不出", line)

    def test_nan_and_infinity_are_not_numbers(self):
        """同一函数的另一处崩:YAML 的 `.nan` / `.inf` 是 float,`int()` 抛错。"""
        for text in (".nan", ".inf", "-.inf"):
            with self.subTest(text=text):
                out = self.check(dashboard={"steps_used": text})
                self.assertIn("WARN", line_of(out, "预算仪表·步数"), out)

    # ── 控制组
    def test_control_ascii_digits_still_count(self):
        """`'30'` 与 30 仍是同一个数(v1.1.0 · C-1 的口径不变)。"""
        out = self.check(dashboard={"steps_used": "'30'"}, limits={"steps_max": "'500'"})
        self.assertIn("PASS", line_of(out, "预算仪表·步数"), out)
        self.assertIn("PASS", line_of(out, "预算仪表"), out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
