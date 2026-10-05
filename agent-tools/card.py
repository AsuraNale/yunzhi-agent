# -*- coding: utf-8 -*-
"""card — 选项卡:出卡(prepare)、记回答(answer)、记用户消息(note-user)。

用法(在工作区根目录下跑;$PY = Windows 上 py -X utf8,macOS / Linux 上 python3 -X utf8):
  $PY agent-tools/card.py prepare <kind> "<项目名>" [--fields-file <json 文件>] [--outside <路径> | --outside none]
                                  [--mode text] [--pane-open] [--id <卡号>]
      kind:report_type · task_plan · dossier · outline · delivery · decision · flow_change(规格 §⑦)
            pick_project(不带项目名:有几个项目时请用户选一个)
      确认类的卡(任务计划 / 资料汇编 / 提纲 / 交付 / 改流程)顺手把那份材料生成好读的网页(view.py),
      换进右侧那一页(右侧.html,等于 view.py --pane);卡上「依据的材料」附点开看的地址(view_url)。
      --pane-open:这个对话里已经把右侧挂上了,卡上才写「右侧已打开」(命令行、WorkBuddy 没有右侧,不带)。
      --mode text:选项框工具用不了的时候,用文字卡重新准备这张卡(文字卡回数字才算选第几项;
      文字卡上不放 file:/// 地址,写「全文在项目文件夹的……里」)。
      输出 JSON:message(卡前那条消息)· ask(request_user_input 的参数)· fallback_text(文字卡)· mode · view_url · next;
      或者 refuse(一句改法)+ problems。退出码:0 出卡 · 1 退回 · 2 用法错 · 3 脚本出错。
  $PY agent-tools/card.py answer "<项目名>" --card <卡号> --answer-file <文件> [--snapshot-dir <对话的可视化目录>]
      回答原文:request_user_input 的返回,或文字卡模式下用户回的那条消息(先写进文件)。认得的形状:
        {"answers":{"<卡号>":{"answers":["<选项名>"]}}}          选了一项
        {"answers":{"<卡号>":{"answers":["<写的话>"]}}}          写了一段话:开头是选项的数字或选项名(「2」「2，另外……」)
                                                                = 选这一项(+ 意见);别的话只是意见(确认类 = 先不确认;报告类型 / 决定 = 没选)
        {"answers":{"<卡号>":{"answers":["<选项名>","user_note: <写的话>"]}}}   选了一项又写了意见(终端版才有)
        {"answers":{}}                                          点了跳过,或者桌面版太久没操作被自动收起(两种一样)
      文字卡回的消息同样认:「1」「选 2」「第 2 个」「1，……」、选项名(后面也可以隔着标点接一句话)。
      卡收起来之后、或者只收到意见没选选项之后(第七轮),用户回的消息认得出是这张卡的某一项,这张卡还能回答一次
      (只此一次;这期间没出过别的卡;确认类的材料没变;守门脚本在跑时,要是用户的原话 —— 只写了意见的,
      要是意见之后说的;note-user 会提示)。
      其余形状原样存下,result 是 unclear。
      输出 JSON:result(approved / not_approved / selected / note / skipped / unclear / changed / error)· next · say ·
      say_revise(只写了意见、助手照意见改材料时说的那一句;不改就说 say,里面告诉用户回哪个数字)·
      pane_url(答完就把右侧那一页换回进度页,等于 progress.py --page --pane)·
      visualize(带了 --snapshot-dir:这一轮最后一条回复里单独一行贴它)。
      确认类的卡点了「确认」:由本脚本调 stamp.py --approve 写确认记录、按流程推进状态。
      回答过(含认不出、材料变了、写记录失败)的卡就关了:要再问,重新 prepare。
      第六轮:守门脚本在跑(当前会话 CODEX_THREAD_ID 有它的记录,见 sessions.py)时,对话里的回答要和这张卡出了之后
      它记下的某条用户原话一字不差,选项框交回的要和它记下的原文一致;对不上 → ok false + refuse(退出码 1),卡照旧开着。
  $PY agent-tools/card.py note-user "<项目名>" --file <文件>      (或 --stdin:从标准输入读 UTF-8)
      把用户的一条消息逐字记进 records/messages.jsonl(卡上引用用户的话,只认这里和委托原话)。
      只从文件或标准输入读:原话写在命令行里,PowerShell 会把 $… 和反引号悄悄展开、把中文引号当语法。
  $PY agent-tools/card.py mode ["<项目名>"]
      现在是原生选项卡还是文字卡模式。

文字卡模式:环境变量 YUNZHI_CARDS=text,或项目文件夹里有 .text-cards 文件,或 projects/ 下有 .text-cards 文件,
或者 prepare 带 --mode text。--fields-file / --answer-file / --file 读完就删(原文已进记录);文件用 UTF-8 保存。
"""
import argparse
import hashlib
import io
import json
import os
import re
import shutil
import sys
import time
import unicodedata

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yzlib  # noqa: E402
from yzlib import pl, UsageError  # noqa: E402
import sessions  # noqa: E402
import wording_check  # noqa: E402

KINDS = ("report_type", "task_plan", "dossier", "outline", "delivery", "decision", "flow_change", "pick_project")
CONFIRM_KINDS = ("task_plan", "dossier", "outline", "delivery", "flow_change")
DOC_OF = {"task_plan": "task_plan.md", "dossier": "dossier.md", "outline": "outline.md"}
AWAITING = {"task_plan": "gate1_awaiting", "dossier": "gate2_awaiting", "outline": "gate3_awaiting"}
CAPS = {"report_name": 6, "thesis": 40, "report_why": 80, "question": 40, "decision_why": 80,
        "option_label": 20, "option_effect": 40, "changes": pl.FLOW_CHANGE_CAP, "counter_reason": 40}

# ---- 卡上的固定文字(确认卡文字模板第 2 稿;与客户端 app-core/src/cards.mjs 的 CARD_TEXT 同一份)。
#      卡上提到助手一律写「助手」,不写「我」(页脚是「由助手写下确认记录」,一张卡一个口吻) ----
BADGE = "等你决定"
FOOTER_CONFIRM = "你点「确认」后，由助手写下确认记录。"
FOOTER_DELIVERY = "你点「交付」后，由助手写下交付记录。"
# 文字卡没有按钮可点:确认 / 交付总是第 1 项,页脚照文字卡的样子说
TEXT_FOOTERS = {FOOTER_CONFIRM: "你回复 1 之后，由助手写下确认记录。",
                FOOTER_DELIVERY: "你回复 1 之后，由助手写下交付记录。"}
MATERIALS = "依据的材料"
POINTS = "请你重点看这三点"
POINT_LABELS = ("改动内容", "最薄弱的依据", "最可能出错的地方")
OUTSIDE = "这一轮读过的项目以外的文件（助手自报）"
OUTSIDE_GENERIC = "项目外的一份材料"
OUTSIDE_NOTE = "这份清单是助手自己报的，可能不全。"
FALLBACK_TAIL = "回复选项前面的数字（例如 1）；想补一句意见，接在数字后面写（例如：2，时间段改成从 2021 年开始）。也可以只写意见；回「跳过」就先放一边。"
# 第五轮 H2:桌面版的原生卡没人回答,大约 3 分钟会被自动收起(和点「跳过」交回一样的东西)。卡前那条消息里先说一声
NATIVE_COLLAPSE_NOTE = "卡片两三分钟没操作会自动收起；收起了也不要紧，直接回复选项前面的数字就行。"
SKIP_WORDS = ("跳过", "先跳过")
# 第五轮 H1:「数字或选项名 + 一句话」= 选这一项 + 意见(文字卡、原生卡一样)。
# 数字只认开头那一个(1–9,全角也认),前面可以有「选」「选择」「第」,后面可以有「个」「项」「号」;
# 后面紧跟数字的不算(「2023年的数据也要」),隔开数字和意见的只认这些标点和空白
LEAD_NUMBER_RE = re.compile("\\A\\s*(?:选择|选|第)?\\s*([1-9%s-%s])(?![0-9%s-%s])(?:\\s*(?:个|项|号))?"
                            % (chr(0xFF11), chr(0xFF19), chr(0xFF10), chr(0xFF19)))
NOTE_SEPARATORS = "，,、.。：:；;）)" + chr(0xFF0E)
# 第八轮:全角的「，」「；」「：」不会出现在数里,后面跟什么都算隔开(「1，2021 年的数据也要」= 第 1 项 + 意见)。
# 半角「,」后面紧跟数字照旧不算(「1,000 辆」千分位),「、」后面紧跟数字照旧不算(「1、2 都要」是列举),「.」同理(「1.5 倍」)
HARD_SEPARATORS = "，；："
# 空白后面紧跟这些字,说的是数量(「3 个方面都要写」「1 年」),不是「选第几项」
UNIT_AFTER = "个项条点种处年月日号位名张份次成倍万亿千百十%％"
# 确认类的卡上写了这种话:像是想确认,但只有点了确认那一项才算(用户的话都是意见)
YES_WORDS = ("确认", "可以", "好", "好的", "行", "同意", "没问题", "定了", "就这样", "ok", "OK", "Ok", "yes", "可以了", "确定")
REPORT_OPTIONS = (
    ("judge", "下判断（研判型）", "找资料之前，先把「成立 / 部分成立 / 不成立」的判定标准写进任务计划。结论有证据撑着；代价是正反两面的资料都要找。"),
    ("survey", "梳理情况（综述型）", "只梳理现状和各方观点，不下结论。仍会写明核心问题和数据口径。"),
    ("undecided", "先不定", "助手先按「下判断」写任务计划，你确认任务计划时再改也行。"),
)
CONFIRM_OPTIONS = {
    "task_plan": (("confirm", "确认，开始收集资料", "任务计划按这一版定下，以后要改会再请你确认一次。"),
                  ("not_yet", "先不确认", "助手按你的意见修改，改好再请你确认。")),
    "dossier_outline": (("confirm", "确认，开始拟提纲", "资料汇编按这一版定下，写作只用这里面的结论。"),
                        ("not_yet", "先不确认", "告诉助手要补什么资料，或者改哪条结论。")),
    "dossier_draft": (("confirm", "确认，开始写", "资料汇编按这一版定下，写作只用这里面的结论。"),
                      ("not_yet", "先不确认", "告诉助手要补什么资料，或者改哪条结论。")),
    "outline": (("confirm", "确认，开始写", "提纲按这一版定下。写的时候要大改结构，会先问你。"),
                ("not_yet", "先不确认", "接着改提纲。")),
    "delivery": (("deliver", "交付成稿", "交付后不能撤回。这次用到的来源和资料缺口会存进项目，留给以后的课题。"),
                 ("not_yet", "先不交付，回去改", "告诉助手改哪里。")),
    # 「确认」两个字太像用户会在意见框里写的话:标签写全,免得一句意见被当成点了确认
    "flow_change": (("confirm", "确认，按新流程走", "以后按这个流程走。"),
                    ("not_yet", "先不确认", "还按原来的流程走。")),
    # 重新确认(以前确认过、改了又来问):问法跟着现在在哪一步走,不重复第一次的说法(第三轮实测:8 次点击才交一份 2000 字的稿)
    "task_plan_again": (("confirm", "确认，接着做", "任务计划按这一版定下，接着往下做；以后要改会再请你确认一次。"),
                        ("not_yet", "先不确认", "助手按你的意见修改，改好再请你确认。")),
    "dossier_again_draft": (("confirm", "确认，继续写", "资料汇编按这一版定下，接着写；提纲确认过、没有改动的，不用再确认。"),
                            ("not_yet", "先不确认", "告诉助手要补什么资料，或者改哪条结论。")),
    "dossier_again_outline": (("confirm", "确认，接着改提纲", "资料汇编按这一版定下，接着把提纲改到和它一致，再请你确认提纲。"),
                              ("not_yet", "先不确认", "告诉助手要补什么资料，或者改哪条结论。")),
    "outline_again": (("confirm", "确认，继续写", "提纲按这一版定下，接着写。"),
                      ("not_yet", "先不确认", "接着改提纲。")),
}
# 交付前检查里只有客户端做得了的那一项(工作步数要读客户端的会话记录):agent 版永远只能是「提醒」,不算进卡上的通过数
AGENT_SKIP_CHECKS = ("预算仪表·步数",)
# 抓取工具给的行号不是资料里的位置:「第127至138行」「lines 12-30」「L12-L30」这类**光有行号**的定位不收。
# 第四轮:带了页码或表号的行(「第 37 页第 5 行」「表3第2行」「p.12 line 5」)是资料里真的位置,照收;
# 「第3行政区」这种「行」后面紧跟着成词的汉字(行政、行业、行动……),本来就不是行号。
LINE_LOCATOR_RE = re.compile(r"第\s*\d+\s*(?:[至到\-–—~～]\s*第?\s*\d+\s*)?行(?![一-鿿])"
                             r"|第\s*\d+\s*(?:[至到\-–—~～]\s*第?\s*\d+\s*)?行(?=[的起至到之左上下以前后间])"
                             r"|(?<![A-Za-z])[Ll]ines?\s*\d+"
                             r"|(?<![A-Za-z0-9])L\d+(?:\s*[-–]\s*L?\d+)?(?![A-Za-z0-9])")
# 页码、表号、图号:定位里有它们,同一处的行号就是资料里的位置
#(要带编号:「页面」「表格」这种不算;「第127至138行（页面抓取）」照样是光有行号)
PAGE_TABLE_RE = re.compile(r"[0-9０-９一二三四五六七八九十百]\s*[页頁]|[页頁]\s*[0-9０-９]"
                           r"|表\s*[0-9０-９一二三四五六七八九十A-Za-z]|图\s*[0-9０-９一二三四五六七八九十]"
                           r"|(?<![A-Za-z])(?:pp?|PP?)\.\s*\d|(?<![A-Za-z])(?:[Pp]age|PAGE|[Tt]able|TABLE|[Ff]ig\.?)\s*[0-9A-Z]")
# 资料卡片 direction 的开头(和核心判断的关系)要和 stance 对得上(第四轮:写着「不支持：」的卡标成了支持判断)
DIRECTION_PREFIXES = {"support": ("支持", "支持判断"), "counter": ("不支持",), "mixed": ("背景", "背景资料")}
DIRECTION_SPLIT_RE = re.compile(r"\A\s*([^：:\n]{1,6}?)\s*[：:]\s*(.*)\Z", re.S)
# 「具体的数据」:阿拉伯数字,或者汉字数目带单位(三成、两倍、五个县……)
DATA_POINT_RE = re.compile(r"[0-9０-９]|[一二三四五六七八九十百千万亿两半]+\s*(?:成|倍|个|家|台|座|项|条|名|人|户|元|万|亿|%|％|分之|年|月|天|次|所|辆|吨|公里|千瓦|处|张|份)")
# 方法说明、口径说明、概念界定、背景介绍:不是正反证据,不能标「不支持」
METHOD_NOTE_RE = re.compile(r"\A\s*(?:关于)?(?:方法|方法论|研究方法|统计方法|统计口径|数据口径|口径|定义|概念|名词解释|数据说明|数据来源|说明|介绍|描述|背景)")
EXCERPT_CAP = 300
DIRECTION_CAP = 80
RECOMMEND_MARK = {"report_type": "推荐", "decision": "助手建议"}
VIEW_OPEN = "右侧已打开"
VIEW_LINK = "点开看"
CHANGES_PLAN_NOTE = "会改任务计划，再请你确认。"
UNCHANGED = "判断、范围、口径都没动。"
CHANGED_SAY = "材料刚刚变了，我按新的内容重新准备了这张卡，请再看一遍。"
FLOW_REVERTED_SAY = "流程刚才已经还原成原来的步骤了，这次不算数。还要改流程的话告诉我，我再请你确认。"
ERROR_SAY = "记下你这次选择的时候出了故障，这一步还没定下来。我先停在这里，请你稍后再试一次，或者告诉我怎么办。"
# 点了「跳过」之后,用户说哪句话就能把卡拿回来
BRING_BACK = {"task_plan": "确认任务计划", "dossier": "确认资料汇编", "outline": "确认提纲",
              "delivery": "交付成稿", "flow_change": "确认改流程", "report_type": "定报告类型",
              "decision": "接着定刚才那件事", "pick_project": "接着做项目"}
HEADERS = {"report_type": "报告类型", "delivery": "交付确认", "decision": "需要你决定",
           "flow_change": "重新确认", "pick_project": "选项目"}
# 第七轮:「只写了意见、没选选项」的回答,在卡片记录里的 result(确认类记成先不确认,报告类型 / 决定 / 选项目卡记成 note)
NOTE_RESULTS = ("not_approved", "note")
# 一张卡还能再回答一次的两种情况(第五轮 H2 收起;第七轮 只写了意见),对用户、对助手各怎么说
REOPEN_WORDS = {"skipped": "收起来", "note": "只写了意见、没选选项"}


class Refuse(Exception):
    def __init__(self, problems):
        super().__init__("; ".join(problems))
        self.problems = list(problems)


# ---- 小工具 ----

def text_len(s):
    return len(s.strip()) if isinstance(s, str) else 0


def need_text(fields, key, name, cap):
    v = fields.get(key)
    if not isinstance(v, str) or not v.strip():
        return None, ["fields 里要有 %s（%s，≤%d 字）" % (key, name, cap)]
    if text_len(v) > cap:
        return v.strip(), ["%s（%s）%d 字，超过上限 %d 字" % (key, name, text_len(v), cap)]
    bad = pl.plain_text_problems(v)
    if bad:
        return v.strip(), ["%s（%s）不是纯文字（有%s）" % (key, name, "、".join(bad))]
    return v.strip(), []


def md_time(iso):
    """ISO 时间 → 「9-30 10:43」(给用户看)。"""
    iso = pl.date_text(iso)
    if not isinstance(iso, str):
        return None
    m = re.match(r"\d{4}-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})", iso)
    if not m:
        return None
    return "%d-%d %s:%s" % (int(m.group(1)), int(m.group(2)), m.group(3), m.group(4))


