# -*- coding: utf-8 -*-
"""cite_check — 交付对接窗口机器清单(规格 §⑥,现十二项)。

用法:
  python -X utf8 cite_check.py --project <项目目录> [--json 输出.json]
      [--numbers-corpus 文件1,文件2,...]   # 数字底库改用这些文件的文本(golden 模式)
      [--skip-stamps]                       # 跳过三印章(单件测试用;交付对接窗口禁止跳)

项目目录按规格 §0 布局:task_plan.md · dossier.md · outline.md · references.yaml ·
cards/(或 cards.jsonl)· drafts/*.md · out/*.html|*.docx

十二项:①无悬空占位 ②无幽灵链接 ③文内⊆参考 ④跨格式相等 ⑤无出处数字
⑥三印章(只查本项目流程里有的环节,v1.1.0 `stages`)⑦liveness
⑧预算仪表(步数或三个出处字段与 session_facts.json 不符 = FAIL,其余 WARN)⑨大纲预算(WARN)
⑩自述统计一致 ⑪窗口后证据变更(WARN)⑫双格式各自机器核
全绿(无 FAIL)是交付卡的前提之一;另一条是独立复核结果对得上当前成稿、必须改为 0(规格 §⑨-2)。
v1.2.0 起这道门槛由应用把关:它弹交付卡前自己再跑一遍本脚本。
"""
import glob
import io
import json
import math
import os
import re
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline_lib import (BARE_URL_RE, FIVE_STATES, PLACEHOLDER_RE, STAGES, Report,
                          count_live_cards, count_tables, count_tables_docx, deliverable_paths,
                          count_tables_html, declared_card_counts, markdown_residue,
                          parse_stages, product_texts_of, stage_docs,
                          content_hash, docx_links, docx_text, html_links,
                          html_text, load_references, number_entries, outline_total_words, read_md,
                          resolve_placeholder, take_doc_title)


# ---- 云织 Agent 分支(10-03 第四轮):各节的文内引用数,两种格式各自与同名底稿对 ----
# ④ 原来只比两种格式的外链集合:Word 版生成时丢了标题后紧贴的那一段(网页版 42 处引用、Word 版 34 处),
# 外链集合照样相等,检查照样过。现在每种格式按节数文内引用(网页版数 <sup class="doc-cite">,Word 版数上标的
# [n] 超链接),与同名底稿按同一套切节数出的数比 —— 和 ⑫ 一样,判据是「各自与底稿相等」,不是两种格式互相相等。
_HEAD_LINE_RE = re.compile(r"^(#{1,4})\s+(.+)$")
_HTML_HEAD_RE = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1>", re.S | re.I)
_HTML_CITE_RE = re.compile(r"<sup class=\"doc-cite\">", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def draft_cites_by_section(body, entries):
    """底稿正文 → ([[节名, 引用数]…], 第一个引用块所在的节号或 None)。第 0 节是第一个标题之前的部分(「开头」)。
    切节和两个生成脚本一样:按行认 1–4 个 # 的标题(文档名那一行先摘掉);只数登记表里查得到的占位。"""
    ph_no, _ = number_entries(body, entries)
    _title, body = take_doc_title(body)
    sections, premise_at = [["开头", 0]], None
    for line in body.split("\n"):
        s = line.strip()
        m = _HEAD_LINE_RE.match(s)
        if m:
            sections.append([m.group(2).strip(), 0])
        elif premise_at is None and s.startswith(">"):
            premise_at = len(sections) - 1     # 网页版把第一个引用块挪到开头当前提框:它的引用记回这一节
        sections[-1][1] += sum(1 for ph in PLACEHOLDER_RE.findall(line) if ph in ph_no)
    return sections, premise_at


def html_cites_by_section(path, premise_at=None):
    """网页版 → [[节名, 引用数]…](节的切法同底稿;前提框里的引用记回底稿里那个引用块所在的节)。"""
    with io.open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    at, end = text.find("<main>"), text.rfind("</main>")
    pre, main = (text[:at], text[at:end if end > at else len(text)]) if at >= 0 else ("", text)
    pm = re.search(r'<div class="doc-premise">(.*?)</div>', pre, re.S)
    premise = len(_HTML_CITE_RE.findall(pm.group(1))) if pm else 0
    sections = [["开头", len(_HTML_CITE_RE.findall(pre)) - premise]]
    pos = 0
    for m in _HTML_HEAD_RE.finditer(main):
        sections[-1][1] += len(_HTML_CITE_RE.findall(main[pos:m.start()]))
        sections.append([_TAG_RE.sub("", m.group(2)).strip(), len(_HTML_CITE_RE.findall(m.group(0)))])
        pos = m.end()
    sections[-1][1] += len(_HTML_CITE_RE.findall(main[pos:]))
    if premise:
        k = premise_at if premise_at is not None and premise_at < len(sections) else 0
        sections[k][1] += premise
    return sections


def docx_cites_by_section(path):
    """Word 版 → [[节名, 引用数]…]:文档名(Title)不算节;每个标题段起一节,到「参考资料」为止;数上标的 [n] 超链接。"""
    import zipfile
    from xml.etree import ElementTree as ET
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    sections = [["开头", 0]]
    for p in root.iter(_W + "p"):
        st = p.find(_W + "pPr/" + _W + "pStyle")
        sid = st.get(_W + "val") if st is not None else ""
        if sid == "Title":
            continue
        if sid.startswith("Heading"):
            name = "".join(t.text or "" for t in p.iter(_W + "t")).strip()
            if name == "参考资料":
                break
            sections.append([name, 0])
        for hl in p.iter(_W + "hyperlink"):
            for r in hl.iter(_W + "r"):
                va = r.find(_W + "rPr/" + _W + "vertAlign")
                t = "".join(x.text or "" for x in r.iter(_W + "t"))
                if va is not None and va.get(_W + "val") == "superscript" and re.fullmatch(r"\[\d+†?\]", t):
                    sections[-1][1] += 1
    return sections


def section_cite_mismatch(expected, got):
    """→ 第一处对不上的一句说明;对得上 → None。"""
    if len(expected) != len(got):
        return "节数不同:底稿 %d 节,这一版 %d 节" % (len(expected) - 1, len(got) - 1)
    for i, ((name, want), (_n, have)) in enumerate(zip(expected, got)):
        if want != have:
            return "%s:底稿 %d 处,这一版 %d 处" % ("开头" if i == 0 else "第 %d 节「%s」" % (i, name), want, have)
    return None


def norm_url(u):
    return (u or "").strip().rstrip("/")


def timezone_iso8601(value):
    """有效且带时区的 ISO-8601；接受 JSON 常见的尾随 Z。"""
    if not isinstance(value, str) or not value.strip():
        return False
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).tzinfo is not None
    except ValueError:
        return False


