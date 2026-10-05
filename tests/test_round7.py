# -*- coding: utf-8 -*-
"""第七轮(10-04,开着 hooks 做的 4 回合命令行冒烟之后)的两处小修:每条一个反例 + 改对后的对照。

1. 只写意见的回答之后,用户再回数字要直接算数 —— 任何卡(确认类、报告类型、决定)都一样。条件同「跳过后回数字」:
   只此一次;这期间没出过别的卡;确认类的材料自出卡以来没变;守门脚本在跑时,这条回答要是用户在那之后说的原话。
   note-user 的 looks_like_answer 也覆盖这种情况。
   10-04 冒烟:任务计划卡收到「直接帮我确认吧」→ 记成意见、先不确认,卡就关了;用户下一条回「1」→ answer 退出码 1,
   助手只好把同一张卡重出一遍,用户要再回一次「1」。
2. 话术:「只写意见」之后的 say 末尾说怎么继续(回哪个数字);守门脚本打回重说时,请助手保留原来告诉用户的下一步。
   10-04 冒烟:打回之后重说的那版,把「怎么继续」丢了。
"""
import json
import os
import time

import pytest

import card
import hook
import sessions
import wording_check
import yzlib
from conftest import MAIN, ROOT, jsonl, read
from yzlib import pl

SID = "01a10898-1a0a-7152-bcbf-c0f95b8fead2"
NOTE = "直接帮我确认吧"
REPORT_FIELDS = {"report_name": "内参", "thesis": "下乡活动带动了县域公共充电设施增长",
                 "why": "你说「领导就想知道这钱花得值不值」，这需要一个判断。两种写法的区别在于要不要由这份报告来证明它。",
                 "recommend": "judge"}
DECISION_FIELDS = {"question": "2021 年的分县充电桩数据查不到。增速从哪年开始算？",
                   "why": "查过省统计局和能源局，都没有公布 2021 年的分县数据。不定下来，活动前后的对比就没法算。",
                   "options": [{"label": "从 2022 年开始算", "effect": "少一年，但全是分县数据，结论最站得住。", "changes_plan": False},
                               {"label": "用全省数据补 2021 年", "effect": "年份完整，但 2021 年那段只能说全省，不能说县。", "changes_plan": False}]}
FLOW_FIELDS = {"changes": "去掉拟定提纲这一步，写作按资料汇编的结构走。"}


def ans(card_id, *strings):
    return json.dumps({"answers": {card_id: {"answers": list(strings)}}}, ensure_ascii=False)


def approval(project, doc="task_plan.md"):
    return pl.load_md(os.path.join(project, doc))[0].get("approval") or {}


def log(project):
    return jsonl(os.path.join(project, "records", "cards.jsonl"))


def messages(project):
    return [m["text"] for m in jsonl(os.path.join(project, "records", "messages.jsonl"))]


def reply(mode, card_id, text):
    """用户写的一段话:原生卡上写在意见框里(选项框交回 JSON),文字卡模式下是对话里的一条消息。"""
    return ans(card_id, text) if mode == "native" else text


def set_mode(monkeypatch, mode):
    if mode == "text":
        monkeypatch.setenv("YUNZHI_CARDS", "text")


# ---- 守门脚本在跑(同第六轮的测试:输入形状取自 10-04 实测记录)

def base(event, **kw):
    d = {"session_id": SID, "turn_id": "01a10898-turn", "transcript_path": "C:\\Users\\x\\.codex\\sessions\\rollout.jsonl",
         "cwd": ROOT, "hook_event_name": event, "model": "gpt-6.1-sol", "permission_mode": "default"}
    d.update(kw)
    return d


def say_prompt(text):
    assert hook.handle(base("UserPromptSubmit", prompt=text)) is None
    time.sleep(0.02)