def load_doc(project, doc):
    path = os.path.join(project, doc)
    if not os.path.isfile(path):
        raise Refuse(["%s还没有写出来（项目里没有 %s）" % (yzlib.DOC_NAMES.get(doc, doc), doc)])
    meta, body, problem = pl.load_md(path)
    if problem:
        raise Refuse(["%s 的 frontmatter 读不出来：%s。先改好再出卡" % (doc, problem)])
    return path, meta, body


def version_of(meta, doc):
    v = meta.get("version")
    if not (isinstance(v, int) and not isinstance(v, bool)):
        raise Refuse(["%s 的 version 不是整数，改成整数再出卡" % doc])
    return v


def points_of(path, body, whole=False):
    """三点(规格 §⑨-1 / §⑨-3)→ 三条内容;不齐全 → Refuse。读法与判据全用 pipeline_lib。"""
    text, m = pl._front(path)
    offset = text.count(chr(10), 0, m.end()) if m else 0
    problems, _notes, items = pl.commitment_problems(body, whole=whole, line_offset=offset)
    if problems:
        where = "交付的三点（library/delivery_commitments.md）" if whole else "「要你认的三件事」"
        raise Refuse(["%s不齐全：%s" % (where, p) for p in problems])
    return [it["content"] for it in items[:3]]


def doc_checks(meta):
    """commitments_check 对这份材料另核的几项(初步结论的理由、研究范围摘要、成稿叫法、对应表述)。"""
    out = []
    out += pl.gate_reason_problems(meta)
    out += pl.scope_brief_problems(meta)
    out += pl.report_name_problems(meta)
    return out


def approve_dry_run(project, doc):
    """在临时副本上试跑 stamp.py --approve:落章守卫(版本、revision_log 形状、省掉提纲)不过 → Refuse。
    用的就是 stamp.py 本身,不另写一份守卫。"""
    tmp = yzlib.work_tmp("card")
    try:
        shutil.copy2(os.path.join(project, doc), os.path.join(tmp, doc))
        plan = os.path.join(project, "task_plan.md")
        if doc != "task_plan.md" and os.path.isfile(plan):
            shutil.copy2(plan, os.path.join(tmp, "task_plan.md"))
        rc, out, err = yzlib.run_toolkit("stamp.py", [os.path.join(tmp, doc), "--approve", "--by", "用户", "--quote", "试跑"])
        if rc != 0:
            lines = [l.strip() for l in (out + err).splitlines() if l.strip()]
            raise Refuse(["用户点了确认也写不进确认记录：%s" % " ".join(lines[:3]).replace(tmp, "").strip()])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def state_of(meta):
    return meta.get("pipeline_status") if isinstance(meta, dict) else None


def criteria_rows(body):
    """任务计划「预注册结论空间」那张表 → [(outcome, 判定条件, 对应表述)](取法同 pipeline_lib.preregistration_problems)。"""
    m = pl._PREREG_HEADING_RE.search(body or "")
    if not m:
        return []
    rest = body[m.end():]
    nxt = pl._ANY_HEADING_RE.search(rest)
    lines = (rest[:nxt.start()] if nxt else rest).split(chr(10))
    table = next((t for t in (pl.split_table(lines, i) for i in range(len(lines))) if t), None)
    if not table:
        return []
    return [(r[0] if len(r) > 0 else "", r[1] if len(r) > 1 else "", r[2] if len(r) > 2 else "") for r in table[1]]


def conclusion_phrase(outcome, plan_body):
    """资料汇编 gate_verdict.outcome → 成立 / 部分成立 / 不成立(按任务计划判定标准表第三栏;读不出时 A/B/C 缺省)。"""
    if not isinstance(outcome, str) or not outcome.strip():
        return None
    o = outcome.strip()
    if o in pl.PREREG_PHRASES:
        return o
    for row in criteria_rows(plan_body):
        if row[0].strip() == o and row[2].strip() in pl.PREREG_PHRASES:
            return row[2].strip()
    return {"A": "成立", "B": "部分成立", "C": "不成立"}.get(o)


def stance_counts(project):
    """资料卡片按四类数(不算 deprecated),用 card_check.load_cards 读卡。读不出 → None。"""
    cards_dir = os.path.join(project, "cards")
    if not os.path.isdir(cards_dir):
        return None
    from card_check import load_cards
    try:
        cards = load_cards(cards_dir)
    except Exception:
        return None
    counts = {"total": 0}
    for key in pl.STANCE_LABELS:
        counts[key] = 0
    for card, _ in cards:
        if not isinstance(card, dict) or card.get("deprecated"):
            continue
        counts["total"] += 1
        s = card.get("stance")
        if s in pl.STANCE_LABELS:
            counts[s] += 1
    return counts


def stance_parts(counts, genre):
    """资料卡片按类数 → [(说法, 张数)]。综述型不下判断,不说「支持判断 / 不支持」:有内容的都叫背景资料,另列资料缺口。"""
    if genre == "survey":
        return [(pl.STANCE_LABELS["mixed"], counts["total"] - counts["gap"]), (pl.STANCE_LABELS["gap"], counts["gap"])]
    return [(pl.STANCE_LABELS[k], counts[k]) for k in pl.STANCE_LABELS]


def stance_phrase(counts, genre):
    return " · ".join("%s %d" % (word, n) for word, n in stance_parts(counts, genre))


def stamps_of(project):
    facts, items, _ = yzlib.pipeline_facts(project)
    return facts.get("stamps") or {}, items


def again_of(meta):
    """以前确认过(作废时挪进 revision_log 的旧确认记录在)→ 卡头写「重新确认」。"""
    for e in meta.get("revision_log") or []:
        if isinstance(e, dict) and isinstance(e.get("prev_approval"), dict) and e["prev_approval"].get("status") == "approved":
            return True
    return False


def outside_name(path, utexts=()):
    """项目外的一个文件 → 给用户看的名字:文件名去掉扩展名;读不出个名字、或者名字里有用户读不懂的词
    (task_plan、.md 之类),就写「项目外的一份材料」。完整路径只进出卡记录,不上卡。"""
    p = str(path).strip().replace("\\", "/").rstrip("/")
    base = p.rsplit("/", 1)[-1]
    stem = re.sub(r"\.[A-Za-z0-9]{1,10}$", "", base).strip(" ._-")
    if not stem or not re.search(r"[一-鿿]|[A-Za-z]{3,}", stem):
        return OUTSIDE_GENERIC
    if wording_check.scan(stem, utexts):
        return OUTSIDE_GENERIC
    return stem


def outside_block(fields, utexts=()):
    """「这一轮读过的项目以外的文件」:助手自报(Codex 里没有会话日志可读)。卡上只写文件的名字,不写路径。"""
    if "outside" not in fields:
        raise Refuse(['fields 里要有 outside：这一轮你读过的项目以外的文件（路径列表；没读过就写 "outside": []）。工具包里的规范和脚本不算'])
    items = fields.get("outside")
    if not isinstance(items, list) or not all(isinstance(x, str) and x.strip() for x in items):
        raise Refuse(["outside 要是路径列表（每条一个路径），没有就写 []"])
    lines = ["- %s：%d 个" % (OUTSIDE, len(items))]
    for p in items:
        lines.append("  - %s" % outside_name(p, utexts))
    if items:
        lines.append("  - %s" % OUTSIDE_NOTE)
    return lines, [p.strip() for p in items]


def cards_digest(project):
    """cards/ 文件夹的指纹:每个文件的相对路径 + 内容 hash。出资料汇编卡时记下,回答时比 —— 用户看卡期间改了资料卡片就不签。"""
    d = os.path.join(project, "cards")
    if not os.path.isdir(d):
        return None
    h = hashlib.sha256()
    for root, dirs, files in os.walk(d):
        dirs.sort()
        for fn in sorted(files):
            p = os.path.join(root, fn)
            h.update(("%s\0%s\n" % (os.path.relpath(p, d).replace("\\", "/"), pl.file_sha256(p))).encode("utf-8"))
    return h.hexdigest()


def delivery_commitments_path(project):
    return os.path.join(project, "library", "delivery_commitments.md")


def delivery_points_ok(project):
    """交付的三点写好了没有(和出交付卡用同一个判法)。"""
    dc = delivery_commitments_path(project)
    if not os.path.isfile(dc):
        return False
    _meta, body, problem = pl.load_md(dc)
    if problem:
        return False
    try:
        points_of(dc, body, whole=True)
    except Refuse:
        return False
    return True


# ---- 资料卡片与资料汇编的硬规矩(第三轮:实测里反面证据被读反、停下来问用户的条件被跳过、打不开记成没有) ----

def locator_problem(loc):
    """定位只写了抓取工具的行号(没有页码、表号这类资料里的位置)→ 那一段;没问题 → None。"""
    m = LINE_LOCATOR_RE.search(loc) if isinstance(loc, str) else None
    if m and PAGE_TABLE_RE.search(loc):
        return None
    return m.group(0) if m else None


def direction_parts(direction):
    """direction → (开头那几个字, 冒号后面的话);没有「…：」的开头 → (None, 全句)。"""
    m = DIRECTION_SPLIT_RE.match(direction or "")
    if not m:
        return None, (direction or "").strip()
    return m.group(1).strip(), m.group(2).strip()


def direction_stance(prefix):
    """direction 的开头 → 它说的是哪一类(support / counter / mixed);认不出 → None。"""
    for stance, words in DIRECTION_PREFIXES.items():
        if prefix in words:
            return stance
    return None


def has_data_point(text):
    return isinstance(text, (str, int, float)) and not isinstance(text, bool) and bool(DATA_POINT_RE.search(str(text)))


def _text_ok(v, cap):
    return isinstance(v, str) and 2 <= len(v.strip()) <= cap


def gap_card_state(card):
    """资料缺口卡的实际情况:来源里有打不开的 → blocked;有出错的 → failed;有查了没内容的 → empty;否则 → gap(查过,确实没有)。"""
    states = {s.get("fetch_state") for s in card.get("sources") or [] if isinstance(s, dict)}
    for st in ("blocked", "failed", "empty"):
        if st in states:
            return st
    return "gap"


def load_live_cards(project):
    """→ [(卡, 文件名)],不算弃用的;读不出 → None。"""
    d = os.path.join(project, "cards")
    if not os.path.isdir(d):
        return None
    from card_check import load_cards
    try:
        cards = load_cards(d)
    except Exception:
        return None
    return [(c, fn) for c, fn in cards if isinstance(c, dict) and not c.get("deprecated")]


def card_rule_problems(project, genre):
    """资料卡片的硬规矩 → 毛病列表:
    - 取到了的来源(fetch_state: ok)要写原文摘录 excerpt(照抄原文里依据的那一两句,≤300 字);
    - 研判型:每张不是资料缺口的卡写 direction:它和核心判断的关系(≤80 字,如「不支持：……」);
    - 标成支持判断 / 不支持的卡要有引用的数据(facts 里有 cited: true):方法说明、口径说明、背景都是背景资料;
    - 定位不写抓取工具的行号;
    - 资料缺口卡记的是打不开 / 出错,来源里要有那个网址。"""
    cards = load_live_cards(project)
    if cards is None:
        return ["资料卡片读不出来（cards 文件夹不在或有卡片读不出）：先跑 card_check.py 修好"]
    out = []
    for card, fn in cards:
        uid = str(card.get("uid") or fn)
        stance = card.get("stance")
        srcs = [s for s in card.get("sources") or [] if isinstance(s, dict)]
        for s in srcs:
            bad = locator_problem(s.get("locator"))
            if bad:
                out.append("资料卡片 %s 的定位写成了「%s」：那是抓取工具给的行号，不是资料里的位置。写章节名、标题或页码"
                           "（例如「第 4 章表 4-3」「二、主要结论」「第 37 页」）" % (uid, bad))
        if stance == "gap":
            st = gap_card_state(card)
            if st in ("blocked", "failed") and not any(isinstance(s.get("url"), str) and s["url"].strip() for s in srcs):
                out.append("资料缺口卡 %s 记的是%s，来源里要写上那个网址（照实记下试过哪里）"
                           % (uid, "打不开" if st == "blocked" else "没取到（出错）"))
            continue
        if any(s.get("fetch_state") == "ok" and not _text_ok(s.get("excerpt"), EXCERPT_CAP) for s in srcs):
            out.append("资料卡片 %s：取到了的来源都要写原文摘录（excerpt：照抄原文里你依据的那一两句，≤%d 字）" % (uid, EXCERPT_CAP))
        direction = card.get("direction")
        if genre == "argument" and not _text_ok(direction, DIRECTION_CAP):
            out.append("资料卡片 %s：写一句它和核心判断的关系（direction，≤%d 字，例如「不支持：……」「支持：……」「背景：……」）"
                       % (uid, DIRECTION_CAP))
        elif isinstance(direction, str) and direction.strip() and stance in DIRECTION_PREFIXES:
            # 第四轮:方向写在 direction 的开头,卡片的类别写在 stance,两样要说同一件事
            prefix, said = direction_parts(direction)
            says = direction_stance(prefix)
            if says is None:
                out.append("资料卡片 %s 的 direction 开头要写「支持：」「不支持：」或「背景：」（现在是「%s」），和这张卡标的类别一致"
                           % (uid, prefix or direction.strip()[:8]))
            elif says != stance:
                out.append("资料卡片 %s 的 direction 写的是「%s：……」，卡却标成了%s（stance: %s）：两样要一致 —— 先读懂原文，"
                           "再定是支持判断（support）、不支持（counter）还是背景资料（mixed）" % (uid, prefix, pl.STANCE_LABELS[stance], stance))
            if stance == "counter" and (METHOD_NOTE_RE.match(said) or METHOD_NOTE_RE.match(str(card.get("title") or ""))):
                out.append("资料卡片 %s 是方法说明、口径说明或背景介绍，却标成了不支持：这类卡不是正反证据，标成背景资料（stance: mixed，"
                           "direction 写「背景：……」）" % uid)
        cited = [f for f in card.get("facts") or [] if isinstance(f, dict) and f.get("cited")]
        if stance in ("support", "counter") and not cited:
            out.append("资料卡片 %s 标成了%s，但没有引用的数据（facts 里没有 cited: true）：方法说明、口径说明、背景介绍不是正反证据，"
                       "标成背景资料（stance: mixed）" % (uid, pl.STANCE_LABELS[stance]))
        elif stance in ("support", "counter") and not any(has_data_point(f.get("value")) for f in cited):
            out.append("资料卡片 %s 标成了%s，但引用的数据里没有一个具体的数（数字、比例、金额、个数……）：正反证据要有可核对的数据点。"
                       "原文有数就把数和它的口径写进 facts（cited: true）；只有定性的说法，标成背景资料（stance: mixed）"
                       % (uid, pl.STANCE_LABELS[stance]))
    return out


def gap_decision_valid(project, plan_meta):
    """有资料缺口时,用户在决定卡上拿过主意吗(任务计划写了缺资料就停下来问):
    - 任务计划最近一次确认之后,用户在决定卡上选过一项 → 算;
    - 第四轮:更早的一次决定(在任务计划第一次确认之后)也算,只要它回答时记下的资料缺口卡把现在的每一个缺口都包住了 ——
      任务计划后来又确认一次(改了别处),那次决定照样管着同一批缺口,不用把同一个问题再问一遍;多出新的缺口才要再问。"""
    if yzlib.answered_decisions(project, since=yzlib.approval_of(plan_meta).get("approved_at")):
        return True
    current = set(gap_card_uids(project))
    times = plan_approval_times(plan_meta)
    if not current or not times:
        return False
    for e in yzlib.answered_decisions(project, since=times[0].isoformat()):
        covered = e.get("gap_cards")
        if isinstance(covered, list) and current <= {str(x) for x in covered}:
            return True
    return False


def gap_card_uids(project):
    """现在的资料缺口卡(没弃用的)的卡号 → 排好序的列表。决定卡回答时记下,用来判断那次决定还管不管现在的缺口。"""
    return sorted(str(c.get("uid") or fn) for c, fn in (load_live_cards(project) or []) if c.get("stance") == "gap")


def plan_approval_times(plan_meta):
    """任务计划确认过的每一个时刻(现在的确认记录 + revision_log 里作废前的旧记录)→ 排好序的 datetime 列表。"""
    times = []
    ap = yzlib.approval_of(plan_meta)
    if ap.get("status") == "approved":
        times.append(yzlib.parse_iso(ap.get("approved_at")))
    for e in (plan_meta or {}).get("revision_log") or []:
        prev = e.get("prev_approval") if isinstance(e, dict) else None
        if isinstance(prev, dict) and prev.get("status") == "approved":
            times.append(yzlib.parse_iso(prev.get("approved_at")))
    return sorted(t for t in times if t is not None)


def fetch_summary(project):
    """records/fetches.jsonl 里没取到的来源 → {blocked: n, failed: n, empty: n}:按「网址 + 情况」去重,
    后来重新取到了的不算(第八轮;实现在 fetches.source_counts)。一个都没有 → {}。"""
    import fetches
    return fetches.source_counts(project)


def fetch_phrase(counts):
    said = (("blocked", "打不开"), ("failed", "出错"), ("empty", "查了没内容"))
    parts = ["%s %d 个来源" % (word, counts[k]) for k, word in said if counts.get(k)]
    return "没取到的资料：%s（都记在资料卡片上）" % " · ".join(parts) if parts else None


