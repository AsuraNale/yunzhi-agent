# -*- coding: utf-8 -*-
"""convert_pdf — PDF 摄入转换器:抽正文(pypdf)。

用法:
  python -X utf8 convert_pdf.py <文件.pdf> [--out 输出.md] [--pages 1-5]

采集纪律:抽不出文本(扫描件)时如实报 empty,不编。
"""
import io
import sys

from pypdf import PdfReader


def main(argv):
    HELP_FLAGS = ("-h", "--help")
    if any(f in argv for f in HELP_FLAGS):
        print(__doc__)
        return 0
    if len(argv) < 2:
        print(__doc__)
        return 2
    out = None
    pages = None
    if "--out" in argv:
        i = argv.index("--out")
        out = argv[i + 1]
        del argv[i:i + 2]
    if "--pages" in argv:
        i = argv.index("--pages")
        a, _, b = argv[i + 1].partition("-")
        pages = (int(a) - 1, int(b or a))
        del argv[i:i + 2]
    r = PdfReader(argv[1])
    n = len(r.pages)
    lo, hi = pages or (0, n)
    chunks = []
    empty_pages = 0
    for i in range(lo, min(hi, n)):
        t = (r.pages[i].extract_text() or "").strip()
        if not t:
            empty_pages += 1
        chunks.append(t)
    text = "\n\n".join(chunks)
    state = "ok" if text.strip() else "empty"
    print("fetch_state=%s · %d/%d 页 · %d 字符 · 空白页 %d" % (
        state, min(hi, n) - lo, n, len(text), empty_pages))
    if state == "empty":
        print("⚠️ 抽不出文本(可能是扫描件)—— 如实记 empty,不要编内容。")
    if out:
        with io.open(out, "w", encoding="utf-8") as f:
            f.write(text)
        print("→ %s" % out)
    return 0 if state == "ok" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
