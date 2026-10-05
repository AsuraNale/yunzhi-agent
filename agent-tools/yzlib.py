# -*- coding: utf-8 -*-
"""yzlib — agent-tools/ 下各脚本共用的小工具。

- 路径:工作区根、toolkit/scripts(从这里 import pipeline_lib 等,不另写一份)、projects/、wording/;
- 项目:按名字或路径找到项目文件夹;
- 记录:records/*.jsonl 的读写;
- 运行 toolkit 脚本:用当前解释器、-X utf8、-B(不在 toolkit 里留 __pycache__);
- 读 agent 写的输入文件(UTF-8 优先,兼容 PowerShell 写出的 UTF-16 / GBK)。

测试钩子:环境变量 YUNZHI_PROJECTS 指向别的 projects 文件夹(测试用临时目录)。
"""
import hashlib
import html
import io
import json
import os
import re
import subprocess
import sys

sys.dont_write_bytecode = True

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLKIT = os.path.join(ROOT, "toolkit")
SCRIPTS = os.path.join(TOOLKIT, "scripts")
WORDING = os.path.join(ROOT, "wording")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import pipeline_lib as pl  # noqa: E402  (toolkit 的参考实现,只调用不重写)
from stamp import now_iso  # noqa: E402  (与确认记录同一个时钟:东部时间)


def projects_root():
    return os.environ.get("YUNZHI_PROJECTS") or os.path.join(ROOT, "projects")


TMP_ROOT = os.path.join(ROOT, ".yz-tmp")


def inside_workspace(path):
    """这个路径在不在工作区(本文件夹)里面。快照这类给对话用的文件不许写进来:写进 out/ 就成了「成稿」。"""
    try:
        a = os.path.normcase(os.path.abspath(str(path)))
        root = os.path.normcase(os.path.abspath(ROOT))
        return os.path.commonpath([a, root]) == root
    except ValueError:      # 不在同一个盘上
        return False


def inside_protected(path):
    """在工作区里,或者在 projects 文件夹里(测试时它可以在别处):给对话用的文件(快照)都不许写进来。"""
    if inside_workspace(path):
        return True
    try:
        a = os.path.normcase(os.path.abspath(str(path)))
        root = os.path.normcase(os.path.abspath(projects_root()))
        return os.path.commonpath([a, root]) == root
    except ValueError:
        return False


def work_tmp(prefix="tmp"):
    """工作区里的临时文件夹 <工作区>/.yz-tmp/<prefix>-<随机>,用完由调用方删掉(shutil.rmtree)。
    ⛔ 不用 tempfile.mkdtemp / TemporaryDirectory:Python 3.12 起它们在 Windows 上建的文件夹只给当前用户权限,
    Codex 沙盒的身份写不进去(10-03 端到端实测:出不了确认卡)。os.makedirs 建的文件夹沿用上级文件夹的权限。"""
    import secrets
    os.makedirs(TMP_ROOT, exist_ok=True)
    for _ in range(50):
        path = os.path.join(TMP_ROOT, "%s-%s" % (prefix, secrets.token_hex(4)))
        try:
            os.makedirs(path)
            return path
        except FileExistsError:
            continue
    raise UsageError("工作区的临时文件夹 .yz-tmp 里建不了新文件夹")


def clean_agent_tmp(project):
    """交付之后清掉助手自己临时用的 .yz-tmp/<项目名>/(第八轮:交付后留着一个空文件夹)→ 清了没有。
    只动这一个文件夹;脚本自己的临时文件夹(.yz-tmp/<用途>-<随机>)不动。TMP_ROOT 在调用时才读(测试可以换)。"""
    import shutil
    name = project_name(project)
    if not name or name in (".", "..") or os.path.basename(name) != name:
        return False
    path = os.path.join(TMP_ROOT, name)
    if not os.path.isdir(path):
        return False
    shutil.rmtree(path, ignore_errors=True)
    return not os.path.exists(path)


# 交给脚本的几份临时文件(脚本读完就删;留下的就是没用上的)
INBOX_FILES = ("request.md", "fields.json", "answer.txt")


