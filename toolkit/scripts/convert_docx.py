# -*- coding: utf-8 -*-
"""convert_docx — DOCX 摄入转换器:抽正文 + track changes(修订)。

用法:
  python -X utf8 convert_docx.py <文件.docx> [--out 输出.md] [--track-changes]

--track-changes 输出修订统计与逐条清单(w:ins 插入 / w:del 删除)。
"""
import io
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def q(tag):
    return "{%s}%s" % (W, tag)


def extract(path):
    """→ (正文文本, ins列表, del列表);正文不含被删除文本。"""
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    paras = []
    ins_list = []
    del_list = []
    for p in root.iter(q("p")):
        buf = []
        for el in p.iter():
            if el.tag == q("ins"):
                text = "".join(t.text or "" for t in el.iter(q("t")))
                ins_list.append(text)
            elif el.tag == q("del"):
                text = "".join(t.text or "" for t in el.iter(q("delText")))
                del_list.append(text)
            elif el.tag == q("t"):
                # 不重复计 ins 内的 t:ins 的 t 也会被父层 iter 命中,
                # 但正文本来就该包含已插入文本,所以直接收
                buf.append(el.text or "")
        paras.append("".join(buf))
    text = "\n".join(paras)
    return text, ins_list, del_list


def main(argv):
    HELP_FLAGS = ("-h", "--help")
    if any(f in argv for f in HELP_FLAGS):
        print(__doc__)
        return 0
    if len(argv) < 2:
        print(__doc__)
        return 2
    out = None
    if "--out" in argv:
        i = argv.index("--out")
        out = argv[i + 1]
        del argv[i:i + 2]
    tc = "--track-changes" in argv
    if tc:
        argv.remove("--track-changes")
    text, ins_list, del_list = extract(argv[1])
    print("正文 %d 字符 · %d 段" % (len(text), text.count("\n") + 1))
    if tc:
        print("track changes: ins=%d · del=%d" % (len(ins_list), len(del_list)))
        for i, t in enumerate(ins_list[:10]):
            print("  +[%d] %s" % (i + 1, t[:60]))
        for i, t in enumerate(del_list[:10]):
            print("  -[%d] %s" % (i + 1, t[:60]))
    if out:
        with io.open(out, "w", encoding="utf-8") as f:
            f.write(text)
        print("→ %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
