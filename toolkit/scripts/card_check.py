# -*- coding: utf-8 -*-
"""card_check — 证据卡校验器(规格 §② 校验规则)。

用法:
  python -X utf8 card_check.py <cards目录 | cards.jsonl> [--json 输出.json]

输入两种形态:
  - 目录:cards/<uid>.md(正本形态,frontmatter 按 §②)
  - .jsonl:一行一卡(golden 形态,键名同 frontmatter)
"""
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline_lib import (FIVE_STATES, STANCES, TIERS, Report, norm_tier,
                          read_md)


# 「这轮沿用了既有取证」的措辞线索 —— 见 check_cards 里「继承未标注」一节的说明
INHERIT_HINT_RE = re.compile(
    r"(继承|沿用|延用|上一轮|上轮|前一轮"
    r"|与\s*\d{1,2}-\d{1,2}\s*[^;；,，。]{0,14}一致"
    r"|同\s*[A-Z]\d{2,3})")


def load_cards(path):
    """→ [(卡 dict, 来源标签)];md 卡把正文 {#limit} 节并入 limit 字段(若 frontmatter 无)。"""
    cards = []
    if os.path.isdir(path):
        for fn in sorted(os.listdir(path)):
            if not fn.endswith(".md"):
                continue
            meta, body = read_md(os.path.join(path, fn))
            import re
            for field in ("limit", "support"):
                if field not in meta:
                    m = re.search(r"##[^\n{]*\{#%s\}\s*\n(.*?)(?=\n##|\Z)" % field,
                                  body, re.S)
                    if m:
                        meta[field] = m.group(1).strip()
            cards.append((meta, fn))
    else:
        with io.open(path, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if line:
                    cards.append((json.loads(line), "line %d" % i))
    return cards


def check_cards(cards, rep):
    # ⚠️ 物化:本函数遍历两次(主检查 + 继承未标注)。若 cards 是 generator,
    #    第二次会静默空转 → 检查假通过。这里显式 list() 掉。
    cards = list(cards)
    seen_uid = {}
    for card, where in cards:
        uid = card.get("uid")
        tag = uid or where
        # uid 存在且唯一
        if not uid:
            rep.add("uid", "FAIL", "%s: 缺 uid" % where, "补稳定 uid,永不复用")
            continue
        if uid in seen_uid:
            rep.add("uid", "FAIL", "%s 与 %s 重复 uid=%s" % (where, seen_uid[uid], uid),
                    "uid 项目内唯一;弃用卡标 deprecated 而非复用")
        seen_uid[uid] = where
        # stance 封闭四值
        stance = card.get("stance")
        if stance not in STANCES:
            rep.add("stance", "FAIL", "%s: stance=%r 不在 %s" % (tag, stance, sorted(STANCES)),
                    "补 stance(support|counter|mixed|gap);历史卡也要补判")
        # limit 必填非空
        if not (card.get("limit") or "").strip():
            rep.add("limit", "FAIL", "%s: 局限为空" % tag,
                    "必填;确无则写「无重大局限(理由)」")
        # sources
        sources = card.get("sources") or []
        for si, s in enumerate(sources):
            t = norm_tier(s.get("tier"))
            if t not in TIERS:
                rep.add("tier", "FAIL",
                        "%s: sources[%d](%s) tier=%r 不在 A|A-|B" % (
                            tag, si, (s.get("title") or s.get("url") or "?")[:30], s.get("tier")),
                        "tier 封闭三级;细微差别(A/B+ 之类)写进 note")
            fs = s.get("fetch_state")
            if fs is not None and fs not in FIVE_STATES:
                rep.add("fetch_state", "FAIL",
                        "%s: sources[%d] fetch_state=%r 不在五态" % (tag, si, fs),
                        "取 ok|empty|gap|failed|blocked")
        # 非 gap 卡至少一条来源
        if stance != "gap" and not sources:
            rep.add("sources", "FAIL", "%s: 非 gap 卡无来源" % tag,
                    "补来源;确属缺口卡则 stance 改 gap")
        # cited:true 的 fact 至少一条来源带 locator
        cited = [f for f in (card.get("facts") or [])
                 if isinstance(f, dict) and f.get("cited")]
        n_loc = sum(1 for s in sources if (s.get("locator") or "").strip())
        if cited and not n_loc:
            rep.add("locator", "FAIL",
                    "%s: 有 %d 条 cited 数字但无任何来源 locator" % (tag, len(cited)),
                    "被直接引用的数字必须能定位(页码/表名,粒度可粗)")
        elif n_loc and len(cited) > 2 * n_loc:
            # v1.0.2(实证):原规则「至少一条来源带 locator」太松 ——
            # 实测里 1 条 locator 把 3 条 cited 事实全盖过去,其中一条实际无源。
            # 数量启发式只能当软提示(同一张表本来就能出多个数),故门槛设 >2×、只 WARN。
            # 真正的解在 v1.1:facts 增 source_ref 逐条绑来源。
            rep.add("locator覆盖", "WARN",
                    "%s: %d 条 cited 数字 vs %d 条带 locator 的来源(>2倍)——覆盖可能不足"
                    % (tag, len(cited), n_loc),
                    "软提示:逐条核对每个 cited 数字都有来源支撑;同一来源支撑多数可忽略")
        # ⭐ 锋利判据:cited 事实的 caliber 把「卡号/前两轮档案」当出处 = D8 违规
        #   (卡号不作终点引用)。实测中「(前两轮档案S12录)」正是此形态。
        for f in cited:
            cal = str(f.get("caliber") or "")
            m2 = re.search(r"(前两轮|档案|见卡|录自|转自)|([A-Z]{1,3}\d{2})", cal)
            if m2:
                rep.add("D8卡号终点引用", "WARN",
                        "%s: cited 数字的口径以「%s」为出处 —— 卡号/档案不是来源"
                        % (tag, m2.group(0)),
                        "回源核验原始出处并补进 sources + 登记表;核不到则改 cited: false")
    # 继承未标注(WARN · v1.0.3 2026-08-30)
    #   五态 ok|empty|gap|failed|blocked **没有「继承」这一态** —— 于是「上一轮抓过、
    #   这轮沿用」只能标成 ok,和「这轮真的抓了」在数据上完全同形。
    #   实测形态:note 写「页码索引与上一轮尽调 C001 一致」、fetch_state: ok,
    #   而整轮任务(父会话 + 5 个子代理)从未有任何一次工具调用打开过那份源 PDF。
    #   ⚠️ 这是**启发式**:靠 note/caliber 里的措辞发现,会漏(没写就抓不到);
    #   它只保证「明说了继承的」不会被当成本轮实取。真要治本得在采集侧记录取证动作。
    for card, where in cards:
        uid = card.get("uid") or where
        if card.get("inherited_from"):
            continue
        blob = json.dumps({k: v for k, v in card.items() if k != "sources"},
                          ensure_ascii=False, default=str)
        for so in (card.get("sources") or []):
            blob += json.dumps({k: so.get(k) for k in ("note", "locator", "title")},
                               ensure_ascii=False, default=str)
        m = INHERIT_HINT_RE.search(blob)
        if m:
            rep.add("继承未标注", "WARN",
                    "%s: 措辞提示这是沿用既有取证(%r),但没有 inherited_from 字段" % (
                        uid, m.group(0)[:40]),
                    "补 inherited_from: <来源轮次/原卡号>;"
                    "或本轮真的回源取证一次再标 fetch_state: ok")

    rep.add("总量", "PASS", "共 %d 张卡,uid 唯一 %d 个" % (len(cards), len(seen_uid)))


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
        argv = argv[:i] + argv[i + 2:]
    rep = Report("card_check")
    cards = load_cards(argv[1])
    check_cards(cards, rep)
    return rep.finish(json_out)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
