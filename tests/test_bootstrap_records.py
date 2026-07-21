from app.services.bootstrap_service import normalize_bootstrap_output_v2


def test_v2_parses_record_schema_and_vocab():
    raw = '''{
      "persona": "큐레이터",
      "digest_sections": [
        {"key": "overview", "kind": "llm", "title": "요약", "guide": "g"},
        {"key": "top_viewed", "kind": "computed", "title": "조회수 상위"}
      ],
      "record_schema": {"version": 1, "types": [
        {"type_key": "topic", "label": "주제", "fields": [
          {"key": "entity", "label": "대상", "datatype": "entity", "required": true},
          {"key": "summary", "label": "요지", "datatype": "text"}]}]},
      "vocab": {"sentiment": {"label": "평가", "values": ["긍정","부정","혼조"],
                              "synonyms": {"positive": "긍정"}}}
    }'''
    persona, sections, record_schema, vocab = normalize_bootstrap_output_v2(raw)
    assert persona == "큐레이터"
    assert len(sections) >= 2
    assert record_schema["types"][0]["type_key"] == "topic"
    assert vocab["sentiment"]["synonyms"]["positive"] == "긍정"


def test_v2_missing_records_keys_yield_empty_schema():
    raw = '''{"persona": "p", "digest_sections": [
        {"key": "a", "kind": "llm", "title": "A", "guide": "g"},
        {"key": "b", "kind": "llm", "title": "B", "guide": "g"}]}'''
    persona, sections, record_schema, vocab = normalize_bootstrap_output_v2(raw)
    assert record_schema == {"version": 1, "types": []}
    assert vocab == {}


def test_v2_bad_json_raises():
    import pytest
    with pytest.raises(ValueError):
        normalize_bootstrap_output_v2("not json")
