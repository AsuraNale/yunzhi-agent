# -*- coding: utf-8 -*-
"""render_html — 占位正文 + 登记表 → 自包含 HTML 成品(规格 §⑤ 渲染规则)。

用法:
  python -X utf8 render_html.py <draft.md> <references.yaml> <out.html>
      [--title 标题] [--theme plan|classic|<名>] [--cards <cards 目录>]

骨架固定、样式可换(2026-08-29 v2):
  - **骨架**由本脚本产出,带稳定语义 class(`doc-*`),不随主题变。
  - **样式**来自 `<toolkit>/templates/themes/`:`base.css`(结构,共用)+ `<theme>.css`(观感)。
    两者读入后**内联**进产物 —— 成品必须自包含(离线可读、可转发),主题也不外链字体。
  - 换主题不改正本、不改脚本:`--theme classic` 即回到 v1 的宋体暖纸观感。

引用规则(与 v1 完全一致,勿动 —— 交付对接窗口 cite_check 依赖它):
  - `[[n]]`(数字)按 display_no 解析并保留原号;`[[R###]]` 按 id 解析、按出现顺序编号。
  - unverified → [n†] + 脚注;tier B 且有 claims_source → 「(转引自 …)」;
    liveness.state == failed(非 net) → 条目前 ⚠️。
  - ⚠️ 文内引用 v2 改为**锚点回跳**(#doc-ref-n)。这不影响交付对接窗口:
    `pipeline_lib.html_links()` 只收 http(s) 外链、跳过页内锚点,而参考列表条目仍直链来源 URL,
    故 HTML 的**外链集合**与 v1、与 DOCX 均相同(④ 跨格式相等比的是 set)。

v2 相对 v1 补的(v1 只有 `if 标题 else 段落`,markdown 几乎不解析):
  表格 / 列表 / 粗体 / 行内代码 / 引用块 / 分隔线 · 章节导航 · 行动卡 · 语义色表列 ·
  **元信息条一律机器取值**(章节/引用/来源/字数/卡数由本脚本数,不接受正文自述)。

正本/渲染分离:本产物不可手改,改正本后重跑。
"""
import html as _html
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline_lib import (PLACEHOLDER_RE, count_inline_citations, load_references,
                          split_table, take_doc_title,
                          norm_tier, number_entries, read_md)

THEME_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "templates", "themes")
DEFAULT_THEME = "plan"

# 表头写这三个词的列 → 自动上语义色(词本身就是语义,不绑具体文档)
LIGHT_COLS = {"绿灯": "pos", "黄灯": "warn", "红灯": "neg"}
CIRCLED = "①②③④⑤⑥⑦⑧⑨"


def tier_label(t):
    """来源类型的中文标签(参考列表显式标注,PM QC U-2)。
    云织 Agent 分支(10-03):成稿是给用户和他的读者看的,用词表里的说法(官方一手 / 机构自报 / 二手),
    不带「A级」这类等级代号(词表禁用)。"""
    t = norm_tier(t)
    if t == "A":
        return "官方一手"
    if t == "A-":
        return "机构自报"
    if t == "B":
        return "二手"
    return "来源类型未标注"


# 云织 Agent 分支(10-03 第四轮):没打开过的来源 —— 资料卡片上这个来源的 fetch_state 是 blocked(打不开)或
# failed(出错),refs_add 原样带进参考文献清单 —— 在参考资料里写「未能打开」,不写「查阅于 <日期>」
# (实测:三条从没读到过的来源照样写着「查阅于…」,读者以为查过)。两个生成脚本共用这一份判法。
NEVER_READ_STATES = ("blocked", "failed")
NEVER_READ_LABEL = "未能打开（只记下了网址，没有读到内容）"


def never_read(e):
    return isinstance(e, dict) and str(e.get("fetch_state") or "").strip() in NEVER_READ_STATES


def load_theme(theme):
    """base.css + <theme>.css,读不到就退回内置兜底(仍可读,只是朴素)。"""
    css = []
    for name in ("base.css", "%s.css" % theme):
        p = os.path.join(THEME_DIR, name)
        if os.path.exists(p):
            with io.open(p, encoding="utf-8") as f:
                css.append("/* %s */\n%s" % (name, f.read()))
        elif name != "base.css":
            sys.stderr.write("⚠️ 主题 %s 不存在,只用 base.css\n" % theme)
    if not css:
        css.append("body{font-family:system-ui,sans-serif;max-width:46em;margin:2em auto;"
                   "padding:0 1.2em;line-height:1.8}")
        sys.stderr.write("⚠️ 主题目录不可用(%s),已用内置兜底样式\n" % THEME_DIR)
    return "\n".join(css)


