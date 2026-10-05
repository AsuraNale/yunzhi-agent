# -*- coding: utf-8 -*-
"""v1.1.0 的两个新格式:任务计划的 `stages`(流程可调,V8)与确认记录的
`signature`(确认只能由人做,D1 / V9)。格式定义见 docs/stages.md 与
docs/signoff-format.md;规格 04 §① / §0 / §⑧-5、§⑧-6。

跑法: python -X utf8 -m pytest -q test_stages_signoff.py
"""
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from pipeline_lib import (STAGES, content_hash, parse_stages, read_md,  # noqa: E402
                          signoff_payload, stage_docs, stage_states, write_md)

NL = chr(10)
PY_ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
for _k in ("DSH_HOME", "DSH_SESSION_ID", "DSH_SESSION_JSONL"):
    PY_ENV.pop(_k, None)

NO_OUTLINE = ["task", "sources", "delivery"]


def py(script, *args):
    r = subprocess.run([sys.executable, "-X", "utf8", os.path.join(HERE, script)] + list(args),
                       capture_output=True, env=PY_ENV, timeout=120)
    return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")


def stamp(*args):
    return py("stamp.py", *args)


def status(project, json_out=None):
    args = [project] + (["--json", json_out] if json_out else [])
    return py("pipeline_status.py", *args)


def new_doc(path, title, extra=None):
    meta = {"title": title, "version": 1,
            "approval": {"status": "awaiting", "approved_at": None, "approved_by": None,
                         "approval_quote": None, "approved_hash": None},
            "revision_log": []}
    meta.update(extra or {})
    write_md(path, meta, NL + "# " + title + NL + NL + "正文若干。" + NL)
    return path


def new_project(tmp, stages=None):
    """一个刚开工的项目:task_plan(planning)+ PROGRESS.md。stages=None 表示不写这个键。"""
    extra = {"kind": "task_plan", "pipeline_status": "planning",
             "last_turn_at": "2026-09-29T10:00:00-04:00"}
    if stages is not None:
        extra["stages"] = stages
    new_doc(os.path.join(tmp, "task_plan.md"), "任务计划", extra)
    with io.open(os.path.join(tmp, "PROGRESS.md"), "w", encoding="utf-8", newline=NL) as f:
        f.write("进行中" + NL)
    # cite_check 没有登记表就在第一步停下,走不到 ⑥。
    with io.open(os.path.join(tmp, "references.yaml"), "w", encoding="utf-8", newline=NL) as f:
        f.write("entries: []" + NL)
    return tmp


# ═══════════════════════════════════════════════════════════════ stages
class ParseStages(unittest.TestCase):
    def test_absent_or_null_means_all_four(self):
        self.assertEqual(parse_stages({}), (list(STAGES), None))
        self.assertEqual(parse_stages({"stages": None}), (list(STAGES), None))

    def test_dropping_the_outline_is_accepted(self):
        self.assertEqual(parse_stages({"stages": NO_OUTLINE}), (NO_OUTLINE, None))
        self.assertEqual(stage_docs(NO_OUTLINE), ["task_plan.md", "dossier.md"])
        self.assertNotIn("outlining", stage_states(NO_OUTLINE))
        self.assertIn("drafting", stage_states(NO_OUTLINE))

    def test_everything_else_is_refused_not_guessed(self):
        for bad in ("task, sources, delivery", [], ["task", "delivery"],
                    ["task", "sources", "outline"], ["task", "sources", "sources", "delivery"],
                    ["sources", "task", "delivery"], ["task", "sources", "提纲", "delivery"],
                    ["task", "sources", 3, "delivery"], {"task": True}):
            with self.subTest(stages=bad):
                got, err = parse_stages({"stages": bad})
                self.assertIsNone(got)
                self.assertTrue(err)

    def test_the_four_stages_cover_the_state_machine_exactly_once(self):
        from pipeline_lib import PIPELINE_STATES
        self.assertEqual(stage_states(list(STAGES)), PIPELINE_STATES)


