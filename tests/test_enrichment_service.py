# tests/test_enrichment_service.py
import json
from datetime import datetime, timedelta, timezone

from app.services.enrichment_service import (
    apply_proposal_items, build_enrich_prompt, normalize_enrich_proposal,
    proposal_is_empty, should_enrich,
)

_NOW = datetime(2026, 7, 22, tzinfo=timezone.utc)


def test_should_enrich_conditions():
    kw = dict(analysis_count=10, last_at="", has_proposal=False, now=_NOW)
    assert should_enrich(**kw) is True
    assert should_enrich(**{**kw, "analysis_count": 9}) is False
    assert should_enrich(**{**kw, "has_proposal": True}) is False
    recent = (_NOW - timedelta(days=5)).isoformat()
    old = (_NOW - timedelta(days=31)).isoformat()
    assert should_enrich(**{**kw, "last_at": recent}) is False
    assert should_enrich(**{**kw, "last_at": old}) is True


def test_normalize_proposal_filters_and_caps():
    raw = json.dumps({
        "sections_add": [
            {"key": "risks", "kind": "llm", "title": "리스크", "guide": "g"},
            {"key": "top_viewed", "kind": "computed", "title": "x"},  # llm 아님 → drop
        ],
        "record_fields_add": [
            {"type_key": "campaign",
             "field": {"key": "region", "label": "지역", "datatype": "weird"}},
        ],
        "vocab_add": {"sentiment": {"values": ["중립"], "synonyms": {"neutral": "중립"}}},
        "entity_attrs_add": [{"entity": "SoftBank", "attrs": {"region": "일본"}}],
        "note": "보강",
    })
    p = normalize_enrich_proposal(raw)
    assert [s["key"] for s in p["sections_add"]] == ["risks"]
    assert p["record_fields_add"][0]["field"]["datatype"] == "text"  # weird → text
    assert p["entity_attrs_add"][0]["entity"] == "SoftBank"
    assert p["note"] == "보강"
    assert "created_at" in p


def test_normalize_proposal_empty_returns_empty_dict():
    assert normalize_enrich_proposal("garbage") == {}
    assert normalize_enrich_proposal('{"sections_add": [], "note": "없음"}') == {}


def test_proposal_is_empty():
    assert proposal_is_empty({}) is True
    assert proposal_is_empty(None) is True
    assert proposal_is_empty({"note": "x"}) is True
    assert proposal_is_empty(
        {"vocab_add": {"a": {"values": ["v"], "synonyms": {}}}}) is False


def test_apply_proposal_items_merges_and_bumps_version():
    profile = {
        "digest_sections": [{"key": "overview", "kind": "llm", "title": "요약", "guide": "g"}],
        "record_schema": {"version": 1, "types": [
            {"type_key": "campaign", "label": "캠페인", "fields": [
                {"key": "entity", "label": "브랜드", "datatype": "entity", "required": True}]}]},
        "vocab": {"sentiment": {"label": "평가", "values": ["긍정"], "synonyms": {}}},
    }
    proposal = {
        "sections_add": [{"key": "risks", "kind": "llm", "title": "리스크", "guide": "g"}],
        "record_fields_add": [{"type_key": "campaign", "field": {
            "key": "region", "label": "지역", "datatype": "text", "required": False}}],
        "vocab_add": {"sentiment": {"label": "평가", "values": ["중립"],
                                    "synonyms": {"neutral": "중립"}}},
        "entity_attrs_add": [],
        "note": "n",
    }
    items = {i["key"]: i["value"] for i in apply_proposal_items(profile, proposal)}
    sections = json.loads(items["digest_sections"])
    assert [s["key"] for s in sections] == ["overview", "risks"]
    schema = json.loads(items["record_schema"])
    assert schema["version"] == 2
    assert [f["key"] for f in schema["types"][0]["fields"]] == ["entity", "region"]
    vocab = json.loads(items["vocab"])
    assert vocab["sentiment"]["values"] == ["긍정", "중립"]
    assert vocab["sentiment"]["synonyms"]["neutral"] == "중립"
    assert items["enrich_proposal"] == "{}"
    assert items["vocab_pending"] == "[]"


def test_apply_proposal_items_empty_returns_empty():
    assert apply_proposal_items({}, {}) == []


def test_build_enrich_prompt_includes_inputs():
    p = build_enrich_prompt(
        persona="p", sections=[], record_schema={"version": 1, "types": []},
        vocab={}, samples=["요약1"], vocab_pending=["sentiment:애매"],
        merge_holds=["A ← B"],
    )
    assert "요약1" in p and "sentiment:애매" in p and "A ← B" in p
