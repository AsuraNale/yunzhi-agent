# -*- coding: utf-8 -*-
"""五份 skill 与 AGENTS.md 的合同:出口契约表的结构,以及 v1.2.0 起的卡片接口。

一、出口契约表的结构不变量。存在的理由:cite-trace 的 E1/E4 曾把 ⭐/⚠ 注释块直接贴进单元格,
行内跨了 34 行 —— markdown 的表格行必须在一行内,于是表在那里断掉,「怎么验」格任何解析器
都读不到(面板 exits.ts 只能拿到 2 段)。文件看上去是好的,只有按格数一量才现形。
条数 5/5/3/5 原来与 v0.1 面板的 naming.ts(PHASES.exits)、progress-model.ts 同源;v0.2 的应用
不读出口契约,这里仍钉着,免得出口行被悄悄删掉或并掉。

二、卡片接口(界面实装工单 05 §二、§四;规格 §⑦ §⑨,v1.2.0):
- 确认只由人做:skill 与 AGENTS.md 里没有一处让 agent 落确认记录(没有落章那几个参数),
  `--advance` 只推进到 gate1/2/3_awaiting 与 verifying(确认之后的推进由应用做);
- 不再有窗口报告与 http 链接(界面有阅读页);
- 每个 `ann_gate_card` 示例(```json 块)只给 `id` + `kind` + 该 kind 的字段,字数在上限内;
- 源文件里没有看不见的字符。
每条判据都配了一个会失败的输入(各自的「判据会失败」测试),证明它不是永远绿。
"""
import io
import json
import os
import re

NL = chr(10)
PIPELINE = {"task-planner": 5, "evidence-card": 5, "outline-cocreate": 3, "cite-trace": 5}


def skills_dir():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel in ((".dsh", "skills"), ("skills",)):
        p = os.path.join(root, *rel)
        if os.path.isdir(p):
            return p
    raise AssertionError("找不到 skills 目录,root=" + root)


def is_exit_row(line):
    if not line.startswith("|"):
        return False
    cells = line.split("|")
    if len(cells) < 2:
        return False
    first = cells[1].strip()
    return len(first) >= 2 and first[0] == "E" and first[1:].isdigit()


def each_skill():
    """⚠️ 两侧布局不同:本体是 `<name>/SKILL.md`,镜像里 review-module 是平铺的
    `review-module.md`。原来只认前者,于是**镜像上这些测试静默跳过一把 skill**
    —— 少测一个文件和测过了长得一模一样。"""
    d = skills_dir()
    for entry in sorted(os.listdir(d)):
        nested = os.path.join(d, entry, "SKILL.md")
        flat = os.path.join(d, entry)
        if os.path.exists(nested):
            yield entry, io.open(nested, encoding="utf-8").read().split(NL)
        elif os.path.isfile(flat) and entry.endswith(".md"):
            yield entry[:-3], io.open(flat, encoding="utf-8").read().split(NL)


def test_每条出口行都是完整三格():
    bad = []
    for name, lines in each_skill():
        for i, line in enumerate(lines):
            if is_exit_row(line) and line.count("|") != 4:
                bad.append("%s:%d 竖线 %d 条(应 4):%s" % (name, i + 1, line.count("|"), line[:40]))
    assert bad == [], NL.join(bad)


def test_怎么验格不许为空():
    bad = []
    for name, lines in each_skill():
        for i, line in enumerate(lines):
            if is_exit_row(line) and line.count("|") == 4 and not line.split("|")[3].strip():
                bad.append("%s:%d 缺「怎么验」:%s" % (name, i + 1, line[:40]))
    assert bad == [], NL.join(bad)


def test_出口行必须连续_中间不许夹散文():
    """真正抓住 cite-trace 那次事故的一条:注释块夹在 E4 与 E5 之间。"""
    bad = []
    for name, lines in each_skill():
        idx = [i for i, l in enumerate(lines) if is_exit_row(l)]
        if not idx:
            continue
        for i in range(idx[0], idx[-1] + 1):
            if not is_exit_row(lines[i]):
                bad.append("%s:%d 夹在出口行之间:%s" % (name, i + 1, lines[i].strip()[:56]))
    assert bad == [], NL.join(bad)


def test_四把管线技能的出口条数是_5_5_3_5():
    """与面板 naming.ts 的 PHASE_EXITS 同源;改这里必须同改面板。"""
    got = {}
    for name, lines in each_skill():
        if name in PIPELINE:
            got[name] = len([l for l in lines if is_exit_row(l)])
    assert got == PIPELINE, "实得 %s,应为 %s" % (got, PIPELINE)


def test_四把管线技能各有且仅有一个可解析的出口契约标题():
    """面板只从 `## 出口契约` 开始解析；标题坏掉会静默得到 0 条。

    `## 出口契约附注` 也命中面板的宽前缀，所以不能只搜包含词；主标题须
    保持当前可解析形态 `## 出口契约(...)`，且每把管线 skill 恰有一个。
    """
    got = {}
    for name, lines in each_skill():
        if name not in PIPELINE:
            continue
        got[name] = [i + 1 for i, line in enumerate(lines)
                     if line.startswith("## 出口契约(")]
    bad = {name: rows for name, rows in got.items() if len(rows) != 1}
    assert got.keys() == PIPELINE.keys(), "缺管线 skill:实得 %s" % sorted(got)
    assert bad == {}, "出口契约主标题须各恰有一个:%s" % bad


def test_闭合竖线之后不得有文本():
    """evidence-card E4 曾把 352 字粘在闭合竖线**之后**:竖线仍是 4 条,
    上面三条测试全绿,而 exits.ts 只取 cells[1..3] —— 整段面板不可达,
    文件里却看着像写了。"""
    bad = []
    for name, lines in each_skill():
        for i, line in enumerate(lines):
            if is_exit_row(line) and not line.rstrip().endswith("|"):
                after = line.rstrip().rsplit("|", 1)[-1].strip()
                bad.append("%s:%d 闭合竖线后还有 %d 字:%s" % (name, i + 1, len(after), after[:40]))
    assert bad == [], NL.join(bad)


def test_契约节内最后一条出口行之后不得有散文():
    """exits.ts 的 close() 把「## 出口契约」节内所有非行首行并进**上一条**。
    cite-trace 的注释块曾直接跟在表下,于是 E5 的 condition 涨到 3606 字
    (HEAD 时 290),面板的 E5 会拖着整块窗口卡形态。附注要挂在自己的
    `## ` 标题下 —— `### ` 不行:节的终止判据是 startsWith('## ')。"""
    bad = []
    for name, lines in each_skill():
        idx = [i for i, l in enumerate(lines) if is_exit_row(l)]
        if not idx:
            continue
        start = next((i for i, l in enumerate(lines) if l.startswith("## ") and "出口契约" in l), None)
        if start is None:
            continue
        end = len(lines)
        for i in range(start + 1, len(lines)):
            if lines[i].startswith("## "):
                end = i
                break
        for i in range(idx[-1] + 1, end):
            if lines[i].strip():
                bad.append("%s:%d 会被并进 %s 的 condition:%s"
                           % (name, i + 1, lines[idx[-1]].split("|")[1].strip(), lines[i].strip()[:46]))
    assert bad == [], NL.join(bad)