def dossier_rule_problems(project, meta, plan_meta, genre):
    """出资料汇编卡之前的硬规矩 → 毛病列表:
    - 资料汇编列了资料缺口,而任务计划确认之后用户一次都没在决定卡上选过 → 先问用户(第三轮:任务计划写了缺资料就停下问,结果没问就出了卡);
    - 资料缺口卡和资料汇编的缺口一一对得上(张数和每个缺口的情况:没有这项数据 / 打不开 / 出错 / 查了没内容);
    - 记过没取到的网址(records/fetches.jsonl)都在资料卡片上留了痕;
    - 研判型:写了找反面证据的记录(counter_search);
    - 资料卡片的硬规矩(card_rule_problems)。"""
    import fetches
    out = []
    gaps = [g for g in (meta.get("gaps") or []) if isinstance(g, dict)] if isinstance(meta.get("gaps"), list) else []
    if gaps and not gap_decision_valid(project, plan_meta):
        out.append("资料汇编列了 %d 个资料缺口（%s），任务计划确认之后还没有问过用户怎么处理：先用决定卡问用户"
                   "（card.py prepare decision：缺口怎么办、要不要改研究范围），用户选了之后把决定写进资料汇编，再出资料汇编卡"
                   % (len(gaps), "、".join(str(g.get("what") or g.get("id") or "") for g in gaps[:3])))
    cards = load_live_cards(project) or []
    gap_states = sorted(gap_card_state(c) for c, _fn in cards if c.get("stance") == "gap")
    dossier_states = sorted(str(g.get("state")) for g in gaps)
    if gap_states != dossier_states:
        said = lambda states: "、".join("%s %d 个" % (FETCH_SAID.get(s, s), states.count(s)) for s in sorted(set(states))) or "0 个"
        out.append("资料汇编列的缺口（%s）和资料缺口卡（%s）对不上：每个缺口一张资料缺口卡（stance: gap），"
                   "情况要一致，而且用同一个名字 —— 资料缺口卡的情况看它来源上的 fetch_state：有打不开的就是 blocked（打不开），"
                   "有出错的是 failed（没取到），网页打开了、里面没有要的数据是 empty（查了但没有内容）；"
                   "gap（没有这项数据）只留给查实了这项数据根本没有公布的" % (said(dossier_states), said(gap_states)))
    urls = {str(s.get("url")).strip() for c, _fn in cards for s in c.get("sources") or [] if isinstance(s, dict) and s.get("url")}
    latest = fetches.latest_states(project)
    lost = sorted(u for u, st in latest.items() if st in ("blocked", "failed") and u not in urls)
    if lost:
        out.append("这几个网址记过没取到，但没有记在任何资料卡片上：%s。记进相关那张卡的来源（fetch_state 照实写 blocked / failed），"
                   "或者为它写一张资料缺口卡" % "、".join(lost[:4]))
    # 第四轮:记过打不开 / 出错的网址,卡上却写成 ok(读到了)—— 要么是真的后来重新取到了(那就先记一次 ok),要么是改了标签
    relabeled = sorted({(str(c.get("uid") or fn), str(s.get("url")).strip(), str(s.get("fetch_state")))
                        for c, fn in cards for s in c.get("sources") or []
                        if isinstance(s, dict) and s.get("url") and latest.get(str(s.get("url")).strip()) in ("blocked", "failed")
                        and s.get("fetch_state") not in ("blocked", "failed")})
    for uid, url, on_card in relabeled[:4]:
        out.append("资料卡片 %s 上的 %s 记过%s，卡上却写成了 %s：没取到就照实写 %s；后来真的重新打开、读到了，先跑 "
                   "$PY \"agent-tools/fetches.py\" add \"%s\" --url \"%s\" --state ok 记下这次取到了，再写原文摘录"
                   % (uid, url, fetches.SAID[latest[url]], on_card, latest[url], yzlib.project_name(project), url))
    if genre == "argument":
        cs = meta.get("counter_search")
        good = [x for x in cs if isinstance(x, dict) and _text_ok(x.get("query") or x.get("where"), 200)
                and _text_ok(x.get("result"), 300)] if isinstance(cs, list) else []
        if not good:
            out.append("资料汇编要写找反面证据的记录（counter_search：每条 {query: 怎么找、在哪找, result: 找到了什么、没找到什么, "
                       "found: [这次找到、做成卡的卡号]}，至少一条）：研判型要专门找过不支持核心判断的资料")
        out += counter_found_problems(project, cs)
    out += card_rule_problems(project, genre)
    return out


# ---- 第六轮:反向检索找到的资料归成了什么(10-04 命令行那一轮:4 条反向检索,几条逆向数据全归成了背景,卡上「不支持 0」) ----

def counter_found_problems(project, cs):
    """每条反向检索记录要写 found:这次找到、做成了资料卡片的卡号(没找到写 []),卡号要真有那张卡。"""
    out = []
    live = {str(c.get("uid") or fn) for c, fn in (load_live_cards(project) or [])}
    for i, x in enumerate(cs if isinstance(cs, list) else [], start=1):
        if not isinstance(x, dict):
            continue
        found = x.get("found")
        if not isinstance(found, list) or not all(isinstance(u, str) and u.strip() for u in found):
            out.append("反向检索记录第 %d 条要写 found：这次找到、做成了资料卡片的卡号列表（例如 found: [S07, S09]）；"
                       "什么都没找到写 found: []" % i)
            continue
        lost = [u for u in found if u.strip() not in live]
        if lost:
            out.append("反向检索记录第 %d 条的 found 里有找不到的资料卡片：%s（写已经做好的卡的卡号，弃用的卡不算）" % (i, "、".join(lost)))
    return out


def counter_found(project, meta):
    """反向检索记录 → (找到的资料卡片卡号, 其中归为不支持的卡号)。只数 found 里写了、确实有这张卡(没弃用)的,重复的算一次。"""
    cards = {str(c.get("uid") or fn): c for c, fn in (load_live_cards(project) or [])}
    cs = meta.get("counter_search")
    found = []
    for x in cs if isinstance(cs, list) else []:
        if isinstance(x, dict) and isinstance(x.get("found"), list):
            for u in x["found"]:
                u = str(u).strip()
                if u in cards and u not in found:
                    found.append(u)
    return found, [u for u in found if cards[u].get("stance") == "counter"]


def counter_reason_rows(project, uids, fields):
    """反向检索找到、却没归为不支持的卡 → [(卡号, 标题, 归为什么, 理由)];缺理由或理由不合格 → Refuse(先请助手重核归类)。
    理由写在 fields 的 counter_reasons 里:{卡号: 一句研究上的话,≤40 字}。"""
    cards = {str(c.get("uid") or fn): c for c, fn in (load_live_cards(project) or [])}
    given = fields.get("counter_reasons")
    given = given if isinstance(given, dict) else {}
    rows, bad = [], []
    for uid in uids:
        c = cards.get(uid) or {}
        title = str(c.get("title") or uid).strip()
        label = pl.STANCE_LABELS.get(c.get("stance"), "背景资料")
        reason = given.get(uid)
        if not isinstance(reason, str) or not reason.strip():
            bad.append("%s（%s，现在标的是%s）没写理由" % (uid, title, label))
        elif text_len(reason) > CAPS["counter_reason"]:
            bad.append("%s 的理由 %d 字，超过上限 %d 字" % (uid, text_len(reason), CAPS["counter_reason"]))
        elif pl.plain_text_problems(reason):
            bad.append("%s 的理由不是纯文字（有%s）" % (uid, "、".join(pl.plain_text_problems(reason))))
        else:
            rows.append((uid, title, label, reason.strip()))
    if bad:
        raise Refuse(["反向检索找到的资料里，有 %d 张没归为不支持：%s。先回头核一遍归类 —— 削弱核心判断任何一部分的数据都归不支持"
                      "（stance: counter，direction 写「不支持：」并说清削弱的是哪一部分、什么方向）；成稿里要拿来削弱判断的资料，"
                      "立场就是不支持。核过确实只是背景的，在 fields.json 里给每一张写一句为什么："
                      "{\"counter_reasons\": {\"<卡号>\": \"<一句研究上的话，≤%d 字>\"}}，再出卡。这些理由会逐条写在卡上给用户看。"
                      % (len(uids), "；".join(bad), CAPS["counter_reason"])])
    return rows


FETCH_SAID = {"ok": "已取得", "empty": "查了但没有内容", "gap": "没有这项数据", "failed": "没取到（出错）", "blocked": "打不开"}


def view_token(kind, main=True):
    """卡上「依据的材料」那一行末尾的占位:出卡检查都过了才生成那一页,再换成「右侧已打开 · 点开看」。"""
    return "\x00VIEW:%s:%s\x00" % (kind, "main" if main else "link")


def points_block(points):
    out = ["**%s**" % POINTS, ""]
    for i, (label, content) in enumerate(zip(POINT_LABELS, points), start=1):
        out.append("%d. %s：%s" % (i, label, content))
    return out


def point_lines(points):
    """三点的紧凑写法,放进原生选项卡的问题里(第三轮:上下文压缩之后卡前那条消息没发出去,用户没看到三点就点了交付)。"""
    return ["%s：%s" % (label, content) for label, content in zip(POINT_LABELS, points)]


def position_of(doc, stages):
    docs = pl.stage_docs(stages)
    return docs.index(doc) + 1 if doc in docs else None


def options_from(spec):
    return [{"key": k, "label": l, "base": l, "description": d} for k, l, d in spec]


def recommended_first(options, key):
    """推荐的那一项挪到第一个(第五轮 M2:原生卡默认选中第一项,旧的工具说明也要求推荐项放第一)。
    原生卡的 ask 和文字卡用同一个顺序,卡上的编号跟着变;其余几项的先后不动。确认类的卡不用它(「确认」总在第一)。"""
    if key is None:
        return options
    options.sort(key=lambda o: 0 if o["key"] == key else 1)
    return options


def _fold_label(s):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))


def label_problems(label, i):
    """选项名不能是「跳过」、不能是一个数字(文字卡里用户回这个会被当成跳过或选第几项),也别自己带推荐的标记。"""
    out = []
    folded = _fold_label(label)
    if folded in SKIP_WORDS:
        out.append("options[%d] 的 label 不能写「%s」：用户回这个会被当成跳过这张卡" % (i, label))
    if _NUMBER_RE.fullmatch(folded):
        out.append("options[%d] 的 label 不能只写一个数字（「%s」）：文字卡里用户回数字是在选第几项" % (i, label))
    elif re.match("\\A[1-9%s-%s]\\s*[%s\\s]" % (chr(0xFF11), chr(0xFF19), re.escape(NOTE_SEPARATORS)), label.strip()):
        # 第五轮 H1:「2，……」是在选第 2 项再补一句意见;选项名这样开头,用户回它就分不清是哪一项
        out.append("options[%d] 的 label 不能以「数字 + 标点」开头（「%s」）：用户回「2，……」是在选第 2 项" % (i, label))
    if any(folded.endswith("·" + mark) for mark in RECOMMEND_MARK.values()):
        out.append("options[%d] 的 label 别自己写「 · %s」这类标记：推荐哪一项用 recommend 键，脚本会加" % (i, RECOMMEND_MARK["decision"]))
    return out


def label_collisions(options):
    """每个选项显示的名字(可能带「 · 助手建议」)和不带标记的名字,都只能认出这一个选项(文字卡里按名字认)。"""
    owner = {}
    out = []
    for o in options:
        for s in {_fold_label(o["label"]), _fold_label(o["base"])}:
            if s in owner and owner[s] is not o:
                out.append("options 里有两个选项都能叫「%s」：用户回这几个字就分不清选的是哪一项。每个选项写不同的做法" % s)
            owner.setdefault(s, o)
    return out


# ---- 每种卡怎么准备 ----

def build_report_type(project, fields):
    if os.path.isfile(os.path.join(project, "task_plan.md")):
        raise Refuse(["任务计划已经写出来了：报告类型要改，就改任务计划（genre 与理由）再请用户确认任务计划，不再弹报告类型卡"])
    problems = []
    name, p1 = need_text(fields, "report_name", "成稿叫法，用用户的说法", CAPS["report_name"])
    thesis, p2 = need_text(fields, "thesis", "核心判断，一句能用资料检验的话", CAPS["thesis"])
    why, p3 = need_text(fields, "why", "为什么问这个，必须引用户的原话", CAPS["report_why"])
    problems += p1 + p2 + p3
    rec = fields.get("recommend")
    if rec is not None and rec not in ("judge", "survey"):
        problems.append("recommend 只能是 judge 或 survey；不推荐就不给这个键")
    if problems:
        raise Refuse(problems)
    options = options_from(REPORT_OPTIONS)
    for o in options:
        if o["key"] == rec:
            o["label"] = "%s · %s" % (o["base"], RECOMMEND_MARK["report_type"])
    recommended_first(options, rec)
    return {
        "card_id": "report-type",
        "title": "先定报告类型",
        # 这里的「」是模板里的槽位,装的是 agent 改写的核心判断,不是引用户的话:引文核对不扫这一句
        # (确认卡文字模板第 2 稿就是这样写的,Rick 认过;核对只管 why 里写成用户原话的「」)
        "question": "这份%s是要对「%s」下一个明确判断，还是先把情况梳理清楚？" % (name, thesis),
        "body": ["**为什么问这个**", "", why],
        "options": options, "footer": None,
        "data": {"report_name": name, "thesis": thesis, "recommend": rec},
        "scan": {"report_name": name, "thesis": thesis, "why": why},
        "quotes": [("why", why, True)],
    }


def _confirm_common(project, kind, fields):
    doc = DOC_OF[kind]
    path, meta, body = load_doc(project, doc)
    plan_meta, plan_body, plan_problem = yzlib.load_task_plan(project)
    if plan_problem:
        raise Refuse(["task_plan.md 的 frontmatter 读不出来：%s" % plan_problem])
    stages, stages_err = pl.parse_stages(plan_meta or {})
    if stages_err:
        raise Refuse(["任务计划的 %s —— 读不出本项目有哪些环节，先改好" % stages_err])
    if doc not in pl.stage_docs(stages):
        raise Refuse(["本项目的任务计划没有「拟定提纲」这个环节，不出提纲卡"])
    state = state_of(plan_meta)
    if state != AWAITING[kind]:
        raise Refuse(["进度现在是 %s，不是 %s：先跑 $PY toolkit/scripts/stamp.py \"projects/%s/task_plan.md\" --advance %s，再出卡"
                      % (state, AWAITING[kind], yzlib.project_name(project), AWAITING[kind])])
    version = version_of(meta, doc)
    ap = meta.get("approval") if isinstance(meta.get("approval"), dict) else {}
    current = pl.content_hash(meta, body)
    if ap.get("status") == "approved" and ap.get("approved_hash") == current:
        raise Refuse(["%s第 %d 版已经确认过了，不用再出卡" % (yzlib.DOC_NAMES[doc], version)])
    points = points_of(path, body)
    problems = doc_checks(meta)
    if kind == "task_plan":
        problems += pl.preregistration_problems(body)
    if problems:
        raise Refuse(problems)
    if kind != "task_plan":
        stamps, _ = stamps_of(project)
        for need in pl.stage_docs(stages)[:pl.stage_docs(stages).index(doc)]:
            st = (stamps.get(need) or {}).get("status")
            if st != "approved":
                raise Refuse(["%s还没有确认（或确认之后又改过，现在是 %s）：先把它确认好，再出%s卡"
                              % (yzlib.DOC_NAMES[need], st, yzlib.DOC_NAMES[doc])])
    approve_dry_run(project, doc)
    again = again_of(meta)
    position = position_of(doc, stages)
    title = ("重新确认 · %s" % yzlib.DOC_NAMES[doc]) if again else ("第 %d 次确认 · %s" % (position, yzlib.DOC_NAMES[doc]))
    data = {"doc": doc, "version": version, "content_hash": current, "position": position, "again": again,
            "stages": stages}
    return path, meta, body, plan_meta, plan_body, stages, version, points, title, data


def build_task_plan(project, fields):
    path, meta, body, _pm, _pb, stages, version, points, title, data = _confirm_common(project, "task_plan", fields)
    outside_lines, outside = outside_block(fields, yzlib.user_texts(project))
    lines = ["**%s**" % MATERIALS, "", "- 任务计划第 %d 版%s" % (version, view_token("task_plan"))]
    scan = {"三点 %d" % (i + 1): p for i, p in enumerate(points)}
    genre = meta.get("genre")
    if genre == "argument":
        lines.append("- 判定标准（成立 / 部分成立 / 不成立）")
        for i, (outcome, cond, phrase) in enumerate(criteria_rows(body)):
            lines.append("  - %s：%s" % (phrase or outcome, cond))
            scan["判定标准第 %d 行" % (i + 1)] = cond
    lines += outside_lines
    lines += [""] + points_block(points)
    data.update({"genre": genre, "outside": outside})
    again = data.get("again")
    return {
        "card_id": "task-plan-v%d" % version, "title": title,
        "question": ("任务计划第 %d 版可以定下来，接着做吗？" if again else "任务计划第 %d 版可以定下来，开始收集资料吗？") % version,
        "body": lines, "options": options_from(CONFIRM_OPTIONS["task_plan_again" if again else "task_plan"]), "footer": FOOTER_CONFIRM,
        "data": data, "scan": scan, "quotes": [(k, v, False) for k, v in scan.items()],
        "ask_lines": point_lines(points) + (["读过的项目以外的文件（助手自报）：%d 个" % len(outside)] if outside else []),
    }


