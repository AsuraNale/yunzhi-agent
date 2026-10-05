# -*- coding: utf-8 -*-
"""liveness — 交付前探测参考文献清单里每个链接还能不能打开,照实记五态(规格 §⑤ liveness)。

用法(在工作区根目录下跑;$PY 见 AGENTS.md):
  $PY agent-tools/liveness.py "<项目名>" [--from <地区码,如 CN-BJ>] [--timeout 15]
      探测每一条。
  $PY agent-tools/liveness.py mark "<项目名>" --id R001 [--id R002 ...]
      这几条是你这一轮用网页工具亲手打开、读过的页面:记成能打开(带日期)。没打开过的不许记。
      第八轮:只收有读过证据的 —— 资料卡片上这个网址的来源取到了(fetch_state: ok)、写了原文摘录(excerpt);
      没有就拒(没读过的不能记成读过)。

第八轮:成稿里引用了、探测却没打开的条目(打不开、连不上、不能联网没检查),交付前检查(cite_check ⑦)会拦,
探测完 next 里就先说是哪几条:读过的用 mark 记下(不用重新生成成稿),没读过的改掉成稿里的引用。

每条 entries[] 写 liveness: {state, probed_at, probed_from, detail?, http_status?, unchecked?, via?}:
  2xx / 3xx → ok;401 / 403 / 429 → blocked(打不开:拒绝访问);其他 4xx → failed(detail 4xx);
  5xx → failed(detail 5xx);个别连不上(超时、域名解析不了)→ failed(detail net,不上成稿 ⚠)。
  **一条都连不上 = 这个环境不能联网**(10-03 实测:桌面版没认工作区的联网设置,沙盒里不能联网,而助手用 Codex 自己的
  网页工具照样打得开网页)→ 不算链接坏了:每条记 failed + detail net + unchecked: true,对用户说「未能自动检查」;
  助手用网页工具打开过的,用 mark 记成 ok(via: web-tool,带日期)。之前 mark 过的条目,不能联网时探测不会盖掉它。
探测完要重新生成两种成稿(生成脚本会给 4xx / 5xx 的条目加 ⚠),而且都放在独立复核之前。
第四轮:资料卡片上记过打不开 / 出错(从没读到)的来源,探测发现现在打得开了 → 输出 retry,next 叫助手写进成稿之前先回去真的读一遍。
probed_from 取 --from,其次环境变量 YUNZHI_PROBED_FROM,都没有就写 unknown(不猜)。
"""
import argparse
import io
import os
import socket
import ssl
import sys
import urllib.error
import urllib.request

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yaml  # noqa: E402
import yzlib  # noqa: E402
from yzlib import UsageError  # noqa: E402

UA = "Mozilla/5.0 (yunzhi-agent liveness check)"
UNCHECKED_SAID = "未能自动检查（这个环境不能联网）"
WEB_TOOL = "web-tool"


