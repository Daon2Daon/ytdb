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