# ── 卡片接口(v1.2.0 · 界面实装工单 05 §二、§四;规格 §⑦ §⑨)──────────────────────────────
# 五份 skill 里由 wording/ 生成的那一节是词表(左栏是内部叫法,「窗口报告」「127.0.0.1」就写在
# 「不要对用户提」里),不是要 agent 去做的事 —— 下面查「做什么」的判据只看那一节以外的文字;
# 那一节另有一条:里面不许出现命令与链接。
WORDING_BEGIN = "<!-- wording:begin"
WORDING_END = "<!-- wording:end -->"


def split_wording(text):
    """→ (wording 那一节以外的文字, 那一节的文字)。没有那一节 → (全文, "")。"""
    i = text.find(WORDING_BEGIN)
    j = text.find(WORDING_END)
    if i < 0 or j < i:
        return text, ""
    return text[:i] + text[j + len(WORDING_END):], text[i:j + len(WORDING_END)]


def toolkit_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def instruction_texts():
    """→ [(名字, 要 agent 照做的文字)]:五份 skill(去掉 wording 生成的那一节)与 AGENTS.md。"""
    out = [(name, split_wording(NL.join(lines))[0]) for name, lines in each_skill()]
    with io.open(os.path.join(toolkit_root(), "AGENTS.md"), encoding="utf-8") as f:
        out.append(("AGENTS.md", f.read()))
    return out


# 落确认记录的参数:确认记录与交付记录只由应用写(带签名),agent 一处都不写。
APPROVE_FLAGS = ("--approve", "--replace-signed", "--signature")
# agent 只推进到这几个状态:弹确认卡之前的 gateN_awaiting,开始交付前检查时的 verifying。
# 确认之后的那一步(collecting / outlining / drafting / delivered,以及 gateN_approved)由应用推进。
ALLOWED_ADVANCE = ("gate1_awaiting", "gate2_awaiting", "gate3_awaiting", "verifying")
ADVANCE_RE = re.compile(r"--advance[\s`*\"']+([A-Za-z0-9_<>]+)")
# 窗口报告、报告服务、提纲树网页:v0.2 由界面的阅读页代替。
GATE_REPORT_WORDS = ("窗口报告", "gate-report", "report_server", "report-server", "outline_tree",
                     "render_outline")
# http 链接与本机地址:界面不再收 agent 贴的链接。
LINK_WORDS = ("http://", "https://", "127.0.0.1", "localhost", ":8130")


def flow_violations(text):
    """一段要 agent 照做的文字里,v1.2.0 不再允许的东西 → [说明]。"""
    out = []
    for flag in APPROVE_FLAGS:
        if flag in text:
            out.append("落确认记录的参数 %s(确认记录只由应用写)" % flag)
    for target in ADVANCE_RE.findall(text):
        if target not in ALLOWED_ADVANCE:
            out.append("--advance %s(确认之后的推进由应用做;agent 只推进到 %s)"
                       % (target, " / ".join(ALLOWED_ADVANCE)))
    for word in GATE_REPORT_WORDS:
        if word in text:
            out.append("窗口报告一类的东西:%s" % word)
    for word in LINK_WORDS:
        if word in text:
            out.append("链接 / 本机地址:%s" % word)
    # v1.2.1 复核(L1)加了下面三种;再复核(N3)改成按结构认 —— 字面一出现就报,会把正当的说明也报出来
    # (「不复述三点」「approved_hash 与当前内容对不上」「应用写下 approval.status: approved」)。现在只报:
    # 围栏代码块里的、行内代码里是命令的、句子里让 agent 去写的(写 / 改成 / 填… 紧挨在前头,或「把 … 写上」);
    # 否定的(不 / 别 / 不用 / 不要…)与说应用做什么的(动词前头紧挨着「应用」)不报。
    # ① 让 agent 在对话里把三点再念一遍(卡上有三点,不用复述);
    for m in RESTATE_RE.finditer(text):
        if not negated(text[clause_start(text, m.start()):m.start()]):
            out.append("在对话里复述三点一类的要求:%s" % m.group())
    # ② 让 agent 手写确认记录或手写进度状态(只有 planning 是 agent 自己写进新任务计划的);
    for m in PIPELINE_STATUS_RE.finditer(text):
        if m.group(1) != "planning" and instructed(text, m.start(), m.end()):
            out.append("手写 pipeline_status: %s(推进只用 --advance,确认之后的由应用做)" % m.group(1))
    for m in APPROVED_RE.finditer(text):
        if instructed(text, m.start(), m.end()):
            out.append("手写确认记录(status: approved)")
    for m in RECORD_KEY_RE.finditer(text):
        if instructed(text, m.start(), m.end()):
            out.append("手写确认记录的字段 %s" % m.group())
    # ③ --advance 后面不紧跟状态名、而是隔了标点再写状态(「--advance，状态写 collecting」):这时从 --advance 起
    #    到这一句结束(。;;→ 表格竖线 换行)之间的状态名都算推进目标。紧跟着状态名的(`--advance gate2_awaiting`)
    #    目标就是它,上面已经核过;同一句后面的话是说明(「这时状态是 collecting」),不再往后找。
    for segment in advance_segments(text):
        for m in STATE_WORDS_RE.finditer(segment):
            head = segment[clause_start(segment, m.start()):m.start()]
            if m.group(1) not in ALLOWED_ADVANCE and "应用" not in head and not negated(head):
                out.append("--advance 那一句里写着 %s" % m.group(1))
    return out


