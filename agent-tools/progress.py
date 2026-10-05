# -*- coding: utf-8 -*-
"""progress — 进度表:整页(自己每 15 秒刷新)、右侧那一页、对话里的快照。

用法(在工作区根目录下跑;$PY 见 AGENTS.md):
  $PY agent-tools/progress.py "<项目名>" --page [--pane]
      生成 projects/<项目名>/进度.html(完整网页,带 <meta http-equiv="refresh" content="15">),打印它的路径与 file:/// 地址。
      --pane:同时把它换进右侧那一页 projects/<项目名>/右侧.html(原子写;显示进度页时每 5 秒、显示材料时每 30 秒
      自己刷新,刷新后回到读到的位置;右侧只挂一次,之后不再打开)。
      右侧那一页正显示进度页时,不带 --pane 也会跟着换;正显示材料时不动。
  $PY agent-tools/progress.py "<项目名>" --snapshot --out-dir "<对话的可视化目录>"
      在那个目录里生成一份片段(没有 doctype / html / head / body,根元素有唯一 id),
      打印该写进回复的那一行 visualize{...}(只在这一轮最后一条回复里单独一行才显示)。
      目录不能在工作区或 projects 文件夹里(写进 out/ 会被当成成稿):在里面就拒绝。
页面只读文件;整张页面的可见文字过一遍用词检查,有用户读不懂的词就在输出里报 page_wording。

内容照设计稿右栏:现在在哪一步(大字)· 四个环节各一行(名字、用时、完成数 / 总数、小方块;当前环节加粗框并展开)·
底部「确认与决定记录」(读 records/cards.jsonl,由助手记录)。所有数字由脚本从文件读,取不到就不显示。
"""
import argparse
import html
import io
import json
import os
import random
import re
import sys
from datetime import datetime, timezone

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yzlib  # noqa: E402
from yzlib import pl, UsageError  # noqa: E402

PAGE_NAME = "进度.html"
STEPS = {
    "task": ("聊清需求", "写任务计划", "你的确认"),
    "sources": ("找资料", "写资料汇编", "你的确认"),
    "outline": ("拟提纲", "你的确认"),
    "delivery": ("写成稿", "交付前检查", "独立复核", "你的确认"),
}
REPORT_TYPE_SAID = {"judge": "研判型", "survey": "综述型", "undecided": "先不定（先按研判型写）"}
STANCE_COLORS = {"support": "var(--accent)", "counter": "#d97b4a", "mixed": "#cbd2dc",
                 "gap": "repeating-linear-gradient(135deg,var(--muted) 0 2px,transparent 2px 5px)"}
STANCE_LEGEND = {"support": "var(--accent)", "counter": "#d97b4a", "mixed": "#cbd2dc", "gap": "var(--muted)"}


def esc(s):
    return html.escape(str(s), quote=True)


