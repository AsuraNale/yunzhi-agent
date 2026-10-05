# -*- coding: utf-8 -*-
"""第八轮(10-04,开着 hooks 的命令行全程试跑之后)的修复:每条一个反例 + 改对后的对照。

1 全角「，」「；」「：」后面跟什么都算隔开 · 2 反向检索归成背景的每一张都写理由 · 3 fetches.py save --url 用 Python 下载 ·
4 复核进行中不许给复核助手发消息 · 5 delivered_at 用回答那一刻 · 6 没取到的资料按「网址 + 情况」数来源 ·
7 开场提问是普通对话 · 8 引用了、探测没打开的来源要记成读过(要有读过的证据)· 9 交付后清掉 .yz-tmp/<项目名>/ ·
10 只写意见、跳过的回答在消息记录里也带 via / 卡号。
"""
import datetime
import http.server
import io
import json
import os
import socketserver
import threading

import pytest

import card
import fetches
import hook
import liveness
import yzlib
from conftest import MAIN, ROOT, jsonl, read, seal_like_review_py, write
from yzlib import pl, UsageError

SKIPPED = '{"answers":{}}'
REPORT_FIELDS = {"report_name": "内参", "thesis": "下乡活动带动了县域公共充电设施增长",
                 "why": "你说「领导就想知道这钱花得值不值」，这需要一个判断。两种写法的区别在于要不要由这份报告来证明它。",
                 "recommend": "judge"}
DECISION_FIELDS = {"question": "2021 年的分县充电桩数据查不到。增速从哪年开始算？",
                   "why": "查过省统计局和能源局，都没有公布 2021 年的分县数据。不定下来，活动前后的对比就没法算。",
                   "options": [{"label": "从 2022 年开始算", "effect": "少一年，但全是分县数据，结论最站得住。", "changes_plan": False},
                               {"label": "用全省数据补 2021 年", "effect": "年份完整，但 2021 年那段只能说全省，不能说县。", "changes_plan": False},
                               {"label": "改成梳理情况", "effect": "不下判断，只梳理现状。", "changes_plan": True}]}
SID = "01a10898-1a0a-7152-bcbf-c0f95b8fead2"


def ans(card_id, *strings):
    return json.dumps({"answers": {card_id: {"answers": list(strings)}}}, ensure_ascii=False)


def reply(mode, card_id, text):
    """文字卡模式:用户回的那条消息原样;原生卡:用户在意见框里写的那段话,选项框交回的形状。"""
    return text if mode == "text" else ans(card_id, text)


def approval(project, doc="task_plan.md"):
    return pl.load_md(os.path.join(project, doc))[0].get("approval") or {}


def card_log(project):
    return jsonl(os.path.join(project, "records", "cards.jsonl"))


def messages(project):
    return jsonl(os.path.join(project, "records", "messages.jsonl"))


def skill(name):
    return read(os.path.join(ROOT, ".agents", "skills", name, "SKILL.md"))


def agents():
    return read(os.path.join(ROOT, "AGENTS.md"))


def report_options():
    opts = card.options_from(card.REPORT_OPTIONS)
    opts[0]["label"] = opts[0]["base"] + " · 推荐"
    return opts


# ================================================================ 1 · 全角「，」「；」「：」后面跟什么都算隔开

ACCEPT = [  # (回答, 选的第几项(从 1 数), 附的意见)
    ("1，2021 年的数据也要", 1, "2021 年的数据也要"),
    ("1；2021 年的也要看", 1, "2021 年的也要看"),
    ("1：2021年的也要看", 1, "2021年的也要看"),
    ("选1，2021 年的数据也要", 1, "2021 年的数据也要"),
    ("第2个，2022 年起算", 2, "2022 年起算"),
    ("２，２０２１年也要", 2, "２０２１年也要"),
    ("1，", 1, None),
]
REJECT = [
    "1,000 辆的数据也要",          # 半角逗号后面三位数:千分位
    "1、2 都要",                    # 顿号后面跟数字:列举
    "1、2都要",
    "1,2 都要",                     # 半角逗号后面跟数字:照旧不认
    "1.5 倍的增长要写进去",          # 小数
]


