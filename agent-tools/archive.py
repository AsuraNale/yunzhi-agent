# -*- coding: utf-8 -*-
"""archive — 交付之后的归档(cite-trace 出口契约 E5):三样东西进 projects/<项目名>/library/。

用法(在工作区根目录下跑;$PY 见 AGENTS.md):
  $PY agent-tools/archive.py "<项目名>"
      写 library/sources.yaml(成稿用到的来源快照;走过放宽资料要求的标出放宽了什么、适用哪些主张)·
      library/gaps.yaml(没补上的资料缺口,带五态,连同用户在决定卡上怎么定的)·
      library/rules/(资料汇编 rules_candidates 里的每一条红线候选一份,README.md 写条数),
      PROGRESS.md 末尾记一行。交付卡点了「交付」之后 card.py answer 会自动跑它;没跑成就照输出改好再跑。
  $PY agent-tools/archive.py "<项目名>" --check
      核:三样都在,而且和资料汇编、成稿对得上;项目文件夹里没有助手写的脚本 → 退出码 0;否则 1 并逐条说明。

10-04 实测:命令行那一轮助手自己写了 records/archive_delivery.py 来做这一步(还写了 read_source.py、source_images.py)。
归档的格式照它那一版(那一版的做法是对的),换成现成的脚本:助手不在项目文件夹里写脚本。
library/delivery_commitments.md 留在原处(下一轮任务拿它核上一轮的预测),这里不动它。
退出码:0 做完(或核过)· 1 没做成 / 没核过(照 problems 改)· 2 用法错 · 3 脚本出错。
"""
import argparse
import io
import json
import os
import re
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yaml  # noqa: E402
import yzlib  # noqa: E402
from yzlib import pl  # noqa: E402

RULE_ITEM_RE = re.compile(r"^\s*[-*]\s*\**\s*(R\d+)\s*\**\s*[：:、.．]?\s*(.+?)\s*$", re.M)
BOUNDARIES_RE = re.compile(r"^#{1,6}[^\n]*\{#boundaries\}[^\n]*$", re.M)
NEXT_H_RE = re.compile(r"^#{1,6}\s", re.M)
CARD_ID_RE = re.compile(r"(?<![A-Za-z0-9])S\d{1,4}(?![0-9])")
COUNT_RE = re.compile(r"共\s*(\d+)\s*条")


def library(project, *parts):
    return os.path.join(project, "library", *parts)


def _dump(data):
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)


def _read_yaml(path):
    try:
        with io.open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except (OSError, yaml.YAMLError, ValueError):
        return None


def delivery_record(project):
    path = os.path.join(project, "records", "delivery.json")
    try:
        with io.open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def cited_entries(project):
    """成稿(底稿)里真正用到的参考文献条目,按正文里第一次出现的顺序 → (条目列表, 问题或 None)。"""
    import review as review_mod
    draft = review_mod.draft_for(project)
    refs = os.path.join(project, "references.yaml")
    if not draft:
        return [], "找不到成稿的底稿（drafts/ 里没有和成稿同名的 .md）"
    if not os.path.isfile(refs):
        return [], "没有参考文献清单（references.yaml）"
    _meta, body = pl.read_md(draft)
    entries = pl.load_references(refs)
    _ph, numbered = pl.number_entries(body, entries if isinstance(entries, list) else [])
    return [dict(e) for _no, e in numbered if isinstance(e, dict)], None


def boundary_texts(body):
    """资料汇编正文里每条红线候选的文字 → {R1: 文字}。先找「边界层」那一节,找不到再找全文。"""
    found = {}
    m = BOUNDARIES_RE.search(body or "")
    scopes = []
    if m:
        rest = body[m.end():]
        nxt = NEXT_H_RE.search(rest)
        scopes.append(rest[:nxt.start()] if nxt else rest)
    scopes.append(body or "")
    for scope in scopes:
        for rid, text in RULE_ITEM_RE.findall(scope):
            found.setdefault(rid, text.strip())
        if found:
            break
    return found


def decisions(project):
    """用户在决定卡上的选择(资料缺口怎么处理一类)→ [{at, card, question, chose, note}]。"""
    log = yzlib.read_jsonl(yzlib.card_log(project))
    questions = {e.get("card_id"): e.get("question") for e in log if e.get("type") == "prepared" and e.get("kind") == "decision"}
    out = []
    for e in yzlib.answered_decisions(project):
        out.append({"at": e.get("at"), "card": e.get("card_id"), "question": questions.get(e.get("card_id")),
                    "chose": e.get("label"), "note": e.get("note")})
    return out