def clean_inbox(project):
    """交付之后清掉这个项目交给脚本的临时文件夹 projects/_inbox/<项目名>/(第九轮:交付后留着一个空文件夹)→ 清了没有。
    只在里面是空的、或者只剩没用上的那几份临时文件(request.md、fields.json、answer.txt)时清;有别的东西就不动。
    别的项目的文件夹、共用的 projects/_inbox/ 本身都不动(别的对话可能正在用)。"""
    name = project_name(project)
    if not name or name in (".", "..", PICK_INBOX) or os.path.basename(name) != name:
        return False
    path = os.path.join(shared_inbox(), name)
    if not os.path.isdir(path):
        return False
    try:
        names = os.listdir(path)
    except OSError:
        return False
    if any(n not in INBOX_FILES or not os.path.isfile(os.path.join(path, n)) for n in names):
        return False
    try:
        for n in names:
            os.remove(os.path.join(path, n))
        os.rmdir(path)
    except OSError:
        return False
    return True


def file_url(path):
    """file:/// 地址:正斜杠,中文照原样(10-02 实测的写法);只转义 % 空格 # ? 这几个会让地址断开的字符。"""
    p = os.path.normpath(os.path.abspath(path)).replace("\\", "/")
    for ch, rep in (("%", "%25"), (" ", "%20"), ("#", "%23"), ("?", "%3F")):
        p = p.replace(ch, rep)
    return "file:///" + p.lstrip("/")


def write_atomic(path, text):
    """整份先写进同一个文件夹里的临时文件,再一次换名换上(os.replace):读的人(右侧每几秒刷新一次)看到的
    不是旧的就是新的,不会是写了一半的。Windows 上目标文件正被浏览器读着的那一下换名会失败:稍等重试;
    一直不行就删掉临时文件、报错。临时文件不用 tempfile(Python 3.12 起它在 Windows 上建的东西只给当前用户
    权限,Codex 沙盒写不进去,见 work_tmp);os.open 建的文件沿用文件夹的权限。"""
    import secrets
    import time
    folder = os.path.dirname(os.path.abspath(path))
    tmp = os.path.join(folder, ".%s.%s.tmp" % (os.path.basename(path), secrets.token_hex(4)))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(text.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(40):
            try:
                os.replace(tmp, path)
                return path
            except PermissionError:
                if attempt == 39:
                    raise
                time.sleep(0.05)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


# ---- 右侧那一页:Codex 右侧浏览器里固定挂着的一页(每个项目一页) ----
# 10-03 实测:Codex 每往右侧打开一次网页都多开一个标签,旧的还在;也不回标签号。所以右侧只挂一次这一页,
# 之后要显示进度页还是某份材料,都由脚本把那一页的内容换进来(原子写),它自己定时刷新(见 pane_refresh)。
PANE_NAME = "右侧.html"
# 显示进度页时 5 秒刷新一次:进度要跟得上(助手做完一步、答完一张卡,右侧马上看得到)。
PANE_REFRESH = 5
# 显示材料时 30 秒:材料只在用户答完卡(换回进度页)或换下一份材料时才变 —— 新一版材料总是先经过进度页
# (5 秒)才换上来,所以慢的只是「答完卡后进度页回来」这一下,最多半分钟;而长材料(成稿尤其)要读好几分钟,
# 每刷新一次就丢一次选中的文字和页内查找、还可能闪一下,30 秒比 5 秒少六倍。读到的位置刷新后照旧(见 PANE_SCROLL_JS)。
PANE_REFRESH_VIEW = 30
_REFRESH_RE = re.compile(r"<meta\s[^>]*http-equiv\s*=\s*[\"']?refresh[\"']?[^>]*>[ \t]*\n?", re.I)
_PANE_MARK_RE = re.compile(r"<meta name=\"yunzhi-pane\" content=\"([^\"]*)\">")
_PANE_CONTENT_RE = re.compile(r"<meta name=\"yunzhi-pane-content\" content=\"([^\"]*)\">")
_NOW_BLOCK_RE = re.compile(r"<div class=\"now\">.*?</div>", re.S)
_BODY_RE = re.compile(r"<body\b[^>]*>(.*)</body>", re.S | re.I)
# 右侧整页刷新时记住读到哪了:滚动时(最多每 250 毫秒记一次)和离开这一页时把位置记进 sessionStorage,
# 刷新后只在显示的还是同一份内容(标记 yunzhi-pane-content 相同)时回到原处;换了内容(进度页换材料、
# 换了一份材料、换了一版)就从头看。只在手里有这一页记下的位置时才接管滚动:第一次打开、存储用不了
# (读写抛错)、记下的读不出来,都什么都不做 —— 页面照常显示、照常刷新,浏览器自己怎么滚就怎么滚
# (10-03 实测:Chromium 自己会在刷新后回到原处,但换了内容也回原处;所以有记录时由这里接管)。
PANE_SCROLL_JS = """<script>
(function () {
  var meta = document.querySelector('meta[name="yunzhi-pane-content"]');
  var mark = meta ? meta.getAttribute("content") : "";
  var key = "yunzhi-pane-scroll:" + location.pathname;
  var store, saved = null, target = 0, timer = 0;
  try { store = window.sessionStorage; store.getItem(key); } catch (e) { return; }
  try { saved = JSON.parse(store.getItem(key) || "null"); } catch (e) { saved = null; }
  function save() {
    if (timer) { clearTimeout(timer); timer = 0; }
    try { store.setItem(key, JSON.stringify({ m: mark, y: Math.round(window.pageYOffset || 0) })); } catch (e) {}
  }
  function restore() {
    try { window.scrollTo(0, target); } catch (e) {}
  }
  if (saved && typeof saved === "object") {
    if (saved.m === mark && typeof saved.y === "number" && saved.y > 0) { target = saved.y; }
    try { history.scrollRestoration = "manual"; } catch (e) {}
    document.addEventListener("DOMContentLoaded", restore);
    window.addEventListener("load", restore);
  }
  window.addEventListener("scroll", function () { if (!timer) { timer = setTimeout(save, 250); } });
  window.addEventListener("pagehide", save);
})();
</script>"""
_HEAD_RE = re.compile(r"<head\b[^>]*>(?:\s*<meta\s+charset=[^>]*>)?", re.I)
_ATTR_URL_RE = re.compile(r"(\s(?:href|src)\s*=\s*)([\"'])(.*?)\2", re.I | re.S)
_ABSOLUTE_URL_RE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.\-]*:|#|//|/)")


