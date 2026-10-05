# -*- coding: utf-8 -*-
"""view — 把一份材料生成好读的网页(样式同进度页),确认之前放到右侧给用户看。

用法(在工作区根目录下跑;$PY 见 AGENTS.md):
  $PY agent-tools/view.py "<项目名>" <task_plan|dossier|outline|cards|draft> [--pane] [--check]
      --check(只给提纲):先跑 toolkit 的 outline_check.py 自查(结果在 check_ok / check_problems),再生成、换到右侧 ——
      提纲每改一版就跑一次 outline --pane --check,用户在右侧马上看到这一版(第三轮:用户得开口要才看得到)。
      task_plan / dossier / outline / cards → projects/<项目名>/查看/任务计划.html、资料汇编.html、提纲.html、资料卡片.html;
      draft → 成稿的网页版:用 toolkit 的 render_html.py 生成的 out/*.html;out/ 里还没有网页版,就用 render_html.py
      把底稿生成到 查看/成稿预览.html(不碰 out/)。
      打印 {ok, page, url, title, next}。--pane:同时把这一页换进右侧那一页 projects/<项目名>/右侧.html
      (原子写;右侧只挂一次,换页面从来不重新打开)。

只读材料,不改任何文件(除了 查看/ 里的这几页)。正文照原样显示;规格里固定的章节名、表头换成研究员的说法,
资料卡片的编号换成资料的标题(点了跳到资料卡片那一页),资料汇编里结论、适用范围、缺口、来源前面的编号不显示。
"""
import argparse
import html
import io
import os
import re
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yzlib  # noqa: E402
from yzlib import pl, UsageError  # noqa: E402

KINDS = ("task_plan", "dossier", "outline", "cards", "draft")
VIEW_DIR = "查看"
FILE_NAMES = {"task_plan": "任务计划.html", "dossier": "资料汇编.html", "outline": "提纲.html", "cards": "资料卡片.html"}
DOC_OF = {"task_plan": "task_plan.md", "dossier": "dossier.md", "outline": "outline.md"}
DRAFT_PREVIEW = "成稿预览.html"
# 规格里固定的章节(按 {#id} 认)→ 研究员的说法
SECTION_NAMES = {
    "request": "委托原话", "genre_rationale": "报告类型", "thesis": "核心判断", "hypotheses": "假设",
    "preregistration": "事先写好的判定标准", "evidence_gate": "中途检查点", "definitions": "概念界定",
    "scope": "研究范围", "evidence_standard": "证据标准", "constraints": "约束", "budget": "篇幅和工作量上限",
    "commitments": "请你重点看这三点",
    "header": "定义与研究范围", "claims": "初步结论", "boundaries": "适用范围与不能下的结论", "content": "内容",
    "gate": "检查点结论", "next": "下一步", "index": "来源清单",
    "support": "可支持的观点", "limit": "局限",
}
TABLE_HEADERS = {"outcome": "结论", "判定条件": "判定标准", "对应表述": "表述", "操作定义": "怎么界定"}
GENRE_SAID = {"argument": "研判型（下判断）", "survey": "综述型（梳理情况）"}
TIER_SAID = {"A": "官方一手", "A-": "机构自报", "B": "二手"}
FETCH_SAID = {"ok": "已取得", "empty": "查了但没有内容", "gap": "没有这项数据", "failed": "没取到（出错）", "blocked": "打不开"}
MODE_SAID = {"methodology": "方法说明", "synthesis": "综合收束", "pending_evidence": "资料待补"}
CARD_ID_RE = re.compile(r"(?<![A-Za-z0-9_])S\d{2,3}(?![A-Za-z0-9_])")
LEAD_ID_RE = re.compile(r"^(?:[CRG]\d{1,3})\s+")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
ANCHOR_RE = re.compile(r"\s*\{#([A-Za-z0-9_-]+)\}\s*$")
NODE_RE = re.compile(r"\s*\{id:[^}]*\}\s*$")
LIST_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")

