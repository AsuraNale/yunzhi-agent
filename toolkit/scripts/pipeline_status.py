# -*- coding: utf-8 -*-
"""pipeline_status — 开场仪式(规格 §⑦):新会话首轮或距上轮 >6h 先跑本脚本。

用法:
  python -X utf8 pipeline_status.py <项目目录> [--json 输出.json]

做三件事:
  1. 读 task_plan.pipeline_status + last_turn_at + stages(v1.1.0,缺省四个环节);
  2. 读本项目流程里的正本(task_plan/dossier/outline,按 stages)approval 印章并复算 hash;
  3. 读 PROGRESS.md(若有)。
输出一行状态 + 不一致项;有不一致 → exit 1(停在窗口上,不推进)。
省掉「拟定提纲」的项目:outline 印章记为 skipped,不要求确认;
状态机里 outlining / gate3_* 不是合法状态(规格 §① stages、§⑧-5)。
"""
import glob
import io
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yaml
from pipeline_lib import (PIPELINE_STATES, STAGES, Report, content_hash,
                          count_inline_citations, count_live_cards, date_text,
                          declared_card_counts, frontmatter_lines, kind_of, load_md,
                          load_references, not_utf8, number_entries,
                          parse_commitments, parse_stages, commitment_state,
                          product_texts_of, stage_docs, stage_states, yaml_lines,
                          yaml_problem)

GATE_DOCS = ("task_plan.md", "dossier.md", "outline.md")

# pipeline_status 走到某站,意味着哪些正本必须已批
REQUIRED_APPROVED = {
    "gate1_approved": ["task_plan.md"],
    "collecting": ["task_plan.md"],
    "gate2_awaiting": ["task_plan.md"],
    "gate2_approved": ["task_plan.md", "dossier.md"],
    "outlining": ["task_plan.md", "dossier.md"],
    "gate3_awaiting": ["task_plan.md", "dossier.md"],
    "gate3_approved": ["task_plan.md", "dossier.md", "outline.md"],
    "drafting": ["task_plan.md", "dossier.md", "outline.md"],
    "verifying": ["task_plan.md", "dossier.md", "outline.md"],
    "delivered": ["task_plan.md", "dossier.md", "outline.md"],
}


FIX_UNREADABLE = ("改好报出来的那一行再跑;只动了 revision_log、updated_at 这类留痕字段时,"
                  "改好后原来的确认照样有效(它们不进内容 hash)")


def _locator(path):
    """→ line(*键路径):那个键 / 条目在文件第几行,找不到(锚点、合并键带进来的……)给 None。
    只在第一次要用时才再读一遍 frontmatter —— 多数正本一条都用不上。"""
    cache = []

    def line(*keys):
        if not cache:
            cache.append(frontmatter_lines(path))
        return cache[0].get(keys)
    return line


def _paren(n):
    return "(第 %d 行)" % n if n else ""


def _on(n):
    return "第 %d 行的 " % n if n else ""


def _log_entries(log, line=lambda *keys: None):
    """revision_log → ([(原序号, 条目)] 只含键值映射的条目, 形状问题的一句说明或 None)。

    v1.1.2 · T3-1:原来直接 `enumerate(log)` 再 `.get("at")`,写成散文字符串、映射、整数,
    或条目不是键值映射(散文条目、嵌套列表)时崩。形状的判法与 stamp.py 落章守卫相同
    (规格 §⑧-1 第 3 类);这里只报 WARN(留痕读不出,但不挡推进),落章时 stamp.py 会拒绝。
    ⛔ 说明只说它现在是什么、在第几行,不引内容(v1.1.2 复核):原来引 repr 的前 40 个字,把记录里的
    人和原话(approved_by、approval_quote……)带进报告,截断的片段应用遮不住。`line` 见 _locator。
    """
    if log is None:
        return [], None
    if not isinstance(log, list):
        return [], "revision_log 现在是%s,不是列表%s" % (kind_of(log), _paren(line("revision_log")))
    entries = [(i, e) for i, e in enumerate(log) if isinstance(e, dict)]
    bad = [(i, e) for i, e in enumerate(log) if not isinstance(e, dict)]
    if not bad:
        return entries, None
    i, e = bad[0]
    more = ";这样的共 %d 条" % len(bad) if len(bad) > 1 else ""
    return entries, ("revision_log 第 %d 条现在是%s,不是键值映射%s%s"
                     % (i + 1, kind_of(e), _paren(line("revision_log", i)), more))


