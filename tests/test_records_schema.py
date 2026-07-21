from app.services.records_schema import normalize_record_schema, normalize_vocab


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
