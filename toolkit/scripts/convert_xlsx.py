# -*- coding: utf-8 -*-
"""convert_xlsx — XLSX 摄入转换器(纯标准库:zipfile + xml,不依赖 openpyxl)。

用法:
  python -X utf8 convert_xlsx.py <文件.xlsx> [--sheet 1] [--out 输出.csv]

抽单元格值(含共享字符串/内联字符串/数值);公式取缓存值。
"""
import csv
import io
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def col_to_idx(ref):
    m = re.match(r"([A-Z]+)(\d+)", ref)
    col = 0
    for ch in m.group(1):
        col = col * 26 + (ord(ch) - 64)
    return int(m.group(2)) - 1, col - 1


def read_xlsx(path, sheet_no=1):
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter(
                    "{%s}t" % NS["m"])))
        sheet_path = "xl/worksheets/sheet%d.xml" % sheet_no
        if sheet_path not in z.namelist():
            raise SystemExit("没有 %s" % sheet_path)
        root = ET.fromstring(z.read(sheet_path))
    rows = {}
    for c in root.iter("{%s}c" % NS["m"]):
        ref = c.get("r")
        if not ref:
            continue
        r, col = col_to_idx(ref)
        ctype = c.get("t")
        v = c.find("m:v", NS)
        is_node = c.find("m:is", NS)
        if ctype == "s" and v is not None:
            val = shared[int(v.text)]
        elif ctype == "inlineStr" and is_node is not None:
            val = "".join(t.text or "" for t in is_node.iter("{%s}t" % NS["m"]))
        elif v is not None:
            val = v.text
        else:
            val = ""
        rows.setdefault(r, {})[col] = val
    if not rows:
        return []
    max_col = max(max(cols) for cols in rows.values())
    table = []
    for r in range(max(rows) + 1):
        table.append([rows.get(r, {}).get(c, "") for c in range(max_col + 1)])
    return table


def main(argv):
    HELP_FLAGS = ("-h", "--help")
    if any(f in argv for f in HELP_FLAGS):
        print(__doc__)
        return 0
    if len(argv) < 2:
        print(__doc__)
        return 2
    sheet = 1
    out = None
    if "--sheet" in argv:
        i = argv.index("--sheet")
        sheet = int(argv[i + 1])
        del argv[i:i + 2]
    if "--out" in argv:
        i = argv.index("--out")
        out = argv[i + 1]
        del argv[i:i + 2]
    table = read_xlsx(argv[1], sheet)
    print("%d 行 × %d 列" % (len(table), len(table[0]) if table else 0))
    for row in table[:5]:
        print("  " + " | ".join(str(x)[:20] for x in row[:8]))
    if out:
        with io.open(out, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows(table)
        print("→ %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
