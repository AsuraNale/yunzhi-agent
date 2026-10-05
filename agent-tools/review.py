# -*- coding: utf-8 -*-
"""review — 独立复核的开始与封存(包着 toolkit 的 review_result.py,加三样它没有的东西)。

用法(在工作区根目录下跑;$PY 见 AGENTS.md):
  $PY agent-tools/review.py start "<项目名>" --round <n>
      主助手在另起复核助手之前跑:记下这一轮复核的成稿(toolkit review_result.py --files <n>),
      写好给复核助手的说明 review/brief-<n>.md(只给它的文件清单、成稿逐段编号、要交的东西,和这一轮的随机码)。
      输出里有起复核助手的那条消息(spawn.message)和一个「复核暗号」:暗号只出现在这一次输出里,
      不写进任何文件,也不许写进给复核助手的消息。随机码正相反:只写在说明里,从不出现在这段输出(主助手的对话)里,
      开始记录 round-<n>.json 里也只存它的 sha256。
      ⚠ Codex 0.159.2 的 spawn_agent 不写 fork_turns 时默认是 "all"(把整段对话分给子助手):起复核助手一定要
      明写 fork_turns: "none"。
  $PY agent-tools/review.py seal "<项目名>" --round <n>
      复核助手做完后跑(它自己跑):先核它交的 review/inputs-<n>.json,过了才调 toolkit 的
      review_result.py --seal <n> 封存,并留一份封存记录 review/sealed-<n>.json(出交付卡时核它)。
  $PY agent-tools/review.py check "<项目名>"
      复核结果对得上当前成稿、必须改为 0、而且是用本脚本封存的 → 退出码 0;否则 1(说明原因)。
  $PY agent-tools/review.py abandon "<项目名>" --round <n>
      放弃一个没封存的轮次(复核期间成稿改了):等于 toolkit review_result.py --abandon <n>。

封存前核的几样(第三轮实测:三次复核都带着整段写作对话,第一轮漏掉的问题第二轮才查出来;
第四轮查明:Codex 的 spawn_agent 不写 fork_turns 默认就是 "all",原来说明里写的「不加 fork_turns」正好把对话分了过去):
1. **读的是这一轮的说明**:随机码(nonce)只写在 review/brief-<n>.md 里 —— start 的输出不带它、开始记录只存它的 sha256,
   所以它从没出现在主助手的对话里。复核助手要把它原样抄进 inputs 文件。
2. **没带写作对话(绊线)**:复核助手把它在这份说明之前见过的「复核暗号」照抄进 seen_codes。暗号只在主助手的对话里出现过:
   从写作对话分出来的复核助手看得见它,另起的干净助手看不见。报了暗号、或者交的文件里出现暗号 → 拒绝封存。
3. **逐段核了**:成稿每一段(说明里编了号)在 coverage 里都要有一行:这一段核了几条论断、结论是什么。
4. **抽核了资料卡片和原文,而且结论落了地**:source_checks 至少抽 5 张资料卡片(不到 5 张就全抽);
   每一张「不符」都要指向发现列表里的一条「必须改」(finding:那条发现在 findings-<n>.json 里是第几条,从 1 数);
   全部写「无法验证」不算抽核过(一张都没核实)。

这几样各自证明到哪一步(照实说,别夸大):
- 随机码:证明交 inputs 文件的那一方在 start 之后读过这一轮的说明。它**不**证明读的人没带写作对话 ——
  分了对话的复核助手照样读得到说明;主助手要是自己去打开说明、自己写复核结果,它也拦不住。
- 暗号:一根绊线。分了对话的复核助手照实报,就抓得住(实测的「不小心带过去」正是这种);
  复核助手不报、或者主助手故意作假,都抓不住。
- 真正让复核不带写作对话的,是起复核助手时明写 fork_turns: "none"。更硬的证据要读平台自己的会话记录
  (~/.codex/sessions,看子助手那一份里有没有主助手的对话),那是项目以外的私人文件,这一版不读。
- 不符必须连着一条「必须改」、不许全是无法验证:管的是复核的结论有没有落地,不是复核的人是谁。
"""
import argparse
import hashlib
import io
import json
import os
import re
import secrets
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yzlib  # noqa: E402
from yzlib import pl, UsageError  # noqa: E402

