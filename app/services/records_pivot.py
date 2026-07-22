# app/services/records_pivot.py
"""analysis_records 피벗 집계 (Phase 3). SQL은 얇게, 변환은 순수 함수로.

행 튜플 규약: (entity_name, value_text, value_num, event_date, attrs)
"""

from __future__ import annotations

import json
from typing import Any

PIVOT_KEYS = ("entity_pivot", "period_compare", "top_records")


def pivot_entity_rows(rows: list, *, group_by: str = "", top_k: int = 8) -> dict:
    """엔티티별 레코드 요약 → {"items": [{entity, count, samples, by?}]}."""
    by_entity: dict[str, dict] = {}
    for name, text, _num, _dt, attrs in rows:
        name = str(name or "").strip()
        if not name:
            continue
        e = by_entity.setdefault(name, {"entity": name, "count": 0, "samples": []})
        e["count"] += 1
        text = str(text or "").strip()
        if text and len(e["samples"]) < 3:
            e["samples"].append(text)
        if group_by:
            val = str((attrs or {}).get(group_by) or "").strip()
            if val:
                by = e.setdefault("by", {})
                by[val] = by.get(val, 0) + 1
    items = sorted(by_entity.values(), key=lambda x: (-x["count"], x["entity"]))
    return {"items": items[:top_k]}


def compare_period_rows(cur_rows: list, prev_rows: list) -> dict:
    """직전 기간 대비 신규/소멸/지속 엔티티."""
    def _counts(rows: list) -> dict[str, int]:
        out: dict[str, int] = {}
        for name, *_ in rows:
            name = str(name or "").strip()
            if name:
                out[name] = out.get(name, 0) + 1
        return out

    cur, prev = _counts(cur_rows), _counts(prev_rows)
    new = [{"entity": n, "count": c} for n, c in cur.items() if n not in prev]
    gone = [{"entity": n, "count": c} for n, c in prev.items() if n not in cur]
    cont = [{"entity": n, "cur": c, "prev": prev[n]} for n, c in cur.items() if n in prev]
    new.sort(key=lambda x: (-x["count"], x["entity"]))
    gone.sort(key=lambda x: (-x["count"], x["entity"]))
    cont.sort(key=lambda x: (-x["cur"], x["entity"]))
    return {"new": new, "gone": gone, "continuing": cont}


def top_records_rows(rows: list, *, top_k: int = 8) -> dict:
    """value_num 보유 레코드 상위 표."""
    items: list[dict[str, Any]] = []
    for name, text, num, dt, _attrs in rows:
        if num is None:
            continue
        items.append({
            "entity": str(name or "").strip() or None,
            "value": float(num),
            "text": str(text or "").strip() or None,
            "date": dt.isoformat() if dt is not None else None,
        })
    items.sort(key=lambda x: -x["value"])
    return {"items": items[:top_k]}


def has_content(data: dict) -> bool:
    """피벗 데이터에 표시할 내용이 있는지."""
    return any(bool(v) for v in (data or {}).values())


def records_block_text(records_data: dict) -> str:
    """custom digest_prompt의 {records_block} placeholder 값."""
    if not records_data:
        return "없음"
    return json.dumps(records_data, ensure_ascii=False)
