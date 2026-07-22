# Phase 3 — 레코드 피벗 섹션 · 프로필 보강 루프 · 승인/편집 UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phase 2가 축적한 analysis_records/entities를 다이제스트에 피벗 섹션(hybrid)으로 노출하고, 프로필 보강 제안 루프(월 1회, 카드 승인)와 데이터 프로필 편집·엔티티 병합 승인 UI를 제공한다.

**Architecture:** 피벗 데이터는 digest 생성 시 SQL 전수 집계로 만들어 `DigestAggregate.records_data`에 부착 — LLM 본 호출은 이 JSON을 자료로 받아 hybrid 섹션의 서술(body_md)만 쓴다. custom digest_prompt 사용자는 `{records_block}` placeholder로 동일 데이터를 활용한다. 보강 루프는 자동 적용 없이 `profile.enrich_proposal`에 diff를 저장하고 사용자가 [적용]/[무시]. 병합 승인은 배치 병합과 동일 코드(`apply_merge_cluster`)를 사용한다. record_schema 없는 그룹은 전부 no-op(회귀 0).

**Tech Stack:** Python 3(SQLAlchemy async, FastAPI, APScheduler, LiteLLMClient), React+TS(vitest), pytest.

---

## 배경 근거 (설계 §3, `docs/superpowers/specs/2026-07-21-digest-sections-group-profile-records-design.md`)

- Phase 2 완료·main 머지(`7626fe7`): `analysis_records`/`entities` 테이블, `records_extractor`, vocab 매핑, `vocab_pending` 적재, 엔티티 병합 배치(`attrs.merge_candidates` 보류) 모두 존재.
- 섹션 시스템: `app/services/digest_sections.py` — kind `llm|computed`, `COMPUTED_SECTIONS` 레지스트리, `normalize_sections`/`resolve_sections`/`build_computed_data`/`assemble_output_sections`/`sections_to_markdown`/`build_structured_prompt`/`parse_structured_response`.
- digest 생성: `app/services/digest_service.py:543` `generate_digest_for_group` — 세션 열고 `aggregate_period`(line 277) → `synthesize_with_llm`(line 360, custom `.format()` kwargs line 383-393 / structured line 400-414). `DigestAggregate` dataclass line 176.
- 프로필: `app/services/group_profile.py` `GroupProfile(persona, digest_sections, bootstrap_status, bootstrap_at, record_schema, vocab)`; `vocab_pending`/`enrich_proposal`은 dataclass 밖 — `get_typed(group_id, "profile")` dict로 접근.
- 프로필 API: `app/routers/profile.py` GET/`regenerate`. 라우터 등록: `app/main.py:118-134`.
- 엔티티: `app/services/entity_service.py` — `_apply_merge(session, cluster) -> list[str]`(alias 흡수 + records UPDATE), `_hold_merge`(attrs.merge_candidates), `run_entity_merge_once`, JobLog(`job_type="entity_merge"`).
- 스케줄러: `app/services/scheduler.py` — `entity_merge` job이 `trigger="interval", minutes=1440` 패턴.
- 프론트: `frontend/src/components/DigestSectionBuilder.tsx`(OrderedItemsBuilder 기반, `COMPUTED_SECTION_DEFS`/`LLM_PRESETS`/`addSection`), `DigestConfigsEditor.tsx`(line 245에서 SectionBuilder 사용, props `{items, saving, onSave}`), `pages/Settings.tsx`(카테고리 탭, `isDigest` 특례), `settings/defs.ts` `SETTING_CATEGORIES`, `pages/DigestDetail.tsx`(`computedToMarkdown`, line 82-93 렌더), `api/profile.ts` `GroupProfile`/`profileApi`.
- 테스트 baseline: 452 passed / 1 failed(`test_instant_analyze_daily_quota_400` — 기존 실패, 무관).

## 데이터 형태 (전 태스크 공통 — 여기 고정)

**hybrid 섹션 설정:** `{"key": "entity_pivot|period_compare|top_records", "kind": "hybrid", "title": str, "guide"?: str, "params"?: {"record_type": str, "group_by": str, "top_k": 1..20}}`

**피벗 data (산출 섹션의 `data`):**
- `entity_pivot`: `{"items": [{"entity": str, "count": int, "samples": [str ≤3], "by"?: {axis값: count}}]}`
- `period_compare`: `{"new": [{"entity","count"}], "gone": [{"entity","count"}], "continuing": [{"entity","cur","prev"}]}`
- `top_records`: `{"items": [{"entity": str|null, "value": float, "text": str|null, "date": "YYYY-MM-DD"|null}]}`

**보강 제안 (`profile.enrich_proposal`):** `{"sections_add": [섹션dict(llm만)], "record_fields_add": [{"type_key", "field": {key,label,datatype,required}}], "vocab_add": {axis: {label,values,synonyms}}, "entity_attrs_add": [{"entity", "attrs": {str:str}}], "note": str, "created_at": iso}` — 빈 제안은 `{}`.

**행 튜플 규약(records_pivot 내부):** `(entity_name, value_text, value_num, event_date, attrs)`

## File Structure

**신규**
- `app/services/records_pivot.py` — 피벗 순수 변환 + SQL 로더 + records_block.
- `app/services/enrichment_service.py` — 보강 제안 프롬프트·정규화·적용·배치.
- `app/routers/entities.py` — 병합 승인 큐 API.
- `frontend/src/api/entities.ts`, `frontend/src/components/DataProfile.logic.ts`, `RecordSchemaBuilder.tsx`, `VocabEditor.tsx`, `DataProfilePanel.tsx`, `EnrichProposalCard.tsx`, `MergeQueue.tsx`.
- 테스트: `tests/test_records_pivot.py`, `tests/test_digest_sections_pivot.py`, `tests/test_digest_records_block.py`, `tests/test_enrichment_service.py`, `tests/test_entities_router.py`, `frontend/src/pages/DigestDetail.pivot.test.tsx`, `frontend/src/components/DigestSectionBuilder.pivot.test.tsx`, `frontend/src/components/DataProfile.logic.test.ts`.

**수정**
- `app/services/digest_sections.py` — hybrid kind + PIVOT_SECTIONS + params + 렌더.
- `app/services/digest_service.py` — records_data 부착 + custom kwargs + hybrid llm_keys.
- `app/services/records_schema.py` — `bump_schema_version_if_changed`.
- `app/services/entity_service.py` — `apply_merge_cluster` 공개 wrapper.
- `app/services/scheduler.py` — enrich 일일 배치 등록.
- `app/routers/profile.py` — GET 확장 + PUT + proposal apply/dismiss.
- `app/main.py` — entities 라우터 등록.
- `frontend/src/api/types.ts`, `api/profile.ts`, `pages/DigestDetail.tsx`, `pages/Settings.tsx`, `settings/defs.ts`, `components/DigestSectionBuilder.tsx`, `components/DigestConfigsEditor.tsx`.

**환경 주의(전 태스크):** homebrew python으로 `python -m pytest`(드라이브 `.venv` 깨짐). `postgres-ytdb` MCP는 프로덕션 — 쓰기 금지. 프론트는 `cd frontend && npm run test`.

---

### Task 1: records_pivot 순수 변환 함수

**Files:**
- Create: `app/services/records_pivot.py`
- Test: `tests/test_records_pivot.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_records_pivot.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.records_pivot`.

- [ ] **Step 3: Implement the pure functions**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_records_pivot.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/records_pivot.py tests/test_records_pivot.py
git commit -m "feat: records 피벗 순수 변환 함수(Phase 3)"
```

---

### Task 2: build_records_data — SQL 로더

**Files:**
- Modify: `app/services/records_pivot.py`
- Test: `tests/test_records_pivot.py`

- [ ] **Step 1: Write the failing test (스텁 세션)**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_records_pivot.py -k build_records -v`
Expected: FAIL — `ImportError: cannot import name 'build_records_data'`.

- [ ] **Step 3: Implement the loader**

`app/services/records_pivot.py`에 추가:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_records_pivot.py -v`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/records_pivot.py tests/test_records_pivot.py
git commit -m "feat: build_records_data 피벗 SQL 로더(Phase 3)"
```

---

### Task 3: digest_sections — hybrid kind + 피벗 레지스트리

**Files:**
- Modify: `app/services/digest_sections.py`
- Test: `tests/test_digest_sections_pivot.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_digest_sections_pivot.py
from app.services.digest_sections import (
    PIVOT_SECTIONS, assemble_output_sections, build_computed_data,
    build_structured_prompt, normalize_sections, sections_to_markdown,
)


class _Agg:
    video_count = 3
    sentiment_breakdown = {}
    top_tags = []
    top_channels = []
    videos = []
    records_data = {
        "entity_pivot": {"items": [{"entity": "SoftBank", "count": 2, "samples": ["5G"]}]},
    }


def test_normalize_accepts_hybrid_pivot_with_params():
    raw = [{"key": "entity_pivot", "kind": "hybrid",
            "params": {"record_type": "campaign", "top_k": 5, "junk": "x"}}]
    out = normalize_sections(raw)
    assert out[0]["kind"] == "hybrid"
    assert out[0]["title"] == PIVOT_SECTIONS["entity_pivot"]
    assert out[0]["params"] == {"record_type": "campaign", "top_k": 5}


def test_normalize_drops_hybrid_unknown_key_and_bad_topk():
    assert normalize_sections([{"key": "nope", "kind": "hybrid"}]) == []
    out = normalize_sections([{"key": "top_records", "kind": "hybrid",
                               "params": {"top_k": 99}}])
    assert "params" not in out[0]


def test_build_computed_data_reads_records_data():
    d = build_computed_data("entity_pivot", _Agg())
    assert d["items"][0]["entity"] == "SoftBank"
    assert build_computed_data("period_compare", _Agg()) == {}


def test_assemble_hybrid_merges_body_and_data():
    sections = [{"key": "entity_pivot", "kind": "hybrid", "title": "집중"}]
    out = assemble_output_sections(sections, llm_bodies={"entity_pivot": "서술"}, agg=_Agg())
    assert out[0]["body_md"] == "서술"
    assert out[0]["data"]["items"][0]["entity"] == "SoftBank"


def test_assemble_hybrid_skips_when_empty():
    sections = [{"key": "period_compare", "kind": "hybrid", "title": "대비"}]
    assert assemble_output_sections(sections, llm_bodies={}, agg=_Agg()) == []


def test_sections_markdown_renders_pivot_data():
    secs = [{"key": "entity_pivot", "kind": "hybrid", "title": "집중",
             "body_md": "서술",
             "data": {"items": [{"entity": "SoftBank", "count": 2, "samples": ["5G"]}]}}]
    md = sections_to_markdown(secs)
    assert "서술" in md and "SoftBank" in md


def test_structured_prompt_includes_hybrid_schema_and_records():
    sections = [
        {"key": "overview", "kind": "llm", "title": "요약", "guide": "g"},
        {"key": "entity_pivot", "kind": "hybrid", "title": "집중", "guide": "피벗 서술"},
    ]
    p = build_structured_prompt(
        persona="p", data_block="D", sections=sections, records_data=_Agg.records_data)
    assert '"entity_pivot"' in p
    assert "레코드 집계" in p and "SoftBank" in p
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_digest_sections_pivot.py -v`
Expected: FAIL — `ImportError: cannot import name 'PIVOT_SECTIONS'`.

- [ ] **Step 3: Extend digest_sections.py**

상수 추가(`SECTION_KIND_COMPUTED` 아래):

```python
SECTION_KIND_HYBRID = "hybrid"

# 피벗 섹션 레지스트리(Phase 3): data는 agg.records_data에서 온다(레코드 기반).
PIVOT_SECTIONS: dict[str, str] = {
    "entity_pivot": "엔티티 집중 분석",
    "period_compare": "지난 기간 대비",
    "top_records": "수치 상위",
}
```

params 정규화 헬퍼(`_clean` 아래):

```python
def _clean_pivot_params(raw: Any) -> dict:
    """피벗 params 정규화: record_type/group_by(str), top_k(1~20 int)만 통과."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for k in ("record_type", "group_by"):
        v = _clean(raw.get(k))
        if v:
            out[k] = v
    try:
        tk = int(raw.get("top_k"))
        if 1 <= tk <= 20:
            out["top_k"] = tk
    except (TypeError, ValueError):
        pass
    return out
```

