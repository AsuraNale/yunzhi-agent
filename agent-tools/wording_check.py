# -*- coding: utf-8 -*-
"""wording_check — 用词检查:客户端 wording/src/normalize.mjs 与 scanner.mjs 的 Python 移植(严格模式)。

读 wording/banned-terms.json(硬名单)和 wording/glossary.json(对用户的说法);命中就报,附词表里的说法。
用在:card.py 出卡前扫卡上 agent 写的空和卡上列出的材料文字(三点、判定标准、初步结论的理由、没有资料卡片支撑的节、
项目外文件的名字);progress.py 扫整张进度页的可见文字;tests/ 扫 skills 里给用户说的话和示例卡。

和客户端一样的(每一条都照客户端的代码移植):
- 归一写法(buildView):逐字 NFKC,但保留圈码 ①、上下标、分数、带圈 / 带括号的字(客户端 KEEP 那一组);
  去掉不可见字符 —— Unicode Cf 类全部(零宽字符、软连字符、BOM、方向控制符、U+206A–206F、标签字符 U+E0001 与
  U+E0020–E007F 等)以及 U+034F、U+17B4、U+17B5、U+180B–180D、U+180F、异体选择符 U+FE00–FE0F 与 U+E0100–E01EF;
  空白样的填充字符(U+115F、U+1160、U+2800、U+3164、U+FFA0)读成空格;连字符 U+2010–2013、U+02D7、U+2796 读成 -
  (破折号——不动);繁体按词表的对照表转简体;两个汉字之间的空白去掉(最多一个换行;引用块续行行首的 > 算换行的一部分)。
  归一后的每个字都记得它来自原文哪里:命中按原文报行、列、原文写法。
- 硬名单每一条的边界(ascii-word)、大小写、复数、forms、复合词例外(except)、领域用法例外(except_re)、in_titles;
  一处被两条规则同时命中只报最宽的那条。
- 不扫的:《》里的标题(in_titles 的词照扫,除非整个标题是用户写过的);网址 —— http / https 网址遇到汉字就结束,
  后面的中文照扫;file: 链接和 127.0.0.1 / localhost / [::1] / 0.0.0.0 本机链接整条单独报(env:local-link);
  「」“”""『』里与 > 引用块里(含懒续行)用户写过的话。
- 比客户端多一条(第六轮小修):markdown 链接括号里的目标、<网址> 里的整段不扫英文词(成稿 Word 版的链接里有 docx,
  原来被英文词规则误拦);目标是内部文件的照旧报「内部文件的链接」,是本机地址的照旧报 env:local-link。
  scan 另有两个可选参数给守门脚本扫助手的回复用:link_ok(放行成稿、「查看」网页这类本机链接)和 extra(另加的规则)。
- 「用户写过」的比法:两边都归一、去掉空白(两个英文字母或数字之间留一个空格),整段引文出现在用户某一条话里
  (委托原话,或用户消息记录里的一条,不跨条),两头不切断英文单词,至少两个字;引用块先整块比,比不上再逐行比。

quote_problems(卡片专用,客户端没有这个函数):「」里写成用户原话的要对得上 —— 报告类型卡的 why 每一处都核,其余字段
只核前面(同一行、12 个字以内)紧跟提示词(你说 / 你的原话 / 委托里 …)的那几处;比法同上。

客户端有、这里没有的(卡片和进度页都用不到):宽松模式(提示词后面的引号直接当用户原话,客户端只给自由聊天文字用)、
软名单提醒、allow 名单、domainFromUserText。命中报告不把不可见字符显示成码位。
汉字的判定:这里用 Unicode 的汉字区段(统一汉字、扩展 A–I、兼容汉字、部首、〇 々 〻、苏州码子),客户端用
\\p{Script=Han};两者只在这些区段以外的极少数字上可能不同。
"""
import io
import json
import os
import re
import sys
import unicodedata

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
WORDING = os.path.join(os.path.dirname(HERE), "wording")

# ---- 归一(normalize.mjs) ----

