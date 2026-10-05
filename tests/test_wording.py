# -*- coding: utf-8 -*-
"""wording_check:移植客户端扫描器之后补齐的几条(不可见字符、连字符、网址、整段引文、引用块),和分支加的词。"""
import os

import pytest

import wording_check as w
from conftest import ROOT


def rules(text, users=()):
    return [(h["text"], h["rule"]) for h in w.scan(text, users)]


@pytest.mark.parametrize("hidden", [chr(c) for c in (0x034F, 0x206A, 0x206F, 0xE0067, 0xE0001, 0xE0100, 0xE01EF,
                                                     0x200B, 0xFE0F, 0x00AD)])
def test_invisible_characters_do_not_hide_a_term(hidden):
    hits = rules("这是 argu%sment 的问题" % hidden)
    assert [r for _t, r in hits] == ["en-internal:argument"]


@pytest.mark.parametrize("dash", [chr(c) for c in (0x2010, 0x2011, 0x2012, 0x2013, 0x02D7, 0x2796)])
def test_dash_variants_are_folded(dash):
    assert [r for _t, r in rules("来源是 A%s级" % dash)] == ["glossary-zh:A[−-]级?"]


def test_em_dash_is_left_alone():
    assert rules("方案A——优先") == []


def test_url_stops_where_chinese_starts():
    assert rules("详见 https://x.com/a对接窗口已过") == [("对接窗口", "coined-zh:对接窗口")]
    assert rules("详见 https://x.com/a/b 这一页") == []


def test_quote_must_be_a_whole_span_in_one_message():
    assert rules("你说「scope」要改", ["the scoped review"]) == [("scope", "en-internal:scope")]   # 不切断英文单词
    assert rules("你说「scope」要改", ["the scope review"]) == []
    assert rules("你说「先看对接窗口」", ["先看对接", "窗口怎么排"]) != []                          # 不跨两条消息
    assert w.quote_problems("你说「scope」，所以", ["the scoped review"], "why")
    assert w.quote_problems("你说「scope」，所以", ["the scope review"], "why") == []


def test_quote_block_is_checked_as_a_whole_first():
    users = ["我们要尽快过对接窗口"]
    assert rules("> 我们要尽快过对接\n> 窗口", users) == []                 # 用户的一句话分两行引:整块对得上
    assert rules("> 我们要尽快过对接\n> 窗口") != []                        # 助手自己的话分两行写:照扫
    assert rules("> 这是助手写的对接\n窗口") != []                          # 懒续行也算引用块里的一行


def test_hits_report_original_positions():
    h = w.scan("第一行\n这里有个 dossier 词")[0]
    assert (h["line"], h["column"]) == (2, 6) and h["text"] == "dossier"


@pytest.mark.parametrize("text, rule", [
    ("改一下 stages 再说", "en-internal:stage"),
    ("先定 genre", "en-internal:genre"),
    ("这几条 claims 都要核", "en-internal:claim"),
    ("最薄弱的是 S02 那个数", "ids:S\\d{2,3}"),
])
def test_fork_terms_are_flagged(text, rule):
    assert [r for _t, r in rules(text)] == [rule]


def test_fork_terms_do_not_hit_ordinary_text():
    assert rules("G1 京哈高速和 C1 驾照都不算编号；2025 年 S1 赛季也不算") == []


def test_docstring_names_what_is_not_covered():
    doc = w.__doc__
    for word in ("U+034F", "U+206A", "U+E0100", "宽松模式", "整段", "遇到汉字就结束"):
        assert word in doc


def test_evidence_card_points_name_evidence_in_words():
    with open(os.path.join(ROOT, ".agents", "skills", "evidence-card", "SKILL.md"), encoding="utf-8") as f:
        text = f.read()
    assert "具体到哪张卡" not in text and "不写卡号" in text
