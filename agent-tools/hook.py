# -*- coding: utf-8 -*-
"""hook — 云织 Agent 的守门脚本:Codex 的 hooks(.codex/hooks.json)四个事件都调这一个脚本。

  py -X utf8 agent-tools/hook.py        (Codex 从标准输入交给它一段 JSON;会话的工作目录是工作区根目录)

只用标准库(扫回复时才读词表;检查改正本时才用 PyYAML)。每次工具调用都要跑,所以先看是不是要管的工具,不是就马上走。
**它自己出任何错都放行**(错误记进 projects/.records/hook-errors.log):守门脚本坏了不能把用户卡死。
Codex 没信任这份 hooks 时它根本不跑:一切照 AGENTS.md 靠说明,card.py 也照旧(见 sessions.active)。

事件(输入的形状是 10-04 用 CLI 0.160.0 + gpt-6.1-sol 实测记下的):
- UserPromptSubmit:用户的原话逐字记进会话记录 projects/.records/sessions/<会话号>.jsonl。永不拦。
- PreToolUse:要拦就往标准输出打 permissionDecision: deny 的 JSON、退出码 0(实测只有这种写法拦得住;退出码 2 拦不住):
  ① request_user_input:参数要和某个项目 records/cards.jsonl 里「出了、还没答」的原生卡的 ask 按结构一致;
     request_user_input_async 一律拒;
  ② 起子助手(collaborationspawn_agent):fork_turns 不是 "none" 就拒(只在独立复核时起子助手,复核不带写作对话);
     第八轮:协作工具只放行起、等、列出(spawn_agent / wait_agent / list_agents),给子助手发消息、追加任务、打断
     (send_message / followup_task / interrupt_agent,以及协作命名空间里不认识的)一律拒;
  ③ shell:stamp.py 写确认记录的参数(--approve 等)、推进到助手不该推进的状态;往卡片和消息记录、交付记录、守门记录、
     工具与说明里写;先 cd 再跑 toolkit / agent-tools 的脚本;改会话号和云织开关的环境变量;
  ④ apply_patch:改上面那几类受保护的文件;改任务计划 / 资料汇编 / 提纲里的确认记录(approval),
     或者把任务计划的进度(pipeline_status)改到助手不该推进的状态。
- PostToolUse(request_user_input):原生卡交回的原文(用户真实的回答)记进会话记录,拿不到就记「缺」。
- Stop:扫这一轮最后一条回复 —— 内部文件的链接或路径、说明书这类说自己说明的词、词表里的内部词;有问题就打回重说一次。
  打回时请它保留原来告诉用户的下一步(回哪个数字);回复里说下一步的那句本身没毛病,就原样交还给它(第七轮)。
  stop_hook_active 为 true(打回过一次了)时放行,并记一笔。只看得到最后一条回复,工具调用之间的过程消息看不到。
"""
import io
import json
import os
import re
import sys
import traceback

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import sessions  # noqa: E402

# 助手自己推进得了的进度(其余的推进由 card.py answer 在用户点了确认之后做)
AGENT_STATES = ("gate1_awaiting", "gate2_awaiting", "gate3_awaiting", "verifying")
# 任务计划里助手自己写得了的进度:新写的任务计划是 planning
PLAN_STATES_OK = ("planning",) + AGENT_STATES
# 回答过的卡(同 yzlib.CARD_CLOSED;测试核两边一致)
CARD_CLOSED = ("answered", "skipped", "changed", "unclear", "error")
APPROVAL_VALUES = ("approved_at", "approved_by", "approval_quote", "approved_hash")
SHELL_TOOLS = ("bash", "shell", "local_shell", "exec_command", "unified_exec", "shell_command")
_MISSING = object()

ASYNC_REASON = ("只用会等回答的 request_user_input，不用 request_user_input_async：它不等用户回答就往下走，"
                "回答后来作为一条新消息进来，和卡对不上。照 card.py prepare 的输出，用 request_user_input 原样发 ask。")
NO_CARD_REASON = ("这张选项卡没有对上任何一张「出了、还没答」的卡：先跑 $PY \"agent-tools/card.py\" prepare …，"
                  "调 request_user_input 时原样用它输出的 ask（一个字都不改，卡号也不改）。")
CHANGED_ASK_REASON = ("卡 %s 的参数和出卡时 card.py prepare 输出的 ask 不一样：原样用 ask，一个字都不改"
                      "（问题、选项名、选项说明、卡号都照抄）。")
CLOSED_CARD_REASON = ("卡 %s 已经回答过或收起来了：要再问用户，重新跑 card.py prepare 出这张卡，用新输出的 ask。")
OLD_CARD_REASON = ("卡 %s 是守门脚本装上之前出的（出卡记录里没记下 ask）：重新跑 card.py prepare 出这张卡，再原样用新输出的 ask。")
TEXT_CARD_REASON = ("卡 %s 是文字卡模式出的：原样发 card.py prepare 输出的 fallback_text，然后结束这一轮、等用户回复，"
                    "不调选项框工具。")
SPAWN_REASON = ("起子助手只为独立复核，fork_turns 必须明写 \"none\"（%s）：不写就是把整段写作对话都分给复核助手，"
                "这一轮复核就不算。照 review.py start 的输出起复核助手。")