`normalize_sections` 루프 본문 교체(기존 key/kind 검사~guide 처리 부분):

```python
        key = _clean(item.get("key"))
        kind = _clean(item.get("kind"))
        if not key or kind not in (SECTION_KIND_LLM, SECTION_KIND_COMPUTED, SECTION_KIND_HYBRID):
            continue
        if kind == SECTION_KIND_COMPUTED and key not in COMPUTED_SECTIONS:
            continue
        if kind == SECTION_KIND_HYBRID and key not in PIVOT_SECTIONS:
            continue
        title = _clean(item.get("title"))
        if not title:
            if kind == SECTION_KIND_COMPUTED:
                title = COMPUTED_SECTIONS.get(key, key)
            elif kind == SECTION_KIND_HYBRID:
                title = PIVOT_SECTIONS.get(key, key)
            else:
                title = key
        section: dict[str, Any] = {"key": key, "kind": kind, "title": title}
        guide = _clean(item.get("guide"))[:_MAX_GUIDE_LEN]
        if kind in (SECTION_KIND_LLM, SECTION_KIND_HYBRID) and guide:
            section["guide"] = guide
        if kind == SECTION_KIND_HYBRID:
            params = _clean_pivot_params(item.get("params"))
            if params:
                section["params"] = params
        out.append(section)
```

`build_computed_data` 함수 맨 앞에 추가:

```python
    if key in PIVOT_SECTIONS:
        return dict((getattr(agg, "records_data", {}) or {}).get(key) or {})
```

`_computed_to_markdown`의 elif 체인에 세 renderer 추가(`top_viewed` 분기 뒤):

```python
    elif key == "entity_pivot":
        for it in data.get("items") or []:
            samples = " / ".join(it.get("samples") or [])
            by = it.get("by") or {}
            suffix = f" — {samples}" if samples else ""
            if by:
                suffix += " (" + ", ".join(f"{k} {v}" for k, v in by.items()) + ")"
            lines.append(f"- **{it.get('entity')}** {it.get('count')}건{suffix}")
    elif key == "period_compare":
        for label, arr_key in (("신규", "new"), ("소멸", "gone")):
            arr = data.get(arr_key) or []
            if arr:
                lines.append(f"- {label}: " + ", ".join(x.get("entity", "") for x in arr))
        for x in data.get("continuing") or []:
            lines.append(f"- 지속: {x.get('entity')} ({x.get('prev')}→{x.get('cur')}건)")
    elif key == "top_records":
        for it in data.get("items") or []:
            head = it.get("entity") or it.get("text") or ""
            date_txt = f" · {it.get('date')}" if it.get("date") else ""
            lines.append(f"- {head}: {it.get('value')}{date_txt}")
```

`sections_to_markdown`의 body 분기 교체:

```python
        if s.get("kind") == SECTION_KIND_LLM:
            body = _clean(s.get("body_md"))
        elif s.get("kind") == SECTION_KIND_HYBRID:
            parts = [p for p in (_clean(s.get("body_md")), _computed_to_markdown(s)) if p]
            body = "\n\n".join(parts)
        else:
            body = _computed_to_markdown(s)
```

`build_structured_prompt` 시그니처·본문 수정:

```python
def build_structured_prompt(
    *, persona: str, data_block: str, sections: list[dict[str, Any]],
    records_data: dict | None = None,
) -> str:
    """페르소나(1층) + 데이터 블록 + llm/hybrid 섹션 출력 스키마(2층)로 프롬프트 조립."""
    persona = persona.strip() or "너는 유튜브 콘텐츠를 종합하는 애널리스트다."
    llm_sections = [
        s for s in sections
        if s.get("kind") in (SECTION_KIND_LLM, SECTION_KIND_HYBRID)
    ]
    schema_lines = []
    for s in llm_sections:
        guide = _clean(s.get("guide")) or s.get("title") or s.get("key")
        schema_lines.append(f'    {{"key": "{s["key"]}", "body_md": "<{guide}>"}}')
    sections_schema = ",\n".join(schema_lines)
    records_block = ""
    if records_data:
        records_block = (
            "\n\n## 레코드 집계(피벗) — 해당 섹션은 아래 수치를 근거로 서술하라\n"
            + json.dumps(records_data, ensure_ascii=False)
        )
    return f"""{persona}

아래 자료를 바탕으로 이번 기간을 한국어 개조식('~함','~임')으로 종합하라.
개별 영상 나열이 아니라 여러 영상에 걸친 흐름을 묶어 서술할 것.

## 자료
{data_block}{records_block}

## 출력 형식
반드시 아래 JSON으로만 출력. sections 배열은 지정된 key를 순서대로 포함:
{{
  "headline": "<이모지 1~2개 포함, 이번 기간 핵심 한 줄(40자 이내)>",
  "sections": [
{sections_schema}
  ],
  "telegram_summary": "<텔레그램용 400자 이내 일반 텍스트 브리핑>"
}}"""
```

`assemble_output_sections` 분기 교체:

```python
    for s in sections:
        base = {"key": s["key"], "kind": s["kind"], "title": s.get("title", "")}
        if s["kind"] == SECTION_KIND_LLM:
            body = llm_bodies.get(s["key"], "")
            if not body:
                continue
            out.append({**base, "body_md": body})
        elif s["kind"] == SECTION_KIND_HYBRID:
            data = build_computed_data(s["key"], agg)
            body = llm_bodies.get(s["key"], "")
            if not body and not any(bool(v) for v in data.values()):
                continue  # 데이터·서술 모두 빈 하이브리드 섹션은 생략
            sec = {**base, "data": data}
            if body:
                sec["body_md"] = body
            out.append(sec)
        else:
            out.append({**base, "data": build_computed_data(s["key"], agg)})
```

- [ ] **Step 4: Run tests (신규 + 기존 섹션 테스트 회귀)**

Run: `python -m pytest tests/test_digest_sections_pivot.py tests/test_digest_sections.py tests/test_digest_structured.py -v`
Expected: PASS 전부.

- [ ] **Step 5: Commit**

```bash
git add app/services/digest_sections.py tests/test_digest_sections_pivot.py
git commit -m "feat: digest 섹션 hybrid kind + 피벗 레지스트리(Phase 3)"
```

---

### Task 4: digest_service 배선 — records_data 부착 + {records_block}

**Files:**
- Modify: `app/services/digest_service.py`
- Test: `tests/test_digest_records_block.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_digest_records_block.py -v`
Expected: FAIL — `ImportError: cannot import name 'custom_prompt_kwargs'` (또는 `records_data` 인자 TypeError).

- [ ] **Step 3: Wire digest_service.py**

import 추가(파일 상단, 기존 digest_sections import 라인에 `SECTION_KIND_HYBRID` 추가):

```python
from app.services.records_pivot import build_records_data, records_block_text
```

`DigestAggregate` dataclass(line 176 부근)에 필드 추가:

```python
    records_data: dict = field(default_factory=dict)  # Phase 3: 피벗 집계 {key: data}
```

custom kwargs 헬퍼 추가(`_render_payload` 아래, 순수 함수):

```python
def custom_prompt_kwargs(
    aggregate: DigestAggregate, *, category: str, period_label: str, previous_digest: str
) -> dict:
    """custom digest_prompt .format() 인자 (순수). Phase 3: records_block 추가."""
    return {
        "category": category or "전체",
        "period_label": period_label,
        "video_count": aggregate.video_count,
        "sentiment_summary": _sentiment_summary_text(aggregate.sentiment_breakdown),
        "top_tags": ", ".join(t["name"] for t in aggregate.top_tags[:8]) or "없음",
        "top_channels": ", ".join(
            f"{c['name']}({c['count']})" for c in aggregate.top_channels[:10]) or "없음",
        "top_viewed": _build_top_viewed_block(aggregate.videos),
        "previous_digest": previous_digest,
        "videos_block": _build_videos_block(aggregate.videos, aggregate.video_count),
        "records_block": records_block_text(getattr(aggregate, "records_data", {}) or {}),
    }
```

`synthesize_with_llm` custom 분기(line 383-393)의 `prompt.format(...)` 호출을 교체:

```python
            user_msg = prompt.format(**custom_prompt_kwargs(
                aggregate, category=category, period_label=period_label,
                previous_digest=previous_digest,
            ))
```

structured 분기 수정 — `build_structured_prompt` 호출에 records_data 전달:

```python
        user_msg = build_structured_prompt(
            persona=getattr(profile, "persona", ""),
            data_block=data_block, sections=sections_spec,
            records_data=(aggregate.records_data or None),
        )
```

`llm_keys` 계산(line 444)을 hybrid 포함으로 교체:

```python
        llm_keys = [
            s["key"] for s in sections_spec
            if s["kind"] in (SECTION_KIND_LLM, SECTION_KIND_HYBRID)
        ]
```

`generate_digest_for_group`의 `agg = await aggregate_period(...)` 직후에 부착(실패 무시):

```python
        # Phase 3: record_schema 보유 그룹은 피벗 집계를 agg에 부착(실패는 무시).
        try:
            profile = await get_settings_manager().get_profile(group.group_id)
            if profile.record_schema.get("types"):
                sections_spec = resolve_sections(digest_cfg.sections, profile.digest_sections)
                agg.records_data = await build_records_data(
                    session, sections=sections_spec,
                    record_schema=profile.record_schema,
                    period_start=period_start, period_end=period_end,
                )
        except Exception as e:  # noqa: BLE001 — 피벗 실패가 다이제스트를 막지 않는다
            print(f"[digest] records_data 집계 실패(무시): {e}")
```

- [ ] **Step 4: Run tests + digest 회귀**

Run: `python -m pytest tests/test_digest_records_block.py tests/test_digest_structured.py tests/test_digest_helpers.py tests/test_digest_configs.py -v`
Expected: PASS 전부. (record_schema 없는 그룹: `profile.record_schema["types"]` 빈 배열 → records_data 미계산 → records_block "없음" → 완전 무변경.)

- [ ] **Step 5: Full suite**

Run: `python -m pytest tests/ -q`
Expected: 기존 baseline 실패(`test_instant_analyze_daily_quota_400`) 외 전부 PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/digest_service.py tests/test_digest_records_block.py
git commit -m "feat: digest에 피벗 records_data 부착 + {records_block} placeholder(Phase 3)"
```

---

### Task 5: bump helper + profile 라우터 GET 확장·PUT 편집

**Files:**
- Modify: `app/services/records_schema.py`
- Modify: `app/routers/profile.py`
- Test: `tests/test_records_schema.py`, `tests/test_profile_api.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_records_schema.py`에 추가:

```python
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
```

`tests/test_profile_api.py`에 추가:

```python
def test_put_profile_route_registered():
    methods = set()
    for r in profile_router.router.routes:
        if r.path == "/api/groups/{slug}/profile":
            methods |= r.methods
    assert "PUT" in methods and "GET" in methods
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_records_schema.py -k bump tests/test_profile_api.py -v`
Expected: FAIL — `ImportError: bump_schema_version_if_changed` / PUT 미등록.

- [ ] **Step 3: Implement bump helper**

`app/services/records_schema.py` 끝에 추가:

```python
def bump_schema_version_if_changed(old: Any, new: Any) -> dict:
    """정규화 후 types가 달라졌으면 version=old+1, 같으면 old version 유지."""
    old_n = normalize_record_schema(old)
    new_n = normalize_record_schema(new)
    old_v = old_n.get("version") or 1
    if old_n.get("types") != new_n.get("types"):
        return {"version": old_v + 1, "types": new_n["types"]}
    return {"version": old_v, "types": new_n["types"]}