CODE_LABEL = "复核暗号"
CODE_RE = re.compile(r"YZC-[0-9A-F]{8}")
SOURCE_RESULTS = ("相符", "不符", "无法验证")
MIN_SOURCE_CHECKS = 5
RULE_FILES = ("redlines.md", "aggregation.md", "discipline.md")
SKILL = ".agents/skills/review-module/SKILL.md"
# 起了复核助手之后对用户说的一句(只说这一句,然后等它交卷)
WAITING_SAY = "一个没参与写作的助手正在独立复核成稿，做完我告诉你结果。"


def review_dir(project):
    d = os.path.join(project, "review")
    os.makedirs(d, exist_ok=True)
    return d


def logical(project, *parts):
    """项目里的文件 → 给复核助手看的路径(从工作区根目录算):projects/<项目名>/…"""
    return "/".join(["projects", yzlib.project_name(project)] + [p.replace("\\", "/") for p in parts])


def sha256_file(path):
    return pl.file_sha256(path) if os.path.isfile(path) else None


def last_sealed_round(project, before):
    """比第 before 轮早、封存过的最后一轮(有 result-<k>.json),没有 → None。"""
    for k in range(before - 1, 0, -1):
        if os.path.isfile(os.path.join(project, "review", "result-%d.json" % k)):
            return k
    return None


def draft_for(project):
    """成稿对应的那份底稿:out/ 里成稿的文件名去掉扩展名,在 drafts/ 里找同名的 .md;找不到就用最近改过的那份。"""
    drafts = os.path.join(project, "drafts")
    if not os.path.isdir(drafts):
        return None
    stems = [os.path.splitext(os.path.basename(rel))[0] for rel, _p in pl.deliverable_paths(project)]
    for stem in stems:
        p = os.path.join(drafts, stem + ".md")
        if os.path.isfile(p):
            return p
    mds = sorted((os.path.join(drafts, f) for f in os.listdir(drafts) if f.endswith(".md")), key=os.path.getmtime, reverse=True)
    return mds[0] if mds else None


_PLACEHOLDER_RE = re.compile(r"\[\[[^\]]*\]\]")


def paragraphs(draft_path):
    """底稿 → 逐段编号 [{id, section, start}]:空行分段;标题不算段(记成这一段在哪一节);
    表格、列表各算一段。start 是这一段开头的几个字(去掉引文占位),给复核助手找位置用。"""
    if not draft_path:
        return []
    _meta, body, _problem = pl.load_md(draft_path)
    _title, body = pl.take_doc_title(body)
    out, section, block = [], "", []

    def flush():
        text = " ".join(l.strip() for l in block).strip()
        block.clear()
        if not text:
            return
        plain = re.sub(r"\s+", " ", _PLACEHOLDER_RE.sub("", text)).strip(" |-*#>")
        out.append({"id": "P%02d" % (len(out) + 1), "section": section, "start": plain[:30]})

    for line in body.split("\n"):
        s = line.strip()
        if not s:
            flush()
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            flush()
            section = re.sub(r"\s*\{[^}]*\}\s*$", "", m.group(2)).strip()
            continue
        block.append(s)
    flush()
    return out


def expected_files(project, n):
    """复核助手这一轮该读、也只该读的文件(从工作区根目录算的路径)。"""
    files = [SKILL, logical(project, "review", "brief-%d.md" % n)]
    files += [logical(project, rel) for rel, _p in pl.deliverable_paths(project)]
    cards = os.path.join(project, "cards")
    if os.path.isdir(cards):
        files += [logical(project, "cards", f) for f in sorted(os.listdir(cards)) if f.endswith(".md")]
    for name in ("references.yaml", "dossier.md", "outline.md", "task_plan.md"):
        if os.path.isfile(os.path.join(project, name)):
            files.append(logical(project, name))
    files += ["toolkit/library/rules/%s" % f for f in RULE_FILES]
    k = last_sealed_round(project, n)
    if k:
        files += [logical(project, "review", "findings-%d.json" % k), logical(project, "review", "report-%d.md" % k)]
    return files