@pytest.fixture
def hooks_on(projects_root, monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", SID)
    say_prompt("开始吧")
    return SID


# ================================================================ 1 · 只写意见之后回数字直接算数

@pytest.mark.parametrize("mode", ["native", "text"])
def test_note_then_number_confirms_the_task_plan_once(world, monkeypatch, mode):
    project = world("S1")
    set_mode(monkeypatch, mode)
    out, rc = card.prepare("task_plan", MAIN, {"outside": []})
    assert rc == 0, out
    cid = out["card_id"]
    res, _ = card.answer(MAIN, cid, reply(mode, cid, NOTE))       # 只写了意见 = 先不确认,什么记录都不写
    assert res["result"] == "not_approved" and res["choice"] is None and res["note"] == NOTE
    assert approval(project).get("status") != "approved"
    note, _ = card.note_user(MAIN, "1")                              # 记用户消息时提示:像是在回答刚才那张卡
    assert note["looks_like_answer"]["card_id"] == cid and note["looks_like_answer"]["after"] == "note"
    assert "card.py\" answer" in note["next"] and "只写了意见" in note["next"]
    res, rc = card.answer(MAIN, cid, "1")                            # 不用重新出卡
    assert rc == 0 and res["result"] == "approved", res
    assert approval(project)["status"] == "approved"
    assert log(project)[-1].get("after_note") is True and log(project)[-1]["result"] == "approved"
    assert messages(project).count("1") == 1                        # note-user 记过的那一句不再记
    with pytest.raises(yzlib.UsageError):                            # 只此一次
        card.answer(MAIN, cid, "1")


def test_note_then_number_with_a_note_uses_it_as_the_quote(world, monkeypatch):
    project = world("S1")
    set_mode(monkeypatch, "text")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    card.answer(MAIN, out["card_id"], NOTE)
    res, _ = card.answer(MAIN, out["card_id"], "1，其余都可以")
    assert res["result"] == "approved" and res["note"] == "其余都可以"
    assert approval(project)["approval_quote"] == "其余都可以"


@pytest.mark.parametrize("mode", ["native", "text"])
def test_note_then_number_on_report_type(world, monkeypatch, mode):
    world("S0b")
    set_mode(monkeypatch, mode)
    out, rc = card.prepare("report_type", MAIN, dict(REPORT_FIELDS))
    assert rc == 0, out
    cid = out["card_id"]
    res, _ = card.answer(MAIN, cid, reply(mode, cid, "两种写法有什么区别？"))
    assert res["result"] == "note" and res["selected"] is None
    note, _ = card.note_user(MAIN, "2")
    assert note["looks_like_answer"]["card_id"] == cid
    res, rc = card.answer(MAIN, cid, "2")                            # 推荐的「下判断」排第一,2 = 梳理情况
    assert rc == 0 and res["result"] == "selected" and res["selected"] == "survey" and res["genre"] == "survey"
    with pytest.raises(yzlib.UsageError):
        card.answer(MAIN, cid, "1")


@pytest.mark.parametrize("mode", ["native", "text"])
def test_note_then_number_on_decision(world, monkeypatch, mode):
    project = world("S2")
    set_mode(monkeypatch, mode)
    out, rc = card.prepare("decision", MAIN, json.loads(json.dumps(DECISION_FIELDS)))
    assert rc == 0, out
    cid = out["card_id"]
    res, _ = card.answer(MAIN, cid, reply(mode, cid, "两种能不能都写进去？"))
    assert res["result"] == "note" and res["selected"] is None
    res, rc = card.answer(MAIN, cid, "1，另外把 2021 年的情况也提一句")
    assert rc == 0 and res["result"] == "selected" and res["selected"] == 1 and res["note"] == "另外把 2021 年的情况也提一句"
    assert log(project)[-1].get("after_note") is True


def test_native_handwritten_number_after_a_note(world):
    # 原生卡上手写的「2」也认(卡上的选项有编号);这里是只写了意见之后,用户在对话里回的「2」
    project = world("S1")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    card.answer(MAIN, out["card_id"], ans(out["card_id"], NOTE))
    res, _ = card.answer(MAIN, out["card_id"], "2")
    assert res["result"] == "not_approved" and res["choice"] == "not_yet"
    assert approval(project).get("status") != "approved"


def test_note_reopen_only_once(world):
    world("S1")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    cid = out["card_id"]
    card.answer(MAIN, cid, ans(cid, NOTE))
    res, _ = card.answer(MAIN, cid, "2，时间段再看看")              # 再答的那一次用掉了
    assert res["result"] == "not_approved"
    with pytest.raises(yzlib.UsageError):
        card.answer(MAIN, cid, "1")
    note, _ = card.note_user(MAIN, "1")
    assert "looks_like_answer" not in note


def test_explicit_not_yet_is_not_reopened(world):
    # 明明白白点了「先不确认」:不是「只写了意见」,卡关了(助手照意见改、出新版的卡)
    project = world("S1")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    cid = out["card_id"]
    res, _ = card.answer(MAIN, cid, ans(cid, "先不确认"))
    assert res["result"] == "not_approved" and res["choice"] == "not_yet"
    note, _ = card.note_user(MAIN, "1")
    assert "looks_like_answer" not in note
    with pytest.raises(yzlib.UsageError):
        card.answer(MAIN, cid, "1")
    assert approval(project).get("status") != "approved"


def test_unclear_is_not_reopened(world):
    world("S1")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    cid = out["card_id"]
    res, _ = card.answer(MAIN, cid, '{"answers":{"%s":{"answers":[]}}}' % cid)
    assert res["result"] == "unclear"
    with pytest.raises(yzlib.UsageError):
        card.answer(MAIN, cid, "1")


def test_after_note_a_newer_card_wins(world):
    project = world("S1")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    cid = out["card_id"]
    card.answer(MAIN, cid, ans(cid, NOTE))
    newer, rc = card.prepare("decision", MAIN, json.loads(json.dumps(DECISION_FIELDS)))
    assert rc == 0, newer
    note, _ = card.note_user(MAIN, "1")
    assert "looks_like_answer" not in note
    with pytest.raises(yzlib.UsageError):
        card.answer(MAIN, cid, "1")
    assert approval(project).get("status") != "approved"


def test_after_note_changed_material_is_not_signed(world):
    # 用户的意见让助手改了材料:材料校验照样拦,这张卡不签(助手照常出新版的卡)
    project = world("S1")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    cid = out["card_id"]
    card.answer(MAIN, cid, ans(cid, NOTE))
    path = os.path.join(project, "task_plan.md")
    text = read(path)
    assert "资料卡片不超过 30 张" in text
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text.replace("资料卡片不超过 30 张", "资料卡片不超过 40 张"))
    res, _ = card.answer(MAIN, cid, "1")
    assert res["result"] == "changed" and approval(project).get("status") != "approved"