_INVISIBLE_EXTRA = {0x034F, 0x17B4, 0x17B5, 0x180B, 0x180C, 0x180D, 0x180F}
_BLANK = {0x115F, 0x1160, 0x2800, 0x3164, 0xFFA0}
_KEEP = {0x00AA, 0x00B2, 0x00B3, 0x00B9, 0x00BA, 0x2189}
_KEEP_RANGES = ((0x00BC, 0x00BE), (0x2070, 0x209F), (0x2150, 0x215F), (0x2460, 0x24FF), (0x3200, 0x32FF))
_DASH = {0x2010, 0x2011, 0x2012, 0x2013, 0x02D7, 0x2796}
_HAN_CLASS = ("\u2E80-\u2E99\u2E9B-\u2EF3\u2F00-\u2FD5\u3005\u3007\u3021-\u3029\u3038-\u303B"
              "\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFA6D\uFA70-\uFAD9\U00020000-\U0002FA1F\U00030000-\U000323AF")
_HAN_RE = re.compile("[%s]" % _HAN_CLASS)
_LINE_BREAKS = {"\n": 1, "\u2028": 1, "\u2029": 2}
_ALNUM_RE = re.compile(r"[A-Za-z0-9]")


def _invisible(ch):
    cp = ord(ch)
    return (unicodedata.category(ch) == "Cf" or cp in _INVISIBLE_EXTRA
            or 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF)


def _kept(cp):
    return cp in _KEEP or any(a <= cp <= b for a, b in _KEEP_RANGES)


def _is_han(ch):
    return bool(ch) and bool(_HAN_RE.match(ch))


def _is_space(ch):
    return ch.isspace()


def build_view(text, traditional=None):
    """归一后的文字 → (view, frm, to):view 的第 i 个字来自原文 [frm[i], to[i])。"""
    trad = data()["traditional"] if traditional is None else traditional
    chars = []
    for i, ch in enumerate(text):
        if _invisible(ch):
            continue
        cp = ord(ch)
        out = " " if cp in _BLANK else (ch if _kept(cp) else unicodedata.normalize("NFKC", ch))
        for c in out:
            c2 = trad.get(c)
            if c2 is None:
                c2 = "-" if ord(c) in _DASH else c
            chars.append((c2, i, i + 1))
    n = len(chars)
    # 引用块续行行首的 >(上一行也在引用块里):算换行的一部分,跨两行的「对接 / 窗口」照样连起来
    soft = [False] * n
    prev_quoted = False
    i = 0
    while i < n:
        end = i
        while end < n and chars[end][0] != "\n":
            end += 1
        k = i
        while k < end and k - i < 3 and chars[k][0] in " \t":
            k += 1
        markers = -1
        while k < end and chars[k][0] == ">":
            k += 1
            if k < end and chars[k][0] in " \t":
                k += 1
            markers = k
        if markers != -1 and prev_quoted:
            for x in range(i, markers):
                soft[x] = True
        prev_quoted = markers != -1
        i = end + 1

    def blank(idx):
        return soft[idx] or _is_space(chars[idx][0])

    kept = []
    i = 0
    while i < n:
        if not blank(i):
            kept.append(chars[i])
            i += 1
            continue
        j = i
        breaks = 0
        while j < n and blank(j):
            breaks += _LINE_BREAKS.get(chars[j][0], 0)
            j += 1
        before = kept[-1][0] if kept else ""
        after = chars[j][0] if j < n else ""
        if not (breaks < 2 and _is_han(before) and _is_han(after)):
            kept.extend(chars[i:j])
        i = j
    return "".join(c for c, _s, _e in kept), [s for _c, s, _e in kept], [e for _c, _s, e in kept]


def normalize(text):
    """归一后的文字(不带位置)。"""
    return build_view(text)[0]


def flatten(text, traditional=None):
    return re.sub(r"\s+", "", build_view(text, traditional)[0])


def quote_key(text, traditional=None):
    """引文比较用的形式:归一,去掉空白,只在两个英文字母或数字之间留一个空格。"""
    view = build_view(text, traditional)[0]

    def keep_gap(m):
        a, b = m.start(), m.end()
        return " " if (a > 0 and _ALNUM_RE.match(view[a - 1]) and b < len(view) and _ALNUM_RE.match(view[b])) else ""
    return re.sub(r"\s+", keep_gap, view)