CSS = """
:root { --ink:#1f2937; --muted:#6b7280; --line:#d6dbe2; --soft:#f3f5f8; --accent:#3e5b80; --wait:#b45309; --wait-soft:#fff3e4; --done:#2f6b45; }
@media (prefers-color-scheme: dark) { :root { --ink:#e6eaf0; --muted:#9aa6b5; --line:#2e3846; --soft:#202834; --accent:#8fb0d6; --wait:#f0a050; --wait-soft:#3a2a18; --done:#6fcf97; } }
body { margin:0; padding:16px; background:#ffffff; color:var(--ink); font-family:"Microsoft YaHei UI","PingFang SC","Noto Sans SC",sans-serif; font-size:14px; line-height:1.7; }
@media (prefers-color-scheme: dark) { body { background:#12161c; } }
main { max-width:760px; margin:0 auto; display:grid; gap:12px; }
.head { border:1px solid var(--line); border-radius:6px; padding:12px 14px; display:grid; gap:6px; }
.head .k { font-size:12px; color:var(--muted); }
.head .v { font-size:17px; font-weight:700; }
.chip { display:inline-block; font-size:12px; font-weight:400; padding:0 8px; border-radius:10px; border:1px solid var(--line); margin-left:6px; vertical-align:2px; }
.chip.wait { border-color:var(--wait); color:var(--wait); background:var(--wait-soft); }
.chip.done { border-color:var(--done); color:var(--done); }
.kv { display:grid; grid-template-columns:auto 1fr; gap:2px 12px; font-size:13px; }
.kv .k { color:var(--muted); }
h2 { font-size:15px; margin:14px 0 4px; padding-bottom:4px; border-bottom:1px solid var(--line); }
h3 { font-size:14px; margin:10px 0 2px; }
p { margin:4px 0; }
ul, ol { margin:4px 0; padding-left:22px; }
li.sub { margin-left:18px; }
table { border-collapse:collapse; width:100%; font-size:13px; margin:6px 0; }
th, td { border:1px solid var(--line); padding:4px 8px; text-align:left; vertical-align:top; }
th { background:var(--soft); }
blockquote { margin:6px 0; padding:6px 12px; border-left:3px solid var(--accent); background:var(--soft); }
a { color:var(--accent); }
.meta { color:var(--muted); font-size:12px; }
.points { border:1px solid var(--wait); background:var(--wait-soft); border-radius:6px; padding:6px 14px; }
.card { border:1px solid var(--line); border-radius:6px; padding:10px 14px; display:grid; gap:4px; }
.card.dep { opacity:.65; }
.card .t { font-weight:700; }
.node { border-left:3px solid var(--line); padding:2px 0 2px 12px; margin:8px 0; }
.node.l3 { margin-left:16px; }
.node .t { font-weight:700; }
"""


def esc(s):
    return html.escape(str(s), quote=True)


def view_dir(project):
    d = os.path.join(project, VIEW_DIR)
    os.makedirs(d, exist_ok=True)
    return d


def card_titles(project):
    """卡号 → 资料标题(读 cards/ 每张卡的 title)。"""
    out = {}
    d = os.path.join(project, "cards")
    if not os.path.isdir(d):
        return out
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".md"):
            continue
        meta, _b, problem = pl.load_md(os.path.join(d, fn))
        if problem or not isinstance(meta, dict):
            continue
        uid = meta.get("uid") or os.path.splitext(fn)[0]
        title = meta.get("title")
        out[str(uid)] = str(title) if isinstance(title, str) and title.strip() else str(uid)
    return out


