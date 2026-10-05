# -*- coding: utf-8 -*-
"""v1.1.2 · 积压清理(2026-09-30)工具包 T3-1、T3-2 的回归测试。

跑法: python -X utf8 -m pytest -q test_v112_fixes.py

⚠ 同 test_v111_fixes.py:标了「回归」的每条必须在修之前的脚本上**是红的** —— 否则说明没
测到缺陷。它们只用旧脚本里本来就有的入口(命令行),可以原样拿到旧脚本上跑,对照一次红、一次
绿(2026-09-30 对 11964e3 的脚本跑过:回归条全红)。标了「控制组」的在新旧脚本上都该是绿的:
它们钉住「修的时候没把正常的路也堵了」。
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
from pipeline_lib import content_hash, read_md, write_md  # noqa: E402

NL = chr(10)
# 外面的 DSH_* 不许漏进子进程(见 test_pipeline_status.py 同名一段的理由)。
for _k in ("DSH_HOME", "DSH_SESSION_ID", "DSH_SESSION_JSONL"):
    os.environ.pop(_k, None)
PY_ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
AT = "2026-09-29T14:03:11-04:00"


def run(script, *args):
    r = subprocess.run([sys.executable, "-X", "utf8", os.path.join(HERE, script)] + list(args),
                       capture_output=True, env=PY_ENV, timeout=120)
    return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")


def new_doc(path, kind, pipeline_status=None, version=2):
    """一份等确认的正本(与 stamp.py 写出来的形态相同),revision_log 先空着、待手改。"""
    meta = {"kind": kind, "title": kind, "version": version}
    if pipeline_status:
        meta["pipeline_status"] = pipeline_status
    meta["approval"] = {"status": "awaiting", "approved_at": None, "approved_by": None,
                        "approval_quote": None, "approved_hash": None}
    meta["revision_log"] = []
    write_md(path, meta, NL + "# " + kind + NL + NL + "正文若干。" + NL)
    return path


def write_progress(tmp):
    with io.open(os.path.join(tmp, "PROGRESS.md"), "w", encoding="utf-8", newline=NL) as f:
        f.write("进行中" + NL)


def set_log(path, lines):
    """把 `revision_log: []` 那一行换成手写的几行(write_md 会替字符串加引号,这里要的正是手写形态)。"""
    with io.open(path, encoding="utf-8") as f:
        text = f.read()
    new, n = re.subn(r"^revision_log: \[\]$", NL.join(lines), text, flags=re.M)
    assert n == 1, "样例里没找到 revision_log: []"
    with io.open(path, "w", encoding="utf-8", newline=NL) as f:
        f.write(new)


def line_of(path, needle):
    """needle 第一次出现在文件第几行(1 起)。"""
    with io.open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            if needle in line:
                return i
    raise AssertionError("%s 里没有 %s" % (path, needle))


def entry(at):
    return ["revision_log:", "- v: 2", "  at: " + at, "  who: agent", "  what: 改了范围", "  why: 用户要求"]


# ═════════════════════════════════════════════════════════════════ T3-1
class T31PipelineStatusSurvivesABrokenRevisionLog(unittest.TestCase):
    """T3-1:`revision_log` 写坏(不是列表、条目不是键值映射、YAML 写法有误)或日期不存在
    (不加引号的 2026-02-30)时 pipeline_status 崩溃。要的是:不崩,报一条看得懂的,`--json` 照样写出。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v112-t31-")
        self.addCleanup(shutil.rmtree, self.tmp, True)   # 每次都删(原来一跑留下十几个临时目录)
        self.plan = new_doc(os.path.join(self.tmp, "task_plan.md"), "task_plan", "gate1_awaiting")
        write_progress(self.tmp)

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

    def lines(self, out, prefix):
        return [l for l in out.split(NL) if l.startswith(prefix)]

    # ── 回归
    def test_a_revision_log_of_the_wrong_shape_is_a_warning_not_a_crash(self):
        # 名字: (手写的几行, 说明里该有的话, 说明该指的那一行里有的字)。说明只说类型和行号,不引值(复核补)。
        cases = {
            "散文字符串": (["revision_log: v2 改了范围"], "revision_log 现在是一段文字,不是列表", "revision_log:"),
            "映射": (["revision_log:", "  v: 2", "  at: '" + AT + "'"], "revision_log 现在是一个映射,不是列表", "revision_log:"),
            "整数": (["revision_log: 3"], "revision_log 现在是一个数字,不是列表", "revision_log:"),
            "条目是散文": (["revision_log:", "- v2 改了范围", "- v3 又改了"],
                      "revision_log 第 1 条现在是一段文字,不是键值映射", "- v2 改了范围"),
            "条目是列表": (["revision_log:", "- [2, '2026-09-29']"], "revision_log 第 1 条现在是一个列表,不是键值映射", "- [2,"),
            "好坏混着": (entry("'" + AT + "'") + ["- 顺手改了标题"], "revision_log 第 2 条现在是一段文字,不是键值映射",
                     "- 顺手改了标题"),
        }
        for name, (log, said, needle) in cases.items():
            with self.subTest(name=name):
                self.setUp()
                set_log(self.plan, log)
                code, out, data = self.status()
                self.assertEqual(code, 0, "形状不对是留痕的问题,不该挡住推进:" + out)
                warn = [l for l in self.lines(out, "[WARN] 留痕: task_plan.md: ") if said in l]
                self.assertTrue(warn, out)
                self.assertIn("(第 %d 行)" % line_of(self.plan, needle), warn[0])
                self.assertIn(("留痕", "WARN"), [(i["check"], i["status"]) for i in data["items"]])

    def test_an_impossible_unquoted_date_is_a_readable_error_and_json_is_still_written(self):
        for written in ("2026-02-30", "2026-13-01", "2026-09-29 25:00:00"):
            with self.subTest(written=written):
                self.setUp()
                set_log(self.plan, entry(written))
                code, out, data = self.status()
                self.assertEqual(code, 1, out)
                fail = self.lines(out, "[FAIL] 状态机: task_plan.md: frontmatter 读不出来")
                self.assertTrue(fail, out)
                self.assertIn("第 %d 行" % line_of(self.plan, written), fail[0])
                self.assertNotIn(written, fail[0], "只说第几行,不引写的值(复核补)")
                self.assertIn("不是存在的日期", fail[0])
                facts = data["facts"]
                self.assertIsNone(facts["pipeline_status"])
                self.assertIsNone(facts["stages"], "读不出就是读不出,不按缺省冒充")
                self.assertIn("frontmatter 读不出来", facts["stages_error"])
                self.assertEqual(facts["stamps"], {}, "读不出的任务计划不许带出任何一份确认记录")

    def test_broken_yaml_in_the_revision_log_is_a_readable_error(self):
        log = ["revision_log: [{v: 2, at: '" + AT + "', who: agent"]   # 少了收尾的 }]
        set_log(self.plan, log)
        code, out, data = self.status()
        self.assertEqual(code, 1, out)
        fail = self.lines(out, "[FAIL] 状态机: task_plan.md: frontmatter 读不出来")
        self.assertTrue(fail and "YAML 写法有误" in fail[0], out)
        self.assertRegex(fail[0], r"第 \d+ 行")

    def test_an_unreadable_dossier_reads_as_not_confirmed(self):
        """坏的不是任务计划而是资料汇编:它的确认记录读不到,按未确认算,报印章 FAIL。"""
        code, out = run("stamp.py", self.plan, "--approve", "--by", "研究员", "--quote", "好", "--at", AT)
        self.assertEqual(code, 0, out)
        for state in ("gate1_approved", "collecting", "gate2_awaiting"):
            code, out = run("stamp.py", self.plan, "--advance", state)
            self.assertEqual(code, 0, out)
        dossier = new_doc(os.path.join(self.tmp, "dossier.md"), "dossier")
        set_log(dossier, entry("2026-02-30"))
        code, out, data = self.status()
        self.assertEqual(code, 1, out)
        fail = self.lines(out, "[FAIL] 印章: dossier.md: frontmatter 读不出来")
        self.assertTrue(fail, out)
        self.assertIn("第 %d 行" % line_of(dossier, "2026-02-30"), fail[0])
        self.assertEqual(data["facts"]["stamps"]["dossier.md"]["status"], "draft",
                         "读不出的确认记录不许算作已确认")
        self.assertEqual(data["facts"]["stamps"]["task_plan.md"]["status"], "approved")

    def test_a_time_that_cannot_be_read_is_a_warning(self):
        """加了引号的不存在日期、不是 ISO 8601 的写法:原来一声不吭地跳过,现在照实报。"""
        for written in ("'2026-02-30'", "昨天下午"):
            with self.subTest(written=written):
                self.setUp()
                set_log(self.plan, entry(written))
                code, out, data = self.status()
                self.assertEqual(code, 0, out)
                warn = [l for l in self.lines(out, "[WARN] 留痕: task_plan.md: ")
                        if "revision_log[0].at" in l and "读不出时间" in l]
                self.assertTrue(warn, out)
                self.assertIn("第 %d 行的 revision_log[0].at" % line_of(self.plan, "  at: "), warn[0])
                self.assertNotIn(written.strip("'"), warn[0], "只说第几行,不引写的值(复核补)")

    def test_a_broken_card_or_reference_list_does_not_stop_the_report(self):
        """计数读的卡片与登记表坏了:数不出来就记 None(面板显示「未产出」),报告照常。"""
        os.makedirs(os.path.join(self.tmp, "cards"))
        with io.open(os.path.join(self.tmp, "cards", "C01.md"), "w", encoding="utf-8", newline=NL) as f:
            f.write("---" + NL + "uid: C01" + NL + "captured_at: 2026-02-30" + NL + "---" + NL + "正文" + NL)
        with io.open(os.path.join(self.tmp, "references.yaml"), "w", encoding="utf-8", newline=NL) as f:
            f.write("entries: [{id: R1" + NL)
        code, out, data = self.status()
        self.assertEqual(code, 0, out)
        counts = data["facts"]["counts"]
        self.assertIsNone(counts["cards_actual"])
        self.assertIsNone(counts["references_registered"])

    # ── 控制组
    def test_control_a_good_revision_log_raises_nothing_new(self):
        set_log(self.plan, entry("'" + AT + "'"))
        code, out, data = self.status()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.lines(out, "[WARN] 留痕"), [], out)
        self.assertEqual(data["facts"]["pipeline_status"], "gate1_awaiting")

    def test_control_a_future_time_is_still_the_timestamp_warning(self):
        set_log(self.plan, entry("'2099-01-01T00:00:00+00:00'"))
        code, out, data = self.status()
        self.assertEqual(code, 0, out)
        said = "[WARN] 时间戳: task_plan.md: 第 %d 行的 revision_log[0].at 比文件写入时刻晚" % line_of(self.plan, "  at: ")
        self.assertTrue(self.lines(out, said), out)
        self.assertEqual(self.lines(out, "[WARN] 留痕"), [], out)