```

- [ ] **Step 4: Rewrite profile router**

`app/routers/profile.py` 전체 교체:

```python
"""그룹 프로필 조회·재생성·편집 API (Phase 3: record_schema·vocab·보강 제안 포함)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.models.control.group import Group
from app.routers.deps import get_group_or_404
from app.services.bootstrap_service import bootstrap_profile
from app.services.digest_sections import normalize_sections
from app.services.records_schema import bump_schema_version_if_changed, normalize_vocab
from app.services.settings_manager import get_settings_manager

router = APIRouter(prefix="/api/groups/{slug}/profile", tags=["profile"])


async def _profile_payload(group_id: int) -> dict:
    mgr = get_settings_manager()
    p = await mgr.get_profile(group_id)
    d = await mgr.get_typed(group_id, "profile")
    proposal = d.get("enrich_proposal")
    pending = d.get("vocab_pending")
    return {
        "persona": p.persona,
        "digest_sections": p.digest_sections,
        "bootstrap_status": p.bootstrap_status,
        "bootstrap_at": p.bootstrap_at,
        "record_schema": p.record_schema,
        "vocab": p.vocab,
        "vocab_pending": pending if isinstance(pending, list) else [],
        "enrich_proposal": proposal if isinstance(proposal, dict) else {},
    }


@router.get("")
async def get_profile(group: Group = Depends(get_group_or_404)) -> dict:
    return await _profile_payload(group.group_id)


@router.post("/regenerate")
async def regenerate_profile(group: Group = Depends(get_group_or_404)) -> dict:
    await bootstrap_profile(group, force=True)
    return await _profile_payload(group.group_id)


class ProfileUpdate(BaseModel):
    persona: str | None = None
    digest_sections: list[dict] | None = None
    record_schema: dict | None = None
    vocab: dict | None = None


@router.put("")
async def put_profile(
    body: ProfileUpdate, group: Group = Depends(get_group_or_404)
) -> dict:
    """L2 편집: 제공된 필드만 정규화해 저장. record_schema 변경은 version 증가."""
    mgr = get_settings_manager()
    current = await mgr.get_profile(group.group_id)
    items: list[dict] = []
    if body.persona is not None:
        items.append({"key": "persona", "value": body.persona.strip(),
                      "value_type": "string"})
    if body.digest_sections is not None:
        sections = normalize_sections(body.digest_sections)
        items.append({"key": "digest_sections",
                      "value": json.dumps(sections, ensure_ascii=False),
                      "value_type": "json"})
    if body.record_schema is not None:
        schema = bump_schema_version_if_changed(current.record_schema, body.record_schema)
        items.append({"key": "record_schema",
                      "value": json.dumps(schema, ensure_ascii=False),
                      "value_type": "json"})
    if body.vocab is not None:
        vocab = normalize_vocab(body.vocab)
        items.append({"key": "vocab", "value": json.dumps(vocab, ensure_ascii=False),
                      "value_type": "json"})
    if items:
        await mgr.set_values(group.group_id, "profile", items)
    return await _profile_payload(group.group_id)
```

(기존 GET/regenerate 응답에 record_schema 등 4개 key가 추가됨 — 프론트 기존 필드는 그대로라 하위 호환.)

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_records_schema.py tests/test_profile_api.py tests/test_group_profile.py -v`
Expected: PASS 전부.

- [ ] **Step 6: Commit**

```bash
git add app/services/records_schema.py app/routers/profile.py tests/test_records_schema.py tests/test_profile_api.py
git commit -m "feat: 프로필 PUT 편집 + record_schema 버전 bump(Phase 3)"
```

---

### Task 6: 프론트 — DigestSection 타입 확장 + DigestDetail hybrid 렌더

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/pages/DigestDetail.tsx`
- Test: `frontend/src/pages/DigestDetail.pivot.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/pages/DigestDetail.pivot.test.tsx
import { describe, expect, it } from 'vitest'
import { computedToMarkdown } from './DigestDetail'
import type { DigestSection } from '../api/types'

describe('pivot section markdown', () => {
  it('renders entity_pivot items', () => {
    const s: DigestSection = {
      key: 'entity_pivot', kind: 'hybrid', title: '집중',
      data: { items: [{ entity: 'SoftBank', count: 2, samples: ['5G'] }] },
    }
    const md = computedToMarkdown(s)
    expect(md).toContain('SoftBank')
    expect(md).toContain('2건')
  })

  it('renders period_compare new/gone/continuing', () => {
    const s: DigestSection = {
      key: 'period_compare', kind: 'hybrid', title: '대비',
      data: {
        new: [{ entity: 'A', count: 1 }], gone: [],
        continuing: [{ entity: 'B', cur: 2, prev: 1 }],
      },
    }
    const md = computedToMarkdown(s)
    expect(md).toContain('신규: A')
    expect(md).toContain('지속: B (1→2건)')
  })

  it('renders top_records values', () => {
    const s: DigestSection = {
      key: 'top_records', kind: 'hybrid', title: '상위',
      data: { items: [{ entity: 'A', value: 1200, date: '2026-07-01' }] },
    }
    expect(computedToMarkdown(s)).toContain('A: 1200 · 2026-07-01')
  })

  it('empty hybrid returns empty string', () => {
    const s: DigestSection = { key: 'period_compare', kind: 'hybrid', title: '대비', data: {} }
    expect(computedToMarkdown(s)).toBe('')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test -- --run src/pages/DigestDetail.pivot.test.tsx`
Expected: FAIL — `computedToMarkdown` 미export / `'hybrid'` 타입 에러.

- [ ] **Step 3: Extend types.ts**

`frontend/src/api/types.ts`의 `DigestSection`을 교체:

```ts
export interface DigestSection {
  key: string
  kind: 'llm' | 'computed' | 'hybrid'
  title: string
  guide?: string
  body_md?: string
  data?: Record<string, unknown>
  params?: Record<string, unknown>
}
```

- [ ] **Step 4: Extend DigestDetail.tsx**

`computedToMarkdown`을 `export function`으로 바꾸고, 마지막 `return ''` 앞에 피벗 케이스 추가:

```ts
  if (s.key === 'entity_pivot') {
    const pitems = (s.data?.items as { entity?: string; count?: number; samples?: string[] }[]) ?? []
    return pitems.map((it) => {
      const samples = it.samples?.length ? ` — ${it.samples.join(' / ')}` : ''
      return `- **${it.entity}** ${it.count}건${samples}`
    }).join('\n')
  }
  if (s.key === 'period_compare') {
    const d = s.data as {
      new?: { entity: string }[]; gone?: { entity: string }[]
      continuing?: { entity: string; cur: number; prev: number }[]
    } | undefined
    const lines: string[] = []
    if (d?.new?.length) lines.push(`- 신규: ${d.new.map((x) => x.entity).join(', ')}`)
    if (d?.gone?.length) lines.push(`- 소멸: ${d.gone.map((x) => x.entity).join(', ')}`)
    d?.continuing?.forEach((x) => lines.push(`- 지속: ${x.entity} (${x.prev}→${x.cur}건)`))
    return lines.join('\n')
  }
  if (s.key === 'top_records') {
    const ritems = (s.data?.items as {
      entity?: string | null; value?: number; text?: string | null; date?: string | null
    }[]) ?? []
    return ritems
      .map((it) => `- ${it.entity ?? it.text ?? ''}: ${it.value}${it.date ? ` · ${it.date}` : ''}`)
      .join('\n')
  }
```

렌더부(line 82-84)의 body 계산 교체:

```ts
      {toRenderSections(digest).map((s) => {
        const computed = s.kind !== 'llm' ? computedToMarkdown(s) : ''
        const body = s.kind === 'llm' ? (s.body_md ?? '')
          : s.kind === 'hybrid'
            ? [s.body_md ?? '', computed].filter((x) => x.trim()).join('\n\n')
            : computed
        if (!body.trim()) return null
```

- [ ] **Step 5: Run tests (신규 + 기존 프론트 회귀)**

Run: `cd frontend && npm run test -- --run`
Expected: 신규 4건 포함 전부 PASS(기존 45+).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/pages/DigestDetail.tsx frontend/src/pages/DigestDetail.pivot.test.tsx
git commit -m "feat: 프론트 hybrid 피벗 섹션 렌더(Phase 3)"
```

---

### Task 7: 프론트 — 섹션 빌더 피벗 추가 + 설정 배선

**Files:**
- Modify: `frontend/src/components/DigestSectionBuilder.tsx`
- Modify: `frontend/src/components/DigestConfigsEditor.tsx`
- Modify: `frontend/src/pages/Settings.tsx`
- Test: `frontend/src/components/DigestSectionBuilder.pivot.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/components/DigestSectionBuilder.pivot.test.tsx
import { describe, expect, it } from 'vitest'
import { addSection, PIVOT_SECTION_DEFS, setSectionParam } from './DigestSectionBuilder'

describe('pivot section add', () => {
  it('adds hybrid section with default record_type', () => {
    const out = addSection([], { key: 'entity_pivot', kind: 'hybrid' }, ['campaign', 'topic'])
    expect(out[0].kind).toBe('hybrid')
    expect(out[0].params?.record_type).toBe('campaign')
  })

  it('setSectionParam updates record_type', () => {
    const secs = addSection([], { key: 'top_records', kind: 'hybrid' }, ['a', 'b'])
    const out = setSectionParam(secs, 'top_records', 'record_type', 'b')
    expect(out[0].params?.record_type).toBe('b')
  })

  it('pivot defs cover three keys', () => {
    expect(PIVOT_SECTION_DEFS.map((p) => p.key))
      .toEqual(['entity_pivot', 'period_compare', 'top_records'])
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test -- --run src/components/DigestSectionBuilder.pivot.test.tsx`
Expected: FAIL — `PIVOT_SECTION_DEFS` 미export.

- [ ] **Step 3: Extend DigestSectionBuilder.tsx**

export 추가(`COMPUTED_SECTION_DEFS` 아래):

```ts
export const PIVOT_SECTION_DEFS: { key: string; title: string }[] = [
  { key: 'entity_pivot', title: '엔티티 집중 분석' },
  { key: 'period_compare', title: '지난 기간 대비' },
  { key: 'top_records', title: '수치 상위' },
]
```

`addSection` 교체(hybrid 지원 + recordTypes 인자):

```ts
export function addSection(
  sections: DigestSection[],
  add: { key: string; kind: 'llm' | 'computed' | 'hybrid' },
  recordTypes: string[] = [],
): DigestSection[] {
  if (add.kind === 'hybrid') {
    const def = PIVOT_SECTION_DEFS.find((d) => d.key === add.key)
    return [...sections, {
      key: add.key, kind: 'hybrid', title: def?.title ?? add.key,
      params: recordTypes.length ? { record_type: recordTypes[0] } : {},
    }]
  }
  if (add.kind === 'computed') {
    const def = COMPUTED_SECTION_DEFS.find((d) => d.key === add.key)
    return [...sections, { key: add.key, kind: 'computed', title: def?.title ?? add.key }]
  }
  const preset = LLM_PRESETS.find((p) => p.key === add.key)
  return [...sections, {
    key: add.key, kind: 'llm', title: preset?.title ?? add.key, guide: preset?.guide ?? '',
  }]
}

export function setSectionParam(
  sections: DigestSection[], key: string, name: string, value: string,
): DigestSection[] {
  return sections.map((s) =>
    s.key === key ? { ...s, params: { ...(s.params ?? {}), [name]: value } } : s)
}
```

컴포넌트 교체(Props에 recordTypes 추가, 피벗 addable·renderExtra):

```ts
interface Props {
  sections: DigestSection[]
  onChange: (s: DigestSection[]) => void
  recordTypes?: string[]
}

export default function DigestSectionBuilder({ sections, onChange, recordTypes = [] }: Props) {
  const addable = [
    ...ALL_ADDABLE,
    ...(recordTypes.length
      ? PIVOT_SECTION_DEFS.map((p) => ({ key: p.key, label: `${p.title} (레코드)` }))
      : []),
  ]
  const includedKeys = new Set(sections.map((s) => s.key))
  const available = addable.filter((a) => !includedKeys.has(a.key))
  const kindOf = (key: string): 'llm' | 'computed' | 'hybrid' =>
    PIVOT_SECTION_DEFS.some((p) => p.key === key) ? 'hybrid'
      : COMPUTED_SECTION_DEFS.some((c) => c.key === key) ? 'computed' : 'llm'

  return (
    <OrderedItemsBuilder
      included={sections.map((s) => ({
        key: s.key,
        label: `${s.title}${s.kind === 'computed' ? ' (자동)' : s.kind === 'hybrid' ? ' (레코드)' : ''}`,
      }))}
      available={available}
      onMove={(idx, dir) => onChange(moveItem(sections, idx, dir))}
      onRemove={(key) => onChange(removeSection(sections, key))}
      onAdd={(key) => onChange(addSection(sections, { key, kind: kindOf(key) }, recordTypes))}
      renderExtra={(key) => {
        const s = sections.find((x) => x.key === key)
        if (!s) return null
        const idx = sections.findIndex((x) => x.key === key)
        if (s.kind === 'llm') {
          return (
            <input
              className="mt-1 w-full border border-gray-200 rounded px-2 py-1 text-xs text-gray-600"
              placeholder="작성 지침 (선택)"
              value={s.guide ?? ''}
              onChange={(e) => {
                const next = [...sections]
                next[idx] = { ...s, guide: e.target.value }
                onChange(next)
              }}
            />
          )
        }
        if (s.kind === 'hybrid' && recordTypes.length > 1) {
          return (
            <select
              className="mt-1 border border-gray-200 rounded px-2 py-1 text-xs text-gray-600"
              value={(s.params?.record_type as string) ?? recordTypes[0]}
              onChange={(e) => onChange(setSectionParam(sections, key, 'record_type', e.target.value))}
            >
              {recordTypes.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          )
        }
        return null
      }}
    />
  )
}
```

- [ ] **Step 4: Thread recordTypes through DigestConfigsEditor**

`frontend/src/components/DigestConfigsEditor.tsx`:
- Props interface에 `recordTypes?: string[]` 추가.
- 컴포넌트 시그니처에 `recordTypes = []` 추가.
- line 245 부근 `<DigestSectionBuilder sections={...} onChange={...} />`에 `recordTypes={recordTypes}` prop 추가.

- [ ] **Step 5: Load record types in Settings.tsx (digest 탭)**

import 추가:

```ts
import { profileApi } from '../api/profile'
```

state 추가(`const [modelMsg, ...]` 아래):

```ts
  const [recordTypes, setRecordTypes] = useState<string[]>([])
```

`load()` 안 `if (isPresetMode)` 아래에 추가:

```ts
      if (isDigest) {
        try {
          const p = await profileApi(activeSlug).get()
          setRecordTypes((p.record_schema?.types ?? []).map((t) => t.type_key))
        } catch {
          setRecordTypes([])
        }
      }
```

(주의: `load`의 useCallback 의존성 배열에 `isDigest`가 이미 없으면 추가.)

렌더의 `<DigestConfigsEditor items={items} saving={saving} onSave={handleSave} />`에 `recordTypes={recordTypes}` 추가.

`api/profile.ts`의 `GroupProfile`에 optional 필드가 아직 없으므로 이 태스크에서 최소 확장:

```ts
export interface GroupProfile {
  persona: string
  digest_sections: DigestSection[]
  bootstrap_status: string
  bootstrap_at?: string
  record_schema?: { version: number; types: { type_key: string; label: string; fields: unknown[] }[] }
}
```

(Task 11에서 전체 타입으로 재정의한다.)

- [ ] **Step 6: Run tests + typecheck**

Run: `cd frontend && npm run test -- --run && npx tsc --noEmit`
Expected: 전부 PASS, 타입 에러 0.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/DigestSectionBuilder.tsx frontend/src/components/DigestSectionBuilder.pivot.test.tsx frontend/src/components/DigestConfigsEditor.tsx frontend/src/pages/Settings.tsx frontend/src/api/profile.ts
git commit -m "feat: 섹션 빌더 피벗(hybrid) 추가 + digest 설정 배선(Phase 3)"
```

---

### Task 8: enrichment_service — 순수부(프롬프트·정규화·적용·조건)

**Files:**
- Create: `app/services/enrichment_service.py`
- Test: `tests/test_enrichment_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_enrichment_service.py
import json
from datetime import datetime, timedelta, timezone

from app.services.enrichment_service import (
    apply_proposal_items, build_enrich_prompt, normalize_enrich_proposal,
    proposal_is_empty, should_enrich,
)

_NOW = datetime(2026, 7, 22, tzinfo=timezone.utc)


def test_should_enrich_conditions():
    kw = dict(analysis_count=10, last_at="", has_proposal=False, now=_NOW)
    assert should_enrich(**kw) is True
    assert should_enrich(**{**kw, "analysis_count": 9}) is False
    assert should_enrich(**{**kw, "has_proposal": True}) is False
    recent = (_NOW - timedelta(days=5)).isoformat()
    old = (_NOW - timedelta(days=31)).isoformat()
    assert should_enrich(**{**kw, "last_at": recent}) is False
    assert should_enrich(**{**kw, "last_at": old}) is True


def test_normalize_proposal_filters_and_caps():
    raw = json.dumps({
        "sections_add": [
            {"key": "risks", "kind": "llm", "title": "리스크", "guide": "g"},
            {"key": "top_viewed", "kind": "computed", "title": "x"},  # llm 아님 → drop
        ],
        "record_fields_add": [
            {"type_key": "campaign",
             "field": {"key": "region", "label": "지역", "datatype": "weird"}},
        ],
        "vocab_add": {"sentiment": {"values": ["중립"], "synonyms": {"neutral": "중립"}}},
        "entity_attrs_add": [{"entity": "SoftBank", "attrs": {"region": "일본"}}],
        "note": "보강",
    })
    p = normalize_enrich_proposal(raw)
    assert [s["key"] for s in p["sections_add"]] == ["risks"]
    assert p["record_fields_add"][0]["field"]["datatype"] == "text"  # weird → text
    assert p["entity_attrs_add"][0]["entity"] == "SoftBank"
    assert p["note"] == "보강"
    assert "created_at" in p


def test_normalize_proposal_empty_returns_empty_dict():
    assert normalize_enrich_proposal("garbage") == {}
    assert normalize_enrich_proposal('{"sections_add": [], "note": "없음"}') == {}


def test_proposal_is_empty():
    assert proposal_is_empty({}) is True
    assert proposal_is_empty(None) is True
    assert proposal_is_empty({"note": "x"}) is True
    assert proposal_is_empty(
        {"vocab_add": {"a": {"values": ["v"], "synonyms": {}}}}) is False


def test_apply_proposal_items_merges_and_bumps_version():
    profile = {
        "digest_sections": [{"key": "overview", "kind": "llm", "title": "요약", "guide": "g"}],
        "record_schema": {"version": 1, "types": [
            {"type_key": "campaign", "label": "캠페인", "fields": [
                {"key": "entity", "label": "브랜드", "datatype": "entity", "required": True}]}]},
        "vocab": {"sentiment": {"label": "평가", "values": ["긍정"], "synonyms": {}}},
    }
    proposal = {
        "sections_add": [{"key": "risks", "kind": "llm", "title": "리스크", "guide": "g"}],
        "record_fields_add": [{"type_key": "campaign", "field": {
            "key": "region", "label": "지역", "datatype": "text", "required": False}}],
        "vocab_add": {"sentiment": {"label": "평가", "values": ["중립"],
                                    "synonyms": {"neutral": "중립"}}},
        "entity_attrs_add": [],
        "note": "n",
    }
    items = {i["key"]: i["value"] for i in apply_proposal_items(profile, proposal)}
    sections = json.loads(items["digest_sections"])
    assert [s["key"] for s in sections] == ["overview", "risks"]
    schema = json.loads(items["record_schema"])
    assert schema["version"] == 2
    assert [f["key"] for f in schema["types"][0]["fields"]] == ["entity", "region"]
    vocab = json.loads(items["vocab"])
    assert vocab["sentiment"]["values"] == ["긍정", "중립"]
    assert vocab["sentiment"]["synonyms"]["neutral"] == "중립"
    assert items["enrich_proposal"] == "{}"
    assert items["vocab_pending"] == "[]"


def test_apply_proposal_items_empty_returns_empty():
    assert apply_proposal_items({}, {}) == []


def test_build_enrich_prompt_includes_inputs():
    p = build_enrich_prompt(
        persona="p", sections=[], record_schema={"version": 1, "types": []},
        vocab={}, samples=["요약1"], vocab_pending=["sentiment:애매"],
        merge_holds=["A ← B"],
    )
    assert "요약1" in p and "sentiment:애매" in p and "A ← B" in p
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_enrichment_service.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.enrichment_service`.

- [ ] **Step 3: Implement the pure part**

```python
# app/services/enrichment_service.py
"""프로필 보강 제안 루프 (Phase 3).