# 第八轮:复核进行中,主助手给复核助手发了一条消息(内容加密,查不了说了什么)。我们的流程只要起(spawn)和等(wait):
# 协作工具里只放行这几样,别的(发消息 send_message、追加任务 followup_task、打断 interrupt_agent、以后新出的)一律拒。
# 工具名照 collaborationspawn_agent 的规律:命名空间 collaboration + 工具名;不带命名空间的同名工具也认(以防版本变了写法)
COLLAB_PREFIX = "collaboration"
COLLAB_ALLOWED = ("spawn_agent", "wait_agent", "list_agents")
COLLAB_MESSAGE_TOOLS = ("send_message", "followup_task", "send_input", "interrupt_agent")
REVIEW_MSG_REASON = ("复核助手只看那份说明，不再给它发消息（也不追加任务、不打断）：起它的时候说明已经交给它了，"
                     "接下来只用 wait_agent 等它交卷，再跑 review.py seal。")
APPROVE_REASON = ("确认记录只由 card.py answer 写（用户在卡上点了确认之后）：不要自己调 stamp.py --approve "
                  "（也不带 --signature、--expect-hash、--replace-signed）。要用户确认，按选项卡的做法出卡。")
ADVANCE_REASON = ("进度只许你推进到 gate1_awaiting / gate2_awaiting / gate3_awaiting / verifying（这次是 %s）："
                  "确认之后的推进由 card.py answer 做。")
RECORD_REASON = ("卡片记录、用户消息记录、交付记录和守门脚本的记录只由脚本写，不能用命令改、删、覆盖"
                 "（只读的话用 Get-Content -Raw -Encoding utf8）。用户消息照 AGENTS.md 先写进 records/_inbox.txt 再跑 card.py note-user。")
TOOL_REASON = ("「%s」是工具或说明文件（toolkit/、agent-tools/、.agents/、.codex/、wording/、AGENTS.md 这些），对你只读："
               "不改、不删、不往里写。脚本退回就照它说的改材料；确信是检查本身错了，在对话里告诉用户。")
CD_REASON = ("命令在工作区根目录下跑（会话本来就在这里）：去掉 cd / Set-Location / Push-Location（和别的工作目录），"
             "直接原样跑这条命令，路径照旧写 projects/…、toolkit/…、agent-tools/…。")
ENV_REASON = ("不改会话号（CODEX_THREAD_ID）和云织的开关（YUNZHI_ 开头的环境变量）：它们由平台和用户定。去掉这一段再跑。")
PATCH_PROTECTED_REASON = ("「%s」是%s，不能用编辑工具改。%s")
APPROVAL_REASON = ("「%s」里的确认记录（approval）只能由 card.py answer 经工具包写：不改、不补、不删它。"
                   "要改一份确认过的材料，先跑 $PY \"toolkit/scripts/stamp.py\" \"<那份材料>\" --invalidate --why \"<为什么>\"，"
                   "再改内容，改好出卡请用户确认。")
STATE_REASON = ("任务计划的进度只许你写成 planning（新写的）或推进到 gate1_awaiting / gate2_awaiting / gate3_awaiting / verifying"
                "（这次改成了 %s）：确认之后的推进由 card.py answer 做；推进用 stamp.py --advance，不直接改文件。")
STOP_REASON = ("这条回复里有用户读不懂的内部东西：%s。用研究上的话重说这条回复，不提内部文件和说明"
               "（成稿、「查看」里的材料网页、用户自己的材料可以给链接）。"
               "重说时保留原来要告诉用户的下一步（比如回哪个数字）%s。")
# 第七轮:回复里告诉用户下一步怎么做的句子(回哪个数字、回复选项前面的数字、在卡上点哪一项、说一声「……」)。
# 10-04 冒烟:打回重说的那版把「要按这一版定下来，直接回 1 就行」丢了 —— 打回时把这句原样交还给助手
NEXT_STEP_RE = re.compile("回\\s*(?:复\\s*)?(?:选项前面的)?(?:[1-9%s-%s]|数字)|点「|说一声「|也可以说「"
                          % (chr(0xFF11), chr(0xFF19)))


# ---- 入口 ----