RESTATE_RE = re.compile(r"(?:逐字)?复述(?:一遍|一下)?(?:判断|承诺)?三[行点]|逐字复述|(?:判断|承诺)三行")
PIPELINE_STATUS_RE = re.compile(r"pipeline_status[`\s]*[:：=][`\s]*([a-z0-9_]+)")
APPROVED_RE = re.compile(r"(?:approval\.)?status[`\s]*[:：][`\s]*approved")
RECORD_KEY_RE = re.compile(r"approved_hash|approved_by|approved_at|approval_quote")
STATE_WORDS_RE = re.compile(r"(?<![a-z0-9_])(planning|gate[123]_awaiting|gate[123]_approved|collecting|outlining|drafting|verifying|delivered)(?![a-z0-9_])")
SENTENCE_END = ("。", "；", ";", "→", "|", NL)
# 一小句的边界:否定词、「应用」、写的动词都只在同一小句里算(「别贴路径,在对话里复述三点」的否定管不到后半句)。
CLAUSE_MARKS = ("。", "；", ";", "，", ",", "！", "？", "（", "(", "）", ")", NL)
# 否定:强的(不用 / 不要 / 别 …)在这一小句里前头哪里都算(「不要在 frontmatter 里写」);单个「不」要紧挨着(「不复述」「不再写」)。
NEGATION_STRONG_RE = re.compile(r"不用|不要|不必|无需|不需要|用不着|(?<![区类特分个级性识告差辨鉴])别(?![的人处])|不许|不能|不该|勿")
NEGATION_NEAR_RE = re.compile(r"不(?:再|去|要|用|必)?[\s`*]*$")
WRITE_VERB = r"(?:写上|写下|写进|写入|写成|写|改成|改为|改|设为|设成|填上|填成|填|置为|标为|标成|记成|记为|记上|加上)"
WRITE_BEFORE_RE = re.compile(WRITE_VERB + r"[\s`*\"'：:]*$")
WRITE_AFTER_RE = re.compile(r"^[\s`*\"']*" + WRITE_VERB)
APP_SUBJECT_RE = re.compile(r"应用(?:会|就|再|才|自己|来|负责|随后|然后|先)?[\s`*]*$")
FENCE_RE = re.compile(r"^[ \t]*```[^\n]*\n.*?^[ \t]*```[ \t]*$", re.M | re.S)
CODE_SPAN_RE = re.compile(r"`[^`\n]+`")
COMMAND_RE = re.compile(r"\.py\b|\.mjs\b|(?:^|[\s`])--[a-z]")


def clause_start(text, at):
    """text[at] 所在那一小句的开头。"""
    return max(text.rfind(mark, 0, at) for mark in CLAUSE_MARKS) + 1


def clause_end(text, at):
    ends = [i for i in (text.find(mark, at) for mark in CLAUSE_MARKS) if i >= 0]
    return min(ends) if ends else len(text)


def negated(head):
    """同一小句里某件事前头的那段文字 → 是不是否定的(「不复述三点」「别写」「不要在 frontmatter 里写」)。"""
    return bool(NEGATION_STRONG_RE.search(head) or NEGATION_NEAR_RE.search(head))


def instructed(text, start, end):
    """text[start:end](手写的进度状态或确认记录)是不是在让 agent 去写 → bool。按结构认:
    围栏代码块里的(YAML / JSON 片段、命令)算;行内代码里是命令的(带 .py / .mjs 或 --参数)算;
    句子里写的动词紧挨在它前头(「写上 `approved_hash: …`」),或「把 … 写上」的算 —— 动词(或「把」)
    前头是否定的不算,紧挨着「应用」的(说应用做什么:「应用写下 …」)不算。其余(条件句、说明)不算。"""
    if any(m.start() <= start < m.end() for m in FENCE_RE.finditer(text)):
        return True
    if any(m.start() <= start < m.end() and COMMAND_RE.search(m.group()) for m in CODE_SPAN_RE.finditer(text)):
        return True
    head = text[clause_start(text, start):start]
    verb = WRITE_BEFORE_RE.search(head)
    if verb:
        before = head[:verb.start()]
    else:
        ba = head.rfind("把")
        if ba < 0 or not WRITE_AFTER_RE.match(text[end:clause_end(text, end)]):
            return False
        before = head[:ba]
    return not (negated(before) or APP_SUBJECT_RE.search(before))


def advance_segments(text):
    """每个后面不紧跟状态名的 --advance 到这一句结束的那一段(不含 --advance 本身)。"""
    out = []
    for m in re.finditer(r"--advance", text):
        if ADVANCE_RE.match(text, m.start()):
            continue
        ends = [i for i in (text.find(mark, m.end()) for mark in SENTENCE_END) if i >= 0]
        out.append(text[m.end():min(ends) if ends else len(text)])
    return out


def test_没有一处让_agent_落章或做确认之后的推进_也没有窗口报告与链接():
    """验收 A3-1。"""
    bad = []
    for name, text in instruction_texts():
        bad += ["%s: %s" % (name, v) for v in flow_violations(text)]
    assert bad == [], NL.join(bad)


def test_允许的推进写法确实认得出():
    """上一条的对照:判据要认得出允许的推进。四个允许的状态在 skill 里都写着 —— 写法变了、正则
    认不出来时,上一条会因为「一条都没找到」而照样绿,这一条就红。"""
    found = set()
    for _, text in instruction_texts():
        found.update(ADVANCE_RE.findall(text))
    assert found == set(ALLOWED_ADVANCE), "skill 里认出的推进目标:%s" % sorted(found)


def test_wording_生成的那一节里没有命令和链接():
    """那一节的词表左栏会写脚本名(`stamp.py` 是要换成「自动检查」的内部叫法),但不许写命令参数与链接。"""
    bad = []
    for name, lines in each_skill():
        section = split_wording(NL.join(lines))[1]
        assert section, "%s 没有 wording 生成的那一节" % name
        for word in APPROVE_FLAGS + ("--advance", "http://", "https://", "逐字复述", "复述三点"):
            if word in section:
                bad.append("%s 的「对用户说话」一节里有 %s" % (name, word))
    assert bad == [], NL.join(bad)


def test_落章与推进的判据会失败():
    """每一类违规各放回一处,判据都得报出来;干净的文字一条都不报。"""
    cases = {
        "stamp.py <project>/task_plan.md --approve --by 研究员 --quote 好": "--approve",
        "stamp.py <project>/dossier.md --replace-signed --why 人工": "--replace-signed",
        "然后 `stamp.py <project>/task_plan.md --advance collecting`": "--advance collecting",
        "然后 --advance outlining": "--advance outlining",
        "确认后 `--advance drafting`": "--advance drafting",
        "交付后 --advance delivered": "--advance delivered",
        "--advance gate2_approved": "--advance gate2_approved",
        "先生成窗口报告再弹卡": "窗口报告",
        "生成 out/gate-reports/gate1.html": "gate-report",
        "起报告服务 report_server.py": "report_server",
        "链接 = http://127.0.0.1:8130/ + 路径": "http://",
        "打开 https://example.com/x": "https://",
        "发一条普通消息,逐字复述判断三行": "逐字复述",
        "把 frontmatter 的 approval.status: approved 写上": "status: approved",
        "然后把 pipeline_status: collecting 写进任务计划": "pipeline_status: collecting",
        "跑 --advance,状态写 collecting": "--advance 那一句里写着 collecting",
        "跑 --advance，状态写 drafting": "--advance 那一句里写着 drafting",
        # 再复核(N3):按结构认之后,这几种写法照样报
        "在 frontmatter 里写上 `approved_hash: <hash>`": "approved_hash",
        "```yaml" + NL + "pipeline_status: drafting" + NL + "```": "pipeline_status: drafting",
        "`stamp.py <project>/task_plan.md --set pipeline_status=outlining`": "pipeline_status: outlining",
        "别贴路径,在对话里复述三点": "复述三点",
        "应用弹卡之后,把 approval.status: approved 写上": "status: approved",
    }
    for text, word in cases.items():
        found = flow_violations(text)
        assert any(word in v for v in found), "没报出 %s:%s → %s" % (word, text, found)
    clean = ("`stamp.py <project>/task_plan.md --advance gate1_awaiting` → 调 `ann_gate_card`;"
             "开始检查前 `--advance verifying`。新任务计划写 `pipeline_status: planning`;"
             "`--advance gate2_awaiting` → 用户点确认,应用推进到 `outlining`。")
    assert flow_violations(clean) == []