def inline(text, titles=None):
    """行内:转义 → 链接 → 卡号换成资料标题 → 粗体 → 行内代码。"""
    titles = titles or {}
    keep = []

    def stash(s):
        keep.append(s)
        return "\x00%d\x00" % (len(keep) - 1)

    def link(m):
        label, url = m.group(1), m.group(2)
        if not re.match(r"^(?:https?://|#|[^:/\\]+\.html(?:#.*)?$)", url):
            return m.group(0)
        return stash('<a href="%s">%s</a>' % (url, label))

    t = esc(text)
    t = LINK_RE.sub(link, t)

    def card(m):
        uid = m.group(0)
        if uid not in titles:
            return uid
        return stash('<a href="%s#%s">%s</a>' % (FILE_NAMES["cards"], uid, esc(titles[uid])))

    t = CARD_ID_RE.sub(card, t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    return re.sub("\x00(\\d+)\x00", lambda m: keep[int(m.group(1))], t)


def markdown(body, titles=None, strip_ids=False, skip_h1=True):
    """正本的正文 → HTML。认:标题(去掉 {#…} 锚点,固定章节换说法)、表格、引用块、列表、段落。"""
    lines = (body or "").split("\n")
    out = []
    i = 0
    in_points = False

    def close_points():
        nonlocal in_points
        if in_points:
            out.append("</div>")
            in_points = False

    while i < len(lines):
        raw = lines[i]
        s = raw.strip()
        if not s:
            i += 1
            continue
        m = HEADING_RE.match(s)
        if m:
            level, text = len(m.group(1)), m.group(2)
            am = ANCHOR_RE.search(text)
            anchor = am.group(1) if am else None
            text = ANCHOR_RE.sub("", text)
            text = NODE_RE.sub("", text)
            i += 1
            if level == 1 and skip_h1:
                continue
            close_points()
            name = SECTION_NAMES.get(anchor, text)
            tag = "h2" if level <= 2 else "h3"
            out.append("<%s>%s</%s>" % (tag, inline(name, titles), tag))
            if anchor == "commitments":
                out.append('<div class="points">')
                in_points = True
            continue
        table = pl.split_table(lines, i)
        if table is not None:
            head, rows, i = table
            t = ["<table><thead><tr>"]
            t += ["<th>%s</th>" % inline(TABLE_HEADERS.get(h, h), titles) for h in head]
            t.append("</tr></thead><tbody>")
            for r in rows:
                t.append("<tr>%s</tr>" % "".join("<td>%s</td>" % inline(c, titles) for c in r))
            t.append("</tbody></table>")
            out.append("".join(t))
            continue
        if s.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(re.sub(r"^>\s?", "", lines[i].strip()))
                i += 1
            out.append("<blockquote>%s</blockquote>" % "<br>".join(inline(x, titles) for x in buf))
            continue
        lm = LIST_RE.match(raw)
        if lm:
            ordered = lm.group(2)[0].isdigit()
            items = []
            while i < len(lines):
                lm2 = LIST_RE.match(lines[i])
                if lm2:
                    text = lm2.group(3)
                    if strip_ids:
                        text = LEAD_ID_RE.sub("", text)
                    items.append((len(lm2.group(1).replace("\t", "    ")) >= 2, text))
                    i += 1
                elif lines[i].strip() and items and not HEADING_RE.match(lines[i].strip()) \
                        and not lines[i].strip().startswith(("|", ">")):
                    sub, text = items[-1]
                    items[-1] = (sub, text + " " + lines[i].strip())
                    i += 1
                else:
                    break
            tag = "ol" if ordered else "ul"
            out.append("<%s>%s</%s>" % (tag, "".join(
                '<li%s>%s</li>' % (' class="sub"' if sub else "", inline(text, titles)) for sub, text in items), tag))
            continue
        buf = [s]
        i += 1
        while i < len(lines) and lines[i].strip() and not HEADING_RE.match(lines[i].strip()) \
                and not LIST_RE.match(lines[i]) and not lines[i].strip().startswith(("|", ">")):
            buf.append(lines[i].strip())
            i += 1
        out.append("<p>%s</p>" % inline(" ".join(buf), titles))
    close_points()
    return "\n".join(out)


def status_of_doc(project, doc, meta, body):
    """→ (文字, 样式):等你确认 / 已确认 时间 / 确认之后又改过 / 还没确认。"""
    ap = meta.get("approval") if isinstance(meta.get("approval"), dict) else {}
    if ap.get("status") == "approved":
        if ap.get("approved_hash") == pl.content_hash(meta, body):
            import card as card_mod
            when = card_mod.md_time(ap.get("approved_at"))
            return ("已确认 %s" % when if when else "已确认"), "done"
        if doc == "task_plan.md" and yzlib.flow_change_pending(meta, body):
            return "等你重新确认（只改了流程）", "wait"
        return "确认之后又改过，要重新确认", "wait"
    plan_meta, _pb, _pp = yzlib.load_task_plan(project)
    state = plan_meta.get("pipeline_status") if isinstance(plan_meta, dict) else None
    awaiting = {"task_plan.md": "gate1_awaiting", "dossier.md": "gate2_awaiting", "outline.md": "gate3_awaiting"}.get(doc)
    if state == awaiting:
        return "等你确认", "wait"
    return "还没确认", ""


def page(project, title, chip, rows, body_html):
    name = yzlib.project_name(project)
    chip_html = '<span class="chip %s">%s</span>' % (chip[1], esc(chip[0])) if chip else ""
    kv = "".join('<span class="k">%s</span><span>%s</span>' % (esc(k), v) for k, v in rows if v)
    return ("<!doctype html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "<title>%s · %s</title>\n<style>%s</style>\n</head>\n<body>\n<main>\n"
            "<div class=\"head\"><span class=\"k\">%s</span><span class=\"v\">%s%s</span>%s</div>\n"
            "%s\n</main>\n</body>\n</html>\n") % (
        esc(title), esc(name), CSS.strip(), esc(name), esc(title), chip_html,
        ('<div class="kv">%s</div>' % kv) if kv else "", body_html)


def _load(project, doc):
    path = os.path.join(project, doc)
    if not os.path.isfile(path):
        raise UsageError("%s还没有写出来" % yzlib.DOC_NAMES.get(doc, doc))
    meta, body, problem = pl.load_md(path)
    if problem:
        raise UsageError("%s读不出来：%s" % (yzlib.DOC_NAMES.get(doc, doc), problem))
    return meta, body


def _version(meta):
    v = meta.get("version")
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def render_task_plan(project):
    meta, body = _load(project, "task_plan.md")
    v = _version(meta)
    title = "任务计划%s" % ("第 %d 版" % v if v else "")
    stages, _err = yzlib.project_stages(meta)
    rows = [("报告类型", esc(GENRE_SAID.get(meta.get("genre"), ""))),
            ("研究范围", esc(meta.get("scope_brief") or "")),
            ("成稿形式", esc(pl.panel_format(meta) or "")),
            ("研究流程", esc(" → ".join(yzlib.stage_name(s) for s in stages)))]
    return title, page(project, title, status_of_doc(project, "task_plan.md", meta, body), rows,
                       markdown(body, card_titles(project)))


def render_dossier(project):
    meta, body = _load(project, "dossier.md")
    v = _version(meta)
    title = "资料汇编%s" % ("第 %d 版" % v if v else "")
    import card as card_mod
    counts = card_mod.stance_counts(project)
    rows = []
    genre = meta.get("genre") or (yzlib.load_task_plan(project)[0] or {}).get("genre")
    if counts is not None:
        rows.append(("资料卡片", '%d 张：%s · <a href="%s">逐张看</a>' % (
            counts["total"], esc(card_mod.stance_phrase(counts, genre)), FILE_NAMES["cards"])))
    verdict = meta.get("gate_verdict") if isinstance(meta.get("gate_verdict"), dict) else None
    if verdict:
        plan_meta, plan_body, _pp = yzlib.load_task_plan(project)
        phrase = card_mod.conclusion_phrase(verdict.get("outcome"), plan_body or "")
        if phrase:
            rows.append(("初步结论", esc("%s：%s" % (phrase, (verdict.get("reason") or "").strip()))))
    # 资料缺口照记录里的情况说(第三轮:打不开的被写成了「没有这项数据」;出卡前脚本已核过它和资料缺口卡一致)
    gaps = [g for g in meta.get("gaps") or [] if isinstance(g, dict)] if isinstance(meta.get("gaps"), list) else []
    if gaps:
        rows.append(("资料缺口", esc("；".join("%s（%s）" % (str(g.get("what") or g.get("id") or ""), FETCH_SAID.get(g.get("state"), "情况没写"))
                                         for g in gaps))))
    return title, page(project, title, status_of_doc(project, "dossier.md", meta, body), rows,
                       markdown(body, card_titles(project), strip_ids=True))


def render_outline(project):
    meta, body = _load(project, "outline.md")
    v = _version(meta)
    title = "提纲%s" % ("第 %d 版" % v if v else "")
    import card as card_mod
    path = os.path.join(project, "outline.md")
    nodes, total, target, unsupported = card_mod.outline_numbers(path)
    titles = card_titles(project)
    rows = [("合计字数", esc("%d 字%s" % (total, "（目标 %d 字）" % target if target is not None else ""))),
            ("没有资料卡片支撑的节", esc("%d 个%s" % (len(unsupported), "：" + "、".join(t or i for i, t in unsupported) if unsupported else "")))]
    parts = []
    for n in nodes:
        level = n.get("level") or 2
        bits = ['<div class="node%s"><div class="t">%s%s</div>' % (
            " l3" if level >= 3 else "", inline(n.get("title") or n.get("id") or "", titles),
            ' <span class="meta">· %s 字</span>' % esc(n["words"]) if n.get("words") is not None else "")]
        if n.get("revision"):
            bits.append('<div class="meta">这一版：%s</div>' % esc(n["revision"]))
        if n.get("point"):
            bits.append("<div>要说的判断：%s</div>" % inline(str(n["point"]), titles))
        if n.get("task"):
            bits.append("<div>要做的事：%s</div>" % inline(str(n["task"]), titles))
        ev = [e for e in (n.get("evidence") or []) if isinstance(e, str)]
        if ev:
            bits.append("<div>用到的资料：%s</div>" % "、".join(inline(e, titles) for e in ev))
        elif n.get("evidence_mode"):
            bits.append('<div class="meta">没有资料卡片：%s</div>' % esc(MODE_SAID.get(n["evidence_mode"], n["evidence_mode"])))
        bits.append("</div>")
        parts.append("".join(bits))
    tail = ""
    m = re.search(r"^#{1,6}[^\n]*\{#commitments\}[^\n]*$", body or "", re.M)
    if m:
        tail = markdown(body[m.start():], titles)
    return title, page(project, title, status_of_doc(project, "outline.md", meta, body), rows,
                       "\n".join(parts) + "\n" + tail)


def render_cards(project):
    d = os.path.join(project, "cards")
    if not os.path.isdir(d):
        raise UsageError("还没有资料卡片")
    import card as card_mod
    counts = card_mod.stance_counts(project)
    genre = (yzlib.load_task_plan(project)[0] or {}).get("genre")
    live, dead = [], []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".md"):
            continue
        meta, body, problem = pl.load_md(os.path.join(d, fn))
        if problem or not isinstance(meta, dict):
            continue
        (dead if meta.get("deprecated") else live).append((meta, body))
    blocks = []
    for meta, body in live + dead:
        uid = str(meta.get("uid") or "")
        bits = ['<div class="card%s" id="%s"><div class="t">%s</div>' % (
            " dep" if meta.get("deprecated") else "", esc(uid), esc(meta.get("title") or uid))]
        stance = meta.get("stance")
        if genre == "survey" and stance in ("support", "counter", "mixed"):
            stance = "mixed"          # 综述型不下判断:有内容的都叫背景资料
        info = [pl.STANCE_LABELS.get(stance, "")]
        if meta.get("as_of"):
            info.append("数据截至 %s" % pl.date_text(meta.get("as_of")))
        if meta.get("deprecated"):
            info.append("已弃用")
        bits.append('<div class="meta">%s</div>' % esc(" · ".join(x for x in info if x)))
        if isinstance(meta.get("direction"), str) and meta["direction"].strip():
            bits.append('<div class="meta">和核心判断的关系：%s</div>' % esc(meta["direction"].strip()))
        facts = [f for f in (meta.get("facts") or []) if isinstance(f, dict)]
        if facts:
            bits.append("<ul>%s</ul>" % "".join("<li><strong>%s</strong>%s</li>" % (
                esc(f.get("value") or ""), "（%s）" % esc(f["caliber"]) if f.get("caliber") else "") for f in facts))
        for s in [x for x in (meta.get("sources") or []) if isinstance(x, dict)]:
            label = esc(s.get("title") or s.get("url") or "")
            url = s.get("url")
            name = '<a href="%s">%s</a>' % (esc(url), label) if isinstance(url, str) and url.startswith(("http://", "https://")) else label
            # 来源的 as_of 是取到这份资料的日期,不是资料发布的日期(第三轮:标成了「发布于」)
            extra = [TIER_SAID.get(pl.norm_tier(s.get("tier")), ""), s.get("locator") or "",
                     ("查阅于 %s" % pl.date_text(s.get("as_of"))) if s.get("as_of") else "", FETCH_SAID.get(s.get("fetch_state"), "")]
            bits.append('<div class="meta">来源：%s%s</div>' % (name, "（%s）" % esc(" · ".join(x for x in extra if x)) if any(extra) else ""))
            if isinstance(s.get("excerpt"), str) and s["excerpt"].strip():
                bits.append('<div class="meta">原文摘录：「%s」</div>' % esc(s["excerpt"].strip()))
        bits.append(markdown(body, None, skip_h1=True))
        bits.append("</div>")
        blocks.append("".join(bits))
    title = "资料卡片"
    rows = []
    if counts is not None:
        rows.append(("张数", esc("%d 张：%s" % (counts["total"], card_mod.stance_phrase(counts, genre)))))
    if dead:
        rows.append(("已弃用", esc("%d 张（放在最后，不算进张数）" % len(dead))))
    return title, page(project, title, None, rows, "\n".join(blocks))


