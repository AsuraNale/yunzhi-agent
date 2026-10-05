# -*- coding: utf-8 -*-
"""render_docx — 占位正文 + 登记表 → DOCX 成品(与 render_html 同一套解析/编号规则)。

用法:
  python -X utf8 render_docx.py <draft.md> <references.yaml> <out.docx> [--title 标题]

文内引用 = 上标 [n] 真超链接;文末参考列表每条一个超链接。
"""
import datetime
import os
import re
import sys

import docx
from docx.oxml.ns import qn
from docx.shared import Pt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline_lib import (PLACEHOLDER_RE, load_references, norm_tier, read_md,
                          split_table, take_doc_title)
from render_html import NEVER_READ_LABEL, never_read, number_entries, tier_label


def add_hyperlink(paragraph, url, text, superscript=False):
    """python-docx 原生不支持超链接,手工建 relationship + w:hyperlink。"""
    part = paragraph.part
    r_id = part.relate_to(
        url,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True)
    hl = paragraph._p.makeelement(qn("w:hyperlink"), {qn("r:id"): r_id})
    r = paragraph._p.makeelement(qn("w:r"), {})
    rpr = paragraph._p.makeelement(qn("w:rPr"), {})
    color = paragraph._p.makeelement(qn("w:color"), {qn("w:val"): "8A6D3B"})
    rpr.append(color)
    if superscript:
        va = paragraph._p.makeelement(qn("w:vertAlign"), {qn("w:val"): "superscript"})
        rpr.append(va)
    t = paragraph._p.makeelement(qn("w:t"), {})
    t.text = text
    r.append(rpr)
    r.append(t)
    hl.append(r)
    paragraph._p.append(hl)


# 内联标记:粗体与引用占位。两者在同一次扫描里处理 —— 分两次扫会让
# `**[[R001]]**` 这类嵌套形态的其中一层留在成品里。
INLINE_RE = re.compile(r"\*\*(.+?)\*\*|\[\[(R?\d+)\]\]")


def add_inline(p, text, ph_no, numbered_dict):
    """把一段 markdown 内联文本写进段落 p → 新增的引用数。

    ⚠️ docx 没有「markdown 残留」的概念:`**` 和未展开的 `[[n]]` 会原样成为
    可见字符。实测成品里 `**` 18 处、竖线 233 处 —— 那些都是本该变成
    格式的标记。交付对接窗口 ⑫ 正是按「残留 > 0 → FAIL」判的。
    """
    added = 0
    pos = 0
    for m in INLINE_RE.finditer(text):
        if m.start() > pos:
            p.add_run(text[pos:m.start()])
        if m.group(1) is not None:
            p.add_run(m.group(1)).bold = True
        else:
            ph = m.group(2)
            no = ph_no.get(ph)
            if no is None:
                p.add_run(m.group(0))  # 悬空占位原样可见,交给 cite_check
            else:
                e = numbered_dict[no]
                dagger = "†" if e.get("unverified") else ""
                add_hyperlink(p, e.get("url") or "#", "[%d%s]" % (no, dagger),
                              superscript=True)
                added += 1
        pos = m.end()
    if pos < len(text):
        p.add_run(text[pos:])
    return added


def add_table(doc, head, rows, ph_no, numbered_dict):
    """一张 markdown 表 → 真的 w:tbl → 新增的引用数。

    ⭐ 认表不在这里:`pipeline_lib.split_table` 是唯一口径,html 与 docx 共用。
    这里只负责生成 —— 之前这个函数不存在,表格整块被当段落写进去,于是
    python-docx 数出真表格 0、而 HTML 那边 6 张都是好的。
    """
    table = doc.add_table(rows=1, cols=len(head))
    try:
        table.style = "Table Grid"
    except KeyError:
        # 模板里没有这个样式时不该整份渲染失败:表格结构比边框重要。
        pass
    added = 0
    for cell, htext in zip(table.rows[0].cells, head):
        cell.text = ""
        added += add_inline(cell.paragraphs[0], htext, ph_no, numbered_dict)
        for run in cell.paragraphs[0].runs:
            run.bold = True
    for r in rows:
        cells = table.add_row().cells
        # 列数不齐的行不该丢内容:多出来的并进最后一格,少的留空。
        for j, cell in enumerate(cells):
            cell.text = ""
            if j < len(r):
                chunk = " ".join(r[j:]) if j == len(cells) - 1 and len(r) > len(cells) else r[j]
                added += add_inline(cell.paragraphs[0], chunk, ph_no, numbered_dict)
    return added