def contains_quote(hay, needle):
    """needle 整段出现在 hay 里,而且两头不切断英文单词(中文没有词间空格,只要是子串)。"""
    if not needle:
        return False
    first = bool(_ALNUM_RE.match(needle[0]))
    last = bool(_ALNUM_RE.match(needle[-1]))
    at = hay.find(needle)
    while at >= 0:
        after = at + len(needle)
        if not (first and at > 0 and _ALNUM_RE.match(hay[at - 1])) and \
                not (last and after < len(hay) and _ALNUM_RE.match(hay[after])):
            return True
        at = hay.find(needle, at + 1)
    return False


def user_keys(user_texts):
    """用户写过的话 → 每一条一个比较用的形式(不拼在一起:引文不能跨两条消息)。"""
    trad = data()["traditional"]
    return [quote_key(u, trad) for u in user_texts if isinstance(u, str) and u.strip()]


def verified(snippet, keys):
    if not keys:
        return False
    said = quote_key(snippet, data()["traditional"])
    return len(said.replace(" ", "")) >= 2 and any(contains_quote(k, said) for k in keys)


# ---- 词表 ----

def _load(name):
    with io.open(os.path.join(WORDING, name), encoding="utf-8") as f:
        return json.load(f)


_DATA = {}


def data():
    if not _DATA:
        banned = _load("banned-terms.json")
        _DATA["banned"] = banned
        _DATA["glossary"] = _load("glossary.json")
        _DATA["traditional"] = banned.get("normalize", {}).get("traditional", {})
        _DATA["matchers"] = _compile(banned)
        _DATA["entries"] = {e["id"]: e for e in _DATA["glossary"].get("entries", [])}
    return _DATA


def _flags(spec):
    return re.I if "i" in (spec or "") else 0


_ASCII_BEFORE = r"(?<![A-Za-z0-9_])"
_ASCII_AFTER = r"(?![A-Za-z0-9_])"


def _term_source(term, plurals):
    if "re" in term:
        return term["re"]
    spelled = re.escape(term["t"]).replace("\\ ", "\\s+")
    plural = "(?:[Ee]?[Ss])?" if plurals and re.fullmatch(r"[A-Za-z]+", term["t"]) else ""
    return "|".join([spelled + plural] + [re.escape(f) for f in term.get("forms", [])])


def _compile(banned):
    matchers = []
    order = 0
    for group in banned["hard"]:
        for term in group["terms"]:
            boundary = term.get("boundary", group.get("boundary", "none"))
            flags = term.get("flags", group.get("flags", ""))
            core = _term_source(term, term.get("plurals", group.get("plurals", False)))
            src = _ASCII_BEFORE + "(?:" + core + ")" + _ASCII_AFTER if boundary == "ascii-word" else core
            matchers.append({
                "rule": "%s:%s" % (group["id"], term.get("t", term.get("re"))),
                "re": re.compile(src, _flags(flags)),
                "except_re": [re.compile(x["re"], _flags(x.get("flags", flags))) for x in term.get("except_re", [])],
                "except": term.get("except", []),
                "in_titles": term.get("in_titles", group.get("in_titles", False)),
                "term": term,
                "order": order,
            })
            order += 1
    return matchers


def _suggestion(term, said):
    if term.get("suggest"):
        return term["suggest"]
    entry = data()["entries"].get(term.get("see"))
    if not entry:
        return None
    needles = {w.lower() for w in (said, term.get("t")) if w}
    for t in entry.get("terms", []):
        if t.get("zh") and t.get("action") != "keep" and any(w.lower() in needles for w in t.get("internal", [])):
            return "%s（%s）" % (t["zh"], t["gloss"]) if t.get("gloss") else t["zh"]
    zh = [t["zh"] for t in entry.get("terms", []) if t.get("zh") and t.get("action") != "keep"]
    if zh:
        return " / ".join(zh)
    note = next((t.get("note") for t in entry.get("terms", []) if t.get("note")), None)
    return "不要对用户提（%s）" % note if note else "不要对用户提"


