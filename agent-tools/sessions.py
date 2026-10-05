# -*- coding: utf-8 -*-
"""sessions — 守门脚本(hook.py)自己的会话记录:projects/.records/sessions/<会话号>.jsonl。

hook.py 写:用户每条原话(UserPromptSubmit)、原生选项卡问了哪张卡(PreToolUse 放行时)、卡上交回的原文
(PostToolUse)、拦下了什么、最后一条回复被打回重说了什么。card.py 读:回答、引文只认用户真说过的话。
助手写不了这份记录:守门脚本拦下往 projects/.records/ 里写的命令和编辑(hook.py 第 6、7 条)。

只用标准库:hook 每次工具调用都要跑,不能把 PyYAML、工具包都拉进来拖慢。

「hooks 在跑」的判法(card.py 用):当前会话号(环境变量 CODEX_THREAD_ID,Codex 给助手跑的每条命令都带;
CODEX_SESSION_ID 同值)有一份记录,而且里面至少有一条。没有就是 hooks 没跑(文件夹没信任、hooks 没审过):
一切照旧靠说明,不硬依赖 hooks。
"""
import io
import json
import os
import re
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]")


def projects_root():
    return os.environ.get("YUNZHI_PROJECTS") or os.path.join(ROOT, "projects")


def records_root():
    """projects/.records:跨项目的记录(选项目的卡、hook 的会话记录与出错记录)。"""
    return os.path.join(projects_root(), ".records")


def sessions_dir():
    return os.path.join(records_root(), "sessions")


def error_log():
    return os.path.join(records_root(), "hook-errors.log")


def session_file(session_id):
    """会话号 → 记录文件;会话号读不出 → None。会话号只留字母、数字、_ . -(不让它带出路径)。"""
    sid = _SAFE_ID.sub("_", str(session_id or "").strip())[:120].strip(".")
    return os.path.join(sessions_dir(), sid + ".jsonl") if sid else None


def current_session_id():
    """助手跑的命令里带的会话号(Codex 设的环境变量;等于 hook 输入里的 session_id)。"""
    for key in ("CODEX_THREAD_ID", "CODEX_SESSION_ID"):
        v = (os.environ.get(key) or "").strip()
        if v:
            return v
    return None


def stamp():
    """(ISO 时刻带时区, 纪元秒)。先后按纪元秒比(比 ISO 的秒更细:出卡和回答可能在同一秒里)。"""
    ts = time.time()
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds"), ts


def append(session_id, record):
    """追加一条;会话号读不出就不记。一次 write 写完一行(几个 hook 同时追加也不会交错成半行)。"""
    path = session_file(session_id)
    if not path:
        return None
    os.makedirs(os.path.dirname(path), exist_ok=True)
    at, ts = stamp()
    rec = {"at": at, "ts": ts}
    rec.update(record)
    line = json.dumps(rec, ensure_ascii=False, default=str) + "\n"
    with io.open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(line)
    return path


def read(session_id):
    """一个会话的全部记录(读不出的行跳过)。"""
    path = session_file(session_id)
    return read_jsonl(path) if path else []


def read_jsonl(path):
    """一份 .jsonl → 键值映射的列表;文件不在 → [];读不出的行跳过(同 yzlib.read_jsonl,这里不拉 yzlib 进来)。"""
    out = []
    if not path or not os.path.isfile(path):
        return out
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    return out


def active(session_id=None):
    """这个会话(缺省:当前会话)有 hook 记录 = hooks 在跑。"""
    sid = session_id or current_session_id()
    path = session_file(sid) if sid else None
    if not path or not os.path.isfile(path):
        return False
    try:
        return os.path.getsize(path) > 0 and bool(read(sid))
    except OSError:
        return False


def norm_text(s):
    """比用户原话用的形式:换行统一成 \\n,去掉首尾空白。别的一个字都不动。"""
    return (s or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _ts(rec):
    t = rec.get("ts")
    return t if isinstance(t, (int, float)) and not isinstance(t, bool) else None


def prompts(session_id, after=None):
    """这个会话里用户的原话 [(纪元秒, 原话)];after 给了就只要它之后的。"""
    out = []
    for r in read(session_id):
        if r.get("event") != "prompt" or not isinstance(r.get("prompt"), str):
            continue
        t = _ts(r)
        if after is not None and (t is None or t <= after):
            continue
        out.append((t, r["prompt"]))
    return out


_USER_NOTE = re.compile(r"\A\s*user_note\s*[:：]\s*(.*)\Z", re.S)


def answer_texts(session_id):
    """这个会话里原生卡交回的、用户自己的字:卡上写的那段话(意见框里写的不经对话,不在用户原话里)。
    点选项交回的选项名也在里面(那是卡上本来就有的字,当用户说过的也无妨);「user_note: …」去掉前缀。"""
    out = []
    for r in read(session_id):
        resp = r.get("response") if r.get("event") == "card_answer" else None
        answers = resp.get("answers") if isinstance(resp, dict) else None
        for entry in (answers.values() if isinstance(answers, dict) else []):
            for s in (entry.get("answers") if isinstance(entry, dict) else None) or []:
                if isinstance(s, str) and s.strip():
                    m = _USER_NOTE.match(s)
                    out.append(m.group(1) if m else s)
    return out


def all_user_words():
    """所有会话里用户说过、写过的字:对话里的原话 + 原生卡上写的话(卡上的引文认任何一个会话里的)。"""
    d = sessions_dir()
    out = []
    if not os.path.isdir(d):
        return out
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".jsonl"):
            sid = fn[:-len(".jsonl")]
            out += [p for _t, p in prompts(sid)] + answer_texts(sid)
    return out


