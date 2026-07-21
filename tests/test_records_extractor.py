from app.services.records_extractor import build_records_prompt, parse_records_response


_SCHEMA = {"version": 1, "types": [
    {"type_key": "campaign", "label": "캠페인", "fields": [
        {"key": "entity", "label": "브랜드", "datatype": "entity", "required": True},
        {"key": "message", "label": "메시지", "datatype": "text"}]}]}


def test_build_prompt_includes_schema_and_entities():
    p = build_records_prompt(
        analysis_text="본문 요약",
        record_schema=_SCHEMA,
        top_entities=["SoftBank", "KDDI"],
        vocab={"sentiment": {"values": ["긍정", "부정"], "synonyms": {}}},
    )
    assert "campaign" in p
    assert "SoftBank" in p
    assert "본문 요약" in p


def test_parse_records_response_lenient():
    raw = '''{"records": [
        {"type": "campaign", "fields": {"entity": "SoftBank", "message": "5G"}},
        {"type": "unknown_type", "fields": {"x": 1}},
        {"type": "campaign", "fields": {"message": "브랜드 없음"}}
    ]}'''
    rows = parse_records_response(raw, _SCHEMA)
    assert len(rows) == 1
    assert rows[0]["record_type"] == "campaign"
    assert rows[0]["entity_name"] == "SoftBank"
    assert rows[0]["position"] == 0


def test_parse_records_response_bad_json_returns_empty():
    assert parse_records_response("garbage", _SCHEMA) == []
    assert parse_records_response('{"records": "nope"}', _SCHEMA) == []


def test_parse_assigns_position_per_type():
    raw = '''{"records": [
        {"type": "campaign", "fields": {"entity": "A"}},
        {"type": "campaign", "fields": {"entity": "B"}}
    ]}'''
    rows = parse_records_response(raw, _SCHEMA)
    assert [r["position"] for r in rows] == [0, 1]


import pytest
from app.services import records_extractor as rx


def _stub_group():
    class G:
        group_id = 7
        owner_user_id = 1
        slug = "g"
    return G()


@pytest.mark.asyncio
async def test_run_records_extraction_skips_without_schema(monkeypatch):
    async def fake_profile(gid):
        from app.services.group_profile import GroupProfile
        return GroupProfile()  # record_schema empty by default

    monkeypatch.setattr(rx, "_load_profile", fake_profile)

    def _boom(ai):
        raise AssertionError("LLM must not be constructed when no schema")

    monkeypatch.setattr(rx, "LiteLLMClient", _boom)

    # Must return without raising and without constructing the LLM client.
    await rx.run_records_extraction(group=_stub_group(), video_pk=1, analysis={"one_line": "x"})


@pytest.mark.asyncio
async def test_post_pass_helper_reads_analysis_and_calls(monkeypatch):
    from app.services import monitor_service as ms
    captured = {}

    async def fake_run(*, group, video_pk, analysis):
        captured["video_pk"] = video_pk
        captured["one_line"] = analysis.get("one_line")

    monkeypatch.setattr(ms, "run_records_extraction", fake_run)

    async def fake_load(session, video_pk):
        return {"one_line": "요약", "sentiment": "긍정"}

    monkeypatch.setattr(ms, "_load_analysis_for_records", fake_load)

    class _FakeSess:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    await ms._records_post_pass(group=_stub_group(), make_session=lambda: _FakeSess(), video_pk=42)
    assert captured["video_pk"] == 42
    assert captured["one_line"] == "요약"