def render_draft(project):
    """成稿网页版:out/ 里有就用它(toolkit 的 render_html.py 生成的);没有就把底稿生成到 查看/成稿预览.html。"""
    htmls = [p for rel, p in pl.deliverable_paths(project) if rel.lower().endswith(".html")]
    if htmls:
        return "成稿（网页版）", htmls[0]
    drafts = os.path.join(project, "drafts")
    refs = os.path.join(project, "references.yaml")
    mds = sorted((f for f in os.listdir(drafts) if f.endswith(".md")), key=lambda f: os.path.getmtime(os.path.join(drafts, f)),
                 reverse=True) if os.path.isdir(drafts) else []
    if not mds or not os.path.isfile(refs):
        raise UsageError("还没有成稿：底稿（drafts/）或参考文献清单还没有")
    out = os.path.join(view_dir(project), DRAFT_PREVIEW)
    args = [os.path.join(drafts, mds[0]), refs, out]
    if os.path.isdir(os.path.join(project, "cards")):
        args += ["--cards", os.path.join(project, "cards")]
    rc, so, se = yzlib.run_toolkit("render_html.py", args)
    if rc != 0 or not os.path.isfile(out):
        raise UsageError("成稿预览没生成出来：%s" % (so + se).strip()[-300:])
    return "成稿预览（网页版）", out


