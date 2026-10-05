# -*- coding: utf-8 -*-
"""右侧那一页(10-03 实测:Codex 每往右侧打开一次网页都多开一个标签,旧的还在,也不回标签号)。

做法:每个项目一页固定的 右侧.html,一个对话只挂一次;要显示进度页还是材料,都由脚本把那一页的内容换进来
(原子写:同一文件夹里的临时文件 → 一次换名),它自己定时刷新(进度页 5 秒,材料 30 秒)。这里核:
- --pane 原子写、带刷新(进度页 5 秒、材料 30 秒)、只有一个刷新;换名失败时旧的那一页原样不坏、不留临时文件;
- 整页刷新后回到读到的位置:内容标记跟着内容变,记位置的脚本在,真跑一遍(node)只在同一份内容时回到原处;
- 材料页换进右侧后,相对地址改成绝对地址(右侧那一页在项目根上),材料页自己不动;
- 右侧正显示进度页时跟着进度页更新,正显示材料时不盖掉;
- 五种确认卡出卡时都把材料换进右侧,回答之后换回进度页(test_card_fixes);新建、接着做、改名也都换成进度页;
- skills 里只有一处打开右侧,条件是这个对话里第一次;脚本的输出从来不叫 agent 打开右侧。
"""
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time

import pytest

import card
import progress
import projects
import view
import yzlib
from conftest import MAIN, ROOT, read, write

TOOLS = os.path.join(ROOT, "agent-tools")
SKILLS_DIR = os.path.join(ROOT, ".agents", "skills")
REFRESH_5 = '<meta http-equiv="refresh" content="5">'
REFRESH_30 = '<meta http-equiv="refresh" content="30">'
FLOW_FIELDS = {"changes": "去掉拟定提纲这一步，写作按资料汇编的结构走。"}


def pane_of(project):
    return os.path.join(project, "右侧.html")


def tmp_files(project):
    return [f for f in os.listdir(project) if f.endswith(".tmp")]


def body_of(page):
    m = re.search(r"<body>(.*)</body>", page, re.S)
    assert m, "no body"
    return m.group(1)


# ---------------------------------------------------------------- 原子写、刷新

