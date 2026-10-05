# -*- coding: utf-8 -*-
"""review_result — 独立复核结果 `review/result.json`(规格 §⑨-2 · v1.2.1)。

用法:
  python -X utf8 review_result.py <项目目录> --files [<轮次>]
      列出当前的成稿文件与内容 hash。带轮次:同时记进 review/files-<轮次>.json —— 第 n 轮开始复核之前跑。
      第 n 轮已经开始复核(有了 findings-<n>.json 或 report-<n>.md)、已经封存或已经放弃,就不再重记:
      成稿与记下的一样,什么都不改(退出码 0);不一样,拒绝(退出码 2)—— 成稿变了就是新的一轮,
      没封存的那一轮先 --abandon,再开第 n+1 轮、对现在的成稿重新复核。
  python -X utf8 review_result.py <项目目录> --seal <轮次>
      读 review/findings-<轮次>.json(这一轮的发现,一个列表)与 review/report-<轮次>.md(人读报告,
      必须在),校验每条发现,按盘上的成稿算 files、按发现数 counts,写出 review/result.json,另存
      review/result-<轮次>.json(下一轮核对用;应用只读 result.json)。
      拒写(退出码 2,什么都不写):发现写得不对、报告不在、没有成稿文件、这一轮已经放弃、没有先 --files <轮次>、
      成稿与复核开始时记下的不一样、轮次比现有结果的小、同一轮已经封过而成稿之后又改过;
      与最后一个封存的轮次对不上:那一轮还开着的发现没有全部带过来(按 round + location + original + severity 核,
      带过来的不许改档)、成稿和那一轮一模一样却把那一轮还开着的标成已改好、标成已改好的在那一轮里找不到;
      两轮之间有一轮既没封存也没放弃。
  python -X utf8 review_result.py <项目目录> --abandon <轮次>
      放弃一个没封存的轮次(复核期间成稿改了,这一轮的发现绑不上任何一版)。写 review/abandoned-<轮次>.json,
      这一轮的发现与报告留在盘上、不进结果;之后封下一轮时,对照的是最后一个封存过的轮次。封存过的不能放弃。
  python -X utf8 review_result.py <项目目录> --check
      对得上当前成稿且必须改为 0 → 退出码 0;对不上,或还有必须改的 → 1;结果文件不在或格式不对 → 2。

counts 与 files 都由本脚本算,不手写。应用不调用本脚本,按规格 §⑨-2 自己判 —— 弹交付卡之前跑
--check,是让你先知道应用会不会放行。
"""
import io
import json
import os
import sys
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline_lib import (REVIEW_FORMAT, REVIEW_KIND, REVIEW_RESULT, deliverable_files,
                          review_applies, review_counts, review_finding_problems,
                          review_result_problems)

LABELS = (("must_fix", "必须改", "处"), ("tone_down", "说法要收一收", "处"),
          ("citation_or_format", "引用或格式损坏", "处"), ("checked_ok", "抽查无误", "段"))
# 拒绝时告诉 agent 下一步:成稿变了就是新的一轮,要对现在的成稿重新复核(v1.2.1 再复核 N1)。
NEW_ROUND = "成稿变了就是新的一轮:%s开第 %d 轮(--files %d),对现在的成稿重新复核"


def say_counts(counts):
    return " · ".join("%s %d %s" % (zh, counts.get(key, 0), unit) for key, zh, unit in LABELS)


def load_json(path):
    """→ (对象, 读不出时的一句说明)。"""
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f), None
    except UnicodeDecodeError:
        return None, "不是 UTF-8 编码"
    except ValueError as error:
        line = getattr(error, "lineno", None)
        return None, "第 %d 行 JSON 写法有误" % line if line else "JSON 写法有误"


def write_atomic(path, text):
    """先写旁边的临时文件再换名:写到一半崩了,原来那份还在。"""
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def review_path(project, name):
    return os.path.join(project, "review", name)


def round_state(project, n):
    """第 n 轮现在怎样:sealed(封存过)· abandoned(放弃了)· started(有了发现或报告)· recorded(只记下了成稿)· none。"""
    if os.path.exists(review_path(project, "result-%d.json" % n)):
        return "sealed"
    if os.path.exists(review_path(project, "abandoned-%d.json" % n)):
        return "abandoned"
    if any(os.path.exists(review_path(project, name % n)) for name in ("findings-%d.json", "report-%d.md")):
        return "started"
    if os.path.exists(review_path(project, "files-%d.json" % n)):
        return "recorded"
    return "none"


