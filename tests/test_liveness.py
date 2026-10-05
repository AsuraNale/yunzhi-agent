# -*- coding: utf-8 -*-
"""liveness.py:HTTP 结果 → 五态(不联网,用假的 urlopen);写回参考文献清单后交付前检查第 ⑦ 项认得出。"""
import io
import os
import urllib.error

import yaml

import liveness
from conftest import MAIN


class _Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake(code=None, net=False):
    def urlopen(req, timeout=None):
        if net:
            raise urllib.error.URLError("no network")
        if code:
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)
        return _Resp()
    return urlopen


def test_states(monkeypatch):
    cases = [(None, False, ("ok", None)), (404, False, ("failed", "4xx")), (500, False, ("failed", "5xx")),
             (403, False, ("blocked", None)), (None, True, ("failed", "net"))]
    for code, net, want in cases:
        monkeypatch.setattr(liveness.urllib.request, "urlopen", fake(code, net))
        state, detail, _status = liveness.probe("https://example.org/a.pdf", 1)
        assert (state, detail) == want, (code, net)


def test_writes_five_state_records(world, monkeypatch):
    project = world("S6")
    monkeypatch.setattr(liveness.urllib.request, "urlopen", fake(net=True))
    assert liveness.main(["liveness.py", MAIN, "--from", "CA-ON"]) == 0
    with io.open(os.path.join(project, "references.yaml"), encoding="utf-8") as f:
        entries = yaml.safe_load(f)["entries"]
    assert entries and all(e["liveness"]["state"] == "failed" and e["liveness"]["detail"] == "net"
                           and e["liveness"]["probed_from"] == "CA-ON" for e in entries)