def live_cards(project):
    """不是资料缺口、没弃用的资料卡片的卡号(抽核原文用)。"""
    from card_check import load_cards
    d = os.path.join(project, "cards")
    if not os.path.isdir(d):
        return []
    out = []
    for card, _src in load_cards(d):
        if isinstance(card, dict) and not card.get("deprecated") and card.get("stance") != "gap" and card.get("uid"):
            out.append(str(card["uid"]))
    return out


def all_card_ids(project):
    from card_check import load_cards
    d = os.path.join(project, "cards")
    if not os.path.isdir(d):
        return set()
    return {str(c.get("uid")) for c, _s in load_cards(d) if isinstance(c, dict) and c.get("uid")}


def brief_text(project, n, nonce, files, paras, need_checks):
    name = yzlib.project_name(project)
    lines = ["# 第 %d 轮独立复核 · 说明（脚本生成）" % n, "",
             "你是独立复核助手，没参与写这份成稿。方法照 `%s` 做；这份说明只管这一轮读什么、交什么。" % SKILL, "",
             "- 项目：%s" % name, "- 轮次：%d" % n, "- 这一轮的随机码（nonce）：`%s`（原样抄进 inputs 文件）" % nonce, "",
             "## 只读这些文件（一个都不多读：不读对话记录、records/、drafts/ 和这里没列的文件）", ""]
    lines += ["- `%s`" % f for f in files]
    lines += ["", "## 成稿逐段编号（每一段都要核，coverage 里一段一行）", ""]
    if paras:
        lines += ["- %s · %s · %s……" % (p["id"], p["section"] or "开头", p["start"]) for p in paras]
    else:
        lines.append("（没找到底稿，没法编号：照成稿的段落自己从 P01 起编，一段一行）")
    lines += ["", "## 要交的三份文件", "",
              "1. `%s`：人读报告（照 review-module「输出」写）。" % logical(project, "review", "report-%d.md" % n),
              "2. `%s`：发现列表（照 review-module「输出」写）。" % logical(project, "review", "findings-%d.json" % n),
              "3. `%s`：这一轮你读了什么、核了什么，照下面的样子写：" % logical(project, "review", "inputs-%d.json" % n), "",
              "```json",
              json.dumps({"round": n, "nonce": nonce, "files_read": ["（上面「只读这些文件」那一节的每一个路径，原样照抄）"],
                          "history": "没有", "seen_codes": [],
                          "coverage": [{"para": "P01", "claims": 2, "result": "两条数字都和资料卡片一致"}],
                          "source_checks": [{"card": "S01", "result": "相符", "note": "原文摘录、数字和方向都对得上"}]},
                         ensure_ascii=False, indent=1),
              "```", "",
              "- `files_read`：上面那一节列的路径，一个不少、一个不多。",
              "- `seen_codes`：叫你来的那条消息、以及在你读到这份说明之前你能看到的任何对话里，如果出现过「%s」开头的一串字，"
              "把那串字原样写进这个列表；一个都没见过就写 `[]`。照实写 —— 这一项是用来核复核有没有带着写作对话的。" % CODE_LABEL,
              "- `coverage`：上面每一段编号一行。`claims` 是这一段里你逐条核过的论断（数字、判断、引述）有几条；"
              "`result` 一句话写核的结论（有问题的写进发现列表）。",
              "- `source_checks`：至少抽 %d 张资料卡片（不到这么多就全抽），每张核三样：卡上的原文摘录在原始资料里找得到、"
              "卡上的数字和原文一致、卡上写的方向（支持判断 / 不支持 / 背景资料）没读反。打开原始资料核；打不开的写「无法验证」。"
              "`result` 只能写 相符 / 不符 / 无法验证。" % need_checks,
              "  - **不符**的那一张：在发现列表里写一条 `severity` 为 `must_fix` 的发现（方向读反、数字或摘录对不上原文都是必须改），"
              "再在这一行加 `finding`：那条发现在 findings 文件里是第几条（从 1 数），写成 "
              "`{\"card\": \"<卡号>\", \"result\": \"不符\", \"note\": \"<哪里对不上>\", \"finding\": <第几条>}`。"
              "没有对应的必须改，封存时脚本拒绝。",
              "  - 不能全写「无法验证」：至少要有一张打开原文核实过（相符或不符）。抽到的原文打不开，就换别的资料卡片核。", "",
              "## 做完", "",
              "跑 `$PY \"agent-tools/review.py\" seal \"%s\" --round %d`（`$PY` 见 AGENTS.md）。它拒绝封存时照它说的改；"
              "把它最后的输出原样交回给叫你来的主助手。" % (name, n)]
    return "\n".join(lines) + "\n"