def _epoch(v):
    """ISO8601 → epoch 秒;解析不了返回 None(不因此报错)。"""
    if not v:
        return None
    try:
        t = datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return t.timestamp()
    except Exception:
        return None


def check(project, rep):
    p = lambda *a: os.path.join(project, *a)
    tp = p("task_plan.md")
    if not os.path.exists(tp):
        rep.add("task_plan", "FAIL", "task_plan.md 不存在", "先过对接窗口1建 task_plan")
        rep.facts = {"pipeline_status": None, "stamps": {},
                     "hours_since_last_turn": None, "progress_first_line": None,
                     "stages": None, "stages_source": None, "stages_error": None}
        return None
    meta, _, problem = load_md(tp)
    if problem:
        # ⛔ v1.1.2 · T3-1:frontmatter 读不出来(YAML 写坏、日期不存在、不是键值映射)。原来在
        # read_md 里崩掉,一项检查都没跑,--json 也没有。facts 照「task_plan.md 不存在」那一支的
        # 形状给,但流程环节是「读不出」不是「缺省」:按 facts 的约定 stages 记 None、原因进 stages_error。
        rep.add("状态机", "FAIL",
                "task_plan.md: frontmatter 读不出来 —— %s。进度、流程环节、确认记录都读不到" % problem,
                FIX_UNREADABLE)
        rep.facts = {"pipeline_status": None, "stamps": {},
                     "hours_since_last_turn": None, "progress_first_line": None,
                     "stages": None, "stages_source": None,
                     "stages_error": "task_plan.md 的 frontmatter 读不出来:%s" % problem}
        return None
    # v1.1.0 · 流程可调(V8):本项目有哪几个环节。读不出来就按四个环节全查(最严),
    # 并且报 FAIL —— 「读不出」和「省掉了」后果相反,不能让前者冒充后者。
    stages, stages_err = parse_stages(meta)
    if stages_err:
        rep.add("流程环节", "FAIL", "task_plan.md 的 %s" % stages_err,
                "stages 写成列表,如 [task, sources, outline, delivery];本版只允许省掉 outline")
    effective = stages if stages is not None else list(STAGES)
    in_flow = stage_docs(effective)
    status = meta.get("pipeline_status")
    if status not in PIPELINE_STATES:
        rep.add("状态机", "FAIL", "pipeline_status=%r 非法" % status,
                "取值:%s" % " → ".join(PIPELINE_STATES))
        status = None
    elif status not in stage_states(effective):
        rep.add("状态机", "FAIL",
                "pipeline_status=%s 属于本项目流程里没有的环节(stages = %s)" % (status, effective),
                "本项目的合法状态:%s" % " → ".join(stage_states(effective)))
    stamps = {}
    # 面板要的印章明细。人读那行只用得上 status,但 hash 与批准时刻是面板要显示的
    # 东西 —— 从前它们在这里算完就被扔了。
    facts_stamps = {}
    for name in GATE_DOCS:
        fp = p(name)
        if name not in in_flow:
            # 本项目省掉了这个环节:不要求确认,也不因为它缺席而报错。
            stamps[name] = ("skipped", None)
            facts_stamps[name] = {"status": "skipped", "approved_at": None,
                                  "approved_hash": None, "current_hash": None}
            if os.path.exists(fp):
                rep.add("流程环节", "WARN",
                        "%s 在盘上,但本项目流程没有这个环节(stages = %s)—— 它不需要确认,"
                        "也不进交付检查" % (name, effective),
                        "要用提纲就把 outline 加回 stages 并重新确认任务计划;不用就删掉这份文件")
            continue
        if not os.path.exists(fp):
            stamps[name] = ("missing", None)
            facts_stamps[name] = {"status": "missing", "approved_at": None,
                                  "approved_hash": None, "current_hash": None}
            continue
        m, body, problem = load_md(fp)
        if problem:
            # ⛔ v1.1.2 · T3-1:frontmatter 读不出来,确认记录也就读不到 —— 读不出的确认记录不算
            # 确认(同 approval 不是键值映射,规格 §⑧-7):记 draft,报 FAIL,这份正本的其余检查跳过。
            stamps[name] = ("draft", None)
            facts_stamps[name] = {"status": "draft", "approved_at": None,
                                  "approved_hash": None, "current_hash": None}
            rep.add("印章", "FAIL",
                    "%s: frontmatter 读不出来 —— %s。确认记录读不到,按未确认处理" % (name, problem),
                    FIX_UNREADABLE)
            continue
        ap = m.get("approval")
        if ap is not None and not isinstance(ap, dict):
            # ⛔ v1.1.1 · Tb2:`approval: approved` 写成标量(或列表)时原来 `.get` 抛
            # AttributeError —— 开场仪式崩掉,一项检查都没跑,--json 也没有。
            # 读不出的确认记录**不算确认**:按缺 status 的缺省读法记 draft,并报 FAIL。
            rep.add("印章", "FAIL",
                    "%s: approval 不是键值映射(现为 %s %s)—— 读不出确认记录,按未确认处理"
                    % (name, type(ap).__name__, repr(ap)[:60]),
                    "确认记录只由 stamp.py 写,不要手改:跑 stamp.py %s --invalidate --why "
                    "\"确认记录写坏了\" 恢复成等确认,再重过该窗口" % name)
            ap = {}
        ap = ap or {}
        st = ap.get("status") or "draft"
        current = content_hash(m, body)
        if st == "approved" and ap.get("approved_hash") != current:
            stamps[name] = ("stale", ap.get("approved_at"))
            rep.add("印章", "FAIL",
                    "%s 批准后内容已变(hash 不匹配)→ 视为 stale" % name,
                    "重过该窗口;或回滚改动")
        else:
            stamps[name] = (st, ap.get("approved_at"))
        # v1.1.1 · Tb2:不加引号的时间 YAML 读成日期,按字符串归一(json 不认日期)。
        facts_stamps[name] = {"status": stamps[name][0],
                              "approved_at": date_text(ap.get("approved_at")),
                              "approved_hash": date_text(ap.get("approved_hash")),
                              "current_hash": current}
        # v1.0.2(实证):规格 §0 要求 revision_log,而此前无任何脚本读它。
        # ⛔ 原来是裸 `int(m.get("version") or 1)`:version 写成 'v2' 之类,
        # pipeline_status 直接抛 ValueError —— 而它是过窗口前第一个跑的东西,
        # 崩了就没有任何一项检查跑过(对抗核查 2026-09-04)。
        line = _locator(fp)
        log_entries, log_problem = _log_entries(m.get("revision_log"), line)
        if log_problem:
            rep.add("留痕", "WARN", "%s: %s" % (name, log_problem),
                    "写成列表,每条一个 {v, at, who, what, why};这种写法 stamp.py 落章时会拒绝(规格 §⑧-1)")
        _ver = m.get("version")
        if not (isinstance(_ver, int) and not isinstance(_ver, bool)):
            rep.add("留痕", "WARN",
                    "%s: version 不是整数(现为 %r)" % (name, _ver),
                    "改成整数;stamp.py --approve / --invalidate 也会拒绝这种正本")
        elif _ver > 1 and not log_problem and not log_entries:
            rep.add("留痕", "WARN",
                    "%s: version=%s 但 revision_log 为空" % (name, m.get("version")),
                    "改过正本就该有 {v,at,who,what,why};作废印章走 stamp.py --invalidate --why 会自动记")
        # v1.0.3(2026-08-30):正本时间戳是**模型手写**的,实测出现过物理上不可能的未来时间 ——
        #   某份 outline.md 写入发生在 17:30:35,而写进去的 updated_at 是 17:40:00(早 9m25s);
        #   另一份正本的 revision_log 记 19:00 而文件最后写入 18:42。
        #   机器取不到「模型以为的现在」,但能验一条**物理硬约束**:
        #   声称的更新时刻不可能晚于文件被写入的时刻。判 WARN 不 FAIL:审计轨迹失真但不阻塞交付。
        #
        #   ⚠️ **覆盖面(别夸大这条检查证明了什么)**:mtime 是「最后一次写入」,
        #   所以只抓得到「写完之后没再被覆盖」的失真。上面那份 outline.md 实测:
        #   写 updated_at=17:40 的那次 write 发生在 17:30(确实是未来时间),
        #   但对接窗口3 盖章时 stamp.py 在 18:17 又写了一次 → mtime 变新 → 本检查**看不见它**。
        #   实测能抓到的是另一个项目的 dossier(updated_at 晚于 mtime 23 分钟)。
        #   要抓全须在**写入那一刻**校验(即改 agent 的写法或加写入包装),不在本检查范围。
        try:
            mt = os.path.getmtime(fp)
        except OSError:
            mt = None
        # ⛔ v1.1.2 复核:下面两条说明只说哪个字段、在第几行,不引写的值(读不出的那个可能是任何文字)。
        pairs = [("updated_at", ("updated_at",), m.get("updated_at"))]
        pairs += [("revision_log[%d].at" % idx, ("revision_log", idx, "at"), rv.get("at"))
                  for idx, rv in log_entries]
        for label, keys, val in pairs:
            if val is None or val == "":
                continue
            ts = _epoch(val)
            if ts is None:
                # v1.1.2 · T3-1:读不出的时间(加了引号的不存在日期、不是 ISO 8601 的写法)原来
                # 一声不吭地跳过,下面那条「晚于写入时刻」的检查也就看不见它。照实报。
                rep.add("留痕", "WARN",
                        "%s: %s%s 读不出时间(日期不存在,或不是 ISO 8601 写法)" % (
                            name, _on(line(*keys)), label),
                        "写成机器取的 ISO 8601 时刻,带时区,如 2026-09-29T14:03:11-04:00")
            elif mt is not None and ts > mt + 60:
                rep.add("时间戳", "WARN",
                        "%s: %s%s 比文件写入时刻晚 %.0f 分钟(物理上不可能)" % (
                            name, _on(line(*keys)), label, (ts - mt) / 60),
                        "时间戳应取机器时间,别手写;不阻塞,但这条审计轨迹已失真")
    if status in REQUIRED_APPROVED:
        for name in REQUIRED_APPROVED[status]:
            if name not in in_flow:
                continue   # 省掉的环节没有确认记录可要
            st = stamps.get(name, ("missing", None))[0]
            if st != "approved":
                rep.add("一致性", "FAIL",
                        "pipeline_status=%s 但 %s 印章=%s" % (status, name, st),
                        "状态机与印章矛盾:停在窗口,向用户报告后由人裁决")
    hours = None
    lt = meta.get("last_turn_at")
    if lt:
        lt = date_text(lt)
        try:
            t = datetime.fromisoformat(str(lt))
        except ValueError:
            t = None
            rep.add("时间", "WARN", "last_turn_at=%r 无法解析" % (lt,), "用 ISO8601 带时区")
        # ⛔ v1.1.1 · Tb2(同类):不带时区的时刻(含不加引号的 `2026-09-29`)原来拿去减
        # 带时区的「现在」,TypeError 崩。算不出就说算不出,不替它猜时区。
        if t is not None and t.tzinfo is None:
            rep.add("时间", "WARN", "last_turn_at=%r 不带时区,算不出距上轮多久" % (lt,),
                    "用 ISO8601 带时区")
        elif t is not None:
            hours = (datetime.now(t.tzinfo) - t).total_seconds() / 3600
    line = "状态:%s" % (status or "?")
    line += " · 印章 " + " ".join("%s=%s" % (n.split(".")[0], s[0])
                                  for n, s in stamps.items())
    if hours is not None:
        line += " · 距上轮 %.1fh" % hours
    prog = p("PROGRESS.md")
    first = None
    if os.path.exists(prog):
        try:
            with io.open(prog, encoding="utf-8") as f:
                first = f.readline().strip()
        except UnicodeDecodeError:
            # v1.1.2 复核:GBK 之类的进度笔记原来在这里崩,没有 --json。
            first = None
            rep.add("PROGRESS", "WARN", "PROGRESS.md 不是 UTF-8 编码,读不出来",
                    "用 UTF-8 另存;开场仪式要读它的第一行")
        else:
            line += " · PROGRESS: %s" % first[:60]
    else:
        rep.add("PROGRESS", "WARN", "PROGRESS.md 不存在", "建一个,开场仪式要读")
    rep.facts = {"pipeline_status": status, "stamps": facts_stamps,
                 "hours_since_last_turn": hours, "progress_first_line": first,
                 "counts": counts_of(p, rep),
                 "commitments": commitments_of(p, effective),
                 # v1.1.0:本项目的环节。读不出时为 None,原因在 stages_error。
                 "stages": stages,
                 "stages_source": ("task_plan" if isinstance(meta, dict)
                                   and meta.get("stages") is not None else "default"),
                 "stages_error": stages_err}
    return line