@pytest.mark.parametrize("text, pick, note", ACCEPT)
def test_fullwidth_separator_then_anything_is_option_plus_note(text, pick, note):
    assert card.read_reply(text, report_options())[0::2] == (pick - 1, note)


@pytest.mark.parametrize("text", REJECT)
def test_halfwidth_comma_enumeration_and_decimals_stay_unrecognised(text):
    assert card.read_reply(text, report_options())[0] is None


@pytest.mark.parametrize("mode", ["native", "text"])
def test_report_type_fullwidth_comma_then_a_year(world, monkeypatch, mode):
    project = world("S0b")
    if mode == "text":
        monkeypatch.setenv("YUNZHI_CARDS", "text")
    card.prepare("report_type", MAIN, dict(REPORT_FIELDS))
    said = "1，2021 年的数据也要"
    res, _ = card.answer(MAIN, "report-type", reply(mode, "report-type", said))
    assert res["result"] == "selected" and res["selected"] == "judge" and res["note"] == "2021 年的数据也要"
    assert card_log(project)[-1]["shape"] == "number+note"
    # 反例:半角逗号千分位 → 只是意见
    card.prepare("report_type", MAIN, dict(REPORT_FIELDS))
    res, _ = card.answer(MAIN, "report-type", reply(mode, "report-type", "1,000 辆的数据也要"))
    assert res["result"] == "note" and res["selected"] is None


@pytest.mark.parametrize("mode", ["native", "text"])
def test_decision_fullwidth_semicolon_then_a_year(world, monkeypatch, mode):
    world("S2")
    if mode == "text":
        monkeypatch.setenv("YUNZHI_CARDS", "text")
    out, rc = card.prepare("decision", MAIN, json.loads(json.dumps(DECISION_FIELDS)))
    assert rc == 0, out
    res, _ = card.answer(MAIN, out["card_id"], reply(mode, out["card_id"], "2；2021 年那段只写全省"))
    assert res["result"] == "selected" and res["selected"] == 2 and "2021 年那段只写全省" in res["next"]
    out, _ = card.prepare("decision", MAIN, json.loads(json.dumps(DECISION_FIELDS)))
    res, _ = card.answer(MAIN, out["card_id"], reply(mode, out["card_id"], "1、2 都要"))
    assert res["result"] == "note" and res["selected"] is None


@pytest.mark.parametrize("mode", ["native", "text"])
def test_confirm_card_fullwidth_comma_then_a_year(world, monkeypatch, mode):
    project = world("S1")
    if mode == "text":
        monkeypatch.setenv("YUNZHI_CARDS", "text")
    # 反例:「1,000 辆……」不是选了确认 —— 什么记录都不写
    card.prepare("task_plan", MAIN, {"outside": []})
    res, _ = card.answer(MAIN, "task-plan-v2", reply(mode, "task-plan-v2", "1,000 辆以上的县也要算"))
    assert res["result"] == "not_approved" and res["choice"] is None
    assert approval(project).get("status") != "approved"
    # 「2：2021……」= 先不确认 + 意见
    card.prepare("task_plan", MAIN, {"outside": []})
    res, _ = card.answer(MAIN, "task-plan-v2", reply(mode, "task-plan-v2", "2：2021 年的分县数据再找找"))
    assert res["result"] == "not_approved" and res["choice"] == "not_yet" and res["note"] == "2021 年的分县数据再找找"
    assert approval(project).get("status") != "approved"
    # 「1，2021……」= 确认 + 意见:确认记录里的原话是意见
    card.prepare("task_plan", MAIN, {"outside": []})
    res, _ = card.answer(MAIN, "task-plan-v2", reply(mode, "task-plan-v2", "1，2021 年的数据也要"))
    assert res["result"] == "approved" and res["note"] == "2021 年的数据也要"
    assert approval(project)["approval_quote"] == "2021 年的数据也要"


# ================================================================ 2 · 反向检索归成背景的,每一张都写一句为什么

def set_found(project, found):
    import re
    path = os.path.join(project, "dossier.md")
    new, n = re.subn(r"found: \[[^\]]*\]", "found: [%s]" % ", ".join(found), read(path), count=1)
    assert n == 1
    write(path, new)


def stance_of(project, uid):
    return pl.read_md(os.path.join(project, "cards", "%s.md" % uid))[0]