def pane_path(project):
    return os.path.normpath(os.path.abspath(os.path.join(project, PANE_NAME)))


def pane_kind(project):
    """右侧那一页现在显示什么:"progress"(进度页)或 "view:<材料>";还没有这一页、认不出 → None。"""
    path = pane_path(project)
    if not os.path.isfile(path):
        return None
    try:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            m = _PANE_MARK_RE.search(f.read())
    except OSError:
        return None
    return html.unescape(m.group(1)) if m else None


def pane_refresh(kind):
    """右侧那一页几秒刷新一次:显示材料 PANE_REFRESH_VIEW 秒,显示进度页(和别的)PANE_REFRESH 秒。"""
    return PANE_REFRESH_VIEW if str(kind).startswith("view:") else PANE_REFRESH


def pane_marker(page, kind):
    """右侧那一页显示的是什么、是哪一版 →「显示什么@摘要」,刷新后要不要回到读到的位置就看它变没变。
    - 材料:材料页正文的摘要 —— 同一份材料再换进来,位置照旧;改了内容、出了新一版、换了别的材料,从头看;
    - 进度页:页头「现在在哪一步」那一块的摘要 —— 用时、张数、记录跟着变不算换内容(位置照旧),
      走到下一步、等你确认的事变了,才从头看。找不到页头就用整页正文(宁可多回到顶上)。"""
    part = None
    if kind == "progress":
        m = _NOW_BLOCK_RE.search(page)
        part = m.group(0) if m else None
    if part is None:
        m = _BODY_RE.search(page)
        part = m.group(1) if m else page
    return "%s@%s" % (kind, hashlib.sha256(part.encode("utf-8")).hexdigest()[:12])


def pane_content(project):
    """右侧那一页现在的内容标记(pane_marker);没有这一页、认不出 → None。"""
    path = pane_path(project)
    if not os.path.isfile(path):
        return None
    try:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            m = _PANE_CONTENT_RE.search(f.read())
    except OSError:
        return None
    return html.unescape(m.group(1)) if m else None