def recorded_files(project, n):
    """review/files-<n>.json → (files 映射, 读不出时的一句说明)。"""
    path = review_path(project, "files-%d.json" % n)
    if not os.path.isfile(path):
        return None, ("没有 review/files-%d.json:开始复核之前要先 --files %d 记下成稿。这一轮已经复核了却没记,就不知道"
                      "复核的是哪一版 —— 先 --abandon %d,再开第 %d 轮(--files %d),对现在的成稿重新复核"
                      % (n, n, n, n + 1, n + 1))
    record, problem = load_json(path)
    files = record.get("files") if isinstance(record, dict) else None
    if problem or not isinstance(files, dict) or record.get("round") != n:
        return None, ("review/files-%d.json 读不出来或不是第 %d 轮的:先 --abandon %d,再开第 %d 轮(--files %d)"
                      % (n, n, n, n + 1, n + 1))
    return files, None


def record_files(project, n):
    """--files <n>:记下第 n 轮复核的成稿。这一轮开始复核之后就不再重记(N1:否则结果会绑到没人复核过的那一版上)。"""
    files = deliverable_files(project)
    for rel, digest in files.items():
        print("%s  %s" % (digest, rel))
    if not files:
        print("⛔ 盘上没有成稿文件(out/ 下的 .html / .docx):先生成成稿再复核")
        return 2
    state = round_state(project, n)
    if state in ("sealed", "abandoned", "started"):
        recorded, _ = recorded_files(project, n)
        if state != "abandoned" and recorded == files:
            print("第 %d 轮记下的成稿和现在的一样,没有重记(review/files-%d.json 不动)" % (n, n))
            return 0
        if state == "sealed":
            print("⛔ 第 %d 轮已经封存,成稿之后又改过,不能重记。" % n + NEW_ROUND % ("", n + 1, n + 1))
        elif state == "abandoned":
            print("⛔ 第 %d 轮已经放弃,不能重记。" % n + NEW_ROUND % ("", n + 1, n + 1))
        else:
            print("⛔ 第 %d 轮已经开始复核(有了 findings-%d.json 或 report-%d.md),成稿不能重记:重记了,这一轮的发现就会"
                  "绑到没人复核过的那一版上。" % (n, n, n) + NEW_ROUND % ("先 --abandon %d,再" % n, n + 1, n + 1))
        return 2
    os.makedirs(os.path.join(project, "review"), exist_ok=True)
    record = {"round": n, "recorded_at": now(), "files": files}
    write_atomic(review_path(project, "files-%d.json" % n), json.dumps(record, ensure_ascii=False, indent=1) + "\n")
    print("已记下第 %d 轮复核的成稿:%d 个文件(review/files-%d.json)" % (n, len(files), n))
    return 0


def abandon(project, n):
    """--abandon <n>:放弃一个没封存的轮次。"""
    if round_state(project, n) == "sealed":
        print("⛔ 第 %d 轮已经封存,不能放弃:封存的结果是下一轮核对的依据。成稿改了就开第 %d 轮(--files %d)"
              % (n, n + 1, n + 1))
        return 2
    if round_state(project, n) != "abandoned":
        os.makedirs(os.path.join(project, "review"), exist_ok=True)
        write_atomic(review_path(project, "abandoned-%d.json" % n),
                     json.dumps({"round": n, "abandoned_at": now()}, ensure_ascii=False) + "\n")
    print("已放弃第 %d 轮(review/abandoned-%d.json;这一轮的发现和报告留在盘上,不进结果)。接着开第 %d 轮:--files %d,"
          "对现在的成稿重新复核;封存时要带上的,是最后一个封存过的轮次里还开着的每一条" % (n, n, n + 1, n + 1))
    return 0


def last_sealed_round(project, n):
    """n 之前最后一个封存的轮次 → (k 或 None, 说明或 None)。k 与 n 之间的轮次必须都放弃了。"""
    for k in range(n - 1, 0, -1):
        state = round_state(project, k)
        if state == "sealed":
            return k, None
        if state != "abandoned":
            return None, ("第 %d 轮既没封存也没放弃:先封好上一轮(--seal %d);成稿已经改了、封不了,或者根本没做过这一轮,"
                          "就 --abandon %d" % (k, k, k))
    return None, None