def card_key(card_id):
    """卡号比对时不计 - 与 _、不分大小写(同 card._id_key)。"""
    return str(card_id or "").replace("_", "-").lower()


def card_records(session_id, card_id, after=None):
    """这个会话里关于某张卡的记录(问过 card_asked / 交回的原文 card_answer),after 之后的,按先后。"""
    key = card_key(card_id)
    out = []
    for r in read(session_id):
        if r.get("event") not in ("card_asked", "card_answer") or card_key(r.get("card_id")) != key:
            continue
        t = _ts(r)
        if after is not None and (t is None or t <= after):
            continue
        out.append(r)
    return out


def find_answers(obj, depth=0):
    """选项框交回的东西 → {"answers": {...}};找不到 → None。交回的可能是那段 JSON 本身、一段 JSON 文字,
    或者包在别的键里(这个工具 PostToolUse 时 tool_response 长什么样还没实测过),逐层找第一个 answers 映射。"""
    if depth > 6:
        return None
    if isinstance(obj, str):
        s = obj.strip()
        if s[:1] in ("{", "["):
            try:
                return find_answers(json.loads(s), depth + 1)
            except ValueError:
                return None
        return None
    if isinstance(obj, dict):
        if isinstance(obj.get("answers"), dict):
            return {"answers": obj["answers"]}
        for v in obj.values():
            got = find_answers(v, depth + 1)
            if got is not None:
                return got
        return None
    if isinstance(obj, list):
        for v in obj:
            got = find_answers(v, depth + 1)
            if got is not None:
                return got
    return None


def canon(value):
    """按结构比:映射不计键的先后,字符串去掉首尾空白、换行统一;别的照原样。"""
    if isinstance(value, dict):
        return {str(k): canon(v) for k, v in value.items()}
    if isinstance(value, list):
        return [canon(v) for v in value]
    if isinstance(value, str):
        return norm_text(value)
    return value


# ---- 第九轮:还在等用户定的报告类型卡 / 决定卡(hook.py 拦推进、card.py 拦出确认卡,用的是同一份判法) ----
# 回答过的卡(同 yzlib.CARD_CLOSED;测试核两边一致)
CARD_CLOSED = ("answered", "skipped", "changed", "unclear", "error")
CHOICE_KINDS = ("report_type", "decision")
# 只写了意见、没选选项的回答记成的 result(同 card.NOTE_RESULTS)
NOTE_RESULTS = ("not_approved", "note")
WAITING_SAID = {"unanswered": "卡还没回答", "note": "用户只写了意见、没选选项", "skipped": "卡收起来了",
                "unclear": "回答没认出来，还没问清"}


def waiting_choice_card(events):
    """一个项目的卡片记录 → 最近出的那张卡是报告类型卡或决定卡、而用户还没在上面选定一项时:
    (出卡记录, unanswered / note / skipped / unclear);否则 (None, None)。
    「还没选定」= 这张卡后面没有一条选了某一项的回答(answered 且 choice 不是空的):出了还没回答(unanswered);
    最后一条回答只写了意见、没选选项(note);收起来了(skipped);回答没认出来(unclear)。其中「只有一条记录、是只写了意见或
    收起来」的,这张卡还能再回答一次(同 card.reopen_reason,测试核两边一致);别的要重新出卡问清 —— 但不管哪种,
    这件事都还没定。只看最近出的那张:之后出过别的卡就不算了。
    10-04 第二次全程试跑:决定卡收到「这个缺口我也没办法，你看着办」(只写了意见),助手接着写资料汇编、推进进度、
    去出资料汇编卡 —— 用户什么都没选。"""
    last_i = None
    for i, e in enumerate(events):
        if e.get("type") == "prepared":
            last_i = i
    if last_i is None:
        return None, None
    last = events[last_i]
    if last.get("kind") not in CHOICE_KINDS:
        return None, None
    cid = last.get("card_id")
    closing = [e for e in events[last_i + 1:] if e.get("card_id") == cid and e.get("type") in CARD_CLOSED]
    if any(c.get("type") == "answered" and c.get("choice") is not None for c in closing):
        return None, None                       # 用户在卡上(或回数字)选定了一项
    if not closing:
        return last, "unanswered"
    c = closing[-1]
    if c.get("type") == "skipped":
        return last, "skipped"
    if c.get("type") == "answered" and c.get("result") in NOTE_RESULTS:
        return last, "note"
    return last, "unclear"


def project_waiting_card(project_dir):
    """projects/<项目名>/records/cards.jsonl → waiting_choice_card 的结果。"""
    return waiting_choice_card(read_jsonl(os.path.join(project_dir, "records", "cards.jsonl")))
