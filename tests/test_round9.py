# -*- coding: utf-8 -*-
"""第九轮(10-04,开着 hooks 的第二次命令行全程试跑之后)的修复:每条一个反例 + 改对后的对照。

1 只写了意见之后助手没停下:a say 直接说推荐(推荐可以说,替用户选不行)· b 还有报告类型卡 / 决定卡在等用户定时,
  守门脚本拦推进、出卡脚本不出确认类的卡 · c 说明写清「说完 say 就结束这一轮」· d 最后一条回复里不讲工具怎么运作
2 反爬虫的验证页记成打不开、不存原件 · 3 资料缺口卡上的来源重新打得开,探测完提示重读、再定缺口 ·
4 反向检索的查询词里不许有已经找到的数字 · 5 mark 的读过证据:存了原件的,摘录要在原文里找得到 ·
6 网页版只数在用的资料卡片 · 交付后清 projects/_inbox/<项目名>/ · 等复核时说一句就够
"""
import http.server
import json
import os
import re
import socketserver
import threading

import pytest

import card
import fetches
import hook
import liveness
import review
import sessions
import yzlib
from conftest import FIXTURES, MAIN, ROOT, USER_TEXT, jsonl, read, write
from test_skills import SKILLS, body_spans, prescribed_lines, text_of
from yzlib import pl, UsageError

SID = "01a10898-1a0a-7152-bcbf-c0f95b8fead2"
SKIPPED = '{"answers":{}}'
# 第二次全程试跑里那份 987 字节的原件(和示例夹具放在一起;变异检查在工作区拷贝里跑时也从原处读)
CHALLENGE = os.path.join(os.path.dirname(FIXTURES), "challenge", "eo-bot-cookie-page.html")
REPORT_FIELDS = {"report_name": "内参", "thesis": "下乡活动带动了县域公共充电设施增长",
                 "why": "你说「领导就想知道这钱花得值不值」，这需要一个判断。两种写法的区别在于要不要由这份报告来证明它。",
                 "recommend": "judge"}
DECISION_FIELDS = {"question": "2021 年的分县充电桩数据查不到。增速从哪年开始算？",
                   "why": "查过省统计局和能源局，都没有公布 2021 年的分县数据。不定下来，活动前后的对比就没法算。",
                   "options": [{"label": "从 2022 年开始算", "effect": "少一年，但全是分县数据，结论最站得住。", "changes_plan": False},
                               {"label": "用全省数据补 2021 年", "effect": "年份完整，但 2021 年那段只能说全省，不能说县。", "changes_plan": False},
                               {"label": "改成梳理情况", "effect": "不下判断，只梳理现状。", "changes_plan": True}],
                   "recommend": 1}
ADVANCE = 'py -X utf8 "toolkit/scripts/stamp.py" "projects/%s/task_plan.md" --advance gate2_awaiting' % MAIN


def ans(card_id, *strings):
    return json.dumps({"answers": {card_id: {"answers": list(strings)}}}, ensure_ascii=False)


def reply(mode, card_id, text):
    return text if mode == "text" else ans(card_id, text)


def fields(base, **kw):
    out = json.loads(json.dumps(base))
    out.update(kw)
    return out


def skill(name):
    return read(os.path.join(ROOT, ".agents", "skills", name, "SKILL.md"))


def agents():
    return read(os.path.join(ROOT, "AGENTS.md"))


def base(event, **kw):
    d = {"session_id": SID, "turn_id": "01a10898-turn", "transcript_path": "C:\\Users\\x\\.codex\\sessions\\rollout.jsonl",
         "cwd": ROOT, "hook_event_name": event, "model": "gpt-6.1-sol", "permission_mode": "default"}
    d.update(kw)
    return d


def bash(cmd):
    return hook.handle(base("PreToolUse", tool_name="Bash", tool_input={"command": cmd}, tool_use_id="call_1"))


def denied(out):
    return bool(out) and out["hookSpecificOutput"]["permissionDecision"] == "deny"


def reason(out):
    return out["hookSpecificOutput"]["permissionDecisionReason"]


def stop(msg, active=False):
    return hook.handle(base("Stop", last_assistant_message=msg, stop_hook_active=active))


def first_label(out):
    return out["ask"]["questions"][0]["options"][0]["label"]


# ================================================================ 1a · 只写了意见:say 直接说推荐,但不替用户选