def start(project, n):
    state = yzlib.state_of_project(project)
    if state != "verifying":
        raise UsageError("进度现在是 %s：独立复核在成稿生成好、交付前检查全过之后做（进度在 verifying）" % state)
    rc, out, err = yzlib.run_toolkit("review_result.py", [project, "--files", n])
    if rc != 0:
        raise UsageError("记不下这一轮复核的成稿（review_result.py 退出码 %d）：%s" % (rc, (out + err).strip()[-400:]))
    files = expected_files(project, n)
    paras = paragraphs(draft_for(project))
    need = min(MIN_SOURCE_CHECKS, len(live_cards(project)))
    nonce = secrets.token_hex(4)
    code = "YZC-%s" % secrets.token_hex(4).upper()
    rd = review_dir(project)
    with io.open(os.path.join(rd, "brief-%d.md" % n), "w", encoding="utf-8", newline="\n") as f:
        f.write(brief_text(project, n, nonce, files, paras, need))
    # 随机码只进说明(复核助手读);开始记录只存它的 sha256,这段输出也不带它 —— 它从不出现在主助手的对话里
    record = {"round": n, "nonce_sha256": hashlib.sha256(nonce.encode("ascii")).hexdigest(),
              "code_sha256": hashlib.sha256(code.encode("ascii")).hexdigest(),
              "files": files, "paragraphs": paras, "source_checks_min": need, "started_at": yzlib.now_iso()}
    with io.open(os.path.join(rd, "round-%d.json" % n), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record, ensure_ascii=False, indent=1) + "\n")
    message = ("你是独立复核助手。按 %s 做第 %d 轮独立复核：先读 %s，再读那份说明，照它做。"
               % (logical(project, "review", "brief-%d.md" % n), n, SKILL))
    return {"ok": True, "round": n, "brief": logical(project, "review", "brief-%d.md" % n),
            "paragraphs": len(paras), "source_checks_min": need,
            "code_notice": "%s：%s（只写在你自己的这段输出里：不许写进给复核助手的消息、不许写进任何文件）" % (CODE_LABEL, code),
            "spawn": {"message": message, "fork_turns": "none"},
            # 第九轮:等复核助手的时候,助手连发了约 7 条「还在等」—— 说一句就够,然后等
            "say": WAITING_SAY,
            "next": ("Codex：调 spawn_agent 另起复核助手，参数就是 spawn 那两项：message 写 spawn.message 那一句（原样），"
                     "fork_turns 明写 \"none\" —— 这一项不写，Codex 默认是 \"all\"，会把整段写作对话分给复核助手，这一轮就不算独立；"
                     "也不写轮数。然后 wait_agent 等它做完；起了之后不再给它发消息（不用 send_message、followup_task，也不打断它）。"
                     "等的时候在对话里说一句就够（say 里那句），然后就等它交卷，不要每隔一会儿再发一条「还在等」。"
                     "不要自己打开那份说明（里面的随机码只给复核助手）。"
                     "WorkBuddy：调用 review-module 这份 skill，把 spawn.message 那一句交给它。"
                     "复核助手做完会自己跑 review.py seal；你再跑 review.py check。")}


def _load_json(path, what):
    if not os.path.isfile(path):
        return None, "%s还没写（%s）" % (what, os.path.basename(path))
    try:
        with io.open(path, encoding="utf-8-sig") as f:
            return json.load(f), None
    except ValueError as e:
        return None, "%s不是合法的 JSON：%s" % (what, e)