# ═════════════════════════════════════════════════════════════════ T3-1 复核(第二轮)
GOLDEN = os.path.join(HERE, "fixtures", "golden_content_hashes.json")


def write_bytes(path, data):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with io.open(path, "wb") as f:
        f.write(data)


class ContentHashIsWhatSignaturesBind(unittest.TestCase):
    """签名绑定的是 content_hash。11964e3 时算得出 hash 的每一份文档 —— 两边测试自己的样例项目
    (含 stamp.py 写的确认、签名、作废、替换)、合成的各种 YAML 写法、仓里带 frontmatter 的样例文件 —— 现在必须
    算出逐位相同的值。哪一份变了,就等于用户签过的确认全部作废(或者更糟:换了内容还对得上)。
    记录方法:从 `git archive 11964e3` 取出那时的工具包与应用核心,用那时的 read_md + content_hash 算。"""

    def test_every_document_that_hashed_at_11964e3_hashes_the_same(self):
        with io.open(GOLDEN, encoding="ascii") as f:
            golden = json.load(f)
        self.assertEqual(golden["recorded_at"], "11964e3")
        entries = golden["entries"]
        self.assertGreaterEqual(len(entries), 70, "golden 记录少得可疑")
        tmp = tempfile.mkdtemp(prefix="v112-golden-")
        self.addCleanup(shutil.rmtree, tmp, True)
        changed = []
        for i, e in enumerate(entries):
            path = os.path.join(tmp, "%03d.md" % i)
            with io.open(path, "w", encoding="utf-8", newline="") as f:
                f.write(e["text"])
            meta, body = read_md(path)
            got = content_hash(meta, body)
            if got != e["hash"]:
                changed.append("%s: %s -> %s" % (e["name"], e["hash"], got))
        self.assertEqual(changed, [])

    def test_the_command_line_gives_the_same_hash(self):
        """stamp.py --hash(应用取 hash 走的那条路)对带确认记录的样例给出同一个值。"""
        with io.open(GOLDEN, encoding="ascii") as f:
            entries = [e for e in json.load(f)["entries"] if "approved_hash:" in e["text"]][:6]
        self.assertGreaterEqual(len(entries), 3)
        tmp = tempfile.mkdtemp(prefix="v112-golden-cli-")
        self.addCleanup(shutil.rmtree, tmp, True)
        for i, e in enumerate(entries):
            path = os.path.join(tmp, "%d.md" % i)
            with io.open(path, "w", encoding="utf-8", newline="") as f:
                f.write(e["text"])
            code, out = run("stamp.py", path, "--hash")
            self.assertEqual((code, out.strip()), (0, e["hash"]), e["name"])