@pytest.mark.parametrize("mode", ["native", "text"])
def test_report_type_note_only_say_recommends_but_does_not_choose(world, monkeypatch, mode):
    project = world("S0b")
    if mode == "text":
        monkeypatch.setenv("YUNZHI_CARDS", "text")
    out, rc = card.prepare("report_type", MAIN, fields(REPORT_FIELDS))
    assert rc == 0, out
    res, _ = card.answer(MAIN, "report-type", reply(mode, "report-type", "你看着办"))
    assert res["result"] == "note" and res["selected"] is None and res["choice"] is None
    say = res["say"]
    assert "我建议选 1，下判断（研判型）。要按这个办，回 1 就行" in say
    assert "想选别的，回对应的数字：1 下判断（研判型） · 推荐；2 梳理情况（综述型）；3 先不定" in say
    assert "说完就结束这一轮" in res["next"] and "替用户选不行" in res["next"]
    assert not os.path.exists(os.path.join(project, "task_plan.md"))
    assert hook.scan_reply(say, USER_TEXT, "report_type") == []          # 这句话守门脚本扫回复也不打回


def test_decision_note_only_recommends_the_first_card_option_and_waits_for_the_number(world):
    project = world("S2")
    out, rc = card.prepare("decision", MAIN, fields(DECISION_FIELDS, recommend=2))
    assert rc == 0, out
    assert first_label(out) == "用全省数据补 2021 年 · 助手建议"          # 推荐的排第一,卡上是 1
    res, _ = card.answer(MAIN, out["card_id"], ans(out["card_id"], "这个缺口我也没办法，你看着办"))
    assert res["result"] == "note" and res["selected"] is None
    assert "你让我来定的话，我建议选 1，用全省数据补 2021 年。要按这个办，回 1 就行" in res["say"]
    assert hook.scan_reply(res["say"], USER_TEXT, "decision") == []
    # 用户自己回了 1:才算选了(fields 里的第 2 项)
    note, _ = card.note_user(MAIN, "1")
    assert note["looks_like_answer"]["card_id"] == out["card_id"]
    res, _ = card.answer(MAIN, out["card_id"], "1")
    assert res["result"] == "selected" and res["selected"] == 2


def test_note_only_say_without_a_recommendation_just_lists_the_numbers(world):
    world("S0b")
    out, rc = card.prepare("report_type", MAIN, {k: v for k, v in REPORT_FIELDS.items() if k != "recommend"})
    assert rc == 0, out
    res, _ = card.answer(MAIN, "report-type", ans("report-type", "你看着办"))
    assert "我建议" not in res["say"] and "直接回选项前面的数字就行：1 下判断（研判型）" in res["say"]


# ================================================================ 1b · 还有报告类型卡 / 决定卡在等用户定:不推进、不出确认类的卡

# 「只写了意见之后再答又只写了意见」走不到:再答的那一次,card.py 只收选了某一项的回答(见 LOGS 里直接构造的那一条)
WAYS = ["unanswered", "note", "skipped", "unclear"]
SAID_AS = {"unanswered": "unanswered", "note": "note", "skipped": "skipped", "unclear": "unclear"}


def leave(out, how):
    """让那张决定卡停在「还没选定」:没答 / 只写了意见 / 收起来 / 回答认不出(卡关了,事还没定)。"""
    cid = out["card_id"]
    if how == "note":
        card.answer(MAIN, cid, ans(cid, "你看着办"))
    elif how == "skipped":
        card.answer(MAIN, cid, SKIPPED)
    elif how == "unclear":
        card.answer(MAIN, cid, '{"foo":"bar"}')


def settle(out, how):
    """让那张决定卡定下来:没答的在卡上点第一项;只写了意见、收起来的,用户回 1;卡已经关了的,重新出卡再点第一项。"""
    if how == "unanswered":
        return card.answer(MAIN, out["card_id"], ans(out["card_id"], first_label(out)))[0]
    if how in ("note", "skipped"):
        return card.answer(MAIN, out["card_id"], "1")[0]
    again, rc = card.prepare("decision", MAIN, fields(DECISION_FIELDS))
    assert rc == 0, again
    return card.answer(MAIN, again["card_id"], ans(again["card_id"], first_label(again)))[0]