def deny(reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def main(argv=None):
    data = None
    try:
        raw = sys.stdin.buffer.read() if hasattr(sys.stdin, "buffer") else sys.stdin.read().encode("utf-8")
        data = json.loads(raw.decode("utf-8-sig", errors="replace") or "{}")
        out = handle(data)
    except Exception:  # noqa: BLE001  守门脚本自己出错:放行,记一笔
        log_error(data)
        return 0
    if out:
        # 只打这一段 JSON(ASCII 转义:不管控制台是什么编码都读得对)
        sys.stdout.write(json.dumps(out, ensure_ascii=True) + "\n")
        sys.stdout.flush()
    return 0


def log_error(data):
    try:
        path = sessions.error_log()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        at, _ts = sessions.stamp()
        rec = {"at": at, "event": data.get("hook_event_name") if isinstance(data, dict) else None,
               "tool": data.get("tool_name") if isinstance(data, dict) else None,
               "error": traceback.format_exc()[-2000:]}
        with io.open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def handle(d):
    if not isinstance(d, dict):
        return None
    ev = d.get("hook_event_name")
    if ev == "UserPromptSubmit":
        return on_prompt(d)
    if ev == "PreToolUse":
        return on_pre(d)
    if ev == "PostToolUse":
        return on_post(d)
    if ev == "Stop":
        return on_stop(d)
    return None


def note(d, record):
    rec = {"turn_id": d.get("turn_id")}
    rec.update(record)
    sessions.append(d.get("session_id"), rec)


def refuse(d, tool, reason):
    """拦下:记一笔(秧秧核记录用),返回 deny。"""
    note(d, {"event": "denied", "tool": tool, "tool_use_id": d.get("tool_use_id"), "reason": reason})
    return deny(reason)


def tool_input(d):
    ti = d.get("tool_input")
    if isinstance(ti, str):
        try:
            return json.loads(ti)
        except ValueError:
            return ti
    return ti


# ---- UserPromptSubmit ----

def on_prompt(d):
    prompt = d.get("prompt")
    if isinstance(prompt, str):
        note(d, {"event": "prompt", "session_id": d.get("session_id"), "prompt": prompt})
    return None


# ---- PreToolUse ----

def on_pre(d):
    name = str(d.get("tool_name") or "")
    low = name.lower()
    if low.endswith("request_user_input_async"):
        return refuse(d, name, ASYNC_REASON)
    if low.endswith("request_user_input"):
        return check_card_ask(d, tool_input(d))
    if low.endswith("spawn_agent"):
        return check_spawn(d, tool_input(d))
    if collab_message_tool(low):
        return refuse(d, name, REVIEW_MSG_REASON)
    if low in SHELL_TOOLS or low.endswith("exec_command"):
        return check_shell(d, tool_input(d))
    if low.endswith("apply_patch"):
        return check_patch(d, tool_input(d))
    return None


# ① 选项卡:只放行 card.py prepare 出过、还没答的原生卡,参数原样

def card_logs():
    root = sessions.projects_root()
    out = [(None, os.path.join(sessions.records_root(), "cards.jsonl"))]
    try:
        names = sorted(os.listdir(root))
    except OSError:
        names = []
    for name in names:
        if name.startswith((".", "_")):
            continue
        path = os.path.join(root, name, "records", "cards.jsonl")
        if os.path.isfile(path):
            out.append((name, path))
    return out


def card_states():
    """每张卡最近一次出卡 → [(项目名或 None, 出卡记录, 开着吗)]。"""
    res = []
    for proj, path in card_logs():
        events = sessions.read_jsonl(path)
        latest = {}
        for i, e in enumerate(events):
            if e.get("type") == "prepared" and e.get("card_id"):
                latest[e["card_id"]] = i
        for cid, i in latest.items():
            closed = any(e.get("card_id") == cid and e.get("type") in CARD_CLOSED for e in events[i + 1:])
            res.append((proj, events[i], not closed))
    return res


def asked_ids(ti):
    qs = ti.get("questions") if isinstance(ti, dict) else None
    return [q.get("id") for q in qs if isinstance(q, dict)] if isinstance(qs, list) else []


def check_card_ask(d, ti):
    want = sessions.canon(ti)
    states = card_states()
    for proj, ev, is_open in states:
        ask = ev.get("ask")
        if is_open and isinstance(ask, dict) and sessions.canon(ask) == want:
            if ev.get("mode") == "text":
                return refuse(d, "request_user_input", TEXT_CARD_REASON % ev.get("card_id"))
            note(d, {"event": "card_asked", "card_id": ev.get("card_id"), "project": proj, "kind": ev.get("kind"),
                     "tool_use_id": d.get("tool_use_id")})
            return None
    ids = {sessions.card_key(i) for i in asked_ids(ti)}
    same = [(proj, ev, is_open) for proj, ev, is_open in states if sessions.card_key(ev.get("card_id")) in ids]
    shown = "、".join(sorted(str(i) for i in asked_ids(ti) if i))
    if any(is_open and not isinstance(ev.get("ask"), dict) for _p, ev, is_open in same):
        return refuse(d, "request_user_input", OLD_CARD_REASON % shown)
    if any(is_open for _p, _e, is_open in same):
        return refuse(d, "request_user_input", CHANGED_ASK_REASON % shown)
    if same:
        return refuse(d, "request_user_input", CLOSED_CARD_REASON % "、".join(sorted(i for i in asked_ids(ti) if i)))
    return refuse(d, "request_user_input", NO_CARD_REASON)


# ② 起子助手:fork_turns 必须明写 "none";起了之后不再给它发消息

def collab_message_tool(low):
    """协作工具里给子助手传话的(发消息、追加任务、打断,以及协作命名空间里不认识的)→ True;起、等、列出 → False。
    不带命名空间的只认这几个名字本身(别的插件里碰巧叫 …send_message 的工具不管)。"""
    if low in COLLAB_MESSAGE_TOOLS:
        return True
    if low.startswith(COLLAB_PREFIX):
        return low[len(COLLAB_PREFIX):].lstrip("._:/") not in COLLAB_ALLOWED
    return False


def check_spawn(d, ti):
    fork = ti.get("fork_turns", _MISSING) if isinstance(ti, dict) else _MISSING
    if isinstance(fork, str) and fork.strip() == "none":
        return None
    said = "没写" if fork is _MISSING else "写的是 %s" % json.dumps(fork, ensure_ascii=False)
    return refuse(d, "spawn_agent", SPAWN_REASON % said)


# ③ shell

STAMP_RE = re.compile(r"stamp\.py|\b(?:import|from)\s+stamp\b", re.I)
APPROVE_RE = re.compile(r"--(?:approve|replace-signed|signature|expect-hash)\b", re.I)
ADVANCE_RE = re.compile(r"--advance(?:\s+|=)[\"']?([A-Za-z0-9_]+)", re.I)
# 受保护的记录:卡片记录、用户消息记录、交付记录(每个项目的 records/ 里)和守门脚本自己的记录(projects/.records/)
RECORD_RE = re.compile(r"records[\\/]+(?:cards\.jsonl|delivery\.json|messages\.jsonl)"
                       r"|(?:^|[\\/\s\"'=])\.records(?:[\\/\s\"']|$)", re.I)
_WRITE_CMDLETS = (r"Set-Content|Add-Content|Out-File|Clear-Content|Remove-Item|Move-Item|Copy-Item|Rename-Item|New-Item"
                  r"|Tee-Object|Clear-Item")
_WRITE_ALIASES = r"sc|ac|ri|rm|rmdir|del|erase|mi|mv|move|cpi|cp|copy|ren|rni|ni|tee"
WRITE_RE = re.compile(r"\b(?:%s)\b" % _WRITE_CMDLETS
                      + r"|(?:^|[\s;|&(])(?:%s)(?=\s)" % _WRITE_ALIASES
                      + r"|WriteAll(?:Text|Lines|Bytes)|AppendAll(?:Text|Lines)|\[(?:System\.)?IO\.File\]|StreamWriter"
                      + r"|(?:^|[\s;|&(\"'])(?:py|python3?|pythonw)(?:\.exe)?(?=\s)[^|;&\n]*?\s-c(?=\s)"
                      + r"|\|\s*(?:py|python3?|pythonw)(?:\.exe)?(?=\s|$)", re.I)
# 跑一份 .py(不在 toolkit/、agent-tools/ 里的,多半是助手自己写在 .yz-tmp/ 的一次性脚本):读它的内容,碰受保护的记录就拦
PY_FILE_RE = re.compile(r"(\"[^\"\n]+\.pyw?\"|'[^'\n]+\.pyw?'|[^\s\"'|;&<>]+\.pyw?)(?=[\s\"'|;&<>]|$)", re.I)
SCRIPT_MARKS = ("cards.jsonl", "messages.jsonl", "delivery.json", ".records", "approved_hash", "approval_quote", "--approve")
SCRIPT_REASON = ("脚本「%s」要碰卡片记录、用户消息记录、交付记录、守门脚本的记录或确认记录：这些只由云织自己的脚本写"
                 "（card.py、stamp.py 经 card.py answer）。不要自己写脚本绕过去；要做的事用现成的脚本。")
REDIRECT_RE = re.compile(r"(?<![<>=\-])(\d?)(>>?)\s*(\"[^\"]*\"|'[^']*'|[^\s|;&<>]+)?")
CMDLET_ARGS_RE = re.compile(r"(?:^|[\s;|&(])(%s|%s)(?=\s)([^|;&\n]*)" % (_WRITE_CMDLETS, _WRITE_ALIASES), re.I)
COPY_CMDS = ("copy-item", "cpi", "cp", "copy")
PY_C_RE = re.compile(r"(?:py|python3?|pythonw)(?:\.exe)?\s[^|;&\n]*?-c\s+(\"[^\"]*\"|'[^']*')", re.I)
WRITE_CALL_RE = re.compile(r"open\([^)]*['\"][wax]|\.write|remove\(|unlink|rename|replace\(|rmtree|copy|move\(", re.I)
TOOL_PATH_RE = re.compile(r"(?:^|[\\/\s\"'=])(?:toolkit|agent-tools|\.agents|\.codebuddy|\.codex|wording|upstream)(?:[\\/]|$)"
                          r"|(?:^|[\\/\s\"'=])AGENTS\.md(?![\w.])", re.I)
CD_RE = re.compile(r"(?:^|[;&|\n({]|&&|\|\||-Command\s+[\"']?|\s-c\s+[\"']?)\s*(?:cd|chdir|Set-Location|sl|Push-Location|pushd)(?=\s|$)",
                   re.I)
SCRIPT_RE = re.compile(r"(?:toolkit|agent-tools)[\\/][^\s\"']*\.py", re.I)
_ENV_NAMES = r"(?:CODEX_THREAD_ID|CODEX_SESSION_ID|YUNZHI_[A-Za-z_]+)"
ENV_SET_RE = re.compile(r"\$env:" + _ENV_NAMES + r"\s*=" + r"|(?:^|[\s;&|])(?:set|export|setx)\s+" + _ENV_NAMES + r"(?:=|\s)"
                        + r"|SetEnvironmentVariable\(\s*['\"]" + _ENV_NAMES
                        + r"|(?:Remove-Item|ri|rm|del)\s+[\"']?env:\\?" + _ENV_NAMES
                        + r"|(?:^|[\s;&|])" + _ENV_NAMES + r"=\S", re.I)


def shell_text(ti):
    """shell 工具要跑的那段命令。写成列表的(["powershell.exe", "-Command", "<命令>"])取 -Command / -c 后面那一段。"""
    c = ti.get("command") if isinstance(ti, dict) else ti
    if isinstance(c, list):
        parts = [str(x) for x in c]
        low = [p.lower() for p in parts]
        for flag in ("-command", "-c", "/c", "-lc"):
            if flag in low[:-1]:
                parts = parts[low.index(flag) + 1:]
                break
        c = " ".join(parts)
    return c if isinstance(c, str) else ""


def redirect_targets(cmd):
    """> / >> / 2> 写进去的文件;2>&1、>$null、> nul 不算。"""
    out = []
    for m in REDIRECT_RE.finditer(cmd):
        t = (m.group(3) or "").strip("\"'")
        if not t or t.startswith("&") or t.lower() in ("$null", "nul", "null"):
            continue
        out.append(t)
    return out


def write_targets(cmd):
    """写文件的命令写到哪(重定向的目标、写文件的命令后面的路径;复制只算目标那一个)。"""
    targets = redirect_targets(cmd)
    for m in CMDLET_ARGS_RE.finditer(cmd):
        verb = m.group(1).lower()
        toks = [t.strip("\"'") for t in re.findall(r"\"[^\"]*\"|'[^']*'|[^\s\"']+", m.group(2))]
        paths = [t for t in toks if t and not t.startswith("-") and not t.startswith("$")]
        if verb in COPY_CMDS:
            paths = paths[-1:]
        targets += paths
    return targets


def rule_stamp(cmd, ti, d):
    if not STAMP_RE.search(cmd):
        return None
    if APPROVE_RE.search(cmd):
        return APPROVE_REASON
    for m in ADVANCE_RE.finditer(cmd):
        if m.group(1) not in AGENT_STATES:
            return ADVANCE_REASON % m.group(1)
    return None


def rule_records(cmd, ti, d):
    if RECORD_RE.search(cmd) and (WRITE_RE.search(cmd) or redirect_targets(cmd)):
        return RECORD_REASON
    return None


def rule_tool_files(cmd, ti, d):
    for t in write_targets(cmd):
        if TOOL_PATH_RE.search(" " + t):
            return TOOL_REASON % t
    for m in PY_C_RE.finditer(cmd):
        code = m.group(1)
        if TOOL_PATH_RE.search(" " + code) and WRITE_CALL_RE.search(code):
            return TOOL_REASON % "（python -c 里写的文件）"
    return None


def same_dir(a, b):
    def norm(p):
        return os.path.normcase(os.path.realpath(os.path.abspath(p)))
    try:
        return norm(a) == norm(b)
    except (OSError, ValueError):
        return False


def rule_cd(cmd, ti, d):
    if not SCRIPT_RE.search(cmd):
        return None
    if CD_RE.search(cmd):
        return CD_REASON
    wd = ti.get("workdir") if isinstance(ti, dict) else None
    if isinstance(wd, str) and wd.strip():
        base = d.get("cwd") if isinstance(d.get("cwd"), str) and d.get("cwd") else ROOT
        if not same_dir(os.path.join(base, wd), ROOT):
            return CD_REASON
    return None


def rule_env(cmd, ti, d):
    return ENV_REASON if ENV_SET_RE.search(cmd) else None


def rule_own_script(cmd, ti, d):
    """命令跑一份不在 toolkit/、agent-tools/ 里的 .py:读它(最多 200 KB),里面提到受保护的记录或确认记录的字段就拦。
    只是绊线:脚本可以拼字符串躲开,但「写个一次性脚本往记录里写」这种明显的做法拦得住。"""
    if not re.search(r"(?:^|[\s;|&(\"'])(?:py|python3?|pythonw)(?:\.exe)?(?=\s)", cmd, re.I):
        return None
    base = d.get("cwd") if isinstance(d.get("cwd"), str) and d.get("cwd") else ROOT
    for m in PY_FILE_RE.finditer(cmd):
        token = m.group(1).strip("\"'")
        full = os.path.abspath(os.path.join(base, token))
        rel = rel_of(full, d) or ""
        if rel.startswith(("toolkit/", "agent-tools/", "tests/")) or not os.path.isfile(full):
            continue
        try:
            with io.open(full, encoding="utf-8", errors="replace") as f:
                code = f.read(200000)
        except OSError:
            continue
        if any(mark in code for mark in SCRIPT_MARKS):
            return SCRIPT_REASON % os.path.basename(full)
    return None


SHELL_RULES = (rule_stamp, rule_records, rule_tool_files, rule_cd, rule_env, rule_own_script)


def check_shell(d, ti):
    cmd = shell_text(ti)
    if not cmd.strip():
        return None
    for rule in SHELL_RULES:
        reason = rule(cmd, ti, d)
        if reason:
            return refuse(d, "shell", reason)
    return None


# ④ apply_patch

DOC_NAMES = ("task_plan.md", "dossier.md", "outline.md")
TOOL_PREFIXES = ("toolkit/", "agent-tools/", ".agents/", ".codebuddy/", ".codex/", "wording/", "upstream/")
APPROVAL_TEXT_RE = re.compile(r"^\+\s*(?:approval\s*:\s*\{[^}]*status\s*:\s*approved|status\s*:\s*approved\b"
                              r"|(?:approved_at|approved_by|approval_quote|approved_hash|signature)\s*:\s*"
                              r"(?!null\b|~|''|\"\"|\s*$))", re.I)


def patch_text(ti):
    if isinstance(ti, str):
        return ti
    if isinstance(ti, dict):
        for k in ("command", "input", "patch"):
            v = ti.get(k)
            if isinstance(v, list):
                v = "\n".join(str(x) for x in v)
            if isinstance(v, str) and "*** Begin Patch" in v:
                return v
    return ""


def parse_patch(text):
    """apply_patch 的补丁 → [{op: add|update|delete, path, move_to, lines}]。"""
    ops, cur = [], None
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith("*** Begin Patch"):
            continue
        if line.startswith("*** End Patch"):
            break
        m = re.match(r"\*\*\* (Add|Update|Delete) File:\s*(.+?)\s*$", line)
        if m:
            cur = {"op": m.group(1).lower(), "path": m.group(2), "move_to": None, "lines": []}
            ops.append(cur)
            continue
        m = re.match(r"\*\*\* Move to:\s*(.+?)\s*$", line)
        if m and cur is not None:
            cur["move_to"] = m.group(1)
            continue
        if cur is not None:
            cur["lines"].append(line)
    return ops


def rel_of(path, d):
    """补丁里的路径 → 相对工作区(或 projects 文件夹:projects/<…>)的小写正斜杠路径;在工作区外 → None。"""
    p = str(path).strip().strip("\"'")
    base = d.get("cwd") if isinstance(d.get("cwd"), str) and d.get("cwd") else ROOT
    full = os.path.normcase(os.path.abspath(os.path.join(base, p)))
    for root, prefix in ((sessions.projects_root(), "projects/"), (ROOT, "")):
        r = os.path.normcase(os.path.abspath(root))
        if full == r or full.startswith(r + os.sep):
            rel = os.path.relpath(full, r).replace("\\", "/")
            return (prefix + rel).lower() if rel != "." else prefix.rstrip("/").lower()
    return None


def protected_kind(rel):
    """受保护的路径 → (是什么, 怎么办);不受保护 → None。"""
    if rel is None:
        return None
    if rel == "agents.md" or rel.startswith(TOOL_PREFIXES):
        return ("工具或说明文件（只读）", "脚本退回就照它说的改材料；确信是检查本身错了，在对话里告诉用户。")
    if rel == "projects/.records" or rel.startswith("projects/.records/"):
        return ("守门脚本的记录", "这份记录只由守门脚本写。")
    if re.fullmatch(r"projects/[^/]+/records/(?:cards\.jsonl|delivery\.json|messages\.jsonl)", rel):
        return ("卡片、用户消息或交付的记录", "只由 card.py 写：卡片回答跑 card.py answer，用户消息写进 records/_inbox.txt 再跑 card.py note-user。")
    return None


def split_hunks(lines):
    hunks, cur = [], None
    for line in lines:
        if line.startswith("@@"):
            cur = {"header": line[2:].strip().strip("@").strip(), "lines": [], "eof": False}
            hunks.append(cur)
            continue
        if line.startswith("*** End of File"):
            if cur is not None:
                cur["eof"] = True
            continue
        if cur is None:
            cur = {"header": "", "lines": [], "eof": False}
            hunks.append(cur)
        cur["lines"].append(line)
    return hunks


def seek(lines, pattern, start, eof):
    """pattern 在 lines 里从 start 起第一次出现的位置(先逐字比,再去掉行尾空白比,再去掉两头空白比;eof 时从末尾往前找)。"""
    n = len(pattern)
    if n == 0:
        return start
    last = len(lines) - n
    order = list(range(last, start - 1, -1)) if eof else list(range(start, last + 1))
    for norm in (lambda s: s, lambda s: s.rstrip(), lambda s: s.strip()):
        want = [norm(x) for x in pattern]
        for i in order:
            if [norm(x) for x in lines[i:i + n]] == want:
                return i
    return None


def apply_update(old, lines):
    """照 apply_patch 的规矩把一个 Update 的几个块套到原文上 → 新文;套不上 → None。"""
    work = old.replace("\r\n", "\n").split("\n")
    if work and work[-1] == "":
        work = work[:-1]
    pos = 0
    for h in split_hunks(lines):
        old_seq, new_seq = [], []
        for line in h["lines"]:
            if line == "":
                old_seq.append("")
                new_seq.append("")
                continue
            tag, body = line[0], line[1:]
            if tag == " ":
                old_seq.append(body)
                new_seq.append(body)
            elif tag == "-":
                old_seq.append(body)
            elif tag == "+":
                new_seq.append(body)
            else:
                return None
        if h["header"]:
            at = seek(work, [h["header"]], pos, False)
            if at is None:
                return None
            pos = at + 1
        if not old_seq:
            at = len(work) if h["eof"] else pos
        else:
            at = seek(work, old_seq, pos, h["eof"])
            if at is None:
                return None
        work[at:at + len(old_seq)] = new_seq
        pos = at + len(new_seq)
    return "\n".join(work) + "\n"


def front_matter(text):
    """正本的 frontmatter → dict;没有 → {};读不出来 → None。"""
    if text is None:
        return {}
    import yaml
    t = text.replace("\r\n", "\n").lstrip(chr(0xFEFF))
    m = re.match(r"---\s*\n(.*?)\n---\s*(?:\n|$)", t, re.S)
    if not m:
        return {}
    try:
        meta = yaml.safe_load(m.group(1))
    except (yaml.YAMLError, ValueError):
        return None
    return meta if isinstance(meta, dict) else ({} if meta is None else None)


def approval_sig(meta):
    """确认记录的样子:EMPTY(没有、或还没确认:status 为 draft / awaiting、其余几项都空)或 SET:<原样>。"""
    from datetime import date
    ap = meta.get("approval") if isinstance(meta, dict) else None
    if ap is None:
        return "EMPTY"
    if not isinstance(ap, dict):
        return "SET:" + json.dumps(ap, default=str, ensure_ascii=False)
    # 不加引号的时刻 YAML 读成日期时间:按 ISO 写法比(同 pipeline_lib.date_text),免得重写 frontmatter 时换了引号就当成改了记录
    ap = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in ap.items()}
    empty = (ap.get("status") in (None, "", "draft", "awaiting") and all(ap.get(k) in (None, "") for k in APPROVAL_VALUES)
             and ap.get("signature") in (None, ""))
    return "EMPTY" if empty else "SET:" + json.dumps(ap, sort_keys=True, default=str, ensure_ascii=False)