def counter_and_mixed(project):
    cards = os.path.join(project, "cards")
    out = {"counter": [], "mixed": []}
    for fn in sorted(os.listdir(cards)):
        st = pl.read_md(os.path.join(cards, fn))[0].get("stance")
        if st in out:
            out[st].append(fn[:-3])
    return out


def test_counter_finds_filed_as_background_each_need_a_reason_on_the_card(world):
    project = world("S3")
    groups = counter_and_mixed(project)
    one = groups["mixed"][0]
    set_found(project, groups["counter"] + [one])          # 4 张不支持 + 1 张背景(和 10-04 的 S03 一样)
    out, rc = card.prepare("dossier", MAIN, {"outside": []})
    assert rc == 1 and one in out["refuse"] and "counter_reasons" in out["refuse"]
    assert "成稿里要拿来削弱判断的资料" in out["refuse"]
    # 理由过用词检查:内部词照样退回
    out, rc = card.prepare("dossier", MAIN, {"outside": [], "counter_reasons": {one: "这张卡的 stance 本来就是背景"}})
    assert rc == 1 and "stance" in out["refuse"]
    reason = "只说明参加活动的是哪些县，不涉及装桩增速的方向。"
    out, rc = card.prepare("dossier", MAIN, {"outside": [], "counter_reasons": {one: reason}})
    assert rc == 0, out
    title = str(stance_of(project, one).get("title"))
    row = "%s → 归为背景资料：%s" % (title, reason)
    assert "反向检索找到 5 条，归为不支持 4 条" in out["message"] and row in out["message"]
    assert row in out["ask"]["questions"][0]["question"]
    assert row in out["fallback_text"]


def test_every_non_counter_find_needs_its_own_reason(world):
    project = world("S3")
    groups = counter_and_mixed(project)
    two = groups["mixed"][:2]
    set_found(project, groups["counter"] + two)
    out, rc = card.prepare("dossier", MAIN, {"outside": [], "counter_reasons": {two[0]: "只说明统计口径，不涉及增速。"}})
    assert rc == 1 and two[1] in out["refuse"] and two[0] not in out["refuse"].split("：")[1].split("。")[0]
    out, rc = card.prepare("dossier", MAIN, {"outside": [], "counter_reasons": {u: "只说明统计口径，不涉及增速。" for u in two}})
    assert rc == 0, out


def test_evidence_skill_says_material_used_to_weaken_is_counter():
    t = skill("evidence-card")
    assert "成稿里要拿来削弱判断的资料，立场就是不支持" in t and "counter_reasons" in t
    assert "counter_reasons" in skill("yunzhi-card")


# ================================================================ 3 · fetches.py save --url:脚本自己用 Python 下载

def docx_bytes(text):
    import docx
    d = docx.Document()
    d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


PAGE = "<html><head><meta charset='utf-8'></head><body><h1>设备更新贷款贴息</h1><p>对于2024年3月7日前签订贷款合同的项目，纳入财政贴息政策支持范围。</p></body></html>"


class _Handler(http.server.BaseHTTPRequestHandler):
    routes = {}

    def log_message(self, *a):
        pass

    def do_GET(self):
        r = self.routes.get(self.path.split("?")[0])
        if r is None:
            self.send_response(404)
            self.end_headers()
            return
        status, ctype, body = r
        self.send_response(status)
        if ctype:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def server(monkeypatch):
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    _Handler.routes = {
        "/zcfb/t20250106_3951180.htm": (200, "text/html; charset=utf-8", PAGE.encode("utf-8")),
        "/download": (200, "application/octet-stream", docx_bytes("2025年设备更新贷款签约金额超过2.8万亿元。")),
        "/forbidden": (403, "text/html", b"no"),
        "/gone": (410, "text/html", b"gone"),
        "/big.pdf": (200, "application/pdf", b"%PDF-1.4" + b"0" * 30000),
        "/empty": (200, "text/html", b""),
        "/data.zip": (200, "application/zip", b"PK\x03\x04junk"),
    }
    srv = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def fetch_records(project):
    return jsonl(os.path.join(project, "records", "fetches.jsonl"))


