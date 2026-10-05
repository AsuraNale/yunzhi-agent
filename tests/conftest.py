# -*- coding: utf-8 -*-
"""测试共用:把客户端示例生成器造的示例项目(tests/fixtures/example,全是虚构内容)拷进临时的 projects/。

- 每个测试一个临时 projects 根(环境变量 YUNZHI_PROJECTS 指过去),不碰工作区的 projects/;
- 拷的时候去掉确认记录里的签名(agent 版写的确认记录本来就没有签名;签名不进内容 hash,去掉后确认照样有效);
- 客户端记的用户消息(app-data/records/<项目 id>/messages.jsonl)拷进项目的 records/messages.jsonl(同一个格式)。
"""
import io
import json
import os
import re
import shutil
import sys

import pytest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOLS = os.path.join(ROOT, "agent-tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

# 变异测试在工作区的拷贝里跑时,夹具只读地用原处的(YUNZHI_FIXTURES),不再拷一份
FIXTURES = os.environ.get("YUNZHI_FIXTURES") or os.path.join(HERE, "fixtures", "example")
MAIN = "新能源汽车下乡与县域充电设施"
STAGES = ("S0", "S0b", "S1", "S2", "S2-flow", "S3", "S4", "S5", "S6")
USER_TEXT = [
    "想弄清楚这几年的新能源汽车下乡活动，到底有没有带动县里的公共充电桩建设。要一份给厅里的内参，八千字左右，最后要有政策建议。",
    "领导就想知道这钱花得值不值。乡镇的算，村里的不算。Word 就行。",
]
SIG_RE = re.compile(r"^[ \t]+signature:[^\n]*\n", re.M)


def _manifest():
    with io.open(os.path.join(FIXTURES, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)


def strip_signatures(project):
    for dirpath, _dirs, files in os.walk(project):
        for fn in files:
            if not fn.endswith(".md"):
                continue
            p = os.path.join(dirpath, fn)
            with io.open(p, encoding="utf-8") as f:
                text = f.read()
            new = SIG_RE.sub("", text)
            if new != text:
                with io.open(p, "w", encoding="utf-8", newline="") as f:
                    f.write(new)


def copy_world(stage, dest_root, name=MAIN):
    src = os.path.join(FIXTURES, stage, "projects", name)
    dest = os.path.join(dest_root, name)
    shutil.copytree(src, dest)
    strip_signatures(dest)
    if stage == "S2-flow":
        # 规格 §①:改流程那条 revision_log 的 what 以「改流程：」开头(agent 重启时靠它认出改流程卡还在等);
        # 客户端生成器不写这个前缀,这里补上,模拟 agent 照 skill 写出来的样子(revision_log 不进内容 hash)
        plan = os.path.join(dest, "task_plan.md")
        with io.open(plan, encoding="utf-8") as f:
            text = f.read()
        text = text.replace("what: 去掉「拟定提纲」这一步", "what: 改流程：去掉「拟定提纲」这一步", 1)
        with io.open(plan, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    # 客户端的用户消息记录 → 项目的 records/messages.jsonl
    pid = next(p["id"] for p in _manifest()["projects"].values() if p["title"] == name)
    msgs = os.path.join(FIXTURES, stage, "app-data", "records", pid, "messages.jsonl")
    os.makedirs(os.path.join(dest, "records"), exist_ok=True)
    if os.path.isfile(msgs):
        shutil.copyfile(msgs, os.path.join(dest, "records", "messages.jsonl"))
    write_project_info(stage, dest, name)
    return finish_world(stage, dest, name, pid)


def write_project_info(stage, dest, name):
    """客户端登记表(app-data/projects.json)里这个项目的材料 → records/project.json(projects.py new 写的那份,同一个格式)。
    第五轮:出卡前要核 inputs/ 里只有用户给的材料,靠的就是这份登记。"""
    reg = os.path.join(FIXTURES, stage, "app-data", "projects.json")
    if not os.path.isfile(reg):
        return
    with io.open(reg, encoding="utf-8") as f:
        entry = next((p for p in json.load(f)["projects"] if p.get("title") == name), None)
    if entry is None:
        return
    keep = ("name", "stored", "state", "note", "outputs", "added_at")
    info = {"name": name, "title": name, "created_at": entry.get("created_at"), "request_file": "inputs/request.md",
            "materials": [{k: m.get(k) for k in keep} for m in entry.get("materials") or []]}
    write(os.path.join(dest, "records", "project.json"), json.dumps(info, ensure_ascii=False, indent=1) + "\n")


def finish_world(stage, dest, name, pid):
    if stage == "S3" and name == MAIN:
        upgrade_s3(dest, os.path.join(FIXTURES, stage, "app-data", "records", pid, "cards.jsonl"))
    if stage in ("S5", "S6") and name == MAIN:
        seal_like_review_py(dest)
    return dest


def seal_like_review_py(dest):
    """S5、S6 的独立复核是客户端那一套做的(toolkit review_result.py 直接封存),没有 agent 版 review.py 的开始记录、
    inputs 文件和封存记录。照 agent 版补上最后一个封存的轮次:模拟一个照说明做完、用 review.py seal 封存的复核助手
    (随机码抄对、文件清单一致、没见过暗号、逐段都核了、抽核了够数的资料卡片)。result.json 一个字节不动。"""
    import hashlib
    import review as review_mod
    rd = os.path.join(dest, "review")
    with io.open(os.path.join(rd, "result.json"), encoding="utf-8") as f:
        n = json.load(f)["round"]
    files = review_mod.expected_files(dest, n)
    paras = review_mod.paragraphs(review_mod.draft_for(dest))
    cards = review_mod.live_cards(dest)
    need = min(review_mod.MIN_SOURCE_CHECKS, len(cards))
    nonce = "f1x7ure%d" % n
    # 和 review.py start 一样:开始记录只存随机码的 sha256(随机码本身只在给复核助手的说明里)
    record = {"round": n, "nonce_sha256": hashlib.sha256(nonce.encode("ascii")).hexdigest(),
              "code_sha256": hashlib.sha256(b"YZC-00000000").hexdigest(), "files": files,
              "paragraphs": paras, "source_checks_min": need, "started_at": "2026-10-05T15:00:00-04:00"}
    inputs = {"round": n, "nonce": nonce, "files_read": files, "history": "没有", "seen_codes": [],
              "coverage": [{"para": p["id"], "claims": 1, "result": "核过，和资料卡片一致"} for p in paras],
              "source_checks": [{"card": c, "result": "相符", "note": "原文摘录、数字和方向都对得上"} for c in cards[:need]]}
    write(os.path.join(rd, "round-%d.json" % n), json.dumps(record, ensure_ascii=False, indent=1) + "\n")
    write(os.path.join(rd, "inputs-%d.json" % n), json.dumps(inputs, ensure_ascii=False, indent=1) + "\n")
    sealed = {"round": n, "sealed_at": "2026-10-05T15:20:00-04:00",
              "inputs_sha256": review_mod.sha256_file(os.path.join(rd, "inputs-%d.json" % n)),
              "result_sha256": review_mod.sha256_file(os.path.join(rd, "result.json"))}
    write(os.path.join(rd, "sealed-%d.json" % n), json.dumps(sealed, ensure_ascii=False, indent=1) + "\n")


STANCE_WORD = {"support": "支持", "counter": "不支持", "mixed": "背景"}


def upgrade_s3(dest, client_cards):
    """S3(资料汇编等第 2 次确认)照第三轮的规矩补齐(客户端的示例生成器还不写这些;只动 S3:别的阶段的确认记录、
    交付前检查的结果都依赖文件原样):
    - 每张不是资料缺口的卡写 direction(和核心判断的关系),每个取到了的来源写原文摘录 excerpt(虚构);
    - 资料汇编写找反面证据的记录 counter_search(资料汇编在 S3 还没确认,改它不碰任何确认记录);
    - 任务计划确认之后用户在决定卡上选过一次:客户端卡片记录里那次(增速从 2022 年开始算),照 agent 版的记录格式写进
      records/cards.jsonl。"""
    upgrade_cards(dest, with_direction=True)
    dossier = os.path.join(dest, "dossier.md")
    # 第六轮:每条反向检索写 found(找到、做成卡的卡号);示例项目里那 4 张不支持的卡就是这次找到的
    write(dossier, read(dossier).replace(
        "\ngaps:\n",
        "\ncounter_search:\n  - {query: '没参加活动的县 充电桩 增速；农村电网改造 充电桩', "
        "result: '找到电网改造覆盖乡镇翻番等 4 份，已做成不支持的卡', found: [%s]}\ngaps:\n" % ", ".join(counter_uids(dest)), 1))
    for line in jsonl(client_cards):
        if line.get("type") == "answered" and line.get("kind") == "decision":
            record_decision_answer(dest, line["card_id"], line.get("label"), line["at"], line.get("choice"))


def counter_uids(dest):
    """标成不支持(stance: counter)的资料卡片的卡号,按卡号排。"""
    cards = os.path.join(dest, "cards")
    return sorted(fn[:-3] for fn in os.listdir(cards)
                  if fn.endswith(".md") and re.search(r"^stance: counter$", read(os.path.join(cards, fn)), re.M))


def upgrade_cards(dest, with_direction):
    """每个取到了的来源写原文摘录 excerpt(虚构);研判型(with_direction)每张不是资料缺口的卡写 direction。"""
    cards = os.path.join(dest, "cards")
    for fn in sorted(os.listdir(cards)):
        p = os.path.join(cards, fn)
        text = read(p)
        stance = re.search(r"^stance: (\w+)$", text, re.M).group(1)
        if stance == "gap":
            continue
        title = re.search(r"^title: '([^']*)'$", text, re.M).group(1)
        fact = re.search(r"\{value: '([^']*)'", text)
        if with_direction:
            text = re.sub(r"^(stance: \w+\n)", lambda m: m.group(1) + "direction: '%s：%s'\n" % (STANCE_WORD[stance], title),
                          text, count=1, flags=re.M)
        excerpt = "（虚构原文）%s。" % (fact.group(1) if fact else title)
        text = re.sub(r"^(    )(fetch_state: ok)$", lambda m: "%sexcerpt: '%s'\n%s%s" % (m.group(1), excerpt, m.group(1), m.group(2)),
                      text, flags=re.M)
        write(p, text)


def record_decision_answer(dest, card_id, label, at, choice="1"):
    """照 agent 版的记录格式,在 records/cards.jsonl 里记一张决定卡和用户在卡上选的那一项。"""
    log = os.path.join(dest, "records", "cards.jsonl")
    prepared = {"type": "prepared", "at": at, "card_id": card_id, "kind": "decision", "mode": "native",
                "title": "需要你决定 · 收集资料", "question": "资料缺口怎么处理？",
                "options": [{"key": "1", "label": label, "base": label, "changes_plan": False}],
                "data": {"stage": "sources", "recommend": 1}, "fields": {}}
    answered = {"type": "answered", "at": at, "card_id": card_id, "kind": "decision", "raw": "{}", "recorded_by": "助手",
                "shape": "option", "choice": choice, "label": label, "note": None, "result": "selected"}
    os.makedirs(os.path.dirname(log), exist_ok=True)
    with io.open(log, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(prepared, ensure_ascii=False) + "\n" + json.dumps(answered, ensure_ascii=False) + "\n")


@pytest.fixture
def projects_root(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setenv("YUNZHI_PROJECTS", str(root))
    monkeypatch.delenv("YUNZHI_CARDS", raising=False)
    # 第六轮:在 Codex 的对话里跑测试时,这两个变量是那个对话的会话号;测试一律从「守门脚本没跑」开始,要用的测试自己设
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)
    return str(root)


@pytest.fixture
def world(projects_root):
    """world("S3") → 拷好的项目文件夹路径。"""
    def make(stage, name=MAIN):
        return copy_world(stage, projects_root, name)
    return make


def read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def write(path, text):
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def jsonl(path):
    if not os.path.isfile(path):
        return []
    with io.open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
