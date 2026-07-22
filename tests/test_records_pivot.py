# tests/test_records_pivot.py
from datetime import date

from app.services.records_pivot import (
    compare_period_rows, has_content, pivot_entity_rows,
    records_block_text, top_records_rows,
)

_R = [
    ("SoftBank", "5G 확대", 1200, date(2026, 7, 1), {"region": "일본"}),
    ("SoftBank", "AI 투자", None, None, {"region": "일본"}),
    ("KDDI", "요금제 개편", 800, None, {"region": "일본"}),
    ("", "무명", None, None, {}),
]


def test_pivot_entity_rows_counts_and_samples():
    d = pivot_entity_rows(_R, top_k=8)
    assert d["items"][0]["entity"] == "SoftBank"
    assert d["items"][0]["count"] == 2
    assert d["items"][0]["samples"] == ["5G 확대", "AI 투자"]
    assert len(d["items"]) == 2  # 빈 entity drop


def test_pivot_entity_rows_group_by_axis():
    d = pivot_entity_rows(_R, group_by="region")
    assert d["items"][0]["by"] == {"일본": 2}


def test_pivot_entity_rows_top_k():
    assert len(pivot_entity_rows(_R, top_k=1)["items"]) == 1


def test_compare_period_rows():
    cur = [("SoftBank", None, None, None, {}), ("Rakuten", None, None, None, {})]
    prev = [("SoftBank", None, None, None, {}), ("KDDI", None, None, None, {})]
    d = compare_period_rows(cur, prev)
    assert d["new"] == [{"entity": "Rakuten", "count": 1}]
    assert d["gone"] == [{"entity": "KDDI", "count": 1}]
    assert d["continuing"] == [{"entity": "SoftBank", "cur": 1, "prev": 1}]


def test_top_records_rows_sorts_and_skips_null():
    d = top_records_rows(_R)
    assert [it["value"] for it in d["items"]] == [1200.0, 800.0]
    assert d["items"][0]["date"] == "2026-07-01"


def test_has_content_and_records_block():
    assert has_content({"items": []}) is False
    assert has_content({"items": [{"entity": "A"}]}) is True
    assert records_block_text({}) == "없음"
    assert "SoftBank" in records_block_text(
        {"entity_pivot": {"items": [{"entity": "SoftBank"}]}})


# tests/test_records_pivot.py 에 추가
from datetime import datetime, timedelta, timezone

import pytest

from app.services.records_pivot import build_records_data


class _Res:
    def __init__(self, rows): self._rows = rows
    def all(self): return self._rows


class _StubSession:
    """execute 호출 순서대로 rows를 돌려준다."""
    def __init__(self, rows_by_call):
        self.rows_by_call = list(rows_by_call)
        self.calls = 0

    async def execute(self, stmt):
        rows = self.rows_by_call[self.calls] if self.calls < len(self.rows_by_call) else []
        self.calls += 1
        return _Res(rows)


_SCHEMA = {"version": 1, "types": [{"type_key": "campaign", "label": "캠페인", "fields": [
    {"key": "entity", "label": "브랜드", "datatype": "entity", "required": True}]}]}

_NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)
_WEEK = dict(period_start=_NOW - timedelta(days=7), period_end=_NOW)


@pytest.mark.asyncio
async def test_build_records_data_no_schema_returns_empty():
    out = await build_records_data(
        _StubSession([]), sections=[], record_schema={"version": 1, "types": []}, **_WEEK)
    assert out == {}


@pytest.mark.asyncio
async def test_build_records_data_entity_pivot_section():
    sess = _StubSession([[("SoftBank", "5G", None, None, {})]])
    sections = [{"key": "entity_pivot", "kind": "hybrid", "title": "집중",
                 "params": {"record_type": "campaign"}}]
    out = await build_records_data(sess, sections=sections, record_schema=_SCHEMA, **_WEEK)
    assert out["entity_pivot"]["items"][0]["entity"] == "SoftBank"
    assert sess.calls == 1  # 현재 기간 1회만 조회(캐시)


@pytest.mark.asyncio
async def test_build_records_data_defaults_when_no_pivot_sections():
    # 피벗 섹션이 없으면 {records_block}용 기본 3종. 빈 데이터 key는 생략.
    sess = _StubSession([[("SoftBank", "5G", 100, None, {})], []])
    out = await build_records_data(sess, sections=[], record_schema=_SCHEMA, **_WEEK)
    assert set(out) >= {"entity_pivot", "top_records"}
    assert out["period_compare"]["new"][0]["entity"] == "SoftBank"


@pytest.mark.asyncio
async def test_build_records_data_invalid_record_type_falls_back():
    sess = _StubSession([[("A", None, None, None, {})]])
    sections = [{"key": "entity_pivot", "kind": "hybrid", "title": "t",
                 "params": {"record_type": "없는타입"}}]
    out = await build_records_data(sess, sections=sections, record_schema=_SCHEMA, **_WEEK)
    assert out["entity_pivot"]["items"][0]["entity"] == "A"
