# -*- coding: utf-8 -*-
"""env_check — 每次对话开始先跑一遍:Python 能不能用、缺不缺组件、这个文件夹写不写得进。

用法(在工作区根目录下跑;$PY 见 AGENTS.md):
  $PY agent-tools/env_check.py
输出 JSON:ok · checks(逐项)· say(有问题时原样告诉用户的话)· next(你接着做什么)。退出码 0 = 都行,1 = 有问题。

这条命令本身跑不起来(报「No installed Pythons found」、拒绝访问 / Access is denied、找不到 py 或 python3)时,
它什么也打印不出来 —— 那种情况怎么跟用户说,写在 AGENTS.md 第〇节:是 Codex 的防护沙盒挡住了 Python,
不要说「没有安装 Python」。
"""
import io
import json
import os
import shutil
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGES = (("yaml", "PyYAML"), ("docx", "python-docx"), ("pypdf", "pypdf"))
# 对用户说缺了什么时说用途,不说英文的组件名(第六轮:守门脚本扫回复,python-docx 里的 docx 会被当成内部词)
PURPOSES = {"PyYAML": "读写资料卡片和任务计划", "python-docx": "生成 Word 版", "pypdf": "读 PDF 材料"}
SANDBOX_USERS = ("codexsandboxoffline",)
# 第六轮:这台机器的命令行里没有 rg,GPT-6.1 Sol 每轮都先试 rg、报错再换。报出来,让助手直接用 Select-String
RG_MISSING = ("这台电脑的命令行里没有 rg：搜文件内容直接用 PowerShell 的 Select-String（例如 "
              "Select-String -Path \"projects/<项目名>/cards/*.md\" -Pattern \"关键词\" -Encoding utf8），不要试 rg，也不要重试。")


def check_rg():
    """命令行里有没有 rg(只是告诉助手怎么搜文件,不算环境问题)。"""
    path = shutil.which("rg")
    return {"present": bool(path), "detail": path or RG_MISSING}


def check_python():
    ok = sys.version_info >= (3, 8)
    return {"name": "Python", "ok": ok, "detail": "%d.%d.%d" % sys.version_info[:3]}


def check_packages():
    missing = []
    for mod, pip_name in PACKAGES:
        try:
            __import__(mod)
        except ImportError:
            missing.append(pip_name)
    return {"name": "Python 组件", "ok": not missing, "missing": missing,
            "detail": "齐了" if not missing else "缺 %s" % "、".join(missing)}


def check_write():
    """在工作区的临时文件夹里建一个文件夹、写一个文件、读回来、删掉(和脚本平时用临时文件夹的办法一样)。"""
    try:
        import yzlib
        tmp = yzlib.work_tmp("envcheck")
        try:
            probe = os.path.join(tmp, "写入测试.txt")
            with io.open(probe, "w", encoding="utf-8") as f:
                f.write("云织")
            with io.open(probe, encoding="utf-8") as f:
                ok = f.read() == "云织"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return {"name": "写文件", "ok": ok, "detail": "这个文件夹写得进" if ok else "写进去的内容读回来不对"}
    except Exception as e:  # noqa: BLE001
        return {"name": "写文件", "ok": False, "detail": "%s: %s" % (type(e).__name__, e)}


def check_cwd():
    """命令是不是在工作区根目录下跑的(第四轮:在上一级文件夹里跑命令,相对路径全对不上)。"""
    import yzlib
    problem = yzlib.cwd_problem()
    return {"name": "在工作区根目录下跑", "ok": problem is None, "detail": problem or "是"}


def who():
    for key in ("USERNAME", "USER"):
        v = os.environ.get(key)
        if v:
            return v
    return ""


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    where = check_cwd()
    checks = [check_python(), check_packages(), check_write()]
    user = who()
    rg = check_rg()
    out = {"ok": where["ok"] and all(c["ok"] for c in checks), "checks": [where] + checks, "python": sys.executable, "user": user,
           "rg": rg}
    if not where["ok"]:
        # 命令写法的问题:助手自己改了重跑,不用告诉用户(没有 say)
        out["next"] = where["detail"]
        out["retry"] = "自己改了重跑"
    elif not checks[0]["ok"]:
        out["say"] = "这台电脑上的 Python 版本太旧（%s），云织的脚本要 3.8 以上。请装一个新一点的 Python，再重开这个对话。" % checks[0]["detail"]
        out["next"] = "停在这里，等用户装好。"
    elif not checks[2]["ok"]:
        out["say"] = ("Codex 为了保护你的电脑给命令加了一层沙盒，现在这层沙盒不让我在这个文件夹里写文件，"
                      "所以云织的脚本跑不起来。请在 Codex 里把这个文件夹设为信任（第一次打开时会问），然后重开这个对话。")
        out["next"] = "停在这里：不要绕过、不要换别的文件夹写。用户处理好之后重开对话再跑一次这条检查。"
    elif not checks[1]["ok"]:
        out["say"] = "云织的脚本还缺几个 Python 组件（用来%s），我可以帮你装上，可以吗？" % "、".join(
            PURPOSES.get(m, "跑云织的脚本") for m in checks[1]["missing"])
        out["next"] = "先问用户；同意后跑 $PY -m pip install -r \"toolkit/requirements.txt\"，装完再跑一次这条检查。"
    else:
        out["next"] = ("环境没问题，照常开始（按 yunzhi 跑 projects.py status 或新建项目）。"
                       "这个对话里不用再跑这条检查（第三轮实测跑了 6 次）；只有用户说装了东西、改了设置，才再跑一次。")
    if not rg["present"]:
        out["next"] += " " + RG_MISSING
    if user.lower() in SANDBOX_USERS:
        out["note"] = "命令在 Codex 的沙盒身份下跑（%s）；这次 Python 能用，照常做。" % user
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