def read_text(path):
    try:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def doc_problem(op, rel, d):
    """改任务计划 / 资料汇编 / 提纲:确认记录不许动,任务计划的进度不许改到助手不该推进的状态。"""
    base = d.get("cwd") if isinstance(d.get("cwd"), str) and d.get("cwd") else ROOT
    full = os.path.join(base, str(op["path"]).strip().strip("\"'"))
    old = read_text(full) if os.path.isfile(full) else None
    if op["op"] == "delete" or op.get("move_to"):
        new = None
    elif op["op"] == "add":
        new = "\n".join(l[1:] if l.startswith("+") else l for l in op["lines"]) + "\n"
    else:
        new = apply_update(old, op["lines"]) if old is not None else None
    old_meta = front_matter(old)
    new_meta = front_matter(new) if new is not None else {}
    name = rel.rsplit("/", 1)[-1]
    if old_meta is None or new_meta is None or (op["op"] == "update" and new is None and not op.get("move_to")):
        # 套不上补丁、或 frontmatter 读不出来:退一步只看补丁加进去的行
        if any(APPROVAL_TEXT_RE.match(l) for l in op["lines"]):
            return APPROVAL_REASON % name
        return None
    if approval_sig(old_meta) != approval_sig(new_meta) and "SET" in (approval_sig(old_meta)[:3], approval_sig(new_meta)[:3]):
        return APPROVAL_REASON % name
    if name == "task_plan.md" and new is not None:
        before, after = old_meta.get("pipeline_status"), new_meta.get("pipeline_status")
        if after != before and after is not None and after not in PLAN_STATES_OK:
            return STATE_REASON % after
    return None


