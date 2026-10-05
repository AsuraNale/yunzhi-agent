# -*- coding: utf-8 -*-
"""pipeline_lib — 云织-Ann 机器窗口共享库(依据 04-正本规格-v1.md,改此文件=改接口)。

约定:
- 所有校验器输出统一 Report:{check, status(PASS|FAIL|WARN), detail, fix_hint} 列表
  + 人读摘要;exit code 0=无 FAIL,1=有 FAIL,2=用法/环境错误。
- WARN = 规格里的「提示,不 FAIL」(预算类);FAIL = 拦交付。
"""
# 两个格式各自的转义,在这里被抹平。HTML 的 href 与 docx rels 的 Target 都
# 按各自规范转义了 &,而 references.yaml 存的是真实 URL —— 三处要能比对,
# 必须先反转义。⚠️ 实测:11 条带 &stockid 的链接被 ③「文内⊆参考」
# 判为不在登记表,而它们全都在。检查器读的是转义后的形态,登记表读的是原形。
import html as html_entities
import hashlib
import io
import json
import os
import re
import sys
from datetime import date, datetime

import yaml

# ---- 枚举(规格 §0/§②) ----
FIVE_STATES = {"ok", "empty", "gap", "failed", "blocked"}
STANCES = {"support", "counter", "mixed", "gap"}
TIERS = {"A", "A-", "B"}
GENRES = {"survey", "argument"}
APPROVAL_STATUSES = {"draft", "awaiting", "approved", "stale"}
PIPELINE_STATES = [
    "planning", "gate1_awaiting", "gate1_approved", "collecting",
    "gate2_awaiting", "gate2_approved", "outlining", "gate3_awaiting",
    "gate3_approved", "drafting", "verifying", "delivered",
]
STRENGTHS = {"strong", "medium_strong", "medium", "weak"}


# ---- 流程环节(规格 §① `stages` · v1.1.0) ----
# 四个环节的机器键,顺序即流程顺序。给用户看的叫法:明确任务 · 收集资料 ·
# 拟定提纲 · 撰写交付(见 docs/stages.md)。
STAGES = ("task", "sources", "outline", "delivery")
# ⛔ 本版只允许省掉「拟定提纲」。其余三个环节省掉的话,后面的检查没有定义过
# 该怎么走 —— 与其猜,不如拒绝。
OPTIONAL_STAGES = frozenset({"outline"})
# 每个环节由哪份正本过窗口(交付环节不落印章,走 `stamp.py --advance delivered`)。
STAGE_DOCS = {"task": "task_plan.md", "sources": "dossier.md",
              "outline": "outline.md", "delivery": None}
# 每个环节占哪几个 pipeline_status 取值。合起来恰好是 PIPELINE_STATES。
STAGE_STATES = {
    "task": ("planning", "gate1_awaiting", "gate1_approved"),
    "sources": ("collecting", "gate2_awaiting", "gate2_approved"),
    "outline": ("outlining", "gate3_awaiting", "gate3_approved"),
    "delivery": ("drafting", "verifying", "delivered"),
}


def parse_stages(meta):
    """task_plan 的 frontmatter → (环节列表, 错误说明)。

    - 没有 `stages` 键,或值为 null → 四个环节全有(缺省),错误为 None;
    - 合法的列表 → 原样返回(已按流程顺序),错误为 None;
    - 其他一切 → (None, 一句说明)。

    ⛔ 读不出来时不猜。调用方拿到 None 必须按「四个环节全查」处理(最严),
    并把说明报出来 —— 「读不出」与「省掉了」在这里后果相反。
    """
    if not isinstance(meta, dict) or meta.get("stages") is None:
        return list(STAGES), None
    raw = meta["stages"]
    if not isinstance(raw, list):
        return None, "stages 不是列表(现为 %s)" % type(raw).__name__
    for s in raw:
        if not isinstance(s, str) or s not in STAGES:
            return None, "stages 里有不认识的环节 %r(取值:%s)" % (s, " / ".join(STAGES))
    if len(set(raw)) != len(raw):
        return None, "stages 里有重复的环节:%r" % (raw,)
    missing = [s for s in STAGES if s not in raw and s not in OPTIONAL_STAGES]
    if missing:
        return None, ("stages 省掉了 %s —— 本版只允许省掉 outline(拟定提纲)"
                      % " / ".join(missing))
    if raw != [s for s in STAGES if s in raw]:
        return None, "stages 的顺序必须是 %s" % " → ".join(STAGES)
    return list(raw), None


def stage_states(stages):
    """本项目合法的 pipeline_status 取值(按流程顺序)。"""
    return [st for s in stages for st in STAGE_STATES[s]]


def stage_docs(stages):
    """本项目需要过窗口(落确认记录)的正本文件名。"""
    return [STAGE_DOCS[s] for s in stages if STAGE_DOCS[s]]


# ---- 确认记录的签名载荷(规格 §0 印章块 · docs/signoff-format.md · v1.1.0) ----
# ⚠️ 本工具包**不校验签名**,签名与校验都在应用里(私钥只在应用进程)。这里只
# 给出载荷的参考实现,让规格里那段定义可以被测试逐字节钉住。
SIGNOFF_SCHEME = "yunzhi-signoff/1"
SIGNOFF_FIELDS = ("approved_hash", "approved_at", "approved_by", "approval_quote")


def signoff_payload(project_id, doc, approval):
    """一条确认记录被签名的字节串:UTF-8 编码的 JSON 数组(无空白)。

    `["yunzhi-signoff/1", 项目 id, 正本文件名, approved_hash, approved_at,
    approved_by, approval_quote]` —— 与 Node 的 `JSON.stringify(数组)` 逐字节
    相同(测试里用 node 对拍)。任一字段不是字符串 → ValueError。
    """
    values = [SIGNOFF_SCHEME, project_id, doc]
    values += [(approval or {}).get(k) for k in SIGNOFF_FIELDS]
    for v in values:
        if not isinstance(v, str):
            raise ValueError("签名载荷的每一项都必须是字符串,现有 %r" % (v,))
    return json.dumps(values, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def date_text(value):
    """YAML 读出来的日期 / 日期时间 → ISO-8601 字符串;其他值原样返回。

    ⛔ v1.1.1 · Tb2:`approved_at: 2026-09-29T14:03:11-04:00` 不加引号,PyYAML 读成
    datetime(`2026-09-29` 读成 date)。原来 pipeline_status 拿它切片(承诺段的生效日)
    抛 TypeError,写 `--json` 时 json 不认它再崩一次 —— 过窗口前第一个跑的脚本崩了,
    一项检查都没跑。归一用 `isoformat()`:最常见的写法(带时区的 `T` 形)归一后与加了
    引号时逐字相同,带时区的保留原偏移;空格分隔、小数秒这类写法归一成标准形。
    ⚠️ 只用在「读出来用、不写回」的地方(报告、facts、比较)。写回正本不归一 —— 那会
    改掉文件里别的字段的写法,而 hash 覆盖的实质字段正是按原来的读法算的。
    """
    if isinstance(value, date):   # datetime 是 date 的子类
        return value.isoformat()
    return value


def norm_tier(t):
    """归一 tier:U+2212 −→'-',去空白。返回归一后的串(可能仍非法)。"""
    if t is None:
        return None
    return str(t).replace("−", "-").strip()


# ---- frontmatter ----
FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.S)


def read_md(path):
    r"""读 md 正本 → (meta: dict, body: str)。无 frontmatter → ({}, 全文)。

    ⚠️ v1.0.4(2026-09-03):**BOM 不算内容**,读入即剥。
    理由是它同时坏三件事:①`\A---` 匹配不上 → frontmatter 被当正文;
    ②`(#{1,4})\s+` 匹配不上 → 文档自己的 H1 被渲成正文段落(实测,
    交付 HTML 里留着一个字面的 `#`);③`str.strip()` 不去 U+FEFF,所以它
    进了 content_hash —— 同一份内容加不加 BOM 会得到两个印章值。
    ⭐ 剥在这一处,不在各渲染器里:三处都要用到同一个「什么算内容」的判断。
    """
    with io.open(path, encoding="utf-8") as f:
        text = f.read().lstrip("\ufeff")
    m = FM_RE.match(text)
    if not m:
        return {}, text
    meta = yaml.safe_load(m.group(1)) or {}
    return meta, text[m.end():]