def pane_html(page, source_path, kind):
    """一份完整网页 → 右侧那一页:去掉原来的自动刷新,换成每 pane_refresh(kind) 秒刷新一次,标上显示的是什么、
    内容标记(pane_marker),加上记住读到哪了的那段脚本(PANE_SCROLL_JS);
    相对地址改成绝对地址(右侧这一页在项目文件夹根上,材料页在 查看/ 里,互相链着)。页内 #锚点、网址照旧。"""
    from urllib.parse import unquote
    base = os.path.dirname(os.path.abspath(source_path))

    def absolute(m):
        raw = html.unescape(m.group(3)).strip()
        if not raw or _ABSOLUTE_URL_RE.match(raw):
            return m.group(0)
        rest, sep, frag = raw.partition("#")
        target = file_url(os.path.join(base, unquote(rest))) + (sep + frag if sep else "")
        return "%s%s%s%s" % (m.group(1), m.group(2), html.escape(target, quote=True), m.group(2))

    marker = pane_marker(page, kind)
    text = _ATTR_URL_RE.sub(absolute, _REFRESH_RE.sub("", page))
    tags = "".join([
        '\n<meta http-equiv="refresh" content="%d">' % pane_refresh(kind),
        '\n<meta name="yunzhi-pane" content="%s">' % html.escape(kind, quote=True),
        '\n<meta name="yunzhi-pane-content" content="%s">' % html.escape(marker, quote=True),
        "\n" + PANE_SCROLL_JS,
    ])
    m = _HEAD_RE.search(text)
    if not m:
        return tags.lstrip("\n") + "\n" + text
    return text[:m.end()] + tags + text[m.end():]


def write_pane(project, source_path, kind):
    """把一份完整网页(进度页、材料页)换进右侧那一页,原子写 → 右侧那一页的路径。原页面不动。"""
    with io.open(source_path, encoding="utf-8") as f:
        page = f.read()
    return write_atomic(pane_path(project), pane_html(page, source_path, kind))


def pane_info(project, source_path, kind):
    """write_pane,结果给 agent 看:{pane, pane_url, pane_shows};没换成 → {pane_error}(不挡别的事)。"""
    try:
        path = write_pane(project, source_path, kind)
    except Exception as e:  # noqa: BLE001  右侧没换成不挡卡片、不挡进度
        return {"pane_error": "右侧那一页没换成（%s: %s）" % (type(e).__name__, e)}
    return {"pane": path, "pane_url": file_url(path), "pane_shows": kind}


class UsageError(Exception):
    """用法错:退出码 2。"""


def project_dir(name_or_path, must_exist=True):
    """项目名(projects/ 下的文件夹名)或路径 → 绝对路径。"""
    if not name_or_path or not str(name_or_path).strip():
        raise UsageError("要给项目名(projects/ 下的文件夹名)")
    raw = str(name_or_path).strip().rstrip("/\\")
    candidates = []
    if os.path.isabs(raw) or "/" in raw or "\\" in raw:
        candidates.append(os.path.abspath(raw))
        candidates.append(os.path.abspath(os.path.join(ROOT, raw)))
    candidates.append(os.path.join(projects_root(), raw))
    for c in candidates:
        if os.path.isdir(c):
            return c
    if must_exist:
        raise UsageError("找不到项目「%s」:projects/ 下没有这个文件夹(先跑 projects.py list 看有哪些)" % raw)
    return candidates[-1]


def project_name(path):
    return os.path.basename(os.path.normpath(path))


def records_dir(project):
    d = os.path.join(project, "records")
    os.makedirs(d, exist_ok=True)
    return d


# ---- 交给脚本的临时文件放在哪(第五轮 L4) ----
# 10-04 实测:projects/_inbox/ 是所有对话共用的,两个对话同时新建项目、出卡,会互相覆盖 request.md、answer.txt。
# 现在每个项目自己一个文件夹:projects/_inbox/<项目名>/(新建项目时项目还没有,只能放在这里);选项目的卡用 _inbox/选项目/。
INBOX = "_inbox"
PICK_INBOX = "选项目"


def shared_inbox():
    return os.path.join(projects_root(), INBOX)


def inbox_file(project_name, name):
    """交给脚本的一份临时文件,写在命令里的样子:projects/_inbox/<项目名>/<name>。"""
    return "projects/%s/%s/%s" % (INBOX, project_name, name)


def in_shared_inbox_root(path):
    """这份文件是不是直接放在共用的 projects/_inbox/ 下面(没有分到某个项目自己的文件夹里)。"""
    try:
        here = os.path.normcase(os.path.dirname(os.path.abspath(path)))
        return here == os.path.normcase(os.path.abspath(shared_inbox()))
    except (TypeError, ValueError):
        return False