# ---- 不扫的范围(scanner.mjs · buildMask,严格模式) ----

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "0.0.0.0"}
_QUOTE_PAIRS = (("「", "」"), ("“", "”"), ('"', '"'), ("『", "』"))
_URL_STOP = "\\s<>()（）「」『』\"“”'\\]，。；：、！？】》"
_URL_RE = re.compile("(?:https?://[^%s%s]+|file:[^%s]+)" % (_URL_STOP, _HAN_CLASS, _URL_STOP), re.I)
_QUOTE_LINE_RE = re.compile(r"^[ \t]{0,3}>")
_QUOTE_MARKS_RE = re.compile(r"^[ \t]{0,3}(?:>[ \t]?)+")
_NEW_BLOCK_RE = re.compile(r"^[ \t]{0,3}(?:[-*+](?:[ \t]|$)|\d{1,9}[.)](?:[ \t]|$)|#{1,6}(?:[ \t]|$)|```|~~~|(?:[-*_][ \t]*){3,}$)")


def _body_of(line):
    return _QUOTE_MARKS_RE.sub("", line) if _QUOTE_LINE_RE.match(line) else line


# 第五轮 H3:指向本地内部文件的 markdown 链接(10-04 实测:「按[收集资料流程说明](/C:/…/.agents/skills/evidence-card/SKILL.md)要求……」)。
# 说明、脚本、记录这些不给用户看;成稿(out/)、「查看」文件夹里的材料网页、用户自己的材料可以给链接。
_MD_LINK_RE = re.compile(r"\[([^\]\n]*)\]\(\s*<?([^)>\n]+?)>?\s*\)")
_INTERNAL_PARTS = ("/.agents/", "/agent-tools/", "/toolkit/", "/records/", "/.codex/", "/.codebuddy/", "/wording/",
                   "/.yz-tmp/", "/_inbox/", "/upstream/")
_INTERNAL_NAMES = ("skill.md", "agents.md", "progress.md")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]+:")


def internal_link_target(target):
    """markdown 链接的目标是不是本地的内部文件(说明、skill、脚本、记录……)。网址(http / https)不算。"""
    t = (target or "").strip().replace("\\", "/")
    low = t.lower()
    if low.startswith(("http://", "https://", "mailto:")):
        return False
    if _SCHEME_RE.match(t) and not low.startswith("file:") and not re.match(r"^[A-Za-z]:/", t):
        return False                     # 别的协议(不是本地文件)
    probe = "/" + low.split("#", 1)[0].split("?", 1)[0]
    return any(p in probe for p in _INTERNAL_PARTS) or any(probe.endswith("/" + n) for n in _INTERNAL_NAMES)


# 第六轮小修:链接的目标(markdown 链接括号里那一段、<…> 里的网址)整段不扫英文词 —— 成稿 Word 版的链接里有 docx,
# 被英文词规则误拦;网址里有汉字时,客户端「网址遇到汉字就结束」的规则会把后面的 .docx 当成正文扫。
# 目标指向内部文件的,照旧按「内部文件的链接」报(见 scan)。
_AUTOLINK_RE = re.compile(r"<((?:https?|file):[^<>\s]+)>", re.I)