# 再复核(N3):这五句是正当的说明 —— 否定的、说应用做什么的、--advance 紧跟目标之后的说明、条件句 —— 一条都不许报。
LEGIT_PROSE = ("不复述三点（卡上有）",
               "approved_hash 与当前内容对不上…",
               "`--advance gate2_awaiting`（这时状态是 collecting）",
               "应用写下 `approval.status: approved`",
               "只在 `pipeline_status: collecting` … 时用")


def test_正当的说明不报():
    bad = ["%s → %s" % (text, flow_violations(text)) for text in LEGIT_PROSE if flow_violations(text)]
    assert bad == [], NL.join(bad)


# A2 联调:应用装在带空格的路径下(Program Files)时,没加引号的路径会把命令拆成几段。skills 与 AGENTS.md 规定的
# 每条命令(行内代码,或围栏代码块里的一行,带 .py / .mjs 脚本)里,带 <toolkit> / <project>(以及 <那份材料>)的
# 参数一律加双引号 —— pwsh、cmd、bash 都认双引号。
PATH_PLACEHOLDERS = ("<toolkit>", "<project>", "<那份材料>")
SCRIPT_FILE_RE = re.compile(r"\.(?:py|mjs)\b")


def command_snippets(text):
    """一段文字里的命令:围栏代码块里的每一行、围栏以外的行内代码,只留带 .py / .mjs 的。"""
    out = [line for m in FENCE_RE.finditer(text) for line in m.group().split(NL)[1:-1]]
    out += [m.group()[1:-1] for m in CODE_SPAN_RE.finditer(FENCE_RE.sub("", text))]
    return [c for c in out if SCRIPT_FILE_RE.search(c)]


def path_args(command):
    return [tok for tok in command.split() if any(ph in tok for ph in PATH_PLACEHOLDERS)]


def unquoted_paths(command):
    """一条命令里没加双引号的路径参数。"""
    return [tok for tok in path_args(command) if not (len(tok) > 1 and tok[0] == tok[-1] == '"')]


def test_命令里的路径参数都加了引号():
    bad, seen = [], 0
    for name, text in instruction_texts():
        for cmd in command_snippets(text):
            seen += len(path_args(cmd))
            bad += ["%s: %s(在 %s 里)" % (name, tok, cmd[:70]) for tok in unquoted_paths(cmd)]
    assert seen >= 60, "只认出 %d 个路径参数:命令的认法坏了,这条会照样绿" % seen
    assert bad == [], NL.join(bad)


def test_引号的判据会失败_落章与推进的判据不被引号绊住():
    assert unquoted_paths('python -X utf8 <toolkit>/scripts/stamp.py "<project>/task_plan.md"') == ["<toolkit>/scripts/stamp.py"]
    assert unquoted_paths("cite_check.py --project <project>") == ["<project>"]
    assert unquoted_paths('stamp.py <那份材料> --invalidate --why "<为什么>"') == ["<那份材料>"]
    assert unquoted_paths('python -X utf8 "<toolkit>/scripts/review_result.py" "<project>" --files <n>') == []
    assert command_snippets("跑 `python -X utf8 <toolkit>/scripts/x.py <project>`,写 `<project>/task_plan.md`。") \
        == ["python -X utf8 <toolkit>/scripts/x.py <project>"]
    fenced = NL.join(["```", "python -X utf8 <toolkit>/scripts/x.py <project>/outline.md", "```"])
    assert command_snippets(fenced) == ["python -X utf8 <toolkit>/scripts/x.py <project>/outline.md"]
    quoted = '`python -X utf8 "<toolkit>/scripts/stamp.py" "<project>/task_plan.md" --advance %s`'
    assert flow_violations(quoted % "gate1_awaiting") == []
    assert flow_violations(quoted % '"gate1_awaiting"') == []
    for target in ("collecting", '"collecting"'):
        found = flow_violations(quoted % target)
        assert any("--advance collecting" in v for v in found), found


# 七种卡(工单 05 §2.2;规格 §⑦):agent 只给 id + kind + 下表的字段,上限是字数(Unicode 码位数)。
CARD_FIELDS = {
    "report_type": {"report_name": 6, "thesis": 40, "why": 80},
    "task_plan": {},
    "dossier": {},
    "outline": {},
    "delivery": {},
    "decision": {"question": 40, "why": 80},
    "flow_change": {"changes": 60},
}
CARD_OPTIONAL = {"report_type": ("recommend",), "decision": ("recommend",)}
OPTION_FIELDS = {"label": 20, "effect": 40}
REPORT_TYPE_RECOMMEND = ("judge", "survey")
# 每把管线 skill 至少要示范的卡。
SKILL_KINDS = {
    "task-planner": {"report_type", "task_plan", "flow_change", "decision"},
    "evidence-card": {"decision", "dossier"},
    "outline-cocreate": {"outline"},
    "cite-trace": {"delivery"},
}
JSON_BLOCK_RE = re.compile(r"```json[ \t]*\n(.*?)\n```", re.S)
QUOTE_RE = re.compile(r"「([^「」]{2,})」")


def card_examples(text):
    """一段文字里的 ```json 块 → [(块的原文, 解析出来的对象;解析不了是 None)]。"""
    out = []
    for block in JSON_BLOCK_RE.findall(text):
        try:
            out.append((block, json.loads(block)))
        except ValueError:
            out.append((block, None))
    return out


def _is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _text_ok(value, cap):
    return isinstance(value, str) and value.strip() != "" and len(value) <= cap


