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


from sqlalchemy import select

from app.models.pg.analysis_record import AnalysisRecord
from app.models.pg.video_analysis import VideoAnalysis


async def _period_rows(session, record_type: str, start, end) -> list:
    """기간 내 분석 영상의 record 행 튜플 (전수 — 영상 40건 제한과 무관)."""
    rows = (await session.execute(
        select(
            AnalysisRecord.entity_name, AnalysisRecord.value_text,
            AnalysisRecord.value_num, AnalysisRecord.event_date, AnalysisRecord.attrs,
        )
        .join(VideoAnalysis, VideoAnalysis.video_pk == AnalysisRecord.video_pk)
        .where(
            AnalysisRecord.record_type == record_type,
            VideoAnalysis.analyzed_at >= start,
            VideoAnalysis.analyzed_at < end,
        )
    )).all()
    return [tuple(r) for r in rows]


async def build_records_data(
    session, *, sections: list, record_schema: dict, period_start, period_end
) -> dict:
    """섹션이 요청한 피벗(없으면 기본 3종)을 집계해 {key: data}로 반환.

    빈 데이터 key는 생략 — 렌더·프롬프트 양쪽에서 자연히 사라진다.
    """
    types = record_schema.get("types") or []
    if not types:
        return {}
    default_rt = types[0]["type_key"]
    valid_rts = {t["type_key"] for t in types}

    wanted: dict[str, dict] = {}
    for s in sections or []:
        if s.get("kind") == "hybrid" and s.get("key") in PIVOT_KEYS:
            wanted[s["key"]] = dict(s.get("params") or {})
    if not wanted:  # custom 모드 {records_block}용 기본 3종
        wanted = {k: {} for k in PIVOT_KEYS}

    cur_cache: dict[str, list] = {}

    async def _cur(rt: str) -> list:
        if rt not in cur_cache:
            cur_cache[rt] = await _period_rows(session, rt, period_start, period_end)
        return cur_cache[rt]

    out: dict[str, dict] = {}
    for key, params in wanted.items():
        rt = str(params.get("record_type") or "").strip() or default_rt
        if rt not in valid_rts:
            rt = default_rt
        top_k = params.get("top_k") if isinstance(params.get("top_k"), int) else 8
        rows = await _cur(rt)
        if key == "entity_pivot":
            data = pivot_entity_rows(rows, group_by=str(params.get("group_by") or ""), top_k=top_k)
        elif key == "top_records":
            data = top_records_rows(rows, top_k=top_k)
        else:  # period_compare — 직전 동일 길이 기간과 비교
            prev_rows = await _period_rows(
                session, rt, period_start - (period_end - period_start), period_start)
            data = compare_period_rows(rows, prev_rows)
        if has_content(data):
            out[key] = data
    return out