class NoOutlineProjectWalksToDelivery(unittest.TestCase):
    """T-A6:省掉提纲的项目,从确认任务计划一路走到交付 —— pipeline_status 无错,
    cite_check ⑥ 通过;中途想进提纲环节被拒。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="stages-walk-")
        new_project(self.tmp, NO_OUTLINE)
        self.plan = os.path.join(self.tmp, "task_plan.md")
        self.dossier = os.path.join(self.tmp, "dossier.md")

    def assert_clean(self, where):
        out_json = os.path.join(self.tmp, "status.json")
        code, out = status(self.tmp, out_json)
        self.assertEqual(code, 0, "%s:%s" % (where, out))
        self.assertNotIn("[FAIL]", out, where)
        with io.open(out_json, encoding="utf-8") as f:
            return json.loads(f.read())["facts"]

    def advance(self, state):
        code, out = stamp(self.plan, "--advance", state)
        self.assertEqual(code, 0, out)

    def test_walk(self):
        facts = self.assert_clean("planning")
        self.assertEqual(facts["stages"], NO_OUTLINE)
        self.assertEqual(facts["stages_source"], "task_plan")
        self.assertEqual(facts["stamps"]["outline.md"]["status"], "skipped")
        self.advance("gate1_awaiting")
        self.assert_clean("gate1_awaiting")
        code, out = stamp(self.plan, "--approve", "--by", "研究员", "--quote", "按这个版本走")
        self.assertEqual(code, 0, out)
        for state in ("gate1_approved", "collecting"):
            self.advance(state)
            self.assert_clean(state)
        new_doc(self.dossier, "资料汇编", {"kind": "dossier"})
        self.advance("gate2_awaiting")
        self.assert_clean("gate2_awaiting")
        code, out = stamp(self.dossier, "--approve", "--by", "研究员", "--quote", "资料够了")
        self.assertEqual(code, 0, out)
        self.advance("gate2_approved")
        self.assert_clean("gate2_approved")

        # ⛔ 这一步是「合法跳转」的反面:本项目没有提纲环节,进不去。
        before = io.open(self.plan, "rb").read()
        code, out = stamp(self.plan, "--advance", "outlining")
        self.assertEqual(code, 2, out)
        self.assertEqual(io.open(self.plan, "rb").read(), before, "拒绝推进却动了盘上的字节")

        for state in ("drafting", "verifying", "delivered"):
            self.advance(state)
            facts = self.assert_clean(state)
        groups = {g["record"]: g["state"] for g in facts["commitments"]}
        self.assertEqual(groups["outline.md"], "skipped")

        # 交付检查 ⑥:只要求两份确认记录,都有效;提纲那一格说明不要求。
        _, out = py("cite_check.py", "--project", self.tmp)
        six = [l.strip() for l in out.split(NL) if "三印章:" in l]
        self.assertEqual(len(six), 3, out)
        self.assertTrue(all(l.startswith("[PASS]") for l in six), six)
        self.assertTrue(any("outline.md" in l and "不要求确认" in l for l in six), six)


class FourStageProjectsBehaveAsBefore(unittest.TestCase):
    """T-A6 的另一半:不写 stages 的老项目,行为不变。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="stages-old-")
        new_project(self.tmp)   # 不写 stages
        self.plan = os.path.join(self.tmp, "task_plan.md")
        stamp(self.plan, "--approve", "--by", "研究员", "--quote", "好")
        new_doc(os.path.join(self.tmp, "dossier.md"), "资料汇编")
        stamp(os.path.join(self.tmp, "dossier.md"), "--approve", "--by", "研究员", "--quote", "好")

    def test_outlining_is_still_a_legal_state(self):
        code, out = stamp(self.plan, "--advance", "outlining")
        self.assertEqual(code, 0, out)

    def test_drafting_without_an_approved_outline_is_still_a_fail(self):
        stamp(self.plan, "--advance", "drafting")
        code, out = status(self.tmp)
        self.assertEqual(code, 1, out)
        self.assertIn("pipeline_status=drafting 但 outline.md 印章=missing", out)

    def test_cite_check_still_requires_the_outline_stamp(self):
        _, out = py("cite_check.py", "--project", self.tmp)
        self.assertIn("[FAIL] 三印章: outline.md 不存在", out)

    def test_facts_say_the_stages_are_the_default(self):
        out_json = os.path.join(self.tmp, "s.json")
        status(self.tmp, out_json)
        with io.open(out_json, encoding="utf-8") as f:
            facts = json.loads(f.read())["facts"]
        self.assertEqual(facts["stages"], list(STAGES))
        self.assertEqual(facts["stages_source"], "default")


