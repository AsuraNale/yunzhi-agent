# -*- coding: utf-8 -*-
"""dossier_check — 汇编质量底线校验(规格 §③)。

用法:
  python -X utf8 dossier_check.py <dossier.md | 汇编.html> [--genre survey|argument]
      [--cards <cards目录|jsonl>] [--json 输出.json]

md 模式(正本):frontmatter claims/gate_verdict + 正文 H2 {#id} 六层 + 底线条数。
html 模式(渲染产物):锚点 + 8.1/8.2 条数 + 闸门 + 索引链接。
底线:claims ≥5 · boundaries ≥5 · 六层齐(content 按需)· index 带链接。
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline_lib import GENRES, Report, STRENGTHS, read_md

FLOOR_CLAIMS = 5
FLOOR_BOUNDARIES = 5


def check_html(path, rep):
    h = io.open(path, encoding="utf-8", errors="replace").read()
    ids = set(re.findall(r'id="([A-Za-z][\w\-]*)"', h))
    for need, layer in (("synthesis", "综合区"), ("references", "索引层")):
        if need not in ids:
            rep.add("六层齐", "FAIL", "缺锚点 #%s(%s)" % (need, layer),
                    "汇编必须有综合区与索引区,锚点可跳")
    i = h.find('id="synthesis"')
    j = h.find('id="references"')
    if i < 0 or j < 0:
        return
    syn = h[i:j]
    parts = re.split(r"<h3[^>]*>", syn)
    sections = {}
    for part in parts[1:]:
        title = re.sub(r"<[^>]+>", "", part[:80]).strip()
        li = len(re.findall(r"<li\b", part))
        sections[title[:24]] = (title, li, part)
    def find_sec(pat):
        for k, v in sections.items():
            if re.search(pat, v[0]):
                return v
        return None
    claims = find_sec(r"把握|结论")
    if not claims:
        rep.add("结论层", "FAIL", "综合区无「可以较有把握地说什么/结论」节", "补结论层")
    elif claims[1] < FLOOR_CLAIMS:
        rep.add("结论层", "FAIL", "候选命题 %d 条 < 底线 %d" % (claims[1], FLOOR_CLAIMS),
                "低于案例质量底线(dossier_oracle)")
    else:
        rep.add("结论层", "PASS", "%d 条候选命题" % claims[1])
    bounds = find_sec(r"不能过度推断|边界|红线")
    if not bounds:
        rep.add("边界层", "FAIL", "综合区无「不能过度推断/边界」节", "补边界层")
    elif bounds[1] < FLOOR_BOUNDARIES:
        rep.add("边界层", "FAIL", "边界条目 %d 条 < 底线 %d" % (bounds[1], FLOOR_BOUNDARIES),
                "低于案例质量底线")
    else:
        rep.add("边界层", "PASS", "%d 条边界" % bounds[1])
    gate = find_sec(r"闸门|判断")
    if gate:
        rep.add("闸门层", "PASS", "有闸门判断节")
    else:
        rep.add("闸门层", "WARN", "综合区无闸门节(survey 可无)", "argument 必须有")
    nxt = find_sec(r"先查|补证|下一步|gap")
    if nxt:
        rep.add("下一步层", "PASS", "有下一步/补证节")
    else:
        rep.add("下一步层", "FAIL", "综合区无下一步/补证节", "gaps 必须显式列出")
    refs = h[j:]
    n_links = len(re.findall(r'href="https?://', refs))
    if n_links == 0:
        rep.add("索引层", "FAIL", "索引区无外链", "索引 = 去重来源表,每条带链接")
    else:
        rep.add("索引层", "PASS", "索引区 %d 条外链" % n_links)


H2_ID_RE = re.compile(r"^##[^\n{]*\{#([\w\-]+)\}", re.M)
REQUIRED_LAYERS = ["header", "claims", "boundaries", "next", "index"]


def count_items(body, layer_id):
    m = re.search(r"##[^\n{]*\{#%s\}(.*?)(?=\n## |\Z)" % layer_id, body, re.S)
    if not m:
        return 0
    seg = m.group(1)
    bullets = len(re.findall(r"^\s*[-*]\s+\S|^\s*\d+\.\s+\S", seg, re.M))
    rows = max(0, len(re.findall(r"^\s*\|", seg, re.M)) - 2)
    return max(bullets, rows)


def check_md(path, rep, genre, cards_path=None):
    meta, body = read_md(path)
    genre = genre or meta.get("genre")
    ids = H2_ID_RE.findall(body)
    layers = REQUIRED_LAYERS + (["gate"] if genre == "argument" else [])
    for lid in layers:
        if lid not in ids:
            rep.add("六层齐", "FAIL", "缺 {#%s} 层" % lid, "H2 + {#id} 固定节,顺序见规格 §③")
    uid_universe = None
    if cards_path:
        from card_check import load_cards
        uid_universe = {c.get("uid") for c, _ in load_cards(cards_path)}
    claims = meta.get("claims") or []
    if len(claims) < FLOOR_CLAIMS:
        rep.add("结论层", "FAIL", "frontmatter claims %d 条 < 底线 %d" % (
            len(claims), FLOOR_CLAIMS), "低于案例质量底线(dossier_oracle)")
    for c in claims:
        if c.get("strength") not in STRENGTHS:
            rep.add("强度", "FAIL", "claim %s strength=%r 非法" % (
                c.get("id"), c.get("strength")),
                    "取 strong|medium_strong|medium|weak(聚合规则推导)")
        if not c.get("rule_trace") and not c.get("override_reason"):
            rep.add("强度", "FAIL", "claim %s 无 rule_trace 也无 override_reason" %
                    c.get("id"), "按规则推导一行,规则外判断写 override_reason")
        if uid_universe is not None:
            missing = [u for u in (c.get("evidence") or []) if u not in uid_universe]
            if missing:
                rep.add("卡引", "FAIL", "claim %s 引用不存在的卡 %s" % (
                    c.get("id"), missing), "先落卡再引")
    n_bounds = count_items(body, "boundaries")
    if n_bounds < FLOOR_BOUNDARIES:
        rep.add("边界层", "FAIL", "边界层 %d 条 < 底线 %d" % (n_bounds, FLOOR_BOUNDARIES),
                "口径红线候选 + 不能过度推断,低于案例质量底线")
    if genre == "argument":
        gv = meta.get("gate_verdict")
        if not gv or gv.get("outcome") not in ("A", "B", "C"):
            rep.add("闸门层", "FAIL", "gate_verdict=%r(argument 必填 A/B/C)" % gv,
                    "预设闸门宣判 + 反例纳入标准(3b)")
        elif not gv.get("counterexample_criteria"):
            rep.add("闸门层", "FAIL", "gate_verdict 缺 counterexample_criteria",
                    "反例纳入标准必须显式(3b)")
    if count_items(body, "index") == 0:
        rep.add("索引层", "FAIL", "索引层为空", "去重来源表,每条带链接")
    if not rep.fails():
        rep.add("底线", "PASS", "claims %d · boundaries %d · 层齐" % (
            len(claims), n_bounds))


def main(argv):
    HELP_FLAGS = ("-h", "--help")
    if any(f in argv for f in HELP_FLAGS):
        print(__doc__)
        return 0
    if len(argv) < 2:
        print(__doc__)
        return 2
    def pop_opt(name):
        if name in argv:
            i = argv.index(name)
            v = argv[i + 1]
            del argv[i:i + 2]
            return v
        return None
    json_out = pop_opt("--json")
    genre = pop_opt("--genre")
    cards_path = pop_opt("--cards")
    path = argv[1]
    rep = Report("dossier_check")
    if path.endswith(".html"):
        check_html(path, rep)
    else:
        if genre and genre not in GENRES:
            print("--genre 取 survey|argument")
            return 2
        check_md(path, rep, genre, cards_path)
    return rep.finish(json_out)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