@pytest.mark.parametrize("how", WAYS)
def test_hook_blocks_advance_while_a_choice_card_waits(world, how):
    world("S2")
    out, rc = card.prepare("decision", MAIN, fields(DECISION_FIELDS))
    assert rc == 0, out
    leave(out, how)
    res = bash(ADVANCE)
    assert denied(res)
    said = reason(res)
    assert "还有一件事在等用户定" in said and out["card_id"] in said and sessions.WAITING_SAID[SAID_AS[how]] in said
    assert "说 card.py answer 输出的 say 里那句" in said and "停下等他回" in said and "重新跑 card.py prepare" in said
    # 拦下记了一笔
    assert any(r.get("event") == "denied" and r.get("tool") == "shell" for r in sessions.read(SID))
    # 用户定下来了 → 放行
    assert settle(out, how)["result"] == "selected"
    assert bash(ADVANCE) is None


def test_hook_blocks_advance_by_absolute_path_and_for_a_report_type_card(world):
    project = world("S0b")
    out, rc = card.prepare("report_type", MAIN, fields(REPORT_FIELDS))
    assert rc == 0, out
    cmd = 'py -X utf8 "%s" "%s" --advance gate1_awaiting' % (os.path.join(ROOT, "toolkit", "scripts", "stamp.py"),
                                                           os.path.join(project, "task_plan.md"))
    res = bash(cmd)
    assert denied(res) and "report-type" in reason(res) and sessions.WAITING_SAID["unanswered"] in reason(res)


def test_hook_advance_negative_controls(world, projects_root):
    world("S2")
    assert bash(ADVANCE) is None                                           # 没出过卡
    # 别的项目里有一张决定卡在等:不管这个项目,只管那个项目
    other = os.path.join(projects_root, "另一个项目")
    yzlib.append_jsonl(os.path.join(other, "records", "cards.jsonl"),
                       {"type": "prepared", "card_id": "decision-1", "kind": "decision", "title": "需要你决定 · 收集资料"})
    assert bash(ADVANCE) is None
    assert denied(bash(ADVANCE.replace(MAIN, "另一个项目")))
    # 最近出的是确认类的卡(只写了意见)—— 不是这一条管的
    out, rc = card.prepare("decision", MAIN, fields(DECISION_FIELDS))
    settle(out, "unanswered")
    assert bash(ADVANCE) is None
    # 推进到不该推进的状态:照旧是原来那条理由
    res = bash(ADVANCE.replace("gate2_awaiting", "collecting"))
    assert denied(res) and "进度只许你推进到" in reason(res)


@pytest.mark.parametrize("how", WAYS)
def test_confirm_cards_wait_until_the_choice_card_is_settled(world, how):
    world("S3")
    dec, rc = card.prepare("decision", MAIN, fields(DECISION_FIELDS))
    assert rc == 0, dec
    leave(dec, how)
    out, rc = card.prepare("dossier", MAIN, {"outside": []})
    assert rc == 1 and "还有一件事在等用户定" in out["refuse"] and dec["card_id"] in out["refuse"]
    assert settle(dec, how)["result"] == "selected"
    out, rc = card.prepare("dossier", MAIN, {"outside": []})
    assert rc == 0, out


