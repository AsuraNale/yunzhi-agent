# -*- coding: utf-8 -*-
"""出口契约:用**面板那把尺**再量一遍(v1.1.0 · 独立核 2026-09-11 的 T-3)。

`test_skill_contract.py` 判「可解析」的尺与面板不同:面板(ui-ann-progress 的
exits.ts)取**第一个**以 `## 出口契约` 开头的行作起点,测试只数严格形的主标题。
于是在主标题前插一个 `## 出口契约附注(前置)`,那 11 条测试全绿,面板对这把
skill 解析出 0 条出口。

这里把 exits.ts 的 `parseExitContract` 逐条移植过来(起点 = 第一个宽前缀命中;
终点 = 下一个 `## `;行首 `| E<数字> |` 才是出口行;非出口行并进上一条的条件;
第三格可以缺),然后断言:四把管线 skill 用这把尺量出来的条数与 id 就是 5/5/3/5,
每条的「怎么验」都不空,并且第一个宽前缀命中就是那个严格形的主标题。

⚠️ 移植对象:deepseek-harness `packages/client/ui-ann-progress/src/exits.ts`
(`HEADING = '## 出口契约'`、`isRowStart = /^\\| *E\\d+ *\\|/`)。面板改了解析规则,
这里要跟着改。

跑法: python -X utf8 -m pytest -q test_contract_panel_parse.py
"""
import re

from test_skill_contract import PIPELINE, each_skill

HEADING = "## 出口契约"
ROW_START = re.compile(r"^\| *E\d+ *\|")


def parse_exit_contract(lines):
    """exits.ts `parseExitContract` 的 Python 移植 → [{id, condition, verify}]。"""
    start = next((i for i, line in enumerate(lines) if line.startswith(HEADING)), -1)
    if start == -1:
        return []
    end = len(lines)
    for at in range(start + 1, len(lines)):
        if lines[at].startswith("## "):
            end = at
            break
    found, carrying = [], []

    def close():
        if found:
            extra = " ".join(l.strip() for l in carrying if l.strip())
            if extra:
                found[-1]["condition"] = (found[-1]["condition"] + " " + extra).strip()
        carrying.clear()

    for line in lines[start:end]:
        if not ROW_START.match(line):
            if found:
                carrying.append(line)
            continue
        close()
        cells = [c.strip() for c in line.split("|")]
        found.append({"id": cells[1] if len(cells) > 1 else "",
                      "condition": cells[2] if len(cells) > 2 else "",
                      "verify": cells[3] if len(cells) > 3 else ""})
    close()
    return [row for row in found if row["id"]]


def test_面板量出来的出口条数与_id_就是契约写的():
    got = {}
    for name, lines in each_skill():
        if name in PIPELINE:
            got[name] = [row["id"] for row in parse_exit_contract(lines)]
    want = {name: ["E%d" % i for i in range(1, n + 1)] for name, n in PIPELINE.items()}
    assert got == want, "面板解析结果 %s,应为 %s" % (got, want)


def test_面板量出来的每条出口都有怎么验():
    bad = []
    for name, lines in each_skill():
        if name not in PIPELINE:
            continue
        for row in parse_exit_contract(lines):
            if not row["verify"]:
                bad.append("%s %s 在面板上没有「怎么验」" % (name, row["id"]))
    assert bad == [], "; ".join(bad)


def test_第一个宽前缀命中就是严格形的主标题():
    """面板从第一个 `## 出口契约` 开头的行起算;那一行必须就是 `## 出口契约(…`。"""
    bad = []
    for name, lines in each_skill():
        if name not in PIPELINE:
            continue
        first = next((line for line in lines if line.startswith(HEADING)), None)
        if first is None or not first.startswith(HEADING + "("):
            bad.append("%s 的第一个「## 出口契约」开头的行是 %r" % (name, first))
    assert bad == [], "; ".join(bad)