RENDERERS = {"task_plan": render_task_plan, "dossier": render_dossier, "outline": render_outline, "cards": render_cards}


def render(project, kind):
    """→ (页面路径, 标题)。"""
    if kind not in KINDS:
        raise UsageError("材料只能是 %s" % " / ".join(KINDS))
    if kind == "draft":
        title, path = render_draft(project)
        return os.path.normpath(path), title
    title, text = RENDERERS[kind](project)
    path = os.path.normpath(os.path.join(view_dir(project), FILE_NAMES[kind]))
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    if kind == "dossier" and os.path.isdir(os.path.join(project, "cards")):
        render(project, "cards")       # 资料汇编里的资料标题链到资料卡片那一页,一起生成
    return path, title


def main(argv):
    return yzlib.run_main(_main, argv)


def _main(argv):
    yzlib.setup_stdout()
    ap = argparse.ArgumentParser(prog="view.py")
    ap.add_argument("project")
    ap.add_argument("kind")
    ap.add_argument("--pane", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="提纲:先跑 toolkit 的 outline_check.py 自查,再生成、换到右侧(提纲每改一版都跑这一条)")
    args = ap.parse_args(argv[1:])
    project = yzlib.project_dir(args.project)
    if args.check and args.kind != "outline":
        raise UsageError("--check 只给提纲用（view.py \"<项目名>\" outline --pane --check）")
    check = outline_check(project) if args.check else None
    path, title = render(project, args.kind)
    out = {"ok": True, "kind": args.kind, "title": title, "page": path, "url": yzlib.file_url(path)}
    if check is not None:
        out.update(check)
    if args.pane:
        out.update(yzlib.pane_info(project, path, "view:%s" % args.kind))
        out["next"] = (("右侧那一页已换成这份材料，它自己会刷新出来（通常几秒，原来显示别的材料的话最多半分钟）；不用再打开什么。用户看完、回答了卡片，或者这一步做完，"
                        "跑 progress.py --page --pane 把右侧换回进度页。") if "pane" in out else
                       "材料页生成好了，但右侧那一页没换成（pane_error）：卡上的点开看照样能点。")
    else:
        out["next"] = "材料页生成好了（点开看的地址是 url）。要右侧显示它，加 --pane 再跑一次。"
    if check is not None and not check["check_ok"]:
        out["next"] = ("提纲自查有要改的（check_problems）：先改提纲，改好再跑这一条。" + out["next"])
    return yzlib.emit(out)


def outline_check(project):
    """提纲自查(toolkit outline_check.py --cards)→ {check_ok, check_problems}。"""
    path = os.path.join(project, "outline.md")
    if not os.path.isfile(path):
        raise UsageError("提纲还没有写出来")
    rc, so, se = yzlib.run_toolkit("outline_check.py", [path, "--cards", os.path.join(project, "cards")])
    fails = [l.strip() for l in (so + se).splitlines() if l.strip().startswith("[FAIL]")]
    out = {"check_ok": rc == 0, "check_problems": fails or ([] if rc == 0 else [(so + se).strip()[-300:]])}
    try:
        import card as card_mod
        _nodes, total, target, _uns = card_mod.outline_numbers(path)
        out.update({"words_sum": total, "total_words": target})
    except Exception:   # 数不出来就不给,不编
        pass
    return out


if __name__ == "__main__":
    sys.exit(main(sys.argv))