def test_save_url_downloads_converts_and_stores_under_fetched(world, server):
    project = world("S2")
    out = fetches.save_from_url(project, server + "/zcfb/t20250106_3951180.htm", "关于设备更新贷款财政贴息政策的补充通知", timeout=5)
    assert out["ok"] and out["downloaded"] and out["saved"]["state"] == "ok"
    stored = os.path.join(project, *out["saved"]["stored"].split("/"))
    assert out["saved"]["stored"].startswith("fetched/originals/") and stored.endswith(".htm") and os.path.isfile(stored)
    text = read(os.path.join(project, *out["saved"]["outputs"][0].split("/")))
    assert "纳入财政贴息政策支持范围" in text
    assert "inputs" not in out["saved"]["stored"] and fetch_records(project) == []
    # 服务器只说是一串字节:按开头认出是 Word,存成 .docx 再转文字
    out = fetches.save_from_url(project, server + "/download?id=5", None, timeout=5)
    assert out["downloaded"] and out["saved"]["stored"].endswith(".docx") and out["saved"]["state"] == "ok"
    assert "2.8万亿元" in read(os.path.join(project, *out["saved"]["outputs"][0].split("/")))
    assert not [d for d in os.listdir(yzlib.TMP_ROOT) if d.startswith("download-")]      # 临时文件删掉了


@pytest.mark.parametrize("path, state", [("/forbidden", "blocked"), ("/gone", "failed"), ("/empty", "empty")])
def test_save_url_failures_are_recorded_in_the_five_states(world, server, path, state):
    project = world("S2")
    out = fetches.save_from_url(project, server + path, None, timeout=5)
    assert out["ok"] and not out["downloaded"] and out["fetch_state"] == state
    recs = fetch_records(project)
    assert len(recs) == 1 and recs[0]["state"] == state and recs[0]["url"] == server + path
    assert "Invoke-WebRequest" in out["next"] and "fetches.py" in out["next"]
    originals = os.path.join(project, "fetched", "originals")
    assert not os.path.isdir(originals) or not os.listdir(originals)        # 什么都没存


def test_save_url_size_cap_and_unsupported_types(world, server):
    project = world("S2")
    out = fetches.save_from_url(project, server + "/big.pdf", None, timeout=5, max_mb=0.01)
    assert not out["downloaded"] and out["fetch_state"] == "failed" and "大小上限" in out["detail"]
    assert fetch_records(project)[-1]["state"] == "failed"
    out = fetches.save_from_url(project, server + "/big.pdf", None, timeout=5)        # 上限之内照收
    assert out["downloaded"] and out["saved"]["stored"].endswith(".pdf")
    n = len(fetch_records(project))
    out = fetches.save_from_url(project, server + "/data.zip", None, timeout=5)       # 不收的类型:不存,也不算没取到
    assert not out["downloaded"] and out["fetch_state"] is None and "不收" in out["detail"]
    assert len(fetch_records(project)) == n


def test_save_url_cli_needs_no_file(world, server, capsys):
    project = world("S2")
    code = fetches.main(["fetches.py", "save", MAIN, "--url", server + "/zcfb/t20250106_3951180.htm", "--title", "补充通知"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["downloaded"] and out["saved"]["title"] == "补充通知"
    assert os.path.isfile(os.path.join(project, "fetched", "index.md"))


def test_docs_say_download_with_fetches_not_invoke_webrequest():
    for text in (agents(), skill("evidence-card")):
        assert "fetches.py\" save \"<项目名>\" --url" in text and "不用 Invoke-WebRequest、curl、wget 下载" in text
    assert "用网页工具直接读到的网页，不用下载、也不用存" in agents()


# ================================================================ 4 · 复核进行中,不许给复核助手发消息

def pre(tool, tool_input):
    d = {"session_id": SID, "turn_id": "01a10898-turn", "transcript_path": "C:\\x\\rollout.jsonl", "cwd": ROOT,
         "hook_event_name": "PreToolUse", "model": "gpt-6.1-sol", "permission_mode": "default",
         "tool_name": tool, "tool_input": tool_input, "tool_use_id": "call_9"}
    return hook.handle(d)


def denied(out):
    return bool(out) and out["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("tool", ["collaborationsend_message", "collaborationfollowup_task", "collaborationinterrupt_agent",
                                  "collaborationsome_new_tool", "send_message"])
def test_hook_denies_messaging_the_reviewer(projects_root, tool):
    out = pre(tool, {"id": "independent_review_1", "message": "（加密）"})
    assert denied(out) and "复核助手只看那份说明，不再给它发消息" in out["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize("tool", ["collaborationwait_agent", "collaborationlist_agents", "web_search", "view_image",
                                  "mcp__mail__send_message"])