def parse_time(value):
    value = pl.date_text(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        t = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else None


def short_time(value):
    t = parse_time(value)
    if not t:
        return None
    return "%d-%d %02d:%02d" % (t.month, t.day, t.hour, t.minute)


def duration(start, end):
    """两个时刻之间多久 →「45 分钟」「21 小时」「2 天 5 小时」;取不到或倒着的 → None。"""
    if not start or not end:
        return None
    sec = (end - start).total_seconds()
    if sec < 0:
        return None
    minutes = int(sec // 60)
    if minutes < 1:
        return "刚开始"
    days, rem = divmod(minutes, 1440)
    hours, mins = divmod(rem, 60)
    if days:
        return "%d 天%s" % (days, " %d 小时" % hours if hours else "")
    if hours:
        return "%d 小时" % hours
    return "%d 分钟" % mins


def card_events(project):
    return yzlib.read_jsonl(yzlib.card_log(project))


def report_type_cell(events):
    """还没有任务计划时「报告类型」那一格:看报告类型卡的记录。"""
    shown = [e for e in events if e.get("kind") == "report_type"]
    state = None
    for e in shown:
        if e.get("type") == "prepared":
            state = "等你在卡上选"
        elif e.get("type") == "answered" and e.get("choice") in REPORT_TYPE_SAID:
            state = REPORT_TYPE_SAID[e["choice"]]
        elif e.get("type") in yzlib.CARD_CLOSED and state == "等你在卡上选":
            state = None
    return state


def stance_counts(project):
    import card as card_mod
    return card_mod.stance_counts(project)


def outline_info(project):
    path = os.path.join(project, "outline.md")
    if not os.path.isfile(path):
        return None
    try:
        import card as card_mod
        nodes, total, target, unsupported = card_mod.outline_numbers(path)
    except Exception:
        return None
    tops = [n for n in pl.outline_tree(nodes)]
    sections = []
    for item in tops:
        node = item["node"]
        words = pl._subtree_words(item)
        sections.append((node.get("title") or node.get("id"), words))
    return {"total": total, "target": target, "unsupported": unsupported, "sections": sections}


def review_info(project):
    path = os.path.join(project, "review", "result.json")
    if not os.path.isfile(path):
        return None
    try:
        with io.open(path, encoding="utf-8") as f:
            result = json.load(f)
    except ValueError:
        return {"readable": False}
    if pl.review_result_problems(result):
        return {"readable": False}
    # 盘上没有成稿文件时,对不上不是因为「复核之后又改过」
    no_files = not pl.deliverable_paths(project)
    ok, _why = pl.review_applies(project, result)
    return {"readable": True, "applies": ok and not no_files, "no_files": no_files,
            "round": result.get("round"), "counts": result.get("counts")}


def cite_info(project):
    """交付前检查:跑 cite_check.py(与出交付卡同一个检查)。"""
    import shutil
    if not pl.deliverable_paths(project):
        return None
    tmp = yzlib.work_tmp("prog")
    try:
        out = os.path.join(tmp, "c.json")
        rc, _o, _e = yzlib.run_toolkit("cite_check.py", ["--project", project, "--json", out])
        if not os.path.isfile(out):
            return None
        with io.open(out, encoding="utf-8") as f:
            rep = json.load(f)
    except Exception:
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    import card as card_mod
    counts = card_mod.checks_counts(rep.get("items") or [])   # 只有客户端做得了的那一项不算(同交付卡)
    counts["rc"] = rc
    return counts


def first_approvals(events):
    """每种卡第一次被点了确认的时刻 → {kind: datetime}。环节第一次进来的时刻由它推:
    收集资料 = 任务计划第一次确认、拟定提纲 = 资料汇编第一次确认、撰写交付 = 提纲(省掉提纲时资料汇编)第一次确认。"""
    out = {}
    for e in events:
        if e.get("type") == "answered" and e.get("result") == "approved" and e.get("kind") not in out:
            t = parse_time(e.get("at"))
            if t:
                out[e["kind"]] = t
    return out


def doc_version(project, doc):
    path = os.path.join(project, doc)
    if not os.path.isfile(path):
        return None
    meta, _b, problem = pl.load_md(path)
    v = meta.get("version") if not problem else None
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def model(project):
    """进度表要显示的一切(纯数据)。"""
    import projects as projects_mod
    name = yzlib.project_name(project)
    info = projects_mod.status_of(project)
    events = card_events(project)
    meta, body, problem = yzlib.load_task_plan(project)
    facts = info.get("facts") or {}
    stamps = facts.get("stamps") or {}
    state = info["state"]
    stages = info.get("stages") or list(pl.STAGES)
    now = datetime.now(timezone.utc)
    pinfo = projects_mod.read_project_json(project)
    delivery_rec = None
    dpath = os.path.join(project, "records", "delivery.json")
    if os.path.isfile(dpath):
        try:
            with io.open(dpath, encoding="utf-8") as f:
                delivery_rec = json.load(f)
        except ValueError:
            delivery_rec = None
    approved_at = {d: parse_time((stamps.get(d) or {}).get("approved_at")) for d in yzlib.DOC_NAMES
                   if (stamps.get(d) or {}).get("status") == "approved"}
    created = parse_time(pinfo.get("created_at"))
    delivered_at = parse_time((delivery_rec or {}).get("delivered_at")) if state == "delivered" else None
    flow_states = pl.stage_states(stages)
    cur = flow_states.index(state) if state in flow_states else -1
    current = info["stage"]
    out_stages = []
    # 每个环节从第一次进来时算起(第三轮:重新确认之后,按「上一个环节最后一次确认」算的用时成了几分钟)
    firsts = first_approvals(events)
    before = {"sources": ("task_plan", "task_plan.md"), "outline": ("dossier", "dossier.md"),
              "delivery": ("outline", "outline.md") if "outline" in stages else ("dossier", "dossier.md")}

    def stage_start(sid):
        if sid == "task":
            return created
        kind, doc = before[sid]
        return firsts.get(kind) or approved_at.get(doc)

    for sid in pl.STAGES:
        steps = STEPS[sid]
        entry = {"id": sid, "no": list(pl.STAGES).index(sid) + 1, "name": yzlib.stage_name(sid),
                 "steps": list(steps), "marks": [""] * len(steps), "current": sid == current,
                 "skipped": sid not in stages, "status": "", "duration": None, "summary": None, "details": None}
        if entry["skipped"]:
            entry["status"] = "本项目不需要"
            out_stages.append(entry)
            continue
        doc = pl.STAGE_DOCS[sid]
        first, awaiting, approved = pl.STAGE_STATES[sid]
        start = stage_start(sid)
        end = approved_at.get(doc) if doc else delivered_at
        if state is None:
            # 还没有任务计划:第一步刚开始
            if sid == "task":
                marks = ["a", "", ""]
                entry["status"] = "进行中"
            else:
                marks = [""] * len(steps)
                entry["status"] = "未开始"
        elif cur < flow_states.index(first):
            marks = [""] * len(steps)
            entry["status"] = "未开始"
        elif sid == "delivery":
            if state == "delivered":
                marks = ["d"] * 4
                entry["status"] = "已交付%s" % (" " + short_time(delivered_at.isoformat()) if delivered_at else "")
            elif state == "verifying":
                import card as card_mod
                cite = cite_info(project)
                rev = review_info(project)
                points = card_mod.delivery_points_ok(project)
                c = "d" if cite and cite["fail"] == 0 and cite["rc"] == 0 else "a"
                r = "d" if rev and rev.get("applies") and (rev.get("counts") or {}).get("must_fix") == 0 else ("a" if c == "d" else "")
                # 交付卡出得来才算「等你确认交付」:检查、复核都过了,交付的三点也写好了
                x = "w" if c == "d" and r == "d" and points else ""
                marks = ["d", c, r, x]
                entry["status"] = "等你确认交付" if x == "w" else "进行中"
                entry["details"] = {"cite": cite, "review": rev, "points": points}
            else:
                marks = ["a", "", "", ""]
                entry["status"] = "进行中"
        else:
            st = (stamps.get(doc) or {}).get("status")
            if state == first:
                marks = ["d", "a", ""] if sid == "task" else (["a", "", ""] if sid == "sources" else ["a", ""])
                entry["status"] = "进行中"
            elif state == awaiting:
                marks = ["d"] * (len(steps) - 1) + ["w"]
                entry["status"] = "等你确认"
            else:
                marks = ["d"] * len(steps)
                entry["status"] = "已确认%s" % (" " + short_time(end.isoformat()) if end else "")
            if st == "stale":
                marks = ["d"] * (len(steps) - 1) + ["w"]
                entry["status"] = "等你重新确认" if (sid == "task" and info.get("flow_change_pending")) else "需要重新确认"
        entry["marks"] = marks
        is_done = all(m == "d" for m in marks)
        if start and (end if is_done else None):
            entry["duration"] = duration(start, end)
        elif start and not is_done and entry["status"] != "未开始":
            entry["duration"] = duration(start, now)
        entry["done"] = sum(1 for m in marks if m == "d")
        out_stages.append(entry)

    # 当前环节展开的内容,完成的环节一句话
    cards = stance_counts(project) if os.path.isdir(os.path.join(project, "cards")) else None
    dossier_meta = None
    if os.path.isfile(os.path.join(project, "dossier.md")):
        dm, _db, dp = pl.load_md(os.path.join(project, "dossier.md"))
        dossier_meta = dm if not dp else None
    conclusion = None
    if dossier_meta and dossier_meta.get("genre") == "argument" and isinstance(dossier_meta.get("gate_verdict"), dict):
        import card as card_mod
        conclusion = card_mod.conclusion_phrase(dossier_meta["gate_verdict"].get("outcome"), body or "")
    for e in out_stages:
        sid = e["id"]
        if e["skipped"]:
            continue
        if sid == "task":
            panel = pl.clarify_panel(project)
            rtype = panel["report_type"] if panel["source"] == "task_plan" else report_type_cell(events)
            rows = [("报告类型", rtype), ("研究范围", panel["scope"]), ("成稿形式", panel["format"])]
            filled = sum(1 for _k, v in rows if v and v != "等你在卡上选")
            e["three"] = {"rows": rows, "filled": filled}
            v = doc_version(project, "task_plan.md")
            if v is not None:
                e["summary"] = "任务计划第 %d 版%s" % (v, " · %s" % e["status"] if e["status"] else "")
        elif sid == "sources":
            e["genre"] = (meta or {}).get("genre") or (dossier_meta or {}).get("genre")
            if cards and e["status"] != "未开始":
                e["cards"] = cards
            if conclusion:
                e["conclusion"] = conclusion
            if cards and e["status"] != "未开始":
                e["summary"] = "资料卡片 %d 张%s" % (cards["total"], " · 初步结论%s" % conclusion if conclusion else "")
        elif sid == "outline":
            oi = outline_info(project) if e["status"] != "未开始" else None
            if oi:
                e["outline"] = oi
                v = doc_version(project, "outline.md")
                e["summary"] = "提纲%s · 合计 %d 字" % ("第 %d 版" % v if v is not None else "", oi["total"])
        elif sid == "delivery":
            if e.get("details") is None and state == "delivered":
                e["details"] = {"cite": None, "review": review_info(project)}
            formats = []
            names = [rel.lower() for rel, _p in pl.deliverable_paths(project)]
            if any(n.endswith(".docx") for n in names):
                formats.append("Word 版")
            if any(n.endswith(".html") for n in names):
                formats.append("网页版")
            if formats and e["status"] != "未开始":
                e["formats"] = formats

    records = []
    titles = {}
    for ev in events:
        if ev.get("type") == "prepared":
            titles[ev.get("card_id")] = ev
        elif ev.get("type") in ("answered", "skipped"):
            prep = titles.get(ev.get("card_id")) or {}
            ver = (prep.get("data") or {}).get("version")
            title = prep.get("title") or ev.get("kind")
            if ver and prep.get("kind") in ("task_plan", "dossier", "outline", "flow_change"):
                title = "%s（第 %d 版）" % (title, ver)
            if ev.get("type") == "skipped":
                said = "跳过了，先放着"
            elif ev.get("label"):
                said = "你选了：%s" % ev["label"]
            else:
                said = "你没选选项，写了意见"
            records.append({"at": short_time(ev.get("at")), "title": title, "said": said, "note": ev.get("note")})

    head = info["headline"]
    delivery_entry = next((e for e in out_stages if e["id"] == "delivery"), None)
    if state == "verifying" and delivery_entry and delivery_entry["status"] == "等你确认交付":
        head = "第 4 步 %s · 等你确认交付" % yzlib.stage_name("delivery")
    m = re.match(r"^(第 \d 步 \S+) · (.+)$", head)
    # 改流程卡在等用户:任务计划确认之后只改了流程,开场检查报的那两条是预期的(规格 §①),只有它们时不报警;
    # 另有别的对不上,照报
    consistent = info["consistent"] or bool(info.get("flow_change_pending") and not info.get("other_problems"))
    return {"name": name, "headline": head, "head_main": m.group(1) if m else head, "head_state": m.group(2) if m else "",
            "waiting": bool(re.search(r"等你|需要你", head)), "consistent": consistent,
            "stages": out_stages, "records": records, "state": state}


CSS = """
#ID { --ink:#1f2937; --muted:#6b7280; --line:#d6dbe2; --soft:#f3f5f8; --accent:#3e5b80; --wait:#b45309; --wait-soft:#fff3e4; --done:#2f6b45; font-family:"Microsoft YaHei UI","PingFang SC","Noto Sans SC",sans-serif; color:var(--ink); font-size:13px; line-height:1.55; display:grid; gap:12px; max-width:720px; }
@media (prefers-color-scheme: dark) { #ID { --ink:#e6eaf0; --muted:#9aa6b5; --line:#2e3846; --soft:#202834; --accent:#8fb0d6; --wait:#f0a050; --wait-soft:#3a2a18; --done:#6fcf97; } }
#ID .now { border:1px solid var(--line); border-radius:6px; padding:12px 14px; display:grid; gap:2px; }
#ID .now .k { font-size:12px; color:var(--muted); }
#ID .now .v { font-size:16px; font-weight:700; }
#ID .now .v b { color:var(--wait); }
#ID .warn { border:1px solid var(--wait); background:var(--wait-soft); border-radius:6px; padding:8px 12px; }
#ID .stage { border:1px solid var(--line); border-radius:6px; padding:10px 14px; display:grid; gap:6px; }
#ID .stage.cur { border:2px solid var(--accent); }
#ID .stage.skip { opacity:.7; }
#ID .row { display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap; align-items:baseline; }
#ID .name { font-weight:700; }
#ID .meta { color:var(--muted); font-size:12px; font-variant-numeric:tabular-nums; }
#ID .sq { display:flex; gap:4px; flex-wrap:wrap; }
#ID .sq i { width:9px; height:9px; border-radius:2px; background:var(--soft); border:1px solid var(--line); display:inline-block; }
#ID .sq i.d { background:var(--done); border-color:var(--done); }
#ID .sq i.a { border:1.5px dashed var(--accent); background:transparent; }
#ID .sq i.w { border:1.5px solid var(--wait); background:var(--wait-soft); }
#ID .steps { display:flex; gap:10px; flex-wrap:wrap; font-size:12px; color:var(--muted); }
#ID .steps .on { color:var(--ink); font-weight:700; }
#ID .bar { display:flex; height:14px; border-radius:3px; overflow:hidden; background:var(--soft); }
#ID .bar span { display:block; height:100%; }
#ID .legend { display:flex; gap:12px; flex-wrap:wrap; font-size:12px; color:var(--muted); }
#ID .legend i { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:4px; vertical-align:-1px; }
#ID .kv { display:grid; grid-template-columns:auto 1fr; gap:2px 12px; font-size:12px; }
#ID .kv .k { color:var(--muted); }
#ID .sec { font-size:12px; color:var(--muted); font-weight:700; margin-top:2px; }
#ID ul { margin:0; padding-left:18px; }
#ID .rec { border-top:1px solid var(--line); padding-top:8px; display:grid; gap:4px; font-size:12px; }
#ID .rec .by { color:var(--muted); font-weight:400; }
"""


def _squares(marks):
    return '<div class="sq">%s</div>' % "".join('<i class="%s"></i>' % m if m else "<i></i>" for m in marks)


def _stage_html(e):
    cls = "stage" + (" cur" if e["current"] and not e["skipped"] else "") + (" skip" if e["skipped"] else "")
    if e["skipped"]:
        return '<div class="%s"><div class="row"><span class="name">%d %s</span><span class="meta">本项目不需要</span></div></div>' % (
            cls, e["no"], esc(e["name"]))
    parts = [p for p in (e.get("duration"), "%d/%d" % (e["done"], len(e["steps"])), e["status"]) if p]
    if e["status"] == "未开始":
        parts = ["未开始", "%d/%d" % (e["done"], len(e["steps"]))]
    out = ['<div class="%s"><div class="row"><span class="name">%d %s</span><span class="meta">%s</span></div>%s' % (
        cls, e["no"], esc(e["name"]), esc(" · ".join(parts)), _squares(e["marks"]))]
    if e["current"]:
        steps = []
        for label, mark in zip(e["steps"], e["marks"]):
            steps.append('<span class="%s">%s%s</span>' % ("on" if mark in ("a", "w") else "", esc(label),
                                                         " · 进行中" if mark == "a" else (" · 等你" if mark == "w" else "")))
        out.append('<div class="steps">%s</div>' % "".join(steps))
        out.append(_details_html(e))
    elif e.get("summary") and e["status"] != "未开始":
        out.append('<div class="meta">%s</div>' % esc(e["summary"]))
    out.append("</div>")
    return "".join(out)


def _details_html(e):
    sid = e["id"]
    out = []
    if sid == "task" and e.get("three"):
        t = e["three"]
        out.append('<div class="sec">要聊清的三件事 %d / 3</div>' % t["filled"])
        out.append('<div class="kv">%s</div>' % "".join(
            '<span class="k">%s</span><span>%s</span>' % (esc(k), esc(v or "还没答")) for k, v in t["rows"]))
        if e.get("summary"):
            out.append('<div class="meta">%s</div>' % esc(e["summary"]))
    elif sid == "sources":
        c = e.get("cards")
        if c and c.get("total"):
            out.append('<div class="sec">资料卡片 · 共 %d 张</div>' % c["total"])
            import card as card_mod
            spans, legend, label = [], [], []
            keys = {word: key for key, word in pl.STANCE_LABELS.items()}
            for word, n in card_mod.stance_parts(c, e.get("genre")):
                key = keys[word]
                label.append("%s %d" % (word, n))
                if n:
                    spans.append('<span style="width:%.1f%%;background:%s"></span>' % (100.0 * n / c["total"], STANCE_COLORS[key]))
                legend.append('<span><i style="background:%s"></i>%s %d</span>' % (STANCE_LEGEND[key], esc(word), n))
            out.append('<div class="bar" role="img" aria-label="%s">%s</div>' % (esc("，".join(label)), "".join(spans)))
            out.append('<div class="legend">%s</div>' % "".join(legend))
        elif c is not None:
            out.append('<div class="meta">资料卡片 0 张</div>')
        if e.get("conclusion"):
            out.append('<div class="meta">初步结论：%s</div>' % esc(e["conclusion"]))
    elif sid == "outline" and e.get("outline"):
        o = e["outline"]
        out.append('<div class="sec">篇幅与目标 · 合计 %d%s 字</div>' % (o["total"], " / 目标 %d" % o["target"] if o["target"] is not None else ""))
        if o["sections"]:
            out.append('<div class="kv">%s</div>' % "".join(
                '<span class="k">%s</span><span>%s</span>' % (esc(t), esc(w)) for t, w in o["sections"]))
        out.append('<div class="meta">没有资料卡片支撑的节：%d 个%s</div>' % (
            len(o["unsupported"]), "（%s）" % "、".join(esc(t or i) for i, t in o["unsupported"]) if o["unsupported"] else ""))
    elif sid == "delivery":
        if e.get("formats"):
            out.append('<div class="meta">成稿：%s</div>' % esc(" · ".join(e["formats"])))
        d = e.get("details") or {}
        cite = d.get("cite")
        if cite:
            if cite["fail"] == 0 and cite["rc"] == 0:
                import card as card_mod
                out.append('<div class="meta">交付前检查 %s</div>' % esc(card_mod.checks_phrase(cite)))
            else:
                out.append('<div class="meta">交付前检查：%d 项要处理，助手正在修</div>' % cite["fail"])
        rev = d.get("review")
        if rev and rev.get("readable") and rev.get("applies"):
            cnt = rev["counts"]
            out.append('<div class="meta">独立复核（第 %d 轮）：必须改的 %d · 说法收了 %d · 抽查无误 %d 段</div>' % (
                rev["round"], cnt["must_fix"], cnt["tone_down"], cnt["checked_ok"]))
        elif rev and rev.get("readable") and rev.get("no_files"):
            out.append('<div class="meta">独立复核：成稿文件现在不在，重新生成成稿之后要再复核一轮</div>')
        elif rev and rev.get("readable"):
            out.append('<div class="meta">独立复核：成稿复核之后又改过，要再复核一轮</div>')
        if d.get("points") is False and cite and cite["fail"] == 0 and rev and rev.get("applies"):
            out.append('<div class="meta">交付前请你重点看的三点，助手还没写好</div>')
    return "".join(out)


def render_fragment(m, root_id):
    css = CSS.replace("#ID", "#" + root_id)
    parts = ['<div id="%s">' % root_id, "<style>%s</style>" % css.strip()]
    parts.append('<div class="now"><span class="k">%s · 现在</span><span class="v">%s%s</span></div>' % (
        esc(m["name"]), esc(m["head_main"]),
        (" · <b>%s</b>" % esc(m["head_state"])) if m["waiting"] and m["head_state"] else (" · %s" % esc(m["head_state"]) if m["head_state"] else "")))
    if not m["consistent"]:
        parts.append('<div class="warn">进度记录和文件对不上，助手会先停下来问你。</div>')
    for e in m["stages"]:
        parts.append(_stage_html(e))
    if m["records"]:
        rec = ['<div class="rec"><div class="name">确认与决定记录 <span class="by">· 由助手记录</span></div>']
        for r in m["records"]:
            line = "%s · %s · %s" % (r["at"] or "", r["title"], r["said"])
            rec.append("<div>%s</div>" % esc(line.strip(" ·")))
            if r.get("note"):
                rec.append('<div class="meta">你写的意见：「%s」</div>' % esc(r["note"]))
        rec.append("</div>")
        parts.append("".join(rec))
    parts.append("</div>")
    return "\n".join(parts) + "\n"


def visible_text(fragment):
    """页面上用户看得到、读屏读得到的文字:各个元素之间隔一个空行(不让两个元素的字被当成一句连起来扫)。"""
    s = re.sub(r"<style.*?</style>", " ", fragment, flags=re.S)
    labels = [html.unescape(x) for x in re.findall(r'aria-label="([^"]*)"', s)]
    parts = [html.unescape(p).strip() for p in re.split(r"<[^>]+>", s)]
    return "\n\n".join([p for p in parts if p] + labels)


def page_wording(project, fragment):
    """整张进度页的可见文字过一遍用词检查(用户写过的话在「」里照放行)。"""
    import wording_check
    return wording_check.scan(visible_text(fragment), yzlib.user_texts(project), "进度页")


def page_report(project):
    """写进度页、扫一遍可见文字 → {progress_page, progress_url[, page_wording, page_wording_next]}。"""
    import wording_check
    path, hits = write_page(project, scan=True)
    out = {"progress_page": path, "progress_url": file_url(path)}
    if hits:
        out["page_wording"] = wording_check.describe(hits)
        out["page_wording_next"] = ("进度页上有用户读不懂的词（page_wording）：它们出自项目材料（项目名、研究范围、提纲的节名、"
                                    "卡上写的意见等）。改那份材料里的说法，再刷新进度页。")
    return out


def write_page(project, scan=False):
    m = model(project)
    frag = render_fragment(m, "yz-progress")
    page = ("<!doctype html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta http-equiv=\"refresh\" content=\"15\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "<title>云织进度 · %s</title>\n"
            "<style>body{margin:0;padding:16px;background:#ffffff}@media (prefers-color-scheme: dark){body{background:#12161c}}</style>\n"
            "</head>\n<body>\n%s</body>\n</html>\n") % (esc(m["name"]), frag)
    path = os.path.normpath(os.path.abspath(os.path.join(project, PAGE_NAME)))
    yzlib.write_atomic(path, page)
    # 第四轮:助手自己跑 stamp.py --advance verifying 之后,PROGRESS.md 第一行停在 drafting;每次刷新进度页都把它对上
    if os.path.isfile(os.path.join(project, "task_plan.md")):
        try:
            yzlib.update_progress_md(project)
        except OSError:
            pass
    if yzlib.pane_kind(project) == "progress":
        # 右侧正显示进度页:跟着换成新的(显示的是材料就不动,等 card.py answer 或 --pane 换回来)
        pane_follow(project, path)
    if scan:
        return path, page_wording(project, frag)
    return path


def pane_follow(project, path):
    """右侧那一页跟上新的进度页;没跟上不挡进度页(下一次 --pane 再换)。"""
    yzlib.pane_info(project, path, "progress")


def file_url(path):
    """file:/// 地址(实现在 yzlib,各脚本共用一份)。"""
    return yzlib.file_url(path)


def snapshot(project, out_dir):
    if yzlib.inside_protected(out_dir):
        raise UsageError("快照只能放在对话的可视化目录里，不能放进工作区（放进 out/ 会被当成成稿）。没有可视化目录就不贴快照")
    m = model(project)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root_id = "yz-progress-%s-%04d" % (stamp.replace("-", ""), random.randint(0, 9999))
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.normpath(os.path.abspath(os.path.join(out_dir, "yunzhi-progress-%s-%s.html" % (stamp, root_id[-4:]))))
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(render_fragment(m, root_id))
    line = "visualize" + json.dumps({"path": path, "title": "%s · 进度" % m["name"]}, ensure_ascii=False, separators=(",", ":"))
    return path, line


def main(argv):
    return yzlib.run_main(_main, argv)


def _main(argv):
    yzlib.setup_stdout()
    ap = argparse.ArgumentParser(prog="progress.py")
    ap.add_argument("project")
    ap.add_argument("--page", action="store_true")
    ap.add_argument("--pane", action="store_true")
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--out-dir")
    args = ap.parse_args(argv[1:])
    project = yzlib.project_dir(args.project)
    if args.snapshot:
        if not args.out_dir:
            raise UsageError("--snapshot 要带 --out-dir \"<对话的可视化目录>\"")
        path, line = snapshot(project, args.out_dir)
        sys.stdout.write(line + "\n")
        return 0
    rep = page_report(project)
    out = {"ok": True, "page": rep["progress_page"], "url": rep["progress_url"]}
    if args.pane:
        out.update(yzlib.pane_info(project, rep["progress_page"], "progress"))
        out["next"] = ("右侧那一页已换成进度页，它自己会刷新出来（原来显示材料的话最多半分钟）；不用再打开什么。" if "pane" in out else
                       "进度页已更新，但右侧那一页没换成（pane_error）：不挡别的事，接着做。")
    else:
        out["next"] = "进度页已更新。要右侧显示进度页，加 --pane 再跑一次。"
    for k in ("page_wording", "page_wording_next"):
        if k in rep:
            out[k] = rep[k]
    return yzlib.emit(out)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