# ---- 项目文件夹里不该有的东西(第五轮 M3 / M4) ----
# 10-04 实测:助手把自己写的 archive_delivery.py、read_source.py、source_images.py 放进了 records/;
# 从网上取来的 14 个网页、PDF、图放进了 inputs/originals/,inputs/materials.md 却写着「没有添加文件」。
FETCHED = "fetched"           # 取来的资料:fetched/originals/(原件)· fetched/converted/(转成的文字)· fetched/index.md(清单)
SCRIPT_EXTS = (".py", ".pyw", ".pyc", ".ps1", ".psm1", ".bat", ".cmd", ".sh", ".js", ".mjs", ".cjs", ".vbs")
# 这两处放的是原件(用户给的、网上取来的),什么格式都可能有,不当成助手写的脚本
ORIGINALS = (("inputs", "originals"), (FETCHED, "originals"))


def read_project_info(project):
    """records/project.json(projects.py new 写的项目登记)→ dict;没有或读不出 → None。"""
    path = os.path.join(project, "records", "project.json")
    if not os.path.isfile(path):
        return None
    try:
        with io.open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def stray_scripts(project):
    """项目文件夹里助手自己写的脚本(和运行它们留下的 __pycache__)→ 相对路径列表。原件文件夹不算。"""
    out = []
    for root, dirs, files in os.walk(project):
        rel_root = os.path.relpath(root, project).replace("\\", "/")
        parts = () if rel_root == "." else tuple(rel_root.split("/"))
        if parts[:2] in ORIGINALS:
            dirs[:] = []
            continue
        if "__pycache__" in dirs:
            out.append("/".join(parts + ("__pycache__",)))
            dirs.remove("__pycache__")
        for fn in sorted(files):
            if os.path.splitext(fn)[1].lower() in SCRIPT_EXTS:
                out.append("/".join(parts + (fn,)))
    return sorted(out)


def stray_inputs(project):
    """inputs/ 里不是用户给的文件(登记里没有的)→ 相对路径列表。没有 records/project.json(不是 projects.py 建的项目)
    就判断不了,返回 []。用户的材料只经 projects.py new / add 进来,登记里记着原件和转成的文字。"""
    info = read_project_info(project)
    if info is None or not isinstance(info.get("materials"), list):
        return []
    allowed = {"inputs/request.md", "inputs/materials.md"}
    for m in info["materials"]:
        if not isinstance(m, dict):
            continue
        if isinstance(m.get("stored"), str):
            allowed.add(m["stored"].replace("\\", "/"))
        for o in m.get("outputs") or []:
            if isinstance(o, str):
                allowed.add(o.replace("\\", "/"))
    out = []
    base = os.path.join(project, "inputs")
    for root, _dirs, files in os.walk(base):
        for fn in files:
            if fn.startswith(("~$", ".")) or fn.lower() in SYSTEM_FILES:
                continue        # Word / Excel 开着材料时旁边的锁文件、系统自己留的文件:不是谁放进来的材料
            rel = os.path.relpath(os.path.join(root, fn), project).replace("\\", "/")
            if rel not in allowed:
                out.append(rel)
    return sorted(out)


SYSTEM_FILES = ("thumbs.db", "desktop.ini")


def hygiene_problems(project):
    """项目文件夹里不该有的东西 → 给助手的改法(出卡前查,不过就不出卡;自己改,不用问用户)。"""
    out = []
    name = project_name(project)
    scripts = stray_scripts(project)
    if scripts:
        out.append("项目文件夹里有助手写的脚本（%s）：项目文件夹只放研究材料，不放脚本。临时用的脚本和下载下来的文件放 "
                   ".yz-tmp/%s/ 里，用完删掉；把这几个移过去或删掉，再出卡。要做的事有现成的脚本："
                   "转换取来的资料用 fetches.py save，交付后归档用 archive.py" % ("、".join(scripts[:5]) + (" 等" if len(scripts) > 5 else ""), name))
    extra = stray_inputs(project)
    if extra:
        out.append("inputs/ 里有没登记过的文件（%s）：inputs/ 只放用户自己的材料，而且只经脚本放进来（清单是 inputs/materials.md）。"
                   "从网上取来的资料用 $PY \"agent-tools/fetches.py\" save \"%s\" --file \"<那个文件>\" --url \"<网址>\" 存进 fetched/"
                   "（会顺手转成文字、从 inputs/ 里挪走）；用户后来又给的材料用 $PY \"agent-tools/projects.py\" add \"%s\" --material \"<文件>\" "
                   "加进来（会登记、转换）；你自己转出来的文字直接删掉。收拾好再出卡"
                   % ("、".join(extra[:5]) + (" 等" if len(extra) > 5 else ""), name, name))
    return out