def card_problems(card):
    """一个 `ann_gate_card` 参数对象 → 不合 §2.2 的地方。"""
    if not isinstance(card, dict):
        return ["不是 JSON 对象"]
    kind = card.get("kind")
    if kind not in CARD_FIELDS:
        return ["kind=%r 不是七种之一" % (kind,)]
    out = []
    fields = CARD_FIELDS[kind]
    allowed = {"id", "kind"} | set(fields) | set(CARD_OPTIONAL.get(kind, ()))
    if kind == "decision":
        allowed.add("options")
    extra = sorted(set(card) - allowed)
    if extra:
        out.append("%s 卡多了字段 %s(确认类不收 question / detail / options / approve / header)" % (kind, extra))
    if not (isinstance(card.get("id"), str) and card["id"].strip()):
        out.append("%s 卡缺 id" % kind)
    for key, cap in fields.items():
        if not _text_ok(card.get(key), cap):
            out.append("%s 卡的 %s 缺、空或超过 %d 字" % (kind, key, cap))
    if kind == "report_type":
        if "recommend" in card and card["recommend"] not in REPORT_TYPE_RECOMMEND:
            out.append("report_type 的 recommend 只能是 judge / survey(不推荐就不写这个键)")
        if isinstance(card.get("why"), str) and not QUOTE_RE.search(card["why"]):
            out.append("report_type 的 why 必须用「」引用户原话")
    if kind == "decision":
        options = card.get("options")
        if not (isinstance(options, list) and 2 <= len(options) <= 3):
            out.append("decision 的 options 要 2–3 个")
            options = options if isinstance(options, list) else []
        for i, option in enumerate(options, start=1):
            if not isinstance(option, dict) or set(option) != set(OPTION_FIELDS) | {"changes_plan"}:
                out.append("decision 第 %d 个选项的字段应正好是 label / effect / changes_plan" % i)
                continue
            for key, cap in OPTION_FIELDS.items():
                if not _text_ok(option.get(key), cap):
                    out.append("decision 第 %d 个选项的 %s 缺、空或超过 %d 字" % (i, key, cap))
            if not isinstance(option.get("changes_plan"), bool):
                out.append("decision 第 %d 个选项的 changes_plan 应是真或假" % i)
        if "recommend" in card and not (_is_int(card["recommend"]) and 1 <= card["recommend"] <= len(options)):
            out.append("decision 的 recommend 是从 1 数的选项序号(不推荐就不写这个键)")
    return out


def test_每个卡片示例都按_2_2_的字段写():
    """验收 A3-2:skill 里每个 ```json 块都是一个 ann_gate_card 参数对象,字段与上限照 §2.2。"""
    bad, seen = [], 0
    for name, lines in each_skill():
        for block, card in card_examples(split_wording(NL.join(lines))[0]):
            seen += 1
            if card is None:
                bad.append("%s: 这个 json 块解析不了:%s" % (name, block[:40]))
                continue
            bad += ["%s: %s" % (name, p) for p in card_problems(card)]
    assert seen >= 8, "只找到 %d 个卡片示例 —— 提取的正则坏了?" % seen
    assert bad == [], NL.join(bad)


def test_每把管线_skill_都示范了它要弹的那几种卡():
    got = {}
    for name, lines in each_skill():
        got[name] = {card.get("kind") for _, card in card_examples(NL.join(lines)) if isinstance(card, dict)}
    missing = {name: sorted(kinds - got.get(name, set())) for name, kinds in SKILL_KINDS.items()
               if kinds - got.get(name, set())}
    assert missing == {}, "缺示范:%s" % missing
    shown = set().union(*got.values())
    assert shown == set(CARD_FIELDS), "七种卡没有都示范到:%s" % sorted(shown)


def test_不再写旧的窗口卡形态():
    """v0.1 的卡把材料塞进 detail、靠 approve 指明哪个是批准;v0.2 这些都由应用出。"""
    old = ("窗口卡形态", "`detail`", "`approve`", "`header`", "[on hold]", "[确认]")
    bad = ["%s 还写着 %s" % (name, w) for name, text in instruction_texts() for w in old if w in text]
    assert bad == [], NL.join(bad)


def test_卡片示例的判据会失败():
    """每一类写错的卡都得报出来;照 §2.2 写对的卡一条都不报。"""
    good = {
        "report_type": {"id": "r", "kind": "report_type", "report_name": "内参",
                        "thesis": "下乡活动带动了充电设施增长",
                        "why": "你说「领导就想知道这钱花得值不值」，这需要一个判断。", "recommend": "judge"},
        "task_plan": {"id": "t", "kind": "task_plan"},
        "decision": {"id": "d", "kind": "decision", "question": "增速从哪年开始算？",
                     "why": "2021 年的分县数据查不到。",
                     "options": [{"label": "从 2022 年开始算", "effect": "少一年", "changes_plan": False},
                                 {"label": "改成梳理情况", "effect": "不下判断", "changes_plan": True}],
                     "recommend": 1},
        "flow_change": {"id": "f", "kind": "flow_change", "changes": "去掉拟定提纲这一步。"},
    }
    for card in good.values():
        assert card_problems(card) == [], card_problems(card)

    def broken(base, **change):
        card = json.loads(json.dumps(good[base]))
        for key, value in change.items():
            if value is None:
                card.pop(key)
            else:
                card[key] = value
        return card

    one = [{"label": "只有一个", "effect": "x", "changes_plan": False}]
    bad_cards = [
        broken("task_plan", detail="① 对象:task_plan.md"),          # 确认类带材料
        broken("task_plan", question="可以确认吗?"),
        broken("task_plan", approve="确认"),
        broken("task_plan", header="对接窗口1"),
        broken("task_plan", options=one * 2),
        broken("task_plan", kind="gate1"),                           # 不认识的 kind
        broken("task_plan", id=None),                                # 缺 id
        broken("report_type", report_name="新能源汽车下乡内参"),     # 超过 6 字
        broken("report_type", thesis="长" * 41),
        broken("report_type", why="这需要一个判断。"),                # 没引用户原话
        broken("report_type", recommend="argument"),
        broken("report_type", thesis=None),
        broken("decision", question="问" * 41),
        broken("decision", why=None),
        broken("decision", options=one),                             # 少于 2 个
        broken("decision", options=one * 4),                         # 多于 3 个
        broken("decision", options=[{"label": "超" * 21, "effect": "x", "changes_plan": False}] * 2),
        broken("decision", options=[{"label": "a", "effect": "效" * 41, "changes_plan": False}] * 2),
        broken("decision", options=[{"label": "a", "effect": "b", "changes_plan": "否"}] * 2),
        broken("decision", options=[{"label": "a", "description": "b", "changes_plan": False}] * 2),
        broken("decision", recommend=3),
        broken("decision", recommend=0),
        broken("decision", recommend=True),
        broken("flow_change", changes="改" * 61),
        broken("flow_change", changes=None),
    ]
    for card in bad_cards:
        assert card_problems(card), "没报出来:%s" % card
    # 提取:写坏的 json 块要报「解析不了」,不能被静默跳过
    assert card_examples("```json" + NL + '{"kind": "task_plan",}' + NL + "```")[0][1] is None