def test_after_note_other_messages_are_just_chat(world):
    project = world("S1")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    cid = out["card_id"]
    card.answer(MAIN, cid, ans(cid, NOTE))
    said = "顺便问一下，这轮补贴是哪个部门牵头的？"
    note, _ = card.note_user(MAIN, said)
    assert "looks_like_answer" not in note
    with pytest.raises(yzlib.UsageError):
        card.answer(MAIN, cid, said)
    assert approval(project).get("status") != "approved"


def test_looks_like_yes_then_number_confirms_without_a_new_card(world):
    project = world("S1")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    cid = out["card_id"]
    res, _ = card.answer(MAIN, cid, ans(cid, "好的"))
    assert res["result"] == "not_approved" and res.get("looks_like_yes") is True
    assert "不用重新出卡" in res["next"] and "重新跑 prepare" not in res["next"]
    assert "直接回 1 就行" in res["say"] and "say_revise" not in res
    res, _ = card.answer(MAIN, cid, "1")
    assert res["result"] == "approved" and approval(project)["status"] == "approved"


def test_flow_change_note_waits_then_number_confirms(world):
    project = world("S2-flow")
    out, rc = card.prepare("flow_change", MAIN, dict(FLOW_FIELDS))
    assert rc == 0, out
    cid = out["card_id"]
    res, _ = card.answer(MAIN, cid, ans(cid, NOTE))
    assert res["result"] == "not_approved" and res["choice"] is None
    assert "先别还原" in res["next"]                                 # 意见没说清:不马上还原,等用户回 1 或 2
    assert "直接回 1 就行" in res["say"] and "就回 2" in res["say"]
    res, _ = card.answer(MAIN, cid, "1")
    assert res["result"] == "approved"
    assert yzlib.approved_valid(project, "task_plan.md")