def _norm(p):
    return str(p).strip().replace("\\", "/").lstrip("./")


def inputs_problems(project, n):
    """复核助手交的 review/inputs-<n>.json 核不核得过(见模块说明)→ 毛病列表;空 = 过。"""
    rd = os.path.join(project, "review")
    record, err = _load_json(os.path.join(rd, "round-%d.json" % n), "这一轮的开始记录")
    if err:
        return ["第 %d 轮不是用 review.py start 开始的（%s）：主助手先跑 review.py start" % (n, err)]
    data, err = _load_json(os.path.join(rd, "inputs-%d.json" % n), "inputs 文件")
    if err:
        return [err + "：照这一轮说明（review/brief-%d.md）里的样子写" % n]
    if not isinstance(data, dict):
        return ["inputs 文件要是一个 JSON 对象"]
    out = []
    if data.get("round") != n:
        out.append("round 要写 %d" % n)
    if not nonce_matches(record, data.get("nonce")):
        out.append("nonce 和这一轮说明里的随机码对不上：复核助手要读这一轮的说明（review/brief-%d.md），把随机码原样抄回来" % n)
    # 1. 没带写作对话
    seen = data.get("seen_codes")
    if seen is None or not isinstance(seen, list):
        out.append("seen_codes 要是一个列表（没见过「%s」就写 []）" % CODE_LABEL)
    elif seen:
        out.append("复核助手报告在说明之前见过「%s」：它带着写作对话（起它的时候分了对话过去），这一轮不算独立。"
                   "放弃这一轮（review.py abandon），重新起一个不带对话的复核助手" % CODE_LABEL)
    leaked = set()
    for fn in ("inputs-%d.json" % n, "findings-%d.json" % n, "report-%d.md" % n):
        p = os.path.join(rd, fn)
        if os.path.isfile(p):
            with io.open(p, encoding="utf-8", errors="replace") as f:
                for tok in CODE_RE.findall(f.read()):
                    if hashlib.sha256(tok.encode("ascii")).hexdigest() == record.get("code_sha256"):
                        leaked.add(fn)
    if leaked:
        out.append("复核助手交的文件里出现了这一轮的「%s」（%s）：它看得到写作对话，这一轮不算独立。放弃这一轮，重新起一个不带对话的复核助手"
                   % (CODE_LABEL, "、".join(sorted(leaked))))
    files = data.get("files_read")
    if not isinstance(files, list) or not all(isinstance(x, str) for x in files):
        out.append("files_read 要是路径列表")
    else:
        want = {_norm(x) for x in record.get("files") or []}
        got = {_norm(x) for x in files}
        if want - got:
            out.append("files_read 少了说明里列的：%s" % "、".join(sorted(want - got)[:5]))
        if got - want:
            out.append("files_read 多了说明里没列的：%s（只读说明里列的文件）" % "、".join(sorted(got - want)[:5]))
    # 2. 逐段核了
    cov = data.get("coverage")
    paras = [p["id"] for p in record.get("paragraphs") or []]
    if not isinstance(cov, list):
        out.append("coverage 要是列表：成稿每一段一行")
    else:
        rows = {}
        for i, row in enumerate(cov):
            if not isinstance(row, dict) or not isinstance(row.get("para"), str):
                out.append("coverage 第 %d 行要是 {para, claims, result}" % (i + 1))
                continue
            c = row.get("claims")
            if not (isinstance(c, int) and not isinstance(c, bool) and c >= 0):
                out.append("coverage 里 %s 的 claims 要是 0 或正整数" % row["para"])
            if not (isinstance(row.get("result"), str) and row["result"].strip()):
                out.append("coverage 里 %s 要写 result（这一段核的结论）" % row["para"])
            rows[row["para"]] = row
        missing = [p for p in paras if p not in rows]
        if missing:
            out.append("coverage 少了这几段：%s（说明里编了号的每一段都要核）" % "、".join(missing[:8]) + (" 等 %d 段" % len(missing) if len(missing) > 8 else ""))
        if paras and rows and sum(r.get("claims") or 0 for r in rows.values() if isinstance(r.get("claims"), int)) == 0:
            out.append("coverage 里一条论断都没核：每一段的数字、判断、引述要逐条核")
    # 3. 抽核资料卡片与原文
    checks = data.get("source_checks")
    need = record.get("source_checks_min") or 0
    if not isinstance(checks, list):
        out.append("source_checks 要是列表")
    else:
        known = all_card_ids(project)
        findings = round_findings(project, n)
        cards, results = [], []
        for i, row in enumerate(checks):
            if not isinstance(row, dict) or not isinstance(row.get("card"), str):
                out.append("source_checks 第 %d 行要是 {card, result, note}" % (i + 1))
                continue
            if row["card"] not in known:
                out.append("source_checks 里的 %s 不是这个项目的资料卡片" % row["card"])
            if row.get("result") not in SOURCE_RESULTS:
                out.append("source_checks 里 %s 的 result 只能写 %s" % (row["card"], " / ".join(SOURCE_RESULTS)))
            elif row["result"] == "不符":
                # 第四轮:不符只写在核对表里、发现列表里却没有一条必须改 → 问题没进结果,交付卡照样出得来
                problem = mismatch_finding_problem(row, findings)
                if problem:
                    out.append("source_checks 里 %s 写了不符，%s：在发现列表（findings-%d.json）里写一条 severity 为 must_fix 的发现，"
                               "再在这一行写 finding（那条发现是第几条，从 1 数）" % (row["card"], problem, n))
            cards.append(row["card"])
            results.append(row.get("result"))
        if len(set(cards)) < need:
            out.append("source_checks 只核了 %d 张资料卡片，至少要 %d 张（不同的卡）" % (len(set(cards)), need))
        if results and all(r == "无法验证" for r in results):
            out.append("source_checks 全写了无法验证：一张原文都没核实，不算抽核过。换几张原文打得开的资料卡片核（相符或不符）；"
                       "一张都打不开，把这个情况写进报告，并告诉叫你来的主助手")
    return out