def gap_cards(project):
    import card as card_mod
    out = []
    for c, fn in card_mod.load_live_cards(project) or []:
        if c.get("stance") != "gap":
            continue
        srcs = [{"url": s.get("url"), "title": s.get("title"), "fetch_state": s.get("fetch_state"), "as_of": pl.date_text(s.get("as_of"))}
                for s in c.get("sources") or [] if isinstance(s, dict)]
        out.append({"uid": str(c.get("uid") or fn), "title": c.get("title"), "state": card_mod.gap_card_state(c), "sources": srcs})
    return out


def _load_doc(project, name):
    path = os.path.join(project, name)
    if not os.path.isfile(path):
        return None, None
    meta, body, problem = pl.load_md(path)
    return (None, None) if problem else (meta, body)


def run(project):
    """写归档 → {ok, sources, gaps, rules, written, problems}。"""
    problems = []
    plan, _pb = _load_doc(project, "task_plan.md")
    if not isinstance(plan, dict) or plan.get("pipeline_status") != "delivered":
        return {"ok": False, "problems": ["还没交付（进度不是已交付）：交付卡上用户点了「交付」之后再归档"]}
    record = delivery_record(project)
    if record is None:
        problems.append("没有交付记录（records/delivery.json）：交付记录由 card.py answer 写，交付卡回答之后才有")
    dossier, dbody = _load_doc(project, "dossier.md")
    if not isinstance(dossier, dict):
        problems.append("资料汇编（dossier.md）读不出来")
    entries, why = cited_entries(project)
    if why:
        problems.append(why)
    if problems:
        return {"ok": False, "problems": problems}
    os.makedirs(library(project, "rules"), exist_ok=True)
    standard = plan.get("evidence_standard") if isinstance(plan.get("evidence_standard"), dict) else {}
    relaxed = standard.get("inherit") is False
    sources = {
        "kind": "sources_snapshot",
        "delivered_at": record.get("delivered_at"),
        "task_plan_ref": "task_plan.md#v%s" % plan.get("version"),
        "dossier_ref": "dossier.md#v%s" % dossier.get("version"),
        "delivery_files": record.get("files"),
        "evidence_standard": standard,
        # 走过放宽资料要求:放宽了什么(primary_tiers)、适用哪些主张(scope_note)
        "relaxations": ([{"primary_tiers": standard.get("primary_tiers"), "scope_note": standard.get("scope_note")}]
                        if relaxed else []),
        "entries": entries,
    }
    gaps = [dict(g) for g in dossier.get("gaps") or [] if isinstance(g, dict)] if isinstance(dossier.get("gaps"), list) else []
    gap_doc = {"kind": "gaps_snapshot", "delivered_at": record.get("delivered_at"),
               "decisions": decisions(project), "gaps": gaps, "gap_cards": gap_cards(project)}
    candidates = [str(r) for r in dossier.get("rules_candidates") or []] if isinstance(dossier.get("rules_candidates"), list) else []
    texts = boundary_texts(dbody)
    written = []
    yzlib.write_atomic(library(project, "sources.yaml"), _dump(pl_safe(sources)))
    written.append("library/sources.yaml")
    yzlib.write_atomic(library(project, "gaps.yaml"), _dump(pl_safe(gap_doc)))
    written.append("library/gaps.yaml")
    missing_text = []
    for rid in candidates:
        text = texts.get(rid)
        if not text:
            missing_text.append(rid)
            text = "（资料汇编正文的边界层里没找到这一条的文字）"
        uids = list(dict.fromkeys(CARD_ID_RE.findall(text)))
        body = "# %s · 红线候选\n\n%s\n\n%s来自资料汇编第 %s 版；是本项目的候选，还没有进默认规范（要进默认规范得经人审）。\n" % (
            rid, text, ("来源资料卡片：%s。\n\n" % "、".join(uids)) if uids else "", dossier.get("version"))
        yzlib.write_atomic(library(project, "rules", "%s.md" % rid), body)
        written.append("library/rules/%s.md" % rid)
    for fn in os.listdir(library(project, "rules")):
        if re.fullmatch(r"R\d+\.md", fn) and fn[:-3] not in candidates:
            os.remove(library(project, "rules", fn))          # 以前归档过、现在不在候选清单里的
    if candidates:
        readme = "# 本项目的红线候选\n\n共 %d 条，与资料汇编的候选清单一致。\n\n%s\n" % (
            len(candidates), "\n".join("- [%s](%s.md)" % (rid, rid) for rid in candidates))
    else:
        readme = "# 本项目的红线候选\n\n共 0 条：资料汇编没有列红线候选。\n"
    yzlib.write_atomic(library(project, "rules", "README.md"), readme)
    written.append("library/rules/README.md")
    line = "已交付 · 成稿在 out/ · 归档：来源 %d 条、资料缺口 %d 个、红线候选 %d 条" % (len(entries), len(gaps), len(candidates))
    progress = os.path.join(project, "PROGRESS.md")
    already = ""
    if os.path.isfile(progress):
        with io.open(progress, encoding="utf-8", errors="replace") as f:
            already = f.read()
    if line not in already:
        yzlib.append_progress_note(project, line)
    out = {"ok": True, "sources": len(entries), "gaps": len(gaps), "rules": len(candidates), "written": written,
           "say": "成稿已交付。这次用到的来源和还没补上的资料缺口已经存进项目，留给以后的课题。"}
    if missing_text:
        out["warnings"] = ["这几条红线候选在资料汇编正文的边界层里没找到文字：%s（文件照写了，内容是一句说明）" % "、".join(missing_text)]
    return out


