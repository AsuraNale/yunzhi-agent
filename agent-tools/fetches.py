# -*- coding: utf-8 -*-
"""fetches — 取资料没取到的记录(打不开、出错、查了没内容)。

用法(在工作区根目录下跑;$PY 见 AGENTS.md):
  $PY agent-tools/fetches.py add "<项目名>" --url "<网址>" --state <blocked|failed|empty|ok> [--what "<想取什么>"]
      每次网页打不开(403 之类,blocked)、出错(超时、断网、5xx,failed)、打开了但没有要的内容(empty),当场记一条
      → projects/<项目名>/records/fetches.jsonl。
      ok 只用在一种时候:这个网址以前记过没取到,后来重新打开、真的读到了(第四轮)。
  $PY agent-tools/fetches.py list "<项目名>"
      列出记过的几条和合计。
  $PY agent-tools/fetches.py save "<项目名>" --url "<网址>" [--title "<资料标题>"] [--timeout 30] [--max-mb 50]
      第八轮:脚本自己用 Python 下载这个网址(超时、大小上限、只收常见类型:网页、PDF、Word、Excel、纯文字、表格、常见图片),
      存原件、转文字。没下载成照五态记进 records/fetches.jsonl(打不开 blocked:401 / 403 / 429;出错 failed:
      别的 4xx、5xx、超时、连不上、超过大小上限;查了但没有内容 empty:打开了、一个字节都没有)。
      10-04 实测:Windows PowerShell 5.1 的 Invoke-WebRequest 两次都报「基础连接已经关闭」(它默认的 TLS 设置),
      同一个网站 Python 拿到了 200 —— 所以不用 Invoke-WebRequest / curl 下载,用这一条。
      第九轮:下载到的网页是反爬虫的验证页(很小、几乎没有正文、带 EO_Bot_Ssid / Just a moment / 请开启 JavaScript
      这类标记,或者只有一段写 cookie 再跳转的脚本)→ 不存原件,记成打不开(blocked),next 叫助手用网页工具打开读。
      --file 交来的网页也一样核。
  $PY agent-tools/fetches.py save "<项目名>" --file "<下载好的文件>" --url "<网址>" [--title "<资料标题>"]
      第五轮 M4:从网上取来的一份资料(网页、PDF、Word、Excel、图……)存进 projects/<项目名>/fetched/originals/,
      顺手转成文字放 fetched/converted/(和用户材料同一套判法:已转成文字 / 没能读出文字 / 没转成 / 这种格式不能转),
      清单 fetched/index.md 加一行。inputs/ 只放用户自己的材料;--file 在项目的 inputs/ 里(放错了地方)就挪过来。
      已经有文件(用户发来的、别的工具存下的)时用这一条;从网上下载用上面那条 --url。

第三轮实测:403 打不开的几次没留下任何痕迹;资料汇编里还把打不开的缺口写成了「没有这项数据」。
现在:记过的网址,出资料汇编卡时要在某张资料卡片的来源里找得到(fetch_state 照实写);资料汇编卡上列出没取到几次。
第四轮:同一种情况只用一个名字 —— 网页打开了、里面没有要的数据,在这里、在资料卡片的来源上、在资料缺口卡和资料汇编的
缺口里,一律写 empty(查了但没有内容);gap(没有这项数据)只留给查实了这项数据根本没有公布的情况。
记过打不开 / 出错的网址,卡上不许改写成 ok,除非这里后来记了一次 ok(重新取到了)。
"""
import argparse
import io
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yzlib  # noqa: E402
from yzlib import UsageError  # noqa: E402

FAILED_STATES = ("blocked", "failed", "empty")
STATES = FAILED_STATES + ("ok",)
SAID = {"blocked": "打不开", "failed": "没取到（出错）", "empty": "查了但没有内容", "ok": "后来重新取到了"}


def log_path(project):
    return os.path.join(project, "records", "fetches.jsonl")


def entries(project):
    return yzlib.read_jsonl(log_path(project))


def latest_states(project):
    """每个记过的网址最后一次记的情况 → {网址: 状态}。"""
    out = {}
    for e in entries(project):
        url = str(e.get("url") or "").strip()
        if url and e.get("state") in STATES:
            out[url] = e["state"]
    return out