def _card_problems(cards_dir):
    """cards/ 里读不出来的卡 → [(文件名, 一句说明)]。数卡之前先看一遍,好说清是哪一张。"""
    if not os.path.isdir(cards_dir):
        return []
    out = []
    for fn in sorted(os.listdir(cards_dir)):
        if fn.endswith(".md"):
            problem = load_md(os.path.join(cards_dir, fn))[2]
            if problem:
                out.append((fn, problem))
    return out


def _references_problem(entries, path):
    """references.yaml 的 entries 不是「键值映射的列表」→ 一句说明;没毛病 → None。
    只说它现在是什么、在第几行,不引内容(v1.1.2 复核)。行号要再读一遍文件,只在有毛病时读;整个文件
    就是那张列表(没写 `entries:`)时,条目的键路径没有 entries 这一层。"""
    if isinstance(entries, list) and all(isinstance(e, dict) for e in entries):
        return None
    with io.open(path, encoding="utf-8") as f:
        text = f.read()
    lines = yaml_lines(text, 0, text)
    base = ("entries",) if ("entries",) in lines else ()
    if not isinstance(entries, list):
        return "%s现在是%s,不是列表%s" % ("entries " if base else "整个文件", kind_of(entries),
                                       _paren(lines.get(base) if base else None))
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            return "%s第 %d 条现在是%s,不是键值映射%s" % ("entries " if base else "", i + 1, kind_of(e),
                                                _paren(lines.get(base + (i,))))
    return None


