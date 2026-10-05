# -*- coding: utf-8 -*-
"""stamp — 正本印章工具(规格 §0 印章块;hash 由本工具计算,禁止手填)。

用法:
  python -X utf8 stamp.py <正本.md> --approve --by <人名> --quote "<用户原话≤40字>"
          [--at <ISO8601 带时区>] [--expect-hash <12 位 hash>] [--signature <签名串>]
      窗口过了才跑:写 status=approved + 时间 + 原话 + 对当前内容计算的 hash。
      --at / --expect-hash / --signature 给应用用(v1.1.0,格式见规格 §⑧-6;
      开发仓里另有 docs/signoff-format.md):应用先 --hash 取 hash、定时刻、算签名,再一次性
      交给本工具写入。**本工具不校验签名**,只原样写入、原样保留。
      给了 --signature 就必须同时给 --at 与 --expect-hash,且 quote 不得超过 40 字
      (签名签的是写进文件的那几个值,本工具不能再替它截断或取时间)。
      v1.1.1:正本上已有**带签名**的确认记录时,不带 --signature 的 --approve 拒绝
      (rc 2,不写盘)—— 否则签名被静默覆盖、不留痕迹。确有需要,显式加
      --replace-signed --why "<为什么>"。凡是替换带签名的记录(不论新记录带不带签名),
      旧记录都整条挪进新追加的 revision_log 条目的 `prev_approval`,不丢。
  python -X utf8 stamp.py <正本.md> --invalidate --why "<为什么要改>"
      增补/改动前显式作废(status=awaiting,hash 清空)——重过窗口用。
      **--why 必填**:自动追加一条 revision_log,把「为什么作废」钉在必经动作上
      (v1.0.2 实证:靠自觉写 log = 不写)。
      v1.1.0:被作废的那条确认记录(含 signature,逐字)整条挪进这条 revision_log
      的 `prev_approval`,不丢。
  python -X utf8 stamp.py <正本.md> --check
      校验:approved 且 hash 匹配 → exit 0;否则 exit 1。
  python -X utf8 stamp.py <正本.md> --hash
      打印当前内容 hash(弹窗口卡时展示前 6 位用)。
  python -X utf8 stamp.py <task_plan.md> --advance <状态>
      推进 pipeline_status(校验合法取值)并刷新 last_turn_at。
      过程字段不入印章 hash(规格 §0 v1.0.1),推进不会作废印章。
      v1.1.0:合法取值按任务计划的 `stages` 定 —— 省掉「拟定提纲」的项目,
      outlining / gate3_* 不是合法状态,gate2_approved 之后直接 drafting。

退出码:0 成功 · 1 --check 印章无效 · 2 用法错或被守卫拒绝(拒绝时不写盘)。

⛔ 纪律:只有人能批。v1.2.0 起 --approve(带 --signature)与确认之后的推进都由应用在用户
   点「确认」时跑(规格 §⑦);agent 只用 --advance 推进到 gateN_awaiting / verifying,
   以及 --invalidate、--hash、--check。伪造确认记录 = 伪造批准。
"""
import os
import re
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline_lib import (PIPELINE_STATES, content_hash, parse_stages,
                          read_md, stage_states, write_md)


def now_iso():
    # 东部时间(项目约定);夏令时粗略按月份处理
    m = datetime.now(timezone.utc).month
    offset = -4 if 3 <= m <= 11 else -5
    return datetime.now(timezone(timedelta(hours=offset))).isoformat(timespec="seconds")


def _is_int(x):
    """能当版本号用吗。⛔ bool 也排除:`isinstance(True, int)` 为真。

    `--approve` 与 `--invalidate` 两条路共用这一个判据(原来它只长在
    `--approve` 里,`--invalidate` 用裸 `int(...)`,同一份非法正本在一条路上
    被拒批、在另一条路上抛 ValueError 崩掉 —— 对抗核查 2026-09-04)。
    `--advance` 不读 version;pipeline_status 里有一份等价的内联判断。
    """
    return isinstance(x, int) and not isinstance(x, bool)


def _next_version(meta):
    """版本 +1。读不出整数就返回 None,由调用方决定怎么停。"""
    v = meta.get("version")
    return v + 1 if _is_int(v) else None


# 签名串只许这些字符:本工具不懂它的内容,但它要能原样穿过 YAML 与命令行。
# 空白、引号、`#` 之类会让「原样」变得不可靠,一律拒收。
SIGNATURE_RE = re.compile(r"^[A-Za-z0-9._~:+/=-]{1,4096}$")
HASH_RE = re.compile(r"^[0-9a-f]{12}$")