def source_counts(project):
    """没取到的来源各几个 → {blocked: n, failed: n, empty: n}(第八轮:按「网址 + 情况」去重 —— 同一个网址同一种情况
    记了几次都算一个来源;10-04 那一轮 2 个网址各记了 3 次,卡上写成了「出错 6 次」)。后来重新取到了的网址
    (最后一次记的是 ok)不算。一个都没有 → {}。资料汇编卡与 list 都用这一份。"""
    latest = latest_states(project)
    seen = set()
    for e in entries(project):
        url, st = str(e.get("url") or "").strip(), e.get("state")
        if url and st in FAILED_STATES and latest.get(url) != "ok":
            seen.add((url, st))
    counts = {}
    for _url, st in seen:
        counts[st] = counts.get(st, 0) + 1
    return counts


def add(project, url, state, what=None):
    if state not in STATES:
        raise UsageError("--state 只能是 %s" % " / ".join(STATES))
    url = (url or "").strip()
    if not url:
        raise UsageError("要写 --url（哪个网址没取到）")
    rec = {"at": yzlib.now_iso(), "url": url, "state": state}
    if what and what.strip():
        rec["what"] = what.strip()
    yzlib.append_jsonl(log_path(project), rec)
    return rec


def saved_path(project):
    return os.path.join(project, "records", "fetched.jsonl")


def _inside(path, folder):
    try:
        a = os.path.normcase(os.path.abspath(path))
        b = os.path.normcase(os.path.abspath(folder))
        return os.path.commonpath([a, b]) == b
    except ValueError:
        return False


def write_index(project):
    """fetched/index.md:取来的资料清单(由 records/fetched.jsonl 生成,每次 save 之后重写)。"""
    rows = yzlib.read_jsonl(saved_path(project))
    lines = ["# 取来的资料", "",
             "助手从网上取来的资料（不是用户给的；用户的材料在 inputs/）。原件在 fetched/originals/，转成的文字在 fetched/converted/。", ""]
    if not rows:
        lines.append("（还没有）")
    for r in rows:
        outs = "（%s）" % "、".join("`%s`" % o for o in r.get("outputs") or []) if r.get("outputs") else ""
        title = "《%s》 " % r["title"] if r.get("title") else ""
        lines.append("- %s%s · `%s` · %s%s" % (title, r.get("url"), r.get("stored"), r.get("note"), outs))
    yzlib.write_atomic(os.path.join(project, yzlib.FETCHED, "index.md"), "\n".join(lines) + "\n")


def save(project, file, url, title=None):
    """存一份取来的资料 → (记录, 是不是从 inputs/ 挪过来的)。
    第九轮:网页是反爬虫的验证页(challenge_reason)→ 不存,抛 Fetched(blocked)由调用方照五态记下。"""
    import shutil
    import projects as projects_mod
    src = os.path.abspath(file or "")
    if not file or not os.path.isfile(src):
        raise UsageError("找不到 --file 这个文件（%s）：先把资料下载到 .yz-tmp/%s/ 里，再把那个文件的路径交给 --file"
                         % (file, yzlib.project_name(project)))
    url = (url or "").strip()
    if not url:
        raise UsageError("要写 --url（这份资料是从哪个网址取来的）")
    if os.path.splitext(src)[1].lower() in (".html", ".htm") and os.path.getsize(src) <= CHALLENGE_MAX_BYTES:
        with io.open(src, "rb") as f:
            reason = challenge_reason(f.read())
        if reason:
            raise Fetched("blocked", reason, challenge=True)
    orig_dir = os.path.join(project, yzlib.FETCHED, "originals")
    conv_dir = os.path.join(project, yzlib.FETCHED, "converted")
    os.makedirs(orig_dir, exist_ok=True)
    os.makedirs(conv_dir, exist_ok=True)
    stored = projects_mod.unique_in(orig_dir, projects_mod.safe_file_name(os.path.basename(src)))
    dest = os.path.join(orig_dir, stored)
    moved = _inside(src, os.path.join(project, "inputs"))
    if moved:
        shutil.move(src, dest)          # 放错在 inputs/ 里的,挪到这里来(inputs/ 只放用户自己的材料)
    else:
        shutil.copy2(src, dest)
    rel = "%s/originals/%s" % (yzlib.FETCHED, stored)
    conv = projects_mod.convert_material(project, rel, out_dir=conv_dir)
    rec = {"at": yzlib.now_iso(), "url": url, "stored": rel, "state": conv["state"], "note": conv["note"],
           "outputs": conv["outputs"]}
    if title and title.strip():
        rec["title"] = title.strip()
    yzlib.append_jsonl(saved_path(project), rec)
    write_index(project)
    return rec, moved


