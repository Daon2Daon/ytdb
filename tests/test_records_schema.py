from app.services.records_schema import normalize_record_schema, normalize_vocab
from app.services.records_schema import promote_fields
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
