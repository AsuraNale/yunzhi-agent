# -*- coding: utf-8 -*-
"""session_facts.mjs 适配 dsh 0.1.7-rc.2(v1.1.0 · 工单 T5)。

  1. 会话根 = `$DSH_HOME/sessions`(DSH_HOME 未设或空白时 `~/.dsh/sessions`);
  2. 0.1.7-rc.2 的目录布局与文件名(`<项目目录键>/<转义后的会话 id>/session.v4.jsonl.zstd`),
     同一会话目录里有几代日志时只读代数最高的一份;
  3. 帧按帧头结构切,不按魔数切;末尾半帧、坏行都进 `log_notes`;
  4. `bash` 与 `pwsh` 一样计入 shell 调用;
  5. 带继承前缀的子代理不重复计入父会话的事件;启发式只挑顶层会话。

夹具 `fixtures/dsh-0.1.7-rc.2/home` 由 dsh 0.1.7-rc.2 自己的写入器生成(见同目录
make_fixture.mjs;事件是合成的)。其余用例按同一格式在测试里现造。

跑法: python -X utf8 -m pytest -q test_session_facts_v4.py
"""
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE_HOME = os.path.join(HERE, "fixtures", "dsh-0.1.7-rc.2", "home")
NL = chr(10)
BS = chr(92)
for _k in ("DSH_HOME", "DSH_SESSION_ID", "DSH_SESSION_JSONL"):
    os.environ.pop(_k, None)


def win(*parts):
    return "C:" + BS + BS.join(parts)


FIX_PROJECT = win("yz-fixture", "projects", "demo")
FIX_OTHER = win("yz-fixture", "projects", "other")


def run_facts(project, env_extra=None, args=None, home=None, dsh_home=None):
    """→ (rc, 控制台文本, json 或 None)。HOME 永远指向一个空的临时目录,免得读到真会话。"""
    tmp = tempfile.mkdtemp(prefix="sfv4-run-")
    out = os.path.join(tmp, "facts.json")
    empty_home = home or os.path.join(tmp, "empty-home")
    os.makedirs(empty_home, exist_ok=True)
    env = dict(os.environ, USERPROFILE=empty_home, HOME=empty_home)
    if dsh_home is not None:
        env["DSH_HOME"] = dsh_home
    env.update(env_extra or {})
    argv = ["node", os.path.join(HERE, "session_facts.mjs"), "--project", project, "--json", out]
    r = subprocess.run(argv + list(args or []), capture_output=True, env=env, timeout=120)
    text = (r.stdout + r.stderr).decode("utf-8", "replace")
    data = None
    if os.path.exists(out):
        with io.open(out, encoding="utf-8") as f:
            data = json.loads(f.read())
    return r.returncode, text, data


def by_path(data):
    return {r["path"]: r for r in data["outside_reads"]}