class DateKeysCanBeHashed(unittest.TestCase):
    """中(T3-1 复核):键是日期的映射(`milestones: {2026-10-01: 初稿, 2026-10-14: 交付}`)、数字键混着文字键、
    YAML 1.1 的 yes / no 键混着文字键,让 content_hash 抛 TypeError(json 的键只认文字,排序比不了日期和文字)
    —— pipeline_status 崩、没有 --json,stamp.py --hash / --approve 崩:这份任务计划永远确认不了。"""

    KEYS = {
        "日期键": "milestones: {2026-10-01: 初稿, 2026-10-14: 交付}",
        "日期时间键": "log: {2026-10-01 09:00:00: 开会}",
        "数字键混文字键": "years: {2025: 基线, 目标: 2026}",
        "yes/no 键混文字键": "review: {yes: 通过, no: 不通过, 备注: 无}",
    }

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v112-keys-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def doc(self, name, line, body="正文。"):
        path = os.path.join(self.tmp, name)
        with io.open(path, "w", encoding="utf-8", newline=NL) as f:
            f.write("---" + NL + "kind: task_plan" + NL + line + NL + "---" + NL + NL + body + NL)
        return path

    def hash_of(self, path):
        meta, body = read_md(path)
        return content_hash(meta, body)

    # ── 回归
    def test_such_keys_hash_and_the_hash_does_not_depend_on_key_order(self):
        for name, line in self.KEYS.items():
            with self.subTest(name=name):
                self.assertRegex(self.hash_of(self.doc("a.md", line)), r"^[0-9a-f]{12}$")
        a = self.hash_of(self.doc("a.md", "milestones: {2026-10-01: 初稿, 2026-10-14: 交付}"))
        b = self.hash_of(self.doc("b.md", "milestones: {2026-10-14: 交付, 2026-10-01: 初稿}"))
        self.assertEqual(a, b, "同样的内容,键的先后不该改 hash")

    def test_a_change_is_still_seen(self):
        a = self.hash_of(self.doc("a.md", "milestones: {2026-10-01: 初稿, 2026-10-14: 交付}"))
        for other in ("milestones: {2026-10-01: 初稿, 2026-10-15: 交付}",
                      "milestones: {2026-10-01: 初稿, 2026-10-14: 定稿}",
                      "milestones: {'2026-10-01': 初稿, '2026-10-14': 交付}"):
            with self.subTest(other=other):
                self.assertNotEqual(a, self.hash_of(self.doc("b.md", other)))
        # 键的类型也算内容:数字 1 与文字 '1' 不混为一谈
        c = self.hash_of(self.doc("c.md", "n: {1: x, 2: y, 目标: z}"))
        d = self.hash_of(self.doc("d.md", "n: {'1': x, 2: y, 目标: z}"))
        self.assertNotEqual(c, d)
        # 新算法的结果不会和原来那条路的结果撞上:顶层有日期键的文档,对一份把新算法的中间写法原样抄成
        # 普通 YAML 的文档 —— 两者序列化出来的 JSON 一字不差,只靠前面那个标记分开。
        e = self.hash_of(self.doc("e.md", "2026-10-01: 初稿"))
        f_path = os.path.join(self.tmp, "f.md")
        with io.open(f_path, "w", encoding="utf-8", newline=NL) as fh:
            fh.write("---" + NL + "m: [[date, '2026-10-01', 初稿], [str, kind, task_plan]]" + NL + "---" + NL + NL + "正文。" + NL)
        self.assertNotEqual(e, self.hash_of(f_path))

    def test_such_a_task_plan_can_be_confirmed(self):
        """--hash、--approve、pipeline_status 都不崩,确认之后 hash 对得上。"""
        plan = new_doc(os.path.join(self.tmp, "task_plan.md"), "task_plan", "gate1_awaiting")
        write_progress(self.tmp)
        with io.open(plan, encoding="utf-8") as f:
            text = f.read()
        with io.open(plan, "w", encoding="utf-8", newline=NL) as f:
            f.write(text.replace("kind: task_plan" + NL,
                                 "kind: task_plan" + NL + "milestones: {2026-10-01: 初稿, 2026-10-14: 交付}" + NL, 1))
        code, out = run("stamp.py", plan, "--hash")
        self.assertEqual(code, 0, out)
        self.assertRegex(out.strip(), r"^[0-9a-f]{12}$")
        code, out = run("stamp.py", plan, "--approve", "--by", "研究员", "--quote", "好", "--at", AT)
        self.assertEqual(code, 0, out)
        out_json = os.path.join(self.tmp, "s.json")
        code, out = run("pipeline_status.py", self.tmp, "--json", out_json)
        self.assertNotIn("Traceback", out, out)
        with io.open(out_json, encoding="utf-8") as f:
            stamps = json.load(f)["facts"]["stamps"]
        self.assertEqual(stamps["task_plan.md"]["status"], "approved", out)