# 看不见的字符:编辑器、diff 里都看不出来,工具还可能悄悄删掉或换掉它们(规格 §⑨ 的三点按码位数字,
# 一个零宽字符就让「逐字相同」与「字数」两件事都对不上)。与 app-core / wording 的卫生检查同一张表,
# 另加回车(源文件一律 LF)。
HIDDEN = [(0x00A0, 0x00A0), (0x00AD, 0x00AD), (0x034F, 0x034F), (0x115F, 0x1160), (0x17B4, 0x17B5),
          (0x180B, 0x180F), (0x2000, 0x200F), (0x2028, 0x2029), (0x202F, 0x202F), (0x205F, 0x206F),
          (0x2800, 0x2800), (0x3000, 0x3000), (0x3164, 0x3164), (0xE000, 0xF8FF), (0xFE00, 0xFE0F),
          (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0)]


def hidden_chars(text):
    """→ ['行:U+XXXX', …]。"""
    out, line = [], 1
    for ch in text:
        cp = ord(ch)
        if ch == NL:
            line += 1
            continue
        if cp == 13 or any(a <= cp <= b for a, b in HIDDEN):
            out.append("%d:U+%04X" % (line, cp))
    return out


def contract_files():
    """五份 skill、AGENTS.md、规格的路径(按原样读,回车不被换掉)。"""
    d = skills_dir()
    out = []
    for entry in sorted(os.listdir(d)):
        for path in (os.path.join(d, entry, "SKILL.md"), os.path.join(d, entry)):
            if os.path.isfile(path) and path.endswith(".md"):
                out.append(path)
                break
    out += [os.path.join(toolkit_root(), "AGENTS.md"), os.path.join(toolkit_root(), "04-正本规格-v1.md")]
    return out


def test_skill_AGENTS_与规格里没有看不见的字符():
    files = contract_files()
    assert len(files) >= 7, files
    bad = []
    for path in files:
        with io.open(path, encoding="utf-8", newline="") as f:
            found = hidden_chars(f.read())
        if found:
            bad.append("%s: %s" % (os.path.relpath(path, toolkit_root()), ", ".join(found[:8])))
    assert bad == [], NL.join(bad)


def test_看不见的字符的判据会失败():
    for cp in (0x200B, 0xFEFF, 0xFE0F, 0x2028, 0x00A0, 0x3000, 13):
        assert hidden_chars("a" + chr(cp) + "b") == ["1:U+%04X" % cp], hex(cp)
    assert hidden_chars("普通文字" + NL + "第二行 " + chr(0x26A0) + " plain") == []


# ── 红线编号 ────────────────────────────────────────────────────────────
# 规则库 2026-08-27 升格进 R11/R12,而两把 skill 的执行清单、以及 redlines.md
# 自己的抬头,都还写着「R1–R10」—— 新红线存在,却没有任何一处会去对照它们。
# ⛔ 不写编号是允许的(更好);写了就不许低于规则库的最大号。
def _rules_max():
    p = os.path.join(os.path.dirname(skills_dir().rstrip(os.sep)), "library", "rules", "redlines.md")
    if not os.path.exists(p):
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "library", "rules", "redlines.md")
    text = io.open(p, encoding="utf-8").read()
    nums = []
    for line in text.split(NL):
        s = line.strip()
        if not s.startswith("| R"):
            continue
        digits = ""
        for ch in s[3:]:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits:
            nums.append(int(digits))
    return max(nums) if nums else 0


def _mentioned(text):
    """文本里出现的 R<数字>。不用正则:这条链上反斜杠被吃掉过。"""
    out = []
    i = 0
    while i < len(text):
        if text[i] == "R" and i + 1 < len(text) and text[i + 1].isdigit():
            j = i + 1
            digits = ""
            while j < len(text) and text[j].isdigit():
                digits += text[j]
                j += 1
            out.append(int(digits))
            i = j
        else:
            i += 1
    return out


def test_规则库里有几条红线_skill_就不许只对照到更少():
    rules_max = _rules_max()
    assert rules_max > 0, "redlines.md 里一条红线都没解析出来 —— 判据本身坏了"
    bad = []
    for name, lines in each_skill():
        text = NL.join(lines)
        if "redlines" not in text and "红线" not in text:
            continue
        nums = _mentioned(text)
        if not nums:
            continue          # 完全不写编号 = 动态枚举,允许
        if max(nums) < rules_max:
            bad.append("%s 只对照到 R%d,而 redlines.md 已到 R%d" % (name, max(nums), rules_max))
    assert bad == [], NL.join(bad)


def test_没有一处把_cite_check_的项说成只能_WARN():
    """⛔ ⑧ 自 2026-09-04 起「自报与机器取值不符」判 FAIL,而 cite-trace 的 E2
    ——正是面板在交付对接窗口上渲染的那段字——仍写着 `⑧⑨预算(WARN)`,等于告诉操作者
    这一项拦不住任何东西。(对抗核查抓到。)"""
    stale = []
    for name, lines in each_skill():
        text = NL.join(lines)
        for bad in ("⑧⑨预算(WARN)", "⑧预算仪表(WARN)"):
            if bad in text:
                stale.append("%s 仍写着 %s" % (name, bad))
    assert stale == [], NL.join(stale)


# ── 「要聊清的三件事」的工作笔记(规格 §⑨-4 · v1.2.1)──────────────────────────────────────
# 任务计划写出来之前,进度面板的研究范围、成稿形式只能从 agent 的工作笔记 clarify.yaml 读。task-planner
# 必须让 agent 在用户答了之后、弹报告类型卡之前写好它,写完自查;任务计划里要有 scope_brief。
YAML_BLOCK_RE = re.compile(r"```yaml[ \t]*\n(.*?)\n```", re.S)
# 设计稿 D06:用户答完之后、报告类型卡之前那一句(由 wording/skill-notes.json 生成进「对用户说话」一节)。
D06_LINE = "记下了：乡镇的桩算，村里的不算；成稿出 Word 版。还有一件请你在卡上定。"


