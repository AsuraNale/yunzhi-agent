# -*- coding: utf-8 -*-
"""projects — 研究项目:新建、加材料、列出、看状态。

用法(在工作区根目录下跑;$PY 见 AGENTS.md):
  $PY agent-tools/projects.py new --title "<项目名>" --request-file <委托原话文件> [--material "<文件>" ...]
      建 projects/<项目名>/:inputs/request.md(委托原话,逐字)· inputs/originals/(材料原件)·
      inputs/converted/(用 toolkit 的 convert_*.py 转成的文字)· inputs/materials.md(清单,四态照实写)·
      records/ · cards/ · drafts/ · out/ · review/ · library/(先建好空文件夹)· PROGRESS.md · 进度.html ·
      右侧.html(右侧那一页,先显示进度页;输出里的 pane_url 是这个对话里唯一要挂一次的地址)。
      委托原话只从文件读(不收命令行里的文字),--request-file 读完就删。
      同名文件夹已在时自动加 (2)、(3)…
  $PY agent-tools/projects.py add "<项目名>" --material "<文件>" [...]
      给已有项目加材料(同样转换、更新清单)。
  $PY agent-tools/projects.py list
      列出项目和各自在哪一步。
  $PY agent-tools/projects.py status "<项目名>"
      这个项目在哪一步、该用哪份 skill、有没有卡在等用户、进度和文件对不对得上;顺手重新生成进度页,
      右侧那一页换成进度页(输出里有 pane_url)。
  $PY agent-tools/projects.py rename "<项目名>" --to "<新名字>"
      改项目名(文件夹跟着改;右侧那一页的地址会变,要按新的 pane_url 重新挂一次)。
  $PY agent-tools/projects.py now
      打印现在的时刻(ISO-8601 带时区),写 updated_at、revision_log 的 at 时照抄,不要自己估。
  $PY agent-tools/projects.py count "<项目名>"
      资料卡片张数(不算弃用的,与交付前检查同一个数法)和四类各几张。

材料转换的四态(与客户端 app-core/src/projects.mjs 同一套判法):
  ok 已转成可读的文字 · empty 没能读出文字(可能是扫描件或空表)· failed 自动转换没有成功 · unsupported 这种格式暂时不能自动转换
  (原文件都保存在 inputs/originals/)。
"""
import argparse
import io
import json
import os
import re
import shutil
import sys
import zipfile

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yzlib  # noqa: E402
from yzlib import pl, UsageError  # noqa: E402

MATERIAL_STATES = {
    "ok": "已转成可读的文字",
    "empty": "没能读出文字（可能是扫描件或空表），原文件已保存",
    "failed": "自动转换没有成功，原文件已保存",
    "unsupported": "这种格式暂时不能自动转换，原文件已保存",
}
OLD_FORMAT_NOTE = {
    ".doc": "这是旧版 Word 格式，另存为 .docx 后再添加就能自动转换；原文件已保存",
    ".xls": "这是旧版 Excel 格式，另存为 .xlsx 后再添加就能自动转换；原文件已保存",
}
TEXT_EXTS = (".txt", ".md", ".csv")
HTML_EXTS = (".html", ".htm")
# 新建项目时一次建好的文件夹(生成成稿的脚本不会自己建 out/,10-03 端到端实测)。
# 第五轮 M4:从网上取来的资料另放 fetched/(原件 originals/、转成的文字 converted/),inputs/ 只放用户自己的材料
PROJECT_DIRS = (("inputs", "originals"), ("inputs", "converted"), ("records",), ("cards",), ("drafts",),
                ("out",), ("review",), ("library",), (yzlib.FETCHED, "originals"), (yzlib.FETCHED, "converted"))
_BAD = re.compile("[<>:\"/\\\\|?*" + "".join(re.escape(chr(c)) for c in range(32)) + "]")
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$", re.I)
MAX_NAME = 80

SKILL_OF_STATE = {
    None: "task-planner", "planning": "task-planner", "gate1_awaiting": "task-planner",
    "gate1_approved": "evidence-card", "collecting": "evidence-card", "gate2_awaiting": "evidence-card",
    "outlining": "outline-cocreate", "gate3_awaiting": "outline-cocreate", "gate3_approved": "cite-trace",
    "drafting": "cite-trace", "verifying": "cite-trace", "delivered": "cite-trace",
}