# ---- 第八轮:脚本自己下载(Python 的 urllib;不用 Invoke-WebRequest / curl) ----

DOWNLOAD_TIMEOUT = 30
DOWNLOAD_MAX_MB = 50
DOWNLOAD_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) yunzhi-agent fetch"
# 只收常见类型:Content-Type → 存成的扩展名
DOWNLOAD_TYPES = {
    "text/html": ".html", "application/xhtml+xml": ".html",
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/msword": ".doc", "application/vnd.ms-excel": ".xls",
    "text/plain": ".txt", "text/csv": ".csv",
    "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp",
}
# 网址里的扩展名和类型是同一种时,照网址的写(.htm、.jpeg)
SAME_KIND = {".html": (".html", ".htm"), ".jpg": (".jpg", ".jpeg")}
KNOWN_EXTS = {".html", ".htm", ".pdf", ".docx", ".xlsx", ".doc", ".xls", ".txt", ".csv", ".png", ".jpg", ".jpeg", ".gif", ".webp"}
# 服务器不说是什么类型(或者只说是一串字节)时,按开头几个字节认
GENERIC_TYPES = ("", "application/octet-stream", "binary/octet-stream", "application/x-download", "application/force-download",
                 "application/download", "application/x-msdownload")


class Fetched(Exception):
    """下载没成:state 是五态里的一种(blocked / failed / empty),detail 说具体是什么(给助手看)。
    challenge:网站给的是反爬虫的验证页(第九轮)。"""
    def __init__(self, state, detail, status=None, challenge=False):
        super().__init__(detail)
        self.state, self.detail, self.status, self.challenge = state, detail, status, challenge


# ---- 第九轮:反爬虫的验证页(要浏览器跑脚本才放行),不是正文 ----
# 10-04 第二次全程试跑:人社部的一页下载下来是 987 字节的 JS cookie 验证页(带 EO_Bot_Ssid),被存成原件、记成「查了但没有内容」,
# 资料缺口卡和计数都以为部门那页没数据。这种页记成「打不开」(blocked),不存原件,next 叫助手用网页工具打开读。
CHALLENGE_MAX_BYTES = 100 * 1024       # 很小
CHALLENGE_TEXT_MAX = 200               # 可见文字很少(去掉脚本、样式、标签和空白之后的字数)
CHALLENGE_BARE_TEXT = 50               # 几乎没有可见文字:只剩一段写 cookie 再跳转 / 重新载入的脚本,也算
# (去掉空白、转成小写之后找的样子, 报给助手看的写法)
CHALLENGE_MARKERS = (
    ("eo_bot_ssid", "EO_Bot_Ssid"), ("__tst_status", "__tst_status"),
    ("cdn-cgi/challenge", "cdn-cgi/challenge"), ("challenge-platform", "challenge-platform"), ("_cf_chl", "_cf_chl"),
    ("cf-browser-verification", "cf-browser-verification"), ("justamoment", "Just a moment"),
    ("checkingyourbrowser", "Checking your browser"), ("checkingifthesiteconnectionissecure", "Checking if the site connection is secure"),
    ("enablejavascriptandcookies", "Enable JavaScript and cookies"), ("ddosprotectionby", "DDoS protection by"),
    ("请开启javascript", "请开启 JavaScript"), ("请启用javascript", "请启用 JavaScript"), ("请打开javascript", "请打开 JavaScript"),
    ("开启浏览器的javascript", "开启浏览器的 JavaScript"),
)