분석 10건 누적 후 월 1회, 최근 표본 + vocab_pending + 병합 보류 후보를 입력으로
부트스트랩 LLM을 재호출해 제안 diff를 만든다. 자동 적용하지 않는다 —
profile.enrich_proposal에 저장하고 사용자가 [적용]/[무시]로 처리한다.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.services.digest_sections import normalize_sections
from app.services.records_schema import (
    bump_schema_version_if_changed, normalize_record_schema, normalize_vocab,
)

_DATATYPES = ("entity", "text", "number", "date")

_ENRICH_PROMPT = """너는 유튜브 모니터링 그룹의 리포트 프로필을 점검하는 어시스턴트다.
현재 프로필과 최근 관측을 보고 개선 제안을 JSON diff로만 출력하라.
확실히 유용한 것만 제안하라. 없으면 배열·객체를 비워라.

## 현재 프로필
- persona: {persona}
- digest_sections: {sections}
- record_schema: {record_schema}
- vocab: {vocab}

## 최근 관측
- 최근 분석 한줄 요약: {samples}
- 미매핑 어휘(vocab_pending): {vocab_pending}
- 엔티티 병합 보류 후보: {merge_holds}

## 제안 규칙
- sections_add: 기존에 없는 llm 섹션만, 최대 2개.
- record_fields_add: 기존 type_key에 새 field 추가만, 최대 3개. datatype은 entity|text|number|date.
- vocab_add: 기존 축 values/synonyms 확장 또는 새 축 1개. vocab_pending의 빈번 값을 우선 반영.
- entity_attrs_add: 알려진 엔티티의 속성 보강(예: {{"region": "일본"}}). 확실한 것만.

## 출력(JSON만)
{{"sections_add": [], "record_fields_add": [{{"type_key": "", "field": {{"key": "", "label": "", "datatype": "text", "required": false}}}}], "vocab_add": {{}}, "entity_attrs_add": [{{"entity": "", "attrs": {{}}}}], "note": "<한 줄 요지>"}}"""


def build_enrich_prompt(
    *, persona: str, sections: list, record_schema: dict, vocab: dict,
    samples: list[str], vocab_pending: list[str], merge_holds: list[str],
) -> str:
    return _ENRICH_PROMPT.format(
        persona=persona or "(없음)",
        sections=json.dumps(sections, ensure_ascii=False),
        record_schema=json.dumps(record_schema, ensure_ascii=False),
        vocab=json.dumps(vocab, ensure_ascii=False),
        samples=" / ".join(samples[:20]) or "(없음)",
        vocab_pending=", ".join(vocab_pending[:50]) or "(없음)",
        merge_holds="; ".join(merge_holds[:20]) or "(없음)",
    )


