# -*- coding: utf-8 -*-
"""render_cards — 证据卡库 → HTML(索引表 + 逐卡锚点 + tier/stance 徽章)。

用法:
  python -X utf8 render_cards.py <cards目录|cards.jsonl> <out.html> [--title 标题]
"""
import html as _html
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from card_check import load_cards
from pipeline_lib import norm_tier

STANCE_CN = {"support": "支持", "counter": "反例", "mixed": "混合", "gap": "缺口",
             None: "未判"}


def render(path, out_path, title=None):
    cards = load_cards(path)
    title = title or "证据卡库"
    rows = []
    blocks = []
    for card, _ in cards:
        uid = _html.escape(str(card.get("uid")))
        stance = card.get("stance")
        st_cls = stance if stance in STANCE_CN else "none"
        rows.append(
            '<tr><td><a href="#%s">%s</a></td><td>%s</td>'
            '<td><span class="st %s">%s</span></td><td>%s</td></tr>' % (
                uid, uid, _html.escape(str(card.get("title") or ""))[:60],
                st_cls, STANCE_CN.get(stance, str(stance)),
                _html.escape(", ".join(card.get("tags") or []))))
        facts = "".join(
            "<li><b>%s</b> <span class=\"cal\">%s</span></li>" % (
                _html.escape(str(f.get("value"))),
                _html.escape(str(f.get("caliber") or "")))
            for f in (card.get("facts") or []) if isinstance(f, dict))
        srcs = []
        for s in (card.get("sources") or []):
            t = norm_tier(s.get("tier"))
            badge = ('<span class="tier t%s">%s</span>' %
                     ((t or "x").replace("-", "m"), t or "未定级"))
            u = s.get("url") or "#"
            srcs.append('<li>%s <a href="%s">%s</a>%s</li>' % (
                badge, _html.escape(u),
                _html.escape(str(s.get("title") or u))[:70],
                (" · " + _html.escape(str(s.get("locator")))) if s.get("locator") else ""))
        blocks.append(
            '<section class="card" id="%s"><h3><a class="anchor" href="#%s">§</a>'
            ' %s <span class="st %s">%s</span></h3>'
            '%s%s<div class="limit"><b>局限</b> %s</div>'
            '<ul class="srcs">%s</ul></section>' % (
                uid, uid, uid + " · " + _html.escape(str(card.get("title") or "")),
                st_cls, STANCE_CN.get(stance, str(stance)),
                ("<ul class=\"facts\">%s</ul>" % facts) if facts else "",
                ("<p class=\"support\">%s</p>" % _html.escape(str(card.get("support"))))
                if card.get("support") else "",
                _html.escape(str(card.get("limit") or "⚠️ 空")),
                "".join(srcs) or "<li><i>(缺口卡,无来源)</i></li>"))
    doc = TMPL % {"title": _html.escape(title), "n": len(cards),
                  "rows": "\n".join(rows), "blocks": "\n".join(blocks)}
    with io.open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(doc)
    print("渲染 %s:%d 张卡" % (out_path, len(cards)))


TMPL = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(title)s</title><style>
body{font-family:"Microsoft YaHei",sans-serif;max-width:56em;margin:1.5em auto;
padding:0 1em;line-height:1.65;background:#fafaf7;color:#222}
h1{font-size:1.3em;border-bottom:2px solid #6d8a4a;padding-bottom:.3em}
table{border-collapse:collapse;width:100%%;font-size:.88em}
td,th{border:1px solid #ddd;padding:.25em .5em}
.card{border:1px solid #ddd;border-left:4px solid #6d8a4a;border-radius:4px;
background:#fff;margin:1em 0;padding:.6em 1em}
.card h3{margin:.2em 0;font-size:1em}
.anchor{text-decoration:none;color:#bbb}.anchor:hover{color:#6d8a4a}
.st{border-radius:8px;padding:0 .5em;font-size:.78em;vertical-align:middle}
.st.support{background:#e2efd9;color:#2a5d1e}.st.counter{background:#f6e0e0;color:#a33}
.st.mixed{background:#fdf3d7;color:#8a6d1e}.st.gap{background:#e8e8e8;color:#555}
.st.none{background:#f0d9f0;color:#7a2a7a}
.tier{font-weight:700;font-size:.78em;border-radius:3px;padding:0 .35em}
.tier.tA{background:#2a5d1e;color:#fff}.tier.tAm{background:#7a9d5e;color:#fff}
.tier.tB{background:#8a6d3b;color:#fff}.tier.tx{background:#c33;color:#fff}
.facts li{font-size:.92em}.cal{color:#666;font-size:.9em}
.limit{background:#fdf6e3;border-radius:4px;padding:.3em .6em;font-size:.9em;margin:.4em 0}
.srcs{font-size:.85em;color:#444}
</style></head><body>
<h1>%(title)s</h1>
<p>%(n)s 张卡 · 索引点 uid 跳转</p>
<table><tr><th>uid</th><th>标题</th><th>stance</th><th>tags</th></tr>
%(rows)s
</table>
%(blocks)s
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