class OtherUnreadableInputsAreReportedNotCrashedOn(unittest.TestCase):
    """低(T3-1 复核):还有几条路让 pipeline_status 崩、没有 --json —— 不是 UTF-8 的文件、frontmatter
    是列表的卡片、写成纯文字的参考文献登记;另补三处没有测试钉住的行为。"""

    setUp = T31PipelineStatusSurvivesABrokenRevisionLog.setUp
    status = T31PipelineStatusSurvivesABrokenRevisionLog.status
    lines = T31PipelineStatusSurvivesABrokenRevisionLog.lines

    def path(self, *parts):
        return os.path.join(self.tmp, *parts)

    # ── 回归
    def test_files_that_are_not_utf8(self):
        gbk = ("---" + NL + "kind: task_plan" + NL + "title: 研究计划" + NL + "---" + NL + NL + "正文" + NL).encode("gbk")
        write_bytes(self.plan, gbk)
        code, out, data = self.status()
        self.assertEqual(code, 1, out)
        fail = self.lines(out, "[FAIL] 状态机: task_plan.md: frontmatter 读不出来")
        self.assertTrue(fail and "不是 UTF-8" in fail[0], out)
        self.assertEqual(data["facts"]["stamps"], {})

        self.setUp()
        write_bytes(self.path("dossier.md"), gbk.replace(b"task_plan", b"dossier"))
        code, out, data = self.status()
        fail = self.lines(out, "[FAIL] 印章: dossier.md: frontmatter 读不出来")
        self.assertTrue(fail and "不是 UTF-8" in fail[0], out)
        self.assertEqual(data["facts"]["stamps"]["dossier.md"]["status"], "draft")

        self.setUp()
        write_bytes(self.path("PROGRESS.md"), ("进行中" + NL).encode("gbk"))
        code, out, data = self.status()
        self.assertEqual(code, 0, out)
        warn = self.lines(out, "[WARN] PROGRESS: ")
        self.assertTrue(warn and "不是 UTF-8" in warn[0], out)

        # 计数读的卡片与登记表:数不出来记 None,说清是哪个文件、第几行起
        self.setUp()
        write_bytes(self.path("cards", "C01.md"), ("---" + NL + "uid: C01" + NL + "title: 研究计划" + NL + "---" + NL).encode("gbk"))
        write_bytes(self.path("references.yaml"), ("entries:" + NL + "- {id: R1, title: 研究计划}" + NL).encode("gbk"))
        code, out, data = self.status()
        self.assertEqual(code, 0, out)
        for prefix, said in (("[WARN] 留痕: cards/C01.md: ", "第 3 行起不是 UTF-8"),
                             ("[WARN] 留痕: references.yaml: ", "第 2 行起不是 UTF-8")):
            warn = self.lines(out, prefix)
            self.assertTrue(warn and said in warn[0], out)
        counts = data["facts"]["counts"]
        self.assertEqual((counts["cards_actual"], counts["references_registered"]), (None, None))

    def test_a_card_whose_frontmatter_is_a_list(self):
        write_bytes(self.path("cards", "C01.md"), ("---" + NL + "- a" + NL + "- b" + NL + "---" + NL + "正文" + NL).encode("utf-8"))
        code, out, data = self.status()
        self.assertEqual(code, 0, out)
        warn = [l for l in self.lines(out, "[WARN] 留痕: cards/C01.md: ") if "不是键值映射" in l]
        self.assertTrue(warn, out)
        self.assertIsNone(data["facts"]["counts"]["cards_actual"])

    def test_references_that_are_not_a_list_of_mappings(self):
        write_bytes(self.path("drafts", "report.md"), ("见 [[R1]]。" + NL).encode("utf-8"))
        for text, said in (("entries: [R1, R2]", "entries 第 1 条现在是一段文字,不是键值映射(第 1 行)"),
                           ("entries: R1", "entries 现在是一段文字,不是列表(第 1 行)"),
                           ("- id: R1" + NL + "- R2", "第 2 条现在是一段文字,不是键值映射(第 2 行)")):
            with self.subTest(text=text):
                write_bytes(self.path("references.yaml"), (text + NL).encode("utf-8"))
                code, out, data = self.status()
                self.assertEqual(code, 0, out)
                warn = [l for l in self.lines(out, "[WARN] 留痕: references.yaml: ") if said in l]
                self.assertTrue(warn, out)
                counts = data["facts"]["counts"]
                self.assertEqual((counts["references_registered"], counts["references_cited"], counts["inline_citations"]),
                                 (None, None, None))

    def test_a_frontmatter_that_is_a_list_is_not_a_mapping(self):
        listed = ("---" + NL + "- a" + NL + "- b" + NL + "---" + NL + NL + "正文" + NL).encode("utf-8")
        write_bytes(self.plan, listed)
        code, out, data = self.status()
        self.assertEqual(code, 1, out)
        fail = self.lines(out, "[FAIL] 状态机: task_plan.md: frontmatter 读不出来")
        self.assertTrue(fail and "第 2–3 行现在是一个列表,不是键值映射" in fail[0], out)
        self.setUp()
        write_bytes(self.path("dossier.md"), listed)
        code, out, data = self.status()
        fail = self.lines(out, "[FAIL] 印章: dossier.md: frontmatter 读不出来")
        self.assertTrue(fail and "不是键值映射" in fail[0], out)

    def test_line_numbers_count_lines_as_an_editor_does(self):
        """PyYAML 把值里的 U+2028 / U+2029 / NEL 也当换行,行号会比编辑器里多;按字符位置换算回文件行号。"""
        for sep in (chr(0x2028), chr(0x2029), chr(0x85)):
            with self.subTest(sep=hex(ord(sep))):
                self.setUp()
                set_log(self.plan, ['note: "前' + sep + '后"'] + entry("2026-02-30"))
                code, out, data = self.status()
                fail = self.lines(out, "[FAIL] 状态机: task_plan.md: frontmatter 读不出来")
                self.assertTrue(fail, out)
                self.assertIn("第 %d 行" % line_of(self.plan, "2026-02-30"), fail[0])
            # 留痕 那几条的行号是另一条路算的(frontmatter 读得出来,按键找行),同样按编辑器数
            with self.subTest(sep=hex(ord(sep)), path="留痕"):
                self.setUp()
                set_log(self.plan, ['note: "前' + sep + '后"', "revision_log: v2 改了范围"])
                code, out, data = self.status()
                warn = self.lines(out, "[WARN] 留痕: task_plan.md: revision_log 现在是一段文字")
                self.assertTrue(warn, out)
                self.assertIn("(第 %d 行)" % line_of(self.plan, "revision_log: v2"), warn[0])

    def test_a_value_that_cannot_be_read_names_its_line(self):
        """日期之外读不出的值(显式标了 !!int 却不是整数)也指到行,不说成日期。"""
        set_log(self.plan, ["count: !!int 三张"])
        code, out, data = self.status()
        fail = self.lines(out, "[FAIL] 状态机: task_plan.md: frontmatter 读不出来")
        self.assertTrue(fail, out)
        self.assertIn("第 %d 行" % line_of(self.plan, "!!int"), fail[0])
        self.assertNotIn("日期", fail[0])
        self.assertNotIn("三张", fail[0], "只说第几行、标的什么类型,不引写的值(复核补)")