def safe_name(text, fallback="未命名项目", max_len=MAX_NAME):
    s = _BAD.sub("_", str(text or "")).strip()
    s = re.sub(r"[. ]+$", "", s)
    s = re.sub(r"[. ]+$", "", s[:max_len])
    if not s:
        s = fallback
    if _RESERVED.match(s):
        s = "_" + s
    return s


def safe_file_name(name, fallback="文件"):
    raw = str(name or "").strip()
    stem, ext = os.path.splitext(raw)
    keep = ext if re.fullmatch(r"\.[A-Za-z0-9]{1,10}", ext or "") and len(ext) < len(raw) else ""
    stem = raw[:-len(keep)] if keep else raw
    return safe_name(stem, fallback, MAX_NAME - len(keep)) + keep


def unique_in(folder, name):
    if not os.path.exists(os.path.join(folder, name)):
        return name
    stem, ext = os.path.splitext(name)
    if not ext or ext == name:
        stem, ext = name, ""
    for i in range(2, 10000):
        cand = "%s (%d)%s" % (stem, i, ext)
        if not os.path.exists(os.path.join(folder, cand)):
            return cand
    raise UsageError("没有空的名字可用:%s" % name)


def _has_text(path, csv=False):
    try:
        with io.open(path, encoding="utf-8-sig") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return False
    return bool(re.search(r'[^\s,"]', text) if csv else re.search(r"\S", text))