def build_dossier(project, fields):
    path, meta, body, plan_meta, plan_body, stages, version, points, title, data = _confirm_common(project, "dossier", fields)
    outside_lines, outside = outside_block(fields, yzlib.user_texts(project))
    counts = stance_counts(project)
    if counts is None:
        raise Refuse(["资料卡片读不出来（cards 文件夹不在或有卡片读不出）：先跑 card_check.py 修好"])
    genre = meta.get("genre") or (plan_meta or {}).get("genre")
    rules = dossier_rule_problems(project, meta, plan_meta, genre)
    if rules:
        raise Refuse(rules)
    with_outline = "outline" in stages
    again = data.get("again")
    # 确认之后往哪走:提纲确认过、之后没改的,资料汇编重新确认不连带它再确认一次(第三轮:同一份没改的提纲被重新确认)
    outline_ok = with_outline and yzlib.approved_valid(project, "outline.md")
    next_state = "outlining" if with_outline and not outline_ok else "drafting"
    if outline_ok or not with_outline:
        question = ("资料汇编第 %d 版可以定下来，继续写吗？" if again else "资料汇编第 %d 版可以定下来，开始写吗？") % version
        options = CONFIRM_OPTIONS["dossier_again_draft" if again else "dossier_draft"]
    elif again and os.path.isfile(os.path.join(project, "outline.md")):
        question = "资料汇编第 %d 版可以定下来，接着改提纲吗？" % version
        options = CONFIRM_OPTIONS["dossier_again_outline"]
    else:
        question = "资料汇编第 %d 版可以定下来，开始拟提纲吗？" % version
        options = CONFIRM_OPTIONS["dossier_outline"]
    cards_line = "资料卡片 %d 张：%s" % (counts["total"], stance_phrase(counts, genre))
    lines = ["**%s**" % MATERIALS, "", "- 资料汇编第 %d 版%s" % (version, view_token("dossier")),
             "- %s%s" % (cards_line, view_token("cards", main=False))]
    fetched = fetch_phrase(fetch_summary(project))
    if fetched:
        lines.append("- %s" % fetched)
    scan = {"三点 %d" % (i + 1): p for i, p in enumerate(points)}
    ask_lines = [cards_line] + ([fetched] if fetched else [])
    counter_line = None
    if genre == "argument":
        # 反向检索找到几条、归为不支持几条:由脚本从资料汇编的 found 和资料卡片数。
        # 第八轮:找到的卡里没归为不支持的,每一张都在卡上逐条写「标题 → 归为背景资料:一句理由」,理由由助手写进 fields
        # (10-04 那一轮:S03 标的是背景,成稿第三部分正是拿它削弱「新增」的判断;只在一条都没归为不支持时才问一句不够)
        found, counter = counter_found(project, meta)
        counter_line = "反向检索找到 %d 条，归为不支持 %d 条" % (len(found), len(counter))
        lines.append("- %s" % counter_line)
        ask_lines.append(counter_line)
        others = [u for u in found if u not in counter]
        if others:
            for uid, title, label, reason in counter_reason_rows(project, others, fields):
                row = "%s → 归为%s：%s" % (title, label, reason)
                lines.append("  - %s" % row)
                ask_lines.append(row)
                scan["反向检索找到的 %s 的标题" % uid] = title
                scan["反向检索找到的 %s 归为%s的理由" % (uid, label)] = reason
    lines += outside_lines
    conclusion = None
    if meta.get("genre") == "argument":
        verdict = meta.get("gate_verdict") if isinstance(meta.get("gate_verdict"), dict) else {}
        phrase = conclusion_phrase(verdict.get("outcome"), plan_body)
        reason = (verdict.get("reason") or "").strip()
        if not phrase:
            raise Refuse(["研判型资料汇编的 gate_verdict.outcome 读不出来：写 A / B / C（对应任务计划判定标准表的那一行）"])
        lines += ["", "**对照判定标准，初步结论**", "", "「%s」：%s" % (phrase, reason)]
        scan["初步结论的理由"] = reason
        conclusion = {"outcome": verdict.get("outcome"), "phrase": phrase, "reason": reason}
        ask_lines.append("初步结论：%s。%s" % (phrase, reason))
    lines += [""] + points_block(points)
    data.update({"cards": counts, "conclusion": conclusion, "next": next_state, "outline_kept": bool(outline_ok),
                 "outside": outside, "cards_digest": cards_digest(project)})
    return {
        "card_id": "dossier-v%d" % version, "title": title, "question": question, "body": lines,
        "options": options_from(options),
        "footer": FOOTER_CONFIRM, "data": data, "scan": scan, "quotes": [(k, v, False) for k, v in scan.items()],
        "ask_lines": ask_lines + point_lines(points),
    }


def outline_numbers(path):
    """提纲的合计字数、目标字数、没有资料卡片支撑的节(都用 pipeline_lib 的参考实现)。"""
    from outline_check import parse_outline_md
    meta, nodes = parse_outline_md(path)
    total = pl.outline_total_words(nodes)
    target = meta.get("total_words")
    target = target if isinstance(target, int) and not isinstance(target, bool) else None
    unsupported = pl.outline_unsupported(nodes)
    titles = {n.get("id"): n.get("title") for n in nodes}
    return nodes, total, target, [(u, titles.get(u)) for u in unsupported]


def build_outline(project, fields):
    path, meta, body, _pm, _pb, stages, version, points, title, data = _confirm_common(project, "outline", fields)
    nodes, total, target, unsupported = outline_numbers(path)
    lines = ["**%s**" % MATERIALS, "", "- 提纲第 %d 版%s" % (version, view_token("outline")),
             "- 合计 %d 字%s" % (total, "（目标 %d 字）" % target if target is not None else ""),
             "- 没有资料卡片支撑的节：%d 个" % len(unsupported)]
    scan = {"三点 %d" % (i + 1): p for i, p in enumerate(points)}
    for i, (_id, t) in enumerate(unsupported):
        lines.append("  - %s" % (t or _id))
        scan["没有资料卡片支撑的节 %d" % (i + 1)] = t or _id
    lines += [""] + points_block(points)
    data.update({"words_sum": total, "total_words": target, "unsupported": [u for u, _ in unsupported]})
    again = data.get("again")
    return {
        "card_id": "outline-v%d" % version, "title": title,
        "question": ("提纲第 %d 版可以定下来，继续写吗？" if again else "提纲第 %d 版可以定下来，开始写吗？") % version,
        "body": lines, "options": options_from(CONFIRM_OPTIONS["outline_again" if again else "outline"]), "footer": FOOTER_CONFIRM,
        "data": data, "scan": scan, "quotes": [(k, v, False) for k, v in scan.items()],
        "ask_lines": ["合计 %d 字%s" % (total, "（目标 %d 字）" % target if target is not None else ""),
                      "没有资料卡片支撑的节：%d 个" % len(unsupported)] + point_lines(points),
    }


def delivery_gate(project):
    """交付门槛(规格 §⑨-2):交付前检查全过 + 复核结果读得出、对得上当前成稿、必须改为 0 + 交付的三点齐全。
    → (gate 信息, 三点);不过 → Refuse。检查用 cite_check.py 与 pipeline_lib 的参考实现。"""
    problems = []
    files = pl.deliverable_paths(project)
    if not files:
        raise Refuse(["out/ 下还没有成稿文件：先生成网页版和 Word 版"])
    tmp = yzlib.work_tmp("cite")
    try:
        out_json = os.path.join(tmp, "cite.json")
        rc, out, err = yzlib.run_toolkit("cite_check.py", ["--project", project, "--json", out_json])
        report = None
        if os.path.isfile(out_json):
            with io.open(out_json, encoding="utf-8") as f:
                report = json.load(f)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    items = (report or {}).get("items") or []
    fails = [i for i in items if i.get("status") == "FAIL"]
    if rc != 0 or report is None:
        detail = "；".join("%s：%s%s" % (i.get("check"), i.get("detail"), "（改法：%s）" % i["fix_hint"] if i.get("fix_hint") else "")
                          for i in fails[:3]) or (err.strip()[-300:] or "没有输出")
        problems.append("交付前检查没有全过（cite_check 退出码 %s）：%s。修好再出交付卡" % (rc, detail))
    review_path = os.path.join(project, "review", "result.json")
    review = None
    if not os.path.isfile(review_path):
        problems.append("还没有独立复核结果（review/result.json）：先做独立复核并封存")
    else:
        try:
            with io.open(review_path, encoding="utf-8") as f:
                review = json.load(f)
        except ValueError:
            review = None
            problems.append("独立复核结果读不出来（review/result.json 不是合法的 JSON）")
        if review is not None:
            rp = pl.review_result_problems(review)
            if rp:
                problems.append("独立复核结果格式不对：%s" % "；".join(rp[:3]))
            else:
                ok, why = pl.review_applies(project, review)
                if not ok:
                    problems.append("独立复核结果对不上当前成稿（%s）：成稿复核之后又动过，要再复核一轮" % why)
                elif review["counts"]["must_fix"] != 0:
                    problems.append("独立复核还有 %d 处必须改：改完再复核一轮" % review["counts"]["must_fix"])
                else:
                    # 第三轮:三次复核都带着整段写作对话 —— 只认用 review.py 开始、封存的复核(没带对话、逐段核了、抽核了原文)
                    import review as review_mod
                    problems += review_mod.sealed_problems(project, review)
    problems += reference_locator_problems(project)
    dc = delivery_commitments_path(project)
    points = None
    if not os.path.isfile(dc):
        problems.append("交付的三点还没写：先写 library/delivery_commitments.md")
    else:
        meta, body, problem = pl.load_md(dc)
        if problem:
            problems.append("library/delivery_commitments.md 读不出来：%s" % problem)
        else:
            try:
                points = points_of(dc, body, whole=True)
            except Refuse as r:
                problems += r.problems
    if problems:
        raise Refuse(problems)
    gate = {"files": pl.deliverable_files(project), "checks": checks_counts(items),
            "review": {"round": review.get("round"), "counts": review.get("counts")},
            "commitments_digest": pl.file_sha256(dc)}
    return gate, points


def checks_counts(items):
    """交付前检查各项 → {total, pass, warn, fail}。只有客户端做得了的那一项(AGENT_SKIP_CHECKS:工作步数)不算:
    agent 版永远取不到,算进去就永远是「另有 1 项提醒」。"""
    kept = [i for i in items if i.get("check") not in AGENT_SKIP_CHECKS]
    return {"total": len(kept), "pass": sum(1 for i in kept if i.get("status") == "PASS"),
            "warn": sum(1 for i in kept if i.get("status") == "WARN"),
            "fail": sum(1 for i in kept if i.get("status") == "FAIL")}


def reference_locator_problems(project):
    """参考文献清单里的定位不能是抓取工具的行号(第三轮:「第127至138行」进了交付的参考文献)。"""
    path = os.path.join(project, "references.yaml")
    if not os.path.isfile(path):
        return []
    try:
        entries = pl.load_references(path)
    except Exception:
        return []
    out = []
    for e in entries if isinstance(entries, list) else []:
        if isinstance(e, dict):
            bad = locator_problem(e.get("locator"))
            if bad:
                out.append("参考文献清单里 %s 的定位写成了「%s」（抓取工具给的行号）：回到那张资料卡片改成章节名、标题或页码，"
                           "再跑 refs_add.py 更新清单、重新生成成稿" % (e.get("id") or e.get("url") or "", bad))
    return out


def checks_phrase(checks):
    """交付前检查的结果 →「15 / 15 通过」或「13 / 15 通过，另有 2 项提醒」:提醒不算通过。"""
    phrase = "%d / %d 通过" % (checks["pass"], checks["total"])
    if checks.get("warn"):
        phrase += "，另有 %d 项提醒" % checks["warn"]
    return phrase


def build_delivery(project, fields, log):
    plan_meta, _pb, plan_problem = yzlib.load_task_plan(project)
    if plan_meta is None or plan_problem:
        raise Refuse(["任务计划不在或读不出来"])
    state = state_of(plan_meta)
    if state == "delivered":
        raise Refuse(["这个项目已经交付了"])
    if state != "verifying":
        raise Refuse(["进度现在是 %s，不是 verifying：成稿生成之后先跑 $PY toolkit/scripts/stamp.py \"projects/%s/task_plan.md\" --advance verifying，再做交付前检查和独立复核"
                      % (state, yzlib.project_name(project))])
    gate, points = delivery_gate(project)
    formats = []
    names = [rel.lower() for rel in gate["files"]]
    if any(n.endswith(".docx") for n in names):
        formats.append("Word 版")
    if any(n.endswith(".html") for n in names):
        formats.append("网页版")
    counts = gate["review"]["counts"]
    review_line = "独立复核：必须改的 %d 处，说法收了 %d 处" % (counts["must_fix"], counts["tone_down"])
    stats = draft_stats(project)
    size_line = ("篇幅：%d 节 · 文内引用 %d 处 · 参考文献 %d 条" % (stats["sections"], stats["citations"], stats["references"])
                 if stats else None)
    lines = ["**%s**" % MATERIALS, "",
             "- 成稿（%s）%s" % (" · ".join(formats), view_token("draft"))]
    if size_line:
        lines.append("- %s" % size_line)
    lines += ["- 交付前检查：%s" % checks_phrase(gate["checks"]),
              "- %s" % review_line,
              ""] + points_block(points)
    shown = [e for e in log if e.get("type") == "prepared" and e.get("kind") == "delivery"]
    ids = []
    for e in shown:
        if e["card_id"] not in ids:
            ids.append(e["card_id"])
    open_id = shown[-1]["card_id"] if shown and not answered_after(log, shown[-1]) else None
    scan = {"交付三点 %d" % (i + 1): p for i, p in enumerate(points)}
    card_id = open_id or "delivery-%d" % (len(ids) + 1)
    data = dict(gate)
    data["formats"] = formats
    data["stats"] = stats
    return {
        "card_id": card_id, "title": "交付确认", "question": "成稿可以交付了吗？", "body": lines,
        "options": options_from(CONFIRM_OPTIONS["delivery"]), "footer": FOOTER_DELIVERY, "data": data,
        "scan": scan, "quotes": [(k, v, False) for k, v in scan.items()],
        "ask_lines": ["成稿：%s" % " · ".join(formats)] + ([size_line] if size_line else [])
                     + ["交付前检查：%s" % checks_phrase(gate["checks"]), review_line] + point_lines(points),
    }


_META_CELL_RE = re.compile(r'<div class="k">(章节|文内引用|参考来源)</div><div class="v">(\d+)</div>')


def draft_stats(project):
    """成稿的节数、文内引用处数、参考文献条数(第五轮 L3:交付卡前那条消息里的数由脚本读,不靠助手记)。
    先读交付的网页版成稿顶上的元信息条(render_html 生成时机器数的:章节 / 文内引用 / 参考来源)—— 用户拿到的
    就是这个文件;读不出再按底稿用同一份代码数(节 = 一级节 ##,文内引用 = 占位出现的次数,参考文献 = 正文里用到的条目数)。
    都读不出 → None,卡上就不写这一行。"""
    for rel, path in pl.deliverable_paths(project):
        if not rel.lower().endswith(".html"):
            continue
        try:
            with io.open(path, encoding="utf-8", errors="replace") as f:
                cells = dict(_META_CELL_RE.findall(f.read()))
        except OSError:
            cells = {}
        if len(cells) == 3:
            return {"sections": int(cells["章节"]), "citations": int(cells["文内引用"]),
                    "references": int(cells["参考来源"]), "from": rel}
    import review as review_mod
    draft = review_mod.draft_for(project)
    refs = os.path.join(project, "references.yaml")
    if not draft or not os.path.isfile(refs):
        return None
    try:
        import render_html
        _meta, body = pl.read_md(draft)
        entries = pl.load_references(refs)
        ph_no, numbered = pl.number_entries(body, entries)
        _title, body = pl.take_doc_title(body)
        _blocks, toc, _sub, _premise = render_html.parse_blocks(body, ph_no, dict(numbered))
        return {"sections": len(toc), "citations": pl.count_inline_citations(body, entries), "references": len(numbered),
                "from": os.path.relpath(draft, project).replace("\\", "/")}
    except Exception:   # noqa: BLE001  数不出来就不写这一行,不挡交付卡
        return None


def build_decision(project, fields, log):
    problems = []
    question, p = need_text(fields, "question", "问题，必须是一个选择", CAPS["question"])
    problems += p
    if question is not None:
        marks = sum(1 for ch in question if ch in "？?")
        if marks != 1 or question[-1] not in "？?":
            problems.append("question 要是一个选择：一句问题，以问号结尾，只问一件事")
    why, p = need_text(fields, "why", "卡在哪里、不问会怎样", CAPS["decision_why"])
    problems += p
    opts = fields.get("options")
    options = []
    if not isinstance(opts, list) or not 2 <= len(opts) <= 3:
        problems.append("options 要 2–3 个，每个 {label ≤20 字, effect ≤40 字, changes_plan 真或假}")
        opts = []
    labels = []
    for i, o in enumerate(opts):
        if not isinstance(o, dict):
            problems.append("options[%d] 要是 {label, effect, changes_plan}" % i)
            continue
        label, p1 = need_text(o, "label", "options[%d] 的做法" % i, CAPS["option_label"])
        effect, p2 = need_text(o, "effect", "options[%d] 会怎样" % i, CAPS["option_effect"])
        problems += p1 + p2
        if not isinstance(o.get("changes_plan"), bool):
            problems.append("options[%d] 要有 changes_plan（true 或 false）" % i)
        if label:
            problems += label_problems(label, i)
        labels.append(label)
        options.append({"key": str(i + 1), "label": label or "", "base": label or "",
                        "description": (effect or "") + (CHANGES_PLAN_NOTE if o.get("changes_plan") is True else ""),
                        "changes_plan": o.get("changes_plan") is True, "effect": effect})
    rec = fields.get("recommend")
    if rec is not None and not (isinstance(rec, int) and not isinstance(rec, bool) and 1 <= rec <= max(1, len(options))):
        problems.append("recommend 是从 1 数的选项序号；不推荐就不给这个键")
    if problems:
        raise Refuse(problems)
    for o in options:
        if rec is not None and o["key"] == str(rec):
            o["label"] = "%s · %s" % (o["base"], RECOMMEND_MARK["decision"])
    # 推荐的那一项排第一(key 还是 fields 里的序号:answer 交回的 selected 照 fields 的顺序数)
    recommended_first(options, None if rec is None else str(rec))
    collisions = label_collisions(options)
    if collisions:
        raise Refuse(collisions)
    plan_meta, _pb, _pp = yzlib.load_task_plan(project)
    stage = yzlib.stage_of_state(state_of(plan_meta)) or "task"
    n = len({e["card_id"] for e in log if e.get("type") == "prepared" and e.get("kind") == "decision"}) + 1
    cid = fields.get("id") if isinstance(fields.get("id"), str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,60}", fields.get("id") or "") else "decision-%d" % n
    scan = {"question": question, "why": why}
    for i, o in enumerate(options):
        scan["options[%d].label" % i] = o["base"]
        scan["options[%d].effect" % i] = o["effect"]
    return {
        "card_id": cid, "title": "需要你决定 · %s" % yzlib.stage_name(stage), "question": question,
        "body": ["**为什么要问**", "", why], "options": options, "footer": None,
        "data": {"stage": stage, "recommend": rec}, "scan": scan,
        "quotes": [(k, v, False) for k, v in scan.items()],
    }


