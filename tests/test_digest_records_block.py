# tests/test_digest_records_block.py
from app.services.digest_service import DigestAggregate, custom_prompt_kwargs


def _agg(records_data=None):
    return DigestAggregate(
        video_count=1, sentiment_breakdown={}, top_tags=[], top_channels=[],
        videos=[], records_data=records_data or {},
    )


def test_custom_kwargs_include_records_block():
    agg = _agg({"top_records": {"items": [{"entity": "A", "value": 1.0}]}})
    kw = custom_prompt_kwargs(agg, category="", period_label="7월 3주", previous_digest="없음")
    assert '"top_records"' in kw["records_block"]


def test_custom_kwargs_records_block_empty():
    kw = custom_prompt_kwargs(_agg(), category="", period_label="x", previous_digest="없음")
    assert kw["records_block"] == "없음"


def test_custom_prompt_format_with_records_block():
    kw = custom_prompt_kwargs(_agg(), category="", period_label="x", previous_digest="없음")
    assert "레코드: {records_block}".format(**kw) == "레코드: 없음"