def counts_of(p, rep=None):
    """面板计数区的四个数,全部由真源算出。

    ⛔ 每个数都在 pipeline_lib 里有唯一实现,这里只调用不重算 —— 卡数已经因为
    两份实现有过一次口径分歧,不再制造第二例。拿不到的返回 None,由面板显示
    「未产出」,绝不用 0 顶替:0 是「数出来是零」,None 是「没能数」。
    v1.1.2 · T3-1(含复核):卡片或登记表读不出来(YAML 写坏、日期不存在、不是 UTF-8、frontmatter 是列表、
    entries 不是键值映射的列表),那个数就是 None,并报一条 WARN 说清是哪个文件 —— 不许让开场仪式整个崩掉。
    卡片的其余毛病由 card_check 报,登记表的由 cite_check 报。
    """
    def warn(detail, hint):
        if rep is not None:
            rep.add("留痕", "WARN", detail, hint)

    cards_dir = p("cards")
    live = None
    broken = _card_problems(cards_dir)
    if broken:
        fn, problem = broken[0]
        more = ";这样的卡共 %d 张" % len(broken) if len(broken) > 1 else ""
        warn("cards/%s: frontmatter 读不出来 —— %s。资料卡片张数没能数%s" % (fn, problem, more),
             "改好这张卡再跑;card_check 会报卡片的其余问题")
    else:
        try:
            live = count_live_cards(cards_dir)
        except (yaml.YAMLError, ValueError, AttributeError, TypeError):
            live = None
    # ⚠️ 空列表在这里曾经让 declared_card_counts 只看见 dossier 一路自述,
    # 而交付对接窗口看见全部 —— 同一份实现、两种输入、两个答案。成品文本与
    # cite_check 同源。
    product_texts = product_texts_of(p())
    dpath = p("dossier.md")
    dmeta = load_md(dpath)[0] if os.path.exists(dpath) else None
    declared = declared_card_counts(product_texts, dmeta)

    cited = registered = inline = None
    refs = p("references.yaml")
    entries = None
    if os.path.exists(refs):
        try:
            entries = load_references(refs)
        except (yaml.YAMLError, ValueError) as error:
            # ⛔ v1.1.2 复核:不引异常自己的说明 —— PyYAML / int() 的说明会带原文(`invalid literal for
            # int() ... '<原文>'`、锚点名),还带文件的完整路径。只说第几行、哪一类毛病。
            if isinstance(error, UnicodeDecodeError):
                said = not_utf8(refs)
            else:
                with io.open(refs, encoding="utf-8") as f:
                    text = f.read()
                said = yaml_problem(text, 0, text, error)
            warn("references.yaml: 读不出来 —— %s。参考文献的几个计数没能数" % said,
                 "改好再跑;cite_check 会报具体是哪一条")
        else:
            problem = _references_problem(entries, refs)
            if problem:
                warn("references.yaml: %s。参考文献的几个计数没能数" % problem,
                     "每一条写成键值映射(id、title、url……),见规格 §⑤")
                entries = None
    if entries is not None:
        registered = len(entries)
        drafts = sorted(glob.glob(p("drafts", "*.md")))
        if drafts:
            _, body, _ = load_md(drafts[0])
            inline = count_inline_citations(body, entries)
            # 「被引用」与「登记」是两个数,面板并排给两格:只给一个,读的人
            # 无从知道有多少条登记了却没用上。
            cited = len(number_entries(body, entries)[1])
    return {
        "cards_actual": live,
        # ⑩ 的语义:自述与实测的差。网关只渲染,不比较。
        "cards_declared": [{"source": n, "value": v, "raw": raw} for n, v, raw in declared],
        "references_cited": cited,
        "references_registered": registered,
        "inline_citations": inline,
    }