def nonce_matches(record, given):
    """inputs 文件抄回来的随机码对不对:开始记录只存它的 sha256(老记录存的是原文,照样认)。"""
    if not isinstance(given, str) or not given.strip():
        return False
    if record.get("nonce_sha256"):
        return hashlib.sha256(given.strip().encode("utf-8")).hexdigest() == record["nonce_sha256"]
    return bool(record.get("nonce")) and given.strip() == record.get("nonce")


def round_findings(project, n):
    """这一轮的发现列表(findings-<n>.json)→ 列表;不在、读不出 → None。"""
    data, _err = _load_json(os.path.join(project, "review", "findings-%d.json" % n), "发现列表")
    return data if isinstance(data, list) else None


def mismatch_finding_problem(row, findings):
    """一行「不符」连着的那条发现 → 毛病一句话;连得上一条必须改 → None。finding 是从 1 数的序号。"""
    k = row.get("finding")
    if findings is None:
        return "但这一轮的发现列表还没写或读不出来"
    if not (isinstance(k, int) and not isinstance(k, bool)):
        return "但没写 finding（对应的那条必须改在发现列表里是第几条）"
    if not 1 <= k <= len(findings):
        return "但 finding 写的第 %d 条在发现列表里没有（一共 %d 条）" % (k, len(findings))
    f = findings[k - 1]
    if not isinstance(f, dict) or f.get("severity") != "must_fix":
        return "但 finding 指的第 %d 条不是必须改（must_fix）" % k
    return None