def pl_safe(obj):
    """YAML 读出来的日期 → ISO 字符串(写回时不变成 YAML 的日期类型),其余原样。"""
    if isinstance(obj, dict):
        return {k: pl_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [pl_safe(v) for v in obj]
    return pl.date_text(obj)


def check(project):
    """归档核对 → 毛病列表;空 = 核过。"""
    plan, _pb = _load_doc(project, "task_plan.md")
    if not isinstance(plan, dict) or plan.get("pipeline_status") != "delivered":
        return ["还没交付（进度不是已交付）：交付之后才有归档"]
    problems = []
    dossier, dbody = _load_doc(project, "dossier.md")
    dossier = dossier if isinstance(dossier, dict) else {}
    src = _read_yaml(library(project, "sources.yaml"))
    entries, why = cited_entries(project)
    if not isinstance(src, dict) or src.get("kind") != "sources_snapshot":
        problems.append("library/sources.yaml 不在或读不出来：跑 archive.py 写一份")
    elif why is None:
        have = [str(e.get("id")) for e in src.get("entries") or [] if isinstance(e, dict)]
        want = [str(e.get("id")) for e in entries]
        if have != want:
            problems.append("library/sources.yaml 里的来源（%d 条）和成稿用到的参考文献（%d 条）对不上：重跑 archive.py" % (len(have), len(want)))
    gaps = _read_yaml(library(project, "gaps.yaml"))
    want_gaps = len([g for g in dossier.get("gaps") or [] if isinstance(g, dict)]) if isinstance(dossier.get("gaps"), list) else 0
    if not isinstance(gaps, dict) or gaps.get("kind") != "gaps_snapshot":
        problems.append("library/gaps.yaml 不在或读不出来：跑 archive.py 写一份")
    elif len(gaps.get("gaps") or []) != want_gaps:
        problems.append("library/gaps.yaml 里的资料缺口（%d 个）和资料汇编列的（%d 个）对不上：重跑 archive.py"
                        % (len(gaps.get("gaps") or []), want_gaps))
    candidates = [str(r) for r in dossier.get("rules_candidates") or []] if isinstance(dossier.get("rules_candidates"), list) else []
    readme = library(project, "rules", "README.md")
    if not os.path.isfile(readme):
        problems.append("library/rules/README.md 不在：跑 archive.py")
    else:
        with io.open(readme, encoding="utf-8", errors="replace") as f:
            m = COUNT_RE.search(f.read())
        if not m or int(m.group(1)) != len(candidates):
            problems.append("library/rules/README.md 写的条数和资料汇编的红线候选（%d 条）不一致：重跑 archive.py" % len(candidates))
    lost = [r for r in candidates if not os.path.isfile(library(project, "rules", "%s.md" % r))]
    if lost:
        problems.append("这几条红线候选没有归档：%s：重跑 archive.py" % "、".join(lost))
    scripts = yzlib.stray_scripts(project)
    if scripts:
        problems.append("项目文件夹里有助手写的脚本（%s）：项目文件夹不放脚本，删掉或挪到 .yz-tmp/%s/"
                        % ("、".join(scripts[:5]), yzlib.project_name(project)))
    return problems


def main(argv):
    return yzlib.run_main(_main, argv)


def _main(argv):
    yzlib.setup_stdout()
    ap = argparse.ArgumentParser(prog="archive.py")
    ap.add_argument("project")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv[1:])
    project = yzlib.project_dir(args.project)
    if args.check:
        problems = check(project)
        if problems:
            return yzlib.emit({"ok": False, "problems": problems,
                               "next": "照 problems 改（多数是重跑一次 archive.py），再跑 --check 到退出码 0。自己改，不用问用户。"}, 1)
        return yzlib.emit({"ok": True, "next": "归档核过了。"})
    out = run(project)
    if not out.get("ok"):
        out["next"] = "照 problems 改好再跑这条命令（自己改，不用问用户）；改不了的（比如还没交付）就照实停在这里。"
        return yzlib.emit(out, 1)
    problems = check(project)
    if problems:
        out.update({"ok": False, "problems": problems, "next": "写完了，但核对没过：照 problems 改好再跑一次。"})
        return yzlib.emit(out, 1)
    out["next"] = "归档做完、核过了。说 say 里那句话。"
    return yzlib.emit(out)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
