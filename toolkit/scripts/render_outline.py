# -*- coding: utf-8 -*-
"""render_outline — 大纲正本 → 树状 HTML(可折叠、锚点可跳、evidence 徽章)。

用法:
  python -X utf8 render_outline.py <outline.md|outline.json> <out.html> [--title 标题]
"""
import html as _html
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from outline_check import (expand_evidence, parse_outline_json,
                           parse_outline_md, parse_words)


def render(path, out_path, title=None):
    meta, nodes = (parse_outline_json(path) if path.endswith(".json")
                   else parse_outline_md(path))
    title = title or meta.get("title") or "大纲"
    sigma = sum(parse_words(n.get("words")) or 0 for n in nodes)
    items = []
    for n in nodes:
        nid = _html.escape(str(n.get("id")))
        chips = []
        for expr in (n.get("evidence") or []):
            uids, err = expand_evidence(expr)
            cls = "chip bad" if err else "chip"
            chips.append('<span class="%s">%s</span>' % (cls, _html.escape(expr)))
        w = parse_words(n.get("words"))
        body_bits = []
        for key, label in (("point", "论点"), ("task", "任务"), ("revision", "修订")):
            if n.get(key):
                body_bits.append('<div class="kv"><b>%s</b> %s</div>' % (
                    label, _html.escape(str(n[key]))))
        items.append(
            '<details open id="%s"><summary><a class="anchor" href="#%s">§</a> '
            '<b>%s</b>%s %s</summary><div class="body">%s</div></details>' % (
                nid, nid, _html.escape(str(n.get("title") or nid)),
                (' <span class="words">%d字</span>' % w) if w else "",
                "".join(chips), "".join(body_bits) or "<i>(无)</i>"))
    doc = TMPL % {"title": _html.escape(title), "n": len(nodes), "sigma": sigma,
                  "total": meta.get("total_words") or "—",
                  "items": "\n".join(items)}
    # 渲染件从 2026-09-02 起写进 out/gate-reports/ —— 交付对接窗口只扫 out/*.html
    # 且不递归,所以规划产物挪进子目录才不会被当成品。⚠️ 那个目录在共创第一轮
    # 渲染时通常还不存在(窗口报告是后面才生成的),不建目录的话每个新项目的
    # 第一次渲染就 FileNotFoundError。实测过。
    parent = os.path.dirname(os.path.abspath(out_path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with io.open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(doc)
    print("渲染 %s:%d 节点 · Σ%d 字" % (out_path, len(nodes), sigma))


TMPL = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(title)s · 大纲</title><style>
body{font-family:"Microsoft YaHei",sans-serif;max-width:52em;margin:1.5em auto;
padding:0 1em;line-height:1.7;background:#fafaf7;color:#222}
h1{font-size:1.3em;border-bottom:2px solid #4a6d8a;padding-bottom:.3em}
.meta{color:#666;font-size:.9em}
details{margin:.4em 0;border-left:3px solid #c9d6e2;padding:.2em .8em;background:#fff}
details details{margin-left:1.2em}
summary{cursor:pointer}
.anchor{text-decoration:none;color:#aaa}.anchor:hover{color:#4a6d8a}
.words{color:#8a6d3b;font-size:.85em}
.chip{display:inline-block;background:#e8eef4;color:#365;border-radius:8px;
padding:0 .5em;font-size:.8em;margin-left:.3em}
.chip.bad{background:#f6e0e0;color:#a33}
.kv{font-size:.92em;color:#444}.body{margin:.3em 0 .2em}
</style></head><body>
<h1>%(title)s</h1>
<p class="meta">%(n)s 节点 · Σ%(sigma)s 字 / 目标 %(total)s 字 ·
<a href="javascript:document.querySelectorAll('details').forEach(d=&gt;d.open=true)">全展开</a> ·
<a href="javascript:document.querySelectorAll('details').forEach(d=&gt;d.open=false)">全折叠</a></p>
%(items)s
</body></html>
"""


def main(argv):
    HELP_FLAGS = ("-h", "--help")
    if any(f in argv for f in HELP_FLAGS):
        print(__doc__)
        return 0
    if len(argv) < 3:
        print(__doc__)
        return 2
    title = None
    if "--title" in argv:
        i = argv.index("--title")
        title = argv[i + 1]
        del argv[i:i + 2]
    render(argv[1], argv[2], title)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