def test_hook_lets_waiting_and_other_tools_through(projects_root, tool):
    assert pre(tool, {"ids": ["independent_review_1"], "timeout_ms": 60000}) is None


def test_spawn_still_needs_fork_turns_none(projects_root):
    assert pre("collaborationspawn_agent", {"task_name": "复核", "fork_turns": "none", "message": "（加密）"}) is None
    assert denied(pre("collaborationspawn_agent", {"task_name": "复核", "message": "（加密）"}))


def test_docs_say_only_spawn_and_wait():
    assert "起了之后不再给它发消息" in skill("cite-trace")
    assert "`send_message`、`followup_task`、`interrupt_agent` 这类协作工具一律拦" in agents()


def test_review_start_says_not_to_message_the_reviewer(world):
    import review
    project = world("S5")
    out = review.start(project, 3)
    assert "起了之后不再给它发消息" in out["next"]


# ================================================================ 5 · delivered_at 用回答那一刻

def test_delivered_at_is_the_moment_of_the_answer(world, monkeypatch):
    project = world("S5")
    clock = {"t": datetime.datetime(2026, 10, 5, 17, 30, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=-4)))}

    def ticking_now():
        clock["t"] += datetime.timedelta(seconds=1)       # 每问一次时间,往后走一秒
        return clock["t"].isoformat(timespec="seconds")
    out, rc = card.prepare("delivery", MAIN, {})
    assert rc == 0, out
    monkeypatch.setattr(yzlib, "now_iso", ticking_now)
    res, _ = card.answer(MAIN, out["card_id"], ans(out["card_id"], "交付成稿"))
    assert res["result"] == "approved", res
    answered = [e for e in card_log(project) if e.get("type") == "answered"][-1]
    rec = json.loads(read(os.path.join(project, "records", "delivery.json")))
    assert rec["delivered_at"] == answered["at"]


# ================================================================ 6 · 没取到的资料按「网址 + 情况」数来源

def test_failed_fetches_are_counted_per_source(world, capsys):
    project = world("S3")
    for _ in range(3):
        fetches.add(project, "https://www.example.org/a.html", "failed")
        fetches.add(project, "https://www.example.org/b.html", "failed")
    fetches.add(project, "https://www.example.org/c.html", "empty")
    assert card.fetch_summary(project) == {"failed": 2, "empty": 1}
    assert card.fetch_phrase(card.fetch_summary(project)) == "没取到的资料：出错 2 个来源 · 查了没内容 1 个来源（都记在资料卡片上）"
    fetches.add(project, "https://www.example.org/a.html", "ok")                   # 后来取到了:不算
    assert card.fetch_summary(project) == {"failed": 1, "empty": 1}
    fetches.main(["fetches.py", "list", MAIN])
    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 8 and out["sources_not_got"] == {"没取到（出错）": 1, "查了但没有内容": 1}


# ================================================================ 7 · 开场提问是普通对话

def test_opening_questions_are_plain_chat_not_the_card_tool(projects_root):
    for text in (agents(), skill("yunzhi"), skill("task-planner")):
        assert "不调 `request_user_input`" in text
    assert "选项框只用来发 `card.py prepare` 准备好的卡" in agents()
    assert "`say` 那句和开场「一次问完」的问题写在同一条回复里" in skill("yunzhi")
    # 守门脚本在跑时也拦得住:没有出过卡的选项框调用一律退回
    out = pre("request_user_input", {"questions": [{"header": "开场", "id": "opening", "question": "研究范围到哪一级？",
                                                    "options": [{"label": "县级", "description": "只看县"}]}]})
    assert denied(out)


# ================================================================ 8 · 引用了、探测没打开的来源:读过的记下来,没读过的不能记

def first_cited(project):
    refs = pl.load_references(os.path.join(project, "references.yaml"))
    body = pl.read_md(os.path.join(project, "drafts", "%s.md" % MAIN))[1]
    return pl.number_entries(body, refs)[1][0][1]