def html_to_text(raw):
    """网页 → 可读的文字(标准库 HTMLParser):去掉 script / style,段落、标题、表格行之间换行。"""
    from html.parser import HTMLParser

    class _P(HTMLParser):
        BLOCKS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table", "dd", "dt"}

        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.hidden = 0
            self.parts = []

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "noscript"):
                self.hidden += 1
            elif tag in self.BLOCKS:
                self.parts.append("\n")
            elif tag in ("td", "th"):
                self.parts.append(" | ")

        def handle_endtag(self, tag):
            if tag in ("script", "style", "noscript") and self.hidden:
                self.hidden -= 1
            elif tag in self.BLOCKS:
                self.parts.append("\n")

        def handle_data(self, data):
            if not self.hidden:
                self.parts.append(data)

    p = _P()
    p.feed(raw)
    p.close()
    blank = re.compile("[ \\t%s]+" % chr(0x3000))
    lines = [blank.sub(" ", l).strip() for l in "".join(p.parts).split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


def _read_web_page(path):
    """网页文件 → 文字;编码按 UTF-8、GB18030 依次试。"""
    with io.open(path, "rb") as f:
        data = f.read()
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def convert_material(project, stored_rel, out_dir=None):
    """转换一份已存好的原件(用户的材料在 inputs/originals/,取来的资料在 fetched/originals/)
    → {kind, state, outputs, note, detail}(路径相对项目)。转成的文字放 out_dir(默认 inputs/converted/)。"""
    src = os.path.join(project, stored_rel)
    name = os.path.basename(stored_rel)
    ext = os.path.splitext(name)[1].lower()
    out_dir = out_dir or os.path.join(project, "inputs", "converted")
    os.makedirs(out_dir, exist_ok=True)

    def rel(p):
        return os.path.relpath(p, project).replace("\\", "/")

    if ext in (".pdf", ".docx"):
        out = os.path.join(out_dir, name + ".md")
        script = "convert_pdf.py" if ext == ".pdf" else "convert_docx.py"
        try:
            rc, so, se = yzlib.run_toolkit(script, [src, "--out", out])
        except Exception as e:
            rc, so, se = -1, "", str(e)
        if ext == ".pdf":
            state = "ok" if rc == 0 and "fetch_state=ok" in so else ("empty" if rc == 1 and "fetch_state=empty" in so else "failed")
        else:
            m = re.search(r"正文 (\d+) 字符", so)
            state = ("ok" if int(m.group(1)) > 0 else "empty") if rc == 0 and m else "failed"
        if state == "ok" and not os.path.isfile(out):
            state = "failed"
        elif state == "ok" and not _has_text(out):
            state = "empty"
        if state != "ok" and os.path.exists(out):
            os.remove(out)
        return {"kind": ext[1:], "state": state, "outputs": [rel(out)] if state == "ok" else [],
                "note": MATERIAL_STATES[state], "detail": (so + se)[-800:]}
    if ext == ".xlsx":
        try:
            with zipfile.ZipFile(src) as z:
                entries = z.namelist()
        except (zipfile.BadZipFile, OSError):
            return {"kind": "xlsx", "state": "failed", "outputs": [], "note": MATERIAL_STATES["failed"],
                    "detail": "not a readable .xlsx (zip) file"}
        sheets = sorted(int(m.group(1)) for m in (re.match(r"^xl/worksheets/sheet(\d+)\.xml$", e) for e in entries) if m)
        if not sheets:
            return {"kind": "xlsx", "state": "failed", "outputs": [], "note": MATERIAL_STATES["failed"],
                    "detail": "no worksheets in the workbook"}
        outputs, details, failed, non_empty = [], [], 0, 0
        for n in sheets:
            out = os.path.join(out_dir, "%s.sheet%d.csv" % (name, n))
            rc, so, se = yzlib.run_toolkit("convert_xlsx.py", [src, "--sheet", n, "--out", out])
            details.append("[sheet %d] %s%s" % (n, so, se))
            shape = re.search(r"^(\d+) 行 × (\d+) 列", so, re.M)
            if rc != 0 or not shape or not os.path.isfile(out):
                failed += 1
                if os.path.exists(out):
                    os.remove(out)
                continue
            if int(shape.group(1)) > 0 and _has_text(out, csv=True):
                non_empty += 1
                outputs.append(rel(out))
            else:
                os.remove(out)
        state = "failed" if failed else ("ok" if non_empty else "empty")
        note = ("有 %d 张工作表没能转换，其余 %d 张已转成可读的文字；原文件已保存" % (failed, len(outputs))
                if state == "failed" and outputs else MATERIAL_STATES[state])
        return {"kind": "xlsx", "state": state, "outputs": outputs, "note": note, "detail": "\n".join(details)[-800:]}
    if ext in HTML_EXTS:
        # 第五轮 M4:网页(取来的资料多半是网页)转成文字,不用助手自己写脚本
        out = os.path.join(out_dir, name + ".txt")
        try:
            text = html_to_text(_read_web_page(src))
        except Exception as e:  # noqa: BLE001  坏的网页照实记「没转成」
            return {"kind": "html", "state": "failed", "outputs": [], "note": MATERIAL_STATES["failed"], "detail": str(e)[:300]}
        if not text.strip():
            return {"kind": "html", "state": "empty", "outputs": [], "note": MATERIAL_STATES["empty"], "detail": ""}
        with io.open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return {"kind": "html", "state": "ok", "outputs": [rel(out)], "note": MATERIAL_STATES["ok"], "detail": "html → text"}
    if ext in TEXT_EXTS:
        out = os.path.join(out_dir, name if ext != ".csv" else name)
        try:
            with io.open(src, encoding="utf-8-sig") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError):
            return {"kind": ext[1:], "state": "failed", "outputs": [], "note": MATERIAL_STATES["failed"],
                    "detail": "not UTF-8 text"}
        if not text.strip():
            return {"kind": ext[1:], "state": "empty", "outputs": [], "note": MATERIAL_STATES["empty"], "detail": ""}
        with io.open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return {"kind": ext[1:], "state": "ok", "outputs": [rel(out)], "note": MATERIAL_STATES["ok"], "detail": "plain text"}
    return {"kind": "other", "state": "unsupported", "outputs": [],
            "note": OLD_FORMAT_NOTE.get(ext, MATERIAL_STATES["unsupported"]), "detail": ""}


def materials_index(materials):
    lines = ["# 用户提供的材料", "",
             "委托原话在 `inputs/request.md`。下面是用户添加的文件；原文件在 `inputs/originals/`，自动转换的文字在 `inputs/converted/`。", ""]
    if not materials:
        lines.append("（没有添加文件）")
    for m in materials:
        outs = "（%s）" % "、".join("`%s`" % o for o in m["outputs"]) if m.get("outputs") else ""
        lines.append("- %s：%s%s" % (m["name"], m["note"], outs))
    return "\n".join(lines) + "\n"