def check_patch(d, ti):
    text = patch_text(ti)
    if not text:
        return None
    for op in parse_patch(text):
        for p in (op["path"], op.get("move_to")):
            if not p:
                continue
            rel = rel_of(p, d)
            kind = protected_kind(rel)
            if kind:
                return refuse(d, "apply_patch", PATCH_PROTECTED_REASON % (p, kind[0], kind[1]))
        rel = rel_of(op["path"], d)
        if rel and re.fullmatch(r"projects/[^/]+/(?:task_plan|dossier|outline)\.md", rel):
            reason = doc_problem(op, rel, d)
            if reason:
                return refuse(d, "apply_patch", reason)
    return None


# ---- PostToolUse:原生卡交回的原文 ----

def on_post(d):
    name = str(d.get("tool_name") or "").lower()
    if not name.endswith("request_user_input"):
        return None
    ti = tool_input(d)
    ids = asked_ids(ti)
    resp = d.get("tool_response", _MISSING)
    found = sessions.find_answers(resp) if resp is not _MISSING else None
    rec = {"event": "card_answer", "card_id": ids[0] if ids else None, "tool_use_id": d.get("tool_use_id")}
    if found is None:
        rec["response"] = None
        rec["response_state"] = "缺"
        if resp is not _MISSING:
            rec["raw"] = resp if isinstance(resp, (str, dict, list)) and len(json.dumps(resp, default=str)) <= 4000 else str(resp)[:4000]
    else:
        rec["response"] = found
    note(d, rec)
    return None