def seal(project, n):
    problems = inputs_problems(project, n)
    if problems:
        return {"ok": False, "sealed": False, "refuse": problems[0], "problems": problems,
                "next": "照 problems 改好 review/inputs-%d.json（或照说明重做这一轮），再跑一次 review.py seal。" % n}, 1
    rc, out, err = yzlib.run_toolkit("review_result.py", [project, "--seal", n])
    text = (out + err).strip()
    if rc != 0:
        return {"ok": False, "sealed": False, "refuse": "toolkit 拒绝封存", "problems": [text[-600:]],
                "next": "照上面 review_result.py 的说明改 findings 或报告，再跑一次 review.py seal。"}, 1
    rd = os.path.join(project, "review")
    mark = {"round": n, "sealed_at": yzlib.now_iso(),
            "inputs_sha256": sha256_file(os.path.join(rd, "inputs-%d.json" % n)),
            "result_sha256": sha256_file(os.path.join(rd, "result.json"))}
    with io.open(os.path.join(rd, "sealed-%d.json" % n), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(mark, ensure_ascii=False, indent=1) + "\n")
    return {"ok": True, "sealed": True, "round": n, "toolkit": text[-600:],
            "next": "封存好了。把这段输出原样交回给主助手。"}, 0


def sealed_problems(project, result):
    """review/result.json 这一轮是不是用 review.py seal 封存的、之后没动过 → 毛病列表(出交付卡时核)。"""
    n = result.get("round") if isinstance(result, dict) else None
    if not isinstance(n, int):
        return ["独立复核结果读不出轮次"]
    rd = os.path.join(project, "review")
    mark, err = _load_json(os.path.join(rd, "sealed-%d.json" % n), "封存记录")
    if err:
        return ["第 %d 轮复核不是用 review.py seal 封存的（%s）：复核要用 review.py start 开始、review.py seal 封存，"
                "这样才核得了复核助手没带写作对话、逐段核了、抽核了原文" % (n, err)]
    out = []
    if mark.get("result_sha256") != sha256_file(os.path.join(rd, "result.json")):
        out.append("复核结果在 review.py seal 之后又被改过或重新封存过：用 review.py seal 重新封存这一轮")
    if mark.get("inputs_sha256") != sha256_file(os.path.join(rd, "inputs-%d.json" % n)):
        out.append("第 %d 轮的 inputs 文件在封存之后改过：用 review.py seal 重新封存这一轮" % n)
    if not out:
        out = inputs_problems(project, n)
    return out


def check(project):
    rc, out, err = yzlib.run_toolkit("review_result.py", [project, "--check"])
    problems = [] if rc == 0 else [(out + err).strip()[-500:]]
    result, rerr = _load_json(os.path.join(project, "review", "result.json"), "复核结果")
    if result is not None:
        problems += sealed_problems(project, result)
    elif not problems:
        problems.append(rerr)
    if problems:
        return {"ok": False, "passed": False, "problems": problems,
                "next": "复核还不能交付：照 problems 做（改成稿 → 重新生成 → 交付前检查 → 再复核一轮；或者用 review.py seal 重新封存）。"}, 1
    return {"ok": True, "passed": True, "next": "复核结果对得上当前成稿、必须改 0 处、封存核过了：去写交付的三点。"}, 0


def main(argv):
    return yzlib.run_main(_main, argv)


def _main(argv):
    yzlib.setup_stdout()
    ap = argparse.ArgumentParser(prog="review.py")
    sub = ap.add_subparsers(dest="cmd")
    for name in ("start", "seal", "abandon"):
        p = sub.add_parser(name)
        p.add_argument("project")
        p.add_argument("--round", type=int, required=True)
    c = sub.add_parser("check")
    c.add_argument("project")
    args = ap.parse_args(argv[1:])
    if not args.cmd:
        ap.print_help()
        return 2
    project = yzlib.project_dir(args.project)
    if args.cmd in ("start", "seal", "abandon") and args.round < 1:
        raise UsageError("--round 从 1 起")
    if args.cmd == "start":
        return yzlib.emit(start(project, args.round))
    if args.cmd == "seal":
        out, code = seal(project, args.round)
        return yzlib.emit(out, code)
    if args.cmd == "abandon":
        rc, out, err = yzlib.run_toolkit("review_result.py", [project, "--abandon", args.round])
        return yzlib.emit({"ok": rc == 0, "toolkit": (out + err).strip()[-500:],
                           "next": "这一轮放弃了。对现在的成稿从下一轮重新开始：review.py start --round %d。" % (args.round + 1)},
                          0 if rc == 0 else 1)
    out, code = check(project)
    return yzlib.emit(out, code)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
