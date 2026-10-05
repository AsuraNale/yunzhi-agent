# -*- coding: utf-8 -*-
"""outline_check — 大纲校验器(规格 §④ 校验规则)。

用法:
  python -X utf8 outline_check.py <outline.md|outline.json> [--cards <cards目录|jsonl>]
      [--uids uid列表文件] [--genre survey|argument] [--json 输出.json]

- evidence 支持范围表达式(G1–G9 / S1-S3,en-dash 或连字符)→ 展开为具体 uid。
- uid 宇宙来源:--cards(卡库)或 --uids(一行一个 uid 的文本,golden 用)。
- 规则:节点 id 唯一 · evidence uid 均存在 · survey 节点 evidence 必非空 ·
  argument 节点空 evidence 须带 evidence_mode ∈ {methodology, pending_evidence} ·
  Σwords 与 total_words 偏差 > tolerance → WARN(不 FAIL)。
"""
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline_lib import GENRES, Report, outline_total_words, read_md
from card_check import load_cards

RANGE_RE = re.compile(r"^([A-Z]+)(\d+)[–\-—]([A-Z]+)?(\d+)$")
UID_RE = re.compile(r"^[A-Z]+\d+$")
EVIDENCE_MODES = {"methodology", "pending_evidence", "synthesis"}  # synthesis: v1.1 综合/收束节点


def expand_evidence(expr):
    """'G1–G9' → ['G1',...,'G9'];'C3' → ['C3']。返回 (uids, 错误串或None)。
    展开保持原 token 的位数(G01–G03 → G01..G03)。"""
    expr = expr.strip()
    m = RANGE_RE.match(expr)
    if m:
        fam1, a, fam2, b = m.group(1), m.group(2), m.group(3), m.group(4)
        if fam2 and fam2 != fam1:
            return [], "跨家族范围 %s" % expr
        lo, hi = int(a), int(b)
        if lo > hi:
            return [], "范围倒序 %s" % expr
        width = len(a)
        return ["%s%0*d" % (fam1, width, n) for n in range(lo, hi + 1)], None
    if UID_RE.match(expr):
        return [expr], None
    return [], "无法解析 %r" % expr


def parse_outline_json(path):
    data = json.load(io.open(path, encoding="utf-8"))
    meta = {k: v for k, v in data.items() if k != "nodes"}
    return meta, data.get("nodes", [])


NODE_HEAD_RE = re.compile(
    r"^(#{2,4})\s+(.*?)\s*\{\s*id:\s*([\w\-]+)\s*(?:,\s*words:\s*(\d+))?\s*\}\s*$")
FIELD_RE = re.compile(r"^-\s*(point|task|evidence|evidence_mode|revision):\s*(.*)$")


def parse_outline_md(path):
    meta, body = read_md(path)
    nodes = []
    cur = None
    for line in body.splitlines():
        m = NODE_HEAD_RE.match(line.strip())
        if m:
            cur = {"id": m.group(3), "title": m.group(2),
                   "words": int(m.group(4)) if m.group(4) else None,
                   "evidence": [], "level": len(m.group(1))}
            nodes.append(cur)
            continue
        if cur is None:
            continue
        f = FIELD_RE.match(line.strip())
        if f:
            key, val = f.group(1), f.group(2).strip()
            if key == "evidence":
                val = val.strip("[]")
                cur["evidence"] = [t for t in re.split(r"[,，]\s*", val) if t]
            elif val and val.lower() not in ("null", "~"):
                cur[key] = val
    return meta, nodes


def parse_words(w):
    if w is None:
        return None
    if isinstance(w, int):
        return w
    m = re.search(r"\d+", str(w))
    return int(m.group()) if m else None