# ─────────────────────────────────────────── 由 dsh 自己的写入器产生的夹具
@unittest.skipUnless(sys.platform == "win32", "夹具里的路径是 Windows 盘符路径")
class RealWriterFixture(unittest.TestCase):

    def test_main_session_by_injected_id(self):
        code, text, data = run_facts(FIX_PROJECT, {"DSH_SESSION_ID": "yz-main-01"},
                                     dsh_home=FIXTURE_HOME)
        self.assertEqual(code, 0, text)
        self.assertEqual(data["session"], "yz-main-01")
        self.assertIn("DSH_SESSION_ID", data["session_picked_by"])
        self.assertEqual(data["sessions_root"], os.path.join(FIXTURE_HOME, "sessions"))
        # 子代理:冒号 id 的目录名被转义;孙代理用逻辑 id 指回它;带继承前缀的也并入。
        self.assertEqual(set(data["subagent_sessions"]),
                         {"yz-kid~003A02", "yz-fork-03", "yz-grand-04"}, text)
        # 步数:主会话 2;子代理 1 + fork 自己的 1(继承来的那 1 个不算)+ 孙代理 1。
        self.assertEqual(data["steps_used"], 2)
        self.assertEqual(data["steps_incl_subagents"], 5, text)
        # shell:bash 与 pwsh 一样计数。
        self.assertEqual(data["shell_calls"], 4)
        self.assertEqual(data["shell_calls_by_tool"], {"pwsh": 2, "bash": 2})
        self.assertIn("shell 调用 4 次", text)
        self.assertEqual(data["scope_counts"],
                         {"outside_toolkit": 6, "harness_temp": 0, "other_project": 1,
                          "toolkit_internal": 2, "unclassified": 0})
        got = by_path(data)
        expect = {
            win("yz-outside", "notes.md"): ("读", "read", "主会话"),
            win("yz-outside", "chart.png"): ("读", "read_image", "主会话"),
            win("yz-fixture", "scripts", "checker.py"): ("写", "write", "主会话"),
            win("yz-fixture", "library", "rules"): ("读", "grep", "主会话"),
            FIX_OTHER + BS + "cards" + BS + "L01.md": ("读", "str_replace_editor", "主会话"),
            win("yz-outside", "draft.md"): ("写", "str_replace_editor", "主会话"),
            win("yz-outside", "kid.md"): ("读", "read", "子代理"),
            win("yz-outside", "fork.md"): ("读", "read", "子代理"),
            win("yz-outside", "grand.md"): ("读", "read", "子代理"),
        }
        self.assertEqual(set(got), set(expect), text)
        for p, (action, tool, frm) in expect.items():
            self.assertEqual((got[p]["action"], got[p]["tool"], got[p]["from"]), (action, tool, frm), p)
        self.assertTrue(any("继承" in n for n in data["log_notes"]), data["log_notes"])

    def test_an_injected_id_that_needs_escaping(self):
        code, text, data = run_facts(FIX_PROJECT, {"DSH_SESSION_ID": "yz-kid:02"},
                                     dsh_home=FIXTURE_HOME)
        self.assertEqual(code, 0, text)
        self.assertEqual(data["session"], "yz-kid~003A02")
        self.assertEqual(data["subagent_sessions"], ["yz-grand-04"])
        self.assertEqual((data["steps_used"], data["steps_incl_subagents"]), (1, 2))

    def test_the_heuristic_only_picks_a_top_level_session(self):
        """子代理的 header 带着父会话的 cwd;把它们的日志弄成最新的,启发式也不许选它们。"""
        home = tempfile.mkdtemp(prefix="sfv4-heur-")
        shutil.copytree(os.path.join(FIXTURE_HOME, "sessions"), os.path.join(home, "sessions"))
        now = time.time()
        for root, _, files in os.walk(os.path.join(home, "sessions")):
            for fn in files:
                p = os.path.join(root, fn)
                t = now - 3600 if "yz-main-01" in root else now
                os.utime(p, (t, t))
        code, text, data = run_facts(FIX_PROJECT, dsh_home=home)
        self.assertEqual(code, 0, text)
        self.assertEqual(data["session"], "yz-main-01")
        self.assertIn("启发式", data["session_picked_by"])
        code, text, data = run_facts(FIX_OTHER, dsh_home=home)
        self.assertEqual(data["session"], "yz-else-05", text)

    def test_dsh_home_decides_where_sessions_are_read(self):
        # 同一份夹具放在 HOME/.dsh 下:不设 DSH_HOME(或设成空白)时读它。
        home = tempfile.mkdtemp(prefix="sfv4-home-")
        shutil.copytree(os.path.join(FIXTURE_HOME, "sessions"), os.path.join(home, ".dsh", "sessions"))
        for dsh_home in (None, "   "):
            with self.subTest(DSH_HOME=dsh_home):
                code, text, data = run_facts(FIX_PROJECT, {"DSH_SESSION_ID": "yz-main-01"},
                                             home=home, dsh_home=dsh_home)
                self.assertEqual(code, 0, text)
                self.assertEqual(data["sessions_root"], os.path.join(home, ".dsh", "sessions"))
        # DSH_HOME 指向别处(那里没有这个会话):注入的 id 找不到 → 未取到,不回落。
        code, text, data = run_facts(FIX_PROJECT, {"DSH_SESSION_ID": "yz-main-01"},
                                     home=home, dsh_home=tempfile.mkdtemp(prefix="sfv4-empty-"))
        self.assertEqual(code, 3, text)
        self.assertIsNone(data["steps_used"])