WHO, QUOTE, WHY = "鹭岛甲乙丙", "我从来没说过这句话", "鹦鹉螺口径"


class MessagesQuoteNoDocumentContent(unittest.TestCase):
    """v1.1.2 复核(补):v1.1.2 的 [WARN] 留痕 原来引 repr(revision_log) 的前 40 个字,如
    「…(现为 dict {'approval': {'approval_quote': '我从来没说过这…)」—— 把记录里的人和原话带进报告;报告会进
    --json、进应用,截断的片段应用遮不住。本轮加的、动过的说明一律只说类型和行号,不引值。
    判法:把人名、原话、口径(以及不存在的日期、锚点名这类值)写进记录,跑 pipeline_status,人读输出与 --json
    全文里不许出现其中任何连续三个字(截断的片段也算);同时要求那条说明确实在 —— 否则「没引」可能只是「没报」。"""

    setUp = T31PipelineStatusSurvivesABrokenRevisionLog.setUp
    status = T31PipelineStatusSurvivesABrokenRevisionLog.status
    lines = T31PipelineStatusSurvivesABrokenRevisionLog.lines

    def path(self, *parts):
        return os.path.join(self.tmp, *parts)

    def assertQuotesNothing(self, out, data, whole=()):
        text = out + NL + json.dumps(data, ensure_ascii=False)
        for secret in (WHO, QUOTE, WHY):
            for k in range(len(secret) - 2):
                self.assertNotIn(secret[k:k + 3], text, "报告里出现了记录内容的片段「%s」:%s" % (secret[k:k + 3], out))
        for secret in whole:
            self.assertNotIn(secret, text, "报告里出现了记录里的值「%s」:%s" % (secret, out))

    def check_cases(self, cases):
        for name, (prepare, prefix, whole) in cases.items():
            with self.subTest(name=name):
                self.setUp()
                prepare()
                code, out, data = self.status()
                self.assertTrue(self.lines(out, prefix), "该有的那条说明不在:" + out)
                self.assertQuotesNothing(out, data, whole)

    # ── 回归
    def test_a_revision_log_holding_a_record_is_described_not_quoted(self):
        """留痕(形状不对)与 frontmatter 读不出来这两类说明:revision_log 里装着一条记录 —— 写成映射、写成
        文字、套在列表里 —— 报告只说它是什么、在第几行。"""
        def log(*lines):
            return lambda: set_log(self.plan, list(lines))
        warn, fail = "[WARN] 留痕: task_plan.md: ", "[FAIL] 状态机: task_plan.md: frontmatter 读不出来"
        self.check_cases({
            "映射里套着确认记录": (log("revision_log:", "  approval:", "    approval_quote: " + QUOTE,
                               "    approved_by: " + WHO, "    why: " + WHY), warn, ()),
            "映射就是一条记录": (log("revision_log: {who: %s, what: %s, why: %s}" % (WHO, QUOTE, WHY)), warn, ()),
            "键是人名": (log("revision_log: {%s: %s}" % (WHO, QUOTE)), warn, ()),
            "文字": (log("revision_log: %s:%s,%s" % (WHO, QUOTE, WHY)), warn, ()),
            "条目是文字": (log("revision_log:", "- %s:%s" % (WHO, QUOTE)), warn, ()),
            "条目是列表,里面套着记录": (log("revision_log: [[{who: %s, what: %s}]]" % (WHO, QUOTE)), warn, ()),
            "记录里有不存在的日期": (log("revision_log: [{v: 2, at: 2026-02-30, who: %s, what: %s}]" % (WHO, QUOTE)),
                            fail, ("2026-02-30",)),
            "记录里有读不出的 !!int": (log("revision_log: [{v: !!int %s, who: %s}]" % (QUOTE, WHO)), fail, ()),
            "记录里引了不存在的锚点": (log("revision_log: [{v: 2, who: *LuDaoJiaYiBing, what: %s}]" % QUOTE),
                             fail, ("LuDaoJiaYiBing",)),
        })
        # 同一类,换资料汇编走「印章」那一支
        self.check_cases({
            "资料汇编的记录里有读不出的 !!int": (
                lambda: set_log(new_doc(self.path("dossier.md"), "dossier"),
                                ["revision_log: [{v: !!int %s, who: %s}]" % (QUOTE, WHO)]),
                "[FAIL] 印章: dossier.md: frontmatter 读不出来", ()),
        })

    def test_the_other_messages_of_this_round_quote_nothing_either(self):
        """同一条规矩,本轮加的、动过的其余几条:读不出的时间、晚于写入时刻的时间、卡片读不出来、参考文献登记表。"""
        def write(rel, text):
            return lambda: write_bytes(self.path(*rel.split("/")), (text + NL).encode("utf-8"))
        self.check_cases({
            "读不出的修改时间": (lambda: set_log(self.plan, ["revision_log: [{v: 2, at: %s, who: %s}]" % (QUOTE, WHO)]),
                         "[WARN] 留痕: task_plan.md: ", ()),
            "读不出的 updated_at": (lambda: set_log(self.plan, ["updated_at: %s说的那天" % WHO, "revision_log: []"]),
                              "[WARN] 留痕: task_plan.md: ", ()),
            "晚于写入时刻的时间": (lambda: set_log(self.plan, ["updated_at: '2099-01-01T00:00:00+00:00'", "revision_log: []"]),
                          "[WARN] 时间戳: task_plan.md: ", ("2099-01-01",)),
            "卡片里读不出的值": (write("cards/C01.md", "---" + NL + "uid: !!int " + WHO + NL + "---" + NL + "正文"),
                         "[WARN] 留痕: cards/C01.md: ", ()),
            "登记表的条目是文字": (write("references.yaml", "entries: [%s]" % QUOTE), "[WARN] 留痕: references.yaml: ", ()),
            "登记表的 entries 是文字": (write("references.yaml", "entries: %s" % QUOTE), "[WARN] 留痕: references.yaml: ", ()),
            "登记表里读不出的值": (write("references.yaml", "entries: [{id: R1, n: !!int %s}]" % QUOTE),
                          "[WARN] 留痕: references.yaml: ", ()),
        })

    # ── 控制组
    def test_control_messages_that_never_quoted_anything_still_do_not(self):
        """frontmatter 是列表、文件不是 UTF-8:这两条原来就只说类型 / 第几行起,改写说明时别把值带进来。"""
        fail = "[FAIL] 状态机: task_plan.md: frontmatter 读不出来"
        self.check_cases({
            "frontmatter 是列表": (lambda: write_bytes(self.plan, ("---" + NL + "- " + QUOTE + NL + "- " + WHO + NL + "---"
                                                               + NL).encode("utf-8")), fail, ()),
            "不是 UTF-8": (lambda: write_bytes(self.plan, ("---" + NL + "who: " + WHO + NL + "what: " + QUOTE + NL + "---"
                                                          + NL).encode("gbk")), fail, ()),
        })