def check_outline(meta, nodes, uid_universe, genre, rep):
    seen = {}
    total_declared = parse_words(meta.get("total_words"))
    tolerance = float(meta.get("tolerance") or 0.10)
    sigma = 0
    n_words = 0
    for n in nodes:
        nid = n.get("id")
        if not nid:
            rep.add("node_id", "FAIL", "节点缺 id(title=%r)" % (n.get("title"),),
                    "每个节点块必须带 {id: ...}")
            continue
        if nid in seen:
            rep.add("node_id", "FAIL", "节点 id 重复:%s" % nid, "id 唯一,永不复用")
        seen[nid] = True
        if parse_words(n.get("words")) is not None:
            n_words += 1
        ev_uids = []
        for expr in (n.get("evidence") or []):
            uids, err = expand_evidence(expr)
            if err:
                if re.match(r"^(原|合并|深化|拆分|新增)", expr):
                    hint = "识别为修订说明:按规格 §④ 应移入 revision 字段,evidence 只写卡 uid"
                elif "综合" in expr:
                    hint = ("识别为综合节点声明:改写为 evidence_mode: synthesis(v1.1 已开出口),"
                            "evidence 栏只放卡 uid")
                else:
                    hint = "evidence 写单个 uid 或同家族范围(如 G1–G3)"
                rep.add("evidence_parse", "FAIL", "节点 %s: %s" % (nid, err), hint)
                continue
            ev_uids.extend(uids)
        if uid_universe is not None:
            missing = [u for u in ev_uids if u not in uid_universe]
            if missing:
                rep.add("evidence_exists", "FAIL",
                        "节点 %s: 卡不存在 %s" % (nid, missing),
                        "先落卡再绑;或修正 uid 拼写")
        if not ev_uids:
            if genre == "survey":
                # v1.1:综合/收束节点声明 evidence_mode: synthesis 即合法出口
                if n.get("evidence_mode") == "synthesis":
                    pass
                else:
                    rep.add("evidence_empty", "FAIL",
                            "节点 %s: survey 节点 evidence 为空" % nid,
                            "综述型每节必须绑卡;综合/收束节点写 evidence_mode: synthesis(v1.1)")
            elif genre == "argument":
                mode = n.get("evidence_mode")
                if mode not in EVIDENCE_MODES:
                    rep.add("evidence_mode", "FAIL",
                            "节点 %s: 空 evidence 且 evidence_mode=%r" % (nid, mode),
                            "argument 空节点须声明 methodology|pending_evidence")
    # v1.2.1 复核(规格 §④):合计只加最上一级的节 —— 一节的 words 已经包含它下面的小节(D15:3.1 的 1200 字算在
    # 第三节的 2200 字里)。原来逐节相加,D16 那份提纲得 9200,报 15% 偏差。
    sigma = outline_total_words(nodes)
    rep.add("解析", "PASS", "%d 节点全部解析;%d 个带字数,Σ=%d" % (len(nodes), n_words, sigma))
    if total_declared:
        dev = abs(sigma - total_declared) / total_declared
        if dev > tolerance:
            rep.add("预算", "WARN",
                    "Σwords=%d 与 total_words=%d 偏差 %.0f%% > %.0f%%" % (
                        sigma, total_declared, dev * 100, tolerance * 100),
                    "只提醒不拦(4b);确认后调 total_words 或节点分配")
        else:
            rep.add("预算", "PASS", "Σwords=%d / total=%d 偏差 %.0f%% 在容差内" % (
                sigma, total_declared, dev * 100))


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
    cards_path = pop_opt("--cards")
    uids_path = pop_opt("--uids")
    genre = pop_opt("--genre")
    path = argv[1]
    meta, nodes = (parse_outline_json(path) if path.endswith(".json")
                   else parse_outline_md(path))
    genre = genre or meta.get("genre")
    if genre not in GENRES:
        print("必须指定文体:--genre survey|argument(或正本 frontmatter genre)")
        return 2
    uid_universe = None
    if cards_path:
        uid_universe = {c.get("uid") for c, _ in load_cards(cards_path)}
    elif uids_path:
        uid_universe = {l.strip() for l in io.open(uids_path, encoding="utf-8")
                        if l.strip()}
    rep = Report("outline_check")
    check_outline(meta, nodes, uid_universe, genre, rep)
    return rep.finish(json_out)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