def _build_mask(text, keys, link_ok=None):
    n = len(text)
    mask = bytearray(n)
    plain = bytearray(n)     # 同上但不含标题:in_titles 的词在标题里也算
    local_links = []

    def mark(a, b, title=False):
        for i in range(a, b):
            mask[i] = 1
            if not title:
                plain[i] = 1

    def is_local(url):
        if url.lower().startswith("file:"):
            return True
        hm = re.match(r"^https?://(\[[^\]]*\]|[^/:?#]+)", url, re.I)
        return flatten(hm.group(1) if hm else "", data()["traditional"]).lower().rstrip(".") in _LOCAL_HOSTS

    # 0. 链接的目标:markdown 链接括号里那一段、<网址>(本机链接照旧单独报,除非 link_ok 认它)
    for m in _MD_LINK_RE.finditer(text):
        a, b = m.start(2), m.end(2)
        target = m.group(2).strip()
        if is_local(target) and not (link_ok and link_ok(target)):
            local_links.append((a, b, target))
        for x in range(a, b):
            mask[x] = 1
            plain[x] = 1
    for m in _AUTOLINK_RE.finditer(text):
        a, b = m.start(1), m.end(1)
        if mask[a]:
            continue
        if is_local(m.group(1)) and not (link_ok and link_ok(m.group(1))):
            local_links.append((a, b, m.group(1)))
        for x in range(a, b):
            mask[x] = 1
            plain[x] = 1

    lines = text.split("\n")
    starts, pos = [], 0
    for line in lines:
        starts.append(pos)
        pos += len(line) + 1
    # 1. 引用块(含懒续行):先整块比,比不上再逐行比
    i = 0
    while i < len(lines):
        if not _QUOTE_LINE_RE.match(lines[i]):
            i += 1
            continue
        j = i + 1
        while True:
            if j < len(lines) and _QUOTE_LINE_RE.match(lines[j]):
                j += 1
                continue
            if j < len(lines) and lines[j].strip() and _body_of(lines[j - 1]).strip() and not _NEW_BLOCK_RE.match(lines[j]):
                j += 1
                continue
            break
        if j - i > 1 and verified("\n".join(_body_of(l) for l in lines[i:j]), keys):
            mark(starts[i], starts[j - 1] + len(lines[j - 1]))
        else:
            for k in range(i, j):
                if verified(_body_of(lines[k]), keys):
                    mark(starts[k], starts[k] + len(lines[k]))
        i = j
    # 2. 《标题》
    for m in re.finditer(r"《[^《》\n]*》", text):
        mark(m.start(), m.end(), title=not verified(m.group(0)[1:-1], keys))
    # 3. 网址;本机链接单独报
    trad = data()["traditional"]
    for m in _URL_RE.finditer(text):
        url = m.group(0)
        if all(mask[m.start():m.end()]):
            continue                      # 已经是链接目标的一部分(上面第 0 步)
        if url.lower().startswith("file:"):
            host = "file:"
        else:
            hm = re.match(r"^https?://(\[[^\]]*\]|[^/:?#]+)", url, re.I)
            host = flatten(hm.group(1) if hm else "", trad).lower().rstrip(".")
        if host == "file:" or host in _LOCAL_HOSTS:
            if not (link_ok and link_ok(url)):
                local_links.append((m.start(), m.end(), url))
            for x in range(m.start(), m.end()):
                mask[x] = 1
                plain[x] = 1
            continue
        mark(m.start(), m.end())
    # 4. 引号里的用户原话
    for op, cl in _QUOTE_PAIRS:
        for m in re.finditer(re.escape(op) + "([^" + re.escape(op + cl) + "\\n]*)" + re.escape(cl), text):
            if mask[m.start()]:
                continue
            if verified(m.group(1), keys):
                mark(m.start(), m.end())
    return mask, plain, local_links


def _inside_compound(view, a, b, compounds):
    for compound in compounds:
        start = max(0, a - len(compound))
        while True:
            at = view.find(compound, start)
            if at < 0 or at > a:
                break
            if at <= a and at + len(compound) >= b:
                return compound
            start = at + 1
    return None


def _position(text, index):
    before = text[:index]
    return before.count("\n") + 1, index - (before.rfind("\n") + 1) + 1


def _context(text, start, end, radius=12):
    s = text[max(0, start - radius):start] + "【" + text[start:end] + "】" + text[end:end + radius]
    return re.sub(r"\s+", " ", s)


def extra_rule(rule, pattern, suggestion, except_words=(), flags=0, order=10000):
    """给 scan 的 extra 用的一条规则(和硬名单同一个样子:归一后的文字上匹配,跳过引文、标题、网址)。"""
    return {"rule": rule, "re": re.compile(pattern, flags), "except_re": [], "except": list(except_words),
            "in_titles": False, "term": {"suggest": suggestion}, "order": order}