def test_flow_change_reverted_after_note_says_so(world):
    project = world("S2-flow")
    out, _ = card.prepare("flow_change", MAIN, dict(FLOW_FIELDS))
    cid = out["card_id"]
    res, _ = card.answer(MAIN, cid, ans(cid, "还是要拟提纲"))
    assert res["say_revise"] == "好，还按原来的流程走。"
    # 助手照 ① 还原了改流程(删掉 stages、version 改回、revision_log 再加一条)
    path = os.path.join(project, "task_plan.md")
    meta, body = pl.read_md(path)
    meta.pop("stages", None)
    meta["version"] = 2
    meta["revision_log"] = (meta.get("revision_log") or []) + [{"v": 3, "at": yzlib.now_iso(), "who": "agent",
                                                                 "what": "改流程没确认，已还原", "why": "你说还是要拟提纲"}]
    pl.write_md(path, meta, body)
    assert yzlib.approved_valid(project, "task_plan.md")
    res, _ = card.answer(MAIN, cid, "1")                              # 用户后来又回了 1:这张卡不算数了
    assert res["result"] == "changed" and res["say"] == card.FLOW_REVERTED_SAY
    assert log(project)[-1]["type"] == "changed"


def test_answer_events_carry_a_fine_timestamp(world):
    # 再答的那一次只认用户在上一条记录之后说的话:上一条记录要有到毫秒的时刻(老记录只到秒)
    project = world("S1")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    card.answer(MAIN, out["card_id"], ans(out["card_id"], NOTE))
    assert isinstance(log(project)[-1]["ts"], float)


# ---- 守门脚本在跑时:再答的那一次也要是用户真说过的,而且是在只写意见那条记录之后说的

def test_reopen_after_note_needs_words_said_after_the_note(world, hooks_on, monkeypatch):
    project = world("S1")
    set_mode(monkeypatch, "text")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    cid = out["card_id"]
    say_prompt("1")                                                 # 用户先回了 1,紧接着又写了一句意见
    say_prompt("其实等一下，时间段要改")
    res, rc = card.answer(MAIN, cid, "其实等一下，时间段要改")
    assert rc == 0 and res["result"] == "not_approved" and res["choice"] is None
    time.sleep(0.02)
    res, rc = card.answer(MAIN, cid, "1")                            # 意见之前那个「1」不算这一次的回答
    assert rc == 1 and res["ok"] is False and "不要替他回答" in res["refuse"]
    assert approval(project).get("status") != "approved"
    say_prompt("1")                                                 # 用户在意见之后真的回了 1
    res, rc = card.answer(MAIN, cid, "1")
    assert rc == 0 and res["result"] == "approved"
    last = log(project)[-1]
    assert last["hook_check"] == "verified" and last.get("after_note") is True


def test_number_typed_while_the_card_was_open_still_answers_after_a_collapse(world, hooks_on):
    # 卡收起(跳过)什么都没说:卡还开着时用户在对话里回的「1」照样是这张卡的回答(收起之后那一次照旧从出卡算起)
    project = world("S1")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    cid = out["card_id"]
    assert hook.handle(base("PreToolUse", tool_name="request_user_input", tool_input=out["ask"], tool_use_id="c")) is None
    say_prompt("1")                                                 # 卡还开着,用户在对话里回了 1
    hook.handle(base("PostToolUse", tool_name="request_user_input", tool_input=out["ask"], tool_use_id="c",
                     tool_response='{"answers":{}}'))                # 然后卡被自动收起
    res, rc = card.answer(MAIN, cid, '{"answers":{}}')
    assert rc == 0 and res["result"] == "skipped"
    res, rc = card.answer(MAIN, cid, "1")
    assert rc == 0 and res["result"] == "approved", res
    assert log(project)[-1]["hook_check"] == "verified" and log(project)[-1].get("after_skip") is True