def load_md(path):
    """读 md 正本,frontmatter 读不出来也不抛 → (meta: dict, body: str, problem: str 或 None)。

    v1.1.2 · T3-1:frontmatter 的 YAML 写法有误、写了不存在的日期(不加引号的 `2026-02-30`,
    PyYAML 构造日期时抛 ValueError)、或整个不是键值映射,read_md 会抛异常或给出不是 dict 的
    meta —— pipeline_status 原来就这样崩掉:一项检查都没跑,`--json` 也没有。这里把它们变成
    一句给人看的说明(带文件里的行号),meta 记 {};正文照样给(正文不经 YAML)。
    ⛔ 只包一层,不改 read_md 的读法:应用里的 yaml.mjs 按 read_md 的读法逐值对拍(app-core 的
    差分测试),这里把不存在的日期改读成文字,两边就对同一份文件给出两个答案。
    复核补(v1.1.2):不是 UTF-8 的文件(GBK 这类中文编码)原来在这里第二次读时照样抛。说明里能定位就带
    行号:写法有误、读不出的值、不是 UTF-8 → 那一行;不是键值映射 → frontmatter 那几行。
    ⛔ 说明里只说哪一行、哪一类毛病,**不引正本里的任何内容**(值、PyYAML 或 Python 异常自己的说明都可能带
    原文):报告会进 --json、进应用,截断的片段应用遮不住(v1.1.2 复核)。
    """
    try:
        meta, body = read_md(path)
    except UnicodeDecodeError:   # ValueError 的子类,要先接住:下面那一支会把文件再按 UTF-8 读一遍
        return {}, "", not_utf8(path)
    except (yaml.YAMLError, ValueError) as error:
        text, m = _front(path)
        problem = yaml_problem(text, m.start(1), m.group(1), error) if m else yaml_problem(text, 0, text, error)
        return {}, (text[m.end():] if m else text), problem
    if not isinstance(meta, dict):
        text, m = _front(path)
        return {}, body, "%s现在是%s,不是键值映射" % (_lines_of(text, m), kind_of(meta))
    return meta, body, None


def not_utf8(path):
    """不是 UTF-8 的文件 → 一句话(第几行起读不出来),不引内容。"""
    with io.open(path, "rb") as f:
        raw = f.read()
    try:
        raw.decode("utf-8")
        where = ""
    except UnicodeDecodeError as error:
        where = "第 %d 行起" % (raw.count(bytes([10]), 0, error.start) + 1)
    return "%s不是 UTF-8 编码(像是 GBK 这类中文编码),先用 UTF-8 另存" % where


def kind_of(value):
    """一个值是什么 —— 只说类型,不说内容。报告里一律这样说,免得把正本里的话带进报告(v1.1.2 复核)。"""
    if isinstance(value, bool):
        return "一个真假值"
    if isinstance(value, (int, float)):
        return "一个数字"
    if isinstance(value, str):
        return "一段文字"
    if isinstance(value, dict):
        return "一个映射"
    if isinstance(value, list):
        return "一个列表"
    if isinstance(value, date):   # datetime 是 date 的子类
        return "一个日期"
    if value is None:
        return "空值"
    return "一个 %s" % type(value).__name__


def frontmatter_lines(path):
    """frontmatter 里的键与条目在文件里第几行 → {("revision_log",): 12, ("revision_log", 0): 13,
    ("revision_log", 0, "at"): 14, …}:键记键所在的那一行,列表条目记条目开头那一行。读不出来 → {}。
    给报告指位置用(只说哪一行,不引内容)。"""
    try:
        text, m = _front(path)
    except (OSError, ValueError):
        return {}
    return yaml_lines(text, m.start(1), m.group(1)) if m else {}


def yaml_lines(text, start, yaml_text):
    """同上,对任意一段 YAML:`yaml_text` 在 `text`(整个文件)里从 `start` 起;行号按文件里的换行算。"""
    try:
        root = yaml.compose(yaml_text, Loader=yaml.SafeLoader)
    except (yaml.YAMLError, RecursionError):
        return {}
    out = {}

    def line_at(index):
        return text.count(chr(10), 0, start + index) + 1

    def walk(node, path, depth):
        if node is None or depth > 6:   # 只用得到浅的几层;别名可以成环,深度兜住
            return
        if isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                if isinstance(key, yaml.ScalarNode):
                    out[path + (key.value,)] = line_at(key.start_mark.index)
                    walk(value, path + (key.value,), depth + 1)
        elif isinstance(node, yaml.SequenceNode):
            for i, value in enumerate(node.value):
                out[path + (i,)] = line_at(value.start_mark.index)
                walk(value, path + (i,), depth + 1)

    walk(root, (), 0)
    return out


def _front(path):
    with io.open(path, encoding="utf-8") as f:
        text = f.read().lstrip(chr(0xFEFF))
    return text, FM_RE.match(text)


def _lines_of(text, m):
    """frontmatter(两行 --- 之间)在文件里是第几行到第几行。"""
    first = text.count(chr(10), 0, m.start(1)) + 1
    last = text.count(chr(10), 0, m.end(1)) + 1
    return "第 %d 行" % first if first == last else "第 %d–%d 行" % (first, last)


TIMESTAMP_TAG = "tag:yaml.org,2002:timestamp"


def yaml_problem(text, start, yaml_text, error):
    """读 YAML 时抛的异常 → 一句话:哪一行、哪一类毛病。`yaml_text` 是交给 PyYAML 的那一段,在 `text`
    (整个文件)里从 `start` 起。⛔ 不引原文,也不引异常自己的说明(它们会带原文,如 `invalid literal ...
    '三张'`)。行号按文件里的换行算:PyYAML 自己的行号把值里的 U+2028 / U+2029 / NEL 也当换行,碰到它们
    就比编辑器里多 —— 所以用它给的字符位置换算(v1.1.2 复核)。"""
    def line_at(index):
        return text.count(chr(10), 0, start + index) + 1

    if isinstance(error, yaml.YAMLError):
        mark = getattr(error, "problem_mark", None)
        position = mark.index if mark is not None else getattr(error, "position", None)
        return "第 %d 行 YAML 写法有误" % line_at(position) if isinstance(position, int) else "YAML 写法有误"
    # ValueError:PyYAML 把一个值按它的类型构造时抛的(日期或时刻不存在、标了 !!int 却不是整数……)。找出是哪一个。
    bad = _unreadable_value(yaml_text)
    if bad:
        index, tag = bad
        if tag == TIMESTAMP_TAG:
            return "第 %d 行的值不是存在的日期或时刻" % line_at(index)
        return "第 %d 行的值按它标的类型(%s)读不出来" % (line_at(index), tag.rsplit(":", 1)[-1])
    return "有一个值读不出来"