# 四组承诺各自落在哪个文件。⚠️ 交付对接窗口那一组独占一个文件、整篇就是承诺块,
# 与前三组的「正本里的一节」形式不同 —— 少了这一行,面板会安静地只显示 3/4 组。
COMMITMENT_SOURCES = [
    ("task_plan.md", ("task_plan.md",), False),
    ("dossier.md", ("dossier.md",), False),
    ("outline.md", ("outline.md",), False),
    ("delivery", ("library", "delivery_commitments.md"), True),
]


def commitments_of(p, stages=STAGES):
    """四组承诺 → [{record, state, items}]。

    ⛔ 状态不看「段落在不在」而看交付日期:早于约定生效日的项目当时没有这个
    要求,把它标成缺陷等于把历史遗留和真漏写混为一谈,而这一格存在的理由
    恰恰是抓后者。
    v1.1.0:本项目流程里没有的环节,那一组记 `skipped`,不算缺陷。
    """
    approved = None
    plan = p("task_plan.md")
    if os.path.exists(plan):
        # approval 不是映射(v1.1.1 · Tb2)= 没有交付日期;形状问题由印章那一项报。
        # frontmatter 读不出来(v1.1.2 · T3-1)同样没有日期,由状态机 / 印章那一项报。
        ap = load_md(plan)[0].get("approval")
        approved = ap.get("approved_at") if isinstance(ap, dict) else None
    in_flow = stage_docs(stages)
    groups = []
    for record, parts, whole in COMMITMENT_SOURCES:
        if record.endswith(".md") and record not in in_flow:
            groups.append({"record": record, "state": "skipped", "items": []})
            continue
        path = p(*parts)
        items = None
        if os.path.exists(path):
            items = parse_commitments(load_md(path)[1], whole=whole)   # 正文不经 YAML,读得出
        groups.append({"record": record,
                       "state": commitment_state(items, approved),
                       "items": items or []})
    return groups



def main(argv):
    HELP_FLAGS = ("-h", "--help")
    if any(f in argv for f in HELP_FLAGS):
        print(__doc__)
        return 0
    if len(argv) < 2:
        print(__doc__)
        return 2
    json_out = None
    if "--json" in argv:
        i = argv.index("--json")
        json_out = argv[i + 1]
        del argv[i:i + 2]
    rep = Report("pipeline_status")
    line = check(argv[1], rep)
    if line:
        print(line)
    if not rep.fails():
        rep.add("一致性", "PASS", "状态机·印章·hash 相互一致,可推进")
    code = rep.finish(json_out)
    if code:
        print("⛔ 不一致:停在窗口上,先向用户报告,不推进。")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
