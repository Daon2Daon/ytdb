"""그룹 프로필 파싱 단위 테스트."""

from __future__ import annotations

from app.services.group_profile import GroupProfile, parse_profile


def test_parse_profile_empty_gives_defaults():
    p = parse_profile({})
    assert p.persona == ""
    assert p.digest_sections == []
    assert p.bootstrap_status == "none"


def test_parse_profile_reads_fields():
    p = parse_profile({
        "persona": "지식 큐레이터다.",
        "digest_sections": [{"key": "overview", "kind": "llm", "title": "요약"}],
        "bootstrap_status": "done",
        "bootstrap_at": "2026-07-21T00:00:00+00:00",
    })
    assert p.persona == "지식 큐레이터다."
    assert p.digest_sections[0]["key"] == "overview"
    assert p.bootstrap_status == "done"


def test_parse_profile_drops_invalid_sections():
    p = parse_profile({"digest_sections": [{"key": "x", "kind": "bad"}]})
    assert p.digest_sections == []


def test_parse_profile_record_schema_and_vocab():
    from app.services.group_profile import parse_profile
    p = parse_profile({
        "persona": "x",
        "record_schema": {"types": [{"type_key": "c", "fields": [
            {"key": "e", "datatype": "entity"}]}]},
        "vocab": {"sentiment": {"values": ["긍정"], "synonyms": {"positive": "긍정"}}},
    })
    assert p.record_schema["types"][0]["type_key"] == "c"
    assert p.vocab["sentiment"]["synonyms"]["positive"] == "긍정"


def test_parse_profile_defaults_record_schema_empty():
    from app.services.group_profile import parse_profile
    p = parse_profile({"persona": "x"})
    assert p.record_schema == {"version": 1, "types": []}
    assert p.vocab == {}
