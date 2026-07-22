from app.services.records_schema import normalize_record_schema, normalize_vocab
from app.services.records_schema import promote_fields
from app.services.records_schema import map_vocab_value
from datetime import date


def test_normalize_record_schema_basic():
    raw = {
        "version": 1,
        "types": [
            {"type_key": "campaign", "label": "캠페인", "fields": [
                {"key": "entity", "label": "브랜드", "datatype": "entity", "required": True},
                {"key": "budget", "label": "규모", "datatype": "number"},
                {"key": "junk", "label": "?", "datatype": "weird"},
            ]},
        ],
    }
    rs = normalize_record_schema(raw)
    assert rs["version"] == 1
    t = rs["types"][0]
    assert t["type_key"] == "campaign"
    dts = [f["datatype"] for f in t["fields"]]
    assert dts == ["entity", "number", "text"]
    assert t["fields"][0]["required"] is True


def test_normalize_record_schema_drops_typeless_and_fieldless():
    raw = {"types": [
        {"label": "no key"},
        {"type_key": "empty", "fields": []},
        {"type_key": "ok", "fields": [{"key": "e", "datatype": "entity"}]},
    ]}
    rs = normalize_record_schema(raw)
    assert [t["type_key"] for t in rs["types"]] == ["ok"]


def test_normalize_record_schema_none_returns_empty():
    assert normalize_record_schema(None) == {"version": 1, "types": []}
    assert normalize_record_schema("garbage") == {"version": 1, "types": []}


def test_normalize_vocab():
    raw = {"sentiment": {"label": "평가", "values": ["긍정", "부정", "혼조"],
                         "synonyms": {"positive": "긍정", "bullish": "긍정"}}}
    v = normalize_vocab(raw)
    assert v["sentiment"]["values"] == ["긍정", "부정", "혼조"]
    assert v["sentiment"]["synonyms"]["bullish"] == "긍정"


def test_normalize_vocab_none():
    assert normalize_vocab(None) == {}
    assert normalize_vocab([1, 2]) == {}


_CAMPAIGN_TYPE = {
    "type_key": "campaign", "label": "캠페인", "fields": [
        {"key": "entity", "label": "브랜드", "datatype": "entity", "required": True},
        {"key": "message", "label": "메시지", "datatype": "text"},
        {"key": "budget", "label": "규모", "datatype": "number"},
        {"key": "aired_on", "label": "집행", "datatype": "date"},
        {"key": "second_note", "label": "비고", "datatype": "text"},
    ],
}


def test_promote_fields_basic():
    row = promote_fields(_CAMPAIGN_TYPE, {
        "entity": "SoftBank", "message": "5G 확대", "budget": "1200",
        "aired_on": "2026-07-01", "second_note": "지역 한정",
    })
    assert row is not None
    assert row["entity_name"] == "SoftBank"
    assert row["value_text"] == "5G 확대"
    assert row["value_num"] == 1200
    assert row["event_date"] == date(2026, 7, 1)
    assert row["attrs"] == {"second_note": "지역 한정"}


def test_promote_fields_drops_when_required_missing():
    assert promote_fields(_CAMPAIGN_TYPE, {"message": "규모 미상"}) is None


def test_promote_fields_bad_number_and_date_go_to_attrs():
    row = promote_fields(_CAMPAIGN_TYPE, {
        "entity": "KT", "budget": "대규모", "aired_on": "미정",
    })
    assert row["value_num"] is None
    assert row["event_date"] is None
    assert row["attrs"]["budget"] == "대규모"
    assert row["attrs"]["aired_on"] == "미정"


def test_promote_fields_unknown_fields_go_to_attrs():
    row = promote_fields(_CAMPAIGN_TYPE, {"entity": "SKT", "extra": "여분"})
    assert row["attrs"]["extra"] == "여분"


_SENT = {"label": "평가", "values": ["긍정", "부정", "혼조"],
         "synonyms": {"positive": "긍정", "bullish": "긍정", "neg": "부정"}}


def test_map_vocab_synonym_hit():
    assert map_vocab_value("Positive", _SENT) == ("긍정", False)
    assert map_vocab_value("  BULLISH ", _SENT) == ("긍정", False)


def test_map_vocab_canonical_passthrough():
    assert map_vocab_value("부정", _SENT) == ("부정", False)


def test_map_vocab_unmapped_is_pending():
    val, pending = map_vocab_value("애매함", _SENT)
    assert val == "애매함"
    assert pending is True


def test_map_vocab_empty():
    assert map_vocab_value("", _SENT) == ("", False)
    assert map_vocab_value(None, _SENT) == (None, False)


def test_promote_fields_required_second_same_datatype_field():
    type_def = {"type_key": "t", "fields": [
        {"key": "a", "datatype": "text"},
        {"key": "b", "datatype": "text", "required": True},
    ]}
    assert promote_fields(type_def, {"a": "only a"}) is None
    row = promote_fields(type_def, {"a": "aa", "b": "bb"})
    assert row is not None
    assert row["value_text"] == "aa"
    assert row["attrs"]["b"] == "bb"


def test_bump_schema_version_changed_and_unchanged():
    from app.services.records_schema import bump_schema_version_if_changed
    old = {"version": 2, "types": [{"type_key": "a", "fields": [
        {"key": "e", "datatype": "entity"}]}]}
    same = bump_schema_version_if_changed(old, old)
    assert same["version"] == 2
    new = {"version": 2, "types": [{"type_key": "a", "fields": [
        {"key": "e", "datatype": "entity"}, {"key": "n", "datatype": "number"}]}]}
    bumped = bump_schema_version_if_changed(old, new)
    assert bumped["version"] == 3
    assert len(bumped["types"][0]["fields"]) == 2