def test_progress_pane_is_written_atomically_with_a_5s_refresh(world, monkeypatch, capsys):
    project = world("S3")
    calls = []
    real_replace = os.replace

    def spy(src, dst):
        # 换名那一刻:临时文件已经是整页,目标还是旧的(或还没有)—— 右侧刷新时只会读到其中一份完整的
        calls.append({"src": src, "dst": dst, "new": read(src),
                      "old": read(dst) if os.path.isfile(dst) else None})
        return real_replace(src, dst)

    yzlib.write_pane(project, view.render(project, "task_plan")[0], "view:task_plan")      # 先有一份旧的
    monkeypatch.setattr(yzlib.os, "replace", spy)
    code = progress.main(["progress.py", MAIN, "--page", "--pane"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["pane_shows"] == "progress" and "open_in_codex" not in out["next"]
    pane = pane_of(project)
    assert out["pane"] == pane and out["pane_url"] == progress.file_url(pane)
    swaps = [c for c in calls if os.path.normcase(c["dst"]) == os.path.normcase(pane)]
    assert len(swaps) == 1
    swap = swaps[0]
    assert os.path.dirname(swap["src"]) == project                     # 临时文件在项目自己的文件夹里(同一个盘,换名是原子的)
    assert os.path.basename(swap["src"]).startswith(".右侧.html.") and swap["src"].endswith(".tmp")
    assert not os.path.exists(swap["src"]) and not tmp_files(project)  # 换上之后临时文件就没了
    assert swap["new"] == read(pane)                                   # 换名之前临时文件里已经是完整的新页
    assert swap["old"] is not None and 'content="view:task_plan"' in swap["old"]   # 换名之前目标还是完整的旧页
    raw = read(pane)
    assert raw.startswith("<!doctype html>") and '<meta charset="utf-8">' in raw
    assert raw.count(REFRESH_5) == 1 and len(re.findall(r'http-equiv="refresh"', raw)) == 1   # 进度页自己的 15 秒那条去掉了
    assert '<meta name="yunzhi-pane" content="progress">' in raw
    assert body_of(raw) == body_of(read(out["page"]))                  # 内容就是进度页
    assert '<meta http-equiv="refresh" content="15">' in read(out["page"])   # 进度页自己照旧


def test_a_failed_swap_leaves_the_old_pane_whole_and_no_temp_files(world, monkeypatch):
    project = world("S1")
    assert "pane" in yzlib.pane_info(project, progress.write_page(project), "progress")
    before = open(pane_of(project), "rb").read()

    def busy(src, dst):
        raise PermissionError(13, "the browser is reading it")

    monkeypatch.setattr(yzlib.os, "replace", busy)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    res = yzlib.pane_info(project, view.render(project, "task_plan")[0], "view:task_plan")
    assert "pane_error" in res and "pane" not in res
    assert open(pane_of(project), "rb").read() == before
    assert not tmp_files(project)


# ---------------------------------------------------------------- 材料页换进右侧

def test_view_pane_shows_the_material_with_links_that_still_work(world, capsys):
    project = world("S3")
    code = view.main(["view.py", MAIN, "dossier", "--pane"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["pane_shows"] == "view:dossier" and "open_in_codex" not in out["next"]
    own = read(out["page"])
    pane = read(pane_of(project))
    relative = own.count('href="资料卡片.html#S')
    assert relative > 0 and "http-equiv" not in own                   # 材料页自己不动(相对地址、不刷新)
    assert 'href="资料卡片.html' not in pane                            # 右侧那一页里没有相对地址了
    cards_url = progress.file_url(os.path.join(project, "查看", "资料卡片.html"))
    assert pane.count('href="%s#S' % cards_url) == relative            # 改成了指向 查看/ 里那一页的绝对地址
    assert pane.count(REFRESH_30) == 1 and len(re.findall(r'http-equiv="refresh"', pane)) == 1   # 材料 30 秒刷新一次
    assert '<meta name="yunzhi-pane" content="view:dossier">' in pane
    assert body_of(pane).replace(cards_url, "资料卡片.html") == body_of(own)


def test_page_anchors_and_web_links_are_left_alone():
    page = ('<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="15"></head><body>'
            '<a href="#doc-ref-1">1</a><a href="https://example.com/a?b=1&amp;c=2">x</a><a href=\'sub/b.html#k\'>y</a></body></html>')
    out = yzlib.pane_html(page, os.path.join(ROOT, "projects", "某项目", "查看", "a.html"), "view:x")
    assert 'href="#doc-ref-1"' in out and 'href="https://example.com/a?b=1&amp;c=2"' in out
    target = progress.file_url(os.path.join(ROOT, "projects", "某项目", "查看", "sub", "b.html")) + "#k"
    assert "href='%s'" % target in out
    assert out.count('http-equiv="refresh"') == 1 and REFRESH_30 in out
    assert out.index('<meta charset="utf-8">') < out.index(REFRESH_30)    # 字符集照旧在最前面


# ---------------------------------------------------------------- 整页刷新后回到读到的位置

def marker_of(page):
    found = re.findall(r'<meta name="yunzhi-pane-content" content="([^"]*)">', page)
    assert len(found) == 1, found
    return html.unescape(found[0])


def script_of(page):
    head = page[:page.index("</head>")]
    found = re.findall(r"<script>(.*?)</script>", head, re.S)
    assert len(found) == 1, "右侧那一页的 head 里要有、而且只有一段记位置的脚本"
    return found[0]


def test_both_kinds_of_pane_carry_refresh_marker_and_scroll_script(world):
    project = world("S3")
    for source, kind, refresh in ((progress.write_page(project), "progress", REFRESH_5),
                                  (view.render(project, "dossier")[0], "view:dossier", REFRESH_30)):
        yzlib.write_pane(project, source, kind)
        page = read(pane_of(project))
        assert page.count(refresh) == 1 and len(re.findall(r'http-equiv="refresh"', page)) == 1
        assert marker_of(page).startswith(kind + "@") and yzlib.pane_content(project) == marker_of(page)
        js = script_of(page)
        assert "sessionStorage" in js and '"pagehide"' in js and '"scroll"' in js and "scrollTo(" in js
        for line in js.split("\n"):                               # 每一处读写存储都包在 try 里
            if re.search(r"sessionStorage|getItem|setItem", line):
                assert "try {" in line, line
        assert body_of(page).count("<script") == body_of(read(source)).count("<script")   # 正文不动


def test_the_marker_changes_when_the_content_changes(world):
    project = world("S1")
    plan = os.path.join(project, "task_plan.md")

    def show(kind):
        if kind == "progress":
            yzlib.write_pane(project, progress.write_page(project), "progress")
        else:
            yzlib.write_pane(project, view.render(project, kind)[0], "view:" + kind)
        return yzlib.pane_content(project)

    first = show("task_plan")
    assert first.startswith("view:task_plan@") and show("task_plan") == first     # 同一份材料再换进来:标记不变
    write(plan, read(plan).replace("scope_brief: H 省县级，含乡镇，不含村 · 2021–2025 年",
                                   "scope_brief: H 省县级和乡镇 · 2021–2025 年", 1))
    changed = show("task_plan")
    assert changed.startswith("view:task_plan@") and changed != first             # 材料改了:标记跟着变
    shown = show("progress")
    assert shown.startswith("progress@") and shown not in (first, changed)        # 换成进度页:换了内容
    assert show("progress") == shown


def test_progress_marker_follows_the_step_not_the_minutes():
    def page(now, rest):
        return ('<!doctype html><html><head><meta charset="utf-8"></head><body><div id="yz-progress">'
                '<div class="now"><span class="k">某项目 · 现在</span><span class="v">%s</span></div>%s</div>'
                '</body></html>' % (now, rest))

    src = os.path.join(ROOT, "projects", "某项目", "进度.html")

    def mark(p, kind="progress"):
        return marker_of(yzlib.pane_html(p, src, kind))

    a = mark(page("第 2 步 收集资料 · 正在收集资料", "<div>45 分钟 · 资料卡片 11 张</div>"))
    b = mark(page("第 2 步 收集资料 · 正在收集资料", "<div>2 小时 · 资料卡片 14 张</div>"))
    c = mark(page("第 2 步 收集资料 · <b>等你确认资料汇编</b>", "<div>2 小时 · 资料卡片 14 张</div>"))
    assert a == b            # 用时、张数变了:还是同一份内容,读到的位置照旧
    assert c != b            # 走到下一步、等你确认的事变了:从头看
    m1 = mark(page("x", "<p>第一版</p>"), "view:task_plan")
    assert m1 != mark(page("x", "<p>第二版</p>"), "view:task_plan")     # 材料看整页正文
    assert m1 != mark(page("x", "<p>第一版</p>"), "view:dossier")       # 正文一样、显示的东西换了,也算换了


# 在 node 里真跑一遍右侧那一页里的脚本(模拟浏览器:sessionStorage、滚动、DOMContentLoaded / load / pagehide)
NODE_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const script = fs.readFileSync(process.argv[2], "utf8");
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const out = [];
for (const c of cases) {
  const store = Object.assign({}, c.storage || {});
  const listeners = { window: {}, document: {} };
  const timers = [];
  const calls = [];
  let y = 0;
  const on = (where) => (type, fn) => { (listeners[where][type] = listeners[where][type] || []).push(fn); };
  const fire = (where, type) => { (listeners[where][type] || []).forEach((fn) => fn({ type: type })); };
  const storage = {
    getItem: (k) => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
  };
  const win = {
    get pageYOffset() { return y; },
    scrollTo: (x, to) => { calls.push(to); y = to; fire("window", "scroll"); },
    addEventListener: on("window"),
  };
  Object.defineProperty(win, "sessionStorage", {
    get() { if (c.storage_throws) { throw new Error("SecurityError: storage is disabled"); } return storage; },
  });
  const history = {};
  const sandbox = {
    window: win, history: history, JSON: JSON, Math: Math, location: { pathname: c.path },
    document: {
      querySelector: (sel) => (sel === 'meta[name="yunzhi-pane-content"]' ? { getAttribute: () => c.mark } : null),
      addEventListener: on("document"),
    },
    setTimeout: (fn) => { timers.push(fn); return timers.length; },
    clearTimeout: () => {},
  };
  vm.createContext(sandbox);
  let error = null;
  try {
    vm.runInContext(script, sandbox);
    fire("document", "DOMContentLoaded");
    fire("window", "load");
    if (typeof c.user_scroll === "number") { y = c.user_scroll; fire("window", "scroll"); }
    timers.splice(0).forEach((fn) => fn());
    fire("window", "pagehide");
  } catch (e) { error = String(e); }
  out.push({ name: c.name, error: error, calls: calls, storage: store,
             restoration: history.scrollRestoration === undefined ? null : history.scrollRestoration });
}
process.stdout.write(JSON.stringify(out));
"""


def test_reading_position_is_restored_only_for_the_same_content(world, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("没有 node，跑不了这一条（记位置的脚本另有上面的静态检查）")
    project = world("S3")
    yzlib.write_pane(project, view.render(project, "dossier")[0], "view:dossier")
    page = read(pane_of(project))
    mark = marker_of(page)
    path = "/C:/projects/某项目/右侧.html"
    key = "yunzhi-pane-scroll:" + path

    def slot(m, y):
        return json.dumps({"m": m, "y": y})

    cases = [
        {"name": "same", "mark": mark, "path": path, "storage": {key: slot(mark, 640)}},
        {"name": "switched", "mark": mark, "path": path, "storage": {key: slot("progress@000000000000", 640)}},
        {"name": "other_pane", "mark": mark, "path": path, "storage": {"yunzhi-pane-scroll:/C:/别的项目/右侧.html": slot(mark, 640)}},
        {"name": "no_storage", "mark": mark, "path": path, "storage_throws": True},
        {"name": "broken", "mark": mark, "path": path, "storage": {key: "{不是 JSON"}},
        {"name": "user_scrolls", "mark": mark, "path": path, "storage": {}, "user_scroll": 1234},
    ]
    script_file = tmp_path / "pane.js"
    script_file.write_text(script_of(page), encoding="utf-8")
    cases_file = tmp_path / "cases.json"
    cases_file.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    harness = tmp_path / "harness.js"
    harness.write_text(NODE_HARNESS, encoding="utf-8")
    p = subprocess.run([node, str(harness), str(script_file), str(cases_file)], capture_output=True)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    got = {r["name"]: r for r in json.loads(p.stdout.decode("utf-8"))}
    for r in got.values():
        assert r["error"] is None, r                                       # 哪种情况都不出错,页面照常显示
    assert got["same"]["calls"] and set(got["same"]["calls"]) == {640}     # 同一份内容:回到读到的位置
    assert json.loads(got["same"]["storage"][key]) == {"m": mark, "y": 640}
    assert got["same"]["restoration"] == "manual"                          # 不让浏览器自己再滚一次
    assert set(got["switched"]["calls"]) == {0}                            # 换了内容:从头看
    assert got["switched"]["restoration"] == "manual"
    assert json.loads(got["switched"]["storage"][key]) == {"m": mark, "y": 0}
    # 手里没有这一页记下的位置:不接管(不滚、不改浏览器自己的滚动恢复),只是从现在起开始记
    for name in ("other_pane", "broken", "user_scrolls"):                  # 别的项目记的、读不出来的、第一次打开
        assert got[name]["calls"] == [] and got[name]["restoration"] is None, got[name]
    assert json.loads(got["other_pane"]["storage"][key]) == {"m": mark, "y": 0}
    assert json.loads(got["broken"]["storage"][key]) == {"m": mark, "y": 0}               # 坏的记录被换成好的
    assert got["no_storage"]["calls"] == [] and got["no_storage"]["restoration"] is None   # 存不了:什么都不做
    assert json.loads(got["user_scrolls"]["storage"][key]) == {"m": mark, "y": 1234}       # 滚到哪记到哪


# ---------------------------------------------------------------- 进度页更新时右侧跟不跟

def test_pane_follows_the_progress_page_but_never_covers_a_material(world):
    project = world("S1")
    yzlib.write_pane(project, progress.write_page(project), "progress")
    plan = os.path.join(project, "task_plan.md")
    write(plan, read(plan).replace("scope_brief: H 省县级，含乡镇，不含村 · 2021–2025 年",
                                   "scope_brief: H 省县级和乡镇 · 2021–2025 年", 1))
    progress.write_page(project)                                        # 不带 --pane
    assert "H 省县级和乡镇" in read(pane_of(project))                    # 右侧正显示进度页:跟上
    yzlib.write_pane(project, view.render(project, "task_plan")[0], "view:task_plan")
    before = read(pane_of(project))
    progress.write_page(project)
    assert read(pane_of(project)) == before and yzlib.pane_kind(project) == "view:task_plan"   # 正显示材料:不盖掉


# ---------------------------------------------------------------- 出卡、新建、接着做、改名

@pytest.mark.parametrize("stage, kind, fields, shows", [
    ("S1", "task_plan", {"outside": []}, "view:task_plan"),
    ("S3", "dossier", {"outside": []}, "view:dossier"),
    ("S4", "outline", {}, "view:outline"),
    ("S5", "delivery", {}, "view:draft"),
    ("S2-flow", "flow_change", FLOW_FIELDS, "view:task_plan"),
])
def test_every_confirm_card_puts_its_material_in_the_pane(world, stage, kind, fields, shows):
    project = world(stage)
    out, code = card.prepare(kind, MAIN, dict(fields))
    assert code == 0, out
    assert out["pane_shows"] == shows and yzlib.pane_kind(project) == shows
    assert out["pane_url"] == progress.file_url(pane_of(project))
    assert "open_in_codex" not in out["next"] and "不用另外打开" in out["next"]


def test_new_status_and_rename_put_progress_in_the_pane(projects_root, capsys):
    out = projects.new_project("右侧测试项目", "想了解一个虚构县城的公交补贴效果，写一份三千字左右的说明。", [])
    project = os.path.join(projects_root, out["project"])
    assert out["pane_shows"] == "progress" and yzlib.pane_kind(project) == "progress"
    assert "yunzhi-progress 第 1 节" in out["next"] and "pane_url" in out["next"] and "只挂这一次" in out["next"]
    assert projects.main(["projects.py", "status", out["project"]]) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["pane_url"] == progress.file_url(pane_of(project)) and st["pane_shows"] == "progress"
    ren = projects.rename_project(out["project"], "改过名字的项目")
    moved = os.path.join(projects_root, "改过名字的项目")
    assert ren["pane_url"] == progress.file_url(pane_of(moved)) and yzlib.pane_kind(moved) == "progress"
    assert "重新挂一次" in ren["next"]


# ---------------------------------------------------------------- 只挂一次

def skill_texts():
    out = {}
    for name in sorted(os.listdir(SKILLS_DIR)):
        p = os.path.join(SKILLS_DIR, name, "SKILL.md")
        if os.path.isfile(p):
            out[name] = read(p)
    out["AGENTS.md"] = read(os.path.join(ROOT, "AGENTS.md"))
    return out


def test_skills_open_the_pane_exactly_once_and_only_on_first_open():
    docs = skill_texts()
    assert len(docs) >= 9
    hits = [(name, line) for name, text in docs.items() for line in text.split("\n") if "open_in_codex" in line]
    assert len(hits) == 1, hits
    name, line = hits[0]
    assert name == "yunzhi-progress"
    assert line.startswith("mcp__codex_app__open_in_codex(") and '"url": "<pane_url>"' in line and '"placement": "right"' in line
    text = docs["yunzhi-progress"]
    section = text[text.index("## 第 1 节"):]
    section = section[:section.index("\n## ")]
    assert line in section
    assert "一个对话最多挂一次" in section and "这个对话里第一次打开或接着做这个项目" in section    # 第一次
    assert "用户说右侧没了" in section                                                          # 或者用户说它没了
    assert "换页面（进度页、材料）从来不靠它" in section and "别的时候一律不调" in section


def test_scripts_never_tell_the_agent_to_open_the_pane():
    for fn in sorted(os.listdir(TOOLS)):
        if fn.endswith(".py"):
            assert "open_in_codex" not in read(os.path.join(TOOLS, fn)), fn


def test_workbuddy_copies_of_the_skills_are_in_sync():
    p = subprocess.run([sys.executable, "-X", "utf8", "-B", os.path.join(TOOLS, "sync_skills.py"), "--check"],
                       capture_output=True, cwd=ROOT)
    assert p.returncode == 0, p.stdout.decode("utf-8", "replace")
