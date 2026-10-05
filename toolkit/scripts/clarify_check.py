# -*- coding: utf-8 -*-
"""clarify_check — 明确任务时的工作笔记 clarify.yaml 写得对不对(规格 §⑨-4 · v1.2.1)。

用法:
  python -X utf8 clarify_check.py <project>/clarify.yaml [--json 输出.json]

格式对 → 退出码 0(打印面板上会显示的两格);不对 → 1(逐条说明);文件不在、用法错 → 2。
这份笔记只给进度面板「要聊清的三件事」用:研究范围 `scope`、成稿形式 `format`,各一句用户的话,
≤30 字,没答的不写。它不是正本 —— 没有版本、没有确认记录,确认流程不看它;应用读不出就当没有。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline_lib import CLARIFY_CAP, CLARIFY_KEYS, Report, clarify_items, clarify_problems, read_clarify

SAY = {"scope": "研究范围", "format": "成稿形式"}


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
    if len(args) != 1 or args[0].startswith("--"):
        print(__doc__)
        return 2
    path = args[0]
    if not os.path.isfile(path):
        print("⛔ %s 不存在:用户在对话里答了研究范围或成稿形式,就把答案写进这份笔记(规格 §⑨-4)" % path)
        return 2
    rep = Report("clarify_check")
    data, problem = read_clarify(path)
    if problem:
        rep.add("工作笔记", "FAIL", "读不出来 —— %s。应用会当它不存在" % problem,
                "写成两行:scope: <一句话> / format: <一句话>")
        rep.facts = {"scope": None, "format": None}
        return rep.finish(json_out)
    for p in clarify_problems(data):
        rep.add("工作笔记", "FAIL", p, "只写 %s,各一句用户的话,≤%d 字;没答的不写" % (" / ".join(CLARIFY_KEYS), CLARIFY_CAP))
    items = clarify_items(data)
    if not rep.fails():
        rep.add("工作笔记", "PASS", " · ".join("%s:%s" % (SAY[k], items[k] or "还没答") for k in CLARIFY_KEYS))
    rep.facts = items
    return rep.finish(json_out)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