# ---- Stop:最后一条回复里不带内部的东西 ----

_SKILL_AFTER = r"(?!等级|培训|人才|提升|型|证书|鉴定|补贴|水平|结构|岗位|标准|大赛|工|劳动者|要求|竞赛|评价)"
_SELF_SAY = "不提你的说明和工具：说现在做到研究的哪一步、接下来做什么，停下来的理由用研究上的话说"


def self_ref_rules():
    import wording_check as wc
    return [
        wc.extra_rule("self:技能", r"(?:使用|调用|加载|读取|按照|依照|遵照|遵循)(?:「[^」\n]{1,12}」|[^\s，。；！？、,.;!?「」]{0,8}?)技能" + _SKILL_AFTER,
                      _SELF_SAY, order=10001),
        wc.extra_rule("self:技能", r"(?:这个|该|此|这份|这些|那个)技能" + _SKILL_AFTER, _SELF_SAY, order=10002),
        wc.extra_rule("self:技能", r"技能(?:说明|文件|指引|流程|列表|清单)", _SELF_SAY, order=10003),
        wc.extra_rule("self:技能", r"(?:资料卡片|选项卡|进度表|明确任务|收集资料|拟定提纲|撰写交付|独立复核|研究流程|云织)\s*技能",
                      _SELF_SAY, order=10004),
        wc.extra_rule("self:说明", r"工作指引|流程说明|说明书|说明文件|流程文件", _SELF_SAY,
                      except_words=("招股说明书", "募集说明书", "药品说明书", "产品说明书", "使用说明书"), order=10005),
        # 我们自己工具的名字(对用户只说「卡」「进度」,不说「选项卡」「进度表」)后面接流程、说明这类词:说的是自己的说明
        wc.extra_rule("self:说明", r"(?:选项卡|进度表)(?:流程|说明|指引|规矩|规则|技能)", _SELF_SAY, order=10006),
    ]