# ─────────────────────────────────────────── 按同一格式现造的日志
def zstd(data):
    """一个带校验和的 zstd 帧(dsh 0.1.7 的写法)。"""
    script = ("const z=require('zlib'),fs=require('fs');"
              "process.stdout.write(z.zstdCompressSync(fs.readFileSync(0),"
              "{params:{[z.constants.ZSTD_c_checksumFlag]:1}}))")
    r = subprocess.run(["node", "-e", script], input=data, capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    return r.stdout


def raw_frame(content):
    """手拼一个 raw block 的 zstd 帧:内容原样落在帧里(可以故意放进魔数字节)。"""
    fhd = 0xA0  # 单段 + 4 字节内容长度
    block_header = (len(content) << 3) | 1   # type 0 = raw,last = 1
    return (bytes([0x28, 0xB5, 0x2F, 0xFD, fhd]) + struct.pack("<I", len(content))
            + struct.pack("<I", block_header)[:3] + content)


def lines(*rows):
    return (NL.join(json.dumps(r, ensure_ascii=False) for r in rows) + NL).encode("utf-8")


def v4_header(sid, cwd, **extra):
    h = {"type": "session", "version": 4, "id": sid, "createdAt": 1790000000000, "cwd": cwd,
         "isSeeded": False, "delegationDepth": 0}
    h.update(extra)
    return h


def step(n, seq):
    return {"type": "step/start", "seq": seq, "time": 1790000000000 + seq, "data": {"turn": 1, "step": n}}


def tool(name, args, seq):
    return {"type": "tool/call", "seq": seq, "time": 1790000000000 + seq,
            "data": {"turn": 1, "step": 1, "callId": "c%d" % seq, "name": name,
                     "arguments": json.dumps(args, ensure_ascii=False)}}


class BuiltLogs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sfv4-")
        self.project = os.path.join(self.tmp, "proj")
        os.makedirs(self.project)
        self.dsh_home = os.path.join(self.tmp, "dshhome")
        self.inside = os.path.join(self.project, "task_plan.md")

    def session_dir(self, sid):
        d = os.path.join(self.dsh_home, "sessions", "--proj--", sid)
        os.makedirs(d, exist_ok=True)
        return d

    def facts(self, sid, **kw):
        return run_facts(self.project, {"DSH_SESSION_ID": sid}, dsh_home=self.dsh_home, **kw)

    def test_only_the_newest_generation_is_read(self):
        """迁移后目录里会同时有 v0 旧件与 v4 现件:两份都读会把步数算两遍。"""
        d = self.session_dir("s-gen")
        old = lines({"type": "session", "id": "s-gen"}, *[{"type": "step/start", "data": {}}] * 3)
        new = lines(v4_header("s-gen", self.project), *[step(i + 1, i) for i in range(5)])
        with open(os.path.join(d, "session.jsonl.zstd"), "wb") as f:
            f.write(zstd(old))
        with open(os.path.join(d, "session.v4.jsonl.zstd"), "wb") as f:
            f.write(zstd(new[:len(new) // 2]) + zstd(new[len(new) // 2:]))  # 行中间断帧也要接得上
        code, text, data = self.facts("s-gen")
        self.assertEqual(code, 0, text)
        self.assertEqual(data["steps_used"], 5, text)

    def test_an_uncompressed_log_is_read(self):
        d = self.session_dir("s-plain")
        with open(os.path.join(d, "session.v4.jsonl"), "wb") as f:
            f.write(lines(v4_header("s-plain", self.project), step(1, 0), step(2, 1)))
        code, text, data = self.facts("s-plain")
        self.assertEqual(code, 0, text)
        self.assertEqual(data["steps_used"], 2)

    def test_magic_bytes_inside_a_frame_do_not_split_it(self):
        """魔数的四个字节出现在帧中间:按魔数切会把后半截事件静默丢掉。"""
        d = self.session_dir("s-magic")
        bait = lines(v4_header("s-magic", self.project), step(1, 0))
        # 一行事件的字符串值里夹着 28 B5 2F FD(不是合法 UTF-8,解码成替换字符,JSON 照样能读)
        trap = (b'{"type":"x/note","seq":1,"time":1,"data":{"t":"' + bytes([0x28, 0xB5, 0x2F, 0xFD])
                + b'"}}' + NL.encode() + lines(step(2, 2), step(3, 3)))
        with open(os.path.join(d, "session.v4.jsonl.zstd"), "wb") as f:
            f.write(zstd(bait) + raw_frame(trap) + zstd(lines(step(4, 4))))
        code, text, data = self.facts("s-magic")
        self.assertEqual(code, 0, text)
        self.assertEqual(data["steps_used"], 4, text)
        self.assertEqual(data["log_notes"], [], "结构完好的日志不该有丢弃记录")

    def test_a_torn_last_frame_is_reported_not_hidden(self):
        d = self.session_dir("s-torn")
        head = zstd(lines(v4_header("s-torn", self.project), step(1, 0), step(2, 1)))
        tail = zstd(lines(step(3, 2), step(4, 3)))
        with open(os.path.join(d, "session.v4.jsonl.zstd"), "wb") as f:
            f.write(head + tail[:-6])
        code, text, data = self.facts("s-torn")
        self.assertEqual(code, 0, text)
        self.assertGreaterEqual(data["steps_used"], 2)
        self.assertTrue(any("写到一半" in n for n in data["log_notes"]), data["log_notes"])

    def test_bash_counts_as_a_shell_call(self):
        d = self.session_dir("s-sh")
        with open(os.path.join(d, "session.v4.jsonl.zstd"), "wb") as f:
            f.write(zstd(lines(v4_header("s-sh", self.project),
                               tool("read", {"file_path": self.inside}, 0),
                               tool("bash", {"command": "cat /etc/hosts"}, 1),
                               tool("bash", {"command": "ls"}, 2),
                               tool("pwsh", {"command": "dir"}, 3))))
        code, text, data = self.facts("s-sh")
        self.assertEqual(code, 0, text)
        self.assertEqual(data["shell_calls"], 3)
        self.assertEqual(data["shell_calls_by_tool"], {"pwsh": 1, "bash": 2})
        self.assertIn("shell 调用 3 次", text)
        self.assertNotIn("pwsh 调用", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