class StagesGuards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="stages-guard-")

    def test_a_state_in_the_dropped_stage_is_a_fail(self):
        new_project(self.tmp, NO_OUTLINE)
        plan = os.path.join(self.tmp, "task_plan.md")
        meta, body = read_md(plan)
        meta["pipeline_status"] = "gate3_awaiting"
        write_md(plan, meta, body)
        code, out = status(self.tmp)
        self.assertEqual(code, 1, out)
        self.assertIn("属于本项目流程里没有的环节", out)

    def test_a_leftover_outline_is_a_warning_not_a_requirement(self):
        new_project(self.tmp, NO_OUTLINE)
        new_doc(os.path.join(self.tmp, "outline.md"), "提纲")
        code, out = status(self.tmp)
        self.assertEqual(code, 0, out)
        self.assertIn("[WARN] 流程环节: outline.md 在盘上", out)
        self.assertIn("outline=skipped", out)

    def test_approving_an_outline_the_project_does_not_have_is_refused(self):
        new_project(self.tmp, NO_OUTLINE)
        outline = new_doc(os.path.join(self.tmp, "outline.md"), "提纲", {"kind": "outline"})
        before = io.open(outline, "rb").read()
        code, out = stamp(outline, "--approve", "--by", "研究员", "--quote", "好")
        self.assertEqual(code, 2, out)
        self.assertEqual(io.open(outline, "rb").read(), before)

    def test_unreadable_stages_fail_everywhere_and_are_checked_strictly(self):
        new_project(self.tmp, ["task", "delivery"])      # 省掉了 sources:本版不支持
        plan = os.path.join(self.tmp, "task_plan.md")
        code, out = status(self.tmp)
        self.assertEqual(code, 1, out)
        self.assertIn("[FAIL] 流程环节", out)
        code, out = stamp(plan, "--advance", "gate1_awaiting")
        self.assertEqual(code, 2, out)
        _, out = py("cite_check.py", "--project", self.tmp)
        self.assertIn("按四个环节全查", out)
        self.assertIn("[FAIL] 三印章: outline.md 不存在", out)

    def test_changing_the_stages_is_a_content_change(self):
        """stages 进印章 hash:改流程 = 改了已确认的任务计划,要重新确认。"""
        new_project(self.tmp)
        plan = os.path.join(self.tmp, "task_plan.md")
        stamp(plan, "--approve", "--by", "研究员", "--quote", "好")
        code, _ = stamp(plan, "--check")
        self.assertEqual(code, 0)
        meta, body = read_md(plan)
        meta["stages"] = NO_OUTLINE
        write_md(plan, meta, body)
        code, out = stamp(plan, "--check")
        self.assertEqual(code, 1, out)


# ═══════════════════════════════════════════════════════════════ signature
SIG = "v1.ed25519.0123456789abcdef.QkFTRTY0VVJMLVNJR05BVFVSRS1TQU1QTEU"
AT = "2026-09-29T14:03:11-04:00"