def as_session_facts_text(value):
    """dossier 里 YAML 读出来的日期时间 → session_facts.mjs 写的那种字符串。

    ⛔ v1.1.0 · C-3:`captured_at: 2026-09-04T12:00:00.000Z` 不加引号,YAML 会把
    它读成 datetime。原来 ⑧ 只认字符串,于是报「没记 captured_at」(字段明明在),
    而且**绕过了不同源 FAIL** —— 规格示例的时间戳本来就不加引号。
    归一的目标形态就是 mjs 的写法(JS `Date.prototype.toISOString()`:UTC、
    毫秒三位、尾随 Z),归一后与 json 里的串逐字比,判据不放宽。
    - 带时区的 datetime → `YYYY-MM-DDTHH:MM:SS.mmmZ`(微秒不是整毫秒时写六位,
      那样必然对不上 json —— 对不上本来就是事实);
    - 不带时区的 datetime / date → isoformat(对不上 json,照实判不符);
    - 其他值原样返回。
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.isoformat()
        u = value.astimezone(timezone.utc)
        frac = (".%03dZ" % (u.microsecond // 1000) if u.microsecond % 1000 == 0
                else ".%06dZ" % u.microsecond)
        return u.strftime("%Y-%m-%dT%H:%M:%S") + frac
    if isinstance(value, date):
        return value.isoformat()
    return value


# ---- ⑤ 数字扫描(spike① 语义冻结版) ----
NUM_CTX_RE = re.compile(r"(\d[\d,，]*(?:\.\d+)?)\s*([%‰]|万亿|亿|万|千米|千瓦|千)?")

# ⑩ 自述统计:成品里「N 张证据卡」这类关于文档自身的统计。
# ⚠️ 必须要求量词(张/个/份)且与「证据卡」紧邻 —— 否则会跨格误匹配渲染器元信息条
#    (提取成文本后形如「文内引用 62    证据卡 23」,松正则会把 62 读成卡数。实测踩过)。


# ⑤ 的节号(A2 联调):成稿里的「3.1 增速对比」是草稿 / 提纲自己的节号,不是实质数字。原来只看数字前头 4 个字
# 是不是空白或 #,可渲染出来的标题前头紧挨着上一段的末尾(html 去掉标签是「。 \n 」,docx 是「。\n」),节号照样
# 被当成查无出处的数字。现在按草稿与提纲里带编号的标题认:节号之后、去掉空白,紧跟着的是那个标题的头几个字,才算节号;
# 正文里同一个数(「比全省高 3.1 个百分点」)后面不是那个标题,照样要出处。
SECTION_HEADING_RE = re.compile(
    r"^#{1,6}[ \t]+(?P<num>\d{1,2}(?:\.\d{1,2})+)[.、]?[ \t]+(?P<title>[^\n{]*[^\s{])[ \t]*(?:\{[^}\n]*\})?[ \t]*$", re.M)
_TITLE_MARKS_RE = re.compile(r"[\s*_`]+")


def section_headings(texts):
    """草稿与提纲的文字 → {(节号, 去掉空白与强调记号的标题)};节号是 3.1、3.1.2 这种(每段 ≤2 位)。"""
    out = set()
    for text in texts:
        for m in SECTION_HEADING_RE.finditer(text):
            title = _TITLE_MARKS_RE.sub("", m.group("title"))
            if title:
                out.add((m.group("num"), title))
    return out


def is_section_number(text, at, headings):
    """text[at:] 以某个带编号的标题开头:节号之后是空白(或「、」「.」),再往后去掉空白是那个标题的头几个字。"""
    for num, title in headings:
        if not text.startswith(num, at):
            continue
        after = text[at + len(num):at + len(num) + 60]
        if not after or not (after[0].isspace() or after[0] in "、."):
            continue
        if _TITLE_MARKS_RE.sub("", after).lstrip("、.").startswith(title[:6]):
            return True
    return False


def substantive_numbers(text, headings=()):
    """抽「实质数字」:≥3 位整数 / 带小数 / 带单位;裸年份 1900–2100 排除;
    千分位归一;草稿 / 提纲自己的节号(`headings`,见 section_headings)排除。→ [(归一 token, 原文上下文)]"""
    out = []
    for m in NUM_CTX_RE.finditer(text):
        raw, unit = m.group(1), m.group(2)
        tok = raw.replace(",", "").replace("，", "")
        if not tok or not tok[0].isdigit():
            continue
        has_dec = "." in tok
        # 章节号形态(1.1 / 2.3 等:小数点两侧均 ≤2 位且无单位)不是实质数字
        # —— 野外测试暴露的误报类
        if has_dec and not unit:
            a, _, b = tok.partition(".")
            if len(a) <= 2 and len(b) <= 2 and b.isdigit():
                ctx_before = text[max(0, m.start() - 4):m.start()]
                if ctx_before.strip() in ("", "#", "##", "###", "####", "Section", "节"):
                    continue
                if is_section_number(text, m.start(), headings):
                    continue
        if not has_dec and not unit and len(tok) <= 2:
            continue  # 短整数(引用号/序数)不扫
        if not has_dec and not unit and len(tok) == 4:
            y = int(tok)
            if 1900 <= y <= 2100:
                continue  # 裸年份
        ctx = text[max(0, m.start() - 18):m.end() + 8].replace("\n", " ")
        out.append((tok, ctx))
    return out


def check_numbers(rep, product_texts, src_corpus, card_corpus, headings=()):
    """v1.0.2(实证)三分列:

    原实现的底库 = 卡 + 登记表 **合在一起**,于是「卡里写下的数字自己证明自己」——
    某条「2024年40人」是落卡时编进去的,底库当然找得到它,check 照样报「全部可溯源」。
    现在分开报:
      · 追到来源侧 = 出现在 sources 的 locator/title/note 或登记表条目 → 强溯源
      · 仅见于卡自述 = 只出现在卡的 facts/正文 → **本检查不能证明它有出处**
      · 查无出处 → FAIL
    这不改变拦截线(仍只拦查无),但**停止夸大本检查证明了什么**。
    """
    norm = lambda t: t.replace(",", "").replace("，", "")
    src_n, card_n = norm(src_corpus), norm(card_corpus)
    bad, only_card, total = [], 0, 0
    for name, text in product_texts:
        for tok, ctx in substantive_numbers(text, headings):
            total += 1
            if tok in src_n:
                continue
            if tok in card_n:
                only_card += 1
                continue
            bad.append((name, tok, ctx))
    if bad:
        for name, tok, ctx in bad[:20]:
            rep.add("无出处数字", "FAIL", "%s: %r 查无出处(…%s…)" % (name, tok, ctx),
                    "回卡库补卡带口径,或修正笔误;禁止直接改成品")
        if len(bad) > 20:
            rep.add("无出处数字", "FAIL", "…另有 %d 处未列出" % (len(bad) - 20), "")
    else:
        rep.add("无出处数字", "PASS",
                "实质数字 %d 处:追到来源侧 %d · **仅见于卡自述 %d(本检查不证明其有出处)** · 查无 0"
                % (total, total - only_card, only_card))


def check_project(project, rep, numbers_corpus=None, skip_stamps=False):
    p = lambda *a: os.path.join(project, *a)
    refs_path = p("references.yaml")
    entries = load_references(refs_path) if os.path.exists(refs_path) else None
    if entries is None:
        rep.add("登记表", "FAIL", "references.yaml 不存在", "先建登记表(refs_add.py)")
        return
    reg_urls = {norm_url(e.get("url")) for e in entries if e.get("url")}

    drafts = sorted(glob.glob(p("drafts", "*.md")))
    # v1.2.1 复核:成稿只有 deliverable_paths 一种列法(规格 §⑨-2)。原来这里自己 glob,Word 开着成稿时 out/ 里的
    # `~$稿.docx` 锁文件被 ③④⑫ 当成 docx 去读,BadZipFile 崩掉,一项结果都没有。
    deliverables = deliverable_paths(project)
    outs_html = [path for rel, path in deliverables if rel.lower().endswith(".html")]
    outs_docx = [path for rel, path in deliverables if rel.lower().endswith(".docx")]

    # ① 无悬空占位
    dangling = []
    for d in drafts:
        _, body = read_md(d)
        for ph in set(PLACEHOLDER_RE.findall(body)):
            if resolve_placeholder(ph, entries) is None:
                dangling.append((os.path.basename(d), ph))
    if dangling:
        for name, ph in dangling:
            rep.add("无悬空占位", "FAIL", "%s: [[%s]] 不在登记表" % (name, ph),
                    "refs_add 补登记,或修正占位 id")
    else:
        rep.add("无悬空占位", "PASS", "%d 份草稿占位全部可解析" % len(drafts))

    # ② 无幽灵链接(草稿裸 URL 必须经登记表)
    ghosts = []
    for d in drafts:
        _, body = read_md(d)
        for u in BARE_URL_RE.findall(body):
            if norm_url(u) not in reg_urls:
                ghosts.append((os.path.basename(d), u))
    if ghosts:
        for name, u in ghosts[:10]:
            rep.add("无幽灵链接", "FAIL", "%s: 裸 URL 未经登记 %s" % (name, u[:80]),
                    "登记后改用 [[R###]] 占位")
    else:
        rep.add("无幽灵链接", "PASS", "草稿无未登记裸 URL")

    # ③ 文内 ⊆ 参考
    for h in outs_html:
        prod = set(map(norm_url, html_links(h)))
        extra = prod - reg_urls
        if extra:
            rep.add("文内⊆参考", "FAIL", "%s: %d 条链接不在登记表,如 %s" % (
                os.path.basename(h), len(extra), sorted(extra)[0][:80]),
                    "成品链接必须全部来自登记表(正本/渲染分离)")
        else:
            rep.add("文内⊆参考", "PASS", "%s: %d 条链接 ⊆ 登记表" % (
                os.path.basename(h), len(prod)))

    # ④ 跨格式相等
    if outs_html and outs_docx:
        hset = set(map(norm_url, html_links(outs_html[0])))
        dset = set(map(norm_url, docx_links(outs_docx[0])))
        if hset == dset:
            rep.add("跨格式相等", "PASS", "html(%d) == docx(%d) 外链集合" % (
                len(hset), len(dset)))
        else:
            rep.add("跨格式相等", "FAIL",
                    "html %d 条 vs docx %d 条;html独有 %d · docx独有 %d" % (
                        len(hset), len(dset), len(hset - dset), len(dset - hset)),
                    "两格式必须同一正本渲染;禁止手改单一格式")
    elif outs_html or outs_docx:
        rep.add("跨格式相等", "FAIL", "只有单一格式成品(html %d / docx %d)" % (
            len(outs_html), len(outs_docx)), "两种格式都由渲染器出")
    # ④ 续(云织 Agent 分支 · 第四轮):各节的文内引用数,每种格式各自与同名底稿对
    if drafts and (outs_html or outs_docx):
        by_stem = {os.path.splitext(os.path.basename(d))[0]: d for d in drafts}
        bad_secs, ok_secs = [], []
        for path in outs_html + outs_docx:
            name = os.path.basename(path)
            stem, ext = os.path.splitext(name)
            src = by_stem.get(stem)
            if src is None:
                continue        # 找不到同名底稿:⑫ 会报
            expected, premise_at = draft_cites_by_section(read_md(src)[1], entries)
            try:
                got = (html_cites_by_section(path, premise_at) if ext.lower() == ".html"
                       else docx_cites_by_section(path))
            except Exception as error:   # 坏的 docx:照实报,不崩
                bad_secs.append("%s:读不出来(%s)" % (name, type(error).__name__))
                continue
            why = section_cite_mismatch(expected, got)
            if why:
                bad_secs.append("%s:%s" % (name, why))
            else:
                ok_secs.append("%s %d 处" % (name, sum(n for _s, n in got)))
        if bad_secs:
            for line in bad_secs:
                rep.add("跨格式相等", "FAIL", "各节文内引用数与底稿对不上 —— %s" % line,
                        "两种成稿都用 render_html.py / render_docx.py 从同一份底稿重新生成;标题里不要放引用")
        elif ok_secs:
            rep.add("跨格式相等", "PASS", "各节文内引用数都与底稿相等:%s" % ";".join(ok_secs))

    # ⑤ 无出处数字
    src_corpus, card_corpus = "", ""
    if numbers_corpus:
        # 外部底库(如被审文档正文)算「来源侧」
        for f in numbers_corpus:
            src_corpus += (html_text(f) if f.endswith(".html")
                           else io.open(f, encoding="utf-8", errors="replace").read())
    cards_src = p("cards.jsonl") if os.path.exists(p("cards.jsonl")) else p("cards")
    if os.path.exists(cards_src):
        from card_check import load_cards
        for card, _ in load_cards(cards_src):
            # 来源侧:sources 的 locator/title/note/claims_source
            for so in (card.get("sources") or []):
                src_corpus += json.dumps(
                    {k: so.get(k) for k in
                     ("locator", "title", "note", "claims_source", "url")},
                    ensure_ascii=False, default=str)
            # 卡自述:facts + 正文字段
            # default=str:YAML 会把未加引号的 as_of 解析成 date 对象(实测)
            card_corpus += json.dumps(
                {k: v for k, v in card.items() if k != "sources"},
                ensure_ascii=False, default=str)
    src_corpus += json.dumps(entries, ensure_ascii=False, default=str)
    product_texts = product_texts_of(project)
    # 草稿与提纲里带编号的标题:成稿里的同一个节号不算实质数字(A2 联调)
    heading_texts = []
    for f in drafts + ([p("outline.md")] if os.path.exists(p("outline.md")) else []):
        with io.open(f, encoding="utf-8", errors="replace") as fh:
            heading_texts.append(fh.read())
    if (src_corpus or card_corpus) and product_texts:
        check_numbers(rep, product_texts, src_corpus, card_corpus, section_headings(heading_texts))
    else:
        rep.add("无出处数字", "FAIL", "缺证据底库(cards/)或成品(out/)", "先落卡再成稿")

    # ⑥ 三印章
    if skip_stamps:
        rep.add("三印章", "WARN", "--skip-stamps:本次未查(交付对接窗口禁止跳)", "")
    else:
        # v1.1.0 · 流程可调(V8):只要求本项目流程里有的环节的确认记录。
        # stages 读不出来 → FAIL 并按四个环节全查(最严),不当成「省掉了」。
        tp0 = p("task_plan.md")
        stages, stages_err = parse_stages(read_md(tp0)[0] if os.path.exists(tp0) else {})
        if stages_err:
            rep.add("三印章", "FAIL",
                    "task_plan.md 的 %s —— 读不出本项目有哪些环节,按四个环节全查" % stages_err,
                    "stages 写成列表,如 [task, sources, outline, delivery];本版只允许省掉 outline")
            stages = list(STAGES)
        in_flow = stage_docs(stages)
        for name in ("task_plan.md", "dossier.md", "outline.md"):
            fp = p(name)
            if name not in in_flow:
                rep.add("三印章", "PASS",
                        "%s:本项目流程没有这个环节(task_plan.stages = %s),不要求确认"
                        % (name, stages))
                continue
            if not os.path.exists(fp):
                rep.add("三印章", "FAIL", "%s 不存在" % name, "五正本齐全才可交付")
                continue
            meta, body = read_md(fp)
            ap = meta.get("approval")
            if ap is not None and not isinstance(ap, dict):
                # v1.1.1 · Tb2(同类):`approval: approved` 写成标量,原来 `.get` 抛
                # AttributeError,整个交付检查没有输出。读不出的确认记录不算确认。
                rep.add("三印章", "FAIL",
                        "%s: approval 不是键值映射(现为 %s %s)—— 读不出确认记录"
                        % (name, type(ap).__name__, repr(ap)[:60]),
                        "确认记录只由 stamp.py 写:--invalidate --why 恢复成等确认,再重过该窗口")
                continue
            ap = ap or {}
            if ap.get("status") != "approved":
                rep.add("三印章", "FAIL", "%s: approval.status=%r" % (name, ap.get("status")),
                        "过窗口落印章(执行预告+确认按钮)")
            elif ap.get("approved_hash") != content_hash(meta, body):
                rep.add("三印章", "FAIL",
                        "%s: 印章 hash %s ≠ 当前内容 %s(批准后被改)" % (
                            name, ap.get("approved_hash"), content_hash(meta, body)),
                        "内容变更 → 印章作废 → 重过该窗口")
            else:
                rep.add("三印章", "PASS", "%s 印章有效" % name)

    # ⑦ liveness
    no_rec = [e.get("id") for e in entries
              if (e.get("liveness") or {}).get("state") not in FIVE_STATES]
    if no_rec:
        rep.add("liveness", "FAIL", "%d 条缺五态记录:%s" % (
            len(no_rec), no_rec[:8]), "交付前必跑 liveness 探测(5a)")
    else:
        # v1.0.1(复核 F5):failed 带 detail:"net"(网络类失败)按 blocked 处理,
        # 不要求 ⚠️;仅 4xx/5xx(detail 为 4xx/5xx 或未填)上角标。
        # 云织 Agent 分支(10-03 第四轮):只要求**成稿里引用了的**条目带 ⚠️。参考资料只列引用了的条目,没引用的
        # (例如只记在资料缺口卡上、没打开过的网址)根本不上成稿,原来照样要它带 ⚠️ —— 这一项永远过不了,
        # 「重跑渲染器」也修不好,助手最后只好去引用一条死链接。
        cited_ids = set()
        for d in drafts:
            cited_ids.update(e.get("id") for _no, e in number_entries(read_md(d)[1], entries)[1])
        failed = [e for e in entries
                  if e["liveness"]["state"] == "failed"
                  and e["liveness"].get("detail") != "net"
                  and e.get("id") in cited_ids]
        unmarked = []
        for e in failed:
            # ⚠️ 判定:成品文本中该条目标题附近须有 ⚠️
            ok = False
            for _, text in product_texts:
                t = e.get("title") or ""
                i = text.find(t[:20]) if t else -1
                if i >= 0 and "⚠" in text[max(0, i - 30):i + len(t) + 30]:
                    ok = True
            if not ok:
                unmarked.append(e.get("id"))
        # 云织 Agent 分支(10-04 第八轮):成稿里引用了、探测却没打开的条目 —— 打不开(blocked)、连不上(failed 带 net,
        # 含不能联网时的「未能自动检查」)—— 又没记成读过,也拦。实测:一条引用了两次的来源探测连不上,助手和复核助手
        # 其实都用网页工具读过,记录却一直写着打不开。这几种不上 ⚠️,记成读过(agent-tools/liveness.py mark)后成稿一个字不变,
        # 不用重新生成;mark 只收资料卡片上有原文摘录的(读过的证据),没读过的记不了 —— 那就改掉成稿里的引用。
        unreached = [e.get("id") for e in entries
                     if e.get("id") in cited_ids
                     and (e["liveness"]["state"] == "blocked"
                          or (e["liveness"]["state"] == "failed" and e["liveness"].get("detail") == "net"))]
        if unmarked or unreached:
            said, hints = [], []
            if unmarked:
                said.append("failed 条目未在成品带 ⚠️:%s" % unmarked)
                hints.append("重跑渲染器(它会自动加角标)")
            if unreached:
                said.append("成稿里引用了、链接探测却没打开(打不开或连不上):%s" % unreached)
                hints.append("这一轮读过的(资料卡片上有原文摘录)跑 agent-tools/liveness.py mark \"<项目名>\" --id <编号> 记下读过,"
                             "不用重新生成成稿;没读过的不能记,改掉成稿里的这处引用")
            rep.add("liveness", "FAIL", ";".join(said), ";".join(hints))
        else:
            rep.add("liveness", "PASS", "%d 条五态齐;failed %d 条已标注" % (
                len(entries), len(failed)))

    # ⑧ 预算仪表(WARN)
    #   ⚠️ 两个数的可核性**不一样**,分开报。
    #   · cards_count —— 由 ⑩ 对着 cards/ 实际张数核过,可信。
    #   · steps_used  —— **可以核**(v1.0.5 更正)。v1.0.3 曾写「步数只存在于 DSH
    #     session 事件里,脚本读不到」——⛔ **那句是错的**:实测会话日志里有 255 个
    #     `step/start`,session_facts.mjs 读的就是它。那句假话的代价是这项检查
    #     十天里一直在替自报数背书。
    #   ⛔ `dash.get("steps_used", 0)` 原来把 null 当 0:超限判断于是永远不触发,
    #     而输出还写着「预算内」。null 是「没取到」,不是「用了 0 步」。
    tp_path, do_path = p("task_plan.md"), p("dossier.md")
    if os.path.exists(tp_path) and os.path.exists(do_path):
        tp_meta, _ = read_md(tp_path)
        do_meta, _ = read_md(do_path)
        # ⛔ v1.1.0 · C-2:`budget` / `dashboard` 写成字符串或列表时,原来下一行的
        # `.get` 抛 AttributeError,整个交付检查没有输出。形状不对 = 读不出,照实报,
        # 不当成「没设上限」(那会落进「预算内」)。
        unreadable = []

        def _mapping(meta, key, owner):
            raw = meta.get(key) if isinstance(meta, dict) else None
            if raw is None:
                return {}, False
            if isinstance(raw, dict):
                return raw, False
            unreadable.append("%s 的 %s 不是映射(现为 %s)" % (owner, key, type(raw).__name__))
            return {}, True

        budget, _ = _mapping(tp_meta, "budget", "task_plan.md")
        dash, dash_bad = _mapping(do_meta, "dashboard", "dossier.md")
        # ⛔ 比大小之前一律先判类型:`None > int` 抛 TypeError,而 cite_check 一崩
        # 就没有报告、没有 PASS/FAIL 清单、stdout 全空 —— 交付对接窗口看起来是「什么都
        # 没说」而不是「拦住了」。v1.0.6 ④ 诊断的正是这一条,当时只修了 steps_used,
        # 把**紧邻上一行**同样形态的 cards_count 留在原地(对抗核查 2026-09-04 抓到)。
        # v1.1.0 · C-1:上限那一侧(`steps_max` / `cards_max`)同样要先归一 ——
        # 写成 `'500'` 时原来照样 TypeError。
        def _num(x):
            """能当整数用就返回 int,否则 None。

            ⚠️ `'7'` 与 `7` 视为同一个数:YAML 里给数字加引号是常见笔误,而按
            `!=` 直接判"两边不符"会产生一条**两边印出来一模一样**的 FAIL
            (「dossier 写 7,session_facts.json 记 7 —— 不符」),读的人只会
            以为窗口坏了,不会去改正本。
            """
            if x is None or isinstance(x, bool):
                return None
            if isinstance(x, int):
                return x
            if isinstance(x, float):
                # v1.1.1:YAML 的 `.nan` / `.inf` 也是 float,`int()` 对它们抛错。
                return int(x) if math.isfinite(x) and x == int(x) else None
            if isinstance(x, str):
                s = x.strip()
                # ⛔ v1.1.1 · Tb3:只认 ASCII 数字。`str.isdigit()` 对上标 `'²'`、带圈的
                # `'①'` 也为真,而 `int()` 不收它们 → ValueError,整个交付检查没有输出;
                # 全角 `'７'`、阿拉伯-印度数字 `'٣'` 倒是 `int()` 收,但它们不是「数字
                # 加了引号」这种笔误,不替它猜成 7 / 3(原来会与机器取值「一致」而报 PASS)。
                return int(s) if s.isascii() and s.isdigit() else None
            return None

        steps_raw, cards_raw = dash.get("steps_used"), dash.get("cards_count")
        steps, cards = _num(steps_raw), _num(cards_raw)

        # json 读不出来有**三种**不同的原因,不能都说成「文件不在」——
        # 「不在」会让人去跑脚本,而真正的毛病可能是脚本跑了却没取到。
        facts_path = p("out", "session_facts.json")
        facts_state, facts_steps = "absent", None
        facts_session, facts_pick, facts_captured = None, None, None
        if os.path.exists(facts_path):
            facts_state = "unreadable"
            try:
                blob = json.load(io.open(facts_path, encoding="utf-8"))
                if isinstance(blob, dict):
                    facts_steps = _num(blob.get("steps_used"))
                    facts_session = blob.get("session")
                    facts_pick = blob.get("session_picked_by")
                    facts_captured = blob.get("captured_at")
                    facts_state = "ok" if facts_steps is not None else "null"
            except (ValueError, OSError):
                facts_state = "unreadable"

        def _limit(key):
            """上限 → 整数或 None。空值与 0 = 没设(沿用原口径);设了却读不出 → 记一笔。"""
            raw = budget.get(key)
            if raw is None or raw == "" or (raw == 0 and not isinstance(raw, bool)):
                return None
            val = _num(raw)
            if val is None:
                unreadable.append("task_plan.budget.%s=%r 不是整数" % (key, raw))
                return None
            return val if val != 0 else None

        over, unknown = [], []
        cards_max, steps_max = _limit("cards_max"), _limit("steps_max")
        if cards_max:
            if cards is None:
                unknown.append("cards(dashboard.cards_count=%r)" % (cards_raw,))
            elif cards > cards_max:
                over.append("cards %s>%s" % (cards, cards_max))
        if steps_max:
            # ⛔ 机器值优先:自报可以是空的,而机器取到的那个数才是用量。
            # 原来只看自报,于是「dossier 写 null、json 记 200、上限 10」报 PASS 预算内。
            usage = facts_steps if facts_steps is not None else steps
            src = "session_facts.json" if facts_steps is not None else "dossier 自报"
            if usage is None:
                unknown.append("steps(dossier 与 session_facts.json 都没有整数)")
            elif usage > steps_max:
                over.append("steps %s>%s(取自 %s)" % (usage, steps_max, src))
        if over:
            rep.add("预算仪表", "WARN", "超限:%s%s" % (
                "; ".join(over),
                (";另有读不出的:%s" % "; ".join(unreadable)) if unreadable else ""),
                    "超限只问不停(1b):向用户确认是否继续")
        elif unknown and not unreadable:
            # ⛔ 「上限设了、用量读不出」不是「预算内」。原来这一支落进 PASS,
            # 于是一份用量未知的成品在交付对接窗口上显示「预算内」。
            rep.add("预算仪表", "WARN",
                    "设了上限但用量读不出:%s —— 这不等于「预算内」" % "; ".join(unknown),
                    "把 dashboard 的数补成整数;步数跑 session_facts.mjs 取")
        elif unreadable or unknown:
            rep.add("预算仪表", "WARN",
                    "预算读不出:%s —— 这不等于「预算内」" % "; ".join(unreadable + unknown),
                    "budget / dashboard 写成 `键: 值` 的映射,上限与用量写整数"
                    "(不加引号);步数跑 session_facts.mjs 取")
        else:
            rep.add("预算仪表", "PASS",
                    "预算内(或未设上限);cards_count 已由 ⑩ 核实")

        # 步数单独一项:它有机器出处,和 cards_count 不是一回事。
        # 这才是技能里那条 ⛔「不许自报」的机器兜底 —— 之前只有文字。
        take = ("跑 node <toolkit>/scripts/session_facts.mjs --project <project> "
                "--json <project>/out/session_facts.json;⛔ 不许写 0")
        pick_note = ("(该 json 的选法:%s)" % facts_pick) if facts_pick else ""
        # v1.1.0 · C-3:三个出处字段先归一 —— YAML 把不加引号的时间读成 datetime。
        prov_raw = {k: dash.get(k) for k in ("session", "session_picked_by", "captured_at")}
        prov = {k: as_session_facts_text(v) for k, v in prov_raw.items()}
        coerced = [k for k, v in prov_raw.items() if isinstance(v, (datetime, date))]
        coerced_note = ("(dossier 的 %s 没加引号,YAML 读成了日期时间;已按 session_facts 的写法"
                        "归一后再比)" % "、".join(coerced)) if coerced else ""
        if steps is None:
            rep.add("预算仪表·步数", "WARN",
                    ("dossier 的 dashboard 不是映射,steps_used **未取到**" if dash_bad else
                     "dossier 的 dashboard.steps_used **未取到**(现为 %r,不是整数)"
                     % (steps_raw,)), take)
        elif facts_state == "absent":
            rep.add("预算仪表·步数", "WARN",
                    "dossier 写 steps_used=%s,而 out/session_facts.json **不存在** —— "
                    "没有机器出处,等于自报" % (steps,), take)
        elif facts_state == "unreadable":
            rep.add("预算仪表·步数", "WARN",
                    "out/session_facts.json 在,但读不出(不是合法 json 对象)—— "
                    "dossier 写的 %s 仍然没有机器出处" % (steps,),
                    "重跑 session_facts.mjs 覆盖它;别手改这个文件")
        elif facts_state == "null":
            rep.add("预算仪表·步数", "WARN",
                    "out/session_facts.json 在,但里面的 steps_used 不是整数 —— "
                    "那一轮**没取到**,不能拿它给 dossier 的 %s 背书" % (steps,),
                    "看脚本退出码:非 0 表示未取到会话日志,此时报告要写「未取到会话日志」")
        elif facts_steps != steps:
            rep.add("预算仪表·步数", "FAIL",
                    "dossier 写 %s(原值 %r),session_facts.json 记 %s —— 自报与机器取值不符%s"
                    % (steps, steps_raw, facts_steps, pick_note),
                    "以 session_facts.json 为准改 dossier(口径:取数那一刻的 step/start 计数)")
        elif not all(isinstance(x, str) and x.strip()
                     for x in (facts_session, facts_pick, facts_captured)):
            rep.add("预算仪表·步数", "WARN",
                    "steps_used=%s 与 session_facts.json 一致,但该 json 缺少完整的 "
                    "session / session_picked_by / captured_at —— 机器出处本身不可复核"
                    % (steps,),
                    "重跑当前版 session_facts.mjs,不要手改 json")
        elif not timezone_iso8601(facts_captured):
            rep.add("预算仪表·步数", "WARN",
                    "steps_used=%s 与 session_facts.json 一致,但 captured_at=%r "
                    "不是带时区的 ISO-8601 —— 取数时刻不可复核"
                    % (steps, facts_captured),
                    "重跑当前版 session_facts.mjs,不要手改 json")
        elif not all(isinstance(prov[k], str) and prov[k].strip()
                     for k in ("session", "session_picked_by", "captured_at")):
            # 规格 §⑧-2:三个出处字段必须落到正本,否则选错会话时没人看得见。
            rep.add("预算仪表·步数", "WARN",
                    "steps_used=%s 与 session_facts.json 一致,但 dossier.dashboard 没记 "
                    "session / session_picked_by / captured_at —— 数对得上,却不知道对的是哪一轮%s"
                    % (steps, pick_note),
                    "把 json 里的 session、session_picked_by、captured_at 逐值抄进 dashboard")
        elif (prov["session"] != facts_session
              or prov["session_picked_by"] != facts_pick
              or prov["captured_at"] != facts_captured):
            mismatches = []
            for key, expected in (("session", facts_session),
                                  ("session_picked_by", facts_pick),
                                  ("captured_at", facts_captured)):
                if prov[key] != expected:
                    mismatches.append("%s:dossier=%r,json=%r" % (key, prov[key], expected))
            rep.add("预算仪表·步数", "FAIL",
                    "steps_used=%s 虽一致,但机器出处不同源:%s%s"
                    % (steps, "; ".join(mismatches), coerced_note),
                    "逐值复制本轮 session_facts.json 的三个出处字段(时间加引号);"
                    "不要沿用另一轮/另一组")
        else:
            rep.add("预算仪表·步数", "PASS",
                    "steps_used=%s 与 session_facts.json 及三个出处字段一致%s%s"
                    % (steps, pick_note, coerced_note))

    # ⑨ 大纲预算(WARN)— 复用 outline_check 的求和逻辑
    ol_path = p("outline.md")
    if os.path.exists(ol_path):
        from outline_check import parse_outline_md, parse_words
        ol_meta, nodes = parse_outline_md(ol_path)
        total = parse_words(ol_meta.get("total_words"))
        sigma = outline_total_words(nodes)   # v1.2.1 复核:只加最上一级的节(规格 §④)
        tol = float(ol_meta.get("tolerance") or 0.10)
        if total:
            dev = abs(sigma - total) / total
            if dev > tol:
                rep.add("大纲预算", "WARN", "Σ=%d vs total=%d 偏差 %.0f%%" % (
                    sigma, total, dev * 100), "只提醒不拦(4b)")
            else:
                rep.add("大纲预算", "PASS", "Σ=%d / total=%d 在容差内" % (sigma, total))

    # ⑩ 自述统计一致(v1.0.3 · 2026-08-30)
    #   成品/dossier 自述的「N 张证据卡」必须等于 cards/ 里非 deprecated 的实际张数。
    #   ⚠️ 为什么要单开一项:⑤ 的短整数规则(见 substantive_numbers:「len(tok)<=2 不扫」)
    #   让两位数永远进不了扫描面 —— 实测「21 张证据卡」实际 23,
    #   同一时刻交付对接窗口卡自己写的是 23,而 ⑤ 报「查无 0」。这类**关于文档自身的统计**
    #   不需要「出处」,它需要的是**和机器数出来的实际值一致**。
    live_cards = count_live_cards(cards_src)
    if live_cards is not None:
        dpath = p("dossier.md")
        dmeta = read_md(dpath)[0] if os.path.exists(dpath) else None
        claimed = declared_card_counts(product_texts, dmeta)
        bad = [(n, v, raw) for n, v, raw in claimed if v != live_cards]
        if bad:
            for n, v, raw in bad[:8]:
                rep.add("自述统计一致", "FAIL",
                        "%s 自述 %r,实际 cards/ 有 %d 张(非 deprecated)" % (n, raw, live_cards),
                        "改正本重渲染;补卡后必须回写 dossier.dashboard 与成品统计")
        elif claimed:
            rep.add("自述统计一致", "PASS",
                    "%d 处自述卡数均 = %d" % (len(claimed), live_cards))
        else:
            rep.add("自述统计一致", "PASS", "未发现自述卡数(渲染器元信息条为机器取值)")

    # ⑪ 窗口后证据变更(WARN · 口径为「标记 + 提示」,不强制失效印章)
    #   判据:cards/ 里有文件的 mtime 晚于 dossier 印章时间 → 证据基在对接窗口2 之后动过。
    #   ⚠️ 不判 FAIL 是有意的:强制失效会把「补一张卡」的代价抬成一整个窗口,
    #   而实测中 agent 会因为怕重过窗口而隐瞒(它自己写下 "must invalidate + re-gate2. Ouch."
    #   然后没告诉用户)。这里只要求它在交付的三点「改动内容」里写明(v1.2.0 前叫承诺①),
    #   把判断权留给人。
    dpath = p("dossier.md")
    if os.path.exists(dpath) and os.path.isdir(p("cards")):
        dmeta, _ = read_md(dpath)
        # approval 不是映射(v1.1.1)= 没有盖章时刻;⑥ 已经为它报了 FAIL。
        dap = dmeta.get("approval")
        ap_at = dap.get("approved_at") if isinstance(dap, dict) else None
        stamped = _parse_ts(ap_at)
        if stamped is not None:
            later = []
            for fn in sorted(os.listdir(p("cards"))):
                if not fn.endswith(".md"):
                    continue
                mt = os.path.getmtime(p("cards", fn))
                if mt > stamped + 60:  # 60s 宽限:盖章同一分钟内的收尾写入不算
                    later.append((fn[:-3], mt - stamped))
            if later:
                names = ", ".join("%s(+%.0f 分钟)" % (n, d / 60) for n, d in later[:6])
                rep.add("窗口后证据变更", "WARN",
                        "%d 张卡晚于 dossier 盖章:%s" % (len(later), names),
                        "在交付的三点「改动内容」(library/delivery_commitments.md 第 1 条)里写明"
                        "资料汇编确认之后又动过资料卡片;要不要重新确认资料汇编由用户定")
            else:
                rep.add("窗口后证据变更", "PASS", "证据基在对接窗口2 盖章后未变动")

    # ⑫ 双格式各自机器核(2026-09-03)
    #   ④「跨格式相等」只比链接集合,看不见渲染退化:实测 DOCX 真表格 0、
    #   残留竖线 233、`**` 18,而 HTML 那边 6 张表都是好的,交付对接窗口 报告写的「表格
    #   6 张」是 HTML 的数 —— 一个格式的数被当成了两个格式的数。
    #   ⭐ 判据不是「两个格式互相相等」,是「各自与正本源相等」:两边同样错的
    #   时候互相比较是通过的,而那正是渲染器共用一段坏代码时的形态。
    #   源计数用共享口径 count_tables —— 两个渲染器认表也用它,只有一份判定。
    if drafts and (outs_html or outs_docx):
        # ⚠️ 成品与稿件**按同名配对**,不是把 drafts/ 全加起来:实测那次的
        # drafts/ 里除整稿外还有 part1/2a/2b/3 四个中间片,加总得 12 而实际
        # 是 6。我自己在修 docx 时先渲了 part1.md、差点报成没修好 —— 同一个
        # 坑,「量的对象对不对」比「怎么量」先出错。
        src_by_name = {}
        for d in drafts:
            stem = os.path.splitext(os.path.basename(d))[0]
            src_by_name[stem] = count_tables(read_md(d)[1])
        seen = []
        for path, n_tables, text in (
                [(h, count_tables_html(h), html_text(h)) for h in outs_html]
                + [(d, count_tables_docx(d), docx_text(d)) for d in outs_docx]):
            name = os.path.basename(path)
            stem = os.path.splitext(name)[0]
            fmt = "html" if name.lower().endswith(".html") else "docx"
            res = markdown_residue(text)
            src_tables = src_by_name.get(stem)
            if src_tables is None:
                # 成品找不到同名稿件:E1 要求双格式由同一正本渲染,连不上正本
                # 的成品无法核 —— 报出来而不是跳过。
                rep.add("双格式各自核", "FAIL",
                        "%s(%s):找不到同名稿件,无法与正本源对数" % (name, fmt),
                        "成品文件名要与 drafts/ 下的稿件同名;中间片不该留在 out/")
                continue
            seen.append((name, fmt, n_tables, src_tables, res))
            # ⚠️ 每个数字都标明量自哪个格式:报告里「表格 6 张」不标格式,就是
            # 实测那个错的形状 —— 读的人无法知道另一个格式是几张。
            if n_tables != src_tables:
                rep.add("双格式各自核", "FAIL",
                        "%s(%s):真表格 %d,同名正本源 %d" % (name, fmt, n_tables, src_tables),
                        "重渲该格式;两个渲染器认表口径在 pipeline_lib.split_table,别各写一份")
            bad = ["%s=%d" % (k, v) for k, v in sorted(res.items()) if v]
            if bad:
                rep.add("双格式各自核", "FAIL",
                        "%s(%s):markdown 残留 %s" % (name, fmt, ", ".join(bad)),
                        "残留是「渲染没做完」的可见证据:分隔行/粗体/标题标记不该进成品")
        clean = [1 for _, _, n, src, r in seen if n != src or any(r.values())]
        if seen and not clean:
            rep.add("双格式各自核", "PASS",
                    ";".join("%s(%s) %d 张(源 %d)、残留 0" % (n, f, t, s)
                             for n, f, t, s, _ in seen))


def _parse_ts(v):
    """approved_at → epoch 秒;解析不了返回 None(不因此报错)。"""
    if not v:
        return None
    try:
        from datetime import datetime
        s = str(v).strip().replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def main(argv):
    HELP_FLAGS = ("-h", "--help")
    if any(f in argv for f in HELP_FLAGS):
        print(__doc__)
        return 0
    if "--project" not in argv:
        print(__doc__)
        return 2
    def pop_opt(name):
        if name in argv:
            i = argv.index(name)
            v = argv[i + 1]
            del argv[i:i + 2]
            return v
        return None
    project = pop_opt("--project")
    json_out = pop_opt("--json")
    nc = pop_opt("--numbers-corpus")
    numbers_corpus = nc.split(",") if nc else None
    skip_stamps = "--skip-stamps" in argv
    rep = Report("cite_check")
    check_project(project, rep, numbers_corpus, skip_stamps)
    code = rep.finish(json_out)
    print("交付预告按钮:%s" % ("可弹(全绿)" if code == 0 else "禁止(有 FAIL)"))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
