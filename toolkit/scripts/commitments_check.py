# -*- coding: utf-8 -*-
"""commitments_check — 三点齐不齐(规格 §⑨-1「齐全」· v1.2.0)。弹确认卡、交付卡之前跑。

用法:
  python -X utf8 commitments_check.py <正本.md> [--json 输出.json]
      task_plan / dossier / outline 正文最后那一节 `{#commitments}`。
  python -X utf8 commitments_check.py <library/delivery_commitments.md> --delivery [--json 输出.json]
      交付的三点(整个文件就是三点,最上面可以有一行一级标题;规格 §⑨-3)。

齐全 → 退出码 0;不齐全 → 1(逐条说明);文件不在、frontmatter 读不出来、用法错 → 2。
研判型资料汇编另核 `gate_verdict.reason`(上卡的初步结论理由,≤60 字;规格 §③「上卡的字段」);
任务计划另核 `scope_brief`(上进度面板的研究范围,≤30 字)与 `report_name`(可缺,写了就 ≤6 字,上首页项目卡;规格 §⑨-4),
以及「预注册结论空间」表第三栏「对应表述」(只能写 成立 / 部分成立 / 不成立,资料汇编卡上的初步结论照它显示;规格 §①、§③)。
应用弹卡前按同一条判据自己再核一遍,不齐全就不弹、退回 —— 这里让你在弹卡之前先知道。
用的是旧标签(口径承诺 / 最脆证据 / 最可能错在哪 …)只报提醒,照样算齐全;新写的请用
「改动内容 / 最薄弱的依据 / 最可能出错的地方」。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline_lib import (COMMITMENT_CAPS, COMMITMENT_LABELS, GATE_REASON_CAP, PREREG_PHRASES, REPORT_NAME_CAP,
                          SCOPE_BRIEF_CAP, Report, _front, commitment_problems, gate_reason_problems,
                          load_md, preregistration_problems, report_name_problems, scope_brief_problems)


def main(argv):
    if any(flag in argv for flag in ("-h", "--help")):
        print(__doc__)
        return 0
    args = list(argv[1:])
    json_out = None
    if "--json" in args:
        i = args.index("--json")
        if i + 1 >= len(args):
            print(__doc__)
            return 2
        json_out = args[i + 1]
        del args[i:i + 2]
    whole = "--delivery" in args
    args = [a for a in args if a != "--delivery"]
    if len(args) != 1 or args[0].startswith("--"):
        print(__doc__)
        return 2
    path = args[0]
    if not os.path.isfile(path):
        print("⛔ %s 不存在%s" % (path, ":交付的三点要在弹交付卡之前写好(规格 §⑨-3)" if whole else ""))
        return 2
    meta, body, problem = load_md(path)
    if problem:
        print("⛔ %s 的 frontmatter 读不出来 —— %s。应用也读不出这份材料,先改好再查三点" % (path, problem))
        return 2
    text, m = _front(path)
    offset = text.count(chr(10), 0, m.end()) if m else 0
    problems, notes, items = commitment_problems(body, whole=whole, line_offset=offset)
    rep = Report("commitments_check")
    for p in problems:
        rep.add("三点", "FAIL", p, "规格 §⑨-1:恰好三条,一条一行,`1. **%s**:…` 依次往下,字数 %s"
                % (COMMITMENT_LABELS[0], " / ".join("%s ≤%d" % pair for pair in zip(COMMITMENT_LABELS, COMMITMENT_CAPS))))
    for n in notes:
        rep.add("三点", "WARN", n, "新写的正本一律用新标签")
    # 研判型资料汇编:卡上「初步结论」的那句理由也是你写的空(规格 §③「上卡的字段」)
    for p in ([] if whole else gate_reason_problems(meta)):
        rep.add("初步结论的理由", "FAIL", p, "写成一句研究员的话,≤%d 字,不放卡号" % GATE_REASON_CAP)
    # 任务计划:进度面板「要聊清的三件事」里研究范围那一格(规格 §⑨-4)
    for p in ([] if whole else scope_brief_problems(meta)):
        rep.add("研究范围摘要", "FAIL", p, "frontmatter 写 scope_brief: <一句用户的话,≤%d 字>" % SCOPE_BRIEF_CAP)
    for p in ([] if whole else report_name_problems(meta)):
        rep.add("成稿叫法", "FAIL", p, "report_name 写用户的说法,≤%d 字,与报告类型卡上的一字不差;没有就不写" % REPORT_NAME_CAP)
    is_plan = not whole and isinstance(meta, dict) and meta.get("kind") == "task_plan"
    for p in (preregistration_problems(body) if is_plan else []):
        rep.add("对应表述", "FAIL", p, "表三栏 outcome · 判定条件 · 对应表述,第三栏只写 %s" % " / ".join(PREREG_PHRASES))
    if not problems:
        rep.add("三点", "PASS", "齐全:" + " · ".join(
            "%s %d 字(≤%d)" % (COMMITMENT_LABELS[it["position"] - 1], it["length"], COMMITMENT_CAPS[it["position"] - 1])
            for it in items))
    rep.facts = {"file": os.path.basename(path), "delivery": whole,
                 "items": [{"position": it.get("position"), "line": it["line"], "form": it["form"],
                            "length": it["length"]} for it in items]}
    return rep.finish(json_out)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