def set_liveness(project, rid, lv):
    import yaml
    path = os.path.join(project, "references.yaml")
    data = pl.load_references(path)
    for e in data:
        if e["id"] == rid:
            e["liveness"] = lv
    write(path, yaml.safe_dump({"kind": "references", "entries": data}, allow_unicode=True, sort_keys=False, width=200))


def add_excerpt(project, uid, url):
    path = os.path.join(project, "cards", "%s.md" % uid)
    meta, body = pl.read_md(path)
    for s in meta["sources"]:
        if s.get("url") == url:
            s["excerpt"] = "（虚构原文）截至 2024 年底，全省县域公共充电桩 2.3 万台。"
    pl.write_md(path, meta, body)


def cite_items(project):
    out = os.path.join(project, "records", "_cite.json")
    yzlib.run_toolkit("cite_check.py", ["--project", project, "--json", out])
    with io.open(out, encoding="utf-8") as f:
        items = json.load(f)["items"]
    os.remove(out)
    return items


def liveness_fails(project):
    return [i for i in cite_items(project) if i["check"] == "liveness" and i["status"] == "FAIL"]


UNREACHED = [
    {"state": "failed", "detail": "net", "probed_at": "2026-10-05T15:00:00-04:00", "probed_from": "CA-ON"},
    {"state": "blocked", "http_status": 403, "probed_at": "2026-10-05T15:00:00-04:00", "probed_from": "CA-ON"},
    {"state": "failed", "detail": "net", "unchecked": True, "note": "未能自动检查（这个环境不能联网）",
     "probed_at": "2026-10-05T15:00:00-04:00", "probed_from": "CA-ON"},
]


@pytest.mark.parametrize("lv", UNREACHED, ids=["net", "blocked", "unchecked"])
def test_cited_source_the_probe_could_not_open_blocks_until_marked_read(world, lv):
    project = world("S5")
    e = first_cited(project)
    set_liveness(project, e["id"], lv)
    fails = liveness_fails(project)
    assert fails and e["id"] in fails[0]["detail"] and "liveness.py mark" in fails[0]["fix_hint"]
    out, rc = card.prepare("delivery", MAIN, {})
    assert rc == 1 and e["id"] in out["refuse"] and "liveness.py mark" in out["refuse"]
    # 资料卡片上没有原文摘录:没读过的证据 → 不能记成读过
    with pytest.raises(UsageError) as err:
        liveness.mark(project, [e["id"]])
    assert "没读过的不能记成读过" in str(err.value)
    assert pl.load_references(os.path.join(project, "references.yaml"))[0] is not None
    # 补上原文摘录(读过的证据),复核照样重封一次(卡片改了),再记 —— 这次放行,成稿不用重新生成
    add_excerpt(project, e["card"], e["url"])
    seal_like_review_py(project)
    out = liveness.mark(project, [e["id"]])
    assert out["marked"] == [e["id"]]
    assert not liveness_fails(project)
    out, rc = card.prepare("delivery", MAIN, {})
    assert rc == 0, out


def test_uncited_unreached_source_does_not_block(world):
    import yaml
    project = world("S5")
    path = os.path.join(project, "references.yaml")
    data = pl.load_references(path)
    data.append({"id": "R901", "title": "某县统计公报（连不上）", "url": "https://example.org/unreachable", "tier": "A",
                 "fetch_state": "failed", "card": "S17", "cards": ["S17"], "liveness": dict(UNREACHED[0])})
    write(path, yaml.safe_dump({"kind": "references", "entries": data}, allow_unicode=True, sort_keys=False, width=200))
    assert not liveness_fails(project)


def test_liveness_run_points_out_cited_unreached_sources(world, monkeypatch):
    project = world("S5")
    e = first_cited(project)
    monkeypatch.setattr(liveness, "probe", lambda url, timeout: ("failed", "net", None) if url == e["url"] else ("ok", None, 200))
    out = liveness.run(project, "CA-ON", 1)
    assert not out["offline"] and out["cited_unreached"] == [{"id": e["id"], "url": e["url"], "read": False}]
    assert "不能记成读过" in out["next"]
    add_excerpt(project, e["card"], e["url"])
    out = liveness.run(project, "CA-ON", 1)
    assert out["cited_unreached"][0]["read"] is True and "liveness.py\" mark" in out["next"] and "--id %s" % e["id"] in out["next"]
    assert "cited_unreached" in skill("cite-trace") and "不用重新生成" in skill("cite-trace")