def append_jsonl(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")


def read_jsonl(path):
    """→ 列表;读不出的行跳过(不让一行坏数据挡住整个流程)。"""
    out = []
    if not os.path.isfile(path):
        return out
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    return out


def card_log(project):
    return os.path.join(project, "records", "cards.jsonl")


def message_log(project):
    return os.path.join(project, "records", "messages.jsonl")


def read_input_file(path, consume=True):
    """读 agent 写的输入文件 → 文字。UTF-8(可带 BOM)优先;PowerShell 5.1 写出的 UTF-16 / GBK 也认。
    consume=True 时读完就删(这几份是一次性的输入,原文已经进了记录)。"""
    if not os.path.isfile(path):
        raise UsageError("找不到输入文件 %s" % path)
    with io.open(path, "rb") as f:
        raw = f.read()
    text = None
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16")
    else:
        for enc in ("utf-8-sig", "gb18030"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    if text is None:
        raise UsageError("输入文件 %s 读不出来(请用 UTF-8 保存)" % path)
    if consume:
        try:
            os.remove(path)
        except OSError:
            pass
    return text.replace("\r\n", "\n")


def user_texts(project):
    """用户写过的话:委托原话(inputs/request.md)+ 用户说过的话。卡上的引文只认这里面的。
    第六轮:守门脚本在跑(当前会话有它的记录,见 sessions.active)时,用户说过的话只认它记下的(任一会话):
    对话里的原话,和原生卡上用户写的那段话(那不经对话)—— 那份记录助手写不了;records/messages.jsonl 是助手经
    note-user 记的,这时不再拿来核引文(照记,别处还用)。守门脚本没跑(文件夹没信任)时照旧认 messages.jsonl。"""
    import sessions
    out = []
    req = os.path.join(project, "inputs", "request.md")
    if os.path.isfile(req):
        with io.open(req, encoding="utf-8", errors="replace") as f:
            out.append(f.read())
    if sessions.active():
        return out + sessions.all_user_words()
    for m in read_jsonl(message_log(project)):
        t = m.get("text")
        if isinstance(t, str) and t.strip():
            out.append(t)
    return out


def run_toolkit(script, args, timeout=600):
    """跑 toolkit/scripts 里的一个脚本 → (退出码, 标准输出, 标准错误)。"""
    cmd = [sys.executable, "-X", "utf8", "-B", os.path.join(SCRIPTS, script)] + [str(a) for a in args]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env)
    out = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace")
    return proc.returncode, out, err


def emit(obj, code=0):
    """打印 JSON 结果(给 agent 读),返回退出码。"""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=1, default=str) + "\n")
    sys.stdout.flush()
    return code


def cwd_problem():
    """命令不是在工作区根目录下跑的 → 一句改法;是 → None。
    第四轮实测:助手在上一级文件夹里跑了命令,相对路径(projects/…、回答文件)全对不上,它又停下来让用户打「继续」。
    脚本自己的位置不受影响,但交给脚本的路径都是从工作区根目录算的,所以在这里拦下,说清怎么改。"""
    def norm(p):
        return os.path.normcase(os.path.realpath(os.path.abspath(p)))
    try:
        here = os.getcwd()
    except OSError:
        here = ""
    if here and norm(here) == norm(ROOT):
        return None
    return ("命令要在工作区根目录下跑（现在在 %s）：先回到 %s（cd 到这个文件夹），再原样跑同一条命令。"
            "这是命令写法的问题，自己改了重跑，不用问用户。" % (here or "未知的文件夹", ROOT))