def visible_text(html_text):
    """网页里看得见的字:去掉脚本、样式、标签和全部空白(实体照常解开)。"""
    import html as html_mod
    import re
    t = re.sub(r"<(script|style|noscript)\b[^>]*>.*?</\1\s*>", " ", html_text, flags=re.S | re.I)
    t = re.sub(r"<!--.*?-->", " ", t, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", "", html_mod.unescape(t))


def challenge_reason(data):
    """一份网页(字节)是不是反爬虫的验证页 → 给助手看的一句说明(带认出的标记);不是 → None。
    判法:很小(≤100 KB)、可见文字很少(≤200 字),而且带验证页的标记(EO_Bot_Ssid、cdn-cgi/challenge、Just a moment、
    Checking your browser、请开启 JavaScript……);或者可见文字几乎没有(≤50 字)、只有一段写 cookie 再跳转 / 重新载入的脚本
    (10-04 那一页的脚本是混淆过的,字面上没有 document.cookie=,但有 cookie 和 location.href)。"""
    import re
    if not data or len(data) > CHALLENGE_MAX_BYTES:
        return None
    text = data.decode("utf-8", errors="replace")
    seen = visible_text(text)
    if len(seen) > CHALLENGE_TEXT_MAX:
        return None
    flat = re.sub(r"\s+", "", text).lower()
    for needle, shown in CHALLENGE_MARKERS:
        if needle in flat:
            return "网站给的是要浏览器跑脚本才放行的验证页（反爬虫），不是正文（认出：%s）" % shown
    scripts = " ".join(m.group(0) for m in re.finditer(r"<script\b[^>]*>.*?</script\s*>", text, flags=re.S | re.I)).lower()
    if len(seen) <= CHALLENGE_BARE_TEXT and "cookie" in scripts and any(k in scripts for k in ("location", "settimeout", "reload")):
        return "网站给的是要浏览器跑脚本才放行的验证页（反爬虫），不是正文（认出：没有正文，只有一段写 cookie 再跳转的脚本）"
    return None


class NotAccepted(Exception):
    """下载到了,但这种类型不收。"""


def _url_name(url):
    from urllib.parse import unquote, urlsplit
    path = unquote(urlsplit(url).path or "")
    return os.path.basename(path.rstrip("/")) or "page"


def _disposition_name(header):
    import re
    from urllib.parse import unquote
    if not header:
        return None
    m = re.search(r"filename\*\s*=\s*[^']*''([^;]+)", header, re.I) or re.search(r'filename\s*=\s*"?([^";]+)"?', header, re.I)
    return os.path.basename(unquote(m.group(1).strip())) if m else None


def _sniff(head, url_ext):
    """不说类型的下载 → 按开头几个字节认扩展名;认不出 → None。"""
    if head.startswith(b"%PDF"):
        return ".pdf"
    if head.startswith(b"\x89PNG"):
        return ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if head.startswith(b"PK\x03\x04"):
        return url_ext if url_ext in (".docx", ".xlsx") else "zip"
    low = head[:512].lstrip().lower()
    if low.startswith(b"<!doctype html") or low.startswith(b"<html"):
        return ".html"
    return url_ext if url_ext in KNOWN_EXTS and url_ext not in (".docx", ".xlsx") else None


def _zip_kind(data):
    """一个 zip:Word(word/document.xml)→ .docx;Excel(xl/workbook.xml)→ .xlsx;别的 → None。"""
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = set(z.namelist())
    except zipfile.BadZipFile:
        return None
    if "word/document.xml" in names:
        return ".docx"
    if "xl/workbook.xml" in names:
        return ".xlsx"
    return None


def download(url, timeout=DOWNLOAD_TIMEOUT, max_mb=DOWNLOAD_MAX_MB):
    """→ (字节, 文件名, {status, content_type, bytes});没下载成 → Fetched;类型不收 → NotAccepted。"""
    import http.client
    import socket
    import ssl
    import urllib.error
    import urllib.request
    cap = int(max_mb * 1024 * 1024)
    req = urllib.request.Request(url, headers={"User-Agent": DOWNLOAD_UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            length = resp.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > cap:
                raise Fetched("failed", "文件有 %.1f MB，超过大小上限 %d MB" % (int(length) / 1048576.0, max_mb), status)
            chunks, size = [], 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > cap:
                    raise Fetched("failed", "下载超过大小上限 %d MB，停了" % max_mb, status)
                chunks.append(chunk)
            data = b"".join(chunks)
            final_url = resp.geturl() or url
            name = _disposition_name(resp.headers.get("Content-Disposition")) or _url_name(final_url)
    except Fetched:
        raise
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 429):
            raise Fetched("blocked", "网站拒绝访问（HTTP %d）" % e.code, e.code)
        raise Fetched("failed", "HTTP %d" % e.code, e.code)
    except (urllib.error.URLError, socket.timeout, ssl.SSLError, ConnectionError, OSError, ValueError,
            http.client.HTTPException) as e:
        # 连不上、超时、证书不对、没传完(IncompleteRead)……都是「没取到(出错)」
        raise Fetched("failed", "连不上（%s）" % type(e).__name__)
    if not data:
        raise Fetched("empty", "打开了，但一个字节都没有", status)
    stem, url_ext = os.path.splitext(name)
    url_ext = url_ext.lower()
    ext = DOWNLOAD_TYPES.get(ctype)
    if ext is None and ctype in GENERIC_TYPES:
        ext = _sniff(data[:1024], url_ext)
        if ext == "zip":
            ext = _zip_kind(data)
    if ext is None:
        raise NotAccepted("这种类型不收（%s）" % (ctype or "服务器没说是什么类型"))
    if ext == ".html":
        reason = challenge_reason(data)
        if reason:
            raise Fetched("blocked", reason, status, challenge=True)
    if url_ext not in SAME_KIND.get(ext, (ext,)):
        name = (stem if url_ext in KNOWN_EXTS or not url_ext else name) + ext
    return data, name, {"status": status, "content_type": ctype, "bytes": len(data)}


def save_from_url(project, url, title=None, timeout=DOWNLOAD_TIMEOUT, max_mb=DOWNLOAD_MAX_MB):
    """下载、存原件、转文字 → 给助手的 JSON。没下载成照五态记进 records/fetches.jsonl。"""
    import shutil
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise UsageError("--url 要是 http:// 或 https:// 开头的网址")
    name = yzlib.project_name(project)
    try:
        data, filename, info = download(url, timeout, max_mb)
    except Fetched as f:
        return not_saved_reply(project, url, f)
    except NotAccepted as n:
        return {"ok": True, "downloaded": False, "fetch_state": None, "detail": str(n),
                "next": ("下载到了，但%s：没存。网页内容用网页工具直接读（读到的网页不用存）；读不了，就照实记 $PY \"agent-tools/fetches.py\" "
                         "add \"%s\" --url \"%s\" --state failed --what \"类型不收\"。" % (str(n), name, url))}
    tmp = yzlib.work_tmp("download")
    try:
        path = os.path.join(tmp, filename)
        with io.open(path, "wb") as f:
            f.write(data)
        rec, _moved = save(project, path, url, title)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    rec["download"] = info
    return {"ok": True, "downloaded": True, "saved": rec, "next": saved_next(rec, False, downloaded=True)}


def not_saved_reply(project, url, f):
    """没存下来(下载没成,或者网站给的是验证页)→ 照五态记进 records/fetches.jsonl,返回给助手的 JSON。"""
    name = yzlib.project_name(project)
    rec = add(project, url, f.state, ("%s" if f.challenge else "下载：%s") % f.detail)
    if f.challenge:
        nxt = ("%s：已经记成打不开（blocked），没存原件。用网页工具打开这个网址读：读到了，跑 $PY \"agent-tools/fetches.py\" add "
               "\"%s\" --url \"%s\" --state ok 记下重新取到了，再照常做卡（用网页工具读到的网页不用存）；读不到，资料卡片上这个来源照实写 "
               "blocked（打不开，不是查了没内容），要的数据别处也找不到就写一张资料缺口卡。不要换成 Invoke-WebRequest 或 curl 再下一遍。"
               % (f.detail, name, url))
    else:
        nxt = ("没下载成（%s：%s），已经照实记下了。能用网页工具打开就用网页工具读：读到了，跑 $PY \"agent-tools/fetches.py\" add "
               "\"%s\" --url \"%s\" --state ok 记下重新取到了，再照常做卡；读不到，就把这个网址记进相关资料卡片的来源"
               "（fetch_state: %s），要的数据别处也找不到就写一张资料缺口卡。不要换成 Invoke-WebRequest 或 curl 再下一遍。"
               % (SAID[f.state], f.detail, name, url, f.state))
    return {"ok": True, "downloaded": False, "saved": False, "fetch_state": f.state, "challenge": f.challenge,
            "detail": f.detail, "recorded": rec, "next": nxt}


def saved_next(rec, moved, downloaded=False):
    if rec["state"] == "ok":
        nxt = ("存好了，转成的文字在 %s：读它做资料卡片（来源的 url 写 %s，fetch_state: ok，照抄原文摘录）。"
               % (rec["outputs"][0], rec["url"]))
    else:
        nxt = ("原件存好了，但没能转成文字（%s）：这份不能当成「读到了」。图片、扫描件要你自己看懂了再做卡，不许凭记忆补数；"
               "读不了就照实记（fetches.py add … --state failed 或 empty），资料卡片的 fetch_state 也照实写。" % rec["note"])
    if moved:
        nxt += "这份原来放在 inputs/ 里，已经挪到 fetched/（inputs/ 只放用户自己的材料）；你在 inputs/converted/ 里自己转出来的文字直接删掉。"
    elif not downloaded:
        nxt += "下载用的临时文件可以删掉了。"
    return nxt


def main(argv):
    return yzlib.run_main(_main, argv)


def _main(argv):
    yzlib.setup_stdout()
    ap = argparse.ArgumentParser(prog="fetches.py")
    sub = ap.add_subparsers(dest="cmd")
    a = sub.add_parser("add")
    a.add_argument("project")
    a.add_argument("--url", required=True)
    a.add_argument("--state", required=True, choices=STATES)
    a.add_argument("--what")
    s = sub.add_parser("list")
    s.add_argument("project")
    v = sub.add_parser("save")
    v.add_argument("project")
    v.add_argument("--file")
    v.add_argument("--url", required=True)
    v.add_argument("--title")
    v.add_argument("--timeout", type=float, default=DOWNLOAD_TIMEOUT)
    v.add_argument("--max-mb", type=float, default=DOWNLOAD_MAX_MB)
    args = ap.parse_args(argv[1:])
    if not args.cmd:
        ap.print_help()
        return 2
    project = yzlib.project_dir(args.project)
    if args.cmd == "save":
        if not args.file:
            return yzlib.emit(save_from_url(project, args.url, args.title, args.timeout, args.max_mb))
        try:
            rec, moved = save(project, args.file, args.url, args.title)
        except Fetched as f:            # 第九轮:网站给的是验证页 —— 不存,记成打不开
            return yzlib.emit(not_saved_reply(project, args.url, f))
        return yzlib.emit({"ok": True, "saved": rec, "next": saved_next(rec, moved)})
    if args.cmd == "add":
        if args.state == "ok" and latest_states(project).get(args.url.strip()) not in FAILED_STATES:
            raise UsageError("--state ok 只用在这个网址以前记过没取到、后来真的读到了的时候；这个网址没有记过没取到，不用记")
        rec = add(project, args.url, args.state, args.what)
        if args.state == "ok":
            nxt = ("记下了（后来重新取到了）。把资料卡片上这个来源改成 fetch_state: ok，写原文摘录（excerpt）、as_of 写今天，"
                   "再跑 refs_add.py 更新参考文献清单。")
        else:
            nxt = ("记下了（%s）。这个网址要记进相关那张资料卡片的来源（fetch_state: %s）；"
                   "要的数据别处也找不到，就写一张资料缺口卡，资料汇编的缺口照实写 %s（%s）。"
                   % (SAID[args.state], args.state, args.state, SAID[args.state]))
        return yzlib.emit({"ok": True, "recorded": rec, "next": nxt})
    items = entries(project)
    counts = {st: sum(1 for e in items if e.get("state") == st) for st in STATES}
    sources = source_counts(project)
    return yzlib.emit({"ok": True, "count": len(items), "by_state": {SAID[k]: v for k, v in counts.items()},
                       "sources_not_got": {SAID[k]: v for k, v in sources.items()}, "entries": items})


if __name__ == "__main__":
    sys.exit(main(sys.argv))