def _tz_iso(text):
    """带时区的 ISO-8601?(接受尾随 Z)"""
    if not isinstance(text, str) or not text.strip():
        return False
    s = text.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s).tzinfo is not None
    except ValueError:
        return False


def _sibling_stages(path):
    """同目录 task_plan.md 的 stages → (列表, 错误);没有 task_plan 返回 (None, None)。"""
    tp = os.path.join(os.path.dirname(os.path.abspath(path)), "task_plan.md")
    if not os.path.exists(tp):
        return None, None
    return parse_stages(read_md(tp)[0])


def main(argv):
    HELP_FLAGS = ("-h", "--help")
    if any(f in argv for f in HELP_FLAGS):
        print(__doc__)
        return 0
    if len(argv) < 3:
        print(__doc__)
        return 2
    path = argv[1]
    meta, body = read_md(path)
    if "--hash" in argv:
        print(content_hash(meta, body))
        return 0
    if "--advance" in argv:
        at = argv.index("--advance")
        if at + 1 >= len(argv):
            print("--advance 后面要跟状态名;取值:%s" % " → ".join(PIPELINE_STATES))
            return 2
        state = argv[at + 1]
        if state not in PIPELINE_STATES:
            print("非法状态 %r;取值:%s" % (state, " → ".join(PIPELINE_STATES)))
            return 2
        # v1.1.0 · 流程可调:合法取值按本项目的 stages 定。
        stages, err = parse_stages(meta)
        if err:
            print("⛔ 任务计划的 %s —— 读不出本项目有哪些环节,拒绝推进。" % err)
            print("   改法:stages 写成列表,如 [task, sources, outline, delivery];"
                  "本版只允许省掉 outline。")
            return 2
        allowed = stage_states(stages)
        if state not in allowed:
            print("⛔ 状态 %r 属于本项目流程里没有的环节(stages = %s),拒绝推进。"
                  % (state, stages))
            print("   本项目的合法状态:%s" % " → ".join(allowed))
            return 2
        meta["pipeline_status"] = state
        meta["last_turn_at"] = now_iso()
        write_md(path, meta, body)
        print("%s: pipeline_status → %s(印章不受影响)" % (path, state))
        return 0
    if "--check" in argv:
        ap = meta.get("approval")
        if ap is not None and not isinstance(ap, dict):
            # v1.1.1 · Tb2(同类):`approval: approved` 写成标量,原来 `.get` 抛
            # AttributeError(退出码同样是 1,但分不出是「无效」还是「崩了」)。
            # 第一行的形状照旧(应用按它判「不是已确认」),说明另起一行。
            print("%s: 印章无效(status=None)" % path)
            print("   approval 不是键值映射(现为 %s %s)—— 读不出确认记录"
                  % (type(ap).__name__, repr(ap)[:60]))
            return 1
        ap = ap or {}
        ok = (ap.get("status") == "approved"
              and ap.get("approved_hash") == content_hash(meta, body))
        print("%s: %s" % (path, "印章有效" if ok else
                          "印章无效(status=%r)" % ap.get("status")))
        return 0 if ok else 1
    if "--invalidate" in argv:
        why = argv[argv.index("--why") + 1] if "--why" in argv else None
        if not why:
            print("--invalidate 必须带 --why \"<为什么要改>\" —— 作废印章即留痕(v1.0.2)")
            return 2
        old = meta.get("approval")
        prev = old.get("approved_hash") if isinstance(old, dict) else None
        log = meta.get("revision_log") or []
        nxt = _next_version(meta)
        if nxt is None:
            print("⛔ frontmatter 的 version 不是整数(现为 %r)—— 作废要写一条 "
                  "revision_log,而版本号读不出来就编不出 v。" % (meta.get("version"),))
            return 2
        entry = {"v": nxt, "at": now_iso(),
                 "who": "agent", "what": "印章作废(准备修改)",
                 "why": why, "prev_hash": prev}
        # v1.1.0 · 确认记录不丢:被作废的那条(已批,或带签名)整条挪进 revision_log,
        # signature 逐字保留 —— 事后能查「作废前是谁、何时、签过什么」。
        if isinstance(old, dict) and (old.get("status") == "approved"
                                      or old.get("signature") is not None):
            entry["prev_approval"] = dict(old)
        log.append(entry)
        meta["revision_log"] = log
        meta["approval"] = {"status": "awaiting", "approved_at": None,
                            "approved_by": None, "approval_quote": None,
                            "approved_hash": None}
        write_md(path, meta, body)
        print("%s: 印章已作废 → awaiting(重过窗口);revision_log 已记 1 条" % path)
        return 0
    if "--approve" in argv:
        def opt(name):
            if name not in argv:
                return None
            i = argv.index(name)
            return argv[i + 1] if i + 1 < len(argv) else ""
        by = opt("--by")
        quote = opt("--quote")
        if not by or not quote:
            print("--approve 需要 --by 与 --quote(用户原话摘录 ≤40 字)")
            return 2
        # ── v1.1.0 · 应用落确认记录用的三个参数(docs/signoff-format.md)
        at_given = opt("--at")
        expect = opt("--expect-hash")
        signature = opt("--signature")
        # ── v1.1.1 · Tb1:替换带签名的记录要显式声明,并且说明为什么
        replace_signed = "--replace-signed" in argv
        why = opt("--why")
        if replace_signed and not (why or "").strip():
            print("⛔ --replace-signed 必须带 --why \"<为什么要替换>\" —— 替换一条带签名的确认"
                  "记录即留痕(旧记录整条挪进 revision_log)—— 拒绝落章。")
            return 2
        if at_given is not None and not _tz_iso(at_given):
            print("⛔ --at %r 不是带时区的 ISO-8601 —— 拒绝落章。" % (at_given,))
            return 2
        if expect is not None and not HASH_RE.match(expect):
            print("⛔ --expect-hash %r 不是 12 位小写十六进制 —— 拒绝落章。" % (expect,))
            return 2
        if signature is not None:
            if not SIGNATURE_RE.match(signature):
                print("⛔ --signature 含不允许的字符或长度不对(只许 A-Z a-z 0-9 . _ ~ : + / = -,"
                      "1–4096 个)—— 拒绝落章。")
                return 2
            if at_given is None or expect is None:
                print("⛔ 给了 --signature 就必须同时给 --at 与 --expect-hash:签名签的是"
                      "写进文件的时刻与 hash,本工具不能替应用取时间 —— 拒绝落章。")
                return 2
            if len(quote) > 40:
                print("⛔ 带签名时 --quote 不得超过 40 字(现 %d 字):本工具会截断,"
                      "截断后就不是被签的那句了 —— 拒绝落章。" % len(quote))
                return 2
        # v1.1.0 · 流程可调:省掉「拟定提纲」的项目不该有提纲的确认。
        if os.path.basename(path) == "outline.md" or meta.get("kind") == "outline":
            stages, err = _sibling_stages(path)
            if stages is not None and "outline" not in stages:
                print("⛔ 本项目的任务计划没有「拟定提纲」这个环节(stages = %s)——"
                      "提纲不需要确认,拒绝落章。" % (stages,))
                return 2
        # ⛔ v1.1.1 · Tb1:带签名的确认记录不许被不带签名的 --approve 静默覆盖。
        # 原来这里直接整条重写 approval:应用写的签名没了,revision_log 里也没有一个字,
        # 事后查不到「作废前谁、何时、签过什么」(T 验收)。签名在 = 带签名的记录,
        # 不看 status(与 --invalidate 挪 prev_approval 的判据相同)。
        # 本工具仍不校验签名:这里拦的是「无痕覆盖」,不是「签名真假」(那在应用里)。
        old = meta.get("approval")
        old_signed = isinstance(old, dict) and old.get("signature") is not None
        if old_signed and signature is None and not replace_signed:
            print("⛔ %s 上已有一条带签名的确认记录(%s · %s);不带 --signature 的 --approve "
                  "会把签名覆盖掉 —— 拒绝落章。"
                  % (path, old.get("approved_by"), old.get("approved_at")))
            print("   要改正本:先 --invalidate --why \"<为什么>\"(旧记录挪进 revision_log),"
                  "改完由用户在应用里重新确认。")
            print("   确有需要不带签名覆盖:加 --replace-signed --why \"<为什么>\"(旧记录同样"
                  "整条挪进 revision_log;不带签名的新记录,应用会显示「需要重新确认」)。")
            return 2
        # ⛔ 重过窗口必须升版:revision_log 里已经写着 v=N,而 frontmatter 还停在
        # N-1,说明这一轮改动没有升版 —— 于是窗口报告与正本的版本对不上,报告
        # 里那些"来源 157 条"之类的数字停在旧版而正本已经走到新版。
        # 实测:dossier 的 revision_log 三条都写 v=2,frontmatter 仍 version=1,
        # 对接窗口2 报告停在 v1/hash 0032c6/157 来源,而 dossier 已到 0c55fd/164 条。
        # ⚠️ 判据是 max(v) > version,不是「条数 > version」:同一版内改三次是
        # 正常的(会有三条 v 相同的记录),而另一份 dossier 只有一条 v=2 却停在
        # version=1 —— 用条数比会同时漏掉后者、误伤前者。
        # ⛔ 非整数一律**拒批**,不是「当没声明」。原来两处 isinstance 是过滤器:
        # `version: '1'`、缺 version 行、`v: '2'` 三种写法都让守卫整条跳过、rc=0
        # 落章(副本实测三种全中)。读不出版本时唯一安全的
        # 答案是停下,因为"读不出"与"没超版"在这里长得一模一样。
        # bool 也排除:isinstance(True, int) 为真。
        # ⛔ 先判 revision_log 的**形状**。原来这里是个过滤器:
        # `[e for e in log if isinstance(e, dict)]` —— 把整段写成 YAML 映射
        # (少一个 `-`)、写成散文字符串、写成嵌套列表,过滤后 entries 全空,
        # 于是下面两道检查都成了空转,rc=0 落章。实测:同一条 v2 声明,带 `-`
        # 拒批、少个 `-` 落章(对抗核查 2026-09-04)。读不出版本时唯一安全的
        # 答案是停下 —— 而「形状不对」正是最读不出的一种。
        raw_log = meta.get("revision_log")
        if raw_log is not None and not isinstance(raw_log, list):
            print("⛔ revision_log 不是列表(现为 %s)—— 版本声明读不出来,拒绝落章。"
                  % type(raw_log).__name__)
            print("   改法:每条前面加 `- `,写成 `- {v: 2, at: ..., what: ...}` 这样的列表项。")
            return 2
        entries = raw_log or []
        bad_shape = [e for e in entries if not isinstance(e, dict)]
        if bad_shape:
            print("⛔ revision_log 里有 %d 条不是键值对(如 %r)—— 同上,拒绝落章。"
                  % (len(bad_shape), bad_shape[0]))
            return 2
        version = meta.get("version")
        if not _is_int(version):
            print("⛔ frontmatter 的 version 不是整数(现为 %r)—— 版本读不出来就"
                  "判断不了有没有升版,拒绝落章。" % (version,))
            return 2
        bad = [e.get("v") for e in entries if "v" in e and not _is_int(e.get("v"))]
        if bad:
            print("⛔ revision_log 里有非整数的 v(%s)—— 同上,拒绝落章。"
                  % ", ".join(repr(b) for b in bad))
            return 2
        claimed = [e.get("v") for e in entries if "v" in e]
        if claimed and max(claimed) > version:
            print("⛔ revision_log 写到 v%d,而 frontmatter version=%d —— "
                  "改了正本要先升版,再落章。" % (max(claimed), version))
            print("   改法:frontmatter 的 version 改成 %d(与那一条的 v 对齐),再请用户确认 ——"
                  "v1.2.0 起落章由应用在用户点「确认」时做。" % max(claimed))
            return 2
        current = content_hash(meta, body)
        if expect is not None and expect != current:
            print("⛔ --expect-hash %s ≠ 当前内容 %s —— 应用算签名之后正本又变了,拒绝落章。"
                  % (expect, current))
            print("   改法:重新 --hash、重新请用户确认这一版。")
            return 2
        if old_signed:
            # v1.1.1 · Tb1:被替换的带签名记录整条、逐字挪进 revision_log(形状同 --invalidate
            # 的 prev_approval)。v 记当前版本:正文没因此改动,不升版,也就不会绊倒下一次
            # 落章的版本守卫(max(v) > version 才拒)。who 按新记录有没有签名填
            # (带 = app,不带 = agent)—— 本工具不校验签名,这只是照实记下写的是哪一种。
            entry = {"v": version, "at": now_iso(),
                     "who": "app" if signature is not None else "agent",
                     "what": ("带签名的确认记录被新的确认记录替换(%s)"
                              % ("新记录带签名" if signature is not None
                                 else "--replace-signed,新记录不带签名")),
                     "why": why.strip() if (why or "").strip() else None,
                     "prev_hash": old.get("approved_hash"),
                     "prev_approval": dict(old)}
            meta["revision_log"] = entries + [entry]
        meta["approval"] = {"status": "approved",
                            "approved_at": at_given if at_given is not None else now_iso(),
                            "approved_by": by, "approval_quote": quote[:40],
                            "approved_hash": current}
        if signature is not None:
            meta["approval"]["signature"] = signature
        write_md(path, meta, body)
        print("%s: 已落印章(%s · %s%s)" % (path, by, meta["approval"]["approved_hash"],
                                        " · 带签名" if signature is not None else ""))
        if old_signed:
            print("   被替换的那条带签名确认记录已整条挪进 revision_log(v%d 的 prev_approval)"
                  % version)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