def run_main(fn, argv):
    """各脚本的入口:不在工作区根目录 → 退出码 2;用法错 → 退出码 2;脚本自己出错 → 退出码 3。
    都打印一行 JSON,不留空的标准输出和裸的报错栈。退出码 1 / 2 是可以照着改、自己重跑的;3 才要告诉用户。"""
    problem = cwd_problem()
    if problem:
        return emit({"ok": False, "error": problem, "retry": "自己改了重跑"}, 2)
    try:
        return fn(argv)
    except UsageError as e:
        return emit({"ok": False, "error": str(e)}, 2)
    except Exception as e:  # noqa: BLE001  脚本内部出错:照实报,不让 agent 对着空输出猜
        import traceback
        where = traceback.extract_tb(e.__traceback__)[-1] if e.__traceback__ else None
        at = " · %s 第 %d 行" % (os.path.basename(where.filename), where.lineno) if where else ""
        return emit({"ok": False, "error": "脚本出错了（%s: %s%s）。这不是用户的问题：停在这里，用一两句话告诉用户出了故障，不要改脚本，也不要绕过它。"
                     % (type(e).__name__, e, at), "retry": "不要重跑，告诉用户"}, 3)


def setup_stdout():
    """Windows 控制台下也按 UTF-8 输出(配合 -X utf8)。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


# ---- 状态与环节(机器面全部取自 pipeline_lib;用户叫法取自 wording/glossary.json) ----

def glossary():
    with io.open(os.path.join(WORDING, "glossary.json"), encoding="utf-8") as f:
        return json.load(f)


_G = None


def _g():
    global _G
    if _G is None:
        _G = glossary()
    return _G


def stage_name(stage_id):
    for s in _g()["stages"]:
        if s["id"] == stage_id:
            return s["zh"]
    raise KeyError(stage_id)


def state_phrase(state):
    return _g()["statuses"].get(state)


def stage_of_state(state):
    for s, states in pl.STAGE_STATES.items():
        if state in states:
            return s
    return None


DOC_NAMES = {"task_plan.md": "任务计划", "dossier.md": "资料汇编", "outline.md": "提纲"}


def load_task_plan(project):
    """→ (meta, body, problem);文件不在 → (None, None, None)。"""
    path = os.path.join(project, "task_plan.md")
    if not os.path.isfile(path):
        return None, None, None
    return pl.load_md(path)


def project_stages(meta):
    """任务计划的 stages;读不出时按四个环节(最严),第二个值是错误说明。"""
    stages, err = pl.parse_stages(meta or {})
    return (stages if stages is not None else list(pl.STAGES)), err


def pipeline_facts(project):
    """开场检查(pipeline_status.check)→ (facts, items, 人读那一行)。进程内调用,不另写一份。"""
    import pipeline_status
    rep = pl.Report("pipeline_status")
    line = pipeline_status.check(project, rep)
    return rep.facts, rep.items, line


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def approval_of(meta):
    ap = meta.get("approval") if isinstance(meta, dict) else None
    return ap if isinstance(ap, dict) else {}


def approval_stale(meta, body):
    """任务计划确认过、确认之后内容又变了(确认记录的 hash 对不上现在的内容)。没确认过 → False。"""
    ap = approval_of(meta)
    if ap.get("status") != "approved" or not ap.get("approved_hash"):
        return False
    return ap.get("approved_hash") != pl.content_hash(meta, body or "")


def flow_change_logged(meta):
    """revision_log 最后一条是这次改流程:what 以「改流程：」开头、v 等于当前 version(规格 §①)。"""
    log = meta.get("revision_log") if isinstance(meta, dict) else None
    if not isinstance(log, list) or not log or not isinstance(log[-1], dict):
        return False
    last = log[-1]
    what = last.get("what")
    return (isinstance(what, str) and what.startswith(("改流程：", "改流程:"))
            and _is_int(last.get("v")) and last.get("v") == meta.get("version"))


def flow_change_match(meta, body):
    """这一版是不是只改了流程:把 version 减一、stages 换回四种合法写法之一(没有这个键 / null / 四个环节 /
    省掉提纲),能还原出上次确认时的 hash → 返回还原用的那一种('absent'、None 或列表);还原不出 → None。"""
    ap = approval_of(meta)
    version = meta.get("version") if isinstance(meta, dict) else None
    if not ap.get("approved_hash") or not _is_int(version):
        return None
    import copy
    for cand in ("absent", None, list(pl.STAGES), [s for s in pl.STAGES if s != "outline"]):
        m2 = copy.deepcopy(meta)
        m2["version"] = version - 1
        if cand == "absent":
            m2.pop("stages", None)
        else:
            m2["stages"] = cand
        if pl.content_hash(m2, body or "") == ap.get("approved_hash"):
            return cand
    return None


def flow_change_pending(meta, body):
    """改流程卡还在等用户(规格 §①):三条都成立才算 ——
    ① 任务计划确认过,而且确认之后又改过(确认记录对不上现在的内容);
    ② revision_log 最后一条以「改流程：」开头、v 等于当前 version;
    ③ 只改了流程(stages 与 version 还原回去就是上次确认的那一版)。
    改流程卡一确认,确认记录就对得上现在的内容,①不成立,这个标记随之消失;
    ③ 让「改流程之后又动了别处」报成真正的对不上,而不是一张出不来的改流程卡。"""
    if not isinstance(meta, dict):
        return False
    return approval_stale(meta, body) and flow_change_logged(meta) and flow_change_match(meta, body) is not None


# 卡片记录里表示「这张卡已经处理过」的几种类型(处理过的卡不能再回答,要再问就重新出卡)
CARD_CLOSED = ("answered", "skipped", "changed", "unclear", "error")


def state_of_project(project):
    """任务计划里记的进度(pipeline_status);没有任务计划或读不出 → None。"""
    meta, _body, problem = load_task_plan(project)
    if not isinstance(meta, dict) or problem:
        return None
    return meta.get("pipeline_status")


def approved_valid(project, doc):
    """这份材料确认过、而且确认之后内容没变(确认记录的 hash 对得上现在的内容)。"""
    path = os.path.join(project, doc)
    if not os.path.isfile(path):
        return False
    meta, body, problem = pl.load_md(path)
    if problem:
        return False
    ap = approval_of(meta)
    return ap.get("status") == "approved" and bool(ap.get("approved_hash")) and ap.get("approved_hash") == pl.content_hash(meta, body)


def parse_iso(value):
    """ISO-8601(带时区)→ datetime;读不出、不带时区 → None。"""
    from datetime import datetime
    value = pl.date_text(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        t = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else None


def answered_decisions(project, since=None):
    """records/cards.jsonl 里用户在决定卡上选了一项的回答(点了选项才算;只写意见、跳过都不算)→ 列表;
    since(ISO 时刻)给了就只要那之后的。"""
    t0 = parse_iso(since) if since else None
    out = []
    for e in read_jsonl(card_log(project)):
        if e.get("type") != "answered" or e.get("kind") != "decision" or e.get("choice") in (None, ""):
            continue
        if e.get("result") not in (None, "selected"):
            continue
        t = parse_iso(e.get("at"))
        if t0 is not None and (t is None or t <= t0):
            continue
        out.append(e)
    return out


def update_progress_md(project):
    """PROGRESS.md 第一行 = 「<项目名> · <进度> · <日期>」(开场检查读这一行);其余几行(助手记的进度笔记)不动。
    没有任务计划时进度写 planning。→ 写了没有。"""
    path = os.path.join(project, "PROGRESS.md")
    state = state_of_project(project) or "planning"
    first = "%s · %s · %s" % (project_name(project), state, now_iso()[:10])
    rest = []
    if os.path.isfile(path):
        try:
            with io.open(path, encoding="utf-8") as f:
                lines = f.read().split("\n")
        except (OSError, UnicodeDecodeError):
            lines = []
        if lines and lines[0].strip() == first:
            return False
        # 原来的第一行是这一行的旧样子(「名 · 进度 · 日期」)就换掉;是别的(标题、笔记)就留着,新的一行放在它前面
        old = bool(lines) and re.match(r"^.+ · [a-z0-9_]+ · \d{4}-\d{2}-\d{2}$", lines[0].strip()) is not None
        rest = lines[1:] if old else lines
    text = first + "\n" + "\n".join(rest).lstrip("\n")
    if not text.endswith("\n"):
        text += "\n"
    write_atomic(path, text)
    return True


def append_progress_note(project, what):
    """PROGRESS.md 末尾记一行「- <月>-<日> <事>」(助手的进度笔记;第一行由 update_progress_md 管)。
    第四轮:资料汇编确认了却没有记(只有任务计划、提纲那两步的说明里叫助手记)—— 确认、交付这几件事由脚本自己记。"""
    path = os.path.join(project, "PROGRESS.md")
    now = now_iso()
    line = "- %d-%s %s" % (int(now[5:7]), now[8:10], what)
    text = ""
    if os.path.isfile(path):
        try:
            with io.open(path, encoding="utf-8") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError):
            return False
    if text and not text.endswith("\n"):
        text += "\n"
    write_atomic(path, text + line + "\n")
    update_progress_md(project)
    return True
