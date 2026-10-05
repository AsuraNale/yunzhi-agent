# -*- coding: utf-8 -*-
"""view.py:四种材料各生成一页,样式同进度页;成稿用 toolkit 生成的网页版;toolkit 渲染器给用户的文字用词表说法。"""
import html
import os
import re
import shutil

import pytest

import view
import yzlib
from conftest import MAIN, read
from yzlib import pl

TALENT = "长三角人才引进政策梳理"


def text_of(path):
    s = re.sub(r"<style.*?</style>", "", read(path), flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s)))


@pytest.mark.parametrize("stage, kind, title", [
    ("S1", "task_plan", "任务计划第 2 版"), ("S3", "dossier", "资料汇编第 1 版"),
    ("S3", "cards", "资料卡片"), ("S4", "outline", "提纲第 3 版")])
def test_each_material_renders_a_page(world, stage, kind, title):
    project = world(stage)
    path, got = view.render(project, kind)
    assert got == title and os.path.isfile(path)
    assert os.path.dirname(path) == os.path.join(project, view.VIEW_DIR)
    raw = read(path)
    assert raw.startswith("<!doctype html>") and '<meta charset="utf-8">' in raw
    assert "--accent:#3e5b80" in raw                       # 进度页的配色
    assert "{#" not in text_of(path) and "{id:" not in text_of(path)


def test_task_plan_page_uses_researcher_section_names(world):
    project = world("S1")
    t = text_of(view.render(project, "task_plan")[0])
    for name in ("委托原话", "核心判断", "事先写好的判定标准", "中途检查点", "概念界定", "研究范围", "请你重点看这三点"):
        assert name in t
    for internal in ("可证伪命题", "预注册结论空间", "证据对接窗口条件", "概念定义表", "outcome", "操作定义"):
        assert internal not in t
    assert "等你确认" in t and "研判型（下判断）" in t


def test_dossier_page_shows_card_titles_not_numbers(world):
    project = world("S3")
    t = text_of(view.render(project, "dossier")[0])
    assert not re.search(r"(?<![A-Za-z0-9])S\d{2}(?![0-9])", t)
    assert "参加县与未参加县的公共桩增速" in t and "部分成立" in t
    assert not re.search(r"(?<![A-Za-z0-9])[CRG]\d ", t)            # 结论、适用范围、缺口前面的编号不显示
    assert os.path.isfile(os.path.join(project, view.VIEW_DIR, "资料卡片.html"))


def test_cards_page_lists_deprecated_last(world):
    project = world("S3")
    p = os.path.join(project, "cards", "S01.md")
    with open(p, encoding="utf-8") as f:
        s = f.read()
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(s.replace("kind: card\n", "kind: card\ndeprecated: true\n", 1))
    t = text_of(view.render(project, "cards")[0])
    assert "已弃用" in t and t.rindex("H 省县域公共充电桩数量（2024）") > t.index("资料缺口")


def test_draft_uses_the_toolkit_html(world):
    project = world("S5")
    path, title = view.render(project, "draft")
    assert title == "成稿（网页版）" and os.path.normpath(path) == os.path.normpath(
        [p for rel, p in pl.deliverable_paths(project) if rel.endswith(".html")][0])


def test_draft_preview_is_rendered_outside_out_when_out_is_empty(world):
    project = world("S5")
    shutil.rmtree(os.path.join(project, "out"))
    path, title = view.render(project, "draft")
    assert title.startswith("成稿预览") and os.path.dirname(path) == os.path.join(project, view.VIEW_DIR)
    assert not os.path.exists(os.path.join(project, "out"))


def test_survey_pages_use_neutral_wording(world):
    project = world("S6", TALENT)
    for kind in ("dossier", "cards"):
        t = text_of(view.render(project, kind)[0])
        assert "支持判断" not in t and "不支持" not in t, kind


def test_toolkit_renderers_use_user_wording(world):
    project = world("S6")
    tmp = yzlib.work_tmp("test-render")
    try:
        draft = [os.path.join(project, "drafts", f) for f in os.listdir(os.path.join(project, "drafts")) if f.endswith(".md")][0]
        refs = os.path.join(project, "references.yaml")
        out_html, out_docx = os.path.join(tmp, "a.html"), os.path.join(tmp, "a.docx")
        rc, so, se = yzlib.run_toolkit("render_html.py", [draft, refs, out_html, "--cards", os.path.join(project, "cards")])
        assert rc == 0, so + se
        rc, so, se = yzlib.run_toolkit("render_docx.py", [draft, refs, out_docx])
        assert rc == 0, so + se
        texts = [text_of(out_html), pl.docx_text(out_docx)]
        for t in texts:
            assert "官方一手" in t
            for bad in ("A级", "A−级", "B级", "来源层级", "A=", "A−=", "证据卡"):
                assert bad not in t, bad
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