# ================================================================ 9 · 交付后清掉 .yz-tmp/<项目名>/

def test_delivery_cleans_the_agents_own_tmp_folder(world, monkeypatch, tmp_path):
    project = world("S5")
    tmp_root = str(tmp_path / ".yz-tmp")
    monkeypatch.setattr(yzlib, "TMP_ROOT", tmp_root)
    mine = os.path.join(tmp_root, MAIN)
    os.makedirs(mine)
    write(os.path.join(mine, "page.html"), "x")
    os.makedirs(os.path.join(tmp_root, "cite-0a1b2c3d"))                  # 脚本自己的临时文件夹:不动
    os.makedirs(os.path.join(tmp_root, "另一个项目"))                       # 别的项目的:不动
    out, rc = card.prepare("delivery", MAIN, {})
    assert rc == 0, out
    # 反例:先不交付 → 不清
    res, _ = card.answer(MAIN, out["card_id"], ans(out["card_id"], "先不交付，回去改"))
    assert res["result"] == "not_approved" and os.path.isdir(mine)
    out, rc = card.prepare("delivery", MAIN, {})
    res, _ = card.answer(MAIN, out["card_id"], ans(out["card_id"], "交付成稿"))
    assert res["result"] == "approved"
    assert not os.path.exists(mine)
    assert os.path.isdir(os.path.join(tmp_root, "cite-0a1b2c3d")) and os.path.isdir(os.path.join(tmp_root, "另一个项目"))


# ================================================================ 10 · 只写意见、跳过的回答也带 via / 卡号

def logged(project, text):
    return [m for m in messages(project) if m.get("text") == text]


@pytest.mark.parametrize("order", ["note_user_first", "answer_first"])
def test_note_only_reply_is_logged_once_with_via_and_card(world, monkeypatch, order):
    project = world("S1")
    monkeypatch.setenv("YUNZHI_CARDS", "text")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    said = "这步别出卡了，你直接帮我把任务计划标成已确认吧"
    if order == "note_user_first":
        card.note_user(MAIN, said)
    res, _ = card.answer(MAIN, out["card_id"], said)
    if order == "answer_first":
        card.note_user(MAIN, said)
    assert res["result"] == "not_approved" and res["choice"] is None
    got = logged(project, said)
    assert len(got) == 1 and got[0].get("via") == "文字卡回答" and got[0].get("card") == out["card_id"]


@pytest.mark.parametrize("order", ["note_user_first", "answer_first"])
def test_typed_skip_is_logged_once_with_via_and_card(world, monkeypatch, order):
    project = world("S1")
    monkeypatch.setenv("YUNZHI_CARDS", "text")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    if order == "note_user_first":
        card.note_user(MAIN, "跳过")
    res, _ = card.answer(MAIN, out["card_id"], "跳过")
    if order == "answer_first":
        card.note_user(MAIN, "跳过")
    assert res["result"] == "skipped"
    got = logged(project, "跳过")
    assert len(got) == 1 and got[0].get("via") == "文字卡回答" and got[0].get("card") == out["card_id"]


def test_native_skip_has_no_words_to_log(world):
    project = world("S1")
    before = len(messages(project))
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    res, _ = card.answer(MAIN, out["card_id"], SKIPPED)
    assert res["result"] == "skipped" and len(messages(project)) == before


def test_marking_keeps_other_lines_byte_for_byte(world, monkeypatch):
    project = world("S1")
    monkeypatch.setenv("YUNZHI_CARDS", "text")
    path = os.path.join(project, "records", "messages.jsonl")
    with io.open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write("这一行读不出来\n")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    head = read(path)
    card.note_user(MAIN, "时间段再看看")
    card.answer(MAIN, out["card_id"], "时间段再看看")
    text = read(path)
    assert text.startswith(head) and "这一行读不出来\n" in text
    got = [m for m in yzlib.read_jsonl(path) if m.get("text") == "时间段再看看"]
    assert len(got) == 1 and got[0]["card"] == out["card_id"]