_MD_LINK_RE = re.compile(r"\[([^\]\n]*)\]\(\s*(<[^>\n]*>|[^)\n]*?)\s*\)")
_AUTOLINK_RE = re.compile(r"<((?:https?|file):[^<>\s]+)>", re.I)
_URL_RE = re.compile(r"(?:https?://|file:)[^\s<>()（）「」『』\"“”'\]，。；：、！？】》]+", re.I)
_WIN_PATH_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\"'<>|*?「」『』（）()，。；：！？]+")
_ROOTS = r"(?:projects|toolkit|agent-tools|\.agents|\.codex|\.codebuddy|wording|upstream|records|\.yz-tmp|out|查看|inputs|fetched|cards|drafts|review|library)"
_REL_PATH_RE = re.compile(r"(?<![\w/\\.:-])(?:\.{1,2}[\\/])?" + _ROOTS + r"[\\/][^\s\"'<>|*?「」『』（）()，。；：！？]*", re.I)
_EXT_PATH_RE = re.compile(r"(?<![\w/\\.:-])[^\s\"'<>|*?「」『』（）()，。；：！？/\\]+(?:[\\/][^\s\"'<>|*?「」『』（）()，。；：！？/\\]+)+"
                          r"\.(?:py|md|jsonl|json|ya?ml|html?|docx|txt|csv|pdf|ps1|toml)(?![A-Za-z0-9])", re.I)