def normalize_enrich_proposal(raw: str) -> dict:
    """LLM 응답 → 검증된 제안 dict. 실질 내용이 없으면 {} (제안 없음)."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    sections_add = [
        s for s in normalize_sections(data.get("sections_add"))
        if s.get("kind") == "llm"
    ][:2]
    fields_add: list[dict] = []
    for it in (data.get("record_fields_add") or [])[:3]:
        if not isinstance(it, dict):
            continue
        tkey = str(it.get("type_key") or "").strip()
        f = it.get("field")
        if not tkey or not isinstance(f, dict):
            continue
        key = str(f.get("key") or "").strip()
        if not key:
            continue
        dt = str(f.get("datatype") or "text").strip().lower()
        fields_add.append({"type_key": tkey, "field": {
            "key": key,
            "label": str(f.get("label") or key).strip(),
            "datatype": dt if dt in _DATATYPES else "text",
            "required": bool(f.get("required")),
        }})
    vocab_add = normalize_vocab(data.get("vocab_add"))
    attrs_add: list[dict] = []
    for it in (data.get("entity_attrs_add") or [])[:10]:
        if not isinstance(it, dict):
            continue
        name = str(it.get("entity") or "").strip()
        attrs = it.get("attrs")
        if name and isinstance(attrs, dict) and attrs:
            attrs_add.append({"entity": name,
                              "attrs": {str(k): str(v) for k, v in attrs.items()}})
    if not (sections_add or fields_add or vocab_add or attrs_add):
        return {}
    return {
        "sections_add": sections_add,
        "record_fields_add": fields_add,
        "vocab_add": vocab_add,
        "entity_attrs_add": attrs_add,
        "note": str(data.get("note") or "").strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def proposal_is_empty(p) -> bool:
    if not isinstance(p, dict):
        return True
    return not any(p.get(k) for k in
                   ("sections_add", "record_fields_add", "vocab_add", "entity_attrs_add"))


def should_enrich(*, analysis_count: int, last_at: str, has_proposal: bool,
                  now: datetime) -> bool:
    """분석 10건 도달 + (첫 회 또는 30일 경과) + 미처리 제안 없음."""
    if has_proposal or analysis_count < 10:
        return False
    if not last_at:
        return True
    try:
        last = datetime.fromisoformat(last_at)
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (now - last).days >= 30


def apply_proposal_items(profile_typed: dict, proposal: dict) -> list[dict]:
    """제안 적용 → settings set_values 아이템(순수). 빈 제안이면 []."""
    if proposal_is_empty(proposal):
        return []
    sections = normalize_sections(profile_typed.get("digest_sections"))
    have = {s["key"] for s in sections}
    merged_sections = normalize_sections(
        sections + [s for s in proposal.get("sections_add") or []
                    if s.get("key") not in have])

    old_schema = normalize_record_schema(profile_typed.get("record_schema"))
    new_types = json.loads(json.dumps(old_schema["types"]))  # deep copy
    by_key = {t["type_key"]: t for t in new_types}
    for it in proposal.get("record_fields_add") or []:
        t = by_key.get(it.get("type_key"))
        f = it.get("field") or {}
        if t is None or not f.get("key"):
            continue
        if any(x["key"] == f["key"] for x in t["fields"]):
            continue
        t["fields"].append(f)
    new_schema = bump_schema_version_if_changed(
        old_schema, {"version": old_schema["version"], "types": new_types})

    vocab = normalize_vocab(profile_typed.get("vocab"))
    for axis, spec in (proposal.get("vocab_add") or {}).items():
        cur = vocab.get(axis) or {"label": str(spec.get("label") or axis),
                                  "values": [], "synonyms": {}}
        for v in spec.get("values") or []:
            if v not in cur["values"]:
                cur["values"].append(v)
        cur["synonyms"] = {**cur.get("synonyms", {}), **(spec.get("synonyms") or {})}
        vocab[axis] = cur

    return [
        {"key": "digest_sections",
         "value": json.dumps(merged_sections, ensure_ascii=False), "value_type": "json"},
        {"key": "record_schema",
         "value": json.dumps(new_schema, ensure_ascii=False), "value_type": "json"},
        {"key": "vocab", "value": json.dumps(vocab, ensure_ascii=False),
         "value_type": "json"},
        {"key": "enrich_proposal", "value": "{}", "value_type": "json"},
        {"key": "vocab_pending", "value": "[]", "value_type": "json"},
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_enrichment_service.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/enrichment_service.py tests/test_enrichment_service.py
git commit -m "feat: 프로필 보강 제안 순수부 — 정규화·적용·조건(Phase 3)"
```

---

### Task 9: enrichment 배치 + 스케줄러 + proposal 라우트

**Files:**
- Modify: `app/services/enrichment_service.py`
- Modify: `app/services/scheduler.py`
- Modify: `app/routers/profile.py`
- Test: `tests/test_profile_api.py`, `tests/test_enrichment_service.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_profile_api.py`에 추가:

```python
def test_proposal_routes_registered():
    paths = {r.path for r in profile_router.router.routes}
    assert "/api/groups/{slug}/profile/proposal/apply" in paths
    assert "/api/groups/{slug}/profile/proposal/dismiss" in paths
```

`tests/test_enrichment_service.py`에 추가:

```python
def test_batch_and_apply_exported():
    from app.services.enrichment_service import (
        apply_proposal, dismiss_proposal, run_profile_enrichment_once,
    )
    assert callable(run_profile_enrichment_once)
    assert callable(apply_proposal) and callable(dismiss_proposal)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_profile_api.py tests/test_enrichment_service.py -k "proposal_routes or exported" -v`
Expected: FAIL — import/라우트 미존재.

- [ ] **Step 3: Implement async batch + apply/dismiss**

`app/services/enrichment_service.py` 끝에 추가:

```python
async def run_profile_enrichment_once() -> None:
    """전 활성 그룹 순차: 조건 충족 시 보강 제안 생성(자동 적용 없음).

    실패는 그룹 단위 격리. LLM은 조건 충족 그룹에만 1회(purpose='enrich').
    """
    from sqlalchemy import func, select

    from app.control_db import get_sessionmaker
    from app.models.control.group import Group
    from app.models.pg.entity import Entity
    from app.models.pg.video_analysis import VideoAnalysis
    from app.services.ai_usage_service import budget_ok_for_group, record_usage
    from app.services.db_engine import data_plane_engine_manager as dpm
    from app.services.global_settings import resolve_ai_gateway
    from app.services.group_profile import parse_profile
    from app.services.llm_client import LiteLLMClient
    from app.services.settings_manager import get_settings_manager

    mgr = get_settings_manager()
    async with get_sessionmaker()() as csess:
        groups = (await csess.execute(
            select(Group).where(Group.is_active.is_(True))
        )).scalars().all()

    now = datetime.now(timezone.utc)
    for group in groups:
        try:
            d = await mgr.get_typed(group.group_id, "profile")
            async with dpm.group_session(group) as session:
                count = (await session.execute(
                    select(func.count()).select_from(VideoAnalysis)
                )).scalar_one()
                if not should_enrich(
                    analysis_count=int(count),
                    last_at=str(d.get("enrich_last_at") or ""),
                    has_proposal=not proposal_is_empty(d.get("enrich_proposal")),
                    now=now,
                ):
                    continue
                samples = [r[0] for r in (await session.execute(
                    select(VideoAnalysis.one_line)
                    .order_by(VideoAnalysis.analyzed_at.desc()).limit(20)
                )).all() if r[0]]
                holds: list[str] = []
                for e in (await session.execute(select(Entity))).scalars().all():
                    cands = (e.attrs or {}).get("merge_candidates") or []
                    if cands:
                        holds.append(f"{e.canonical_name} ← {', '.join(cands)}")

            ok, _ = await budget_ok_for_group(group)
            if not ok:
                continue

            profile = parse_profile(d)
            pending = d.get("vocab_pending")
            prompt = build_enrich_prompt(
                persona=profile.persona, sections=profile.digest_sections,
                record_schema=profile.record_schema, vocab=profile.vocab,
                samples=samples,
                vocab_pending=[str(x) for x in pending] if isinstance(pending, list) else [],
                merge_holds=holds,
            )
            ai = await resolve_ai_gateway(group.group_id)
            model = ai.digest_model or ai.primary_model
            client = LiteLLMClient(ai)
            try:
                chat = await client.chat(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=min(ai.max_tokens or 2048, 2048),
                    response_format={"type": "json_object"},
                )
            finally:
                await client.aclose()
            await record_usage(
                user_id=group.owner_user_id, group_id=group.group_id,
                purpose="enrich", model=model,
                input_tokens=chat.input_tokens, output_tokens=chat.output_tokens,
            )
            new_proposal = normalize_enrich_proposal(chat.content)
            await mgr.set_values(group.group_id, "profile", [
                {"key": "enrich_proposal",
                 "value": json.dumps(new_proposal, ensure_ascii=False),
                 "value_type": "json"},
                {"key": "enrich_last_at", "value": now.isoformat(),
                 "value_type": "string"},
            ])
        except Exception as e:  # noqa: BLE001 — 그룹 단위 격리
            print(f"[enrich] {getattr(group, 'slug', '?')} 실패: {e}")


async def apply_proposal(group) -> dict | None:
    """저장된 제안을 프로필에 적용(+엔티티 attrs 반영). 제안 없으면 None."""
    from app.services.settings_manager import get_settings_manager

    mgr = get_settings_manager()
    d = await mgr.get_typed(group.group_id, "profile")
    proposal = d.get("enrich_proposal")
    if proposal_is_empty(proposal):
        return None
    items = apply_proposal_items(d, proposal)
    await mgr.set_values(group.group_id, "profile", items)
    attrs_add = proposal.get("entity_attrs_add") or []
    if attrs_add:
        await _apply_entity_attrs(group, attrs_add)
    return {"applied": True, "note": proposal.get("note", "")}


async def _apply_entity_attrs(group, items: list[dict]) -> None:
    from sqlalchemy import func, select, update

    from app.models.pg.entity import Entity
    from app.services.db_engine import data_plane_engine_manager as dpm

    async with dpm.group_session(group) as session:
        async with session.begin():
            for it in items:
                name = str(it.get("entity") or "").strip().lower()
                if not name:
                    continue
                crow = (await session.execute(
                    select(Entity).where(func.lower(Entity.canonical_name) == name)
                )).scalars().first()
                if crow is None:
                    continue
                merged = {**(crow.attrs or {}), **(it.get("attrs") or {})}
                await session.execute(
                    update(Entity).where(Entity.entity_pk == crow.entity_pk)
                    .values(attrs=merged))


async def dismiss_proposal(group) -> None:
    """제안 무시: 비우고 enrich_last_at 갱신(다음 달 재검토)."""
    from app.services.settings_manager import get_settings_manager

    await get_settings_manager().set_values(group.group_id, "profile", [
        {"key": "enrich_proposal", "value": "{}", "value_type": "json"},
        {"key": "enrich_last_at",
         "value": datetime.now(timezone.utc).isoformat(), "value_type": "string"},
    ])
```

- [ ] **Step 4: Register the daily job**

`app/services/scheduler.py` — import 추가:

```python
from app.services.enrichment_service import run_profile_enrichment_once
```

`entity_merge` job 등록 블록 바로 아래에 추가:

```python
    scheduler.add_job(
        run_profile_enrichment_once,   # Phase 3: 프로필 보강 제안 배치
        trigger="interval",
        minutes=1440,
        id="profile_enrich",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
```

- [ ] **Step 5: Add proposal routes**

`app/routers/profile.py` 끝에 추가:

```python
@router.post("/proposal/apply")
async def apply_enrich_proposal(group: Group = Depends(get_group_or_404)) -> dict:
    from fastapi import HTTPException

    from app.services.enrichment_service import apply_proposal
    result = await apply_proposal(group)
    if result is None:
        raise HTTPException(status_code=404, detail="적용할 제안이 없습니다")
    return await _profile_payload(group.group_id)


@router.post("/proposal/dismiss")
async def dismiss_enrich_proposal(group: Group = Depends(get_group_or_404)) -> dict:
    from app.services.enrichment_service import dismiss_proposal
    await dismiss_proposal(group)
    return await _profile_payload(group.group_id)
```

- [ ] **Step 6: Run tests + full suite**

Run: `python -m pytest tests/test_profile_api.py tests/test_enrichment_service.py -v && python -m pytest tests/ -q`
Expected: 신규 PASS + baseline 외 회귀 0.

- [ ] **Step 7: Commit**

```bash
git add app/services/enrichment_service.py app/services/scheduler.py app/routers/profile.py tests/test_profile_api.py tests/test_enrichment_service.py
git commit -m "feat: 보강 제안 일일 배치 + 적용/무시 라우트(Phase 3)"
```

---

### Task 10: 엔티티 병합 승인 큐 라우터

**Files:**
- Modify: `app/services/entity_service.py`
- Create: `app/routers/entities.py`
- Modify: `app/main.py`
- Test: `tests/test_entities_router.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_entities_router.py
"""병합 승인 큐 라우터 스모크(경로 등록 + 공개 wrapper)."""

from app.routers import entities as entities_router


def test_entities_routes_registered():
    paths = {r.path for r in entities_router.router.routes}
    assert "/api/groups/{slug}/entities/merge-candidates" in paths
    assert "/api/groups/{slug}/entities/{entity_pk}/merge" in paths
    assert "/api/groups/{slug}/entities/{entity_pk}/reject" in paths


def test_apply_merge_cluster_exported():
    from app.services.entity_service import apply_merge_cluster
    assert callable(apply_merge_cluster)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_entities_router.py -v`
Expected: FAIL — `ModuleNotFoundError: app.routers.entities`.

- [ ] **Step 3: Export public merge wrapper**

`app/services/entity_service.py`의 `_apply_merge` 정의 바로 아래에 추가:

```python
async def apply_merge_cluster(session, cluster: dict) -> list[str]:
    """수동 승인 경로 재사용 wrapper — 배치 병합과 동일 코드(설계 §3.4)."""
    return await _apply_merge(session, cluster)
```

- [ ] **Step 4: Implement the router**

```python
# app/routers/entities.py
"""엔티티 사전: 병합 보류 후보 조회·승인·거절 (Phase 3 승인 큐)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update

