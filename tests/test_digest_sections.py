"""Digest 섹션 레지스트리·정규화 단위 테스트."""

from __future__ import annotations

from app.services.digest_sections import (
    COMPUTED_SECTIONS,
    DEFAULT_DIGEST_SECTIONS,
    SECTION_KIND_COMPUTED,
    SECTION_KIND_LLM,
    normalize_sections,
    resolve_sections,
)


def test_default_sections_are_valid_and_neutral():
    keys = [s["key"] for s in DEFAULT_DIGEST_SECTIONS]
    assert "overview" in keys
    assert all(s["kind"] in (SECTION_KIND_LLM, SECTION_KIND_COMPUTED) for s in DEFAULT_DIGEST_SECTIONS)
    for s in DEFAULT_DIGEST_SECTIONS:
        if s["kind"] == SECTION_KIND_COMPUTED:
            assert s["key"] in COMPUTED_SECTIONS


def test_normalize_drops_invalid_kind_and_unknown_computed():
    raw = [
        {"key": "overview", "kind": "llm", "title": "요약", "guide": "핵심"},
        {"key": "bogus", "kind": "weird", "title": "x"},
        {"key": "not_a_real_computed", "kind": "computed", "title": "y"},
        {"key": "top_tags", "kind": "computed", "title": "태그"},
    ]
    out = normalize_sections(raw)
    assert [s["key"] for s in out] == ["overview", "top_tags"]
    assert out[0]["guide"] == "핵심"


def test_normalize_enforces_cap_and_defaults_title():
    raw = [{"key": f"s{i}", "kind": "llm"} for i in range(20)]
    out = normalize_sections(raw)
    assert len(out) == 12
    assert out[0]["title"]


def test_resolve_sections_falls_back():
    assert resolve_sections([], []) == DEFAULT_DIGEST_SECTIONS
    prof = [{"key": "overview", "kind": "llm", "title": "P"}]
    assert resolve_sections([], prof) == normalize_sections(prof)
    cfg = [{"key": "insights", "kind": "llm", "title": "C"}]
    assert resolve_sections(cfg, prof) == normalize_sections(cfg)