def render(draft_path, refs_path, out_path, title=None):
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

    doc = docx.Document()
    # 云织 Agent 分支(10-03 第三轮):文档属性写云织和生成时刻,不留 python-docx 模板自带的作者和 2013 年的日期。
    # 第四轮:python-docx 把时刻原样写成「…Z」(UTC),所以这里给 UTC 时刻;原来给的是本地时刻,Word 里差了 4 个小时。
    props = doc.core_properties
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    props.author = "云织"
    props.last_modified_by = "云织"
    props.title = title
    props.created = now
    props.modified = now
    props.revision = 1
    props.comments = ""
    doc.add_heading(title, level=0)
    # 第四轮:正文最上一级的节(稿件里一般是 ##)是 Word 的标题 1,往下依次是标题 2、3……不跳级;参考资料和章同一级(标题 1)。
    # 原来 ## 照写成标题 2,文档名(Title)下面直接是标题 2,导航窗格里少了一级。
    levels = [len(h) for h in re.findall(r"^\s*(#{1,4})\s+", body, re.M)]
    shift = (min(levels) - 1) if levels else 0
    cite_count = 0
    for block in re.split(r"\n\s*\n", body.strip()):
        block = block.strip()
        if not block:
            continue
        lines = block.split(chr(10))
        i = 0
        buf = []

        def flush(buf=buf):
            """把攒下的普通行作为一个段落写出 → 新增引用数。"""
            if not buf:
                return 0
            text = chr(10).join(buf)
            del buf[:]
            return add_inline(doc.add_paragraph(), text, ph_no, numbered_dict)

        while i < len(lines):
            # 第四轮:标题可以紧贴着正文(标题下一行就是正文、中间没有空行)。原来整块按「以 # 开头 = 标题」处理,
            # 只写了标题那一行,同一块里后面的正文整段丢了(实测网页版 42 处引用、Word 版 34 处,交付前检查照样过)。
            # 现在按行扫:标题行先把攒下的正文写出,再写标题;网页版也是按行认标题的。
            m = re.match(r"^\s*(#{1,4})\s+(.*\S)\s*$", lines[i])
            if m:
                cite_count += flush()
                doc.add_heading(m.group(2), level=max(1, min(len(m.group(1)) - shift, 4)))
                i += 1
                continue
            # 表可以紧贴在正文行之后(块之间才有空行),所以要按行扫,
            # 不能假设「一个块要么整块是表、要么整块是段落」。
            table = split_table(lines, i)
            if table is None:
                buf.append(lines[i])
                i += 1
                continue
            cite_count += flush()
            head, rows, i = table
            cite_count += add_table(doc, head, rows, ph_no, numbered_dict)
        cite_count += flush()

    doc.add_heading("参考资料", level=1)
    has_dagger = False
    for no, e in numbered:
        p = doc.add_paragraph()
        prefix = "%d. " % no
        lv = e.get("liveness") or {}
        if lv.get("state") == "failed" and lv.get("detail") != "net":
            prefix = "⚠️ " + prefix
        p.add_run(prefix)
        add_hyperlink(p, e.get("url") or "#",
                      e.get("title") or e.get("url") or "?")
        if norm_tier(e.get("tier")) == "B" and e.get("claims_source"):
            p.add_run("(转引自 %s)" % e["claims_source"])
        p.add_run(" · %s" % tier_label(e.get("tier")))
        if e.get("unverified"):
            p.add_run("†")
            has_dagger = True
        # 云织 Agent 分支(10-03 第三轮):印出来也查得到 —— 定位、查阅日期、网址(网页版的参考资料同样写了定位和日期)
        if e.get("locator"):
            p.add_run(" · %s" % e["locator"])
        # 第四轮:没打开过的来源(fetch_state 是打不开 / 出错)不写「查阅于」,写「未能打开」(网页版同样)
        if never_read(e):
            p.add_run(" · %s" % NEVER_READ_LABEL)
        elif e.get("as_of"):
            p.add_run(" · 查阅于 %s" % e["as_of"])
        if e.get("url"):
            p.add_run(" · %s" % e["url"])
    fp = doc.add_paragraph(
        "来源类型：官方一手是官方或院校发布的原始文件，机构自报是机构自己披露的数据，二手是行业协会、媒体、平台等的转述。"   # 云织 Agent 分支(10-03):不出等级代号
        + (" † 该二手来源无法回溯到原始统计,数值未经一手核验。" if has_dagger else ""))
    fp.runs[0].font.size = Pt(9)

    doc.save(out_path)
    print("渲染 %s:文内引用 %d 处 · 参考 %d 条" % (out_path, cite_count, len(numbered)))


def main(argv):
    HELP_FLAGS = ("-h", "--help")
    if any(f in argv for f in HELP_FLAGS):
        print(__doc__)
        return 0
    if len(argv) < 4:
        print(__doc__)
        return 2
    title = None
    if "--title" in argv:
        i = argv.index("--title")
        title = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    render(argv[1], argv[2], argv[3], title)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