def build_flow_change(project, fields):
    path, meta, body = load_doc(project, "task_plan.md")
    state = state_of(meta)
    if state not in ("collecting", "gate2_awaiting"):
        raise Refuse(["改流程卡只在收集资料这一步用（进度是 collecting 或 gate2_awaiting，现在是 %s）。还没确认过的任务计划直接改 stages，随任务计划卡一起确认；已经进了拟定提纲就用决定卡和用户商量" % state])
    ap = yzlib.approval_of(meta)
    if ap.get("status") != "approved" or not ap.get("approved_hash"):
        raise Refuse(["任务计划还没确认过：直接改 stages，随任务计划卡一起确认，不用改流程卡"])
    version = version_of(meta, "task_plan.md")
    if not yzlib.approval_stale(meta, body):
        raise Refuse(["任务计划第 %d 版现在的确认记录对得上（改流程已经确认过，或者还没改）：没有要确认的改流程。要改流程，先按 task-planner「只改流程」改 stages、version 和 revision_log" % version])
    if not yzlib.flow_change_logged(meta):
        raise Refuse(["revision_log 最后一条要写这次改流程：v 等于新的 version，what 以「改流程：」开头"])
    new_stages, err = pl.parse_stages(meta)
    if err:
        raise Refuse(["stages %s" % err])
    matched = yzlib.flow_change_match(meta, body)
    if matched is None:
        raise Refuse(["这一版不只改了流程：改流程只许动 stages、version（加一）和 revision_log，别的一个字都不动。还动了别处（或 version 没有加一），就要走完整的任务计划确认（先 --invalidate，再出任务计划卡）"])
    before = list(pl.STAGES) if matched in ("absent", None) else matched
    if before == new_stages:
        raise Refuse(["stages 没有变：这不是改流程"])
    changes, problems = need_text(fields, "changes", "改动环节：去掉或加回了哪一步", CAPS["changes"])
    if problems:
        raise Refuse(problems)
    steps = " → ".join(yzlib.stage_name(s) for s in new_stages)
    return {
        "card_id": "flow-change-v%d" % version, "title": "重新确认 · 任务计划（只改了流程）",
        "question": "任务计划第 %d 版可以定下来吗？" % version,
        "body": ["**%s**：任务计划第 %d 版%s" % (MATERIALS, version, view_token("task_plan")), "",
                 "**改动环节**：%s%s" % (changes, UNCHANGED), "", "**改完之后的步骤**：%s" % steps],
        "options": options_from(CONFIRM_OPTIONS["flow_change"]), "footer": FOOTER_CONFIRM,
        "data": {"doc": "task_plan.md", "version": version, "previous_version": version - 1,
                 "stages_before": before, "stages_before_raw": matched, "stages_after": new_stages,
                 "content_hash": pl.content_hash(meta, body), "changes": changes},
        "scan": {"changes": changes}, "quotes": [("changes", changes, False)],
        "ask_lines": ["改动环节：%s%s" % (changes, UNCHANGED), "改完之后的步骤：%s" % steps],
    }


def list_projects():
    root = yzlib.projects_root()
    if not os.path.isdir(root):
        return []
    out = []
    for name in os.listdir(root):
        p = os.path.join(root, name)
        if name.startswith((".", "_")) or not os.path.isdir(p):
            continue
        out.append((os.path.getmtime(p), name, p))
    out.sort(reverse=True)
    return [(n, p) for _, n, p in out]


def build_pick_project(fields):
    import projects as projects_mod
    items = list_projects()
    if len(items) < 2:
        raise Refuse(["只有 %d 个项目，不用选：直接接着做（projects.py status）或新建" % len(items)])
    lines = ["**你现在有 %d 个项目**" % len(items), ""]
    infos = []
    for name, p in items:
        info = projects_mod.status_of(p)
        infos.append((name, info))
        lines.append("- %s：%s" % (name, info["headline"]))
    options = [{"key": name, "label": name, "base": name, "description": info["headline"]} for name, info in infos[:3]]
    if len(items) > 3:
        lines += ["", "卡上只列了最近动过的 3 个；要做别的项目，直接在卡上写项目名。"]
    return {
        "card_id": "pick-project", "title": "接着做哪个项目", "question": "接着做哪个项目？", "body": lines,
        "options": options, "footer": None, "data": {"projects": [n for n, _ in items]}, "scan": {}, "quotes": [],
    }


# ---- 卡片记录 ----

def answered_after(log, prepared):
    """这张出过的卡后面有没有回答(answered / skipped / changed 都算处理过;unclear 不算)。"""
    seen = False
    for e in log:
        if e is prepared:
            seen = True
            continue
        if seen and e.get("card_id") == prepared.get("card_id") and e.get("type") in yzlib.CARD_CLOSED:
            return True
    return False


def card_mode(project=None, override=None):
    if override == "text":
        return "text", "选项框工具用不了，改用文字卡"
    env = (os.environ.get("YUNZHI_CARDS") or "").strip().lower()
    if env in ("text", "native"):
        return env, "环境变量 YUNZHI_CARDS=%s" % env
    if project and os.path.isfile(os.path.join(project, ".text-cards")):
        return "text", "项目文件夹里有 .text-cards"
    if os.path.isfile(os.path.join(yzlib.projects_root(), ".text-cards")):
        return "text", "projects 文件夹里有 .text-cards"
    return "native", "默认：原生选项卡"


def _compose(card, footer):
    head = "**%s** · %s" % (card["title"], BADGE)
    lines = [head, ""] + card["body"]
    if footer:
        lines += ["", footer]
    return "\n".join(lines).strip() + "\n"


def render(card, mode):
    footer = card.get("footer")
    text_footer = TEXT_FOOTERS.get(footer, footer)
    message = _compose(card, text_footer if mode == "text" else footer)
    if mode != "text":
        # 第五轮 H2:原生卡没人回答约 3 分钟会被自动收起;每张原生卡前都说一声(脚本写,不靠助手记)。文字卡不会收起,不加
        message += "\n" + NATIVE_COLLAPSE_NOTE + "\n"
    header = HEADERS.get(card["kind"])
    if header is None:   # 三次确认:Codex 不显示 header,这里只放一个短标签
        pos = card["data"].get("position")
        header = "重新确认" if card["data"].get("again") else ("第 %d 次确认" % pos if pos else "确认")
    # 确认类的卡:要紧的内容(三点、检查和复核的结果、初步结论)也放进原生选项卡的问题里 ——
    # 卡前那条消息万一没发出去(第三轮:上下文压缩之后没重读这份说明),用户在卡上照样看得到
    question = card["question"]
    if card.get("ask_lines"):
        question += "\n" + "\n".join("· %s" % line for line in card["ask_lines"])
    ask = {"questions": [{
        "header": header,
        "id": card["card_id"],
        "question": question,
        "options": [{"label": o["label"], "description": o["description"]} for o in card["options"]],
    }]}
    fb = [_compose(card, text_footer).rstrip(), "", "**%s**" % card["question"], ""]
    for i, o in enumerate(card["options"], start=1):
        fb.append("%d. %s：%s" % (i, o["label"], o["description"]))
    fb += ["", FALLBACK_TAIL]
    return message, ask, "\n".join(fb) + "\n"


def refresh_progress(project):
    try:
        import progress
        return progress.page_report(project)
    except Exception as e:  # 进度页出错不挡卡片流程
        return {"progress_page_error": "%s: %s" % (type(e).__name__, e)}


_VIEW_TOKEN_RE = re.compile("\x00VIEW:([a-z_]+):(main|link)\x00")


def link_target(path):
    """点开看的地址:file:/// 开头,放在 <> 里(路径里有空格、括号也不断开)。"""
    import progress
    return "<%s>" % progress.file_url(path)


def readable_place(project, path):
    """材料网页在项目文件夹里的位置,写成用户看得懂的一句(文字卡里不放 file:/// 地址)。"""
    rel = os.path.relpath(path, project).replace("\\", "/")
    folder, name = (rel.rsplit("/", 1) + [""])[:2] if "/" in rel else ("", rel)
    stem = os.path.splitext(name or rel)[0]
    if folder == "out":
        return "项目文件夹的成稿文件夹里，网页版「%s」" % stem
    if folder:
        return "项目文件夹的「%s」文件夹里，网页「%s」" % (folder, stem)
    return "项目文件夹里，网页「%s」" % stem


def fill_views(project, card, mode="native", pane_open=False):
    """出卡检查都过了:把卡上提到的材料生成好读的网页(view.py),写在「依据的材料」那一行末尾。
    第四轮:「右侧已打开」只在这个对话里真的挂过右侧时写(prepare 带 --pane-open;命令行、WorkBuddy 没有右侧);
    文字卡里不放 file:/// 地址(用户点不了,只看见一长串路径),写一句「全文在项目文件夹的……里」。
    → (主要那一页 {kind, page, url} 或 None, 生成出错的说明列表)。页面生成出错不挡卡片:那一行就不写位置。"""
    import progress
    import view
    tokens = [(m.group(1), m.group(2)) for line in card["body"] for m in _VIEW_TOKEN_RE.finditer(line)]
    main = next((k for k, role in tokens if role == "main"), None)
    pages, errors = {}, []
    for k in sorted({k for k, _role in tokens}):
        try:
            path, _title = view.render(project, k)
            pages[k] = path
        except Exception as e:  # noqa: BLE001
            errors.append("%s：%s" % (k, e))

    def sub(m):
        k, role = m.group(1), m.group(2)
        if k not in pages:
            return ""
        where = ("全文在%s" % readable_place(project, pages[k]) if mode == "text"
                 else "[%s](%s)" % (VIEW_LINK, link_target(pages[k])))
        if role == "main" and pane_open:
            return "（%s · %s）" % (VIEW_OPEN, where)
        return "（%s）" % where
    card["body"] = [_VIEW_TOKEN_RE.sub(sub, line) for line in card["body"]]
    page = {"kind": main, "page": pages[main], "url": progress.file_url(pages[main])} if main in pages else None
    return page, errors


def prepare(kind, project_arg, fields, card_id=None, mode=None, pane_open=False):
    if kind not in KINDS:
        raise UsageError("kind 只能是 %s" % " / ".join(KINDS))
    if mode not in (None, "text"):
        raise UsageError("--mode 只能写 text（选项框工具用不了，改用文字卡）")
    if not isinstance(fields, dict):
        raise UsageError("fields 要是一个 JSON 对象")
    if kind == "pick_project":
        project = None
        log_path = os.path.join(yzlib.projects_root(), ".records", "cards.jsonl")
    else:
        project = yzlib.project_dir(project_arg)
        log_path = yzlib.card_log(project)
    log = yzlib.read_jsonl(log_path)
    try:
        if project:
            # 第五轮 M3 / M4:项目文件夹里有助手写的脚本、inputs/ 里混进了不是用户给的文件 → 先收拾好再出卡
            messy = yzlib.hygiene_problems(project)
            if messy:
                raise Refuse(messy)
        if kind == "report_type":
            card = build_report_type(project, fields)
        elif kind in ("task_plan", "dossier", "outline"):
            card = {"task_plan": build_task_plan, "dossier": build_dossier, "outline": build_outline}[kind](project, fields)
        elif kind == "delivery":
            card = build_delivery(project, fields, log)
        elif kind == "decision":
            card = build_decision(project, fields, log)
        elif kind == "flow_change":
            card = build_flow_change(project, fields)
        else:
            card = build_pick_project(fields)
        if card_id:
            card["card_id"] = card_id
        card["kind"] = kind
        # 用词检查与引文核对:扫 agent 写的空和卡上列出的材料文字(fields、三点、初步结论的理由、判定标准、
        # 没有资料卡片支撑的节);项目外文件的名字在 outside_block 里已经扫过(扫出问题就不写名字)
        utexts = yzlib.user_texts(project) if project else []
        problems = []
        for field, text in card["scan"].items():
            hits = wording_check.scan(text, utexts, field)
            if hits:
                problems.append(wording_check.describe(hits))
        for field, text, require in card["quotes"]:
            problems += wording_check.quote_problems(text, utexts, field, require=require)
        if problems:
            raise Refuse(problems)
    except Refuse as r:
        cid = card_id or {"report_type": "report-type", "pick_project": "pick-project"}.get(kind, kind)
        yzlib.append_jsonl(log_path, {"type": "refused", "at": yzlib.now_iso(), "card_id": cid, "kind": kind,
                                      "problems": r.problems})
        streak = 0
        for e in reversed(yzlib.read_jsonl(log_path)):
            if e.get("kind") != kind:
                continue
            if e.get("type") == "refused":
                streak += 1
            else:
                break
        out = {"ok": False, "kind": kind, "refuse": r.problems[0], "problems": r.problems}
        if streak >= 3:
            out["tell_user"] = "这张卡连着 %d 次没准备好：把 say 那句告诉用户，再用一句研究上的话说你打算怎么改，改好再出卡" % streak
            out["say"] = "这张卡我还没准备好，卡上有几处要先改好。我改好再请你看。"
        return out, 1
    mode, why = card_mode(project, mode)
    view_page, view_errors = fill_views(project, card, mode, pane_open) if project else (None, [])
    message, ask, fallback = render(card, mode)
    stored = []
    for o in card["options"]:
        s = {k: o[k] for k in ("key", "label", "base")}
        if "changes_plan" in o:
            s["changes_plan"] = o["changes_plan"]
        stored.append(s)
    # 第六轮:ask 原样记下(守门脚本只放行和它按结构一致的 request_user_input);ts 是出卡的纪元秒 ——
    # 回答只认这之后守门脚本记下的用户原话和卡上交回的原文(比 at 的秒更细)
    event = {"type": "prepared", "at": yzlib.now_iso(), "ts": time.time(), "card_id": card["card_id"], "kind": kind,
             "mode": mode, "title": card["title"], "question": card["question"], "options": stored,
             "data": card["data"], "fields": fields, "ask": ask}
    if project:
        # 出卡这一刻用户消息记录有几条:回答时只跟这之后记的比(按先后,不按秒数比 —— 同一秒里的先后分不出来)
        event["messages_before"] = len(yzlib.read_jsonl(yzlib.message_log(project)))
    yzlib.append_jsonl(log_path, event)
    out = {"ok": True, "card_id": card["card_id"], "kind": kind, "mode": mode, "mode_reason": why,
           "message": message, "ask": ask, "fallback_text": fallback}
    pname = '"%s"' % yzlib.project_name(project) if project else ""
    answer_file = yzlib.inbox_file(yzlib.project_name(project) if project else yzlib.PICK_INBOX, "answer.txt")
    answer_cmd = "$PY \"agent-tools/card.py\" answer %s --card %s --answer-file \"%s\"" % (pname, card["card_id"], answer_file)
    answer_cmd = re.sub(r"\s+", " ", answer_cmd)
    out["answer_file"] = answer_file
    first = ""
    if view_page:
        out["view"] = view_page
        out["view_url"] = view_page["url"]
        out.update(yzlib.pane_info(project, view_page["page"], "view:%s" % view_page["kind"]))
        if "pane" not in out:
            first = "右侧那一页没换成（pane_error）：卡上的材料位置照样写着，照常出卡。"
        elif pane_open:
            first = "右侧那一页已经换成这份材料（卡上「依据的材料」写了右侧已打开），不用另外打开。"
        else:
            first = ("右侧那一页已经换成这份材料，不用另外打开。你没带 --pane-open，卡上就没写「右侧已打开」：这个对话里挂过右侧的话，"
                     "下一张卡记得带 --pane-open；没挂过（命令行、WorkBuddy 没有右侧）就照这样出卡。")
    if view_errors:
        out["view_errors"] = view_errors
    if project:
        yzlib.update_progress_md(project)
    neutral = ("answer 返回之前不对用户说结果（不说「好，已定下」「确认了」「按你的意见改」）：要说话最多一句「收到，我记一下。」，"
               "结果照 answer 输出的 result 和 say 说。")
    if mode == "text":
        out["next"] = (first + "文字卡模式：原样发出 fallback_text（不要调选项框工具），然后停下等用户回复。"
                       "用户回复后，把他那条消息原样写进回答文件，跑：%s。%s" % (answer_cmd, neutral))
    else:
        out["next"] = (first + "原样发出 message，再调选项框工具（Codex：request_user_input，参数就是 ask —— 用会等回答的这一个，"
                       "不用 request_user_input_async；WorkBuddy：AskUserQuestion）。"
                       "拿到返回后把返回原文写进回答文件，马上跑：%s。%s没有选项框工具或调用报错：重新跑这条 prepare，加 --mode text，"
                       "发它的 fallback_text，把用户下一条消息当回答" % (answer_cmd, neutral))
    if project:
        out.update(refresh_progress(project))
    return out, 0


# ---- 回答 ----

_NOT_JSON = object()
# 第四轮:原生卡上选了一项、又在意见框里写了字,Codex 多半回两串:选项名 + 「user_note: <写的话>」
# (codex.exe 里有 "user_note:" 和 "Add notes" 这两串字)。只写意见、没选项时回的是那段话本身(10-02 实测),不带前缀。
USER_NOTE_RE = re.compile(r"\A\s*user_note\s*[:：]\s*(.*)\Z", re.S)
QUOTE_CAP = 40


def _one_answer(entry, options=()):
    """选项框返回里一道题的回答 → (那一串字, 附的意见或 None);认不出 → None。
    - 恰好一串非空的字:选项名,或者用户写的话(原样);以「user_note:」开头的去掉前缀,当用户写的话;
    - 恰好两串:一串正好是这张卡的选项名、另一串以「user_note:」开头 → (选项名, 意见)。
    其余(空列表、三串、两串都不是选项名、两串都是选项名……)一律认不出。"""
    lst = entry.get("answers") if isinstance(entry, dict) else None
    if not isinstance(lst, list) or not all(isinstance(x, str) and x.strip() for x in lst):
        return None
    if len(lst) == 1:
        m = USER_NOTE_RE.match(lst[0])
        text = m.group(1).strip() if m else lst[0].strip()
        return (text, None) if text else None
    if len(lst) == 2:
        notes = [USER_NOTE_RE.match(x) for x in lst]
        if sum(1 for m in notes if m) == 1:
            k = 0 if notes[0] else 1
            label, note = lst[1 - k].strip(), notes[k].group(1).strip()
            if match_option(label, options, allow_number=False)[0] is not None:
                return label, (note or None)
    return None


def _id_key(s):
    """卡号比对时不计 - 与 _、不分大小写(第三轮:agent 把 dossier-v1 写成了 dossier_v1,用户只好点两次)。"""
    return str(s).replace("_", "-").lower()