from app.models.control.group import Group
from app.models.pg.entity import Entity
from app.models.pg.job_log import JobLog
from app.routers.deps import get_group_or_404
from app.services.db_engine import data_plane_engine_manager as dpm
from app.services.entity_service import apply_merge_cluster

router = APIRouter(prefix="/api/groups/{slug}/entities", tags=["entities"])


@router.get("/merge-candidates")
async def list_merge_candidates(group: Group = Depends(get_group_or_404)) -> list[dict]:
    async with dpm.group_session(group) as session:
        rows = (await session.execute(select(Entity))).scalars().all()
        out = []
        for e in rows:
            cands = list((e.attrs or {}).get("merge_candidates") or [])
            if cands:
                out.append({
                    "entity_pk": e.entity_pk,
                    "canonical_name": e.canonical_name,
                    "candidates": cands,
                    "mention_count": e.mention_count,
                })
    return out


class MergeAction(BaseModel):
    alias: str


async def _pop_candidate(session, entity_pk: int, alias: str) -> Entity:
    """후보 목록에서 alias 제거 후 대상 엔티티 반환. 없으면 404."""
    crow = (await session.execute(
        select(Entity).where(Entity.entity_pk == entity_pk)
    )).scalars().first()
    if crow is None:
        raise HTTPException(status_code=404, detail="엔티티가 없습니다")
    attrs = dict(crow.attrs or {})
    cands = [c for c in (attrs.get("merge_candidates") or []) if c != alias]
    if cands:
        attrs["merge_candidates"] = cands
    else:
        attrs.pop("merge_candidates", None)
    await session.execute(
        update(Entity).where(Entity.entity_pk == entity_pk).values(attrs=attrs))
    return crow


@router.post("/{entity_pk}/merge")
async def approve_merge(
    entity_pk: int, body: MergeAction, group: Group = Depends(get_group_or_404)
) -> dict:
    """보류 후보 승인 — 배치 병합과 동일 코드(apply_merge_cluster) 사용."""
    alias = body.alias.strip()
    if not alias:
        raise HTTPException(status_code=400, detail="alias가 비어 있습니다")
    async with dpm.group_session(group) as session:
        async with session.begin():
            crow = await _pop_candidate(session, entity_pk, alias)
            merged = await apply_merge_cluster(
                session, {"canonical": crow.canonical_name, "aliases": [alias]})
            for a in merged:
                session.add(JobLog(
                    job_type="entity_merge", status="success",
                    message=f"{a} → {crow.canonical_name} (수동 승인)"[:500],
                ))
    return {"merged": merged}


@router.post("/{entity_pk}/reject")
async def reject_merge(
    entity_pk: int, body: MergeAction, group: Group = Depends(get_group_or_404)
) -> dict:
    alias = body.alias.strip()
    if not alias:
        raise HTTPException(status_code=400, detail="alias가 비어 있습니다")
    async with dpm.group_session(group) as session:
        async with session.begin():
            await _pop_candidate(session, entity_pk, alias)
    return {"rejected": alias}
```

- [ ] **Step 5: Register in main.py**

`app/main.py` — 라우터 import 목록(profile 옆)에 `entities` 추가하고, line 124 `profile.router` 아래에:

```python
app.include_router(entities.router, dependencies=_protected)
```

(주의: `app/main.py` 상단 import가 `from app.routers import ...` 나열식이면 거기에 `entities`를 추가. JobLog import 경로는 `app/models/pg/job_log.py`가 실재하는지 `grep -rn "class JobLog" app/models/`로 확인 — `entity_service.run_entity_merge_once`가 이미 같은 import를 쓰므로 그대로 따른다.)

- [ ] **Step 6: Run tests + full suite**

Run: `python -m pytest tests/test_entities_router.py -v && python -m pytest tests/ -q`
Expected: 신규 PASS + baseline 외 회귀 0.

- [ ] **Step 7: Commit**

```bash
git add app/services/entity_service.py app/routers/entities.py app/main.py tests/test_entities_router.py
git commit -m "feat: 엔티티 병합 승인 큐 API(Phase 3)"
```

---

### Task 11: 프론트 — 데이터 프로필 탭(편집 UI)

**Files:**
- Modify: `frontend/src/api/profile.ts`
- Create: `frontend/src/api/entities.ts`
- Create: `frontend/src/components/DataProfile.logic.ts`
- Create: `frontend/src/components/RecordSchemaBuilder.tsx`
- Create: `frontend/src/components/VocabEditor.tsx`
- Create: `frontend/src/components/DataProfilePanel.tsx`
- Modify: `frontend/src/settings/defs.ts`, `frontend/src/pages/Settings.tsx`
- Test: `frontend/src/components/DataProfile.logic.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/components/DataProfile.logic.test.ts
import { describe, expect, it } from 'vitest'
import {
  addAxis, addField, addSynonym, addType, parseValues,
  proposalSummary, removeField, removeType, setAxisValues,
} from './DataProfile.logic'
import type { RecordSchema } from '../api/profile'

const schema: RecordSchema = {
  version: 1,
  types: [{ type_key: 'campaign', label: '캠페인', fields: [
    { key: 'entity', label: '브랜드', datatype: 'entity', required: true }] }],
}

describe('record schema edit', () => {
  it('addField appends unique key only', () => {
    const f = { key: 'region', label: '지역', datatype: 'text' as const, required: false }
    const out = addField(schema, 'campaign', f)
    expect(out.types[0].fields.map((x) => x.key)).toEqual(['entity', 'region'])
    expect(addField(out, 'campaign', f).types[0].fields).toHaveLength(2)
  })

  it('addType/removeType', () => {
    const out = addType(schema, 'topic', '주제')
    expect(out.types).toHaveLength(2)
    expect(removeType(out, 'campaign').types.map((t) => t.type_key)).toEqual(['topic'])
  })

  it('removeField', () => {
    expect(removeField(schema, 'campaign', 'entity').types[0].fields).toHaveLength(0)
  })
})

describe('vocab edit', () => {
  it('parseValues dedupes and trims', () => {
    expect(parseValues(' 긍정, 부정 ,긍정,')).toEqual(['긍정', '부정'])
  })

  it('setAxisValues + addSynonym', () => {
    let v = setAxisValues({}, 'sentiment', '긍정,부정')
    v = addSynonym(v, 'sentiment', 'positive', '긍정')
    expect(v.sentiment.values).toEqual(['긍정', '부정'])
    expect(v.sentiment.synonyms.positive).toBe('긍정')
  })

  it('addAxis ignores duplicates', () => {
    const v = addAxis(addAxis({}, 'a', 'A'), 'a', 'B')
    expect(v.a.label).toBe('A')
  })
})