def scan(text, user_texts=(), field="", link_ok=None, extra=()):
    """扫一段给用户看的文字 → 命中列表 [{field, text, normalized, rule, suggestion, context, line, column, start, end}];
    空列表 = 通过。user_texts:用户写过的话(委托原话、用户消息),引号和引用块里对得上的不扫。
    link_ok(目标):本机链接(file: 地址、127.0.0.1 这类)它认的就不报 —— 守门脚本扫助手的回复时,成稿和「查看」里的网页可以给链接;
    卡片不传它,本机链接照旧一律报。extra:另外几条规则(extra_rule 造的),和硬名单一样匹配、一样跳过引文和标题。"""
    if not isinstance(text, str) or not text:
        return []
    d = data()
    keys = user_keys(user_texts)
    mask, plain, local_links = _build_mask(text, keys, link_ok)
    view, frm, to = build_view(text, d["traditional"])
    raw = []
    for a, b, url in local_links:
        raw.append((a, b, -1, url, url, "env:local-link", "不给本机链接：只说材料的名字，要看就在右侧打开"))
    for m in _MD_LINK_RE.finditer(text):
        if internal_link_target(m.group(2)):
            raw.append((m.start(), m.end(), -2, m.group(0), m.group(0), "env:internal-link",
                        "不贴内部文件的链接（说明、脚本、记录不给用户看）：停下、出卡的理由用研究上的话说，提材料就说它的名字"))

    def match(mt):
        domain = None
        for m in mt["re"].finditer(view):
            a, b = m.start(), m.end()
            if b == a:
                continue
            s, e = frm[a], to[b - 1]
            if all(plain[s:e]):
                continue
            if all(mask[s:e]) and not mt["in_titles"]:
                continue
            if _inside_compound(view, a, b, mt["except"]):
                continue
            if domain is None:
                domain = [(x.start(), x.end()) for r in mt["except_re"] for x in r.finditer(view)]
            if any(s0 <= a and b <= e0 for s0, e0 in domain):
                continue
            raw.append((s, e, mt["order"], text[s:e], m.group(0), mt["rule"], _suggestion(mt["term"], m.group(0))))
    for mt in d["matchers"]:
        match(mt)
    for mt in extra:
        match(mt)
    raw.sort(key=lambda r: (r[0], -(r[1] - r[0]), r[2]))
    kept = []
    for r in raw:
        if any(k[0] <= r[0] and r[1] <= k[1] for k in kept):
            continue
        kept.append(r)
    hits = []
    for s, e, _order, said, normalized, rule, suggestion in kept:
        line, column = _position(text, s)
        hits.append({"field": field, "text": said, "normalized": normalized, "rule": rule,
                     "suggestion": suggestion or "换成研究员的说法", "context": _context(text, s, e),
                     "line": line, "column": column, "start": s, "end": e})
    return hits


def describe(hits):
    """命中 → 给 agent 的一句改法(多条用;分隔)。"""
    parts = []
    for h in hits:
        where = ("%s 里" % h["field"]) if h.get("field") else ""
        variant = "（按「%s」认出）" % h["normalized"] if h.get("normalized") and h["normalized"] != h["text"] else ""
        parts.append("%s「%s」%s是用户读不懂的内部用词，换成：%s" % (where, h["text"], variant, h["suggestion"]))
    return "；".join(parts)


QUOTE_RE = re.compile(r"「([^「」\n]*)」")


def quote_problems(text, user_texts, field, require=False):
    """「」里写成用户原话的,要对得上用户写过的话(比法见模块说明)。
    require=True(报告类型卡的 why):至少要有一处「」引用,而且每一处都要对得上;
    其余字段:只核同一行里、前面 12 个字以内紧跟提示词(你说 / 你的原话 / 委托里 …)的那几处。"""
    cues = data()["banned"].get("quote_cues", {})
    words = cues.get("words", [])
    not_cues = cues.get("not_cues", [])
    window = cues.get("window", 12)
    keys = user_keys(user_texts)
    text = text or ""
    found = list(QUOTE_RE.finditer(text))
    out = []
    if require and not found:
        out.append("%s 里要用「」逐字引用户的一句原话（委托原话或用户发过的消息），前面写「你说」" % field)
    for m in found:
        line_start = text.rfind("\n", 0, m.start()) + 1
        before = text[max(line_start, m.start() - window):m.start()]
        for nc in not_cues:
            before = before.replace(nc, " " * len(nc))
        cued = any(w in before for w in words)
        if not (require or cued):
            continue
        if not verified(m.group(1), keys):
            out.append("%s 里「%s」不是用户的原话：委托原话和用户消息记录里找不到这一整句（逐字、在同一条话里）。"
                       "要么改成用户一字不差的原话，要么去掉引号改成你自己的话" % (field, m.group(1)))
    return out


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="用词检查:扫一段给用户看的文字")
    ap.add_argument("--text")
    ap.add_argument("--file")
    args = ap.parse_args(argv[1:])
    text = args.text
    if args.file:
        with io.open(args.file, encoding="utf-8") as f:
            text = f.read()
    hits = scan(text or "")
    sys.stdout.write(json.dumps({"ok": not hits, "hits": hits}, ensure_ascii=False, indent=1) + "\n")
    return 0 if not hits else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