def _issues(findings):
    return [f for f in findings if isinstance(f, dict) and f.get("severity") != "checked_ok"]


def _key(f):
    return (f.get("round"), f.get("location"), f.get("original"), f.get("severity"))


def carried_problems(project, n, findings, files):
    """第 n 轮与最后一个封存的轮次 k 对照(M2;再复核 N1、N2)→ 说明列表。
    k 里还开着的每一条都要带过来,档位不许改(按 round + location + original + severity 计条数);
    标成已改好的必须是 k 里有的那一条;成稿与 k 封存时一模一样,k 里还开着的不能标成已改好。"""
    k, problem = last_sealed_round(project, n)
    if problem:
        return [problem]
    prev, prev_files = [], None
    if k is not None:
        sealed, problem = load_json(review_path(project, "result-%d.json" % k))
        if problem or not isinstance(sealed, dict) or not isinstance(sealed.get("findings"), list):
            return ["第 %d 轮的封存结果(review/result-%d.json)读不出来" % (k, k)]
        prev, prev_files = _issues(sealed["findings"]), sealed.get("files")
    now_ = _issues(findings)
    prev_all = Counter(_key(f) for f in prev)
    prev_open = Counter(_key(f) for f in prev if f.get("status") == "open")
    prev_fixed = prev_all - prev_open
    now_all = Counter(_key(f) for f in now_)
    now_fixed = Counter(_key(f) for f in now_ if f.get("status") == "fixed")
    same_draft = k is not None and prev_files == files
    out = []
    for key, count in sorted(prev_open.items(), key=repr):
        if now_all[key] >= count:
            continue
        rnd, loc, orig, sev = key
        where = "第 %d 轮还开着的一条(%s,第 %s 轮提出,位置:%s)" % (k, sev, rnd, loc)
        other = sorted({str(f.get("severity")) for f in now_ if _key(f)[:3] == key[:3] and f.get("severity") != sev})
        if other:
            out.append(where + "带过来时档位改成了 %s:带过来的发现不许改档" % " / ".join(other))
        else:
            out.append(where + "这一轮没有带上")
    for key, count in sorted(now_fixed.items(), key=repr):
        rnd, loc, orig, sev = key
        what = "第 %d 轮标成已改好的一条(%s,第 %s 轮提出,位置:%s)" % (n, sev, rnd, loc)
        if count > prev_all[key]:
            out.append(what + ("在第 %d 轮封存的结果里找不到同一档的那一条" % k if k else "之前没有封存过的轮次,找不到它"))
        elif same_draft and count > prev_fixed[key]:
            out.append(what + "在第 %d 轮还开着,而成稿和第 %d 轮封存时一模一样:成稿没改,不能标成已改好" % (k, k))
    return out


