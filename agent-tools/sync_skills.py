# -*- coding: utf-8 -*-
"""sync_skills — 把 .agents/skills/(Codex 读)同步到 .codebuddy/skills/(WorkBuddy 读),逐字节相同。

用法(在工作区根目录下跑;$PY 见 AGENTS.md):
  $PY agent-tools/sync_skills.py            同步:逐个文件按字节复制;目标里多出来的文件删掉
  $PY agent-tools/sync_skills.py --check    只核对:一样 → 退出码 0;不一样 → 1,并列出哪些文件
  [--source <目录>] [--target <目录>]       换源或目标(测试用)

只动目标 skills 文件夹里的东西;.codebuddy/ 下别的文件(settings.json 之类)不碰。第三阶段(WorkBuddy)才用。
"""
import argparse
import hashlib
import io
import json
import os
import shutil
import sys

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def tree(base):
    """目录下的全部文件 → {相对路径(/ 分隔): 绝对路径};跳过 __pycache__。"""
    out = {}
    if not os.path.isdir(base):
        return out
    for d, dirs, files in os.walk(base):
        dirs[:] = sorted(x for x in dirs if x != "__pycache__")
        for f in sorted(files):
            p = os.path.join(d, f)
            out[os.path.relpath(p, base).replace("\\", "/")] = p
    return out


def digest(path):
    h = hashlib.sha256()
    with io.open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def diff(source, target):
    src, dst = tree(source), tree(target)
    missing = sorted(set(src) - set(dst))
    extra = sorted(set(dst) - set(src))
    changed = sorted(p for p in set(src) & set(dst) if digest(src[p]) != digest(dst[p]))
    return src, dst, missing, extra, changed


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="sync_skills.py")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--source", default=os.path.join(ROOT, ".agents", "skills"))
    ap.add_argument("--target", default=os.path.join(ROOT, ".codebuddy", "skills"))
    args = ap.parse_args(argv[1:])
    if not os.path.isdir(args.source):
        print(json.dumps({"ok": False, "error": "没有源目录 %s" % args.source}, ensure_ascii=False))
        return 2
    src, dst, missing, extra, changed = diff(args.source, args.target)
    if args.check:
        same = not (missing or extra or changed)
        print(json.dumps({"ok": same, "files": len(src), "missing": missing, "extra": extra, "changed": changed},
                         ensure_ascii=False, indent=1))
        return 0 if same else 1
    for rel in missing + changed:
        out = os.path.join(args.target, *rel.split("/"))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shutil.copyfile(src[rel], out)
    for rel in extra:
        os.remove(dst[rel])
    for d, dirs, files in sorted(os.walk(args.target, topdown=False)):
        if d != args.target and not os.listdir(d):
            os.rmdir(d)
    _, _, m2, e2, c2 = diff(args.source, args.target)
    ok = not (m2 or e2 or c2)
    print(json.dumps({"ok": ok, "files": len(src), "copied": missing + changed, "removed": extra},
                     ensure_ascii=False, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
