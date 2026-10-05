# -*- coding: utf-8 -*-
"""report_server — 窗口报告/正本渲染的本机只读静态服务。

用法:
  python -X utf8 report_server.py [--root <目录>] [--port 8130]

- 默认 root = 脚本被复制到的工作台根(即 cwd);端口 8130。
- 只 GET、只绑 127.0.0.1;端口已被占用 → 视为已在跑,exit 0(幂等,skill 可无脑调)。
- no-cache:报告每轮重生成,浏览器必须拿到新的。
- 为什么需要它:DSH 界面把 file: 链接静默降级为纯文本(实测),窗口卡上的
  「打开报告/正本」只能走 http。
"""
import argparse
import http.server
import os
import socket
import sys


TEXTUAL = ("application/javascript", "application/json", "image/svg+xml", "application/xml")


class Handler(http.server.SimpleHTTPRequestHandler):
    def guess_type(self, path):
        """必须显式声明 utf-8 —— 否则浏览器按本地代码页解,中文报告整页乱码(实测 windows-1252)。"""
        t = super().guess_type(path)
        if isinstance(t, tuple):  # 旧版本返回 (type, encoding)
            t = t[0] or "application/octet-stream"
        if (t.startswith("text/") or t in TEXTUAL) and "charset=" not in t:
            t += "; charset=utf-8"
        return t

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_POST(self):  # 只读服务
        self.send_error(405)

    def log_message(self, fmt, *args):  # 安静
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.getcwd())
    ap.add_argument("--port", type=int, default=8130)
    a = ap.parse_args()

    # 幂等:端口有人听 = 已在跑
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", a.port)) == 0:
            print(f"already running on 127.0.0.1:{a.port}")
            return 0

    os.chdir(a.root)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"report server: http://127.0.0.1:{a.port}/  root={a.root}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