describe('proposalSummary', () => {
  it('lists diff lines', () => {
    const lines = proposalSummary({
      sections_add: [{ key: 'risks', kind: 'llm', title: '리스크' }],
      record_fields_add: [{ type_key: 'campaign',
        field: { key: 'r', label: '지역', datatype: 'text', required: false } }],
      vocab_add: { sentiment: { label: '평가', values: ['중립'], synonyms: {} } },
      entity_attrs_add: [{ entity: 'SoftBank', attrs: { region: '일본' } }],
      note: 'n',
    })
    expect(lines).toHaveLength(4)
    expect(lines[0]).toContain('리스크')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test -- --run src/components/DataProfile.logic.test.ts`
Expected: FAIL — 모듈 없음.

- [ ] **Step 3: Rewrite api/profile.ts (전체 타입)**

```ts
// frontend/src/api/profile.ts
import { groupClient } from './http'
import type { DigestSection } from './types'

export interface RecordField {
  key: string
  label: string
  datatype: 'entity' | 'text' | 'number' | 'date'
  required: boolean
}

export interface RecordType {
  type_key: string
  label: string
  fields: RecordField[]
}

export interface RecordSchema {
  version: number
  types: RecordType[]
}

export interface VocabAxis {
  label: string
  values: string[]
  synonyms: Record<string, string>
}

export interface EnrichProposal {
  sections_add?: DigestSection[]
  record_fields_add?: { type_key: string; field: RecordField }[]
  vocab_add?: Record<string, VocabAxis>
  entity_attrs_add?: { entity: string; attrs: Record<string, string> }[]
  note?: string
  created_at?: string
}

export interface GroupProfile {
  persona: string
  digest_sections: DigestSection[]
  bootstrap_status: string
  bootstrap_at?: string
  record_schema?: RecordSchema
  vocab?: Record<string, VocabAxis>
  vocab_pending?: string[]
  enrich_proposal?: EnrichProposal
}

export interface ProfileUpdate {
  persona?: string
  digest_sections?: DigestSection[]
  record_schema?: RecordSchema
  vocab?: Record<string, VocabAxis>
}

export function profileApi(slug: string) {
  const c = groupClient(slug)
  return {
    get: () => c.get<GroupProfile>('/profile'),
    regenerate: () => c.post<GroupProfile>('/profile/regenerate'),
    put: (body: ProfileUpdate) => c.put<GroupProfile>('/profile', body),
    applyProposal: () => c.post<GroupProfile>('/profile/proposal/apply'),
    dismissProposal: () => c.post<GroupProfile>('/profile/proposal/dismiss'),
  }
}
```

(주의: `groupClient`에 `put`이 없으면 `frontend/src/api/http.ts`의 기존 메서드(예: settingsApi가 쓰는 것)를 확인해 동일 방식으로 호출한다.)

- [ ] **Step 4: Create api/entities.ts**

```ts
// frontend/src/api/entities.ts
import { groupClient } from './http'

export interface MergeCandidate {
  entity_pk: number
  canonical_name: string
  candidates: string[]
  mention_count: number
}

export function entitiesApi(slug: string) {
  const c = groupClient(slug)
  return {
    mergeCandidates: () => c.get<MergeCandidate[]>('/entities/merge-candidates'),
    approve: (pk: number, alias: string) =>
      c.post<{ merged: string[] }>(`/entities/${pk}/merge`, { alias }),
    reject: (pk: number, alias: string) =>
      c.post<{ rejected: string }>(`/entities/${pk}/reject`, { alias }),
  }
}
```

- [ ] **Step 5: Create DataProfile.logic.ts**

```ts
// frontend/src/components/DataProfile.logic.ts
import type { EnrichProposal, RecordField, RecordSchema, VocabAxis } from '../api/profile'

export function addType(schema: RecordSchema, typeKey: string, label: string): RecordSchema {
  const key = typeKey.trim()
  if (!key || schema.types.some((t) => t.type_key === key)) return schema
  return { ...schema, types: [...schema.types, { type_key: key, label: label.trim() || key, fields: [] }] }
}

export function removeType(schema: RecordSchema, typeKey: string): RecordSchema {
  return { ...schema, types: schema.types.filter((t) => t.type_key !== typeKey) }
}

export function addField(schema: RecordSchema, typeKey: string, field: RecordField): RecordSchema {
  return {
    ...schema,
    types: schema.types.map((t) => {
      if (t.type_key !== typeKey) return t
      if (!field.key.trim() || t.fields.some((f) => f.key === field.key)) return t
      return { ...t, fields: [...t.fields, { ...field, key: field.key.trim() }] }
    }),
  }
}

export function removeField(schema: RecordSchema, typeKey: string, fieldKey: string): RecordSchema {
  return {
    ...schema,
    types: schema.types.map((t) =>
      t.type_key === typeKey ? { ...t, fields: t.fields.filter((f) => f.key !== fieldKey) } : t),
  }
}

export function parseValues(input: string): string[] {
  return [...new Set(input.split(',').map((s) => s.trim()).filter(Boolean))]
}

export function setAxisValues(
  vocab: Record<string, VocabAxis>, axis: string, input: string,
): Record<string, VocabAxis> {
  const cur = vocab[axis] ?? { label: axis, values: [], synonyms: {} }
  return { ...vocab, [axis]: { ...cur, values: parseValues(input) } }
}

export function addSynonym(
  vocab: Record<string, VocabAxis>, axis: string, from: string, to: string,
): Record<string, VocabAxis> {
  const f = from.trim()
  const t = to.trim()
  const cur = vocab[axis]
  if (!cur || !f || !t) return vocab
  return { ...vocab, [axis]: { ...cur, synonyms: { ...cur.synonyms, [f]: t } } }
}

export function removeSynonym(
  vocab: Record<string, VocabAxis>, axis: string, from: string,
): Record<string, VocabAxis> {
  const cur = vocab[axis]
  if (!cur) return vocab
  const next = { ...cur.synonyms }
  delete next[from]
  return { ...vocab, [axis]: { ...cur, synonyms: next } }
}

export function addAxis(
  vocab: Record<string, VocabAxis>, axis: string, label: string,
): Record<string, VocabAxis> {
  const key = axis.trim()
  if (!key || vocab[key]) return vocab
  return { ...vocab, [key]: { label: label.trim() || key, values: [], synonyms: {} } }
}

export function removeAxis(vocab: Record<string, VocabAxis>, axis: string): Record<string, VocabAxis> {
  const next = { ...vocab }
  delete next[axis]
  return next
}

export function proposalSummary(p: EnrichProposal | undefined): string[] {
  if (!p) return []
  const out: string[] = []
  p.sections_add?.forEach((s) => out.push(`섹션 추가: ${s.title}`))
  p.record_fields_add?.forEach((f) => out.push(`레코드 필드: ${f.type_key}.${f.field.label}`))
  Object.entries(p.vocab_add ?? {}).forEach(([axis, spec]) =>
    out.push(`어휘 확장: ${axis} (${spec.values.join(', ')})`))
  p.entity_attrs_add?.forEach((e) => out.push(`엔티티 속성: ${e.entity}`))
  return out
}
```

- [ ] **Step 6: Run logic test**

Run: `cd frontend && npm run test -- --run src/components/DataProfile.logic.test.ts`
Expected: PASS (7 tests).

- [ ] **Step 7: Create RecordSchemaBuilder.tsx**

```tsx
// frontend/src/components/RecordSchemaBuilder.tsx
import { useState } from 'react'
import type { RecordField, RecordSchema } from '../api/profile'
import { addField, addType, removeField, removeType } from './DataProfile.logic'

const DATATYPES: RecordField['datatype'][] = ['entity', 'text', 'number', 'date']
const EMPTY_FIELD: RecordField = { key: '', label: '', datatype: 'text', required: false }

interface Props {
  schema: RecordSchema
  onChange: (s: RecordSchema) => void
}

export default function RecordSchemaBuilder({ schema, onChange }: Props) {
  const [newType, setNewType] = useState({ key: '', label: '' })
  const [drafts, setDrafts] = useState<Record<string, RecordField>>({})
  const draftFor = (tk: string): RecordField => drafts[tk] ?? EMPTY_FIELD
  const setDraft = (tk: string, patch: Partial<RecordField>) =>
    setDrafts({ ...drafts, [tk]: { ...draftFor(tk), ...patch } })

  return (
    <div className="space-y-4">
      {schema.types.map((t) => (
        <div key={t.type_key} className="border border-gray-200 rounded-lg p-3 space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-gray-800">
              {t.label} <code className="text-xs text-gray-400">{t.type_key}</code>
            </span>
            <button type="button" onClick={() => onChange(removeType(schema, t.type_key))}
              className="text-xs text-red-400 hover:text-red-600">타입 삭제</button>
          </div>
          <ul className="space-y-1">
            {t.fields.map((f) => (
              <li key={f.key} className="flex items-center gap-2 text-sm text-gray-700">
                <span className="flex-1">{f.label} <code className="text-xs text-gray-400">{f.key}</code></span>
                <span className="text-xs text-gray-500">{f.datatype}{f.required ? ' · 필수' : ''}</span>
                <button type="button" onClick={() => onChange(removeField(schema, t.type_key, f.key))}
                  className="px-1 text-red-400 hover:text-red-600">×</button>
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <input placeholder="key(영문)" value={draftFor(t.type_key).key}
              onChange={(e) => setDraft(t.type_key, { key: e.target.value })}
              className="border border-gray-200 rounded px-2 py-1 w-24" />
            <input placeholder="라벨" value={draftFor(t.type_key).label}
              onChange={(e) => setDraft(t.type_key, { label: e.target.value })}
              className="border border-gray-200 rounded px-2 py-1 w-24" />
            <select value={draftFor(t.type_key).datatype}
              onChange={(e) => setDraft(t.type_key, { datatype: e.target.value as RecordField['datatype'] })}
              className="border border-gray-200 rounded px-2 py-1">
              {DATATYPES.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
            <label className="flex items-center gap-1 text-gray-600">
              <input type="checkbox" checked={draftFor(t.type_key).required}
                onChange={(e) => setDraft(t.type_key, { required: e.target.checked })} />
              필수
            </label>
            <button type="button"
              onClick={() => {
                const d = draftFor(t.type_key)
                const next = addField(schema, t.type_key, { ...d, label: d.label || d.key })
                if (next !== schema) {
                  onChange(next)
                  setDrafts({ ...drafts, [t.type_key]: EMPTY_FIELD })
                }
              }}
              className="px-2 py-1 border border-gray-300 rounded hover:bg-gray-50">필드 추가</button>
          </div>
        </div>
      ))}
      <div className="flex items-center gap-2 text-xs">
        <input placeholder="type_key(영문)" value={newType.key}
          onChange={(e) => setNewType({ ...newType, key: e.target.value })}
          className="border border-gray-200 rounded px-2 py-1 w-28" />
        <input placeholder="라벨" value={newType.label}
          onChange={(e) => setNewType({ ...newType, label: e.target.value })}
          className="border border-gray-200 rounded px-2 py-1 w-28" />
        <button type="button"
          onClick={() => {
            const next = addType(schema, newType.key, newType.label)
            if (next !== schema) { onChange(next); setNewType({ key: '', label: '' }) }
          }}
          className="px-2 py-1 border border-gray-300 rounded hover:bg-gray-50">레코드 타입 추가</button>
      </div>
    </div>
  )
}
```

- [ ] **Step 8: Create VocabEditor.tsx**

```tsx
// frontend/src/components/VocabEditor.tsx
import { useState } from 'react'
import type { VocabAxis } from '../api/profile'
import { addAxis, addSynonym, removeAxis, removeSynonym, setAxisValues } from './DataProfile.logic'

interface Props {
  vocab: Record<string, VocabAxis>
  onChange: (v: Record<string, VocabAxis>) => void
}

export default function VocabEditor({ vocab, onChange }: Props) {
  const [newAxis, setNewAxis] = useState({ key: '', label: '' })
  const [synDrafts, setSynDrafts] = useState<Record<string, { from: string; to: string }>>({})

  return (
    <div className="space-y-4">
      {Object.entries(vocab).map(([axis, spec]) => {
        const draft = synDrafts[axis] ?? { from: '', to: '' }
        return (
          <div key={axis} className="border border-gray-200 rounded-lg p-3 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-gray-800">
                {spec.label} <code className="text-xs text-gray-400">{axis}</code>
              </span>
              <button type="button" onClick={() => onChange(removeAxis(vocab, axis))}
                className="text-xs text-red-400 hover:text-red-600">축 삭제</button>
            </div>
            <input
              className="w-full border border-gray-200 rounded px-2 py-1 text-sm"
              placeholder="표준 값 (쉼표 구분)"
              value={spec.values.join(', ')}
              onChange={(e) => onChange(setAxisValues(vocab, axis, e.target.value))}
            />
            <ul className="space-y-1 text-xs text-gray-600">
              {Object.entries(spec.synonyms).map(([from, to]) => (
                <li key={from} className="flex items-center gap-2">
                  <span className="flex-1">{from} → {to}</span>
                  <button type="button" onClick={() => onChange(removeSynonym(vocab, axis, from))}
                    className="px-1 text-red-400 hover:text-red-600">×</button>
                </li>
              ))}
            </ul>
            <div className="flex items-center gap-2 text-xs">
              <input placeholder="동의어" value={draft.from}
                onChange={(e) => setSynDrafts({ ...synDrafts, [axis]: { ...draft, from: e.target.value } })}
                className="border border-gray-200 rounded px-2 py-1 w-24" />
              <span>→</span>
              <input placeholder="표준 값" value={draft.to}
                onChange={(e) => setSynDrafts({ ...synDrafts, [axis]: { ...draft, to: e.target.value } })}
                className="border border-gray-200 rounded px-2 py-1 w-24" />
              <button type="button"
                onClick={() => {
                  onChange(addSynonym(vocab, axis, draft.from, draft.to))
                  setSynDrafts({ ...synDrafts, [axis]: { from: '', to: '' } })
                }}
                className="px-2 py-1 border border-gray-300 rounded hover:bg-gray-50">추가</button>
            </div>
          </div>
        )
      })}
      <div className="flex items-center gap-2 text-xs">
        <input placeholder="축 key(영문)" value={newAxis.key}
          onChange={(e) => setNewAxis({ ...newAxis, key: e.target.value })}
          className="border border-gray-200 rounded px-2 py-1 w-28" />
        <input placeholder="라벨" value={newAxis.label}
          onChange={(e) => setNewAxis({ ...newAxis, label: e.target.value })}
          className="border border-gray-200 rounded px-2 py-1 w-28" />
        <button type="button"
          onClick={() => {
            const next = addAxis(vocab, newAxis.key, newAxis.label)
            if (next !== vocab) { onChange(next); setNewAxis({ key: '', label: '' }) }
          }}
          className="px-2 py-1 border border-gray-300 rounded hover:bg-gray-50">어휘 축 추가</button>
      </div>
    </div>
  )
}
```

- [ ] **Step 9: Create DataProfilePanel.tsx (제안 카드·병합 큐는 Task 12에서 추가)**

```tsx
// frontend/src/components/DataProfilePanel.tsx
import { useCallback, useEffect, useState } from 'react'
import { profileApi, type GroupProfile, type RecordSchema, type VocabAxis } from '../api/profile'
import type { DigestSection } from '../api/types'
import DigestSectionBuilder from './DigestSectionBuilder'
import RecordSchemaBuilder from './RecordSchemaBuilder'
import VocabEditor from './VocabEditor'
import Spinner from './Spinner'
import ErrorBanner from './ErrorBanner'

interface Props {
  slug: string
}

export default function DataProfilePanel({ slug }: Props) {
  const [profile, setProfile] = useState<GroupProfile | null>(null)
  const [persona, setPersona] = useState('')
  const [sections, setSections] = useState<DigestSection[]>([])
  const [schema, setSchema] = useState<RecordSchema>({ version: 1, types: [] })
  const [vocab, setVocab] = useState<Record<string, VocabAxis>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [savedAt, setSavedAt] = useState<string | null>(null)

  const applyLoaded = (p: GroupProfile) => {
    setProfile(p)
    setPersona(p.persona ?? '')
    setSections(p.digest_sections ?? [])
    setSchema(p.record_schema ?? { version: 1, types: [] })
    setVocab(p.vocab ?? {})
  }

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      applyLoaded(await profileApi(slug).get())
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [slug])

  useEffect(() => { load() }, [load])

  const save = async () => {
    setSaving(true)
    try {
      applyLoaded(await profileApi(slug).put({
        persona, digest_sections: sections, record_schema: schema, vocab,
      }))
      setSavedAt(new Date().toLocaleTimeString('ko-KR'))
    } catch (e) {
      alert((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <Spinner />
  if (error) return <ErrorBanner message={error} onRetry={load} />
  if (!profile) return null

  const recordTypes = schema.types.map((t) => t.type_key)

  return (
    <div className="space-y-5 max-w-3xl">
      <div className="bg-white rounded-xl shadow-sm p-5 space-y-2">
        <h2 className="font-semibold text-gray-800">페르소나</h2>
        <textarea
          className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm"
          rows={2}
          value={persona}
          onChange={(e) => setPersona(e.target.value)}
          placeholder="이 그룹 리포트를 쓰는 애널리스트를 한 문장으로"
        />
      </div>

      <div className="bg-white rounded-xl shadow-sm p-5 space-y-3">
        <h2 className="font-semibold text-gray-800">리포트 섹션</h2>
        <DigestSectionBuilder sections={sections} onChange={setSections} recordTypes={recordTypes} />
      </div>

      <div className="bg-white rounded-xl shadow-sm p-5 space-y-3">
        <h2 className="font-semibold text-gray-800">
          레코드 스키마 <span className="text-xs text-gray-400">v{schema.version}</span>
        </h2>
        <RecordSchemaBuilder schema={schema} onChange={setSchema} />
      </div>

      <div className="bg-white rounded-xl shadow-sm p-5 space-y-3">
        <h2 className="font-semibold text-gray-800">통제 어휘</h2>
        <VocabEditor vocab={vocab} onChange={setVocab} />
        {(profile.vocab_pending?.length ?? 0) > 0 && (
          <p className="text-xs text-amber-600">
            미매핑 값: {profile.vocab_pending!.join(', ')}
          </p>
        )}
      </div>

      <div className="flex items-center gap-3">
        <button type="button" onClick={save} disabled={saving}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50">
          {saving ? '저장 중...' : '저장'}
        </button>
        {savedAt && <span className="text-xs text-green-600">저장됨 {savedAt}</span>}
      </div>
    </div>
  )
}
```

- [ ] **Step 10: Wire the settings tab**

`frontend/src/settings/defs.ts` — `SETTING_CATEGORIES`에 추가(마지막):

```ts
  { key: 'data_profile', label: '데이터 프로필' },
```

(`ADMIN_ONLY_CATEGORIES`에 넣지 않는다 — 모든 사용자 노출.)

`frontend/src/pages/Settings.tsx`:
- import: `import DataProfilePanel from '../components/DataProfilePanel'`
- `const isDataProfile = category === 'data_profile'` (`isDigest` 아래)
- `load()` 첫 guard와 redirect 조건을 `(!defs && !isDigest && !isDataProfile)`로 확장.
- `load()` 시작부에 `if (isDataProfile) { setLoading(false); return }` 추가(패널이 자체 로드).
- 렌더 분기: `{isDataProfile ? <DataProfilePanel slug={activeSlug} /> : isDigest ? ... }`

- [ ] **Step 11: Run tests + typecheck**

Run: `cd frontend && npm run test -- --run && npx tsc --noEmit`
Expected: 전부 PASS, 타입 에러 0.

- [ ] **Step 12: Commit**

```bash
git add frontend/src/api/profile.ts frontend/src/api/entities.ts frontend/src/components/DataProfile.logic.ts frontend/src/components/DataProfile.logic.test.ts frontend/src/components/RecordSchemaBuilder.tsx frontend/src/components/VocabEditor.tsx frontend/src/components/DataProfilePanel.tsx frontend/src/settings/defs.ts frontend/src/pages/Settings.tsx
git commit -m "feat: 데이터 프로필 탭 — 스키마·어휘·페르소나 편집(Phase 3)"
```

---

### Task 12: 프론트 — 보강 제안 카드 + 병합 승인 큐

**Files:**
- Create: `frontend/src/components/EnrichProposalCard.tsx`
- Create: `frontend/src/components/MergeQueue.tsx`
- Modify: `frontend/src/components/DataProfilePanel.tsx`

- [ ] **Step 1: Create EnrichProposalCard.tsx**

```tsx
// frontend/src/components/EnrichProposalCard.tsx
import { useState } from 'react'
import { profileApi, type EnrichProposal, type GroupProfile } from '../api/profile'
import { proposalSummary } from './DataProfile.logic'

interface Props {
  slug: string
  proposal: EnrichProposal
  onApplied: (p: GroupProfile) => void
}

export default function EnrichProposalCard({ slug, proposal, onApplied }: Props) {
  const [busy, setBusy] = useState(false)
  const lines = proposalSummary(proposal)
  if (!lines.length) return null

  const act = async (fn: () => Promise<GroupProfile>) => {
    setBusy(true)
    try {
      onApplied(await fn())
    } catch (e) {
      alert((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="bg-blue-50 border border-blue-200 rounded-xl p-5 space-y-2">
      <h2 className="font-semibold text-blue-900">프로필 보강 제안</h2>
      {proposal.note && <p className="text-sm text-blue-800">{proposal.note}</p>}
      <ul className="text-sm text-blue-800 list-disc pl-5">
        {lines.map((l) => <li key={l}>{l}</li>)}
      </ul>
      <div className="flex gap-2">
        <button type="button" disabled={busy}
          onClick={() => act(() => profileApi(slug).applyProposal())}
          className="px-3 py-1.5 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50">
          적용
        </button>
        <button type="button" disabled={busy}
          onClick={() => act(() => profileApi(slug).dismissProposal())}
          className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm hover:bg-gray-50 disabled:opacity-50">
          무시
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Create MergeQueue.tsx**

```tsx
// frontend/src/components/MergeQueue.tsx
import { useCallback, useEffect, useState } from 'react'
import { entitiesApi, type MergeCandidate } from '../api/entities'

interface Props {
  slug: string
}

export default function MergeQueue({ slug }: Props) {
  const [rows, setRows] = useState<MergeCandidate[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setRows(await entitiesApi(slug).mergeCandidates())
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [slug])

  useEffect(() => { load() }, [load])

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await fn()
      await load()
    } catch (e) {
      alert((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (error) return null  // 병합 큐는 부가 기능 — 조회 실패 시 조용히 숨김
  if (!rows.length) return null

  return (
    <div className="bg-white rounded-xl shadow-sm p-5 space-y-3">
      <h2 className="font-semibold text-gray-800">엔티티 병합 승인 대기</h2>
      <ul className="space-y-2">
        {rows.map((r) =>
          r.candidates.map((alias) => (
            <li key={`${r.entity_pk}:${alias}`}
              className="flex items-center gap-2 text-sm text-gray-700">
              <span className="flex-1">{alias} → <b>{r.canonical_name}</b></span>
              <button type="button" disabled={busy}
                onClick={() => act(() => entitiesApi(slug).approve(r.entity_pk, alias))}
                className="px-2 py-1 text-xs bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50">
                승인
              </button>
              <button type="button" disabled={busy}
                onClick={() => act(() => entitiesApi(slug).reject(r.entity_pk, alias))}
                className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50">
                거절
              </button>
            </li>
          )))}
      </ul>
    </div>
  )
}
```

- [ ] **Step 3: Wire into DataProfilePanel.tsx**

import 추가:

```tsx
import EnrichProposalCard from './EnrichProposalCard'
import MergeQueue from './MergeQueue'
```

return의 최상단 카드(페르소나 위)에 제안 카드, 맨 아래에 병합 큐:

```tsx
      {profile.enrich_proposal && Object.keys(profile.enrich_proposal).length > 0 && (
        <EnrichProposalCard slug={slug} proposal={profile.enrich_proposal} onApplied={applyLoaded} />
      )}
```

(저장 버튼 div 아래):

```tsx
      <MergeQueue slug={slug} />
```

- [ ] **Step 4: Run full frontend suite + typecheck + build**

Run: `cd frontend && npm run test -- --run && npx tsc --noEmit && npm run build`
Expected: 전부 PASS, 빌드 성공.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/EnrichProposalCard.tsx frontend/src/components/MergeQueue.tsx frontend/src/components/DataProfilePanel.tsx
git commit -m "feat: 보강 제안 카드 + 엔티티 병합 승인 큐 UI(Phase 3)"
```

---

### Task 13: 전체 검증 + E2E 체크리스트

**Files:** 없음(검증 전용)

- [ ] **Step 1: 백엔드 전체 스위트**

Run: `python -m pytest tests/ -q`
Expected: 신규 테스트 전부 PASS. 실패는 기존 baseline(`test_instant_analyze_daily_quota_400`)만. Phase 2 완료 시점(452 passed)과 대조해 **신규 회귀 0**.

- [ ] **Step 2: 프론트 전체**

Run: `cd frontend && npm run test -- --run && npx tsc --noEmit && npm run build`
Expected: vitest 전부 PASS(기존 45+ + 신규), 타입 에러 0, 빌드 성공.

- [ ] **Step 3: 무스키마 그룹 무변경 회귀 확인**

- `test_build_records_data_no_schema_returns_empty` — record_schema 없으면 피벗 집계 자체가 빈 dict.
- digest_service 배선은 `profile.record_schema["types"]` 빈 배열이면 `build_records_data`를 호출하지 않음 → 프로덕션 4그룹(custom digest_prompt, record_schema 미보유) **digest 경로 완전 무변경**.
- 보강 배치는 `analysis_count < 10`이거나 LLM 호출 전 조건에서 skip — 원장 purpose='enrich' 행 없음.

- [ ] **Step 4: 커밋 로그 정리 확인**

Run: `git log --oneline -14 && git status`
Expected: Task 1~12 커밋 순서대로, working tree clean.

- [ ] **Step 5: 실 DB E2E 체크리스트(사용자/다음 세션 — 테스트 DB 도달 필요)**

테스트 DB(`100.115.13.102`, 그룹 `e2e_a`/`e2e_b`)에서:
1. record_schema 보유 그룹에서 기간 내 분석 수건 → digest 생성 시 hybrid 섹션 데이터·서술 생성, DigestDetail에서 피벗 카드 렌더.
2. custom digest_prompt에 `{records_block}` 삽입 → 프롬프트에 피벗 JSON 포함(원장 purpose='digest' 정상).
3. 데이터 프로필 탭: 페르소나·섹션·record_schema(필드 추가 시 version 증가)·vocab 편집 저장 → GET 재조회 일치.
4. 보강 배치 수동 실행(`run_profile_enrichment_once`) → 조건 충족 그룹에 enrich_proposal 생성(원장 purpose='enrich'), 카드 [적용] 시 프로필 병합·vocab_pending 소거, [무시] 시 비움+enrich_last_at 갱신.
5. 병합 승인 큐: 보류 후보 [승인] → analysis_records.entity_name UPDATE + job_log(수동 승인), [거절] → 후보만 제거.
6. **회귀**: record_schema 없는 그룹은 digest 전후 summary_md/sections 동일, records_data 빈 상태.

---

## Self-Review 결과

**1. 스펙 커버리지(§3.1~3.4):**
- §3.1 피벗 3종(entity_pivot/period_compare/top_records, 전수 집계, hybrid kind, {records_block}) → Task 1~4(백), 6~7(프론트). ✅
- §3.2 보강 루프(10건 도달·월 1회, vocab_pending+병합 보류 입력, 자동 미적용 카드, 적용 시 version 증가) → Task 8~9(백), 12(카드). ✅
- §3.3 승인·편집 UI(병합 큐 원클릭, record_schema 필드 빌더·OrderedItemsBuilder 재사용(DigestSectionBuilder 경유)·datatype 4종, 섹션 guide·persona·vocab 편집, 별도 '데이터 프로필' 탭) → Task 5(PUT), 10(API), 11~12(UI). ✅
- §3.4 테스트(피벗 SQL 집계·hybrid 조립/렌더, period_compare 이전 기간 부재, 제안 diff 생성·적용·버전 증가·무시 무변경, 승인 큐=배치 동일 코드) → 각 태스크 Step 1 테스트 + `apply_merge_cluster` wrapper. ✅

**2. Placeholder 스캔:** Task 7 Step 5·Task 10 Step 5·Task 11 Step 3의 "기존 코드 확인(grep)" 지시는 리포지토리 실측 재사용 지시(placeholder 아님). 그 외 모든 코드 스텝은 실제 코드 포함.

**3. 타입 일관성:** `records_data: dict[key→data]`가 build_records_data→DigestAggregate→build_computed_data→프론트 data로 일관. hybrid 섹션 dict(key/kind/title/guide/params)와 normalize·builder·산출 섹션 일치. 제안 dict 5개 key가 normalize→proposal_is_empty→apply_proposal_items→proposalSummary에서 동일. `apply_merge_cluster(session, {"canonical", "aliases"}) -> list[str]`이 배치·라우터 동일. `bump_schema_version_if_changed(old, new) -> {"version", "types"}`가 PUT·apply 양쪽 사용. ✅

**리스크 메모:**
- `str.format`에 미지원 placeholder가 있는 custom 프롬프트는 기존 fallback 경로 유지(records_block kwarg 추가는 기존 프롬프트에 무해).
- 피벗 집계는 LLM 0회(순수 SQL) — 비용 증가는 보강 배치(purpose='enrich', 월 1회/그룹)뿐.
- `enrich_proposal`/`vocab_pending`/`enrich_last_at`은 profile 카테고리 키 추가만 — 스키마 변경 없음.