class SignedApproval(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="signoff-")
        new_project(self.tmp)
        self.plan = os.path.join(self.tmp, "task_plan.md")

    def approve_signed(self, signature=SIG, quote="按这个版本走", at=AT, expect=None):
        h = expect or stamp(self.plan, "--hash")[1].strip()
        return stamp(self.plan, "--approve", "--by", "研究员", "--quote", quote,
                     "--at", at, "--expect-hash", h, "--signature", signature)

    def approval(self):
        return read_md(self.plan)[0]["approval"]

    def test_the_signature_is_written_verbatim_with_the_given_time_and_hash(self):
        h = stamp(self.plan, "--hash")[1].strip()
        code, out = self.approve_signed()
        self.assertEqual(code, 0, out)
        ap = self.approval()
        self.assertEqual(ap["signature"], SIG)
        self.assertEqual(ap["approved_at"], AT)
        self.assertEqual(ap["approved_hash"], h)
        self.assertEqual(stamp(self.plan, "--check")[0], 0, "签名不该进内容 hash")

    def test_unsigned_approval_keeps_the_old_shape(self):
        code, out = stamp(self.plan, "--approve", "--by", "研究员", "--quote", "好")
        self.assertEqual(code, 0, out)
        self.assertEqual(list(self.approval()),
                         ["status", "approved_at", "approved_by", "approval_quote", "approved_hash"])

    def _refused(self, *args):
        before = io.open(self.plan, "rb").read()
        code, out = stamp(self.plan, "--approve", "--by", "研究员", *args)
        self.assertEqual(code, 2, out)
        self.assertEqual(io.open(self.plan, "rb").read(), before, "拒绝落章却动了盘上的字节")
        return out

    def test_a_signature_needs_the_time_and_the_hash_it_signed(self):
        h = stamp(self.plan, "--hash")[1].strip()
        self._refused("--quote", "好", "--signature", SIG)
        self._refused("--quote", "好", "--signature", SIG, "--at", AT)
        self._refused("--quote", "好", "--signature", SIG, "--expect-hash", h)

    def test_content_that_moved_after_signing_is_refused(self):
        self._refused("--quote", "好", "--at", AT, "--expect-hash", "0" * 12, "--signature", SIG)

    def test_malformed_inputs_are_refused(self):
        h = stamp(self.plan, "--hash")[1].strip()
        self._refused("--quote", "好", "--at", AT, "--expect-hash", h, "--signature", "有 空格")
        self._refused("--quote", "好", "--at", "2026-09-29 14:03", "--expect-hash", h,
                      "--signature", SIG)
        self._refused("--quote", "好", "--at", AT, "--expect-hash", "ABC", "--signature", SIG)
        self._refused("--quote", "字" * 41, "--at", AT, "--expect-hash", h, "--signature", SIG)

    def test_signature_survives_advance_and_invalidate(self):
        """T-A7:带签名的确认记录经过 --advance、--invalidate 后签名原样保留。

        --advance 不碰 approval;--invalidate 把整条旧记录挪进 revision_log 的
        prev_approval(见 docs/signoff-format.md §4)。几种 YAML 容易读歪的串都走一遍。
        """
        for sig in (SIG, "1e5", "0777", "null", "yes", "~", "12:30", "=/+-._~:"):
            with self.subTest(signature=sig):
                tmp = tempfile.mkdtemp(prefix="signoff-keep-")
                new_project(tmp)
                self.plan = os.path.join(tmp, "task_plan.md")
                code, out = self.approve_signed(signature=sig)
                self.assertEqual(code, 0, out)
                signed = dict(self.approval())
                self.assertEqual(signed["signature"], sig)
                for state in ("gate1_awaiting", "gate1_approved", "collecting"):
                    code, out = stamp(self.plan, "--advance", state)
                    self.assertEqual(code, 0, out)
                    self.assertEqual(self.approval(), signed, "--advance 改动了确认记录")
                code, out = stamp(self.plan, "--invalidate", "--why", "补一条口径")
                self.assertEqual(code, 0, out)
                meta = read_md(self.plan)[0]
                self.assertEqual(meta["revision_log"][-1]["prev_approval"], signed)
                self.assertEqual(meta["revision_log"][-1]["prev_approval"]["signature"], sig)
                self.assertEqual(meta["approval"]["status"], "awaiting")
                self.assertNotIn("signature", meta["approval"])