def seal(project, n):
    findings_path = review_path(project, "findings-%d.json" % n)
    report_rel = "review/report-%d.md" % n
    report_path = os.path.join(project, *report_rel.split("/"))
    state = round_state(project, n)
    if state == "abandoned":
        print("⛔ 第 %d 轮已经放弃,不能封存。" % n + NEW_ROUND % ("", n + 1, n + 1))
        return 2
    if not os.path.isfile(findings_path):
        print("⛔ 没有 review/findings-%d.json:先把这一轮的发现写成一个列表(规格 §⑨-2 findings 表)" % n)
        return 2
    findings, problem = load_json(findings_path)
    if problem:
        print("⛔ review/findings-%d.json 读不出来:%s" % (n, problem))
        return 2
    if not isinstance(findings, list):
        print("⛔ review/findings-%d.json 应是一个列表(每条一个发现)" % n)
        return 2
    problems = []
    for i, f in enumerate(findings):
        problems += review_finding_problems(f, n, "第 %d 条" % (i + 1))
    if problems:
        print("⛔ 这一轮的发现有 %d 处写得不对,没有封存:" % len(problems))
        for p in problems:
            print("  - " + p)
        return 2
    if not (os.path.isfile(report_path) and os.path.getsize(report_path) > 0):
        print("⛔ 没有人读报告 %s(或是空的):每一轮都要留一份" % report_rel)
        return 2
    files = deliverable_files(project)
    if not files:
        print("⛔ 盘上没有成稿文件(out/ 下的 .html / .docx):先生成成稿再复核")
        return 2
    # M1 / N1:结果只能绑在被复核的那一版上 —— 复核开始时记下的成稿与现在的不一样,就不封。
    recorded, problem = recorded_files(project, n)
    if problem:
        print("⛔ " + problem)
        return 2
    if recorded != files:
        if state == "sealed":
            print("⛔ 第 %d 轮封存之后成稿又改过。" % n + NEW_ROUND % ("", n + 1, n + 1))
        else:
            print("⛔ 成稿和第 %d 轮开始复核时记下的不一样(review/files-%d.json):复核期间成稿改过,这一轮的结果绑不上"
                  "被复核的那一版。" % (n, n) + NEW_ROUND % ("先 --abandon %d,再" % n, n + 1, n + 1))
        return 2
    result_path = os.path.join(project, REVIEW_RESULT)
    if os.path.exists(result_path):
        old, _ = load_json(result_path)
        old_round = old.get("round") if isinstance(old, dict) else None
        if isinstance(old_round, int) and not isinstance(old_round, bool):
            if old_round > n:
                print("⛔ 现有结果已经是第 %d 轮,不能用第 %d 轮盖掉它" % (old_round, n))
                return 2
            if old_round == n and old.get("files") != files:
                print("⛔ 第 %d 轮封存之后成稿又改过。" % n + NEW_ROUND % ("", n + 1, n + 1))
                return 2
    # M2 / N2:与最后一个封存的轮次对照。
    missing = carried_problems(project, n, findings, files)
    if missing:
        print("⛔ 和上一个封存的轮次对不上,没有封存:")
        for p in missing:
            print("  - " + p)
        return 2
    counts = review_counts(findings)
    result = {"kind": REVIEW_KIND, "format": REVIEW_FORMAT, "round": n, "reviewed_at": now(),
              "report": report_rel, "files": files, "counts": counts, "findings": findings}
    left = review_result_problems(result)
    if left:   # 不该发生:上面逐条核过。真发生了就别写一份应用读不懂的结果出去。
        print("⛔ 拼出来的结果格式不对,没有写:" + ";".join(left))
        return 2
    os.makedirs(os.path.join(project, "review"), exist_ok=True)
    text = json.dumps(result, ensure_ascii=False, indent=1) + "\n"
    write_atomic(result_path, text)
    write_atomic(review_path(project, "result-%d.json" % n), text)
    print("已封存第 %d 轮复核:%s · 成稿 %d 个文件" % (n, say_counts(counts), len(files)))
    return 0


def check(project):
    path = os.path.join(project, REVIEW_RESULT)
    if not os.path.isfile(path):
        print("⛔ 没有复核结果 review/result.json:交付前要做独立复核")
        return 2
    result, problem = load_json(path)
    if problem:
        print("⛔ review/result.json 读不出来:%s" % problem)
        return 2
    problems = review_result_problems(result)
    if problems:
        print("⛔ review/result.json 格式不对:" + ";".join(problems))
        return 2
    applies, why = review_applies(project, result)
    print("第 %d 轮复核:%s" % (result["round"], say_counts(result["counts"])))
    if not applies:
        print("⛔ 复核结果对不上当前成稿 —— %s。要再复核一轮" % why)
        return 1
    if result["counts"]["must_fix"] > 0:
        print("⛔ 还有 %d 处必须改:改完再复核一轮" % result["counts"]["must_fix"])
        return 1
    print("可以交付:%s,必须改 0 处" % why)
    return 0


def round_arg(text):
    return int(text) if text.isascii() and text.isdigit() and int(text) >= 1 else None


def main(argv):
    if any(flag in argv for flag in ("-h", "--help")) or len(argv) < 3:
        print(__doc__)
        return 0 if any(flag in argv for flag in ("-h", "--help")) else 2
    project, action = argv[1], argv[2]
    if not os.path.isdir(project):
        print("⛔ 项目目录不在:%s" % project)
        return 2
    if action == "--files" and len(argv) == 3:
        files = deliverable_files(project)
        for rel, digest in files.items():
            print("%s  %s" % (digest, rel))
        if not files:
            print("盘上没有成稿文件(out/ 下的 .html / .docx)")
            return 1
        return 0
    if action == "--check" and len(argv) == 3:
        return check(project)
    if action in ("--files", "--seal", "--abandon") and len(argv) == 4:
        n = round_arg(argv[3])
        if n is None:
            print("⛔ 轮次应是 ≥1 的整数")
            return 2
        return {"--files": record_files, "--seal": seal, "--abandon": abandon}[action](project, n)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