def parse_answer(raw, card_id, options=()):
    """→ (shape, text, note):shape ∈ tool(选项框返回)· chat(对话里的一条消息)· skipped · unclear;
    note 是选了一项之外附的意见(只有选项框返回「选项名 + user_note: …」时有)。
    选项框的返回按卡号找那道题;卡号对不上时,只差 - / _ / 大小写的也认;返回里恰好只有一道题、
    回答又正好是这张卡的某个选项名,也认。"""
    text = (raw or "").strip()
    try:
        obj = json.loads(text)
    except ValueError:
        obj = _NOT_JSON
    if isinstance(obj, dict):
        answers = obj.get("answers")
        if not isinstance(answers, dict):
            return "unclear", None, None
        if not answers:
            return "skipped", None, None
        entry = answers.get(card_id)
        if entry is None:
            near = [v for k, v in answers.items() if _id_key(k) == _id_key(card_id)]
            if len(near) == 1:
                entry = near[0]
            elif len(answers) == 1:
                only = _one_answer(next(iter(answers.values())), options)
                if only is not None and match_option(only[0], options, allow_number=False)[0] is not None:
                    return "tool", only[0], only[1]
        got = _one_answer(entry, options)
        return ("tool", got[0], got[1]) if got is not None else ("unclear", None, None)
    if isinstance(obj, list):
        return "unclear", None, None
    if isinstance(obj, str):
        text = obj.strip()
    if not text:
        return "unclear", None, None
    if text in SKIP_WORDS:
        return "skipped", None, None
    return "chat", text, None


_NUMBER_RE = re.compile(r"(?:选|选择|第)?\s*([1-9])\s*(?:个|项|号)?\s*[.。、)）]?")


def match_option(s, options, allow_number):
    """回答 → 选的是第几项。按选项名认(带不带推荐标记都行,不计空格和全角半角);
    allow_number(只有文字卡模式出的卡)时也认「1」「选 2」「第 3 个」。"""
    s0 = _fold_label(s)
    for i, o in enumerate(options):
        if s0 in (_fold_label(o.get("label")), _fold_label(o.get("base"))):
            return i, "option"
    if allow_number:
        m = _NUMBER_RE.fullmatch(s0)
        if m and 1 <= int(m.group(1)) <= len(options):
            return int(m.group(1)) - 1, "number"
    return None, None


def _split_note(rest):
    """数字或选项名后面剩下的那段 → (是不是隔开了, 附的意见或 None):
    - 什么都没有 → (True, None);
    - 以全角的「，」「；」「：」隔开 → 后面不管跟什么都是意见(第八轮:「1，2021 年的数据也要」);
    - 以别的标点隔开(,、. 。:;）)→ 标点后面的话是意见;标点后面紧跟数字的不算(「1.5 倍」「1,000 辆」「1、2 都要」);
    - 以空白隔开 → 空白后面的话是意见;紧跟数字或量词的不算(「3 个方面都要写」);
    - 紧跟着别的字(「3个方面」「2023年」)→ (False, None):不是在选第几项。"""
    if not rest.strip():
        return True, None
    stripped = rest.lstrip()
    if stripped[0] in HARD_SEPARATORS:
        note = stripped[1:].lstrip().lstrip(NOTE_SEPARATORS).strip()
        return True, (note or None)
    if stripped[0] in NOTE_SEPARATORS:
        after = stripped[1:].lstrip()
        if after[:1].isdigit():
            return False, None
        note = after.lstrip(NOTE_SEPARATORS).strip()
        return True, (note or None)
    if len(stripped) < len(rest):           # 隔开的是空白
        if stripped[0].isdigit() or stripped[0] in UNIT_AFTER:
            return False, None
        return True, stripped.strip()
    return False, None


def read_reply(text, options):
    """一条回答(文字卡里用户回的消息、原生卡上用户写的那段话、选项框交回的选项名)→ (第几项或 None, 怎么认出的, 附的意见或 None)。
    第五轮 H1,文字卡和原生卡一样认(原生卡上的选项也有编号):
    ① 整串就是某个选项名(带不带推荐标记、不计空格和全角半角),或者只是一个数字(「2」「选 2」「第 2 个」);
    ② 开头是数字,隔着标点或空白接一句话(「1，判断标准里要把透支效应算进去」「选择2，…」「第2个，…」「1）…」);
    ③ 开头是某个选项名,隔着标点或空白接一句话。
    第八轮:全角「，」「；」「：」后面不管跟什么都算隔开(「1，2021 年的数据也要」= 第 1 项 + 意见)。
    不认(照旧算意见):数字后面紧跟数字(「2023年的数据也要」)、紧跟量词(「3个方面都要写」)、超出选项数、
    数字不在开头(「我选1吧」)、半角「,」后跟数字(「1,000 辆」)、「、」「.」后跟数字(「1、2 都要」「1.5 倍」)。"""
    s = (text or "").strip()
    if not s:
        return None, None, None
    idx, how = match_option(s, options, allow_number=True)
    if idx is not None:
        return idx, how, None
    m = LEAD_NUMBER_RE.match(s)
    if m:
        ok, note = _split_note(s[m.end():])
        n = int(unicodedata.normalize("NFKC", m.group(1)))
        if ok:
            if 1 <= n <= len(options):
                return n - 1, ("number+note" if note else "number"), note
            return None, None, None
    best = None
    for i, o in enumerate(options):
        for cand in {o.get("label"), o.get("base")}:
            folded = _fold_label(cand)
            if not folded:
                continue
            for k in range(1, len(s) + 1):
                got = _fold_label(s[:k])
                if got == folded:
                    ok, note = _split_note(s[k:])
                    if ok and note and (best is None or k > best[0]):
                        best = (k, i, note)
                    break
                if len(got) > len(folded) or not folded.startswith(got):
                    break
    if best:
        return best[1], "option+note", best[2]
    return None, None, None


def find_prepared(log, card_id):
    for e in reversed(log):
        if e.get("type") == "prepared" and e.get("card_id") == card_id:
            return e
    return None


def closing_events(log, prepared):
    """这张出过的卡后面的「处理过」的记录(yzlib.CARD_CLOSED 那几种),按先后。"""
    out, seen = [], False
    for e in log:
        if e is prepared:
            seen = True
            continue
        if seen and e.get("card_id") == prepared.get("card_id") and e.get("type") in yzlib.CARD_CLOSED:
            out.append(e)
    return out


def latest_prepared(log):
    return next((e for e in reversed(log) if e.get("type") == "prepared"), None)


def reopen_reason(closing):
    """一张卡后面的处理记录 → 它还能不能再回答一次:"skipped" / "note" / None。
    后面恰好只有一条处理记录,而且是 ——
    - 「跳过」(第五轮 H2:用户点了跳过,或者桌面版太久没操作自动收起,交回的都是 {"answers":{}});或者
    - 只写了意见、没选选项的回答(第七轮:确认类记成先不确认,报告类型 / 决定卡记成 note;10-04 冒烟:任务计划卡收到
      「直接帮我确认吧」之后,用户回「1」被拒,助手只好把同一张卡重出一遍)。
    明明白白点了「先不确认」、认不出(unclear)、材料变了(changed)、出错(error)的都不算;已经再答过一次的也不算(只此一次)。"""
    if len(closing) != 1:
        return None
    last = closing[0]
    if last.get("type") == "skipped":
        return "skipped"
    if last.get("type") == "answered" and last.get("choice") is None and last.get("result") in NOTE_RESULTS:
        return "note"
    return None


def reopenable_card(project):
    """最近出的那张卡还能再回答一次吗 → (出卡记录, "skipped" / "note");不能 → (None, None)。
    只看最近出的那张(之后出过别的卡,就不算了)。这时用户回它的数字或选项名,还算这张卡的回答。"""
    log = yzlib.read_jsonl(yzlib.card_log(project))
    last = latest_prepared(log)
    if last is None:
        return None, None
    why = reopen_reason(closing_events(log, last))
    return (last, why) if why else (None, None)


def event_ts(event):
    """卡片记录里一件事的纪元秒:新记录有 ts;老记录只有到秒的 at(取那一秒的开头,只会更早)。读不出 → None。"""
    t = event.get("ts")
    if isinstance(t, (int, float)) and not isinstance(t, bool):
        return float(t)
    at = yzlib.parse_iso(event.get("at"))
    return at.timestamp() if at else None


# ---- 第六轮:守门脚本在跑时,回答要是用户真给的 ----
HOOK_CHAT_REFUSE = ("这份回答对不上用户在这张卡出了之后说过的话（守门脚本记下了用户的每一条原话）：把用户回的那条消息一字不改写进回答文件，"
                    "再跑一次；用户还没回，就等他回，不要替他回答。")
HOOK_NATIVE_REFUSE = ("这份回答对不上选项框交回的原文（守门脚本记下了卡上交回的东西）：把 request_user_input 返回的原文一字不改写进"
                      "回答文件，再跑一次。")
HOOK_NOT_ASKED = ("这个对话里这张卡没有经选项框问过，也没有交回的回答：先照 card.py prepare 的输出发卡（原生卡调 request_user_input），"
                  "拿到用户的回答再跑 answer；不要替用户回答。")


def prepared_ts(prepared):
    """出卡的纪元秒;老的出卡记录只有到秒的 at:往前放一秒(同一秒里的先后分不出来)。"""
    t = prepared.get("ts")
    if isinstance(t, (int, float)) and not isinstance(t, bool):
        return float(t)
    at = yzlib.parse_iso(prepared.get("at"))
    return at.timestamp() - 1 if at else None


def hook_verdict(prepared, raw, text, since=None):
    """守门脚本在跑(当前会话 CODEX_THREAD_ID 有它的记录)时核回答 → (结论, 退回的话或 None)。结论:
    off(守门脚本没跑:照旧,不核)· verified(核过)· unverified(问过这张卡,但选项框交回的原文没记下:
    放行,记一笔)· mismatch / not_asked(退回)。
    - 对话里的回答(文字卡、卡收起后回的数字):要和这张卡出了之后守门脚本记下的某一条用户原话一字不差(只去掉首尾空白);
    - 选项框交回的(JSON):要和守门脚本在这张卡出了之后记下的、卡上交回的原文按结构一致。
    since:从哪一刻算起(纪元秒);不给就从出卡那一刻算。只收到意见之后再答的那一次,从意见那条记录算起
    (第七轮:意见之前说的话,不能拿来当这一次的回答);卡收起之后的那一次照旧从出卡算起。"""
    sid = sessions.current_session_id()
    if not sid or not sessions.active(sid):
        return "off", None
    if since is None:
        since = prepared_ts(prepared)
    try:
        obj = json.loads((raw or "").strip())
    except ValueError:
        obj = _NOT_JSON
    if not isinstance(obj, (dict, list)):
        said = sessions.norm_text(text if isinstance(text, str) and text else (obj if isinstance(obj, str) else raw))
        if said and any(sessions.norm_text(p) == said for _t, p in sessions.prompts(sid, after=since)):
            return "verified", None
        return "mismatch", HOOK_CHAT_REFUSE
    recs = sessions.card_records(sid, prepared.get("card_id"), after=since)
    answers = [r for r in recs if r.get("event") == "card_answer"]
    if answers:
        last = answers[-1]
        if last.get("response") is None:
            return "unverified", None
        if sessions.canon(last["response"]) == sessions.canon(obj):
            return "verified", None
        return "mismatch", HOOK_NATIVE_REFUSE
    if any(r.get("event") == "card_asked" for r in recs):
        return "unverified", None
    return "not_asked", HOOK_NOT_ASKED


def prepared_problems(prepared):
    """出卡记录那一行读不读得出来(被手改坏、截断时不让 answer 崩)。"""
    out = []
    kind = prepared.get("kind")
    if kind not in KINDS:
        out.append("kind")
    opts = prepared.get("options")
    if not isinstance(opts, list) or not opts or not all(
            isinstance(o, dict) and all(isinstance(o.get(k), str) and o.get(k) for k in ("key", "label", "base")) for o in opts):
        out.append("options")
    elif kind == "decision" and not all(o["key"].isdigit() for o in opts):
        out.append("options 的 key")
    data = prepared.get("data", {})
    if not isinstance(data, dict):
        out.append("data")
    elif kind in ("task_plan", "dossier", "outline", "flow_change") and data.get("doc") not in DOC_OF.values():
        out.append("data.doc")
    return out


def snapshot_line(project, snapshot_dir):
    if yzlib.inside_protected(snapshot_dir):
        # 10-03 端到端实测:没有可视化目录时 agent 把快照写进了 out/,被当成成稿,交付卡对不上
        return {"snapshot_skipped": "快照只能放在对话的可视化目录里，不能放进工作区（会被当成成稿或材料）：这一轮不贴快照"}
    try:
        import progress
        path, line = progress.snapshot(project, snapshot_dir)
        return {"snapshot": path, "visualize": line}
    except Exception as e:  # 快照出错不挡回答
        return {"snapshot_error": "%s: %s" % (type(e).__name__, e)}


def finish(out, project, kind, snapshot_dir):
    """回答处理完:刷新进度页,把右侧那一页换回进度页;带了可视化目录就生成快照(贴在这一轮最后一条回复里)。"""
    if not project:
        return out
    yzlib.update_progress_md(project)        # PROGRESS.md 第一行跟着进度走(第三轮:一直停在 planning)
    out.update(refresh_progress(project))
    tail = []
    if out.get("progress_page"):
        out.update(yzlib.pane_info(project, out["progress_page"], "progress"))
    if kind in CONFIRM_KINDS and out.get("pane"):
        tail.append("右侧已经换回进度页（不用调任何工具）。")
    if snapshot_dir:
        out.update(snapshot_line(project, snapshot_dir))
        if out.get("snapshot_skipped"):
            tail.append(out["snapshot_skipped"] + "。")
        if out.get("visualize"):
            tail.append("这一轮的最后一条回复里，单独一行原样放 visualize 那一行（前后不加字、不放进代码块）；"
                        "别放进调选项框工具之前的那条消息。")
    if tail:
        out["next"] = " ".join(tail) + " " + out.get("next", "")
    return out


def answer(project_arg, card_id, raw, snapshot_dir=None):
    if card_id == "pick-project" and not project_arg:
        project = None
        log_path = os.path.join(yzlib.projects_root(), ".records", "cards.jsonl")
    else:
        project = yzlib.project_dir(project_arg)
        log_path = yzlib.card_log(project)
    log = yzlib.read_jsonl(log_path)
    prepared = find_prepared(log, card_id)
    if prepared is None:
        raise UsageError("没有卡号 %s 的出卡记录：先跑 prepare 出卡，用它输出的 card_id" % card_id)
    bad = prepared_problems(prepared)
    if bad:
        raise UsageError("卡 %s 的出卡记录读不出来（records/cards.jsonl 里那一行的 %s 不对，可能被改坏了）：重新跑 prepare 出这张卡"
                         % (card_id, "、".join(bad)))
    # 第五轮 H2:卡被收起(点了跳过,或者桌面版太久没操作自动收起 —— 交回的都是 {"answers":{}})不等于用户不要了;
    # 第七轮:只写了意见、没选选项的回答之后也一样。之后用户回的那条消息认得出是这张卡的某一项,就还算这张卡的回答 ——
    # 只此一次,这期间没出过别的卡;材料自出卡以来没变由下面各种卡自己核(确认类核内容 hash,变了就不签)
    closing = closing_events(log, prepared)
    reopen = reopen_reason(closing)
    if closing and not reopen:
        raise UsageError("卡 %s 已经回答过了（认不出的回答也算答过）：要再问用户，重新跑 prepare 出卡" % card_id)
    if reopen and latest_prepared(log) is not prepared:
        raise UsageError("卡 %s %s之后又出过别的卡：这条消息不算它的回答。要定这件事，重新跑 prepare 出这张卡"
                         % (card_id, REOPEN_WORDS[reopen]))
    kind = prepared["kind"]
    options = prepared["options"]
    shape, text, extra_note = parse_answer(raw, card_id, options)
    if reopen and (shape not in ("chat", "tool") or read_reply(text, options)[0] is None):
        raise UsageError("这条消息认不出是在回答刚才%s的卡 %s（开头不是选项前面的数字，也不是选项名）：照常当对话回应，"
                         "不用跑 answer；要再问就重新跑 prepare 出这张卡" % (REOPEN_WORDS[reopen], card_id))
    # 第六轮:守门脚本在跑时,回答要对得上它记下的用户原话 / 卡上交回的原文;对不上就退回,这张卡照旧开着。
    # 第七轮:只写了意见之后再答的那一次,只认用户在意见之后说的话 —— 意见之前说的「1」是被意见改掉的那个主意。
    # 卡收起之后的那一次照旧从出卡算起:收起什么都没说,卡还开着时用户在对话里回的数字也是真的回答
    since = None
    if reopen == "note":
        since = max(t for t in (prepared_ts(prepared), event_ts(closing[0]), 0.0) if t is not None)
    verdict, problem = hook_verdict(prepared, raw, text, since=since)
    if problem:
        yzlib.append_jsonl(log_path, {"type": "answer_refused", "at": yzlib.now_iso(), "card_id": card_id, "kind": kind,
                                      "raw": raw, "hook_check": verdict, "why": problem})
        return {"ok": False, "card_id": card_id, "kind": kind, "refuse": problem, "problems": [problem],
                "hook_check": verdict}, 1
    base = {"at": yzlib.now_iso(), "ts": time.time(), "card_id": card_id, "kind": kind, "raw": raw, "recorded_by": "助手"}
    if verdict != "off":
        base["hook_check"] = verdict
    if reopen == "skipped":
        base["after_skip"] = True
    elif reopen == "note":
        base["after_note"] = True
    if shape == "unclear":
        # 认不出就关掉这张卡:要再问,问清之后重新出卡(不留一张谁都能接着回答的卡)
        yzlib.append_jsonl(log_path, dict(base, type="unclear"))
        out = {"ok": True, "result": "unclear", "card_id": card_id, "kind": kind,
               "next": "认不出这个回答的样子（已经原样存下，这张卡关了）。在对话里用一句话问清用户选的是哪一项；不要猜，也不要当成选了推荐的那项。问清后重新跑 prepare 出这张卡。",
               "say": "我没看清你在卡上选的是哪一项，能再说一下吗？"}
        return finish(out, project, kind, snapshot_dir), 0
    if shape == "skipped":
        words = chat_words(raw)
        if project and words in SKIP_WORDS:
            # 第八轮:文字卡上用户回的「跳过」也是他说的话,和别的回答一样带上 via 与卡号(选项框交回的 {} 没有话可记)
            record_user_message(project, words, "文字卡回答", card_id=card_id, since=prepared.get("at"),
                                after=prepared.get("messages_before"))
        yzlib.append_jsonl(log_path, dict(base, type="skipped", choice=None, note=None))
        out = {"ok": True, "result": "skipped", "card_id": card_id, "kind": kind,
               "next": ("卡收起来了（用户点了跳过，或者太久没操作被自动收起，两种交回的一样）：没有确认、不推进，什么都不改。"
                        "说 say 里那句话，然后停下等用户。用户之后回的消息，记用户消息时 note-user 会告诉你它像不像是在回答这张卡；"
                        "像的话照它说的跑 answer（这张卡还能回答一次，不用重新出卡）。"),
               "say": skipped_say(kind, prepared.get("mode"), options)}
        if kind in ("report_type", "decision"):
            out["selected"] = None
        return finish(out, project, kind, snapshot_dir), 0
    # 第五轮 H1:数字、选项名,后面还可以隔着标点接一句意见;文字卡和原生卡一样认
    idx, how, typed_note = read_reply(text, options)
    chosen = options[idx] if idx is not None else None
    # 选了一项又附了意见(「2，……」、选项名 + user_note:…):意见照记,选的那一项照算;没选项:整段话就是意见
    note = (typed_note or extra_note) if chosen is not None else text
    # 用户自己写的话(文字卡模式下回的消息、卡上写的那段话)也记进消息记录:以后卡上引用他的话要对得上。
    # 点了选项(交回的正好是选项名)不算用户写的话;用户写了字就记他写的整段。
    # 这张卡出了之后 note-user 已经记过同一句的,不再记一遍(只在这张卡的范围里比,不跟更早的话比)
    if shape == "chat" or chosen is None or typed_note:
        words = text
    else:
        words = extra_note
    if project and words:
        record_user_message(project, words, "文字卡回答" if shape == "chat" else "卡上的意见",
                            card_id=card_id, since=prepared.get("at"), after=prepared.get("messages_before"))
    if chosen is None:
        ev_shape = "note"
    elif "+note" in how or not note:
        ev_shape = how
    else:
        ev_shape = how + "+note"
    event = dict(base, type="answered", shape=ev_shape,
                 choice=chosen["key"] if chosen else None, label=chosen["label"] if chosen else None, note=note)
    if kind in CONFIRM_KINDS:
        out, effects = answer_confirm(project, prepared, chosen, note, at=base["at"])
        event["result"] = out["result"]
        event["effects"] = effects
        if out["result"] in ("changed", "error"):
            # 没签上的不记成「回答了」:进度页的确认记录只列真的回答
            event["type"] = out["result"]
    elif kind == "report_type":
        out = answer_report_type(prepared, chosen, note)
        event["result"] = out["result"]
    elif kind == "decision":
        out = answer_decision(prepared, chosen, note)
        event["result"] = out["result"]
        if project and chosen is not None:
            # 这次决定管的是哪几个资料缺口:任务计划后来再确认一次时,只要缺口还是这几个,决定照样算(不再问一遍)
            event["gap_cards"] = gap_card_uids(project)
    else:
        out = answer_pick_project(prepared, chosen, note)
        event["result"] = out["result"]
    yzlib.append_jsonl(log_path, event)
    out.update({"ok": True, "card_id": card_id, "kind": kind, "choice": event["choice"], "label": event["label"], "note": note})
    return finish(out, project, kind, snapshot_dir), 0


