# -*- coding: utf-8 -*-
"""refs_add — 从证据卡 sources 派生/增量维护 references.yaml(规格 §⑤)。

用法:
  python -X utf8 refs_add.py <cards目录|cards.jsonl> <references.yaml输出> [--merge 既有.yaml]

规则:
  - 按 URL 去重(归一:去尾斜杠、小写 host);同 URL 多卡 → card 列表合并。
  - id 分配:R001 起;--merge 时沿用既有 id(URL 匹配),新条目接续最大号;id 永不复用。
  - tier 取卡内该来源 tier(归一 A−→A-);冲突时取最严(B > A- > A 宽松度)并记 note。
  - locator / as_of / fetch_state 原样带过来(单点记录在卡,此处复制)。
  - 输出确定性排序:按首次出现的卡顺序。
"""
import io
import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline_lib import load_references, norm_tier
from card_check import load_cards


def norm_url(u):
    return (u or "").strip().rstrip("/")


def norm_url_key(u):
    from urllib.parse import urlsplit
    u = norm_url(u)
    try:
        p = urlsplit(u)
        return (p.scheme.lower() + "://" + p.netloc.lower() + p.path +
                (("?" + p.query) if p.query else ""))
    except ValueError:
        return u.lower()


TIER_LOOSENESS = {"A": 0, "A-": 1, "B": 2}
# 云织 Agent 分支(10-03 第四轮):模块说明一直写着 fetch_state 原样带过来,代码里却没带 —— 参考文献清单因此分不出
# 哪些来源从没读到过,成稿里照样写「查阅于…」。现在带上:同一网址在几张卡上,有一张读到了(ok)就是 ok;
# 都没读到,按 打不开 > 出错 > 查了没内容 > 没有这项数据 取最重的那个。
FETCH_RANK = {"blocked": 4, "failed": 3, "empty": 2, "gap": 1}


def merged_fetch_state(old, new):
    if old == "ok" or new == "ok":
        return "ok"
    if new not in FETCH_RANK:
        return old
    if old not in FETCH_RANK:
        return new
    return new if FETCH_RANK[new] > FETCH_RANK[old] else old


def derive(cards, existing=None):
    """cards → entries 列表。existing = 既有登记表(沿用 id)。"""
    by_key = {}
    order = []
    for card, _ in cards:
        uid = card.get("uid")
        for s in (card.get("sources") or []):
            url = s.get("url")
            if not url:
                continue
            k = norm_url_key(url)
            if k not in by_key:
                by_key[k] = {
                    "id": None, "title": s.get("title"), "url": norm_url(url),
                    "tier": norm_tier(s.get("tier")),
                    "as_of": s.get("as_of"), "locator": s.get("locator"),
                    "fetch_state": merged_fetch_state(None, s.get("fetch_state")),
                    "card": uid, "cards": [uid],
                    "claims_source": s.get("claims_source"),
                    "unverified": bool(s.get("unverified", False)),
                    "liveness": s.get("liveness"),
                    "note": s.get("note"),
                }
                order.append(k)
            else:
                e = by_key[k]
                if uid not in e["cards"]:
                    e["cards"].append(uid)
                e["fetch_state"] = merged_fetch_state(e.get("fetch_state"), s.get("fetch_state"))
                t = norm_tier(s.get("tier"))
                if t in TIER_LOOSENESS and (
                        e["tier"] not in TIER_LOOSENESS
                        or TIER_LOOSENESS[t] > TIER_LOOSENESS[e["tier"]]):
                    e["note"] = ((e["note"] or "") +
                                 " tier 冲突:%s(%s) vs %s" % (e["tier"], e["card"], t)).strip()
                    e["tier"] = t
                if not e.get("locator") and s.get("locator"):
                    e["locator"] = s.get("locator")
    # 合并既有登记表(v1.0.1,复核 F4):
    # ① 沿用 id;② 保留人工维护字段(liveness/claims_source/unverified/note),
    #    卡侧没有这些信息时不得清空;③ 失去卡源的条目保留并标 deprecated,
    #    其 id 永久占位 —— id 永不复用(规格 §0)。
    used = set()
    MANUAL_FIELDS = ("liveness", "claims_source", "note")
    if existing:
        for old_e in existing:
            rid = old_e.get("id")
            if rid:
                used.add(rid)          # 所有旧 id 占位,含已消失 URL 的
            k = norm_url_key(old_e.get("url"))
            if k in by_key:
                e = by_key[k]
                e["id"] = rid or e["id"]
                for f in MANUAL_FIELDS:
                    if not e.get(f) and old_e.get(f):
                        e[f] = old_e[f]
                if old_e.get("unverified") and not e.get("unverified"):
                    e["unverified"] = True
                if not e.get("locator") and old_e.get("locator"):
                    e["locator"] = old_e.get("locator")
            else:
                dead = dict(old_e)
                dead["deprecated"] = True
                by_key[k] = dead
                order.append(k)
    nxt = 1
    for k in order:
        e = by_key[k]
        if e.get("id") is None:
            while "R%03d" % nxt in used:
                nxt += 1
            e["id"] = "R%03d" % nxt
            used.add(e["id"])
    return [by_key[k] for k in order]


def main(argv):
    HELP_FLAGS = ("-h", "--help")
    if any(f in argv for f in HELP_FLAGS):
        print(__doc__)
        return 0
    if len(argv) < 3:
        print(__doc__)
        return 2
    existing = None
    if "--merge" in argv:
        i = argv.index("--merge")
        merge_path = argv[i + 1]
        # 首跑时登记表尚不存在:按空表处理,不报错(复核指出)
        existing = load_references(merge_path) if os.path.exists(merge_path) else []
        argv = argv[:i] + argv[i + 2:]
    cards = load_cards(argv[1])
    entries = derive(cards, existing)
    with io.open(argv[2], "w", encoding="utf-8") as f:
        yaml.safe_dump({"kind": "references", "entries": entries}, f,
                       allow_unicode=True, sort_keys=False, width=200)
    print("派生 %d 条(来自 %d 张卡)→ %s" % (len(entries), len(cards), argv[2]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