def probe(url, timeout):
    """→ (state, detail, http_status)。"""
    last = None
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, method=method, headers={"User-Agent": UA, "Range": "bytes=0-0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return "ok", None, resp.status
        except urllib.error.HTTPError as e:
            last = e.code
            if method == "HEAD" and e.code in (400, 403, 405, 501):
                continue   # 有的站不认 HEAD,换 GET 再试一次
            break
        except (urllib.error.URLError, socket.timeout, ssl.SSLError, ConnectionError, OSError, ValueError):
            return "failed", "net", None
    if last in (401, 403, 429):
        return "blocked", None, last
    if last is not None and 500 <= last < 600:
        return "failed", "5xx", last
    if last is not None:
        return "failed", "4xx", last
    return "failed", "net", None


def load(project):
    path = os.path.join(project, "references.yaml")
    if not os.path.isfile(path):
        raise UsageError("项目里还没有参考文献清单(references.yaml):先跑 refs_add.py")
    with io.open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    entries = data.get("entries") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise UsageError("references.yaml 的 entries 不是列表")
    return path, data, entries


def save(path, data):
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def run(project, where, timeout):
    path, data, entries = load(project)
    results = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        url = e.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            results.append((e, ("gap", None, None)))
        else:
            results.append((e, probe(url, timeout)))
    web = [r for r in results if r[1][0] != "gap"]
    offline = bool(web) and all(r[1] == ("failed", "net", None) for r in web)
    counts, kept = {}, 0
    for e, (state, detail, status) in results:
        old = e.get("liveness") if isinstance(e.get("liveness"), dict) else {}
        if offline and old.get("via") == WEB_TOOL and state != "gap":
            kept += 1          # 不能联网时,助手用网页工具打开过的那条照旧算能打开
            counts["ok/web-tool"] = counts.get("ok/web-tool", 0) + 1
            continue
        rec = {"state": state, "probed_at": yzlib.now_iso(), "probed_from": where}
        if detail:
            rec["detail"] = detail
        if status:
            rec["http_status"] = status
        if offline and state != "gap":
            rec["unchecked"] = True
            rec["note"] = UNCHECKED_SAID
        e["liveness"] = rec
        key = "unchecked" if rec.get("unchecked") else state + ("/" + detail if detail else "")
        counts[key] = counts.get(key, 0) + 1
    save(path, data)
    out = {"ok": True, "entries": len(entries), "counts": counts, "offline": offline, "probed_from": where}
    # 第四轮:当初没读到的来源(资料卡片上记的是打不开 / 出错),现在链接打得开了 —— 写进成稿之前先回去真的读一遍
    unread = never_read_urls(project)
    retry = [{"id": e.get("id"), "url": e.get("url")} for e, (state, _d, _s) in results
             if state == "ok" and isinstance(e.get("url"), str) and e["url"].strip() in unread]
    if retry:
        out["retry"] = retry
    if offline:
        out["say"] = ("这次未能自动检查链接还能不能打开（这个环境不能联网）%s。"
                      % ("；你在这一轮看过的 %d 个页面已经记下能打开" % kept if kept else ""))
        out["next"] = ("这个环境不能联网，没有一条链接探测得到：都记成「未能自动检查」，不算链接坏了。"
                       "这一轮你用网页工具亲手打开、读过的页面，跑 liveness.py mark \"<项目名>\" --id <编号> 记成能打开（带日期）；"
                       "没打开过的不许记。然后重新生成两种成稿，再跑交付前检查。把 say 那句告诉用户。")
    else:
        out["next"] = "探测完了。接着重新生成网页版和 Word 版（生成脚本会给打不开的条目加 ⚠），再跑交付前检查。"
    if retry:
        out["next"] = ("这几个来源当初没读到（资料卡片上记的是打不开或出错），现在链接打得开了：%s。写进成稿之前，先用网页工具"
                       "真的打开、读一遍：读到了，就跑 fetches.py add \"<项目名>\" --url \"<网址>\" --state ok 记下重新取到了，"
                       "把资料卡片上这个来源改成 fetch_state: ok、写原文摘录、as_of 写今天，再跑 refs_add.py 更新参考文献清单"
                       "（卡上的数和结论有变化的，资料汇编要重新确认）；还是读不到，就照旧，不要引用它。然后再接着下面这一步：%s"
                       % ("、".join("%s %s" % (r["id"], r["url"]) for r in retry), out["next"]))
    # 第八轮:成稿里引用了、探测却没打开的(打不开、连不上、不能联网没检查)—— 交付前检查会拦,这里先说
    unreached = cited_unreached(project, entries)
    if unreached:
        out["cited_unreached"] = [{"id": i, "url": u, "read": r} for i, u, r in unreached]
        done = [i for i, _u, r in unreached if r]
        never = [i for i, _u, r in unreached if not r]
        said = []
        if done:
            said.append("%s 成稿里引用了、探测没打开，资料卡片上有原文摘录（读过）：跑 $PY \"agent-tools/liveness.py\" mark \"%s\" %s "
                        "记下读过（不用重新生成成稿）" % ("、".join(done), yzlib.project_name(project),
                                                 " ".join("--id %s" % i for i in done)))
        if never:
            said.append("%s 成稿里引用了、探测没打开，资料卡片上也没有读过它的证据（取到了、写了原文摘录）：不能记成读过 —— "
                        "用网页工具真的读一遍（读到了照资料卡片的做法补上原文摘录；以前记过没取到的先 fetches.py add … --state ok），"
                        "读不到就改掉成稿里的这处引用" % "、".join(never))
        out["next"] = "；".join(said) + "。然后：" + out["next"]
    return out


def _norm(url):
    from refs_add import norm_url_key
    return norm_url_key(url) if isinstance(url, str) and url.strip() else None


def read_urls(project):
    """读过的来源的网址(归一后):资料卡片(没弃用的)上这个来源取到了(fetch_state: ok)、写了原文摘录(excerpt)。
    第八轮:这就是「读过」的证据 —— mark 只收有它的。"""
    d = os.path.join(project, "cards")
    if not os.path.isdir(d):
        return set()
    from card_check import load_cards
    try:
        cards = load_cards(d)
    except Exception:  # noqa: BLE001  卡片读不出来:card_check 会报;这里当没有证据
        return set()
    out = set()
    for c, _fn in cards:
        if not isinstance(c, dict) or c.get("deprecated"):
            continue
        for s in c.get("sources") or []:
            if (isinstance(s, dict) and s.get("fetch_state") == "ok" and isinstance(s.get("excerpt"), str)
                    and s["excerpt"].strip() and _norm(s.get("url"))):
                out.add(_norm(s.get("url")))
    return out


def cited_unreached(project, entries):
    """成稿里引用了、探测没打开的条目 → [(编号, 网址, 读过没有)]。判法同 cite_check ⑦:打不开(blocked)、
    连不上(failed 带 net,含不能联网时的「未能自动检查」);4xx / 5xx 是真的失效了,成稿上带 ⚠,不在这里。"""
    import glob
    import pipeline_lib as pl
    cited = set()
    for path in sorted(glob.glob(os.path.join(project, "drafts", "*.md"))):
        try:
            cited.update(e.get("id") for _no, e in pl.number_entries(pl.read_md(path)[1], entries)[1])
        except Exception:  # noqa: BLE001  底稿读不出来:cite_check 会报
            continue
    seen = read_urls(project)
    out = []
    for e in entries:
        if not isinstance(e, dict) or e.get("id") not in cited:
            continue
        lv = e.get("liveness") if isinstance(e.get("liveness"), dict) else {}
        if lv.get("state") == "blocked" or (lv.get("state") == "failed" and lv.get("detail") == "net"):
            out.append((e.get("id"), e.get("url"), _norm(e.get("url")) in seen))
    return out


def never_read_urls(project):
    """资料卡片上记过、却从没读到的网址:有卡记成打不开 / 出错,没有一张卡记成 ok。"""
    d = os.path.join(project, "cards")
    if not os.path.isdir(d):
        return set()
    from card_check import load_cards
    try:
        cards = load_cards(d)
    except Exception:  # noqa: BLE001  卡片读不出来:card_check 会报,这里不挡探测
        return set()
    states = {}
    for c, _fn in cards:
        if not isinstance(c, dict) or c.get("deprecated"):
            continue
        for s in c.get("sources") or []:
            if isinstance(s, dict) and isinstance(s.get("url"), str) and s["url"].strip():
                states.setdefault(s["url"].strip(), set()).add(s.get("fetch_state"))
    return {u for u, st in states.items() if "ok" not in st and st & {"blocked", "failed"}}


def mark(project, ids):
    path, data, entries = load(project)
    want = {i.strip() for i in ids if i and i.strip()}
    if not want:
        raise UsageError("要写 --id（参考文献清单里的编号，如 R003）")
    found, unread = set(), []
    seen = read_urls(project)
    for e in entries:
        if isinstance(e, dict) and str(e.get("id")) in want:
            if not (isinstance(e.get("url"), str) and e["url"].startswith(("http://", "https://"))):
                raise UsageError("%s 没有网址，不用记" % e.get("id"))
            found.add(str(e.get("id")))
            if _norm(e.get("url")) not in seen:
                unread.append(str(e.get("id")))
    missing = sorted(want - found)
    if missing:
        raise UsageError("参考文献清单里没有这几个编号：%s" % "、".join(missing))
    if unread:
        # 第八轮:没读过的不能记成读过 —— 读过的证据是资料卡片上这个来源取到了、写了原文摘录
        raise UsageError("%s 在资料卡片上没有读过的证据（这个网址的来源取到了 fetch_state: ok、写了原文摘录 excerpt）：没读过的不能记成读过。"
                         "用网页工具真的读一遍：读到了，照资料卡片的做法补上原文摘录（以前记过没取到的，先跑 fetches.py add … --state ok），"
                         "再来记；读不到，就改掉成稿里引用它的地方。" % "、".join(sorted(unread)))
    for e in entries:
        if isinstance(e, dict) and str(e.get("id")) in found:
            e["liveness"] = {"state": "ok", "probed_at": yzlib.now_iso(), "probed_from": "助手用网页工具打开", "via": WEB_TOOL}
    save(path, data)
    return {"ok": True, "marked": sorted(found),
            "next": "记下了：这几条是你用网页工具打开过的，算能打开。接着重新生成两种成稿，再跑交付前检查。"}


def main(argv):
    return yzlib.run_main(_main, argv)


def _main(argv):
    yzlib.setup_stdout()
    if len(argv) > 1 and argv[1] == "mark":
        ap = argparse.ArgumentParser(prog="liveness.py mark")
        ap.add_argument("project")
        ap.add_argument("--id", action="append", default=[])
        args = ap.parse_args(argv[2:])
        return yzlib.emit(mark(yzlib.project_dir(args.project), args.id))
    ap = argparse.ArgumentParser(prog="liveness.py")
    ap.add_argument("project")
    ap.add_argument("--from", dest="probed_from")
    ap.add_argument("--timeout", type=float, default=15)
    args = ap.parse_args(argv[1:])
    project = yzlib.project_dir(args.project)
    where = args.probed_from or os.environ.get("YUNZHI_PROBED_FROM") or "unknown"
    return yzlib.emit(run(project, where, args.timeout))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
