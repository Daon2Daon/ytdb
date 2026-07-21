"""그룹 프로필 부트스트랩 테스트."""

from __future__ import annotations

import pytest

from app.services.bootstrap_service import normalize_bootstrap_output


def test_normalize_bootstrap_output_valid():
    raw = (
        '{"persona":"지식 큐레이터다.",'
        '"digest_sections":[{"key":"overview","kind":"llm","title":"요약","guide":"핵심"},'
        '{"key":"top_tags","kind":"computed","title":"태그"}]}'
    )
    persona, sections = normalize_bootstrap_output(raw)
    assert persona == "지식 큐레이터다."
    assert [s["key"] for s in sections] == ["overview", "top_tags"]


def test_normalize_bootstrap_output_too_few_sections_uses_default():
    raw = '{"persona":"P","digest_sections":[{"key":"overview","kind":"llm","title":"요약"}]}'
    persona, sections = normalize_bootstrap_output(raw)
    assert persona == "P"
    assert len(sections) >= 2


def test_normalize_bootstrap_output_bad_json_raises():
    with pytest.raises(ValueError):
        normalize_bootstrap_output("not json")