def test_native_note_then_chat_number_with_hooks(world, hooks_on):
    project = world("S1")
    out, _ = card.prepare("task_plan", MAIN, {"outside": []})
    cid = out["card_id"]
    assert hook.handle(base("PreToolUse", tool_name="request_user_input", tool_input=out["ask"], tool_use_id="c")) is None
    raw = ans(cid, NOTE)
    hook.handle(base("PostToolUse", tool_name="request_user_input", tool_input=out["ask"], tool_use_id="c", tool_response=raw))
    res, rc = card.answer(MAIN, cid, raw)
    assert rc == 0 and res["result"] == "not_approved"
    time.sleep(0.02)
    res, rc = card.answer(MAIN, cid, "1")                            # 用户还没回:不能替他回答
    assert rc == 1
    say_prompt("1")
    res, rc = card.answer(MAIN, cid, "1")
    assert rc == 0 and res["result"] == "approved" and log(project)[-1]["hook_check"] == "verified"


# ================================================================ 2 · 话术:说怎么继续;打回重说时保留下一步

NOTE_WORLDS = [("task_plan", "S1", {"outside": []}), ("dossier", "S3", {"outside": []}), ("outline", "S4", {}),
               ("delivery", "S5", {}), ("flow_change", "S2-flow", FLOW_FIELDS),
               ("report_type", "S0b", REPORT_FIELDS), ("decision", "S2", DECISION_FIELDS)]


@pytest.mark.parametrize("kind, stage, fields", NOTE_WORLDS)
def test_note_only_say_ends_with_how_to_continue(world, kind, stage, fields):
    project = world(stage)
    out, rc = card.prepare(kind, MAIN, json.loads(json.dumps(fields)))
    assert rc == 0, out
    res, _ = card.answer(MAIN, out["card_id"], ans(out["card_id"], "这一版我再想想"))
    assert res["result"] in ("not_approved", "note") and res.get("choice") is None
    say = res["say"]
    if kind in ("report_type", "decision"):
        first = out["ask"]["questions"][0]["options"][0]["label"]
        assert "直接回选项前面的数字就行" in say and ("1 %s" % first) in say
    else:
        assert "直接回 1 就行" in say
        if kind in ("task_plan", "dossier", "outline"):
            assert say.endswith("要按这一版定下来，直接回 1 就行；要改的话告诉我改哪里。")
    assert res.get("say_revise")
    users = yzlib.user_texts(project)
    for text in (say, res["say_revise"]):                            # 给用户的话过用词检查,守门脚本扫回复也不打回
        assert wording_check.scan(text, users) == [], text
        assert hook.scan_reply(text, users) == [], text
    assert "note-user" in res["next"] and "不用重新出卡" in res["next"]


def stop(msg, active=False):
    return hook.handle(base("Stop", last_assistant_message=msg, stop_hook_active=active))


def test_stop_reason_keeps_the_next_step(projects_root):
    msg = ("按[选项卡流程](C:/Users/x/云织Agent/.agents/skills/yunzhi-card/SKILL.md)，任务计划第 2 版先不定。"
           "要按这一版定下来，直接回 1 就行；要改的话告诉我改哪里。")
    got = stop(msg)
    assert got and got["decision"] == "block"
    assert "重说时保留原来要告诉用户的下一步（比如回哪个数字）" in got["reason"]
    assert "「要按这一版定下来，直接回 1 就行；要改的话告诉我改哪里。」" in got["reason"]
    assert sessions.read(SID)[-1]["keep"] == ["要按这一版定下来，直接回 1 就行；要改的话告诉我改哪里。"]


def test_stop_reason_does_not_hand_back_a_sentence_with_the_problem(projects_root):
    # 说下一步的那句本身就带内部东西:不原样交还(要重说的正是它),只留那句通用的提醒
    got = stop("按选项卡流程，想好了直接回 1 就行。")
    assert got and got["decision"] == "block"
    assert "重说时保留原来要告诉用户的下一步" in got["reason"] and "照原样留着" not in got["reason"]
    got = stop("资料汇编（dossier）第 1 版写好了。")                  # 没有说下一步的句子
    assert got and "照原样留着" not in got["reason"]


# ================================================================ 说明文字

def test_card_skill_and_agents_md_cover_the_note_reopen():
    skill = read(os.path.join(ROOT, ".agents", "skills", "yunzhi-card", "SKILL.md"))
    assert "**只写了意见之后**" in skill and "say_revise" in skill
    agents = read(os.path.join(ROOT, "AGENTS.md"))
    assert "卡上只写了意见、没选选项之后也一样" in agents