_INTERNAL_PARTS = ("/.agents/", "/agent-tools/", "/toolkit/", "/records/", "/.codex/", "/.codebuddy/", "/wording/",
                   "/.yz-tmp/", "/_inbox/", "/upstream/", "/.records/")
_INTERNAL_NAMES = ("skill.md", "agents.md", "progress.md")
_INTERNAL_EXTS = (".py", ".jsonl", ".ps1", ".toml")
_OK_PARTS = ("/out/", "/查看/", "/inputs/originals/")
_OK_NAMES = ("/进度.html", "/右侧.html")
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]", "0.0.0.0")


def classify(target):
    """链接的目标或一段路径 → web(网址)· ok(成稿、「查看」网页、用户的材料、进度页)· internal(说明、脚本、记录……)· other(项目里别的文件)。"""
    from urllib.parse import unquote
    s = unquote(str(target).strip().strip("<>").strip("\"'")).replace("\\", "/")
    low = s.lower()
    if low.startswith(("http://", "https://")):
        host = re.sub(r"^https?://", "", low).split("/", 1)[0].split(":", 1)[0]
        return "other" if host in _LOCAL_HOSTS else "web"
    if low.startswith("mailto:"):
        return "web"
    if low.startswith("file:"):
        low = low[len("file:"):]
    probe = "/" + low.lstrip("/").split("#", 1)[0].split("?", 1)[0]
    if any(p in probe for p in _INTERNAL_PARTS) or any(probe.endswith("/" + n) for n in _INTERNAL_NAMES) \
            or probe.endswith(_INTERNAL_EXTS):
        return "internal"
    if probe.startswith(("/out/", "/查看/", "/inputs/originals/")) or any(p in probe for p in _OK_PARTS) \
            or probe.endswith(_OK_NAMES):
        return "ok"
    return "other"


def scan_reply(text, user_texts=()):
    """助手的一条回复 → 问题列表(给助手看的话);空 = 没问题。"""
    import wording_check as wc
    chars = list(text)
    problems = []

    def blank(a, b):
        for i in range(a, b):
            if chars[i] != "\n":
                chars[i] = " "

    def judge(target, shown):
        kind = classify(target)
        short = shown if len(shown) <= 60 else shown[:57] + "…"
        if kind == "internal":
            problems.append("内部文件的链接或路径「%s」（说明、脚本、记录不给用户看）" % short)
        elif kind == "other":
            problems.append("项目里的内部文件或本机地址「%s」（要给用户看材料，给「查看」里的网页或成稿）" % short)

    for m in _MD_LINK_RE.finditer(text):
        judge(m.group(2), m.group(0))
        blank(m.start(2), m.end(2))
    for rx in (_AUTOLINK_RE, _URL_RE, _WIN_PATH_RE, _REL_PATH_RE, _EXT_PATH_RE):
        cur = "".join(chars)
        for m in rx.finditer(cur):
            if not m.group(0).strip():
                continue
            judge(m.group(1) if rx is _AUTOLINK_RE else m.group(0), m.group(0))
            blank(m.start(), m.end())
    masked = "".join(chars)
    for h in wc.scan(masked, list(user_texts), "", extra=self_ref_rules()):
        problems.append("「%s」→ %s" % (h["text"], h["suggestion"]))
    seen, out = set(), []
    for p in problems:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def stop_user_texts(session_id):
    """扫回复时认作用户原话的:这个会话里用户说过的话、原生卡上写的话 + 各项目的委托原话。"""
    texts = [p for _t, p in sessions.prompts(session_id)] if session_id else []
    texts += sessions.answer_texts(session_id) if session_id else []
    root = sessions.projects_root()
    try:
        names = os.listdir(root)
    except OSError:
        names = []
    for name in names:
        req = os.path.join(root, name, "inputs", "request.md")
        if not name.startswith((".", "_")) and os.path.isfile(req):
            t = read_text(req)
            if t:
                texts.append(t)
    return texts


def next_step_lines(text, user_texts=()):
    """回复里告诉用户下一步怎么做的句子 → 原样列出来(最多两句),打回重说时请助手照留;
    本身就带内部东西的句子不列(要重说的正是它)。"""
    out = []
    for part in re.split("(?<=[。！？!?])|\n", text):
        s = part.strip()
        if s and NEXT_STEP_RE.search(s) and s not in out and not scan_reply(s, user_texts):
            out.append(s)
    return out[:2]


def on_stop(d):
    msg = d.get("last_assistant_message")
    if not isinstance(msg, str) or not msg.strip():
        return None
    users = stop_user_texts(d.get("session_id"))
    problems = scan_reply(msg, users)
    if not problems:
        return None
    if d.get("stop_hook_active"):
        note(d, {"event": "stop_passed", "problems": problems, "why": "已经打回过一次（stop_hook_active），这次放行"})
        return None
    keep = next_step_lines(msg, users)
    tail = ("，下面这句照原样留着：%s" % "".join("「%s」" % s for s in keep)) if keep else ""
    note(d, {"event": "stop_blocked", "problems": problems, "keep": keep})
    return {"decision": "block", "reason": STOP_REASON % ("；".join(problems[:6]), tail)}


if __name__ == "__main__":
    sys.exit(main(sys.argv))