def clarify_step_problems(body):
    """task-planner 正文(去掉 wording 生成的那一节)→ 工作笔记那一步缺了什么。"""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import yaml
    from pipeline_lib import CLARIFY_KEYS, clarify_problems
    out = []
    note = body.find("clarify.yaml")
    card = body.find('"kind": "report_type"')
    if note < 0:
        out.append("没有写工作笔记 clarify.yaml 的那一步")
    elif card < 0 or note > card:
        out.append("写工作笔记那一步要在报告类型卡之前")
    if "clarify_check.py" not in body:
        out.append("写完没有让跑 clarify_check.py")
    blocks = YAML_BLOCK_RE.findall(body)
    if not blocks:
        out.append("没有示例笔记(```yaml 块)")
    for block in blocks:
        try:
            data = yaml.safe_load(block)
        except yaml.YAMLError:
            out.append("示例笔记读不出来")
            continue
        if not isinstance(data, dict) or set(data) != set(CLARIFY_KEYS):
            out.append("示例笔记应正好写 %s 两个键" % " / ".join(CLARIFY_KEYS))
        out += ["示例笔记:%s" % p for p in clarify_problems(data)]
    e1 = [line for line in body.split(NL) if is_exit_row(line) and line.split("|")[1].strip() == "E1"]
    if not (e1 and "scope_brief" in e1[0]):
        out.append("E1 没有要求任务计划写 scope_brief")
    return out


def test_task_planner_在报告类型卡之前写工作笔记():
    """Selina 10-02:D06 上面板的「要聊清的三件事」在任务计划写出来之前就有内容 —— 来源是这份笔记。"""
    text = NL.join(dict(each_skill())["task-planner"])
    assert clarify_step_problems(split_wording(text)[0]) == []
    assert D06_LINE in split_wording(text)[1], "「对用户说话」一节里少了 D06 那一句"


def test_工作笔记那一步的判据会失败():
    card = '```json' + NL + '{"id": "report-type", "kind": "report_type"}' + NL + '```'
    note = "写进 `<project>/clarify.yaml`,跑 `clarify_check.py`。" + NL + '```yaml' + NL + "scope: 县级" + NL + "format: Word 版" + NL + '```'
    e1 = "| E1 | frontmatter(kind/scope_brief) | 自查 |"
    good = note + NL + card + NL + e1
    assert clarify_step_problems(good) == []
    bad = {
        "笔记在卡之后": card + NL + note + NL + e1,
        "没有笔记": card + NL + e1,
        "不自查": good.replace("clarify_check.py", "检查"),
        "示例多一个键": good.replace("format: Word 版", "format: Word 版" + NL + "report_type: 研判型"),
        "示例超过 30 字": good.replace("scope: 县级", "scope: " + "县" * 31),
        "没有示例": good.replace("```yaml", "```text"),
        "E1 没有 scope_brief": good.replace("kind/scope_brief", "kind"),
    }
    for name, text in bad.items():
        assert clarify_step_problems(text), "没报出来:%s" % name


# ── 成稿叫法 report_name(规格 §⑨-4 · v1.2.1):写任务计划那一步要把报告类型卡上的叫法写进去 ──────────
TASK_PLAN_STEP = "写 `<project>/task_plan.md`"


def report_name_step_problems(body):
    """task-planner 正文 → 写 report_name 的要求缺了什么:E1 里列着它;写任务计划那一步(报告类型卡之后,
    从「写 `<project>/task_plan.md`」到任务计划卡的示例)让 agent 写它。报告类型卡那几条说明里也有
    report_name,所以只认这一段 —— 不然那一步删掉了也查不出来。"""
    out = []
    e1 = [line for line in body.split(NL) if is_exit_row(line) and line.split("|")[1].strip() == "E1"]
    if not (e1 and "report_name" in e1[0]):
        out.append("E1 没有列 report_name")
    after_type = body.find('"kind": "report_type"')
    step = body.find(TASK_PLAN_STEP, after_type) if after_type >= 0 else -1
    plan_card = body.find('"kind": "task_plan"', step) if step >= 0 else -1
    lines = body[step:plan_card].split(NL) if step >= 0 and plan_card >= 0 else []
    # 要有一行把 report_name 和报告类型卡连起来(写的就是卡上那个词);自查那一行也提 report_name,不算
    if not any("report_name" in line and "报告类型卡" in line for line in lines):
        out.append("写任务计划那一步(报告类型卡之后、任务计划卡之前)没有让照报告类型卡写 report_name")
    return out


def test_task_planner_写任务计划时写成稿叫法():
    text = NL.join(dict(each_skill())["task-planner"])
    assert report_name_step_problems(split_wording(text)[0]) == []


def test_成稿叫法那一步的判据会失败():
    good = NL.join(['{"id": "report-type", "kind": "report_type", "report_name": "内参"}',
                    "- `report_name` ≤6 字:成稿叫法。",
                    TASK_PLAN_STEP + ",frontmatter 一并写 `report_name`(与报告类型卡上的一字不差)。",
                    "1. 自查:`report_name` 写了就 ≤6 字。",
                    '{"id": "task-plan-v1", "kind": "task_plan"}',
                    "| E1 | frontmatter(kind/report_name) | 自查 |"])
    assert report_name_step_problems(good) == []
    bad = {
        "E1 没列": good.replace("kind/report_name", "kind"),
        "那一步没写(只剩自查那一行和报告类型卡的说明)": good.replace(",frontmatter 一并写 `report_name`(与报告类型卡上的一字不差)。", "。"),
        "那一步在报告类型卡之前": good.replace(TASK_PLAN_STEP + ",frontmatter 一并写 `report_name`(与报告类型卡上的一字不差)。" + NL, "").replace(
            '{"id": "report-type"', TASK_PLAN_STEP + ",写 `report_name`(同报告类型卡)。" + NL + '{"id": "report-type"'),
    }
    for name, text in bad.items():
        assert report_name_step_problems(text), "没报出来:%s" % name


# ── v1.2.1 复核(独立核查 M4、L2、L8、L9、L11)────────────────────────────────────────────
def agents_text():
    with io.open(os.path.join(toolkit_root(), "AGENTS.md"), encoding="utf-8") as f:
        return f.read()


def new_project_first_problems(text):
    """M4:开场先看 task_plan.md 在不在 —— 不在就是全新项目,不跑开场检查(它对新项目退出码 1)。
    判据:第一次提到 pipeline_status.py 的那一行里,它前面已经写了 task_plan.md、「全新项目」与「不跑」。"""
    at = text.find("pipeline_status.py")
    if at < 0:
        return ["没有开场检查"]
    line = text[text.rfind(NL, 0, at) + 1:at]
    missing = [word for word in ("task_plan.md", "全新项目", "不跑") if word not in line]
    return ["开场检查之前没有先看 task_plan.md 在不在(那一行缺:%s)" % "、".join(missing)] if missing else []