def answer_confirm(project, prepared, chosen, note, at=None):
    """确认类的卡收到回答。at:回答那一刻(和卡片记录里这次回答的 at 是同一个值;交付记录的 delivered_at 用它)。"""
    kind = prepared["kind"]
    data = prepared.get("data") or {}
    pname = yzlib.project_name(project)
    approved = chosen is not None and chosen["key"] in ("confirm", "deliver")
    if not approved and chosen is None and note and note.strip().rstrip("。.!！~～") in YES_WORDS:
        confirm_label = next((o["label"] for o in prepared.get("options") or [] if o["key"] in ("confirm", "deliver")), "确认")
        # 第七轮:不用重新出卡 —— 用户下一条回 1,就算这张卡的回答(reopen_reason)
        return {"result": "not_approved", "looks_like_yes": True,
                "next": ("用户写的是「%s」，像是想确认，但只有选了「%s」那一项（点它，或者回复 1）才算确认，所以没有写确认记录。"
                         "不改材料，说 say 里那句话，停下等用户。" % (note, confirm_label)) + reopen_next(kind),
                "say": note_only_say(kind, data, prepared.get("options"))}, []
    if not approved and chosen is None and note:
        # 第七轮:只写了意见、没选选项 = 先不确认。意见里说了要改什么就改(说 say_revise);没说就不改材料,
        # 说 say(告诉用户回 1 就能定),等用户:他下一条回 1,还算这张卡的回答,不用重新出卡
        return {"result": "not_approved", "next": note_only_next(kind, data, note),
                "say": note_only_say(kind, data, prepared.get("options")), "say_revise": note_revise_say(kind, data)}, []
    if not approved:
        heard = "用户点了「%s」%s。" % (chosen["label"] if chosen else "先不确认", "，还写了意见：「%s」" % note if note else "")
        todo = {
            "task_plan": "照意见改任务计划；没写意见就问用户要改哪里（一次问完）。改好：version 加一、revision_log 加一条、三点的改动内容写这一轮改了什么，再出任务计划卡（进度还是 gate1_awaiting，不用再推进）。",
            "dossier": "照意见补资料或改结论；没写意见就问要补什么、改哪条。改好：version 加一、revision_log 加一条、三点的改动内容写这一轮改了什么，再出资料汇编卡。",
            "outline": "接着改提纲；没写意见就问要动哪里。每改一版 version 加一、revision_log 记明，再出提纲卡。",
            "delivery": "照意见改；没写意见就问改哪里。改完从头走一遍：重新生成成稿 → 交付前检查 → 再复核一轮 → 重写交付的三点 → 再出交付卡。",
            "flow_change": flow_restore_todo(data),
        }
        return {"result": "not_approved", "next": heard + todo[kind], "say": not_yet_say(kind, note)}, []
    # 点了确认 / 交付:先核材料在用户看卡期间没变
    if kind == "delivery":
        # 成稿文件和交付的三点,出卡时各记了一个指纹:两样都没变才算用户看的就是现在这一版
        try:
            gate, _points = delivery_gate(project)
            same = gate["files"] == data.get("files") and gate.get("commitments_digest") == data.get("commitments_digest")
        except Refuse as r:
            same, gate = False, {"problems": r.problems}
    else:
        doc = data.get("doc", "task_plan.md")
        meta, body, problem = pl.load_md(os.path.join(project, doc))
        same = (not problem) and pl.content_hash(meta, body) == data.get("content_hash")
        if same and kind == "dossier":
            same = cards_digest(project) == data.get("cards_digest")   # 资料卡片也是这张卡的依据
    if not same:
        if kind == "flow_change" and not problem and not yzlib.flow_change_pending(meta, body):
            # 第七轮:只写了意见之后用户还能回 1;要是这期间改流程已经还原了,这张卡就不算数了(不是「重新准备了卡」)
            return {"result": "changed",
                    "next": ("任务计划里已经没有这次改流程了（还原了，或者又改过），没有写确认记录。用户还想改流程，"
                             "就照 task-planner「只改流程」再改一次、重新出改流程卡。"),
                    "say": FLOW_REVERTED_SAY}, []
        return {"result": "changed", "next": "材料在用户看卡期间变了，没有写确认记录。重新跑 prepare 出这张卡（按新内容），并跟用户说 say 里那句话。",
                "say": CHANGED_SAY}, []
    plan = os.path.join(project, "task_plan.md")
    plan_meta, plan_body, _p = pl.load_md(plan)
    state = state_of(plan_meta)
    effects = []
    if kind in AWAITING and state != AWAITING[kind]:
        return {"result": "changed", "next": "进度已经不是等这张卡的时候（现在是 %s），没有写确认记录。先跑 projects.py status 看看现在在哪一步。" % state,
                "say": CHANGED_SAY}, []
    # 交付卡、改流程卡:再出一张、同时开着两张时,第二张不能再记一次
    if kind == "delivery" and state != "verifying":
        return {"result": "changed", "next": "进度现在是 %s，不是等交付的时候（可能已经交付过了）：没有再写交付记录。先跑 projects.py status 看看现在在哪一步。" % state,
                "say": "成稿已经交付过了，这张卡不用再确认。"}, []
    if kind == "flow_change" and (state not in ("collecting", "gate2_awaiting") or not yzlib.flow_change_pending(plan_meta, plan_body)):
        return {"result": "changed", "next": "这次改流程已经确认过了（或者任务计划又变了）：没有再写确认记录。先跑 projects.py status 看看现在在哪一步。",
                "say": "改流程刚才已经定下来了，这张卡不用再确认。"}, []
    # 确认记录里的「原话」:用户在卡上附了意见就用他的意见(截到 40 字;整段意见照记在卡片记录里),没写就用选项名
    quote = (note.strip() if note and note.strip() else (chosen["base"] or chosen["label"]))[:QUOTE_CAP]
    if kind in ("task_plan", "dossier", "outline", "flow_change"):
        doc = data.get("doc", "task_plan.md")
        rc, out, err = yzlib.run_toolkit("stamp.py", [os.path.join(project, doc), "--approve", "--by", "用户", "--quote", quote])
        effects.append({"cmd": "stamp.py %s --approve --by 用户 --quote %s" % (doc, quote), "rc": rc, "out": out.strip()[-300:]})
        if rc != 0:
            return {"result": "error", "next": "写确认记录失败（stamp.py 退出码 %d）：%s。没有推进；说 say 里那句话，等用户定。" % (rc, (out + err).strip()[-300:]),
                    "say": ERROR_SAY}, effects
    target = None
    if kind == "task_plan":
        target = "collecting"
    elif kind == "dossier":
        target = data.get("next") or "outlining"
    elif kind == "outline":
        target = "drafting"
    elif kind == "delivery":
        target = "delivered"
    if target:
        rc, out, err = yzlib.run_toolkit("stamp.py", [plan, "--advance", target])
        effects.append({"cmd": "stamp.py task_plan.md --advance %s" % target, "rc": rc, "out": out.strip()[-300:]})
        if rc != 0:
            return {"result": "error", "next": "确认记录写好了，但推进进度失败（stamp.py 退出码 %d）：%s。说 say 里那句话，等用户定。" % (rc, (out + err).strip()[-300:]),
                    "say": ERROR_SAY}, effects
    if kind == "delivery":
        # 第八轮:交付的时刻 = 用户回答的那一刻(原来取的是写记录的时刻,比回答晚一秒)
        record = {"delivered_at": at or yzlib.now_iso(), "by": "用户", "recorded_by": "助手", "files": data.get("files"),
                  "card_id": prepared["card_id"]}
        with io.open(os.path.join(project, "records", "delivery.json"), "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(record, ensure_ascii=False, indent=1) + "\n")
        effects.append({"cmd": "写 records/delivery.json"})
    # PROGRESS.md 末尾记一行(第四轮:资料汇编确认了却没记;确认、交付这几件事一律由脚本记,不靠助手记得)
    version = data.get("version")
    noted = {"task_plan": "任务计划第 %s 版已确认（由助手记录）" % version,
             "dossier": "资料汇编第 %s 版已确认（由助手记录）" % version,
             "outline": "提纲第 %s 版已确认（由助手记录）" % version,
             "flow_change": "任务计划第 %s 版（只改了流程）已确认（由助手记录）" % version,
             "delivery": "成稿已交付（由助手记录）"}[kind]
    yzlib.append_progress_note(project, noted)
    effects.append({"cmd": "PROGRESS.md 记一行：%s" % noted})
    archived = None
    if kind == "delivery":
        # 第五轮 M3:交付之后的归档(cite-trace 出口契约 E5)由脚本做,不靠助手自己写脚本
        try:
            import archive
            archived = archive.run(project)
        except Exception as e:  # noqa: BLE001  归档出错不挡交付;next 里叫助手跑 archive.py 修好
            archived = {"ok": False, "problems": ["归档没做成（%s: %s）" % (type(e).__name__, e)]}
        effects.append({"cmd": "archive.py", "ok": archived.get("ok")})
        # 第八轮:交付之后,助手自己临时用的 .yz-tmp/<项目名>/ 清掉(脚本自己的临时文件夹不动)
        if yzlib.clean_agent_tmp(project):
            effects.append({"cmd": "清掉 .yz-tmp/%s/" % pname})
    nexts = {
        "task_plan": "确认记录已写好，进度推进到收集资料（PROGRESS.md 已记了一行，不用你记）。说 say 里那句交接的话，然后加载 evidence-card 开始收集资料。",
        "dossier": "确认记录已写好，进度推进到%s%s（PROGRESS.md 已记了一行）。说 say 里那句交接的话，然后加载 %s。" % (
            "拟定提纲" if target == "outlining" else "撰写交付",
            "（提纲确认过、没有改动，不用再确认）" if data.get("outline_kept") else "",
            "outline-cocreate" if target == "outlining" else "cite-trace"),
        "outline": "确认记录已写好，进度推进到撰写交付（PROGRESS.md 已记了一行）。说 say 里那句交接的话，然后加载 cite-trace 开始写成稿。",
        "delivery": ("已推进到已交付，交付记录写在 records/delivery.json（PROGRESS.md 已记了一行），归档也做完了"
                     "（library/ 里的来源、资料缺口、红线候选）。说 say 里那句话。"
                     if archived and archived.get("ok") else
                     "已推进到已交付，交付记录写在 records/delivery.json（PROGRESS.md 已记了一行）；归档还没做成（archived 里写了缺什么）："
                     "照着改好，跑 $PY \"agent-tools/archive.py\" \"%s\"，再跑 $PY \"agent-tools/archive.py\" \"%s\" --check 到退出码 0，"
                     "然后说 say 里那句话。" % (pname, pname)),
        "flow_change": "确认记录已写好，进度不动。说 say 里那句话，接着干原来的活（还在收集资料）。",
    }
    if note:
        nexts[kind] += "用户在卡上还写了意见：「%s」，照做；要改已经确认的内容，就按「重新确认」的做法再请用户确认。" % note
    out = {"result": "approved", "state": target or state, "next": nexts[kind],
           "say": approved_say(kind, version, target, data)}
    if archived is not None:
        out["archived"] = archived
        if archived.get("ok"):
            out["say"] = "成稿已交付。这次用到的来源和还没补上的资料缺口已经存进项目，留给以后的课题。"
    return out, effects


def skipped_say(kind, mode, options):
    """卡收起来之后对用户说的那一句(第五轮 H2):两种情况都说得通(点了跳过 / 太久没操作被自动收起),
    请他直接回复选项前面的数字,并把编号列出来(收起的卡上看不到了);「确认任务计划」这类口令留作另一种说法。"""
    numbered = numbered_options(options)
    head = ("好，这张卡先放一边。" if mode == "text"
            else "这张卡先收起来了（你点了跳过，或者太久没操作被自动收起）。")
    say = head + ("想好了直接回复选项前面的数字就行：%s。" % numbered if numbered else "想好了直接告诉我。")
    phrase = BRING_BACK.get(kind)
    if phrase:
        say += "也可以说「%s」，我再把卡拿出来。" % phrase
    return say


def numbered_options(options):
    """卡上的选项按卡上的顺序编号:「1 确认，开始收集资料；2 先不确认」(卡收起来、只写了意见之后,用户看不到卡了)。"""
    return "；".join("%d %s" % (i, o.get("label")) for i, o in enumerate(options or [], start=1))


def doc_label(data):
    """确认类卡的那份材料 →「任务计划第 2 版」;读不出 →「这一版」。"""
    name = yzlib.DOC_NAMES.get(data.get("doc"))
    v = data.get("version")
    return "%s第 %s 版" % (name, v) if name and v is not None else (name or "这一版")


def note_only_say(kind, data, options):
    """只写了意见、没选选项之后对用户说的那一句(第七轮):末尾一定说怎么继续(回哪个数字)。
    10-04 冒烟:说法里没有「怎么继续」,用户不知道可以直接回 1;守门脚本打回重说的那版也把它丢了。"""
    if kind == "delivery":
        return "成稿还没交付：你写的话算意见，不算交付。要按这一版交付，直接回 1 就行；要改的话告诉我改哪里。"
    if kind == "flow_change":
        return "流程还没改：你写的话算意见，不算确认。要按新的流程走，直接回 1 就行；还按原来的流程走，就回 2。"
    if kind in ("report_type", "decision"):
        what = "要定报告类型" if kind == "report_type" else "要定下来"
        return "你写的我记下了。%s，直接回选项前面的数字就行：%s。" % (what, numbered_options(options))
    return "%s还没定下来：你写的话算意见，不算确认。要按这一版定下来，直接回 1 就行；要改的话告诉我改哪里。" % doc_label(data)


def note_revise_say(kind, data):
    """只写了意见、意见里说了要改什么,助手照改的时候说的那一句(改好会出新版的卡,用户到时候再定)。"""
    if kind == "delivery":
        return "好，先不交付，我按你的意见改，改好再请你确认交付。"
    if kind == "flow_change":
        return "好，还按原来的流程走。"
    if kind in ("report_type", "decision"):
        return "好，我按你的意见把这张卡改一下，改好再请你选。"
    return "好，%s先不定，我按你的意见改，改好再请你确认。" % doc_label(data)


def reopen_next(kind):
    """只写了意见 / 卡收起来之后:告诉助手用户回了数字怎么办(不用重新出卡,只此一次)。"""
    if kind == "flow_change":
        reply = "回 1 或 2"
    elif kind in ("report_type", "decision", "pick_project"):
        reply = "回数字或选项名"
    else:
        reply = "回 1（或者别的选项；数字后面接一句话也行）"
    return ("用户接下来%s，记用户消息时 note-user 会提示你把那条消息当这张卡的回答跑 answer —— 只此一次，不用重新出卡。" % reply)


def flow_restore_todo(data):
    """改流程没确认:怎么把任务计划还原(规格 §①)。"""
    before = data.get("stages_before_raw")
    restore = "删掉 stages 这一行" if before in ("absent", None) else "stages 改回 %s" % json.dumps(before)
    return ("把改流程还原：%s、version 改回 %s；revision_log 再加一条，v 写 %s（不许更大），what 写「改流程没确认，已还原」。"
            "原来的确认照旧有效，按原流程接着做。" % (restore, data.get("previous_version"), data.get("version")))


# 确认类的卡只写了意见、意见里说了要改什么时怎么改(先说 say_revise)
NOTE_REVISE_TODO = {
    "task_plan": "照意见改任务计划：version 加一、revision_log 加一条、三点的改动内容写这一轮改了什么，再出新版的任务计划卡（进度还是 gate1_awaiting，不用再推进）",
    "dossier": "照意见补资料或改结论：version 加一、revision_log 加一条、三点的改动内容写这一轮改了什么，再出新版的资料汇编卡",
    "outline": "照意见改提纲（这就是共创的一轮）：version 加一、revision_log 记明，再出新版的提纲卡",
    "delivery": "照意见改，改完从头走一遍：重新生成成稿 → 交付前检查 → 再复核一轮 → 重写交付的三点 → 再出交付卡",
}


def note_only_next(kind, data, note):
    """只写了意见、没选选项之后助手怎么做(第七轮):意见里说了要改的就改(说 say_revise);没说的不改材料,
    说 say(告诉用户回哪个数字),等用户 —— 他回的数字还算这张卡的回答。"""
    head = "用户没点选项，只写了意见：「%s」。" % note
    if kind == "flow_change":
        return (head + "这算「先不确认」，没有写确认记录，进度不动。看意见是什么："
                "① 明说还要原来的步骤（例如「还是要拟提纲」）：说 say_revise 里那句，再%s"
                "② 没说清（例如「直接帮我确认吧」，或者问了个问题）：先别还原，先用一句研究上的话回应意见，"
                "再说 say 里那句，停下等用户回 1 或 2（回 2 再照①还原）。" % flow_restore_todo(data)) + reopen_next(kind)
    if kind in ("report_type", "decision"):
        what = "成稿叫法、核心判断" if kind == "report_type" else "问法或选项（例如提了别的做法）"
        return (head + "这张卡还没定。看意见是什么：① 要改卡上的%s：说 say_revise 里那句，改好重新出卡（出了新卡，这张就不能再回答了）；"
                "② 其余（补充、问题、想法）：先用一句研究上的话回应意见，再说 say 里那句（里面列着选项的编号），停下等用户。"
                "看得出用户想选哪一项也不要替他选，等他自己回数字。" % what) + reopen_next(kind)
    return (head + "这算「先不确认」，没有写确认记录（用户的话都是意见，只有选了确认那一项才算）。看意见里说没说要改什么："
            "① 说了（例如「时间段改成 2021 年开始」）：说 say_revise 里那句，再%s（改了材料，这张卡就不能再回答了）；"
            "② 没说（例如「直接帮我确认吧」「这版可以」，或者只是问了个问题）：不改材料，先用一句研究上的话回应意见，"
            "再说 say 里那句，停下等用户。" % NOTE_REVISE_TODO[kind]) + reopen_next(kind)


def selected_say(kind, chosen, note):
    """报告类型卡、决定卡、选项目卡选了一项之后对用户说的那一句(第五轮 H3:要转述给用户的话都由脚本给)。"""
    label = (chosen.get("base") or chosen.get("label") or "").strip()
    if kind == "report_type":
        what = {"judge": "下判断（研判型）", "survey": "梳理情况（综述型）"}.get(chosen.get("key"))
        if what is None:
            return "好，先按下判断写，确认任务计划时还能改。"
        return "好，按%s来写%s。我先把任务计划写出来请你确认。" % (what, "，你写的意见也会写进去" if note else "")
    if kind == "pick_project":
        return "好，接着做「%s」。" % label
    return "好，就按「%s」来%s。" % (label, "，你写的意见我照做" if note else "")


def approved_say(kind, version, target, data):
    """点了确认 / 交付之后,对用户说的那一句(answer 跑完才说;脚本给好,助手照说)。"""
    if kind == "task_plan":
        return "好，任务计划按第 %s 版定下了。我开始收集资料，遇到要你拿主意的事会停下来问你。" % version
    if kind == "dossier":
        if target == "outlining":
            return "好，资料汇编按第 %s 版定下了。接下来一起拟提纲。" % version
        return "好，资料汇编按第 %s 版定下了。%s我开始写成稿。" % (version, "提纲之前确认过、没有改动，" if data.get("outline_kept") else "")
    if kind == "outline":
        return "提纲第 %s 版定下了，我开始写。写的时候要大改结构，会先问你。" % version
    if kind == "delivery":
        return "成稿已交付。这次用到的来源和还没补上的资料缺口会存进项目，留给以后的课题。"
    return "好，以后按新的流程走。"


def not_yet_say(kind, note):
    """点了「先不确认」(或只写了意见)之后,对用户说的那一句。写了意见:说照意见改;没写:问改哪里。"""
    what = {"task_plan": "任务计划", "dossier": "资料汇编", "outline": "提纲", "delivery": "成稿", "flow_change": "流程"}[kind]
    if kind == "flow_change":
        return "好，还按原来的流程走。"
    if note:
        return "好，先不定。我按你的意见改%s，改好再请你确认。" % what
    if kind == "delivery":
        return "好，先不交付。你想改哪里？"
    return "好，先不定。你想改哪里？直接说就行，只说哪一部分不对也可以。"


def answer_report_type(prepared, chosen, note):
    data = prepared.get("data") or {}
    if chosen is None:
        # 第七轮:不用再出一次卡 —— 用户下一条回数字就算这张卡的回答(reopen_reason)
        return {"result": "note", "selected": None,
                "next": note_only_next("report_type", data, note),
                "say": note_only_say("report_type", data, prepared.get("options")),
                "say_revise": note_revise_say("report_type", data)}
    genre = {"judge": "argument", "survey": "survey", "undecided": "argument"}[chosen["key"]]
    nxt = "任务计划的 genre 写 %s；report_name 写「%s」（与卡上一字不差）；genre_rationale 记用户在卡上的选择。" % (genre, data.get("report_name"))
    out = {"result": "selected", "selected": chosen["key"], "genre": genre, "report_name": data.get("report_name"),
           "next": nxt + "说 say 里那句话，然后写任务计划。", "say": selected_say("report_type", chosen, note)}
    if chosen["key"] == "undecided":
        out["next"] = nxt + "genre_rationale 写明「你说先不定」。说 say 里那句话，然后写任务计划。"
    if note:
        out["next"] += "用户在卡上还写了意见：「%s」，写任务计划时照做。" % note
    return out


def answer_decision(prepared, chosen, note):
    if chosen is None:
        data = prepared.get("data") or {}
        return {"result": "note", "selected": None,
                "next": note_only_next("decision", data, note),
                "say": note_only_say("decision", data, prepared.get("options")),
                "say_revise": note_revise_say("decision", data)}
    changes = bool(chosen.get("changes_plan"))
    nxt = ("照选的做：说 say 里那句话，再用一句研究上的话说接下来怎么做；把这个决定写进它影响的那份材料"
           "（例如资料汇编的资料缺口一节写「你决定：……」）。selected 按你写进 fields 的选项顺序数（卡上推荐的那一项排在第一个，脚本已经换算好）。")
    if changes:
        nxt += "这一项会改任务计划：按 task-planner「变更与重新确认」改任务计划、重新请用户确认，确认之后再回来接着做。"
    if note:
        nxt += "用户在卡上还写了意见：「%s」，照做。" % note
    return {"result": "selected", "selected": int(chosen["key"]), "changes_plan": changes, "next": nxt,
            "say": selected_say("decision", chosen, note)}


def answer_pick_project(prepared, chosen, note):
    names = (prepared.get("data") or {}).get("projects") or []
    name = chosen["key"] if chosen else None
    if name is None and note:
        exact = [n for n in names if n == note.strip()]
        part = [n for n in names if note.strip() and note.strip() in n]
        name = exact[0] if exact else (part[0] if len(part) == 1 else None)
    if name is None:
        return {"result": "note", "project": None,
                "next": "认不出用户要哪个项目：在对话里问一句，或者再出一次选项目的卡。"}
    return {"result": "selected", "project": name, "say": "好，接着做「%s」。" % name,
            "next": "说 say 里那句话，跑 $PY \"agent-tools/projects.py\" status \"%s\"，按它说的转到对应的 skill。" % name}


# ---- 用户消息 ----

def record_user_message(project, text, via=None, card_id=None, since=None, after=None):
    """把用户的一条话记进 records/messages.jsonl:去掉前后空白(文件末尾的换行不算原话)。→ 记了没有。
    不按字去重(第三轮:用户连着两张卡都回「1」,第二个「1」被当成重复吞掉了)。只有回答卡片时(card_id 给了)
    才比一次:这张卡出了之后已经记过一字不差的同一句(多半是 note-user 先记的那一条),就不再记。
    「这张卡出了之后」= 出卡时记录里已有 after 条,只看那之后的;老的出卡记录没有这个数,才按时刻 since 比。"""
    t = (text or "").strip()
    if not t:
        return False
    path = yzlib.message_log(project)
    if card_id and _mark_logged_reply(path, t, via, card_id, since, after):
        return False
    rec = {"at": yzlib.now_iso(), "text": t}
    if via:
        rec["via"] = via
    if card_id:
        rec["card"] = card_id
    yzlib.append_jsonl(path, rec)
    return True


def _mark_logged_reply(path, text, via, card_id, since=None, after=None):
    """这张卡出了之后,消息记录里已经有一字不差的同一句(多半是 note-user 先记的)→ True,不再记一遍。
    第八轮:那一行没标卡号的,补上 via 与 card(只写了意见、跳过的回答原来就这样漏了标记,和别的回答不一致)。
    「这张卡出了之后」= 出卡时记录里已有 after 条记录,只看那之后的;老的出卡记录没有这个数,才按时刻 since 比。
    改的只是那一行:别的行(包括读不出来的)原样写回。"""
    if not os.path.isfile(path):
        return False
    use_after = isinstance(after, int) and not isinstance(after, bool)
    t0 = None if use_after else yzlib.parse_iso(since)
    if not use_after and t0 is None:
        return False
    with io.open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().split("\n")
    count, matches = 0, []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        n, count = count, count + 1
        if use_after:
            in_scope = n >= after
        else:
            at = yzlib.parse_iso(obj.get("at"))
            in_scope = at is not None and at >= t0
        if in_scope and isinstance(obj.get("text"), str) and obj["text"].strip() == text:
            matches.append((i, obj))
    if any(o.get("card") == card_id for _i, o in matches):
        return True                     # 这张卡的回答已经记过、也标了
    unmarked = [(i, o) for i, o in matches if not o.get("card")]
    if not unmarked:
        return False
    i, obj = unmarked[-1]               # note-user 先记的那一条:补上标记
    if via:
        obj["via"] = via
    obj["card"] = card_id
    lines[i] = json.dumps(obj, ensure_ascii=False)
    yzlib.write_atomic(path, "\n".join(lines))
    return True


def chat_words(raw):
    """回答原文是对话里的一条消息时 → 那条消息(去掉首尾空白);是选项框交回的 JSON → None。"""
    t = (raw or "").strip()
    try:
        obj = json.loads(t)
    except ValueError:
        return t or None
    return obj.strip() if isinstance(obj, str) and obj.strip() else None


def answered_just_now(project, text):
    """这条消息刚刚已经作为卡片回答记过了(第五轮 L1):消息记录的最后一条是同一句、标着卡号 C,
    而卡片记录的最后一件事就是 C 的回答(之后没出过别的卡)。助手先跑 answer、后跑 note-user 时用它,同一句不记两遍。
    10-04 实测:13 条消息记了 22 行 —— answer 记一次(带卡号),note-user 又记一次。"""
    t = (text or "").strip()
    msgs = yzlib.read_jsonl(yzlib.message_log(project))
    if not msgs or not msgs[-1].get("card") or not isinstance(msgs[-1].get("text"), str) or msgs[-1]["text"].strip() != t:
        return False
    log = yzlib.read_jsonl(yzlib.card_log(project))
    last = log[-1] if log else None
    return bool(last) and last.get("card_id") == msgs[-1]["card"] and last.get("type") in ("answered", "skipped", "changed", "error")


def note_user(project_arg, text):
    project = yzlib.project_dir(project_arg)
    if not isinstance(text, str) or not text.strip():
        raise UsageError("消息是空的")
    if answered_just_now(project, text):
        return {"ok": True, "recorded": 0, "file": "records/messages.jsonl",
                "already": "这条是刚才那张卡的回答，记回答的时候已经记过了，不再记一遍"}, 0
    record_user_message(project, text)
    out = {"ok": True, "recorded": len(text.strip()), "file": "records/messages.jsonl"}
    # 第五轮 H2:刚才那张卡收起来了(跳过,或者太久没操作被自动收起);第七轮:或者只收到了意见、没选选项。
    # 这条消息认得出是它的某一项 → 提示助手把它当这张卡的回答(只此一次)
    card, why = reopenable_card(project)
    if card and not prepared_problems(card):
        idx, how, note = read_reply(text, card["options"])
        if idx is not None:
            name = yzlib.project_name(project)
            answer_file = yzlib.inbox_file(name, "answer.txt")
            out["looks_like_answer"] = {"card_id": card["card_id"], "kind": card.get("kind"),
                                        "option": card["options"][idx].get("label"), "note": note, "after": why}
            out["next"] = ("这条消息像是在回答刚才%s的那张卡（%s，选的是「%s」%s）：把这条消息原样写进 %s，跑 "
                           "$PY \"agent-tools/card.py\" answer \"%s\" --card %s --answer-file \"%s\"，照它的输出往下走"
                           "（不用重新出卡；answer 返回之前不说结果）。"
                           % (REOPEN_WORDS[why], card["card_id"], card["options"][idx].get("label"),
                              "，还写了意见" if note else "", answer_file, name, card["card_id"], answer_file))
    return out, 0


def current_project():
    p = os.path.join(yzlib.projects_root(), ".current")
    if os.path.isfile(p):
        with io.open(p, encoding="utf-8") as f:
            name = f.read().strip()
        if name:
            return name
    return None


def read_stdin_text():
    data = sys.stdin.buffer.read() if hasattr(sys.stdin, "buffer") else sys.stdin.read().encode("utf-8")
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(enc).replace(chr(13) + chr(10), chr(10))
        except UnicodeDecodeError:
            continue
    raise UsageError("标准输入读不出来（请用 UTF-8）")


def main(argv):
    return yzlib.run_main(_main, argv)


def _main(argv):
    yzlib.setup_stdout()
    ap = argparse.ArgumentParser(prog="card.py", description="选项卡：prepare / answer / note-user / mode")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("prepare")
    p.add_argument("kind")
    p.add_argument("project", nargs="?")
    # 卡上的空、回答原文、用户消息只从文件读:写在命令行里,PowerShell 会展开 $… 和反引号、吃掉引号
    p.add_argument("--fields-file")
    p.add_argument("--id")
    p.add_argument("--outside", action="append",
                   help="这一轮读过的项目以外的文件，一个路径一次；没读过就写 --outside none")
    p.add_argument("--mode", choices=["text"], help="选项框工具用不了：用文字卡重新准备这张卡")
    p.add_argument("--pane-open", action="store_true",
                   help="这个对话里已经把右侧挂上了（照 yunzhi-progress 第 1 节挂过一次）：卡上才写「右侧已打开」")
    a = sub.add_parser("answer")
    a.add_argument("project", nargs="?")
    a.add_argument("--card", required=True)
    a.add_argument("--answer-file", required=True)
    a.add_argument("--snapshot-dir", help="对话的可视化目录：答完生成一张进度快照，贴在这一轮最后一条回复里")
    n = sub.add_parser("note-user")
    n.add_argument("project", nargs="?")
    src = n.add_mutually_exclusive_group(required=True)
    src.add_argument("--file")
    src.add_argument("--stdin", action="store_true")
    m = sub.add_parser("mode")
    m.add_argument("project", nargs="?")
    args = ap.parse_args(argv[1:])
    if args.cmd == "prepare":
        raw = yzlib.read_input_file(args.fields_file) if args.fields_file else "{}"
        try:
            fields = json.loads(raw) if raw.strip() else {}
        except ValueError as e:
            raise UsageError("fields 不是合法的 JSON：%s" % e)
        if args.kind != "pick_project" and not args.project:
            raise UsageError("要给项目名")
        if not isinstance(fields, dict):
            raise UsageError("fields 要是一个 JSON 对象")
        if args.outside:
            vals = [v.strip() for v in args.outside if v and v.strip()]
            fields["outside"] = [v for v in vals if v.lower() not in ("none", "无", "没有")]
        out, code = prepare(args.kind, args.project, fields, args.id, args.mode, args.pane_open)
        return yzlib.emit(out, code)
    if args.cmd == "answer":
        raw = yzlib.read_input_file(args.answer_file)
        out, code = answer(args.project, args.card, raw, args.snapshot_dir)
        return yzlib.emit(out, code)
    if args.cmd == "note-user":
        text = yzlib.read_input_file(args.file) if args.file else read_stdin_text()
        project = args.project or current_project()
        if not project:
            raise UsageError("要给项目名：note-user \"<项目名>\" --file \"projects/<项目名>/records/_inbox.txt\"")
        out, code = note_user(project, text)
        return yzlib.emit(out, code)
    if args.cmd == "mode":
        project = yzlib.project_dir(args.project) if args.project else None
        mode, why = card_mode(project)
        return yzlib.emit({"mode": mode, "why": why})
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