def _unreadable_value(yaml_text):
    """frontmatter 里按文档顺序第一个按类型构造不出来的值 → (在 yaml_text 里的字符位置, 标签);没有 → None。

    用 SafeLoader 自己的构造器逐个试(与 safe_load 同一套规则),所以不加引号的不存在日期、标了 !!int 的
    文字这类都找得到;加了引号的是字符串,构造不会失败。"""
    try:
        root = yaml.compose(yaml_text, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        return None
    loader = yaml.SafeLoader("")
    seen = set()

    def walk(node):
        if node is None or id(node) in seen:   # 锚点 / 别名可以成环
            return None
        seen.add(id(node))
        if isinstance(node, yaml.ScalarNode):
            if node.tag == "tag:yaml.org,2002:merge":   # << 只在映射里有意义,单独构造不了
                return None
            try:
                loader.construct_object(node, deep=True)
            except (ValueError, yaml.YAMLError):
                return node.start_mark.index, node.tag
            return None
        if isinstance(node, yaml.SequenceNode):
            children = node.value
        elif isinstance(node, yaml.MappingNode):
            children = [n for pair in node.value for n in pair]
        else:
            return None
        for child in children:
            hit = walk(child)
            if hit:
                return hit
        return None

    return walk(root)


def write_md(path, meta, body):
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("---\n")
        yaml.safe_dump(meta, f, allow_unicode=True, sort_keys=False)
        f.write("---\n")
        f.write(body)


# 印章 hash 不覆盖的「过程字段」:批准后这些字段的合法变化不应作废印章;
# 实质内容字段(genre/budget/claims/gate_verdict/正文…)全部覆盖。
#   v1.0.1(对抗复核):状态机推进与时间戳 —— 否则每次推进都作废刚盖的章。
#   v1.0.2(实测):**revision_log** —— 它是「关于变更的审计轨迹」,不是内容。
#     不豁免的后果已实证:只追加一条 log、正文一字未动,印章立刻失效
#     → 规格自己要求的审计字段写不起 → 实测里三份正本 revision_log 全空。
PROCESS_FIELDS = {"approval", "pipeline_status", "last_turn_at", "updated_at",
                  "revision_log"}


HASH_TYPED_KEYS = "#typed-keys/1" + chr(10)


def _typed_keys(value):
    """每个映射写成 {"m": [[键的类型, 键的文字, 值], …]},按(键的文字, 类型)排序 —— 日期键、数字键、yes/no 键
    都写得出、排得了序,键的先后不影响结果;类型写在里面,数字 1 与文字 '1' 不会混成同一个键。输出里的 JSON
    对象只可能来自原文的映射(列表照旧是列表),所以映射也不会和「看起来像这些行的列表」混。"""
    if isinstance(value, dict):
        rows = [[type(k).__name__, str(k), _typed_keys(v)] for k, v in value.items()]
        rows.sort(key=lambda row: (row[1], row[0]))
        return {"m": rows}
    if isinstance(value, list):
        return [_typed_keys(v) for v in value]
    return value


def content_hash(meta, body):
    """approved_hash 的定义实现(规格 §0 v1.0.1):对「frontmatter 去过程字段 + 正文」
    取 sha256 前 12 位。frontmatter 以 key 排序的 JSON 序列化,避免 YAML 风格差异。

    ⛔ 签名绑定的就是这个值:原来算得出的每一份文档,结果一个字节也不许变(fixtures/golden_content_hashes.json
    记着 11964e3 时 77 份文档的值,test_v112_fixes.py 逐份核)。
    v1.1.2 · T3-1 复核:键是日期(`milestones: {2026-10-01: 初稿}`)、数字键混着文字键、yes / no 键混着文字键
    时,json 写不出键或排不了序,原来在这里抛 TypeError —— 这样的正本永远确认不了。只有这种原来算不出来的才改走
    _typed_keys,前面再加一个标记:原来的序列化总以 { 开头,两条路的结果撞不上。"""
    meta2 = {k: v for k, v in (meta or {}).items() if k not in PROCESS_FIELDS}
    try:
        payload = json.dumps(meta2, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        payload = HASH_TYPED_KEYS + json.dumps(_typed_keys(meta2), ensure_ascii=False, default=str)
    payload += "\n\x00\n" + body.strip()
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


# ---- 占位与链接 ----
PLACEHOLDER_RE = re.compile(r"\[\[(R?\d+)\]\]")
BARE_URL_RE = re.compile(r"https?://[^\s\)\]\>\"\'　，。；）]+")



def number_entries(body, entries):
    """→ (占位→号 映射, 按号排序的 [(号, entry)] 列表)。与 v1 逐字同逻辑。"""
    by_id = {e.get("id"): e for e in entries}
    by_no = {e.get("display_no"): e for e in entries if e.get("display_no")}
    ph_no, numbered, next_no, used = {}, {}, 1, set()
    for ph in PLACEHOLDER_RE.findall(body):
        if ph in ph_no:
            continue
        if ph.startswith("R"):
            e = by_id.get(ph)
            if e is None:
                continue  # 悬空占位交给 cite_check,渲染器原样保留
            no = next_no
            while no in used:
                no += 1
            next_no = no + 1
        else:
            no = int(ph)
            e = by_no.get(no)
            if e is None:
                continue
        ph_no[ph], used, numbered[no] = no, used | {no}, e
    return ph_no, sorted(numbered.items())


SELF_CARD_RE = re.compile(r"(\d+)\s?[张个份]\s?证据卡")


# 承诺段的约定生效日。此前交付的项目没有这一段,不是缺陷。
# ⚠️ 判别式用交付日期而不是「段落缺失」:后者会把将来真漏写承诺的新项目
# 伪装成历史遗留,而那正是这一格要抓的东西。
COMMITMENTS_SINCE = "2026-08-25"

_COMMIT_HEADING_RE = re.compile(r"^#{1,6}[^\n]*\{#commitments\}[^\n]*$", re.M)
_NEXT_HEADING_RE = re.compile(r"^#{1,6}\s", re.M)
# 盘上实测两种形式,都收:
#   1. **口径承诺(改了什么已批的)**:正文      ← v1.2.0 起新写的是 1. **改动内容**:正文
#   - ① 本轮我改了什么用户已批准过的:正文
# 一条的写法只在这里定义一次,两种读法共用(内容的读法 parse_commitments、齐全的判据 commitment_problems):
# - 空白按 Python 的 Unicode 空白认(全角空格 U+3000、不换行空格 U+00A0、垂直制表符都算),行首行尾的也算。
#   A2 联调:原来齐全的判据用 `\s`、内容的读法只认空格与制表符,编号后面是全角空格时,同一份三点一个数出三条、
#   一个只读出两条;
# - 但不跨换行(v1.2.1 复核 L5):原来 `\s*` 会跨过换行 —— 第一条内容是空的时候,它把下一行整行吞成自己的内容,
#   三条读成两条、位置全错。空的那条照样读出来,由 commitment_problems 报「内容是空的」。
_HSPACE = r"[^\S\n]"
_NUMBERED_ITEM = r"\d+\." + _HSPACE + r"+\*\*(?P<label>[^*\n]+)\*\*[:：]" + _HSPACE + r"*(?P<text>.*)"
_CIRCLED_ITEM = r"-" + _HSPACE + r"+(?P<label>[①-⑳])" + _HSPACE + r"*(?P<text>.*)"
_ITEM_NUMBERED_RE = re.compile(r"^" + _HSPACE + r"*" + _NUMBERED_ITEM + r"$", re.M)
_ITEM_CIRCLED_RE = re.compile(r"^" + _HSPACE + r"*" + _CIRCLED_ITEM + r"$", re.M)

# ---- 三点(规格 §⑨-1 · v1.2.0) ----
# 卡上「请你重点看这三点」的标签由应用按位置写,正本里的标签只用来对位置。
COMMITMENT_LABELS = ("改动内容", "最薄弱的依据", "最可能出错的地方")
COMMITMENT_CAPS = (60, 60, 80)
# 改流程卡那一点的标签与上限(规格 §⑦ `flow_change`、§⑨-1):它在卡上填,不进正本。
FLOW_CHANGE_LABEL = "改动环节"
FLOW_CHANGE_CAP = 60
# 旧标签(v1.1.2 及以前)按位置认:标签里含这些字就算那个位置的旧写法。
COMMITMENT_OLD_MARKS = (("口径承诺", "改了什么"), ("最脆",), ("最可能错在哪",))
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
_COLONS = (":", "：")


def _circled_content(text):
    """圈码写法的一条 → (旧标签或 None, 内容):圈码之后第一个冒号之前是旧标签,之后是内容;
    没有冒号时整段都是内容(规格 §⑨-1)。"""
    cut = [i for i in (text.find(c) for c in _COLONS) if i >= 0]
    if not cut:
        return None, text.strip()
    at = min(cut)
    return text[:at].strip(), text[at + 1:].strip()


def _commitment_section(body, whole):
    """承诺段的文字与它在 body 里从第几行开始(0 起)→ (section, first_line);没有这一节 → (None, None)。"""
    if whole:
        return body, 0
    found = _COMMIT_HEADING_RE.search(body)
    if found is None:
        return None, None
    rest = body[found.end():]
    nxt = _NEXT_HEADING_RE.search(rest)
    return (rest[:nxt.start()] if nxt else rest), body.count(chr(10), 0, found.end())


def parse_commitments(body, whole=False):
    """正文里的承诺段 → [{index, label, text, content}];没有该段返回 None。

    ⚠️ 只在 `{#commitments}` 那一节内取条目。正文别处有的是有序列表,不加
    这个边界,实测某份 task_plan 会数出 12 条而实际是 3 条。

    None 与 [] 是两件事:None = 没有承诺段,[] = 有段但一条都没解析出来
    (那是解析器该被质疑的信号,不是项目没写)。

    v1.2.0:每条多一个 `content` —— 卡上显示的那段内容(规格 §⑨-1)。编号写法的 `content`
    就是 `text`;圈码写法的 `text` 里还带着旧标签(`本轮我改了什么:…`),`content` 去掉它。
    `label` 与 `text` 的取法不变(pipeline_status 的 facts 照旧)。
    """
    # 交付那一组独占一个文件(`library/delivery_commitments.md`),整篇就是承诺块,没有
    # `{#commitments}` 锚点。⚠ 按锚点找会对它返回 None,而 None 的意思是「没写承诺」——
    # 于是四组里的第四组会安静地消失。
    section, _ = _commitment_section(body, whole)
    if section is None:
        return None
    items = []
    for pattern in (_ITEM_NUMBERED_RE, _ITEM_CIRCLED_RE):
        for m in pattern.finditer(section):
            label, text = m.group("label").strip(), m.group("text").strip()
            content = _circled_content(text)[1] if pattern is _ITEM_CIRCLED_RE else text
            items.append({"index": len(items) + 1, "label": label, "text": text,
                          "content": content})
        if items:
            break
    return items


# 三点与上卡理由的「纯文字」判据(规格 §⑨-1 · v1.2.1 复核):这几样一出现就不是纯文字。
NOT_PLAIN = (("**", "加粗"), ("[[", "引文占位"), ("](", "markdown 链接"), ("http://", "网址"), ("https://", "网址"))


def plain_text_problems(text):
    """一段要上卡的文字里不是纯文字的东西 → [「加粗」「引文占位」…](去重,按上表的顺序)。"""
    found = []
    for mark, name in NOT_PLAIN:
        if isinstance(text, str) and mark in text and name not in found:
            found.append(name)
    return found


# 「齐全」的判据逐行认条目时用(行已去掉首尾空白;一条的写法同上):内容可以是空的(空的要报出来,不能当成「这一行不是条目」)。
_LINE_NUMBERED_RE = re.compile(r"^" + _NUMBERED_ITEM + r"$")
_LINE_CIRCLED_RE = re.compile(r"^" + _CIRCLED_ITEM + r"$")
_LINE_TITLE_RE = re.compile(r"^#\s+\S")


def commitment_problems(body, whole=False, line_offset=0):
    """三点齐不齐(规格 §⑨-1「齐全」)→ (毛病列表, 提醒列表, 条目列表)。毛病列表为空 = 齐全。

    - `whole=True`:交付那一份(`library/delivery_commitments.md`),整篇就是三点,最上面可以有一行一级标题;
    - `line_offset`:body 在文件里从第几行开始之前有几行(frontmatter 占的行),说明里的行号按文件算。
    条目 = [{position, line, form(numbered|circled), label, content, length, old_label}]。
    说明只说第几行、第几条、标签与字数,不引内容。提醒只有一种:用的是旧标签(照样认,新写的请换)。
    """
    section, first = _commitment_section(body, whole)
    if section is None:
        return (["没有「要你认的三件事」这一节(H2 标题带 {#commitments} 锚点),确认卡上的三点无从读起"],
                [], [])
    problems, notes, items, stray = [], [], [], []
    title_seen = False
    for i, raw in enumerate(section.split(chr(10))):
        line = raw.strip()
        if not line:
            continue
        at = line_offset + first + i + 1
        if whole and not items and not title_seen and _LINE_TITLE_RE.match(line):
            title_seen = True
            continue
        m = _LINE_NUMBERED_RE.match(line)
        if m:
            items.append({"line": at, "form": "numbered", "label": m.group("label").strip(),
                          "content": m.group("text").strip(), "old_label": None})
            continue
        m = _LINE_CIRCLED_RE.match(line)
        if m:
            old, content = _circled_content(m.group("text"))
            items.append({"line": at, "form": "circled", "label": m.group("label"),
                          "content": content, "old_label": old})
            continue
        stray.append(at)
    if stray:
        problems.append("这一节里第 %s 行不是三点里的一条(三点之外不放别的;一条写在一行里)"
                        % "、".join(str(n) for n in stray[:6]) + (" 等" if len(stray) > 6 else ""))
    forms = {it["form"] for it in items}
    if len(forms) > 1:
        problems.append("三点混用了编号写法(1. **标签**:)和圈码写法(- ①),统一写成编号写法")
    if len(items) != 3:
        problems.append("读出 %d 条,应恰好 3 条:%s" % (len(items), " / ".join(COMMITMENT_LABELS)))
    for pos, it in enumerate(items[:3], start=1):
        it["position"] = pos
        it["length"] = len(it["content"])
        where = "第 %d 条(第 %d 行)" % (pos, it["line"])
        want, cap = COMMITMENT_LABELS[pos - 1], COMMITMENT_CAPS[pos - 1]
        if not it["content"]:
            problems.append("%s内容是空的" % where)
        elif it["length"] > cap:
            problems.append("%s「%s」%d 字,超过上限 %d 字" % (where, want, it["length"], cap))
        bad = plain_text_problems(it["content"])
        if bad:
            problems.append("%s不是纯文字(有%s)" % (where, "、".join(bad)))
        if it["form"] == "numbered":
            label = it["label"]
            if label == want:
                continue
            if label in COMMITMENT_LABELS:
                problems.append("%s的标签「%s」放错了位置:这一条应是「%s」(顺序:%s)"
                                % (where, label, want, " → ".join(COMMITMENT_LABELS)))
            elif any(mark in label for mark in COMMITMENT_OLD_MARKS[pos - 1]):
                it["old_label"] = label
                notes.append("%s用的是旧标签「%s」,照样认;新写的请改成「%s」" % (where, label, want))
            else:
                problems.append("%s的标签「%s」不认识,应是「%s」" % (where, label, want))
        else:
            if it["label"] != CIRCLED[pos - 1]:
                problems.append("%s的圈码应是 %s" % (where, CIRCLED[pos - 1]))
            notes.append("%s用的是旧的圈码写法,照样认;新写的请改成「%d. **%s**:」" % (where, pos, want))
    for it in items[3:]:
        it["position"] = None
        it["length"] = len(it["content"])
    return problems, notes, items


# 研判型资料汇编上卡的那句理由(规格 §③「上卡的字段」· v1.2.0):资料汇编卡「对照判定标准,初步结论」那一行。
GATE_REASON_CAP = 60


def gate_reason_problems(meta):
    """研判型资料汇编的 `gate_verdict.reason`(上卡)→ 毛病列表;综述型、不是资料汇编 → []。字数与三点同一种数法。"""
    if not isinstance(meta, dict) or meta.get("kind") != "dossier" or meta.get("genre") != "argument":
        return []
    verdict = meta.get("gate_verdict")
    reason = verdict.get("reason") if isinstance(verdict, dict) else None
    if not isinstance(reason, str) or not reason.strip():
        return ["研判型资料汇编的 gate_verdict.reason 缺或是空的 —— 它是资料汇编卡上初步结论的理由"]
    out = []
    length = len(reason.strip())
    if length > GATE_REASON_CAP:
        out.append("gate_verdict.reason %d 字,超过上限 %d 字(它直接上资料汇编卡)" % (length, GATE_REASON_CAP))
    bad = plain_text_problems(reason)
    if bad:
        out.append("gate_verdict.reason 不是纯文字(有%s)" % "、".join(bad))
    return out


# ---- 「要聊清的三件事」:工作笔记 clarify.yaml 与任务计划里的对应字段(规格 §⑨-4 · v1.2.1) ----
# 进度面板第一步的三格(报告类型 · 研究范围 · 成稿形式)在任务计划写出来之前就要有内容。那时研究范围、
# 成稿形式取 agent 的工作笔记 clarify.yaml(报告类型取应用自己的报告类型卡记录);有了 task_plan.md
# 就三格都从任务计划取。笔记不是正本:没有版本、确认记录、内容 hash,读不出来应用就当没有。
CLARIFY_FILE = "clarify.yaml"
CLARIFY_KEYS = ("scope", "format")
CLARIFY_CAP = 30
SCOPE_BRIEF_CAP = 30
PANEL_FORMATS = (("html", "网页版"), ("docx", "Word 版"))
PANEL_FORMATS_BOTH = "网页版和 Word 版"   # 汉字与拉丁字母之间留空格,同全书写法
PANEL_GENRES = {"argument": "研判型", "survey": "综述型"}


def read_clarify(path):
    """读 clarify.yaml → (映射或 None, 读不出时的一句说明或 None)。空文件 = 一件都还没答 → ({}, None)。
    说明只说哪一行、哪一类毛病,不引内容(同 load_md)。"""
    try:
        with io.open(path, encoding="utf-8") as f:
            text = f.read().lstrip(chr(0xFEFF))
    except UnicodeDecodeError:
        return None, not_utf8(path)
    try:
        data = yaml.safe_load(text)
    except (yaml.YAMLError, ValueError) as error:
        return None, yaml_problem(text, 0, text, error)
    if data is None:
        return {}, None
    if not isinstance(data, dict):
        return None, "整个文件现在是%s,不是键值映射" % kind_of(data)
    return data, None


def clarify_problems(data):
    """clarify.yaml 读出来的映射 → 毛病列表;空 = 格式对。值为 null 或没有这个键 = 那件还没答,不是毛病。"""
    if not isinstance(data, dict):
        return ["整个文件不是键值映射"]
    out = []
    extra = [k for k in data if k not in CLARIFY_KEYS]
    if extra:
        out.append("有不认识的键 %s(只认 %s)" % (" / ".join(map(str, extra)), " / ".join(CLARIFY_KEYS)))
    for key in CLARIFY_KEYS:
        value = data.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            out.append("%s 应是一句非空的话(现在是%s)" % (key, kind_of(value)))
        elif len(value.strip()) > CLARIFY_CAP:
            out.append("%s %d 字,超过上限 %d 字" % (key, len(value.strip()), CLARIFY_CAP))
    return out


def clarify_items(data):
    """面板的两格 → {"scope": 文字或 None, "format": 文字或 None}。值是 1–30 字的字符串才给(去掉首尾空白),
    否则那一格是 None(按「还没答」显示)。不认识的键不看。"""
    out = {}
    for key in CLARIFY_KEYS:
        value = data.get(key) if isinstance(data, dict) else None
        ok = isinstance(value, str) and value.strip() and len(value.strip()) <= CLARIFY_CAP
        out[key] = value.strip() if ok else None
    return out


def scope_brief_problems(meta):
    """任务计划的 `scope_brief`(上面板的研究范围,§⑨-4)→ 毛病列表;不是任务计划 → []。"""
    if not isinstance(meta, dict) or meta.get("kind") != "task_plan":
        return []
    value = meta.get("scope_brief")
    if not isinstance(value, str) or not value.strip():
        return ["任务计划缺 scope_brief(研究范围的一句话摘要,上进度面板)或它是空的"]
    if len(value.strip()) > SCOPE_BRIEF_CAP:
        return ["scope_brief %d 字,超过上限 %d 字" % (len(value.strip()), SCOPE_BRIEF_CAP)]
    return []


def _constraints(meta):
    constraints = meta.get("constraints") if isinstance(meta, dict) else None
    return constraints if isinstance(constraints, dict) else {}


def _words_part(meta):
    """`constraints.words_max` 是正整数 → `约 <N> 字`;否则 None。"""
    words = _constraints(meta).get("words_max")
    if isinstance(words, int) and not isinstance(words, bool) and words > 0:
        return "约 %d 字" % words
    return None


def _formats_part(meta):
    """`constraints.formats` → 网页版 / Word 版 / 网页版和 Word 版;一个都认不出 → None。"""
    formats = _constraints(meta).get("formats")
    names = [name for key, name in PANEL_FORMATS if isinstance(formats, list) and key in formats]
    if len(names) == len(PANEL_FORMATS):
        return PANEL_FORMATS_BOTH
    return names[0] if names else None


def panel_format(meta):
    """任务计划 → 面板「成稿形式」那一格:`约 <words_max> 字` 与格式名用 ` · ` 连;格式名 html → 网页版、
    docx → Word 版,两种都有写「网页版和 Word 版」。读不出的那一段省掉;两段都没有 → None。"""
    return " · ".join(part for part in (_words_part(meta), _formats_part(meta)) if part) or None


# 首页项目卡那一行(设计稿 D03;规格 §⑨-4 · v1.2.1):`<类型> · <report_name>约 <N> 字 · <格式>`。
REPORT_NAME_CAP = 6
HOME_UNDECIDED = "类型未定"


def report_name_of(meta):
    """任务计划的 `report_name`(成稿叫法)→ 1–6 字的字符串(去掉首尾空白)或 None(没有,或读不出)。"""
    value = meta.get("report_name") if isinstance(meta, dict) else None
    if isinstance(value, str) and value.strip() and len(value.strip()) <= REPORT_NAME_CAP:
        return value.strip()
    return None


def report_name_problems(meta):
    """任务计划的 `report_name`:可缺;写了就得是 1–6 字的字符串 → 毛病列表;不是任务计划 → []。"""
    if not isinstance(meta, dict) or meta.get("kind") != "task_plan" or meta.get("report_name") is None:
        return []
    value = meta["report_name"]
    if not isinstance(value, str) or not value.strip():
        return ["report_name 写了就得是一个词(成稿叫法,用户的说法),现在是%s" % kind_of(value)]
    if len(value.strip()) > REPORT_NAME_CAP:
        return ["report_name %d 字,超过上限 %d 字" % (len(value.strip()), REPORT_NAME_CAP)]
    return []


def home_line(project):
    """首页项目卡那一行的参考实现(D03):类型 · 成稿叫法紧接字数 · 格式,段间 ` · `,缺的段连分隔号一起省掉。

    - 类型:任务计划的 `genre` → 研判型 / 综述型;还没有任务计划、frontmatter 读不出、`genre` 不是这两个值 → 类型未定;
    - 中间一段:`report_name` 紧接 `约 <N> 字`,中间不空格(「内参约 8000 字」);缺哪样就只有另一样;两样都缺就省掉;
    - 格式段:同面板「成稿形式」的格式名。还没有任务计划时整行只有「类型未定」。
    """
    plan = os.path.join(project, "task_plan.md")
    if not os.path.exists(plan):
        return HOME_UNDECIDED
    meta, _, problem = load_md(plan)
    if problem:
        meta = {}
    middle = (report_name_of(meta) or "") + (_words_part(meta) or "")
    parts = [PANEL_GENRES.get(meta.get("genre"), HOME_UNDECIDED), middle or None, _formats_part(meta)]
    return " · ".join(part for part in parts if part)


def clarify_panel(project):
    """「要聊清的三件事」三格的取法(§⑨-4)的参考实现 → {source, report_type, scope, format}。

    - 有 task_plan.md:source = "task_plan",三格都从任务计划取(frontmatter 读不出 → 三格都是 None);
    - 没有:source = "clarify",研究范围与成稿形式取 clarify.yaml(读不出 → None);报告类型是 None ——
      它取应用自己的报告类型卡记录,工具包不知道。
    """
    plan = os.path.join(project, "task_plan.md")
    if os.path.exists(plan):
        meta, _, problem = load_md(plan)
        if problem:
            meta = {}
        brief = meta.get("scope_brief")
        ok = isinstance(brief, str) and brief.strip() and len(brief.strip()) <= SCOPE_BRIEF_CAP
        return {"source": "task_plan", "report_type": PANEL_GENRES.get(meta.get("genre")),
                "scope": brief.strip() if ok else None, "format": panel_format(meta)}
    data, _ = read_clarify(os.path.join(project, CLARIFY_FILE)) if os.path.exists(
        os.path.join(project, CLARIFY_FILE)) else (None, None)
    items = clarify_items(data or {})
    return {"source": "clarify", "report_type": None, "scope": items["scope"], "format": items["format"]}


# ---- 提纲卡与面板上的两个数(规格 §④ · v1.2.1 复核) ----
# outline_check 的 Σwords、cite_check ⑨、应用的提纲卡都用这一份。节点的 level = 标题 # 的个数(2–4);读 json 的
# 提纲没有 level 时当最上一级。
OUTLINE_SOURCELESS_OK = frozenset({"methodology", "synthesis"})


def _node_level(node):
    level = node.get("level") if isinstance(node, dict) else None
    return level if isinstance(level, int) and not isinstance(level, bool) else 2


def _node_words(value):
    """节点的 words → 整数或 None(与 outline_check.parse_words 同一种读法)。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    m = re.search(r"\d+", str(value))
    return int(m.group()) if m else None


def outline_tree(nodes):
    """平铺的节点 → [{node, children}]:一节的小节 = 它后面、直到下一个同级或更高一级的节之前的那些节。"""
    roots, stack = [], []
    for node in nodes:
        item = {"node": node, "level": _node_level(node), "children": []}
        while stack and stack[-1]["level"] >= item["level"]:
            stack.pop()
        (stack[-1]["children"] if stack else roots).append(item)
        stack.append(item)
    return roots


def _subtree_words(item):
    own = _node_words(item["node"].get("words"))
    if own is not None:
        return own
    return sum(_subtree_words(child) for child in item["children"])


def outline_total_words(nodes):
    """合计字数:只加最上一级的节;一节写了 words 就是它连同下面各小节的总数,没写就用小节按同一规则加起来的数。"""
    return sum(_subtree_words(item) for item in outline_tree(nodes))


def outline_unsupported(nodes):
    """没有资料卡片支撑的节 → [节点 id](各级都数):evidence 为空,且 evidence_mode 不是 methodology / synthesis。
    小节的 evidence 是它自己的,不从上一级继承。"""
    out = []
    for node in nodes:
        evidence = node.get("evidence") if isinstance(node, dict) else None
        if evidence:
            continue
        if node.get("evidence_mode") in OUTLINE_SOURCELESS_OK:
            continue
        out.append(node.get("id"))
    return out


# 资料卡片 stance 在界面上的叫法(规格 §② · v1.2.1 复核)。gap 与 wording/glossary.json 一致。
STANCE_LABELS = {"support": "支持判断", "counter": "不支持", "mixed": "背景资料", "gap": "资料缺口"}


# ---- 成稿文件与独立复核结果(规格 §⑨-2 · v1.2.0) ----
DELIVERABLE_EXTS = (".html", ".docx")


def deliverable_paths(project):
    """成稿文件:`out/` 下一层扩展名为 .html / .docx 的文件 → [(相对路径, 绝对路径)],html 在前、docx 在后,
    各自按文件名排序(cite_check 读成稿的顺序)。

    不算:子文件夹里的、以 `~$` 开头的(Word 打开文件时在旁边留的锁文件 —— 它不是 zip,读它的检查会崩;
    把它算进成稿,用户在 Word 里开着成稿时复核结果就永远对不上)、以 `.` 开头的。
    ⭐ 成稿文件的唯一列法:cite_check(经 product_texts_of)、独立复核结果都从这里取。
    """
    out = os.path.join(project, "out")
    if not os.path.isdir(out):
        return []
    found = {ext: [] for ext in DELIVERABLE_EXTS}
    for name in os.listdir(out):
        if name.startswith("~$") or name.startswith("."):
            continue
        path = os.path.join(out, name)
        ext = os.path.splitext(name)[1].lower()
        if ext in found and os.path.isfile(path):
            found[ext].append(name)
    return [("out/" + name, os.path.join(out, name))
            for ext in DELIVERABLE_EXTS for name in sorted(found[ext])]


def file_sha256(path):
    """文件全部字节的 sha256,64 位小写十六进制(规格 §⑨-2「成稿文件的内容 hash」)。"""
    digest = hashlib.sha256()
    with io.open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def deliverable_files(project):
    """当前成稿 → {相对路径: 内容 hash}(规格 §⑨-2)。"""
    return {rel: file_sha256(path) for rel, path in deliverable_paths(project)}


REVIEW_RESULT = os.path.join("review", "result.json")
REVIEW_KIND = "review_result"
REVIEW_FORMAT = 1
REVIEW_SEVERITIES = ("must_fix", "tone_down", "citation_or_format", "checked_ok")
REVIEW_FINDING_KEYS = ("severity", "round", "status", "location", "original", "supported", "fix")
REVIEW_KEYS = ("kind", "format", "round", "reviewed_at", "report", "files", "counts", "findings")
# v1.2.1:用 fullmatch —— `^…$` 配 .match 时 `$` 在结尾的换行符之前也算匹配,64 位十六进制后面多一个换行符原来会被当成合法 hash。
_HEX64_RE = re.compile(r"[0-9a-f]{64}")


def _is_count(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _text(x):
    return isinstance(x, str) and x.strip() != ""


def review_finding_problems(finding, round_no, at):
    """一条复核发现的毛病(规格 §⑨-2 findings 表)→ 列表。`at` 是给人看的位置(「第 3 条」)。"""
    if not isinstance(finding, dict):
        return ["%s不是键值映射" % at]
    out = []
    extra = sorted(set(finding) - set(REVIEW_FINDING_KEYS))
    missing = [k for k in REVIEW_FINDING_KEYS if k not in finding]
    if extra:
        out.append("%s有不认识的键 %s" % (at, " / ".join(map(str, extra))))
    if missing:
        out.append("%s缺键 %s" % (at, " / ".join(missing)))
    sev = finding.get("severity")
    if sev not in REVIEW_SEVERITIES:
        out.append("%s的 severity 只能是 %s" % (at, " / ".join(REVIEW_SEVERITIES)))
    rnd = finding.get("round")
    if not (_is_count(rnd) and 1 <= rnd <= round_no):
        out.append("%s的 round 应是 1 到 %d 的整数" % (at, round_no))
    for key in ("location", "original", "supported"):
        if key in finding and not _text(finding.get(key)):
            out.append("%s的 %s 应是非空文字" % (at, key))
    status, fix = finding.get("status"), finding.get("fix")
    if sev == "checked_ok":
        if status is not None:
            out.append("%s是抽查无误的段,status 应是 null" % at)
        if fix is not None:
            out.append("%s是抽查无误的段,fix 应是 null" % at)
    elif sev in REVIEW_SEVERITIES:
        if status not in ("open", "fixed"):
            out.append("%s的 status 只能是 open / fixed" % at)
        elif status == "fixed" and rnd == round_no:
            out.append("%s是本轮提出的,不会已经改好(fixed 只给早一轮提出、本轮核过的)" % at)
        if not _text(fix):
            out.append("%s缺处置意见 fix(改哪句、改成什么)" % at)
    return out


def review_counts(findings):
    """按发现数四档计数(规格 §⑨-2 counts 表):must_fix 只数还在的(open),其余三档全数。"""
    counts = {key: 0 for key in REVIEW_SEVERITIES}
    for f in findings:
        sev = f.get("severity")
        if sev == "must_fix":
            counts[sev] += 1 if f.get("status") == "open" else 0
        elif sev in counts:
            counts[sev] += 1
    return counts


def review_result_problems(result):
    """`review/result.json` 读出来的对象本身的毛病(规格 §⑨-2)→ 列表;空 = 格式对。不看盘上的成稿。"""
    if not isinstance(result, dict):
        return ["整个文件不是键值映射"]
    out = []
    if result.get("kind") != REVIEW_KIND:
        out.append("kind 不是 %s" % REVIEW_KIND)
    if result.get("format") != REVIEW_FORMAT or isinstance(result.get("format"), bool):
        out.append("format 不是 %d(不认识的格式版本)" % REVIEW_FORMAT)
    extra = sorted(set(result) - set(REVIEW_KEYS))
    if extra:
        out.append("有不认识的键 %s" % " / ".join(map(str, extra)))
    rnd = result.get("round")
    if not (_is_count(rnd) and rnd >= 1):
        out.append("round 应是 ≥1 的整数")
        rnd = None
    at = result.get("reviewed_at")
    if not (isinstance(at, str) and _tz_aware(at)):
        out.append("reviewed_at 应是带时区的 ISO-8601 时间")
    if not _text(result.get("report")):
        out.append("report 应是人读报告的相对路径")
    files = result.get("files")
    if not isinstance(files, dict) or not files:
        out.append("files 应是非空映射 {成稿文件路径: 内容 hash}")
    else:
        for path, digest in files.items():
            if not (isinstance(path, str) and path.startswith("out/") and "/" not in path[4:]):
                out.append("files 里的路径应写成 out/<文件名>")
                break
            if not (isinstance(digest, str) and _HEX64_RE.fullmatch(digest)):
                out.append("files 里的 hash 应是 64 位小写十六进制")
                break
    findings = result.get("findings")
    if not isinstance(findings, list):
        out.append("findings 应是列表")
        findings = None
    elif rnd is not None:
        for i, f in enumerate(findings):
            out.extend(review_finding_problems(f, rnd, "findings 第 %d 条" % (i + 1)))
    counts = result.get("counts")
    if not isinstance(counts, dict) or set(counts) != set(REVIEW_SEVERITIES) \
            or not all(_is_count(v) and v >= 0 for v in counts.values()):
        out.append("counts 应正好有 %s 四个非负整数" % " / ".join(REVIEW_SEVERITIES))
    elif findings is not None and all(isinstance(f, dict) for f in findings) \
            and counts != review_counts(findings):
        out.append("counts 与 findings 数出来的不一致(counts 由脚本按 findings 数,不手写)")
    return out


def _tz_aware(text):
    """带时区的 ISO-8601 字符串?(接受尾随 Z)"""
    t = text.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(t).tzinfo is not None
    except ValueError:
        return False


def review_applies(project, result):
    """复核结果对不对得上当前成稿(规格 §⑨-2)→ (是否对得上, 一句说明)。只看 files;格式问题先用
    review_result_problems 查。"""
    recorded = result.get("files") if isinstance(result, dict) else None
    if not isinstance(recorded, dict):
        return False, "结果里没有成稿文件的 hash"
    current = deliverable_files(project)
    if not current:
        return False, "盘上没有成稿文件(out/ 下的 .html / .docx)"
    gone = sorted(set(recorded) - set(current))
    new = sorted(set(current) - set(recorded))
    changed = sorted(p for p in set(recorded) & set(current) if recorded[p] != current[p])
    if gone or new or changed:
        parts = []
        if changed:
            parts.append("复核之后改过:%s" % "、".join(changed))
        if new:
            parts.append("复核之后多出来:%s" % "、".join(new))
        if gone:
            parts.append("复核时有、现在没有:%s" % "、".join(gone))
        return False, ";".join(parts)
    return True, "对得上当前成稿(%d 个文件)" % len(current)


def commitment_state(items, approved_at):
    """承诺段的三态。

    - `ok`       写了
    - `predates` 交付于约定生效之前,当时没有这个要求
    - `missing`  该写而没写 —— 这一格存在的全部理由
    """
    if items is not None:
        return "ok"
    # v1.1.1 · Tb2:不加引号的 approved_at 是 datetime / date,原来这里切片抛 TypeError。
    # 读不出日期的(数字之类)按「没有交付日期」算 → missing,不猜成 predates。
    day = date_text(approved_at)
    day = day[:10] if isinstance(day, str) else ""
    if day and day <= COMMITMENTS_SINCE:
        return "predates"
    return "missing"


def count_live_cards(cards_dir):
    """cards/ 里非 deprecated 的实际张数;目录不存在返回 None。

    ⚠️ 口径:**排 deprecated**,与交付对接窗口 ⑩ 同源。render_html 的元信息条不排,
    两者当前恰好相等只因为这批卡没有一张带该键 —— 给任一张卡注入 deprecated: true
    实测就分叉(cite_check 22 / render_html 23 / card_check 23)。
    ⭐ 这里是「排 deprecated」这个口径的唯一实现,cite_check ⑩ 与
    pipeline_status 的 facts 都从这里取。
    """
    if not os.path.exists(cards_dir):
        return None
    from card_check import load_cards
    return sum(1 for card, _ in load_cards(cards_dir) if not card.get("deprecated"))



# 文档标题行:正文最前面那个一级标题。二级及以下不是标题而是章节。
DOC_TITLE_RE = re.compile(r"\A\s*#\s+(.+?)\s*$", re.M)


def take_doc_title(body):
    """摘掉正文最前面的一级标题 → (标题或 None, 剩下的正文)。

    ⭐ 稿件首行的 `# 标题` 和 `--title` 是同一件事的两种写法,不是第 1 章:
    把它算成章节会让文档自己的名字出现在目录里,而独立复核早把「顶部多一行
    原始 # 标题」记成缺陷 —— 预期是它不该出现。
    ⚠️ 只认**最前面**且**一级**:如果正文以 `## ` 开头,一个字都不动。否则
    正文中间的任何一级标题都会被当成文档名摘走。
    ⭐ 判定只有一份,两个渲染器共用 —— 各写一份就会出现「HTML 少一行、
    DOCX 没少」这类只在某个格式里可见的差异,而 ④ 只比链接集合看不见它。
    """
    stripped = body.lstrip()
    if not stripped.startswith("#"):
        return None, body
    m = DOC_TITLE_RE.match(body)
    if m is None:  # 以 `##` 开头:不是文档标题
        return None, body
    return m.group(1), body[m.end():].lstrip(chr(10))

# 表格分隔行:`|---|---|`,冒号对齐也算。
TABLE_RULE_RE = re.compile(r"^\|[\s:|\-]+\|$")


def split_table(lines, at):
    """从 lines[at] 起认一张 markdown 表 → (表头, 行列表, 下一行下标);不是表返回 None。

    ⭐ 「这份正本有几张表」是个**口径**,不是格式细节:交付对接窗口 ⑫ 要按格式分别
    数真表格,两个渲染器各自认表的话,数就会分叉 —— 而分叉在「恰好相等」时
    看不出来。识别在这里只有一份,生成各自去做(html 出 <table>,docx 出 w:tbl)。

    ⚠️ 判据是**下一行是分隔行**,不是「本行以 | 开头」。正文里以 | 开头的
    普通行(以及表格续行)不能被当成新表的开始。
    """
    head_line = lines[at].strip()
    if not head_line.startswith("|"):
        return None
    if at + 1 >= len(lines) or not TABLE_RULE_RE.match(lines[at + 1].strip()):
        return None
    head = [c.strip() for c in head_line.strip("|").split("|")]
    i = at + 2
    rows = []
    while i < len(lines) and lines[i].strip().startswith("|"):
        rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
        i += 1
    return head, rows, i


# ── 预注册结论空间的「对应表述」(规格 §①、§③ · v1.2.1 再复核 L3)──────────────────────────
# 资料汇编卡上「对照判定标准,初步结论」由应用按任务计划这张表的第三栏显示(按位置取,不看表头)。
PREREG_PHRASES = ("成立", "部分成立", "不成立")
_PREREG_HEADING_RE = re.compile(r"^#{1,6}[^\n]*\{#preregistration\}[^\n]*$", re.M)
_ANY_HEADING_RE = re.compile(r"^#{1,6}\s", re.M)


def preregistration_problems(body):
    """任务计划 `{#preregistration}` 那一节第一张表的毛病 → 列表。没有这一节 → [](综述型可以没有)。"""
    m = _PREREG_HEADING_RE.search(body)
    if not m:
        return []
    rest = body[m.end():]
    nxt = _ANY_HEADING_RE.search(rest)
    lines = (rest[:nxt.start()] if nxt else rest).split(chr(10))
    table = next((t for t in (split_table(lines, i) for i in range(len(lines))) if t), None)
    allowed = " / ".join(PREREG_PHRASES)
    if table is None:
        return ["「预注册结论空间」这一节没有表:写成 outcome · 判定条件 · 对应表述 三栏的表"]
    if not table[1]:
        return ["「预注册结论空间」的表里一行也没有:每种可能的结论一行,第三栏「对应表述」写 %s" % allowed]
    out = []
    for i, row in enumerate(table[1]):
        phrase = row[2] if len(row) >= 3 else ""
        if phrase not in PREREG_PHRASES:
            out.append("「预注册结论空间」表第 %d 行的「对应表述」%s:只能写 %s(资料汇编卡上的初步结论照这一栏显示)"
                       % (i + 1, "写的是「%s」" % phrase if phrase else "没写", allowed))
    return out


def count_tables(body):
    """正文里的 markdown 表数量。⑫ 与两个渲染器共用同一口径。"""
    lines = body.split(chr(10))
    n = 0
    i = 0
    while i < len(lines):
        found = split_table(lines, i)
        if found is None:
            i += 1
            continue
        n += 1
        i = found[2]
    return n


# ⑫ 的零件:按格式各自数真表格与 markdown 残留。
#
# ⚠️ 为什么不用 python-docx:交付对接窗口现在只靠 zipfile + ElementTree 读 docx
# (见 docx_text)。为了数表格给交付对接窗口加一个运行时依赖,代价不对 —— 窗口跑不起来
# 比窗口少一项更糟。
TBL_TAG = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tbl"
# markdown 残留:这三种在成品里都是「渲染没做完」的可见证据。
#
# ⚠️ 一律**不锚定行首行尾**。docx 的取文把整块表压成一行 —— 实测一份旧
# 交付件:233 根竖线全在一行里,`| --- | --- |` 夹在文字中间。用 `^...$` 的
# 第一版在这份明显坏掉的文档上报了 rule=0,而漏报比漏一项更糟。
RESIDUE_RULE_RE = re.compile(r"\|(?:\s*:?-{3,}:?\s*\|)+")
# `#` 前面不许是字母数字,免得把「C# 语言」当成标题标记。
RESIDUE_HEAD_RE = re.compile(r"(?<![A-Za-z0-9#])#{1,6}\s")


def count_tables_html(path):
    """HTML 成品里的真表格数。"""
    with io.open(path, encoding="utf-8", errors="replace") as f:
        return len(re.findall(r"<table\b", f.read(), re.I))


def count_tables_docx(path):
    """DOCX 成品里的真表格数(w:tbl,含嵌套 —— 与 HTML 数 <table 的口径一致)。"""
    import zipfile
    from xml.etree import ElementTree as ET
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    return sum(1 for _ in root.iter(TBL_TAG))


def markdown_residue(text):
    """成品可读文本里的 markdown 残留 → {rule, bold, heading}。

    ⚠️ 传进来的必须是**已解实体**的文本。HTML 里一根竖线可以写成 `&#124;`,
    不解实体就数不到 —— 而「数不到」在这里等于「报告说干净」。
    `html_text()` 自己解实体,`docx_text()` 读的是 XML 文本节点(ET 已解)。
    """
    return {"rule": len(RESIDUE_RULE_RE.findall(text)),
            "bold": text.count("**"),
            "heading": len(RESIDUE_HEAD_RE.findall(text))}


def product_texts_of(project):
    """成品文本 → [(文件名, 正文)]。`out/` 下的 html 与 docx。

    ⚠️ 这个函数存在的理由是一次真实的失败:`declared_card_counts` 本来就是
    共用实现,但 pipeline_status 曾经给它传空列表,于是遍历成品那一路一次都
    不跑 —— 同一份实现、两种输入、两个答案。面板因此只看见 dossier 一路的
    自述,而交付对接窗口看见全部。
    ⭐ 「不许两份实现」不够,喂给同一份实现的输入也必须同源。
    """
    # v1.2.0:成稿文件只有一种列法(deliverable_paths,规格 §⑨-2)—— 与独立复核结果绑的是同一批;
    # Word 开着成稿时旁边的 `~$` 锁文件不是 zip,原来在这里被当成 docx 去读。
    return [(os.path.basename(rel), html_text(path) if rel.lower().endswith(".html") else docx_text(path))
            for rel, path in deliverable_paths(project)]


def declared_card_counts(product_texts, dossier_meta=None):
    """成品与 dossier 自述的卡数 → [(来源, 值, 原文)]。

    「自述」和「实际」是两个数,面板要并排显示它们的差 —— 那是 ⑩ 的语义,
    不是网关能算的东西。
    """
    claimed = []
    for name, text in product_texts:
        for m in SELF_CARD_RE.finditer(text):
            claimed.append((name, int(m.group(1)), m.group(0)))
    # ⛔ dashboard 写成字符串/列表时原来直接 `.get` 抛 AttributeError,整个交付
    # 检查没有输出(v1.1.0 · C-2)。形状不对就当「没有自述」,形状问题由 ⑧ 报。
    dash = dossier_meta.get("dashboard") if isinstance(dossier_meta, dict) else None
    if isinstance(dash, dict):
        dc = dash.get("cards_count")
        if isinstance(dc, int) and not isinstance(dc, bool):
            claimed.append(("dossier.dashboard", dc, "cards_count: %d" % dc))
    return claimed


def count_inline_citations(body, entries):
    """文内引用数:正文里解析得到号码的占位出现次数,含重复。

    ⚠️ 这个数与 cite_check ③ 打印的数**不是一回事**。③ 检查「文内 ⊆ 参考」,
    PASS 时打印的是去重后的 http 外链数(某次实测 26);这里数的是占位出现次数
    (同一次 62)。两个数都对,回答的是两个问题。

    ⭐ 这个函数是文内引用数的**唯一实现**。render_html 的元信息条与
    pipeline_status 的 facts 都从这里取 —— 卡数已经因为两份实现而有过一次
    口径分歧(cite_check 排 deprecated、render_html 不排,当前恰好相等只是
    因为这批卡没有一张带该键),不再制造第二例。
    """
    ph_no, _ = number_entries(body, entries)
    return sum(1 for ph in PLACEHOLDER_RE.findall(body) if ph in ph_no)


def placeholders(text):
    """抽 [[n]] / [[R###]] 占位 → 原样字符串列表(含重复,按出现顺序)。"""
    return PLACEHOLDER_RE.findall(text)


def resolve_placeholder(ph, entries):
    """占位 → 登记表条目。R### 按 id 匹配;纯数字按 display_no 匹配。找不到 → None。"""
    if ph.startswith("R"):
        for e in entries:
            if e.get("id") == ph:
                return e
    else:
        n = int(ph)
        for e in entries:
            if e.get("display_no") == n:
                return e
    return None


def load_references(path):
    """读 references.yaml → entries 列表。"""
    with io.open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return []
    if isinstance(data, dict):
        return data.get("entries", []) or []
    return data


# ---- HTML / DOCX 链接抽取 ----
HREF_RE = re.compile(r"""<a\s[^>]*href=["']([^"']+)["']""", re.I)


def html_links(path):
    """HTML 外链列表(按出现顺序,含重复);跳过页内锚点 #、mailto。"""
    with io.open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    return [html_entities.unescape(u) for u in HREF_RE.findall(text)
            if u.startswith("http://") or u.startswith("https://")]


def docx_links(path):
    """DOCX 外部超链接列表(document.xml.rels 中 TargetMode=External,
    按 document.xml 引用顺序展开,含重复)。"""
    import zipfile
    from xml.etree import ElementTree as ET
    with zipfile.ZipFile(path) as z:
        rels = z.read("word/_rels/document.xml.rels").decode("utf-8")
        doc = z.read("word/document.xml").decode("utf-8")
    rid2url = {}
    for m in re.finditer(
            r'<Relationship\s[^>]*Id="([^"]+)"[^>]*Target="([^"]+)"[^>]*/?>', rels):
        rid, target = m.group(1), m.group(2)
        if target.startswith("http://") or target.startswith("https://"):
            rid2url[rid] = html_entities.unescape(target)
    out = []
    for m in re.finditer(r'<w:hyperlink\s[^>]*r:id="([^"]+)"', doc):
        if m.group(1) in rid2url:
            out.append(rid2url[m.group(1)])
    return out


def html_text(path):
    """HTML → 可读文本(去 script/style/标签,解实体)。"""
    import html as _html
    with io.open(path, encoding="utf-8", errors="replace") as f:
        t = f.read()
    t = re.sub(r"<(script|style)\b.*?</\1>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    return _html.unescape(t)


def docx_text(path):
    """DOCX → 正文文本(w:t 串接,段落间换行;不含 track-changes 删除文本)。"""
    import zipfile
    from xml.etree import ElementTree as ET
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    paras = []
    for p in root.iter("{%s}p" % ns["w"]):
        runs = []
        for t in p.iter("{%s}t" % ns["w"]):
            # 跳过 w:del 内的文本(删除标记不算正文)
            runs.append(t.text or "")
        paras.append("".join(runs))
    return "\n".join(paras)


# ---- Report ----
class Report:
    def __init__(self, name):
        self.name = name
        self.items = []
        # 事实层:给机器读的结构化事实(状态机当前态、印章、hash…)。
        # 判定(items/verdict)不看它,人读输出也不看它 —— 它只走 --json,
        # 而且**为空就不出现在 JSON 里**,免得改掉其他工具既有的输出形状。
        self.facts = {}

    def add(self, check, status, detail, fix_hint=""):
        assert status in ("PASS", "FAIL", "WARN"), status
        self.items.append({"check": check, "status": status,
                           "detail": detail, "fix_hint": fix_hint})

    def fails(self):
        return [i for i in self.items if i["status"] == "FAIL"]

    def warns(self):
        return [i for i in self.items if i["status"] == "WARN"]

    def verdict(self):
        return "FAIL" if self.fails() else "PASS"

    def to_json(self):
        payload = {"tool": self.name, "verdict": self.verdict(),
                   "items": self.items}
        if self.facts:
            payload["facts"] = self.facts
        return json.dumps(payload, ensure_ascii=False, indent=1, default=_json_default)

    def print_human(self, stream=None):
        stream = stream or sys.stdout
        icon = {"PASS": "PASS", "FAIL": "FAIL", "WARN": "WARN"}
        print("== %s → %s ==" % (self.name, self.verdict()), file=stream)
        for i in self.items:
            line = "[%s] %s: %s" % (icon[i["status"]], i["check"], i["detail"])
            if i["status"] != "PASS" and i["fix_hint"]:
                line += "  → " + i["fix_hint"]
            print(line, file=stream)
        print("-- FAIL %d · WARN %d · 共 %d 项" %
              (len(self.fails()), len(self.warns()), len(self.items)), file=stream)

    def finish(self, json_path=None):
        """打印人读摘要,可选写 JSON;返回 exit code。"""
        self.print_human()
        if json_path:
            with io.open(json_path, "w", encoding="utf-8") as f:
                f.write(self.to_json())
        return 1 if self.fails() else 0


def _json_default(o):
    """`--json` 的最后一道保险:正本里 YAML 读出来的日期 → ISO-8601 字符串,其余 json 不认
    的值 → str。⛔ 一个值写不进 json,整份报告就没了(文件已打开、内容为空)—— 那比报告里
    多一个字符串糟得多(v1.1.1 · Tb2)。"""
    text = date_text(o)
    return text if isinstance(text, str) else str(o)