def test_新项目先看任务计划在不在_再决定跑不跑开场检查():
    bad = ["%s: %s" % (name, p) for name, text in (("task-planner", split_wording(NL.join(dict(each_skill())["task-planner"]))[0]),
                                                ("AGENTS.md", agents_text()))
           for p in new_project_first_problems(text)]
    assert bad == [], NL.join(bad)


def install_note(text):
    """AGENTS.md 最上面给应用的装入说明(第一个 `>` 引用块)。"""
    lines = text.split(NL)
    start = next(i for i, line in enumerate(lines) if line.startswith(">"))
    end = next((i for i in range(start, len(lines)) if not lines[i].startswith(">")), len(lines))
    return NL.join(lines[start:end])


def test_装入说明装入之后照样读得通():
    """A2 联调:应用装入 AGENTS.md 时把每一处工具包占位符换成真路径。说明里要是写着占位符本身,说明就被换坏了。"""
    text = agents_text()
    note = install_note(text)
    assert "装入说明" in note
    assert install_note(text.replace("<toolkit>", "C:/Program Files/Yunzhi/toolkit")) == note
    old = "> **装入说明(给应用)**:装入时把每一处 `<toolkit>` 换成工具包根目录的绝对路径。" + NL + NL + "- `<toolkit>`"
    assert install_note(old.replace("<toolkit>", "C:/x")) != install_note(old)        # 判据会失败


def test_新项目那一条的判据会失败():
    old = "进入这一步:新会话首轮或距上轮 >6h,先跑 `pipeline_status.py <project>` —— 退出码 1 → 停下。没有 task_plan.md = 全新项目。"
    assert new_project_first_problems(old)
    good = "进入这一步,先看 `task_plan.md` 在不在。不在 = 全新项目:不跑开场检查(`pipeline_status.py` 会退出码 1)。"
    assert new_project_first_problems(good) == []


# L2:skill 正文里给用户看的「」(要说的话、要写进材料的话)用全角标点
ASCII_PUNCT_RE = re.compile(r"[,:;()!?]")


def ascii_punct_spans(text):
    body = split_wording(text)[0]
    return [m.group(1) for m in re.finditer("「([^「」]*)」", body) if ASCII_PUNCT_RE.search(m.group(1))]


def test_给用户看的引文用全角标点():
    bad = ["%s: 「%s」" % (name, span) for name, lines in each_skill() for span in ascii_punct_spans(NL.join(lines))]
    bad += ["AGENTS.md: 「%s」" % span for span in ascii_punct_spans(agents_text())]
    assert bad == [], NL.join(bad)


def test_全角标点的判据会失败():
    assert ascii_punct_spans("说「先一次问完,你挑着答就行:」") == ["先一次问完,你挑着答就行:"]
    assert ascii_punct_spans("说「先一次问完，你挑着答就行：」") == []


# L8:重新确认一份已经确认过的材料 —— 三份 skill 一字不差
SHARED_BEGIN = "**重新确认一份已经确认过的材料**"
SHARED_END = "再弹它的确认卡。"
SHARED_SKILLS = ("task-planner", "evidence-card", "outline-cocreate")


def shared_paragraphs(texts):
    out = {}
    for name, text in texts.items():
        i = text.find(SHARED_BEGIN)
        j = text.find(SHARED_END, i) if i >= 0 else -1
        out[name] = text[i:j + len(SHARED_END)] if i >= 0 and j >= 0 else None
    return out


def test_重新确认的做法三份_skill_一字不差():
    found = shared_paragraphs({name: NL.join(lines) for name, lines in each_skill() if name in SHARED_SKILLS})
    assert all(found.values()), "缺这一段:%s" % [n for n, v in found.items() if not v]
    assert len(set(found.values())) == 1, "三份不一样"
    paragraph = next(iter(found.values()))
    for must in ("--invalidate", "`version` 加一", "「没有改动」", "gate1_awaiting", "gate2_awaiting", "gate3_awaiting"):
        assert must in paragraph, must


def test_重新确认那一段的判据会失败():
    para = SHARED_BEGIN + "① 作废;② 加一;③ 写「没有改动」;再弹它的确认卡。"
    found = shared_paragraphs({"a": para, "b": para.replace("加一", "不加"), "c": "没有这一段"})
    assert found["c"] is None and found["a"] != found["b"]


# L9:回答的形状写在 AGENTS.md;报告类型卡与决定卡的特殊情况写在各自的 skill
SHAPES = ("`{id, kind, approved: true|false, custom?}`",
          "`{id, kind, selected: 'judge'|'survey'|'undecided'|null, custom?}`",
          "`{id, kind, selected: <从 1 数的选项序号>|null, custom?}`")


def answer_shape_problems(agents, planner, evidence):
    out = ["AGENTS.md 缺回答形状 %s" % s for s in SHAPES if s not in agents]
    for word in ("`undecided`", "`null`", "确认任务计划时还能改"):
        if word not in planner:
            out.append("task-planner 没说 %s 怎么办" % word)
    if "`selected: null`" not in evidence:
        out.append("evidence-card 没说决定卡 selected: null 怎么办")
    return out


def test_回答的形状写清楚了():
    skills = {name: NL.join(lines) for name, lines in each_skill()}
    assert answer_shape_problems(agents_text(), skills["task-planner"], skills["evidence-card"]) == []


def test_回答形状的判据会失败():
    assert len(answer_shape_problems("", "", "")) == len(SHAPES) + 4


# L11:交付的三点有一个示例,整篇就是三点、没有 {#commitments} 锚点
MD_BLOCK_RE = re.compile(r"```markdown[ \t]*\n(.*?)\n```", re.S)


def delivery_example_problems(text):
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from pipeline_lib import commitment_problems
    blocks = MD_BLOCK_RE.findall(split_wording(text)[0])
    if not blocks:
        return ["没有 library/delivery_commitments.md 的示例"]
    out = []
    for block in blocks:
        if "{#commitments}" in block:
            out.append("示例里有 {#commitments} 锚点(交付那份整篇就是三点)")
        out += commitment_problems(block, whole=True)[0]
    return out


def test_交付的三点有示例且照规格写():
    assert delivery_example_problems(NL.join(dict(each_skill())["cite-trace"])) == []


def test_交付示例的判据会失败():
    good = "```markdown" + NL + "# 交付" + NL + NL + "1. **改动内容**:无。" + NL + "2. **最薄弱的依据**:基准。" + NL + "3. **最可能出错的地方**:上限。" + NL + "```"
    assert delivery_example_problems(good) == []
    assert delivery_example_problems(good.replace("# 交付", "## 要你认的三件事 {#commitments}"))
    assert delivery_example_problems("没有示例")
