# -*- coding: utf-8 -*-
"""v1.2.1 · 独立核查后的修补(界面实装工单 05 A3 的复核意见 H1、M1–M3、L4–L7)。

- H1 cite_check 读成稿只走 deliverable_paths:Word 开着成稿(out/ 里有 `~$稿.docx` 锁文件)时整份检查跑完、不崩;
- M1 复核结果绑在开始复核时记下的成稿上(`--files <n>` → review/files-<n>.json),复核期间成稿改过就不封;
- M2 第 n 轮必须带上第 n−1 轮还开着的每一条发现(round + location + original);
- M3 提纲的合计字数只加最上一级的节,没有资料卡片支撑的节不数方法说明与综合收束节(D16 的数);
- L4 stance 的界面叫法与词表一致;L5 三点的第一条是空的时不吞下一行;L6 三点与上卡理由的纯文字判据;
- L7 改流程「先不确认」后 revision_log 的 v 不能超过没被确认的那一版,否则下一次落章被拒。
再复核(同一版):
- N1 第 n 轮开始复核之后(有了 findings-<n>.json 或 report-<n>.md)--files n 不再重记;成稿变了就 --abandon n、
  开第 n+1 轮重新复核,对照的是最后一个封存的轮次(不会卡死);
- N2 带过来的发现不许改档;成稿和上一个封存的轮次一模一样时,那一轮还开着的不能标成已改好;
- L3 任务计划「预注册结论空间」表第三栏「对应表述」只能写 成立 / 部分成立 / 不成立(commitments_check 核)。
A2 联调(同一版):
- (a) 三点的两种读法(齐全的判据、内容的读法)空白一样认:Python 的 Unicode 空白,不跨换行;
- (b) cite_check ⑤ 不把草稿 / 提纲自己的节号(D16:三 → 3.1 增速对比)当成查无出处的数字,正文里同一个数照样要出处;
- (e) constraints.words_max 只认正整数,8000.0 这种写法算读不出,字数段省掉。

跑法:python -X utf8 -m pytest -q test_v121_verifier.py
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pipeline_lib as L  # noqa: E402

NL = chr(10)
for _k in ("DSH_HOME", "DSH_SESSION_ID", "DSH_SESSION_JSONL"):
    os.environ.pop(_k, None)
PY_ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
GLOSSARY = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "wording", "glossary.json"))
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def run(script, *args):
    r = subprocess.run([sys.executable, "-X", "utf8", os.path.join(HERE, script)] + list(args),
                       capture_output=True, env=PY_ENV, timeout=180)
    return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if isinstance(data, bytes):
        with io.open(path, "wb") as f:
            f.write(data)
    else:
        with io.open(path, "w", encoding="utf-8", newline="") as f:
            f.write(data)


def docx_paragraphs(paras):
    """一段一个 w:p 的最小 docx。"""
    buf = io.BytesIO()
    body = "".join("<w:p><w:r><w:t>%s</w:t></w:r></w:p>" % p for p in paras)
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", '<w:document xmlns:w="%s"><w:body>%s</w:body></w:document>' % (W, body))
        z.writestr("word/_rels/document.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
    return buf.getvalue()


def docx_bytes(text):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", '<w:document xmlns:w="%s"><w:body><w:p><w:r><w:t>%s</w:t></w:r></w:p>'
                   '</w:body></w:document>' % (W, text))
        z.writestr("word/_rels/document.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
    return buf.getvalue()


# D16 的提纲:三 2200 字里含 3.1 的 1200 字,合计 8000;一是方法说明,五是综合收束。
D16_OUTLINE = NL.join([
    "---", "kind: outline", "version: 3", "genre: argument", "total_words: 8000", "tolerance: 0.1", "---",
    "## 一、问题与判断 {id: s1, words: 800}", "- evidence_mode: methodology",
    "## 二、县域充电设施是怎么变的 {id: s2, words: 1800}", "- evidence: [S01, S02, S03, S04, S05]",
    "## 三、下乡活动起了多大作用 {id: s3, words: 2200}", "- evidence: [S06, S07, S08, S09, S10, S11]",
    "### 3.1 增速对比 {id: s3-1, words: 1200}", "- evidence: [S06, S07]",
    "## 四、其他推动因素:农网改造与服务区 {id: s4, words: 1600}", "- evidence: [C01, C02, C03, C04]",
    "## 五、政策建议 {id: s5, words: 1600}", "- evidence_mode: synthesis", ""])


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v121v-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def at(self, *parts):
        return os.path.join(self.tmp, *parts)


# ═════════════════════════════════════════════════════════ H1
class CiteCheckWithWordOpen(Tmp):
    """Word 开着成稿时,out/ 里多一个 `~$<稿>.docx`(不是 zip)。cite_check 要整份跑完。"""

    def project(self, stem):
        write(self.at("references.yaml"), "entries: []" + NL)
        write(self.at("drafts", stem + ".md"), "# 标题" + NL + NL + "正文。" + NL)
        write(self.at("out", stem + ".html"), "<html><body><p>正文。</p></body></html>")
        write(self.at("out", stem + ".docx"), docx_bytes("正文。"))
        write(self.at("out", "~$" + stem + ".docx"), b"owner file, not a zip")
        write(self.at("outline.md"), D16_OUTLINE)

    def test_the_whole_check_runs_and_ignores_the_lock_file(self):
        for stem in ("稿", "draft"):     # 汉字名:锁文件排在前面,④ 先读到它;字母名:⑫ 逐个读到它
            with self.subTest(stem=stem):
                shutil.rmtree(self.tmp, True)
                os.makedirs(self.tmp)
                self.project(stem)
                code, out = run("cite_check.py", "--project", self.tmp)
                self.assertNotIn("Traceback", out, out)
                self.assertNotIn("~$", out)
                for check in ("文内⊆参考", "跨格式相等", "双格式各自核", "大纲预算"):
                    self.assertIn(check, out)
                self.assertIn("[PASS] 跨格式相等", out)
                self.assertIn("[PASS] 大纲预算: Σ=8000 / total=8000", out)     # M3:⑨ 只加最上一级


# ═════════════════════════════════════════════════════════ M1 / M2
def finding(severity="tone_down", round_no=1, status="open", location="第三节", original="带动全省县域充电设施翻番"):
    f = {"severity": severity, "round": round_no, "status": status, "location": location, "original": original,
         "supported": "参加县增速更高", "fix": "改成参加活动的县增速更高"}
    if severity == "checked_ok":
        f.update(status=None, fix=None)
    return f


class ReviewRounds(Tmp):

    def setUp(self):
        super().setUp()
        write(self.at("out", "稿.html"), "<html>成稿</html>")
        write(self.at("out", "稿.docx"), b"PK docx")

    def round_files(self, n, findings):
        write(self.at("review", "findings-%d.json" % n), json.dumps(findings, ensure_ascii=False))
        write(self.at("review", "report-%d.md" % n), "# 第 %d 轮" % n + NL)

    def r(self, *args):
        return run("review_result.py", self.tmp, *[str(a) for a in args])

    def test_m1_the_result_binds_to_the_draft_recorded_at_the_start(self):
        self.round_files(1, [finding()])
        code, out = self.r("--seal", 1)                                         # 没先记下成稿
        self.assertEqual(code, 2, out)
        self.assertIn("--files 1", out)
        code, out = self.r("--files", 1)                     # N1:已经复核了才补记,记下的不一定是被复核的那一版
        self.assertEqual(code, 2, out)
        self.assertIn("--abandon 1", out)
        self.assertFalse(os.path.exists(self.at("review", "files-1.json")))
        self.assertEqual(self.r("--abandon", 1)[0], 0)
        self.assertEqual(self.r("--files", 2)[0], 0)                            # 复核开始前记下
        with io.open(self.at("review", "files-2.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["files"], L.deliverable_files(self.tmp))
        self.round_files(2, [finding(round_no=2)])
        write(self.at("out", "稿.html"), "<html>复核期间改了一个字</html>")
        code, out = self.r("--seal", 2)
        self.assertEqual(code, 2, out)
        self.assertIn("复核期间成稿改过", out)
        self.assertFalse(os.path.exists(self.at("review", "result.json")))
        # 原来这里「对改过的成稿重记第 2 轮、照样封」—— 结果绑到没人复核过的那一版上(N1)。现在不许重记,要开新的一轮。
        code, out = self.r("--files", 2)
        self.assertEqual(code, 2, out)
        for hint in ("--abandon 2", "--files 3"):
            self.assertIn(hint, out)
        self.assertEqual(self.r("--abandon", 2)[0], 0)
        self.assertEqual(self.r("--files", 3)[0], 0)
        self.round_files(3, [finding(round_no=3)])
        self.assertEqual(self.r("--seal", 3)[0], 0)
        self.assertTrue(os.path.exists(self.at("review", "result-3.json")))    # 每轮的存档
        write(self.at("out", "稿.html"), "<html>封存之后又改了</html>")
        code, out = self.r("--files", 3)                                        # 封过的一轮不许重记
        self.assertEqual(code, 2, out)
        self.assertIn("--files 4", out)

    def test_m2_open_findings_of_the_last_round_must_be_carried(self):
        must = finding("must_fix", location="第二节第一段", original="已建成全国唯一的示范县")
        tone = finding()
        self.r("--files", 1)
        self.round_files(1, [must, tone, finding("checked_ok", location="第四节")])
        self.assertEqual(self.r("--seal", 1)[0], 0)
        write(self.at("out", "稿.html"), "<html>改过的成稿</html>")
        self.r("--files", 2)
        # 第 2 轮把必须改的那一条丢了:原来照样封、--check 说可以交付
        self.round_files(2, [dict(tone, status="fixed")])
        code, out = self.r("--seal", 2)
        self.assertEqual(code, 2, out)
        self.assertIn("没有带上", out)
        self.assertEqual(self.r("--check")[0], 1)
        # 位置改了一个字也不算带上
        self.round_files(2, [dict(must, status="fixed", location="第二节"), dict(tone, status="fixed")])
        self.assertEqual(self.r("--seal", 2)[0], 2)
        # 两条都带上(改好了)才封
        self.round_files(2, [dict(must, status="fixed"), dict(tone, status="fixed")])
        code, out = self.r("--seal", 2)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.r("--check")[0], 0)
        # 上一轮的存档不在,下一轮不封
        os.remove(self.at("review", "result-2.json"))
        write(self.at("out", "稿.html"), "<html>第三版</html>")
        self.r("--files", 3)
        self.round_files(3, [])
        code, out = self.r("--seal", 3)
        self.assertEqual(code, 2, out)
        self.assertIn("先封好上一轮", out)

    def test_m2_fixed_and_checked_findings_need_not_be_carried(self):
        self.r("--files", 1)
        self.round_files(1, [finding(), finding("checked_ok", location="第四节")])
        self.r("--seal", 1)
        write(self.at("out", "稿.html"), "<html>第二版</html>")                 # N2:成稿改了才能标成改好
        self.r("--files", 2)
        self.round_files(2, [dict(finding(), status="fixed")])
        self.assertEqual(self.r("--seal", 2)[0], 0)
        write(self.at("out", "稿.html"), "<html>第三版</html>")
        self.r("--files", 3)
        self.round_files(3, [])                                                 # 第 2 轮没有开着的了
        code, out = self.r("--seal", 3)
        self.assertEqual(code, 0, out)

    def record_bytes(self, n):
        with io.open(self.at("review", "files-%d.json" % n), "rb") as f:
            return f.read()

    def test_n1_a_round_under_review_is_not_re_recorded(self):
        self.assertEqual(self.r("--files", 1)[0], 0)
        before = self.record_bytes(1)
        for started in ("findings", "report"):            # 有了发现,或者只有报告,都算开始复核了
            with self.subTest(started=started):
                for name in ("findings-1.json", "report-1.md"):
                    if os.path.exists(self.at("review", name)):
                        os.remove(self.at("review", name))
                write(self.at("out", "稿.html"), "<html>成稿</html>")
                if started == "findings":
                    write(self.at("review", "findings-1.json"), json.dumps([finding("must_fix")], ensure_ascii=False))
                else:
                    write(self.at("review", "report-1.md"), "# 第 1 轮" + NL)
                code, out = self.r("--files", 1)                               # 成稿没变:什么都不改
                self.assertEqual(code, 0, out)
                self.assertEqual(self.record_bytes(1), before)
                write(self.at("out", "稿.html"), "<html>复核期间改了</html>")
                code, out = self.r("--files", 1)                               # 变了:不许重记
                self.assertEqual(code, 2, out)
                for hint in ("--abandon 1", "--files 2", "重新复核"):
                    self.assertIn(hint, out)
                self.assertEqual(self.record_bytes(1), before)
        self.round_files(1, [finding("must_fix")])
        code, out = self.r("--seal", 1)                                         # 验证者的路子:重记、封、--check 0
        self.assertEqual(code, 2, out)
        for hint in ("--abandon 1", "--files 2"):
            self.assertIn(hint, out)
        self.assertEqual(self.r("--check")[0], 2)                               # 没有结果
        self.assertEqual(self.r("--abandon", 1)[0], 0)
        self.assertTrue(os.path.exists(self.at("review", "findings-1.json")))  # 发现与报告留在盘上
        for draft in ("<html>复核期间改了</html>", "<html>成稿</html>"):       # 改回开始时那一版也一样:放弃了就不再用
            write(self.at("out", "稿.html"), draft)
            for args in (("--files", 1), ("--seal", 1)):
                code, out = self.r(*args)
                self.assertEqual(code, 2, out)
                self.assertIn("--files 2", out)
        self.assertEqual(self.r("--files", 2)[0], 0)
        self.round_files(2, [finding("must_fix", round_no=2)])
        self.assertEqual(self.r("--seal", 2)[0], 0)
        code, out = self.r("--abandon", 2)                                      # 封存过的不能放弃
        self.assertEqual(code, 2, out)
        self.assertEqual(self.r("--check")[0], 1)                               # 必须改那一条还开着

    def test_n1_after_an_abandoned_round_the_last_sealed_round_is_the_reference(self):
        must = finding("must_fix", location="第二节第一段", original="已建成全国唯一的示范县")
        self.r("--files", 1)
        self.round_files(1, [must])
        self.assertEqual(self.r("--seal", 1)[0], 0)
        write(self.at("out", "稿.html"), "<html>第二版</html>")
        self.r("--files", 2)
        self.round_files(2, [dict(must, status="fixed")])
        write(self.at("out", "稿.html"), "<html>复核期间又改了</html>")
        self.assertEqual(self.r("--seal", 2)[0], 2)
        self.r("--files", 3)
        self.round_files(3, [dict(must, status="fixed")])
        code, out = self.r("--seal", 3)                                         # 第 2 轮没封也没放弃
        self.assertEqual(code, 2, out)
        self.assertIn("--abandon 2", out)
        self.assertEqual(self.r("--abandon", 2)[0], 0)                          # 放弃之后就能往下走,不卡死
        self.round_files(3, [])
        code, out = self.r("--seal", 3)                                         # 对照的是第 1 轮:那一条照样要带上
        self.assertEqual(code, 2, out)
        self.assertIn("没有带上", out)
        self.round_files(3, [dict(must, status="fixed")])
        code, out = self.r("--seal", 3)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.r("--check")[0], 0)

    def test_n2_a_carried_finding_keeps_its_tier_and_fixed_needs_a_changed_draft(self):
        must = finding("must_fix", location="第二节第一段", original="已建成全国唯一的示范县")
        self.r("--files", 1)
        self.round_files(1, [must])
        self.assertEqual(self.r("--seal", 1)[0], 0)
        self.r("--files", 2)                                                    # 成稿没动
        for name, carried, hint in (("改了档,还开着", dict(must, severity="tone_down"), "不许改档"),
                                    ("改了档,标成改好", dict(must, severity="tone_down", status="fixed"), "不许改档"),
                                    ("成稿没变却标成改好", dict(must, status="fixed"), "成稿没改")):
            with self.subTest(name):
                self.round_files(2, [carried])
                code, out = self.r("--seal", 2)
                self.assertEqual(code, 2, out)
                self.assertIn(hint, out)
                code, out = self.r("--check")                                   # 结果还是第 1 轮的
                self.assertEqual(code, 1, out)
                self.assertIn("还有 1 处必须改", out)
        self.round_files(2, [must])                                             # 照原档带着、还开着:封得了
        self.assertEqual(self.r("--seal", 2)[0], 0)
        self.assertEqual(self.r("--check")[0], 1)
        write(self.at("out", "稿.html"), "<html>改好的成稿</html>")
        self.r("--files", 3)
        self.round_files(3, [dict(must, severity="tone_down", status="fixed")])  # 成稿改了,档位照样不能动
        self.assertEqual(self.r("--seal", 3)[0], 2)
        self.round_files(3, [dict(must, status="fixed")])
        code, out = self.r("--seal", 3)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.r("--check")[0], 0)
        write(self.at("out", "稿.html"), "<html>第四版</html>")
        self.r("--files", 4)
        self.round_files(4, [finding(round_no=2, status="fixed")])              # 早先没有的,不能凭空标成改好
        code, out = self.r("--seal", 4)
        self.assertEqual(code, 2, out)
        self.assertIn("找不到", out)
        same_place = [dict(must, status="fixed"), finding("citation_or_format", location=must["location"],
                                                          original=must["original"], round_no=4)]
        self.round_files(4, same_place)                                        # 同一句上两档各一条:按档分开核
        self.assertEqual(self.r("--seal", 4)[0], 0)


# ═════════════════════════════════════════════════════════ A2 联调 (b)
class NumberedHeadings(Tmp):
    """⑤ 无出处数字把成稿里的节号「3.1 增速对比」当成查无出处的数字(标题前头紧挨着上一段的末尾)。D16:三 → 3.1。"""

    def project(self, prose):
        write(self.at("references.yaml"), "entries: []" + NL)
        write(self.at("outline.md"), D16_OUTLINE)
        write(self.at("drafts", "稿.md"), NL.join(["# 下乡活动与县域充电设施", "", "## 三、下乡活动起了多大作用", "",
                                                  "参加活动的县增速更高。", "", "### 3.1 增速对比", "", prose, ""]))
        write(self.at("out", "稿.html"), NL.join([
            "<html><body><h1>下乡活动与县域充电设施</h1>",
            '<h2><span class="doc-secnum">3</span>三、下乡活动起了多大作用</h2>',
            "<p>参加活动的县增速更高。</p>", "<h3>3.1 增速对比</h3>", "<p>%s</p>" % prose, "</body></html>"]))
        write(self.at("out", "稿.docx"), docx_paragraphs(["下乡活动与县域充电设施", "三、下乡活动起了多大作用",
                                                        "参加活动的县增速更高。", "3.1 增速对比", prose]))

    def number_lines(self):
        code, out = run("cite_check.py", "--project", self.tmp)
        self.assertNotIn("Traceback", out, out)
        return [line for line in out.splitlines() if "无出处数字" in line], out

    def test_the_section_number_passes_and_the_same_number_in_prose_still_needs_a_source(self):
        self.project("参加县的公共桩增长得更快。")
        lines, out = self.number_lines()
        self.assertEqual(len(lines), 1, out)
        self.assertIn("[PASS]", lines[0])
        self.project("参加县比全省平均高 3.1 个百分点。")                    # 同一个数写在正文里:要出处
        lines, out = self.number_lines()
        fails = [line for line in lines if "[FAIL]" in line]
        self.assertEqual(len(fails), 2, out)                                  # 网页版、Word 版各一处 —— 正文那个,不是标题
        self.assertTrue(all("'3.1'" in line for line in fails), out)

    def test_what_counts_as_a_section_number(self):
        import cite_check as C
        heads = C.section_headings([D16_OUTLINE, "#### 3.1.2 **分县**对比" + NL])
        self.assertEqual(heads, {("3.1", "增速对比"), ("3.1.2", "分县对比")})
        tail = "参加活动的县增速更高。" + NL
        self.assertEqual(C.substantive_numbers(tail + "3.1 增速对比", heads), [])
        self.assertEqual(C.substantive_numbers(tail + "3.1.2 分县对比", heads), [])
        self.assertEqual([t for t, _ in C.substantive_numbers(tail + "3.1 增速对比", ())], ["3.1"])   # 原来:报
        for prose in ("比全省平均高 3.1 个百分点", tail + "3.1 别的标题", tail + "3.15 增速对比"):
            with self.subTest(prose=prose):
                self.assertTrue(C.substantive_numbers(prose, heads), prose)


# ═════════════════════════════════════════════════════════ M3
class OutlineNumbers(Tmp):

    def nodes(self, text=D16_OUTLINE):
        write(self.at("outline.md"), text)
        sys.path.insert(0, HERE)
        from outline_check import parse_outline_md
        return parse_outline_md(self.at("outline.md"))[1]

    def test_d16_total_is_8000_and_nothing_is_unsupported(self):
        nodes = self.nodes()
        self.assertEqual([n["level"] for n in nodes], [2, 2, 2, 3, 2, 2])
        self.assertEqual(L.outline_total_words(nodes), 8000)
        self.assertEqual(sum(n["words"] for n in nodes), 9200)                  # 原来的算法
        self.assertEqual(L.outline_unsupported(nodes), [])
        code, out = run("outline_check.py", self.at("outline.md"))
        self.assertEqual(code, 0, out)
        self.assertIn("Σwords=8000 / total=8000 偏差 0% 在容差内", out)
        self.assertNotIn("[WARN] 预算", out)

    def test_parents_without_words_and_what_counts_as_unsupported(self):
        nodes = [{"id": "a", "level": 2, "words": None, "evidence": ["S1"]},
                 {"id": "a1", "level": 3, "words": 500, "evidence": []},                       # 空、没写方式 → 算
                 {"id": "a2", "level": 3, "words": "700 字", "evidence": [], "evidence_mode": "pending_evidence"},
                 {"id": "b", "level": 2, "words": 1000, "evidence": [], "evidence_mode": "methodology"},
                 {"id": "c", "level": 2, "words": None, "evidence": [], "evidence_mode": "synthesis"}]
        self.assertEqual(L.outline_total_words(nodes), 500 + 700 + 1000 + 0)
        self.assertEqual(L.outline_unsupported(nodes), ["a1", "a2"])
        self.assertEqual(L.outline_total_words([{"id": "x", "words": 300}, {"id": "y", "words": 200}]), 500)   # json 没有 level


# ═════════════════════════════════════════════════════════ L4 / L5 / L6 / L7
class SmallFixes(Tmp):

    def test_l4_stance_labels_match_the_design_and_the_glossary(self):
        self.assertEqual(L.STANCE_LABELS, {"support": "支持判断", "counter": "不支持", "mixed": "背景资料", "gap": "资料缺口"})
        self.assertEqual(set(L.STANCE_LABELS), L.STANCES)
        if not os.path.exists(GLOSSARY):
            self.skipTest("wording/ 不在,无从比对词表")
        with io.open(GLOSSARY, encoding="utf-8") as f:
            entries = json.load(f)["entries"]
        # 词表里 gap 有两处:抓取五态的 gap(没有这项数据)和资料卡片的 gap(资料缺口,条目 id 也叫 gap)。卡片的是后者。
        gap = [t for e in entries if e["id"] == "gap" for t in e["terms"] if "gap" in t["internal"]]
        self.assertEqual([t["zh"] for t in gap], [L.STANCE_LABELS["gap"]])

    def test_l5_an_empty_first_point_does_not_swallow_the_next_line(self):
        for first in ("1. **改动内容**:", "1. **改动内容**：   "):
            body = NL.join(["## 要你认的三件事 {#commitments}", "", first,
                            "2. **最薄弱的依据**:分县数据不全。", "3. **最可能出错的地方**:口径混了。", ""])
            items = L.parse_commitments(body)
            self.assertEqual([it["content"] for it in items], ["", "分县数据不全。", "口径混了。"], first)
            self.assertTrue(any("内容是空的" in p for p in L.commitment_problems(body)[0]))
        circled = NL.join(["## 本轮承诺 {#commitments}", "", "- ①", "- ② 最脆:基准。", "- ③ 最可能错在哪:上限。", ""])
        self.assertEqual([it["content"] for it in L.parse_commitments(circled)], ["", "基准。", "上限。"])

    def test_l6_points_and_the_card_reason_must_be_plain_text(self):
        good = ["这是第 1 版。", "分县数据不全。", "口径混了。"]
        for bad, name in (("**没有改动**", "加粗"), ("见 [[R012]]", "引文占位"), ("见 [年报](x)", "markdown 链接"),
                          ("见 https://example.com/a", "网址"), ("见 http://example.com/a", "网址")):
            with self.subTest(bad=bad):
                body = NL.join(["## 要你认的三件事 {#commitments}", "", "1. **改动内容**:" + bad,
                                "2. **最薄弱的依据**:" + good[1], "3. **最可能出错的地方**:" + good[2], ""])
                self.assertTrue(any("不是纯文字" in p and name in p for p in L.commitment_problems(body)[0]))
                meta = {"kind": "dossier", "genre": "argument", "gate_verdict": {"reason": bad}}
                self.assertTrue(any("不是纯文字" in p for p in L.gate_reason_problems(meta)))
        clean = NL.join(["## 要你认的三件事 {#commitments}", "", "1. **改动内容**:" + good[0],
                         "2. **最薄弱的依据**:" + good[1], "3. **最可能出错的地方**:" + good[2], ""])
        self.assertEqual(L.commitment_problems(clean)[0], [])

    def test_l3_the_preregistration_phrases_are_checked(self):
        """资料汇编卡上的初步结论照「对应表述」那一栏显示:只能写 成立 / 部分成立 / 不成立。"""
        head = "| outcome | 判定条件 | 对应表述 |" + NL + "|---|---|---|"
        rows = ["| A | 参加县增速明显更高 | 成立 |", "| B | 更高但主要来自其他政策 | 部分成立 |", "| C | 没有明显差别 | 不成立 |"]

        def plan(table):
            return NL.join(["---", "kind: task_plan", "version: 1", "genre: argument", "scope_brief: 县域公共充电设施",
                            "---", "# 任务计划", "", "## 预注册结论空间 {#preregistration}", "", table, "",
                            "## 证据对接窗口条件 {#evidence_gate}", "", "| 不算 | 写法 | 别的说法 |", "|---|---|---|",
                            "| x | y | 基本成立 |", "", "## 要你认的三件事 {#commitments}", "",
                            "1. **改动内容**:这是第 1 版。", "2. **最薄弱的依据**:分县数据不全。",
                            "3. **最可能出错的地方**:口径混了。", ""])
        path = self.at("task_plan.md")
        write(path, plan(NL.join([head] + rows)))
        code, out = run("commitments_check.py", path)
        self.assertEqual(code, 0, out)                       # 下一节表里的「基本成立」不归这一节管
        for name, table in {"别的说法": NL.join([head, rows[0], "| B | 更高 | 基本成立 |", rows[2]]),
                            "加粗": NL.join([head, "| A | 更高 | **成立** |", rows[1], rows[2]]),
                            "缺第三栏": NL.join(["| outcome | 判定条件 |", "|---|---|", "| A | 更高 |"]),
                            "表里没有行": head,
                            "不是表": "- A:更高 → 成立"}.items():
            with self.subTest(name):
                write(path, plan(table))
                code, out = run("commitments_check.py", path)
                self.assertEqual(code, 1, out)
                self.assertIn("对应表述", out)
        self.assertEqual(L.preregistration_problems("## 研究范围 {#scope}" + NL), [])     # 综述型可以没有这一节
        self.assertEqual(L.PREREG_PHRASES, ("成立", "部分成立", "不成立"))

    def test_a_the_two_three_point_readers_agree_on_whitespace(self):
        """A2 联调 (a):全角空格、不换行空格、垂直制表符 —— 齐全的判据与内容的读法都认,读出的三条一样;都不跨换行。"""
        contents = ["这是第 1 版。", "分县数据不全。", "口径混了。"]
        for code in (0x3000, 0xA0, 0x0B):
            ws = chr(code)
            with self.subTest(code=hex(code)):
                body = NL.join(["## 要你认的三件事 {#commitments}", "",
                                "1." + ws + "**改动内容**:" + ws + contents[0],
                                ws + "2." + ws + "**最薄弱的依据**:" + contents[1] + ws,
                                "3." + ws + ws + "**最可能出错的地方**:" + ws + contents[2], ""])
                problems, _, items = L.commitment_problems(body)
                self.assertEqual(problems, [])
                self.assertEqual([it["content"] for it in items], contents)
                self.assertEqual([it["content"] for it in L.parse_commitments(body)], contents)
                circled = NL.join(["## 本轮承诺 {#commitments}", "", "-" + ws + "①" + ws + "改了什么:" + contents[0],
                                   ws + "-" + ws + "② 最脆:" + contents[1], "- ③" + ws + "最可能错在哪:" + contents[2], ""])
                self.assertEqual([it["content"] for it in L.commitment_problems(circled)[2]], contents)
                self.assertEqual([it["content"] for it in L.parse_commitments(circled)], contents)
        broken = NL.join(["## 要你认的三件事 {#commitments}", "", "1.", "**改动内容**:" + contents[0],
                          "2. **最薄弱的依据**:" + contents[1], "3. **最可能出错的地方**:" + contents[2], ""])
        self.assertEqual(len(L.parse_commitments(broken)), 2)                    # 编号后面就换行:不跨过去
        self.assertEqual(len(L.commitment_problems(broken)[2]), 2)

    def test_e_words_max_must_be_a_positive_integer(self):
        """A2 联调 (e):words_max 只认正整数;8000.0 这种小数写法算读不出,成稿形式省掉字数段。"""
        self.assertEqual(L.panel_format({"constraints": {"words_max": 8000, "formats": ["docx"]}}), "约 8000 字 · Word 版")
        for bad in (8000.0, "8000", True, 0, -8000, None):
            with self.subTest(bad=bad):
                self.assertEqual(L.panel_format({"constraints": {"words_max": bad, "formats": ["docx"]}}), "Word 版")
        write(self.at("task_plan.md"), NL.join(["---", "kind: task_plan", "version: 1", "genre: argument", "report_name: 内参",
                                                "constraints: {words_max: 8000.0, formats: [docx]}", "---", "# 任务计划", ""]))
        self.assertEqual(L.home_line(self.tmp), "研判型 · 内参 · Word 版")

    def test_l7_the_revert_entry_must_not_outrun_the_next_version(self):
        """改流程 v3 没被确认、还原到 v2:还原那一条写 v3,下一次改到 v3 时照样能落章;写成 v4 就被拒。"""
        for revert_v, want in ((3, 0), (4, 2)):
            with self.subTest(revert_v=revert_v):
                log = NL.join(["revision_log:",
                               "- {v: 2, at: '2026-10-01T10:00:00-04:00', who: agent, what: 时间段改成 2021, why: 用户要求}",
                               "- {v: 3, at: '2026-10-01T11:00:00-04:00', who: agent, what: '改流程:去掉拟定提纲', why: 用户说不用提纲}",
                               "- {v: %d, at: '2026-10-01T11:05:00-04:00', who: agent, what: 改流程没确认、已还原, why: 用户先不确认}" % revert_v])
                plan = NL.join(["---", "kind: task_plan", "version: 3", "genre: argument", "pipeline_status: gate1_awaiting",
                                log, "---", "# 任务计划(第 3 版:又改了研究范围)", ""])
                path = self.at("task_plan.md")
                write(path, plan)
                code, out = run("stamp.py", path, "--approve", "--by", "测试", "--quote", "好")
                self.assertEqual(code, want, out)
                if want:
                    self.assertIn("改了正本要先升版", out)


if __name__ == "__main__":
    unittest.main()