# docs/signoff-format.md §3 那句「测试里对着 node 的 JSON.stringify 逐字节核过(含……)」
# 里的每一类字符 → 在样例里怎么认出它(v1.1.1 · Tb4:样例里的 U+2028 原是一个看不见的原字符
# (6c4aa86),读源码的人以为样例里没有、文档却写了「含 U+2028」;改写成转义、补上 U+2029,再加这张逐类对照)。
# 文档多写了一类、这里没有判据 → 测试红;样例里缺了文档说有的一类 → 测试红。
CLAIMED_COVERAGE = {
    "中文": lambda s: any("\u4e00" <= c <= "\u9fff" for c in s),
    "引号": lambda s: '"' in s,
    "反斜杠": lambda s: "\\" in s,
    "换行": lambda s: "\n" in s,
    "制表": lambda s: "\t" in s,
    "U+2028": lambda s: "\u2028" in s,
    "U+2029": lambda s: "\u2029" in s,
    "emoji": lambda s: any(ord(c) >= 0x1F000 for c in s),
    # 控制字符指 JSON 必须转义的 C0 那一段(制表、换行另有一类);\x7f 两边都不转义,不算数。
    "控制字符": lambda s: any(ord(c) < 0x20 and c not in "\t\n" for c in s),
}
SIGNOFF_DOC = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "docs", "signoff-format.md"))


class SignoffPayload(unittest.TestCase):
    """载荷定义(docs/signoff-format.md §3)与 Node 的 JSON.stringify 逐字节相同。"""

    PROJECT, DOC = "proj-7", "task_plan.md"
    APPROVAL = {"approved_hash": "3f9c0a1b2c4d", "approved_at": AT,
                "approved_by": "研究员 \"甲\" \\ 乙", "approval_quote": "行\t就这样\n走\u2028\u2029😀\x01\x7f"}

    def values(self):
        """被签名的七项,与 signoff_payload 的定义同序。对拍与覆盖面检查共用这一份。"""
        return ["yunzhi-signoff/1", self.PROJECT, self.DOC] + [
            self.APPROVAL[k] for k in ("approved_hash", "approved_at", "approved_by", "approval_quote")]

    def test_python_reference_matches_node(self):
        got = signoff_payload(self.PROJECT, self.DOC, self.APPROVAL)
        script = ("const v=JSON.parse(require('fs').readFileSync(0,'utf8'));"
                  "process.stdout.write(Buffer.from(JSON.stringify(v),'utf8'))")
        r = subprocess.run(["node", "-e", script], input=json.dumps(self.values()).encode("ascii"),
                           capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        self.assertEqual(got, r.stdout)

    def test_the_sample_covers_what_the_format_doc_claims(self):
        """Tb4:文档说这条对拍「含」哪些字符,样例里就必须真有 —— 不然「对拍过」是空话。"""
        if not os.path.exists(SIGNOFF_DOC):
            self.skipTest("docs/ 不随工具包发布:%s 不在,无从比对" % SIGNOFF_DOC)
        with io.open(SIGNOFF_DOC, encoding="utf-8") as f:
            m = re.search(r"逐字节核过\(含([^)]*)\)", f.read())
        self.assertIsNotNone(m, "docs/signoff-format.md 里找不到「逐字节核过(含…)」那句")
        claimed = [c.strip() for c in m.group(1).split("、") if c.strip()]
        self.assertGreaterEqual(len(claimed), 8, claimed)
        sample = "".join(self.values())
        for c in claimed:
            with self.subTest(claimed=c):
                self.assertIn(c, CLAIMED_COVERAGE, "文档说覆盖了「%s」,测试里没有对应的判据" % c)
                self.assertTrue(CLAIMED_COVERAGE[c](sample), "文档说对拍覆盖了「%s」,样例里没有" % c)

    def test_every_field_is_required_and_textual(self):
        for key in ("approved_hash", "approved_at", "approved_by", "approval_quote"):
            broken = dict(self.APPROVAL)
            broken[key] = None
            with self.subTest(key=key), self.assertRaises(ValueError):
                signoff_payload("proj-7", "task_plan.md", broken)


if __name__ == "__main__":
    unittest.main(verbosity=2)