def inline(text, ph_no, numbered_dict):
    """行内:先转义 → 引用 → 粗体 → 行内代码。顺序不可调。"""
    t = _html.escape(text)
    t = PLACEHOLDER_RE.sub(lambda m: cite_sub(m, ph_no, numbered_dict), t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    return t


def cite_sub(m, ph_no, numbered_dict):
    ph = m.group(1)
    no = ph_no.get(ph)
    if no is None:
        return m.group(0)  # 悬空占位原样留给 cite_check
    e = numbered_dict[no]
    dagger = "†" if e.get("unverified") else ""
    title = _html.escape(str(e.get("title") or e.get("url") or ""))[:90]
    return ('<sup class="doc-cite"><a href="#doc-ref-%d" title="%s">[%d%s]</a></sup>'
            % (no, title, no, dagger))


def parse_blocks(body, ph_no, numbered_dict):
    """逐行扫描 → (html 片段列表, 章节目录, 副标题, 前提)。"""
    lines = body.split("\n")
    out, toc = [], []
    subtitle = premise = None
    sec_n = 0
    i = 0
    inl = lambda s: inline(s, ph_no, numbered_dict)

    while i < len(lines):
        s = lines[i].strip()

        if not s:
            i += 1
            continue

        # 首行整行加粗且含分隔符 → 副标题
        if subtitle is None and not out and s.startswith("**") and s.endswith("**") \
                and re.search(r"[｜·|]", s):
            subtitle = inl(s.strip("*"))
            i += 1
            continue

        # 引用块:第一个作前提框,其余作普通引用
        if s.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            joined = inl(" ".join(x for x in buf if x))
            if premise is None:
                premise = joined
            else:
                out.append('<div class="doc-premise">%s</div>' % joined)
            continue

        # 标题
        m = re.match(r"^(#{1,4})\s+(.+)$", s)
        if m:
            lvl, txt = len(m.group(1)), m.group(2)
            if lvl <= 2:
                sec_n += 1
                sid = "doc-s%d" % sec_n
                num = (txt.split(".")[0].strip()
                       if re.match(r"^\d+[.、]", txt) else str(sec_n))
                title = re.sub(r"^\d+[.、]\s*", "", txt)
                toc.append((sid, num, title))
                out.append(('</section>\n<section class="doc-sec" id="%s">' % sid)
                           if sec_n > 1 else '<section class="doc-sec" id="%s">' % sid)
                out.append('<h2><span class="doc-secnum">%s</span>%s</h2>'
                           % (_html.escape(num), inl(title)))
            else:
                out.append("<h%d>%s</h%d>" % (lvl, inl(txt), lvl))
            i += 1
            continue

        # 表格
        # 认表用共享判定(pipeline_lib.split_table):「有几张表」是交付对接窗口 ⑫ 要
        # 按格式分别数的口径,两个渲染器各自认表就会分叉。生成仍在各自这里。
        table = split_table(lines, i)
        if table is not None:
            head, rows, i = table
            lit = {j: LIGHT_COLS[h] for j, h in enumerate(head) if h in LIGHT_COLS}
            t = ['<div class="doc-table-wrap"><table><thead><tr>']
            for j, h in enumerate(head):
                c = ' class="doc-lt-%s"' % lit[j] if j in lit else ""
                t.append("<th%s>%s</th>" % (c, inl(h)))
            t.append("</tr></thead><tbody>")
            for r in rows:
                t.append("<tr>")
                for j, cell in enumerate(r):
                    c = ' class="doc-lt-%s"' % lit[j] if j in lit else ""
                    t.append("<td%s>%s</td>" % (c, inl(cell)))
                t.append("</tr>")
            t.append("</tbody></table></div>")
            out.append("".join(t))
            continue

        # 行动卡:`**任意标题**:①… ②… ③…`(通用模式,不绑具体措辞)
        m = re.match(r"^\*\*(.{2,20}?)\*\*\s*[:：]\s*(.*[%s].*)$" % CIRCLED, s)
        if m and sum(s.count(c) for c in CIRCLED) >= 2:
            head_txt, rest = m.group(1), m.group(2)
            items = [x.strip() for x in re.split(r"(?=[%s])" % CIRCLED, rest) if x.strip()]
            card = ['<div class="doc-action"><div class="doc-action-h">%s</div><ol>'
                    % inl(head_txt)]
            for it in items:
                card.append("<li>%s</li>" % inl(it.lstrip(CIRCLED).strip().rstrip(";；")))
            card.append("</ol></div>")
            out.append("".join(card))
            i += 1
            continue

        # 列表
        if re.match(r"^([-*+]|\d+[.)])\s+", s):
            ordered = bool(re.match(r"^\d+[.)]\s+", s))
            items = []
            while i < len(lines):
                c = lines[i].strip()
                if re.match(r"^([-*+]|\d+[.)])\s+", c):
                    items.append(re.sub(r"^([-*+]|\d+[.)])\s+", "", c))
                    i += 1
                elif c and items and not re.match(r"^(#{1,4}\s|\||>)", c):
                    items[-1] += " " + c          # 续行并入上一项
                    i += 1
                else:
                    break
            tag = "ol" if ordered else "ul"
            out.append("<%s>%s</%s>"
                       % (tag, "".join("<li>%s</li>" % inl(x) for x in items), tag))
            continue

        # 分隔线:其后若是整段斜体 → 尾注
        if re.match(r"^([-*_])\1{2,}$", s):
            i += 1
            rest = "\n".join(lines[i:]).strip()
            if rest.startswith("*") and rest.rstrip().endswith("*") and "**" not in rest[:2]:
                out.append('<p class="doc-colophon">%s</p>' % inl(rest.strip("*").strip()))
                break
            out.append("<hr>")
            continue

        # 段落(合并到空行)
        buf = [s]
        i += 1
        while i < len(lines) and lines[i].strip() \
                and not re.match(r"^(#{1,4}\s|\||>|[-*+]\s|\d+[.)]\s)", lines[i].strip()):
            buf.append(lines[i].strip())
            i += 1
        out.append("<p>%s</p>" % inl(" ".join(buf)))

    if sec_n:
        out.append("</section>")
    return out, toc, subtitle, premise


def build_refs(numbered):
    """参考列表:条目仍**直链来源 URL**(外链集合据此,勿改)。"""
    items, has_dagger = [], False
    for no, e in numbered:
        lv = e.get("liveness") or {}
        warn = (lv.get("state") == "failed" and lv.get("detail") != "net")
        dagger = "†" if e.get("unverified") else ""
        has_dagger = has_dagger or bool(dagger)
        url = e.get("url") or "#"
        title = _html.escape(e.get("title") or e.get("url") or "?")
        tier = norm_tier(e.get("tier"))
        tcls = {"A": "doc-tier-A", "A-": "doc-tier-Am", "B": "doc-tier-B"}.get(tier, "")
        bits = ['<div class="doc-ref-t">']
        if warn:
            bits.append('<span class="doc-flag doc-flag-warn">⚠️ 链接失效</span> ')
        bits.append('<a href="%s">%s</a>%s ' % (_html.escape(url), title, dagger))
        bits.append('<span class="doc-tier %s">%s</span>'
                    % (tcls, _html.escape(tier_label(e.get("tier")))))
        if tier == "B" and e.get("claims_source"):
            bits.append(' <span class="doc-flag">转引自 %s</span>'
                        % _html.escape(str(e["claims_source"])))
        bits.append("</div>")
        loc = e.get("locator")
        if loc:
            bits.append('<div class="doc-ref-m">%s</div>' % _html.escape(str(loc)))
        as_of = e.get("as_of")
        if never_read(e):
            bits.append('<div class="doc-ref-m">%s</div>' % _html.escape(NEVER_READ_LABEL))
        elif as_of:
            # 云织 Agent 分支(10-03 第三轮):来源的日期是查阅日期(取到它的那天),写明,别被读成发布日期
            bits.append('<div class="doc-ref-m doc-mono">查阅于 %s</div>' % _html.escape(str(as_of)))
        items.append('<li id="doc-ref-%d"><span class="doc-ref-no">%d</span>'
                     '<div>%s</div></li>' % (no, no, "".join(bits)))
    return items, has_dagger


def render(draft_path, refs_path, out_path, title=None, theme=DEFAULT_THEME, cards_dir=None):
    meta, body = read_md(draft_path)
    entries = load_references(refs_path)
    ph_no, numbered = number_entries(body, entries)
    numbered_dict = dict(numbered)
    # 稿件首行的一级标题就是文档名,不是第 1 章 —— 摘掉它,别渲成章节。
    # ⚠️ 与 --title 不一致时以稿件为准并出声:正本是权威,命令行参数是便利。
    doc_title, body = take_doc_title(body)
    if doc_title is not None and title and doc_title != title:
        print("⚠️ 稿件标题与 --title 不一致,以稿件为准:%r ← %r" % (doc_title, title))
    if doc_title is not None:
        title = doc_title
    title = title or meta.get("title") or os.path.splitext(os.path.basename(draft_path))[0]

    blocks, toc, subtitle, premise = parse_blocks(body, ph_no, numbered_dict)
    ref_items, has_dagger = build_refs(numbered)
    total_cites = count_inline_citations(body, entries)

    # ---- 元信息条:一律机器取值,不读正文自述 ----
    # ⚠️ 不放「字数」:①口径本身有歧义(CJK 计数 vs 总字符,同一份文档能得出两个数)
    #   ②它是 3 位以上整数,会被 cite_check ⑤ 当「实质数字」判为查无出处 —— 渲染器
    #   生成的元数据不该由 ⑤ 扫,但那要改检查器,记账另修。这里先不制造这个数。
    cells = [("章节", str(len(toc))), ("文内引用", str(total_cites)),
             ("参考来源", str(len(numbered)))]
    if cards_dir and os.path.isdir(cards_dir):
        n_cards = len([f for f in os.listdir(cards_dir) if f.endswith(".md")])
        cells.insert(2, ("资料卡片", str(n_cards)))   # 云织 Agent 分支(10-03):用词表的说法
    meta_html = "".join('<div><div class="k">%s</div><div class="v">%s</div></div>' % kv
                        for kv in cells)

    toc_html = "".join(
        '<a href="#%s"><span class="n">%s</span>%s</a>'
        % (sid, _html.escape(n), _html.escape(t.split(":")[0].split("：")[0]))
        for sid, n, t in toc)

    foot = ["来源类型：官方一手是官方或院校发布的原始文件，机构自报是机构自己披露的数据，二手是行业协会、媒体、平台等的转述。"]   # 云织 Agent 分支(10-03):不出等级代号
    if has_dagger:
        foot.append("† 该二手来源无法回溯到原始统计,数值未经一手核验。")

    doc = HTML_TMPL % {
        "title": _html.escape(title), "css": load_theme(theme),
        "subtitle": ('<div class="doc-sub">%s</div>' % subtitle) if subtitle else "",
        "meta": meta_html,
        "toc": ('<nav class="doc-toc">%s</nav>' % toc_html) if toc_html else "",
        "premise": ('<div class="doc-narrow"><div class="doc-premise">%s</div></div>'
                    % premise) if premise else "",
        "body": "\n".join(blocks), "refs": "\n".join(ref_items),
        "nrefs": len(numbered), "foot": " ".join(foot),
    }
    with io.open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(doc)
    print("渲染 %s:文内引用 %d 处 · 参考 %d 条 · 主题 %s · 章节 %d · 表格 %d"
          % (out_path, total_cites, len(numbered), theme, len(toc), doc.count("<table")))


HTML_TMPL = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(title)s</title>
<style>
%(css)s
</style></head><body>
<div class="doc">
<header class="doc-masthead">
<h1>%(title)s</h1>
%(subtitle)s
<div class="doc-meta">%(meta)s</div>
</header>
%(toc)s
%(premise)s
<main>
%(body)s
</main>
<section class="doc-refs" id="references">
<h2><span class="doc-secnum">R</span>参考资料</h2>
<ol class="doc-ref-list">
%(refs)s
</ol>
<p class="doc-foot">%(foot)s</p>
</section>
</div>
</body></html>
"""


def main(argv):
    if any(f in argv for f in ("-h", "--help")):
        print(__doc__)
        return 0
    theme, cards = DEFAULT_THEME, None
    title = None
    for flag in ("--title", "--theme", "--cards"):
        if flag in argv:
            i = argv.index(flag)
            if i + 1 >= len(argv):
                print("⚠️ %s 缺少取值" % flag)
                return 2
            val = argv[i + 1]
            if flag == "--title":
                title = val
            elif flag == "--theme":
                theme = val
            else:
                cards = val
            argv = argv[:i] + argv[i + 2:]
    if len(argv) < 4:
        print(__doc__)
        return 2
    render(argv[1], argv[2], argv[3], title, theme, cards)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
