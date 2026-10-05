# -*- coding: utf-8 -*-
"""v1.2.0 · 界面实装工单 05 §四(A3)的工具包一侧:三点、成稿文件、独立复核结果(规格 §⑨)。

- 三点(§⑨-1、§⑨-3):新标签读得出、旧标签照样认、「齐全」的五条判据每条都会失败、命令行退出码;
- 成稿文件(§⑨-2):只算 out/ 下一层的 .html / .docx,不算 Word 的锁文件;cite_check 读的是同一批;
- 独立复核结果(§⑨-2):--seal 写出的结果、各种拒写、--check 对不对得上当前成稿、计数的口径;
- 规格自己的示例照这些判据是对的(示例与实现钉在一起);skill 里提到的脚本都在。

每条判据都配一个会失败的输入。跑法:python -X utf8 -m pytest -q test_v120_cards.py
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
TOOLKIT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import pipeline_lib as L  # noqa: E402

NL = chr(10)
FENCE = "`" * 3
for _k in ("DSH_HOME", "DSH_SESSION_ID", "DSH_SESSION_JSONL"):
    os.environ.pop(_k, None)
PY_ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
SPEC = os.path.join(TOOLKIT, "04-正本规格-v1.md")


def run(script, *args):
    r = subprocess.run([sys.executable, "-X", "utf8", os.path.join(HERE, script)] + list(args),
                       capture_output=True, env=PY_ENV, timeout=120)
    return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")


def write(path, text, mode="w"):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if mode == "wb":
        with io.open(path, "wb") as f:
            f.write(text)
    else:
        with io.open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)


GOOD = NL.join([
    "## 要你认的三件事 {#commitments}",
    "",
    "1. **改动内容**：按你的意见，时间段从 2022–2025 改成 2021–2025；其余没有改动。",
    "2. **最薄弱的依据**：分县的充电桩数据可能只有部分地市公布过。",
    "3. **最可能出错的地方**：把私人桩和公共桩的数字混在一起。",
    "",
])


def doc(section, before="# 任务计划" + NL + NL + "## 核心判断 {#thesis}" + NL + NL + "下乡活动带动了增长。" + NL + NL):
    return before + section


def problems(body, whole=False):
    return L.commitment_problems(body, whole=whole)[0]


# ═════════════════════════════════════════════════════════ 三点 §⑨-1
class ThreePointsAreReadAndChecked(unittest.TestCase):

    def test_the_new_labels_are_complete_and_give_their_content(self):
        probs, notes, items = L.commitment_problems(doc(GOOD))
        self.assertEqual((probs, notes), ([], []))
        self.assertEqual([it["label"] for it in items], list(L.COMMITMENT_LABELS))
        self.assertEqual(items[1]["content"], "分县的充电桩数据可能只有部分地市公布过。")
        self.assertEqual([it["length"] for it in items], [41, 20, 16])
        parsed = L.parse_commitments(doc(GOOD))
        self.assertEqual([it["content"] for it in parsed], [it["content"] for it in items])
        self.assertEqual([it["text"] for it in parsed], [it["content"] for it in items])

    def test_the_caps_are_inclusive_and_counted_in_code_points(self):
        """每个位置各自的上限(60 / 60 / 80):正好到上限不报,多一个字就报 —— 三个位置都核,
        只核其中一两个的话,另一个位置的上限改了也没人知道(变异测试抓到过)。"""
        contents = ["按你的意见，时间段从 2022–2025 改成 2021–2025；其余没有改动。",
                    "分县的充电桩数据可能只有部分地市公布过。", "把私人桩和公共桩的数字混在一起。"]
        for pos, (content, cap) in enumerate(zip(contents, L.COMMITMENT_CAPS), start=1):
            with self.subTest(position=pos):
                self.assertEqual(problems(doc(GOOD.replace(content, "字" * cap))), [])
                over = problems(doc(GOOD.replace(content, "字" * (cap + 1))))
                self.assertTrue(any("第 %d 条" % pos in p and "%d 字,超过上限 %d" % (cap + 1, cap) in p for p in over), over)
        self.assertEqual(L.COMMITMENT_CAPS, (60, 60, 80))
        # 码位数:一个四字节的字(U+20000)算一个字,不是两个
        astral = GOOD.replace(contents[1], chr(0x20000) * 60)
        self.assertEqual(problems(doc(astral)), [])

    def test_old_labels_and_the_circled_form_are_still_read(self):
        old = NL.join(["## 本轮承诺 {#commitments}", "",
                       "1. **口径承诺(改了什么已批的)**:没有变更。",
                       "2. **最脆证据**:M06 基准。",
                       "3. **最可能错在哪(可验证预测)**:低估同址竞争。", ""])
        probs, notes, items = L.commitment_problems(doc(old))
        self.assertEqual(probs, [])
        self.assertEqual(len(notes), 3, notes)
        self.assertEqual([it["content"] for it in items], ["没有变更。", "M06 基准。", "低估同址竞争。"])
        circled = NL.join(["## 本轮承诺 {#commitments}", "",
                           "- ① 本轮我改了什么用户已批准过的:章节顺序。",
                           "- ② 本结论最脆的一处证据：折扣量级。",
                           "- ③ 没有冒号的一整句", ""])
        probs, notes, items = L.commitment_problems(doc(circled))
        self.assertEqual(probs, [])
        self.assertEqual([it["content"] for it in items], ["章节顺序。", "折扣量级。", "没有冒号的一整句"])
        parsed = L.parse_commitments(doc(circled))
        self.assertEqual([it["label"] for it in parsed], ["①", "②", "③"])
        self.assertEqual(parsed[0]["text"], "本轮我改了什么用户已批准过的:章节顺序。")   # text 照旧
        self.assertEqual(parsed[0]["content"], "章节顺序。")

    def test_each_rule_of_complete_can_fail(self):
        """「齐全」五条(§⑨-1),每条一个反例。"""
        lines = GOOD.split(NL)
        cases = {
            "没有这一节": doc("## 别的一节" + NL + NL + "1. **改动内容**:x" + NL),
            "两条": NL.join(lines[:4]) + NL,
            "四条": GOOD + "4. **补充**:多出来的一条。" + NL,
            "内容空": GOOD.replace("其余没有改动。", "").replace("按你的意见，时间段从 2022–2025 改成 2021–2025；", ""),
            "标签错位": GOOD.replace("**改动内容**", "**最薄弱的依据**", 1),
            "标签不认识": GOOD.replace("**改动内容**", "**改动**", 1),
            "续行": GOOD.replace("其余没有改动。", "其余" + NL + "没有改动。"),
            "说明行": GOOD.replace("## 要你认的三件事 {#commitments}" + NL, "## 要你认的三件事 {#commitments}" + NL + "以下三条进内容 hash。" + NL),
            "混用写法": GOOD.replace("2. **最薄弱的依据**：", "- ② "),
            "圈码乱序": NL.join(["## 本轮承诺 {#commitments}", "", "- ② 改了什么:无", "- ① 最脆:无", "- ③ 最可能错在哪:无", ""]),
        }
        for name, text in cases.items():
            body = text if name == "没有这一节" else doc(text)
            with self.subTest(name):
                self.assertTrue(problems(body), "%s 没报出来" % name)
        self.assertEqual(problems(doc(GOOD)), [])

    def test_line_numbers_are_the_files(self):
        stray = GOOD.replace("其余没有改动。", "其余" + NL + "没有改动。")
        body = doc(stray)
        probs, _, items = L.commitment_problems(body, line_offset=4)   # offset 4 = frontmatter 占的行
        lines = body.split(NL)
        first = next(i for i, line in enumerate(lines) if line.startswith("1. **改动内容**")) + 1
        cont = lines.index("没有改动。") + 1
        self.assertEqual(items[0]["line"], 4 + first)
        self.assertTrue(any("第 %d 行" % (4 + cont) in p for p in probs), probs)

    def test_the_delivery_file_is_the_whole_text_with_one_title_line(self):
        body = NL.join(["# 交付 · 要你认的三件事", "", "1. **改动内容**：第四节标题按你的意见改了。",
                        "2. **最薄弱的依据**：第三节参加县增速只来自 3 个地市。", "3. **最可能出错的地方**：2025 年数据只到三季度。", ""])
        self.assertEqual(problems(body, whole=True), [])
        self.assertTrue(problems(body.replace("# 交付", "前言一句" + NL + "# 交付"), whole=True))
        self.assertTrue(problems("# 交付" + NL + "# 第二个标题" + NL + body, whole=True))
        # 正本一侧不认那一行一级标题(它在 {#commitments} 那一节之外)
        self.assertIsNone(L.parse_commitments(body))


class CommitmentsCheckCommandLine(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v120-commit-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_exit_codes(self):
        good = os.path.join(self.tmp, "task_plan.md")
        write(good, "---" + NL + "kind: task_plan" + NL + "version: 2" + NL
              + "scope_brief: H 省县级,含乡镇,不含村 · 2021–2025 年" + NL + "---" + NL + doc(GOOD))
        code, out = run("commitments_check.py", good)
        self.assertEqual(code, 0, out)
        self.assertIn("齐全", out)
        bad = os.path.join(self.tmp, "dossier.md")
        write(bad, doc(GOOD.replace("**改动内容**", "**口径**", 1)))
        code, out = run("commitments_check.py", bad)
        self.assertEqual(code, 1, out)
        self.assertIn("不认识", out)
        code, out = run("commitments_check.py", os.path.join(self.tmp, "nope.md"))
        self.assertEqual(code, 2, out)
        broken = os.path.join(self.tmp, "outline.md")
        write(broken, "---" + NL + "kind: [outline" + NL + "---" + NL + doc(GOOD))
        code, out = run("commitments_check.py", broken)
        self.assertEqual(code, 2, out)

    def test_delivery_and_json(self):
        lib = os.path.join(self.tmp, "library", "delivery_commitments.md")
        write(lib, "# 交付" + NL + NL + GOOD.split(NL, 2)[2])
        code, out = run("commitments_check.py", lib)            # 没有 --delivery:找 {#commitments},没有
        self.assertEqual(code, 1, out)
        report = os.path.join(self.tmp, "c.json")
        code, out = run("commitments_check.py", lib, "--delivery", "--json", report)
        self.assertEqual(code, 0, out)
        with io.open(report, encoding="utf-8") as f:
            facts = json.load(f)["facts"]
        self.assertEqual([it["length"] for it in facts["items"]], [41, 20, 16])
        self.assertTrue(facts["delivery"])
        code, out = run("commitments_check.py", os.path.join(self.tmp, "library", "x.md"), "--delivery")
        self.assertEqual(code, 2, out)

    def test_old_labels_pass_with_a_warning(self):
        path = os.path.join(self.tmp, "task_plan.md")
        write(path, doc(NL.join(["## 本轮承诺 {#commitments}", "", "1. **口径承诺**:没有变更。",
                                 "2. **最脆证据**:基准。", "3. **最可能错在哪**:竞争。", ""])))
        code, out = run("commitments_check.py", path)
        self.assertEqual(code, 0, out)
        self.assertIn("[WARN]", out)


class DossierCardReason(unittest.TestCase):
    """研判型资料汇编的 gate_verdict.reason 直接上卡(规格 §③「上卡的字段」),≤60 字。"""

    def meta(self, reason, genre="argument"):
        return {"kind": "dossier", "genre": genre, "gate_verdict": {"outcome": "B", "reason": reason}}

    def test_the_cap_and_who_it_applies_to(self):
        self.assertEqual(L.gate_reason_problems(self.meta("理" * 60)), [])
        self.assertTrue(L.gate_reason_problems(self.meta("理" * 61)))
        self.assertTrue(L.gate_reason_problems(self.meta("  ")))
        self.assertTrue(L.gate_reason_problems({"kind": "dossier", "genre": "argument", "gate_verdict": None}))
        self.assertEqual(L.gate_reason_problems(self.meta("理" * 61, genre="survey")), [])
        self.assertEqual(L.gate_reason_problems({"kind": "outline", "genre": "argument"}), [])
        self.assertEqual(L.GATE_REASON_CAP, 60)

    def test_commitments_check_reports_it(self):
        tmp = tempfile.mkdtemp(prefix="v120-reason-")
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "dossier.md")
        head = "---" + NL + "kind: dossier" + NL + "genre: argument" + NL + "gate_verdict:" + NL + "  outcome: B" + NL
        write(path, head + "  reason: 参加活动的县增速更高,但农网改造的贡献分不开" + NL + "---" + NL + doc(GOOD))
        self.assertEqual(run("commitments_check.py", path)[0], 0)
        write(path, head + "  reason: " + "理" * 61 + NL + "---" + NL + doc(GOOD))
        code, out = run("commitments_check.py", path)
        self.assertEqual(code, 1, out)
        self.assertIn("初步结论的理由", out)


# ═════════════════════════════════════════════════════════ 成稿文件 §⑨-2
class DeliverableFiles(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v120-out-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        out = os.path.join(self.tmp, "out")
        write(os.path.join(out, "稿.html"), "<html><body><p>正文</p></body></html>")
        write(os.path.join(out, "B.DOCX"), b"not a zip", "wb")
        write(os.path.join(out, "a.docx"), b"not a zip either", "wb")
        write(os.path.join(out, "~$稿.docx"), b"owner", "wb")             # Word 的锁文件
        write(os.path.join(out, ".hidden.html"), "<html></html>")
        write(os.path.join(out, "session_facts.json"), "{}")
        write(os.path.join(out, "gate-reports", "gate1.html"), "<html></html>")
        os.makedirs(os.path.join(out, "folder.html"))

    def test_which_files_are_the_draft(self):
        self.assertEqual([rel for rel, _ in L.deliverable_paths(self.tmp)],
                         ["out/稿.html", "out/B.DOCX", "out/a.docx"])
        files = L.deliverable_files(self.tmp)
        with io.open(os.path.join(self.tmp, "out", "a.docx"), "rb") as f:
            import hashlib
            self.assertEqual(files["out/a.docx"], hashlib.sha256(f.read()).hexdigest())
        self.assertEqual(L.deliverable_paths(os.path.join(self.tmp, "no-such")), [])

    def test_cite_check_reads_the_same_files_and_skips_the_lock_file(self):
        """product_texts_of 与 deliverable_paths 同一批:锁文件原来被当 docx 读,BadZipFile。"""
        shutil.rmtree(os.path.join(self.tmp, "out"))
        write(os.path.join(self.tmp, "out", "稿.html"), "<html><body><p>本报告含 7 张证据卡。</p></body></html>")
        write(os.path.join(self.tmp, "out", "~$稿.docx"), b"owner", "wb")
        names = [n for n, _ in L.product_texts_of(self.tmp)]
        self.assertEqual(names, ["稿.html"])


# ═════════════════════════════════════════════════════════ 独立复核结果 §⑨-2
def finding(severity="tone_down", round_no=1, status="open", **extra):
    f = {"severity": severity, "round": round_no, "status": status, "location": "第三节",
         "original": "带动全省县域充电设施翻番", "supported": "参加县增速更高", "fix": "改成参加活动的县增速更高"}
    if severity == "checked_ok":
        f.update(status=None, fix=None)
    f.update(extra)
    return f


class ReviewResult(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v120-review-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        write(os.path.join(self.tmp, "out", "稿.html"), "<html>成稿</html>")
        write(os.path.join(self.tmp, "out", "稿.docx"), b"PK docx bytes", "wb")
        self.result = os.path.join(self.tmp, "review", "result.json")

    def round_files(self, n, findings, report=True):
        """v1.2.1 复核:复核开始前先 --files <n> 记下成稿(这一轮已经开始复核、成稿没变时它什么都不改),再写发现与报告。"""
        run("review_result.py", self.tmp, "--files", str(n))
        write(os.path.join(self.tmp, "review", "findings-%d.json" % n), json.dumps(findings, ensure_ascii=False))
        if report:
            write(os.path.join(self.tmp, "review", "report-%d.md" % n), "# 第 %d 轮" % n + NL)

    def seal(self, n):
        return run("review_result.py", self.tmp, "--seal", str(n))

    def check(self):
        return run("review_result.py", self.tmp, "--check")

    def load(self):
        with io.open(self.result, encoding="utf-8") as f:
            return json.load(f)

    def test_seal_writes_hashes_and_counts_and_check_passes(self):
        self.round_files(1, [finding(), finding("checked_ok"), finding("checked_ok")])
        code, out = self.seal(1)
        self.assertEqual(code, 0, out)
        result = self.load()
        self.assertEqual(L.review_result_problems(result), [])
        self.assertEqual(result["counts"], {"must_fix": 0, "tone_down": 1, "citation_or_format": 0, "checked_ok": 2})
        self.assertEqual(result["files"], L.deliverable_files(self.tmp))
        self.assertEqual((result["kind"], result["format"], result["round"], result["report"]),
                         ("review_result", 1, 1, "review/report-1.md"))
        self.assertTrue(L._tz_aware(result["reviewed_at"]))
        code, out = self.check()
        self.assertEqual(code, 0, out)

    def test_check_fails_when_the_draft_changes_after_the_review(self):
        self.round_files(1, [finding()])
        self.assertEqual(self.seal(1)[0], 0)
        cases = [
            ("改过", lambda: write(os.path.join(self.tmp, "out", "稿.html"), "<html>成稿,改了一个字</html>")),
            ("多了", lambda: write(os.path.join(self.tmp, "out", "第二份.html"), "<html></html>")),
            ("少了", lambda: os.remove(os.path.join(self.tmp, "out", "稿.docx"))),
        ]
        for name, act in cases:
            with self.subTest(name):
                snapshot = {rel: open(p, "rb").read() for rel, p in L.deliverable_paths(self.tmp)}
                act()
                code, out = self.check()
                self.assertEqual(code, 1, out)
                self.assertIn("对不上", out)
                for p in L.deliverable_paths(self.tmp):     # 复原
                    if p[0] not in snapshot:
                        os.remove(p[1])
                for rel, data in snapshot.items():
                    write(os.path.join(self.tmp, *rel.split("/")), data, "wb")
                self.assertEqual(self.check()[0], 0, "复原之后应当又对得上")

    def test_must_fix_counts_only_what_is_still_there(self):
        self.round_files(1, [finding("must_fix"), finding("citation_or_format")])
        self.assertEqual(self.seal(1)[0], 0)
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("还有 1 处必须改", out)
        # 第 2 轮:改过成稿(重新生成),上一轮的两条核过已改好
        write(os.path.join(self.tmp, "out", "稿.html"), "<html>改好的成稿</html>")
        self.assertEqual(self.check()[0], 1)
        self.round_files(2, [finding("must_fix", 1, "fixed"), finding("citation_or_format", 1, "fixed"),
                             finding("checked_ok", 2)])
        code, out = self.seal(2)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.load()["counts"], {"must_fix": 0, "tone_down": 0, "citation_or_format": 1, "checked_ok": 1})
        self.assertEqual(self.check()[0], 0)

    def test_seal_refuses_and_writes_nothing(self):
        self.round_files(1, [finding()])
        self.assertEqual(self.seal(1)[0], 0)
        before = open(self.result, "rb").read()
        bad_findings = {
            "不认识的档": [finding("serious")],
            "本轮的写成已改": [finding(round_no=2, status="fixed")],
            "缺处置意见": [finding(fix="")],
            "抽查无误带 status": [dict(finding("checked_ok"), status="open")],
            "多了键": [dict(finding(), note="x")],
            "缺键": [{k: v for k, v in finding().items() if k != "location"}],
            "轮次越界": [finding(round_no=3)],
            "不是列表": {"findings": []},
        }
        for name, findings in bad_findings.items():
            with self.subTest(name):
                self.round_files(2, findings)
                code, out = self.seal(2)
                self.assertEqual(code, 2, out)
                self.assertEqual(open(self.result, "rb").read(), before)
        # 报告不在
        os.remove(os.path.join(self.tmp, "review", "report-2.md"))
        self.round_files(2, [finding()], report=False)
        self.assertEqual(self.seal(2)[0], 2)
        # 轮次比现有结果小
        self.round_files(2, [finding()])
        self.assertEqual(self.seal(2)[0], 0)
        code, out = self.seal(1)
        self.assertEqual(code, 2, out)
        self.assertIn("不能用第 1 轮盖掉", out)
        # 同一轮封过之后成稿又改了:不许重记这一轮的成稿,也不许封
        write(os.path.join(self.tmp, "out", "稿.html"), "<html>又改了</html>")
        code, out = run("review_result.py", self.tmp, "--files", "2")
        self.assertEqual(code, 2, out)
        self.assertIn("--files 3", out)
        code, out = self.seal(2)
        self.assertEqual(code, 2, out)
        # 没有成稿文件
        shutil.rmtree(os.path.join(self.tmp, "out"))
        self.round_files(3, [finding()])
        self.assertEqual(self.seal(3)[0], 2)

    def test_check_without_a_result_or_with_a_broken_one(self):
        code, out = self.check()
        self.assertEqual(code, 2, out)
        write(self.result, "{not json")
        self.assertEqual(self.check()[0], 2)
        write(self.result, json.dumps({"kind": "review_result"}))
        self.assertEqual(self.check()[0], 2)

    def test_result_problems_can_fail(self):
        good = {"kind": "review_result", "format": 1, "round": 1, "reviewed_at": "2026-10-05T15:20:00-04:00",
                "report": "review/report-1.md", "files": {"out/稿.html": "a" * 64},
                "counts": {"must_fix": 0, "tone_down": 1, "citation_or_format": 0, "checked_ok": 0},
                "findings": [finding()]}
        self.assertEqual(L.review_result_problems(good), [])
        bad = {
            "kind": dict(good, kind="review"),
            "format": dict(good, format=2),
            "format 是真值": dict(good, format=True),
            "多了键": dict(good, verdict="pass"),
            "round": dict(good, round=0),
            "时间没时区": dict(good, reviewed_at="2026-10-05T15:20:00"),
            "files 空": dict(good, files={}),
            "路径不在 out/": dict(good, files={"drafts/稿.md": "a" * 64}),
            "子文件夹": dict(good, files={"out/gate-reports/x.html": "a" * 64}),
            "hash 不对": dict(good, files={"out/稿.html": "A" * 64}),
            "计数手写错": dict(good, counts=dict(good["counts"], tone_down=0)),
            "计数少键": dict(good, counts={"must_fix": 0}),
            "计数是真值": dict(good, counts=dict(good["counts"], must_fix=False)),
            "findings 不是列表": dict(good, findings={}),
            "整个不是映射": [],
        }
        for name, result in bad.items():
            with self.subTest(name):
                self.assertTrue(L.review_result_problems(result), name)

    def test_applies_is_by_the_bytes(self):
        files = L.deliverable_files(self.tmp)
        self.assertEqual(L.review_applies(self.tmp, {"files": files})[0], True)
        self.assertEqual(L.review_applies(self.tmp, {"files": dict(files, **{"out/稿.html": "0" * 64})})[0], False)
        self.assertEqual(L.review_applies(self.tmp, {"files": None})[0], False)


# ═════════════════════════════════════════════════════════ 规格的示例与实现钉在一起
def spec_blocks(lang):
    with io.open(SPEC, encoding="utf-8") as f:
        lines = f.read().split(NL)
    out, block = [], None
    for line in lines:
        if block is None:
            if line.strip() == FENCE + lang:
                block = []
            continue
        if line.strip() == FENCE:
            out.append(NL.join(block))
            block = None
        else:
            block.append(line)
    return out


class SpecExamplesAreValid(unittest.TestCase):

    def test_the_three_point_examples_are_complete(self):
        blocks = spec_blocks("markdown")
        self.assertEqual(len(blocks), 2, "规格 §⑨-1 与 §⑨-3 各一个 markdown 示例")
        self.assertEqual(L.commitment_problems(blocks[0]), ([], [], L.commitment_problems(blocks[0])[2]))
        self.assertEqual(L.commitment_problems(blocks[1], whole=True)[0], [])

    def test_the_review_result_example_is_valid_once_its_placeholders_are_filled(self):
        blocks = [b for b in spec_blocks("json") if '"review_result"' in b]
        self.assertEqual(len(blocks), 1)
        result = json.loads(blocks[0])
        result["files"] = {path: "a" * 64 for path in result["files"]}
        self.assertEqual(L.review_result_problems(result), [])

    def test_the_spec_version_matches_its_latest_revision(self):
        with io.open(SPEC, encoding="utf-8") as f:
            text = f.read()
        head = re.search(r"\*\*版本:(v\d+\.\d+\.\d+)\*\*", text).group(1)
        log = text[text.index("## 修订记录"):]
        latest = re.search(r"\*\*(v\d+\.\d+\.\d+) · ", log).group(1)
        self.assertEqual(head, latest)
        self.assertGreaterEqual(tuple(int(x) for x in head[1:].split(".")), (1, 2, 0))


class ScriptsTheSkillsNameExist(unittest.TestCase):
    """skill 与 AGENTS.md 里点名的每个脚本都在工具包里 —— 改名或删脚本时这里先红。"""

    SCRIPT_RE = re.compile(r"scripts/([A-Za-z0-9_]+\.(?:py|mjs))")

    def named(self):
        found = {}
        skills = os.path.join(TOOLKIT, ".dsh", "skills")
        paths = [os.path.join(skills, d, "SKILL.md") for d in sorted(os.listdir(skills))]
        for path in paths + [os.path.join(TOOLKIT, "AGENTS.md")]:
            with io.open(path, encoding="utf-8") as f:
                for name in self.SCRIPT_RE.findall(f.read()):
                    found.setdefault(name, os.path.relpath(path, TOOLKIT))
        return found

    def test_every_named_script_exists(self):
        found = self.named()
        for must in ("commitments_check.py", "review_result.py", "stamp.py", "pipeline_status.py", "session_facts.mjs"):
            self.assertIn(must, found)
        missing = {n: where for n, where in found.items() if not os.path.isfile(os.path.join(HERE, n))}
        self.assertEqual(missing, {})

    def test_the_check_can_fail(self):
        self.assertEqual(self.SCRIPT_RE.findall("跑 `python -X utf8 <toolkit>/scripts/no_such_tool.py`"), ["no_such_tool.py"])
        self.assertFalse(os.path.isfile(os.path.join(HERE, "no_such_tool.py")))


if __name__ == "__main__":
    unittest.main()