def _project_json(project):
    return os.path.join(project, "records", "project.json")


def read_project_json(project):
    p = _project_json(project)
    if os.path.isfile(p):
        try:
            with io.open(p, encoding="utf-8") as f:
                return json.load(f)
        except ValueError:
            return {}
    return {}


def write_project_json(project, data):
    yzlib.records_dir(project)
    with io.open(_project_json(project), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=1) + "\n")


def add_materials(project, files):
    originals = os.path.join(project, "inputs", "originals")
    os.makedirs(originals, exist_ok=True)
    info = read_project_json(project)
    stored_list = info.setdefault("materials", [])
    results = []
    for f in files:
        src = os.path.abspath(f)
        if not os.path.isfile(src):
            results.append({"name": os.path.basename(src), "stored": None, "kind": None, "state": "failed",
                            "outputs": [], "note": "找不到这个文件，没有添加", "detail": src})
            continue
        stored = unique_in(originals, safe_file_name(os.path.basename(src)))
        shutil.copy2(src, os.path.join(originals, stored))
        conv = convert_material(project, "inputs/originals/" + stored)
        entry = {"name": os.path.basename(src), "stored": "inputs/originals/" + stored, "added_at": yzlib.now_iso()}
        entry.update(conv)
        results.append(entry)
        stored_list.append({k: entry[k] for k in ("name", "stored", "state", "note", "outputs", "added_at")})
    write_project_json(project, info)
    with io.open(os.path.join(project, "inputs", "materials.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(materials_index(stored_list))
    return results


def set_current(name):
    root = yzlib.projects_root()
    os.makedirs(root, exist_ok=True)
    with io.open(os.path.join(root, ".current"), "w", encoding="utf-8", newline="\n") as f:
        f.write(name + "\n")


def new_project(title, request_text, files):
    if not isinstance(request_text, str) or not request_text.strip():
        raise UsageError("委托原话是空的")
    first = next((l for l in request_text.split("\n") if l.strip()), "")
    if re.match(r"^\s{0,3}#{1,6}\s", first):
        # 第三轮:agent 给委托原话加了一行标题(还删掉了开头的「新建项目：」)。原话一字不改;项目名另用 --title 给
        raise UsageError("委托原话要照用户发来的那条消息逐字存（开头的「新建项目：」之类也留着），不要加标题行；"
                         "项目名用 --title 给，不写进委托原话")
    root = yzlib.projects_root()
    os.makedirs(root, exist_ok=True)
    name = unique_in(root, safe_name(title))
    project = os.path.join(root, name)
    for sub in PROJECT_DIRS:
        os.makedirs(os.path.join(project, *sub), exist_ok=True)
    with io.open(os.path.join(project, "inputs", "request.md"), "w", encoding="utf-8", newline="") as f:
        f.write(request_text)
    created = yzlib.now_iso()
    write_project_json(project, {"name": name, "title": str(title).strip() or name, "created_at": created,
                                 "request_file": "inputs/request.md", "materials": []})
    with io.open(os.path.join(project, "inputs", "materials.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(materials_index([]))
    with io.open(os.path.join(project, "PROGRESS.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("%s · planning · %s\n" % (name, created[:10]))
    results = add_materials(project, files) if files else []
    set_current(name)
    out = {"ok": True, "project": name, "path": os.path.normpath(project),
           "materials": [{k: r[k] for k in ("name", "state", "note", "outputs")} for r in results],
           "say": new_project_say(name, results),
           "next": "项目建好了。①这个对话里第一次打开这个项目：按 yunzhi-progress 第 1 节把右侧挂上，地址用 pane_url（只挂这一次）；"
                   "②把 say 那句告诉用户（项目名、每份材料转换的结果）；"
                   "③加载 task-planner 进入明确任务：委托里写明了研究范围、成稿形式的先记进 clarify.yaml，再一次问完还没说清的三件事。"}
    out.update(_page(project))
    return out


def new_project_say(name, results):
    """新建项目之后对用户说的那一句(第五轮 H3:要转述给用户的话由脚本给)。材料转换的结果照四态那几句说。"""
    say = "项目建好了：%s（名字不合适可以随时改）。" % name
    if not results:
        return say
    if all(r.get("state") == "ok" for r in results):
        return say + ("你给的材料已转成可读的文字。" if len(results) == 1 else "你给的 %d 份材料都已转成可读的文字。" % len(results))
    parts = ["「%s」%s" % (os.path.splitext(r.get("name") or "")[0], r.get("note")) for r in results]
    return say + "你给的材料：" + "；".join(parts) + "。"


def staged_request_problem(path):
    """委托原话的暂存文件放错了地方 → 一句改法;没问题 → None(第五轮 L4)。
    10-04:projects/_inbox/ 是所有对话共用的,两个对话同时新建项目会互相覆盖 request.md —— 每个新项目用自己的文件夹。"""
    if yzlib.in_shared_inbox_root(path):
        return ("委托原话不要直接放在大家共用的 projects/_inbox/ 下面（两个对话同时新建项目会互相覆盖）：写进 "
                "projects/_inbox/<项目名>/request.md（每个新项目自己一个文件夹，<项目名> 就是 --title 那个名字），再跑同一条命令。"
                "这是命令写法的问题，自己改了重跑，不用问用户")
    return None


def clean_staging(request_path, materials):
    """新建项目之后收拾暂存的文件夹(projects/_inbox/<项目名>/):里面交过来的材料已经拷进项目,删掉;空了就把文件夹也删掉。
    只动共用的 _inbox 下面、委托原话所在的那一个文件夹。"""
    folder = os.path.dirname(os.path.abspath(request_path))
    inbox = os.path.abspath(yzlib.shared_inbox())
    try:
        if os.path.normcase(os.path.dirname(folder)) != os.path.normcase(inbox):
            return
        for m in materials:
            mp = os.path.abspath(m)
            if os.path.normcase(os.path.dirname(mp)) == os.path.normcase(folder) and os.path.isfile(mp):
                os.remove(mp)
        if os.path.isdir(folder) and not os.listdir(folder):
            os.rmdir(folder)
    except OSError:
        pass


def _page(project):
    """重新生成进度页,并把右侧那一页换成进度页(新建、接着做、改名之后右侧都该显示进度)。"""
    try:
        import progress
        out = progress.page_report(project)
    except Exception as e:  # 进度页出错不挡别的事
        return {"progress_page_error": "%s: %s" % (type(e).__name__, e)}
    out.update(yzlib.pane_info(project, out["progress_page"], "progress"))
    return out


def rename_project(old, new_title):
    project = yzlib.project_dir(old)
    root = os.path.dirname(project)
    new = safe_name(new_title)
    if new == os.path.basename(project):
        return {"ok": True, "project": new, "changed": False}
    if os.path.exists(os.path.join(root, new)):
        raise UsageError("已经有一个叫「%s」的项目了，换个名字" % new)
    target = os.path.join(root, new)
    os.rename(project, target)
    info = read_project_json(target)
    info["name"] = new
    info["title"] = str(new_title).strip() or new
    write_project_json(target, info)
    set_current(new)
    out = {"ok": True, "project": new, "changed": True, "path": os.path.normpath(target),
           "next": "项目改名了，右侧那一页跟着搬了家：按 yunzhi-progress 第 1 节用新的 pane_url 重新挂一次。以后命令里的项目名都用新名字。"}
    out.update(_page(target))
    return out


def pending_card(project):
    """最近出过、还没处理的那张卡 → {card_id, kind} 或 None(处理过 = yzlib.CARD_CLOSED 里那几种)。"""
    log = yzlib.read_jsonl(yzlib.card_log(project))
    handled = set()
    for e in reversed(log):
        cid = e.get("card_id")
        if e.get("type") in yzlib.CARD_CLOSED:
            handled.add(cid)
        elif e.get("type") == "prepared":
            if cid in handled:
                return None
            return {"card_id": cid, "kind": e.get("kind"), "title": e.get("title")}
    return None


def expected_while_flow_change(item):
    """改流程卡在等用户时开场检查本来就会报的两条(任务计划确认之后又改过):规格 §①说这是预期的。"""
    detail = item.get("detail") or ""
    return detail.startswith("task_plan.md 批准后内容已变") or "task_plan.md 印章=stale" in detail


def status_of(project):
    """一个项目在哪一步(用 pipeline_status 的结论,不另判)。"""
    name = yzlib.project_name(project)
    meta, body, problem = yzlib.load_task_plan(project)
    card = pending_card(project)
    if meta is None and problem is None:
        return {"name": name, "state": None, "stage": "task", "stage_no": 1, "stage_zh": yzlib.stage_name("task"),
                "headline": "第 1 步 %s · 正在和你聊需求" % yzlib.stage_name("task"),
                "skill": "task-planner", "consistent": True, "problems": [], "other_problems": [],
                "pending_card": card, "flow_change_pending": False}
    facts, items, _line = yzlib.pipeline_facts(project)
    state = facts.get("pipeline_status")
    stages = facts.get("stages") or list(pl.STAGES)
    fails = [i for i in items if i.get("status") == "FAIL"]
    flow = (not problem) and yzlib.flow_change_pending(meta, body)
    # 改流程卡在等时,除了那两条预期的,还有没有别的对不上
    others = [i for i in fails if not (flow and expected_while_flow_change(i))]
    stage = yzlib.stage_of_state(state) or "task"
    phrase = yzlib.state_phrase(state) if state else "进度读不出来"
    stamps = facts.get("stamps") or {}
    stale = [yzlib.DOC_NAMES[d] for d, s in stamps.items() if (s or {}).get("status") == "stale" and d in yzlib.DOC_NAMES]
    if flow:
        phrase = "等你重新确认任务计划"
    elif stale:
        phrase = "需要你重新确认%s" % "、".join(stale)
    skill = SKILL_OF_STATE.get(state, "task-planner")
    if state == "gate2_approved":
        skill = "outline-cocreate" if "outline" in stages else "cite-trace"
    return {"name": name, "state": state, "stage": stage, "stage_no": list(pl.STAGES).index(stage) + 1,
            "stage_zh": yzlib.stage_name(stage),
            "headline": "第 %d 步 %s · %s" % (list(pl.STAGES).index(stage) + 1, yzlib.stage_name(stage), phrase),
            "skill": skill, "consistent": not fails, "problems": [i["detail"] for i in fails],
            "other_problems": [i["detail"] for i in others],
            "pending_card": card, "flow_change_pending": flow, "stages": stages, "facts": facts}


def next_for(info):
    if info["flow_change_pending"] and info.get("other_problems"):
        return "改流程卡在等用户，但除此之外还有别的对不上（other_problems 里）：停在这里，用一两句话告诉用户是哪份材料，等用户定，不推进。"
    if info["flow_change_pending"]:
        return "改流程卡还在等用户（开场检查报对不上是预期的，别停下）：加载 task-planner，用 card.py prepare flow_change 再出一次改流程卡。"
    if not info["consistent"]:
        return "进度记录和文件对不上：停在这里，用一两句话告诉用户是哪份材料（problems 里有），等用户定，不推进。"
    if info.get("pending_card"):
        c = info["pending_card"]
        return "有一张卡还没回答（%s）：加载 %s，按它重新出这张卡（card.py prepare %s）。" % (c.get("title") or c["card_id"], info["skill"], c["kind"])
    if info["state"] == "delivered":
        return "这个项目已经交付了。用户要接着改，按 cite-trace 走；要做新课题就新建项目。"
    return "加载 %s，从「%s」接着做。" % (info["skill"], info["stage_zh"])


def status_say(info):
    """接着做一个项目时对用户说的那一句(第五轮 H3)。对不上时说是哪份材料(用研究员的叫法),不贴检查的原话。"""
    if not info["consistent"] and not (info["flow_change_pending"] and not info.get("other_problems")):
        names = [yzlib.DOC_NAMES[d] for d in yzlib.DOC_NAMES
                 if any(p.startswith(d) or (" %s " % d) in p or ("%s 印章" % d) in p for p in info.get("problems") or [])]
        which = "（%s确认之后又改过，或者记录对不上）" % "、".join(dict.fromkeys(names)) if names else ""
        return "进度记录和文件对不上%s，我先停在这里，请你看一下。" % which
    if info.get("state") == "delivered":
        return "这个项目已经交付了。要接着改，告诉我改哪里。"
    phrase = info["headline"].split(" · ", 1)[1] if " · " in info["headline"] else ""
    return "现在在第 %d 步：%s%s。" % (info["stage_no"], info["stage_zh"], "，" + phrase if phrase else "")


def cmd_list():
    root = yzlib.projects_root()
    items = []
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            p = os.path.join(root, name)
            if name.startswith((".", "_")) or not os.path.isdir(p):
                continue
            info = status_of(p)
            items.append({k: info[k] for k in ("name", "state", "stage_zh", "headline", "skill", "consistent", "pending_card")})
    if not items:
        nxt = "还没有项目：把 say 那句告诉用户，等他贴委托（和材料），然后 projects.py new 新建。"
        say = "还没有项目。把要研究的委托发给我（有材料就一起附上），我来新建一个项目。"
    elif len(items) == 1:
        nxt = "只有一个项目：跑 projects.py status \"%s\"，按它说的接着做。" % items[0]["name"]
        say = None
    else:
        nxt = "有 %d 个项目：说 say 那句，用选项卡请用户选（card.py prepare pick_project），再按选中的那个 projects.py status。" % len(items)
        say = "你现在有 %d 个项目，接着做哪一个？" % len(items)
    out = {"ok": True, "count": len(items), "projects": items, "next": nxt}
    if say:
        out["say"] = say
    return out


def main(argv):
    return yzlib.run_main(_main, argv)


def _main(argv):
    yzlib.setup_stdout()
    ap = argparse.ArgumentParser(prog="projects.py")
    sub = ap.add_subparsers(dest="cmd")
    n = sub.add_parser("new")
    n.add_argument("--title", required=True)
    # 委托原话只从文件读:写在命令行里,PowerShell 会把 $… 和反引号悄悄展开、把中文引号当语法(委托原话是卡上引文的唯一依据)
    n.add_argument("--request-file", required=True)
    n.add_argument("--material", action="append", default=[])
    a = sub.add_parser("add")
    a.add_argument("project")
    a.add_argument("--material", action="append", default=[])
    sub.add_parser("list")
    s = sub.add_parser("status")
    s.add_argument("project")
    r = sub.add_parser("rename")
    r.add_argument("project")
    r.add_argument("--to", required=True)
    sub.add_parser("now")
    c = sub.add_parser("count")
    c.add_argument("project")
    args = ap.parse_args(argv[1:])
    if args.cmd == "new":
        problem = staged_request_problem(args.request_file)
        if problem:
            raise UsageError(problem)
        text = yzlib.read_input_file(args.request_file)
        out = new_project(args.title, text, args.material)
        clean_staging(args.request_file, args.material)
        return yzlib.emit(out)
    if args.cmd == "add":
        project = yzlib.project_dir(args.project)
        res = add_materials(project, args.material)
        return yzlib.emit({"ok": True, "materials": [{k: r[k] for k in ("name", "state", "note", "outputs")} for r in res]})
    if args.cmd == "list":
        return yzlib.emit(cmd_list())
    if args.cmd == "rename":
        return yzlib.emit(rename_project(args.project, args.to))
    if args.cmd == "now":
        sys.stdout.write(yzlib.now_iso() + "\n")
        return 0
    if args.cmd == "count":
        import card as card_mod
        project = yzlib.project_dir(args.project)
        counts = card_mod.stance_counts(project)
        if counts is None:
            raise UsageError("资料卡片读不出来(cards 文件夹不在,或有卡片读不出):先跑 card_check.py")
        live = pl.count_live_cards(os.path.join(project, "cards"))
        return yzlib.emit({"ok": True, "cards": counts["total"], "live_cards_toolkit": live,
                           "by_stance": {pl.STANCE_LABELS[k]: counts[k] for k in pl.STANCE_LABELS},
                           "next": "资料汇编 dashboard.cards_count 写 cards 这个数；对用户说张数也照这里的数。"})
    if args.cmd == "status":
        project = yzlib.project_dir(args.project)
        yzlib.update_progress_md(project)     # PROGRESS.md 第一行跟着进度走(第三轮:一直停在 planning)
        info = status_of(project)
        info.pop("facts", None)
        set_current(info["name"])
        info["next"] = next_for(info)
        info["say"] = status_say(info)
        info.update(_page(project))
        info["ok"] = True
        return yzlib.emit(info)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