def test_a_note_only_decision_still_does_not_count_as_the_gap_decision(world):
    """第三轮那条(决定卡上只写了意见,不算问过资料缺口怎么办)原来靠出资料汇编卡时的退回钉着;第九轮那张卡还在等用户定,
    先报的是「还有一件事在等用户定」—— 所以这里直接核缺口决定的判法。"""
    project = world("S3")
    log = os.path.join(project, "records", "cards.jsonl")
    write(log, "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in jsonl(log) if e.get("kind") != "decision"))
    plan_meta = pl.load_md(os.path.join(project, "task_plan.md"))[0]
    since = yzlib.approval_of(plan_meta).get("approved_at")
    base_ev = {"at": "2026-10-02T16:00:00-04:00", "card_id": "decision-9", "kind": "decision"}
    yzlib.append_jsonl(log, dict(base_ev, type="prepared", title="需要你决定 · 收集资料", options=[], data={}))
    yzlib.append_jsonl(log, dict(base_ev, type="answered", at="2026-10-02T16:01:00-04:00", choice=None, label=None,
                                 note="缺的再找找看", result="note"))
    assert yzlib.answered_decisions(project, since=since) == [] and card.gap_decision_valid(project, plan_meta) is False
    yzlib.append_jsonl(log, dict(base_ev, type="answered", at="2026-10-02T16:02:00-04:00", choice="1", label="先写已有的部分",
                                 note=None, result="selected"))
    assert len(yzlib.answered_decisions(project, since=since)) == 1 and card.gap_decision_valid(project, plan_meta) is True


def ev(t, cid="c1", kind="decision", **kw):
    e = {"type": t, "card_id": cid, "kind": kind}
    e.update(kw)
    return e


LOGS = [
    ("unanswered", [ev("prepared")]),
    ("note", [ev("prepared"), ev("answered", choice=None, result="note")]),
    ("note", [ev("prepared", kind="report_type"), ev("answered", kind="report_type", choice=None, result="note")]),
    ("skipped", [ev("prepared"), ev("skipped")]),
    ("unclear", [ev("prepared"), ev("unclear")]),
    ("note", [ev("prepared"), ev("answered", choice=None, result="note"), ev("answered", choice=None, result="note")]),
    ("note", [ev("prepared"), ev("skipped"), ev("answered", choice=None, result="note")]),
    (None, [ev("prepared"), ev("skipped"), ev("answered", choice="1", result="selected")]),
    (None, [ev("prepared"), ev("answered", choice=None, result="note"), ev("answered", choice="2", result="selected")]),
    (None, [ev("prepared"), ev("answered", choice="1", result="selected")]),
    (None, [ev("prepared"), ev("answered", choice=None, result="note"), ev("prepared", cid="d1", kind="dossier")]),
    (None, [ev("prepared", kind="task_plan"), ev("answered", kind="task_plan", choice=None, result="not_approved")]),
]


@pytest.mark.parametrize("want, log", LOGS)
def test_waiting_card_logic_is_one_and_matches_card_reopen(want, log):
    assert sessions.CARD_CLOSED == yzlib.CARD_CLOSED and sessions.NOTE_RESULTS == card.NOTE_RESULTS
    got, why = sessions.waiting_choice_card(log)
    assert why == want and (got is not None) == (want is not None)
    last = card.latest_prepared(log)
    closing = card.closing_events(log, last)
    # 还能再回答一次的(card.reopen_reason 认的那两种),一定也是「还没定」,而且两边说的是同一种
    reopen = card.reopen_reason(closing) if closing and last.get("kind") in sessions.CHOICE_KINDS else None
    if reopen is not None:
        assert reopen == why


# ================================================================ 1c · 说明写清:说完 say 就结束这一轮

def test_docs_say_end_the_turn_after_the_say():
    a = agents()
    assert "只写了意见、又没说要改什么时，说 `say` 就结束这一轮" in a and "替用户选不行" in a
    s = skill("yunzhi-card")
    assert "你不改材料、说 `say`，**说完就结束这一轮**" in s and "替用户选不行" in s


# ================================================================ 1d · 最后一条回复里不讲工具怎么运作

EVIDENCE = ("我建议采用第1种：用现有公开资料作限定研判。请直接回 1。"
            "确认卡没有将“你看着办”识别为选定方案，自动检查因此拦住了下一步；目前需要这个数字才能记录选择。")
TOOL_TALK = ["确认卡没有将“你看着办”识别为选定方案。", "自动检查因此拦住了下一步。", "目前需要这个数字才能记录选择。",
             "你的回答已写入确认记录。", "这是项目工具的要求。", "卡片没有把你的话认成选项。", "检查拦住了推进，所以先停在这里。"]
RESEARCH = ["海关的自动检查系统拦截了 3 批不合格货物。", "质量检查发现 3 个数字没有出处，我正在补。", "交付前的自动检查全部通过。",
            "这份调查识别出 3 类风险，识别为高风险地区的有 2 个。", "统计局记录了 2024 年的分县数据。",
            "你点「确认」后，由助手写下确认记录。", "我不会替你选，你回数字就行。",
            "这张确认卡上的三点我写得太长，压到字数以内再请你确认。", "地方财政记录选择性支出的口径不同。",
            "资料汇编第 1 版写好了，请你看一下。", "我认为选择从 2022 年开始算更稳妥。", "这项调查没有识别出选择性偏差。",
            "审计作为守门人拦住了违规资金流出。", "系统检查拦下了 3 份不合格的申报材料。", "经确认为有效样本的有 145 份。"]


def test_stop_sends_back_the_real_tool_talk_once(world):
    world("S2")
    card.prepare("decision", MAIN, fields(DECISION_FIELDS))
    res = stop(EVIDENCE)
    assert res and res["decision"] == "block"
    for word in ("识别为选定", "自动检查因此拦", "记录选择"):
        assert word in res["reason"]
    assert "请直接回 1" in res["reason"]                                  # 打回时把下一步那句交还给它
    assert stop(EVIDENCE, active=True) is None                            # 打回过一次就放行


@pytest.mark.parametrize("text", TOOL_TALK)
def test_stop_flags_each_tool_talk_phrase(projects_root, text):
    res = stop(text)
    assert res and res["decision"] == "block", text


@pytest.mark.parametrize("text", RESEARCH)
def test_stop_lets_research_wording_through(projects_root, text):
    assert stop(text) is None, text


def test_stop_lets_the_users_own_words_through(projects_root):
    hook.handle(base("UserPromptSubmit", prompt="自动检查拦住了也没关系，你接着写"))
    assert stop("你说「自动检查拦住了也没关系，你接着写」，我记下了。") is None


@pytest.mark.parametrize("how", WAYS)
def test_a_waiting_choice_card_is_not_called_a_confirm_card(world, how):
    world("S2")
    out, rc = card.prepare("decision", MAIN, fields(DECISION_FIELDS))
    leave(out, how)
    res = stop("这张确认卡上有三个做法，你回数字就行。")
    assert res and res["decision"] == "block" and "不是确认卡" in res["reason"]
    assert stop("这张卡上有三个做法，你回数字就行。") is None
    # 那张卡定下来以后,「这张确认卡」说的是别的卡(例如连着被退回的任务计划卡):照常
    settle(out, how)
    assert stop("这张确认卡上的三点我写得太长，压到字数以内再请你确认。") is None


def test_a_confirm_card_may_be_called_a_confirm_card(world):
    world("S1")
    out, rc = card.prepare("task_plan", MAIN, {"outside": []})
    assert rc == 0, out
    assert stop("这张确认卡上的三点我写得太长，压到字数以内再请你确认。") is None


def script_says():
    """脚本给用户的话(各种卡、各种回答):守门脚本扫回复时一句都不打回(负控)。"""
    report = card.options_from(card.REPORT_OPTIONS)
    report[0]["label"] += " · 推荐"
    decision = [{"key": "1", "label": "从 2022 年开始算 · 助手建议", "base": "从 2022 年开始算"},
                {"key": "2", "label": "用全省数据补 2021 年", "base": "用全省数据补 2021 年"}]
    out = []
    for kind in ("task_plan", "dossier", "outline", "delivery", "flow_change", "report_type", "decision"):
        opts = report if kind == "report_type" else (decision if kind == "decision" else
                                                     [{"key": "confirm", "label": "确认", "base": "确认"}])
        for rec in (None, "judge" if kind == "report_type" else 1):
            data = {"doc": card.DOC_OF.get(kind), "version": 2, "recommend": rec}
            out += [card.note_only_say(kind, data, opts), card.note_revise_say(kind, data)]
        out += [card.skipped_say(kind, m, opts) for m in ("native", "text")]
        if kind in card.CONFIRM_KINDS:
            out += [card.not_yet_say(kind, None), card.not_yet_say(kind, "时间段改成 2021 年开始")]
    out += [card.approved_say(k, 2, t, {}) for k, t in (("task_plan", None), ("dossier", "outlining"), ("dossier", "drafting"),
                                                         ("outline", "drafting"), ("delivery", "delivered"), ("flow_change", None))]
    out += [card.selected_say("report_type", report[0], None), card.selected_say("decision", decision[0], "照做")]
    out += [card.CHANGED_SAY, card.FLOW_REVERTED_SAY, card.ERROR_SAY, review.WAITING_SAY]
    return out


@pytest.mark.parametrize("kind", [None, "decision", "task_plan"])
def test_every_script_say_passes_the_stop_scan(kind):
    bad = [s for s in script_says() if hook.scan_reply(s, USER_TEXT, kind)]
    assert len(script_says()) >= 50 and bad == []


def test_skill_sentences_for_the_user_pass_the_stop_scan():
    """skills 里写给用户的每一句(「」里的、「这一步常说的几句」)都过得了守门脚本对回复的扫描:新加的「讲工具怎么运作」
    几条规则不误伤(没有卡在等用户定时 —— 那时才多一条「别把决定卡叫成确认卡」)。"""
    count, bad = 0, []
    for s in SKILLS:
        text = text_of(s)
        for span in body_spans(text) + prescribed_lines(text):
            count += 1
            if hook.scan_reply(span, USER_TEXT):
                bad.append("%s: 「%s」" % (s, span))
    assert count >= 100 and bad == []


# ================================================================ 2 · 反爬虫的验证页:记成打不开,不存原件

ARTICLE = ("<html><head><script>document.cookie='visited=1';setTimeout(function(){},0);</script></head><body><h1>发布会实录</h1>"
           "<p>" + "截至今年二季度末，全国个人养老金开户人数继续增加，缴存人数和缴存金额同步上升。" * 6 +
           "</p><p>Just a moment 是发言人引用的一句英文，不是验证页。</p></body></html>")
CLOUDFLARE = ("<!DOCTYPE html><html><head><title>Just a moment...</title></head><body><h1>Checking if the site connection is secure"
              "</h1><noscript>Enable JavaScript and cookies to continue</noscript>"
              "<script src='/cdn-cgi/challenge-platform/h/b/orchestrate/jsch/v1'></script></body></html>")


def challenge_bytes():
    with open(CHALLENGE, "rb") as f:
        return f.read()


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
        ctype, body = r
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def server(monkeypatch):
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    page = challenge_bytes()
    stripped = page.replace(b"EO_Bot_Ssid", b"QQ_Abc_Defg").replace(b"__tst_status", b"__abc_defgh")
    html = "text/html; charset=utf-8"
    _Handler.routes = {
        "/rsxw/202607/t20260722_580692.html": (html, page),
        "/rsxw/stripped.html": (html, stripped),
        "/cf.html": (html, CLOUDFLARE.encode("utf-8")),
        "/article.html": (html, ARTICLE.encode("utf-8")),
        "/notfound.html": (html, "<html><body><h1>页面不存在</h1></body></html>".encode("utf-8")),
    }
    srv = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def fetch_records(project):
    return jsonl(os.path.join(project, "records", "fetches.jsonl"))


def originals(project):
    d = os.path.join(project, "fetched", "originals")
    return os.listdir(d) if os.path.isdir(d) else []


def test_the_real_challenge_fixture_is_the_987_byte_page():
    data = challenge_bytes()
    assert len(data) == 987 and b"EO_Bot_Ssid" in data and fetches.visible_text(data.decode("utf-8")) == ""


@pytest.mark.parametrize("path, mark", [("/rsxw/202607/t20260722_580692.html", "EO_Bot_Ssid"),
                                        ("/rsxw/stripped.html", "写 cookie 再跳转"), ("/cf.html", "cdn-cgi/challenge")],
                         ids=["eo-bot-marker", "cookie-script-only", "cloudflare"])
def test_challenge_pages_are_recorded_blocked_and_not_saved(world, server, path, mark):
    project = world("S2")
    out = fetches.save_from_url(project, server + path, "人社部例行新闻发布会", timeout=5)
    assert out["ok"] and not out["downloaded"] and out["fetch_state"] == "blocked" and out["challenge"] is True
    assert mark in out["detail"] and "验证页" in out["detail"]
    assert "用网页工具打开这个网址读" in out["next"] and "blocked（打不开，不是查了没内容）" in out["next"]
    assert originals(project) == [] and jsonl(os.path.join(project, "records", "fetched.jsonl")) == []
    recs = fetch_records(project)
    assert len(recs) == 1 and recs[0]["state"] == "blocked" and recs[0]["url"] == server + path


@pytest.mark.parametrize("path", ["/article.html", "/notfound.html"])
def test_ordinary_pages_are_still_saved(world, server, path):
    project = world("S2")
    out = fetches.save_from_url(project, server + path, None, timeout=5)
    assert out["downloaded"] and out["saved"]["stored"].startswith("fetched/originals/") and fetch_records(project) == []


def test_a_challenge_page_handed_in_as_a_file_is_blocked_too(world, capsys):
    project = world("S2")
    url = "https://www.mohrss.gov.cn/SYrlzyhshbzb/dongtaixinwen/buneiyaowen/rsxw/202607/t20260722_580692.html"
    with pytest.raises(fetches.Fetched) as err:
        fetches.save(project, CHALLENGE, url)
    assert err.value.state == "blocked" and err.value.challenge
    code = fetches.main(["fetches.py", "save", MAIN, "--file", CHALLENGE, "--url", url, "--title", "例行新闻发布会"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["saved"] is False and out["fetch_state"] == "blocked" and out["challenge"] is True
    assert originals(project) == [] and fetch_records(project)[-1]["state"] == "blocked"
    assert os.path.isfile(CHALLENGE)                                    # 交来的文件没动


# ================================================================ 3 · 资料缺口卡上的来源重新打得开:先重读,再定缺口

def add_source(project, uid, url, state):
    path = os.path.join(project, "cards", "%s.md" % uid)
    meta, body = pl.read_md(path)
    meta["sources"] = list(meta.get("sources") or []) + [
        {"url": url, "title": "某省统计公报", "tier": "A", "locator": "正文", "excerpt": None, "as_of": "2026-10-04",
         "fetch_state": state, "inherited_from": None, "note": None}]
    pl.write_md(path, meta, body)


def refs(project):
    return [e for e in pl.load_references(os.path.join(project, "references.yaml")) if str(e.get("url", "")).startswith("http")]


@pytest.mark.parametrize("state", ["failed", "blocked"])
def test_liveness_lists_gap_card_sources_that_open_again(world, monkeypatch, state):
    project = world("S5")
    e = refs(project)[-1]
    add_source(project, "S17", e["url"], state)
    monkeypatch.setattr(liveness, "probe", lambda url, timeout: ("ok", None, 200))
    out = liveness.run(project, "CA-ON", 1)
    assert out["gap_sources_reachable"] == [{"id": e["id"], "url": e["url"], "card": "S17",
                                             "card_title": "资料缺口：2021 年分县充电桩数", "was": state}]
    assert out["next"].startswith("资料缺口卡上的来源现在打得开了") and "再定缺口" in out["next"]
    assert "资料缺口：2021 年分县充电桩数" in out["next"] and e["id"] in out["next"]


def test_gap_sources_that_were_empty_or_are_still_closed_are_not_listed(world, monkeypatch):
    project = world("S5")
    a, b = refs(project)[-1], refs(project)[-2]
    add_source(project, "S17", a["url"], "empty")                          # 当初就打得开:不是新情况
    add_source(project, "S18", b["url"], "failed")                         # 现在还是打不开
    monkeypatch.setattr(liveness, "probe", lambda url, timeout: ("failed", "4xx", 404) if url == b["url"] else ("ok", None, 200))
    out = liveness.run(project, "CA-ON", 1)
    assert "gap_sources_reachable" not in out and "资料缺口卡上的来源" not in out["next"]


# ================================================================ 4 · 反向检索不许拿已经找到的数字去搜

def set_query(project, query):
    path = os.path.join(project, "dossier.md")
    write(path, re.sub(r"query: '[^']*'", "query: '%s'" % query, read(path), count=1))


def test_counter_search_with_numbers_already_found_is_refused(world):
    project = world("S3")
    set_query(project, "山区县 公共桩 增速 超过 30%；新建快充站 日均充电不足 0.5 小时 17%")
    out, rc = card.prepare("dossier", MAIN, {"outside": []})
    said = "；".join(out.get("problems") or [])
    assert rc == 1 and "反向检索记录第 1 条的查询词里有已经找到的数字（17%、30%）" in said
    assert "不是拿已经找到的数字去搜" in said
    # 改成照实的反向检索(搜和判断相反的说法;带的数字不是找到的卡上的)→ 照常出卡
    set_query(project, "没参加活动的县 充电桩 增速 下降 50%；充电站 使用率 偏低")
    out, rc = card.prepare("dossier", MAIN, {"outside": []})
    assert rc == 0, out


def test_unit_numbers_fold_width_and_spaces():
    assert card.unit_numbers("同比增长 54.97 ％，新增 300 万户，超过１.５亿") == {"54.97%", "300万", "1.5亿"}
    assert card.unit_numbers("2026 年 第 3 季度 1280 个") == set()


# ================================================================ 5 · mark:存了原件的,摘录要在原文里找得到

EXCERPT = "（虚构原文）截至 2024 年底，全省县域公共充电桩 2.3 万台。"
NET = {"state": "failed", "detail": "net", "probed_at": "2026-10-05T15:00:00-04:00", "probed_from": "CA-ON"}


def first_cited(project):
    entries = pl.load_references(os.path.join(project, "references.yaml"))
    body = pl.read_md(os.path.join(project, "drafts", "%s.md" % MAIN))[1]
    return pl.number_entries(body, entries)[1][0][1]


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
            s["excerpt"] = EXCERPT
    pl.write_md(path, meta, body)


def save_text(project, url, text):
    os.makedirs(os.path.join(project, "fetched", "converted"), exist_ok=True)
    write(os.path.join(project, "fetched", "converted", "page.html.md"), text)
    yzlib.append_jsonl(os.path.join(project, "records", "fetched.jsonl"),
                       {"at": "2026-10-04T20:35:15-04:00", "url": url, "stored": "fetched/originals/page.html", "state": "ok",
                        "note": "已转成可读的文字", "outputs": ["fetched/converted/page.html.md"]})


@pytest.mark.parametrize("saved, ok", [
    ("前面的字。（虚构原文）截至 2024 年\n底，全省县域公共充电桩 2.3\n万台。后面的字。", True),     # 断行、空白不算
    ("（虚构原文）截至2024年底,全省县域公共充电桩2.3万台。", True),                          # 全角半角不算
    ("（虚构原文）截至 2024 年底，全省县域公共充电桩 2.1 万台。", False),                     # 数不一样:不是原文
    (None, True),                                                                       # 没存原件:只看有没有摘录
], ids=["line-breaks", "width", "not-in-original", "no-saved-original"])
def test_mark_checks_the_excerpt_against_the_saved_original(world, monkeypatch, saved, ok):
    project = world("S5")
    e = first_cited(project)
    set_liveness(project, e["id"], NET)
    add_excerpt(project, e["card"], e["url"])
    if saved is not None:
        save_text(project, e["url"], saved)
    if ok:
        assert liveness.mark(project, [e["id"]])["marked"] == [e["id"]]
        return
    with pytest.raises(UsageError) as err:
        liveness.mark(project, [e["id"]])
    assert "在存下来的原文" in str(err.value) and e["card"] in str(err.value) and e["id"] in str(err.value)
    # 探测完的提示也照这一条:不算读过
    monkeypatch.setattr(liveness, "probe", lambda url, timeout: ("failed", "net", None) if url == e["url"] else ("ok", None, 200))
    out = liveness.run(project, "CA-ON", 1)
    assert out["cited_unreached"][0]["read"] is False and "摘录要在原文里找得到" in out["next"]


# ================================================================ 6 · 网页版只数在用的卡 · 交付后清 _inbox/<项目名>/ · 等复核说一句

def test_web_version_counts_live_cards_only(world, tmp_path):
    project = world("S5")
    cards_dir = os.path.join(project, "cards")
    total = len([f for f in os.listdir(cards_dir) if f.endswith(".md")])
    path = os.path.join(cards_dir, "S17.md")
    meta, body = pl.read_md(path)
    meta["deprecated"] = True
    pl.write_md(path, meta, body)
    live = pl.count_live_cards(cards_dir)
    assert live == total - 1
    out = str(tmp_path / "web.html")
    rc, so, se = yzlib.run_toolkit("render_html.py", [os.path.join(project, "drafts", "%s.md" % MAIN),
                                                      os.path.join(project, "references.yaml"), out, "--title", MAIN,
                                                      "--cards", cards_dir])
    assert rc == 0, so + se
    cell = '<div class="k">资料卡片</div><div class="v">%d</div>'
    assert cell % live in read(out) and cell % total not in read(out)


def test_delivery_cleans_the_projects_inbox_folder(world, projects_root):
    world("S5")
    mine = os.path.join(projects_root, "_inbox", MAIN)
    os.makedirs(mine)
    write(os.path.join(mine, "fields.json"), "{}")                        # 没用上的临时文件
    other = os.path.join(projects_root, "_inbox", "另一个项目")
    os.makedirs(other)
    out, rc = card.prepare("delivery", MAIN, {})
    assert rc == 0, out
    res, _ = card.answer(MAIN, out["card_id"], ans(out["card_id"], "先不交付，回去改"))
    assert res["result"] == "not_approved" and os.path.isdir(mine)        # 反例:先不交付 → 不清
    out, rc = card.prepare("delivery", MAIN, {})
    res, _ = card.answer(MAIN, out["card_id"], ans(out["card_id"], "交付成稿"))
    assert res["result"] == "approved" and not os.path.exists(mine) and os.path.isdir(other)


def test_inbox_folder_with_other_things_in_it_is_left_alone(world, projects_root):
    project = world("S5")
    mine = os.path.join(projects_root, "_inbox", MAIN)
    os.makedirs(mine)
    write(os.path.join(mine, "笔记.md"), "x")
    assert yzlib.clean_inbox(project) is False and os.path.isfile(os.path.join(mine, "笔记.md"))
    os.remove(os.path.join(mine, "笔记.md"))
    assert yzlib.clean_inbox(project) is True and not os.path.exists(mine)


def test_waiting_for_the_reviewer_is_one_line(world):
    assert "等的时候在对话里说一句就够" in skill("cite-trace")
    project = world("S5")
    out = review.start(project, 3)
    assert out["say"] == review.WAITING_SAY and "等的时候在对话里说一句就够" in out["next"]


def test_evidence_skill_mentions_challenge_pages_and_counter_numbers():
    s = skill("evidence-card")
    assert "网站给的是反爬虫的验证页" in s and "不是拿已经找到的数字去搜" in s
    assert "gap_sources_reachable" in skill("cite-trace")