class TestsCleanUpAfterThemselves(unittest.TestCase):
    """低(T3-1 复核):这两个测试文件原来从不删临时目录,一跑留下十几个(%TEMP% 里攒了上千)。"""

    def test_a_finished_test_leaves_no_temp_folder(self):
        import test_v111_fixes
        for case in (T31PipelineStatusSurvivesABrokenRevisionLog("test_control_a_good_revision_log_raises_nothing_new"),
                     test_v111_fixes.Tb1SignedRecordIsNotOverwrittenSilently(
                         "test_control_unsigned_records_are_replaced_as_before")):
            with self.subTest(case=type(case).__name__):
                result = unittest.TestResult()
                case.run(result)
                self.assertTrue(result.wasSuccessful(), result.failures + result.errors)
                self.assertFalse(os.path.exists(case.tmp), "临时目录没删:" + case.tmp)


# ═════════════════════════════════════════════════════════════════ T3-2
SPEC = os.path.join(HERE, os.pardir, "04-正本规格-v1.md")
STAGES_DOC = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "docs", "stages.md"))


SIGNOFF_DOC = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "docs", "signoff-format.md"))


class T32StagesDocNamesTheCurrentSpec(unittest.TestCase):
    """T3-2:docs/stages.md 开头写「对应规格 … v1.1.0」,而规格早已升版(内容也已含 v1.1.1 的补充)。
    复核补:docs/signoff-format.md 开头同样停在 v1.1.1,而它引的 §⑧-7 在 v1.1.2 扩了。"""

    def said_version(self, doc):
        with io.open(doc, encoding="utf-8") as f:
            head = f.read().split(NL)[:6]
        return [m.group(1) for l in head for m in [re.search(r"对应规格 `toolkit/04-正本规格-v1\.md` (v\d+\.\d+\.\d+)", l)] if m]

    # ── 回归
    def test_the_docs_name_the_spec_version_they_match(self):
        with io.open(SPEC, encoding="utf-8") as f:
            spec = re.search(r"\*\*版本:(v\d+\.\d+\.\d+)\*\*", f.read())
        self.assertIsNotNone(spec, "规格开头找不到「**版本:vX.Y.Z**」")
        for doc in (STAGES_DOC, SIGNOFF_DOC):
            with self.subTest(doc=os.path.basename(doc)):
                if not os.path.exists(doc):
                    self.skipTest("docs/ 不随工具包发布:%s 不在,无从比对" % doc)
                self.assertEqual(self.said_version(doc), [spec.group(1)],
                                 "%s 开头说的规格版本要和规格本身一致(规格升版就跟着改这一处)" % os.path.basename(doc))


if __name__ == "__main__":
    unittest.main()
