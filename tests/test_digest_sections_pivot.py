# tests/test_digest_sections_pivot.py
from app.services.digest_sections import (
    PIVOT_SECTIONS, assemble_output_sections, build_computed_data,
    build_structured_prompt, normalize_sections, sections_to_markdown,
)


class _Agg:
    video_count = 3
    sentiment_breakdown = {}
    top_tags = []
    top_channels = []
    videos = []
    records_data = {
        "entity_pivot": {"items": [{"entity": "SoftBank", "count": 2, "samples": ["5G"]}]},
    }


def test_normalize_accepts_hybrid_pivot_with_params():
    raw = [{"key": "entity_pivot", "kind": "hybrid",
            "params": {"record_type": "campaign", "top_k": 5, "junk": "x"}}]
    out = normalize_sections(raw)
    assert out[0]["kind"] == "hybrid"
    assert out[0]["title"] == PIVOT_SECTIONS["entity_pivot"]
    assert out[0]["params"] == {"record_type": "campaign", "top_k": 5}


def test_normalize_drops_hybrid_unknown_key_and_bad_topk():
    assert normalize_sections([{"key": "nope", "kind": "hybrid"}]) == []
    out = normalize_sections([{"key": "top_records", "kind": "hybrid",
                               "params": {"top_k": 99}}])
    assert "params" not in out[0]


def test_build_computed_data_reads_records_data():
    d = build_computed_data("entity_pivot", _Agg())
    assert d["items"][0]["entity"] == "SoftBank"
    assert build_computed_data("period_compare", _Agg()) == {}


def test_assemble_hybrid_merges_body_and_data():
    sections = [{"key": "entity_pivot", "kind": "hybrid", "title": "집중"}]
    out = assemble_output_sections(sections, llm_bodies={"entity_pivot": "서술"}, agg=_Agg())
    assert out[0]["body_md"] == "서술"
    assert out[0]["data"]["items"][0]["entity"] == "SoftBank"


def test_assemble_hybrid_skips_when_empty():
    sections = [{"key": "period_compare", "kind": "hybrid", "title": "대비"}]
    assert assemble_output_sections(sections, llm_bodies={}, agg=_Agg()) == []


def test_sections_markdown_renders_pivot_data():
    secs = [{"key": "entity_pivot", "kind": "hybrid", "title": "집중",
             "body_md": "서술",
             "data": {"items": [{"entity": "SoftBank", "count": 2, "samples": ["5G"]}]}}]
    md = sections_to_markdown(secs)
    assert "서술" in md and "SoftBank" in md


def test_structured_prompt_includes_hybrid_schema_and_records():
    sections = [
        {"key": "overview", "kind": "llm", "title": "요약", "guide": "g"},
        {"key": "entity_pivot", "kind": "hybrid", "title": "집중", "guide": "피벗 서술"},
    ]
    p = build_structured_prompt(
        persona="p", data_block="D", sections=sections, records_data=_Agg.records_data)
    assert '"entity_pivot"' in p
    assert "레코드 집계" in p and "SoftBank" in p
