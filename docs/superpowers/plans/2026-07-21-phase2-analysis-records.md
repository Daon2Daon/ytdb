# Phase 2 — analysis_records + 통제 어휘 + 엔티티 사전(자동) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 분석 산출물에서 "한 행 = 한 사실"의 구조화 레코드를 2차 경량 LLM 패스로 추출·저장하고, 그룹별 통제 어휘로 표기를 통일하며, 엔티티 별칭을 자동 축적·병합한다. Phase 3 피벗 섹션의 데이터 기반을 만든다.

**Architecture:** 분석 본 호출(공유 캐시 키 참여)은 절대 건드리지 않는다. 대신 `save_analysis_to_group` 완료 후(신규 분석 + 캐시 적중 양쪽) best-effort 후처리로 `records_extractor`를 실행한다. 테이블 구조는 전 그룹 동일(`analysis_records`, `entities`) — 차이는 데이터(그룹 프로필의 `record_schema`·`vocab`)로만 표현한다. record_schema 없는 그룹은 후처리 전체 skip(무비용·완전 무변경).

**Tech Stack:** Python 3, SQLAlchemy(async, `SCHEMA_TOKEN` 스키마 토큰 패턴), FastAPI, LiteLLMClient(경량 `tagging_model`), APScheduler(일일 병합 배치), pytest.

---

## 배경 근거 (설계 문서 §2, `docs/superpowers/specs/2026-07-21-digest-sections-group-profile-records-design.md`)

- Phase 1 완료·배포됨(origin/main `2966ebf`). GroupProfile에 `persona`/`digest_sections`/`bootstrap_status`/`bootstrap_at` 존재.
- 공유 분석 캐시(`app.analysis_cache`)는 프리셋 키가 참여하므로 **분석 본 프롬프트를 그룹별로 바꾸면 캐시가 깨진다** → records는 반드시 2차 패스.
- 자동화는 전부 실패 시 현행으로 폴백. record_schema 없으면 완전 no-op(회귀 0).

## 핵심 통합 지점 (조사 완료)

- 모델 패턴: `app/models/pg/video_analysis.py` — `__table_args__ = {"schema": SCHEMA_TOKEN}`, `PgBase` 상속. 등록은 `app/models/pg/__init__.py` import.
- 스키마 생성: `app/services/db_engine.py:172` `_create_missing` — `PgBase.metadata.sorted_tables` 중 `existing`에 없는 테이블을 `table.create`. **신규 테이블은 모델만 등록하면 자동 생성**(신규·기존 스키마 모두). additive_columns는 기존 테이블 컬럼 추가용이라 신규 테이블엔 불필요.
- 프로필: `app/services/group_profile.py` `GroupProfile`/`parse_profile`, `app/services/settings_manager.py:213` `get_profile`, `set_values(group_id, "profile", items)`.
- 부트스트랩: `app/services/bootstrap_service.py` `_BOOTSTRAP_PROMPT` / `normalize_bootstrap_output` / `bootstrap_profile`.
- 분석 저장(공용): `app/services/analyzer.py:200` `save_analysis_to_group(session, video_pk, result)`. 저장 데이터: `one_line`, `analysis_sections`, `entities`, `insights`, `key_points`, `sentiment`.
- 두 실행 경로: (신규) `app/services/monitor_service.py` → `pipeline.run_and_save` → `save_to_db`; (캐시 적중) `app/services/monitor_service.py:769-788` `result_from_cache` → `save_analysis_to_group`. **양쪽 다 `group`·`make_session`·`video_pk` 접근 가능.**
- AI 설정: `AIGatewaySettings.tagging_model`(경량, `app/services/settings_types.py:74`), `resolve_ai_gateway(group_id)`(`app/services/global_settings.py`).
- 원장: `record_usage(user_id, group_id, purpose, model, input_tokens, output_tokens)`, `budget_ok_for_group(group)` (`app/services/ai_usage_service.py`).
- LLM: `LiteLLMClient(ai).chat(model, messages, temperature, max_tokens, response_format={"type":"json_object"})` → `ChatResult(content, input_tokens, output_tokens)`; `.aclose()`.
- 스케줄러: `app/services/scheduler.py:120-148` `add_job(..., trigger="interval", ...)`. 일일 배치는 `minutes=1440` 패턴(참고: `run_stats_refresh_once`).
- 관리자 라우터: `app/routers/admin.py` `prefix="/api/admin"`, `dependencies=[Depends(require_admin)]`. 백필 트리거 참고: `@router.post("/migrate-schemas")`, `@router.post("/usage/backfill-costs")`.

## File Structure

**신규 파일**
- `app/models/pg/analysis_record.py` — `AnalysisRecord` 모델(한 행 = 한 사실).
- `app/models/pg/entity.py` — `Entity` 모델(자동 축적 사전).
- `app/services/records_schema.py` — 순수: `record_schema`/`vocab` dataclass·정규화, 필드→승격 컬럼 매핑, vocab 매핑 함수.
- `app/services/records_extractor.py` — 2차 경량 LLM 패스(프롬프트 조립·관대 파싱·저장 오케스트레이션).
- `app/services/entity_service.py` — 데이터 평면 엔티티 upsert/alias 조회/병합 배치.
- `tests/test_records_schema.py`, `tests/test_records_extractor.py`, `tests/test_entity_service.py`, `tests/test_analysis_record_model.py`, `tests/test_bootstrap_records.py`.

**수정 파일**
- `app/models/pg/__init__.py` — 신규 모델 2종 등록.
- `app/services/group_profile.py` — `GroupProfile`에 `record_schema`·`vocab` 추가, `parse_profile` 확장.
- `app/services/bootstrap_service.py` — 부트스트랩 v2: 프롬프트·`normalize_bootstrap_output`·저장에 `record_schema`·`vocab` 추가.
- `app/services/monitor_service.py` — 두 분석 경로에 post-pass 훅(best-effort).
- `app/services/scheduler.py` — 엔티티 병합 일일 배치 등록.
- `app/routers/admin.py` — 백필 트리거 엔드포인트.

## 승격 규칙 (Task 3에서 구현, 여러 태스크가 참조하므로 여기 고정)

record_schema의 한 type 정의와 LLM이 뱉은 `fields` dict를 받아 `AnalysisRecord` 컬럼으로 매핑한다:

- `entity_name` ← 그 type의 **첫 `datatype=="entity"` 필드** 값(정규화 전 원문; 정규화는 entity_service가 별도).
- `value_num` ← 첫 `datatype=="number"` 필드 값(숫자 파싱 실패 시 None, 원문은 attrs로).
- `event_date` ← 첫 `datatype=="date"` 필드 값(`YYYY-MM-DD` 파싱, 실패 시 None, 원문 attrs로).
- `value_text` ← 첫 `datatype=="text"` 필드 값.
- `attrs` ← 위 4개로 승격되고 **남은 모든 필드**(스키마의 두 번째 이후 동일 datatype 필드, 스키마에 없는 여분 필드 포함)를 `{field_key: value}`로.
- 필수(`required=true`) 필드가 비면 그 record는 **drop**.

---

### Task 1: 데이터 평면 모델 — analysis_records + entities

**Files:**
- Create: `app/models/pg/analysis_record.py`
- Create: `app/models/pg/entity.py`
- Modify: `app/models/pg/__init__.py`
- Test: `tests/test_analysis_record_model.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_analysis_record_model.py
from app.models.pg import AnalysisRecord, Entity, PgBase


def test_models_registered_in_metadata():
    names = set(PgBase.metadata.tables.keys())
    # 스키마 토큰이 접두어로 붙는다(SCHEMA_TOKEN).
    assert any(n.endswith(".analysis_records") for n in names)
    assert any(n.endswith(".entities") for n in names)


def test_analysis_record_columns():
    cols = {c.name for c in AnalysisRecord.__table__.columns}
    assert {
        "record_pk", "video_pk", "record_type", "schema_version",
        "position", "entity_name", "value_text", "value_num",
        "event_date", "attrs", "created_at",
    } <= cols


def test_analysis_record_unique_constraint():
    # 재분석 delete-insert 멱등을 위한 (video_pk, record_type, position) UNIQUE.
    uniques = [
        set(c.name for c in con.columns)
        for con in AnalysisRecord.__table__.constraints
        if con.__class__.__name__ == "UniqueConstraint"
    ]
    assert {"video_pk", "record_type", "position"} in uniques


def test_entity_columns_and_unique():
    cols = {c.name for c in Entity.__table__.columns}
    assert {
        "entity_pk", "canonical_name", "aliases", "attrs",
        "status", "mention_count", "first_seen", "last_seen",
    } <= cols
    uniques = [
        set(c.name for c in con.columns)
        for con in Entity.__table__.constraints
        if con.__class__.__name__ == "UniqueConstraint"
    ]
    assert {"canonical_name"} in uniques
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_analysis_record_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'AnalysisRecord'`.

- [ ] **Step 3: Write the models**

```python
# app/models/pg/analysis_record.py
"""데이터 평면: analysis_records (한 행 = 한 사실). record_schema로 형태 정의."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger, Date, DateTime, ForeignKey, Index, Integer,
    Numeric, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.pg.base import SCHEMA_TOKEN, PgBase


class AnalysisRecord(PgBase):
    __tablename__ = "analysis_records"
    __table_args__ = (
        UniqueConstraint(
            "video_pk", "record_type", "position",
            name="ux_analysis_records_video_type_pos",
        ),
        Index("ix_analysis_records_type_entity", "record_type", "entity_name"),
        Index("ix_analysis_records_video", "video_pk"),
        {"schema": SCHEMA_TOKEN},
    )

    record_pk: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    video_pk: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{SCHEMA_TOKEN}.videos.video_pk", ondelete="CASCADE"),
        nullable=False,
    )
    record_type: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    entity_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_num: Mapped[Any | None] = mapped_column(Numeric, nullable=True)
    event_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    attrs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

```python
# app/models/pg/entity.py
"""데이터 평면: entities (자동 축적 엔티티 사전). 사용자 등록 대기 없음."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.pg.base import SCHEMA_TOKEN, PgBase


class Entity(PgBase):
    __tablename__ = "entities"
    __table_args__ = (
        UniqueConstraint("canonical_name", name="ux_entities_canonical"),
        {"schema": SCHEMA_TOKEN},
    )

    entity_pk: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    attrs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="auto", server_default="auto")
    mention_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

Modify `app/models/pg/__init__.py` — add imports and `__all__` entries:

```python
from app.models.pg.analysis_record import AnalysisRecord
from app.models.pg.entity import Entity
```

그리고 `__all__` 리스트에 `"AnalysisRecord",`, `"Entity",` 추가.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_analysis_record_model.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Confirm no regression in schema-registration tests**

Run: `python -m pytest tests/ -k "route_registered or schema or model" -q`
Expected: 기존 통과 항목 유지(핀 없는 starlette 신버전의 `*_route_registered` 사전 실패는 무관 — 베이스라인과 동일). 신규 모델 import 에러 없음 확인.

- [ ] **Step 6: Commit**

```bash
git add app/models/pg/analysis_record.py app/models/pg/entity.py app/models/pg/__init__.py tests/test_analysis_record_model.py
git commit -m "feat: analysis_records·entities 데이터 평면 모델(Phase 2)"
```

---

### Task 2: GroupProfile에 record_schema·vocab 추가 + record_schema/vocab 정규화(순수)

**Files:**
- Create: `app/services/records_schema.py`
- Modify: `app/services/group_profile.py`
- Test: `tests/test_records_schema.py` (일부), `tests/test_group_profile.py` (기존에 추가)

- [ ] **Step 1: Write the failing test (정규화)**

```python
# tests/test_records_schema.py
from app.services.records_schema import normalize_record_schema, normalize_vocab


def test_normalize_record_schema_basic():
    raw = {
        "version": 1,
        "types": [
            {"type_key": "campaign", "label": "캠페인", "fields": [
                {"key": "entity", "label": "브랜드", "datatype": "entity", "required": True},
                {"key": "budget", "label": "규모", "datatype": "number"},
                {"key": "junk", "label": "?", "datatype": "weird"},  # 잘못된 datatype → text로 강등
            ]},
        ],
    }
    rs = normalize_record_schema(raw)
    assert rs["version"] == 1
    t = rs["types"][0]
    assert t["type_key"] == "campaign"
    dts = [f["datatype"] for f in t["fields"]]
    assert dts == ["entity", "number", "text"]  # weird → text
    assert t["fields"][0]["required"] is True


def test_normalize_record_schema_drops_typeless_and_fieldless():
    raw = {"types": [
        {"label": "no key"},                        # type_key 없음 → drop
        {"type_key": "empty", "fields": []},         # 필드 0 → drop
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_records_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.records_schema`.

- [ ] **Step 3: Write records_schema.py (정규화 부분)**

```python
# app/services/records_schema.py
"""record_schema·vocab 정규화와 필드→컬럼 승격 (순수 함수)."""

from __future__ import annotations

from datetime import date
from typing import Any

_DATATYPES = ("entity", "text", "number", "date")


def normalize_record_schema(raw: Any) -> dict:
    """LLM/사용자 입력 record_schema를 관대하게 정규화. 항상 유효 구조 반환."""
    if not isinstance(raw, dict):
        return {"version": 1, "types": []}
    version = raw.get("version")
    version = version if isinstance(version, int) and version >= 1 else 1
    types_out: list[dict] = []
    for t in raw.get("types") or []:
        if not isinstance(t, dict):
            continue
        type_key = str(t.get("type_key") or "").strip()
        if not type_key:
            continue
        fields_out: list[dict] = []
        for f in t.get("fields") or []:
            if not isinstance(f, dict):
                continue
            key = str(f.get("key") or "").strip()
            if not key:
                continue
            dt = str(f.get("datatype") or "text").strip().lower()
            if dt not in _DATATYPES:
                dt = "text"
            fields_out.append({
                "key": key,
                "label": str(f.get("label") or key).strip(),
                "datatype": dt,
                "required": bool(f.get("required")),
            })
        if not fields_out:
            continue
        types_out.append({
            "type_key": type_key,
            "label": str(t.get("label") or type_key).strip(),
            "fields": fields_out,
        })
    return {"version": version, "types": types_out}


def normalize_vocab(raw: Any) -> dict:
    """통제 어휘 정규화. {axis: {label, values[], synonyms{}}}."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict] = {}
    for axis, spec in raw.items():
        if not isinstance(spec, dict):
            continue
        values = [str(v).strip() for v in (spec.get("values") or []) if str(v).strip()]
        syn_raw = spec.get("synonyms") or {}
        synonyms = {
            str(k).strip(): str(v).strip()
            for k, v in syn_raw.items()
            if str(k).strip() and str(v).strip()
        } if isinstance(syn_raw, dict) else {}
        out[str(axis).strip()] = {
            "label": str(spec.get("label") or axis).strip(),
            "values": values,
            "synonyms": synonyms,
        }
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_records_schema.py -v`
Expected: PASS.

- [ ] **Step 5: Extend GroupProfile — failing test**

`tests/test_group_profile.py`에 추가:

```python
def test_parse_profile_record_schema_and_vocab():
    from app.services.group_profile import parse_profile
    p = parse_profile({
        "persona": "x",
        "record_schema": {"types": [{"type_key": "c", "fields": [
            {"key": "e", "datatype": "entity"}]}]},
        "vocab": {"sentiment": {"values": ["긍정"], "synonyms": {"positive": "긍정"}}},
    })
    assert p.record_schema["types"][0]["type_key"] == "c"
    assert p.vocab["sentiment"]["synonyms"]["positive"] == "긍정"


def test_parse_profile_defaults_record_schema_empty():
    from app.services.group_profile import parse_profile
    p = parse_profile({"persona": "x"})
    assert p.record_schema == {"version": 1, "types": []}
    assert p.vocab == {}
```

Run: `python -m pytest tests/test_group_profile.py -k "record_schema or vocab" -v`
Expected: FAIL — `AttributeError: 'GroupProfile' object has no attribute 'record_schema'`.

- [ ] **Step 6: Extend GroupProfile**

`app/services/group_profile.py` 수정:

```python
from app.services.records_schema import normalize_record_schema, normalize_vocab
```

`GroupProfile` dataclass에 추가:

```python
    record_schema: dict = field(default_factory=lambda: {"version": 1, "types": []})
    vocab: dict = field(default_factory=dict)
```

`parse_profile` 반환에 추가:

```python
        record_schema=normalize_record_schema(d.get("record_schema")),
        vocab=normalize_vocab(d.get("vocab")),
```

(주의: `get_typed`는 json 값을 이미 파싱해 dict로 준다. 문자열로 저장돼도 정규화 함수가 `isinstance` 가드로 방어.)

- [ ] **Step 7: Run tests**

Run: `python -m pytest tests/test_group_profile.py tests/test_records_schema.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/services/records_schema.py app/services/group_profile.py tests/test_records_schema.py tests/test_group_profile.py
git commit -m "feat: record_schema·vocab 정규화 + GroupProfile 확장(Phase 2)"
```

---

### Task 3: 필드→승격 컬럼 매핑(순수)

**Files:**
- Modify: `app/services/records_schema.py`
- Test: `tests/test_records_schema.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_records_schema.py 에 추가
from app.services.records_schema import promote_fields
from datetime import date


_CAMPAIGN_TYPE = {
    "type_key": "campaign", "label": "캠페인", "fields": [
        {"key": "entity", "label": "브랜드", "datatype": "entity", "required": True},
        {"key": "message", "label": "메시지", "datatype": "text"},
        {"key": "budget", "label": "규모", "datatype": "number"},
        {"key": "aired_on", "label": "집행", "datatype": "date"},
        {"key": "second_note", "label": "비고", "datatype": "text"},  # 두 번째 text → attrs
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
    # entity required 인데 비면 drop(None 반환)
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_records_schema.py -k promote -v`
Expected: FAIL — `ImportError: cannot import name 'promote_fields'`.

- [ ] **Step 3: Implement promote_fields**

`app/services/records_schema.py`에 추가:

```python
def _to_num(v: Any):
    try:
        s = str(v).strip().replace(",", "")
        if s == "":
            return None
        f = float(s)
        return int(f) if f.is_integer() else f
    except (ValueError, TypeError):
        return None


def _to_date(v: Any):
    from datetime import datetime as _dt
    s = str(v).strip()
    try:
        return _dt.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def promote_fields(type_def: dict, values: dict) -> dict | None:
    """type_def(정규화됨) + LLM이 준 필드 dict → AnalysisRecord 컬럼 dict.

    승격: 첫 entity→entity_name, 첫 text→value_text, 첫 number→value_num,
    첫 date→event_date. 나머지·스키마 밖 필드·파싱실패 원문 → attrs.
    required 필드가 비면 None(drop).
    """
    fields = type_def.get("fields") or []
    by_key = {f["key"]: f for f in fields}
    picked = {"entity": None, "text": None, "number": None, "date": None}
    row = {"entity_name": None, "value_text": None, "value_num": None, "event_date": None}
    attrs: dict[str, Any] = {}

    # 스키마 정의 순서로 승격 시도.
    for f in fields:
        key, dt = f["key"], f["datatype"]
        if key not in values:
            continue
        raw = values[key]
        raw_str = "" if raw is None else str(raw).strip()
        if dt == "entity" and picked["entity"] is None and raw_str:
            picked["entity"] = key
            row["entity_name"] = raw_str
        elif dt == "text" and picked["text"] is None and raw_str:
            picked["text"] = key
            row["value_text"] = raw_str
        elif dt == "number" and picked["number"] is None:
            picked["number"] = key
            num = _to_num(raw)
            if num is None:
                attrs[key] = raw_str
            else:
                row["value_num"] = num
        elif dt == "date" and picked["date"] is None:
            picked["date"] = key
            d = _to_date(raw)
            if d is None:
                attrs[key] = raw_str
            else:
                row["event_date"] = d
        else:
            # 두 번째 이후 동일 datatype 등 — attrs로.
            if raw_str:
                attrs[key] = raw_str

    # 스키마에 없는 여분 필드 → attrs.
    for key, raw in values.items():
        if key in by_key:
            continue
        raw_str = "" if raw is None else str(raw).strip()
        if raw_str:
            attrs[key] = raw_str

    # required 검증: 승격 컬럼 또는 attrs에 값이 있어야 함.
    for f in fields:
        if f.get("required"):
            has = (
                (f["datatype"] == "entity" and row["entity_name"])
                or (f["datatype"] == "text" and row["value_text"])
                or (f["datatype"] == "number" and row["value_num"] is not None)
                or (f["datatype"] == "date" and row["event_date"] is not None)
                or (f["key"] in attrs)
            )
            if not has:
                return None

    row["attrs"] = attrs
    return row
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_records_schema.py -k promote -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/records_schema.py tests/test_records_schema.py
git commit -m "feat: record 필드 승격 매핑 promote_fields(Phase 2)"
```

---

### Task 4: vocab 매핑(순수)

**Files:**
- Modify: `app/services/records_schema.py`
- Test: `tests/test_records_schema.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_records_schema.py 에 추가
from app.services.records_schema import map_vocab_value

_SENT = {"label": "평가", "values": ["긍정", "부정", "혼조"],
         "synonyms": {"positive": "긍정", "bullish": "긍정", "neg": "부정"}}


def test_map_vocab_synonym_hit():
    assert map_vocab_value("Positive", _SENT) == ("긍정", False)
    assert map_vocab_value("  BULLISH ", _SENT) == ("긍정", False)


def test_map_vocab_canonical_passthrough():
    # 이미 canonical 값이면 그대로, pending 아님.
    assert map_vocab_value("부정", _SENT) == ("부정", False)


def test_map_vocab_unmapped_is_pending():
    val, pending = map_vocab_value("애매함", _SENT)
    assert val == "애매함"      # 원문 보존
    assert pending is True


def test_map_vocab_empty():
    assert map_vocab_value("", _SENT) == ("", False)
    assert map_vocab_value(None, _SENT) == (None, False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_records_schema.py -k vocab_ -v`
Expected: FAIL — `ImportError: cannot import name 'map_vocab_value'`.

- [ ] **Step 3: Implement map_vocab_value**

`app/services/records_schema.py`에 추가:

```python
def map_vocab_value(value: Any, axis_spec: dict) -> tuple[Any, bool]:
    """(canonical_or_original, is_pending). 대소문자·공백 정규화 후 매핑.

    - synonyms 적중 또는 이미 values 안이면 (canonical, False).
    - 비어있으면 (원값, False).
    - 미매핑이면 (원문, True) — 호출부가 vocab_pending에 적재.
    """
    if value is None:
        return None, False
    raw = str(value).strip()
    if raw == "":
        return "", False
    key = raw.lower()
    values = axis_spec.get("values") or []
    synonyms = axis_spec.get("synonyms") or {}
    # canonical 직접 일치(대소문자 무시).
    for canon in values:
        if canon.lower() == key:
            return canon, False
    # synonym 일치.
    for syn, canon in synonyms.items():
        if syn.lower() == key:
            return canon, False
    return raw, True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_records_schema.py -v`
Expected: PASS (전체 records_schema 테스트).

- [ ] **Step 5: Commit**

```bash
git add app/services/records_schema.py tests/test_records_schema.py
git commit -m "feat: 통제 어휘 매핑 map_vocab_value(Phase 2)"
```

---

### Task 5: 부트스트랩 v2 — record_schema·vocab 생성

**Files:**
- Modify: `app/services/bootstrap_service.py`
- Test: `tests/test_bootstrap_records.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bootstrap_records.py
from app.services.bootstrap_service import normalize_bootstrap_output_v2


def test_v2_parses_record_schema_and_vocab():
    raw = '''{
      "persona": "큐레이터",
      "digest_sections": [
        {"key": "overview", "kind": "llm", "title": "요약", "guide": "g"},
        {"key": "top_viewed", "kind": "computed", "title": "조회수 상위"}
      ],
      "record_schema": {"version": 1, "types": [
        {"type_key": "topic", "label": "주제", "fields": [
          {"key": "entity", "label": "대상", "datatype": "entity", "required": true},
          {"key": "summary", "label": "요지", "datatype": "text"}]}]},
      "vocab": {"sentiment": {"label": "평가", "values": ["긍정","부정","혼조"],
                              "synonyms": {"positive": "긍정"}}}
    }'''
    persona, sections, record_schema, vocab = normalize_bootstrap_output_v2(raw)
    assert persona == "큐레이터"
    assert len(sections) >= 2
    assert record_schema["types"][0]["type_key"] == "topic"
    assert vocab["sentiment"]["synonyms"]["positive"] == "긍정"


def test_v2_missing_records_keys_yield_empty_schema():
    raw = '''{"persona": "p", "digest_sections": [
        {"key": "a", "kind": "llm", "title": "A", "guide": "g"},
        {"key": "b", "kind": "llm", "title": "B", "guide": "g"}]}'''
    persona, sections, record_schema, vocab = normalize_bootstrap_output_v2(raw)
    assert record_schema == {"version": 1, "types": []}
    assert vocab == {}


def test_v2_bad_json_raises():
    import pytest
    with pytest.raises(ValueError):
        normalize_bootstrap_output_v2("not json")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_bootstrap_records.py -v`
Expected: FAIL — `ImportError: cannot import name 'normalize_bootstrap_output_v2'`.

- [ ] **Step 3: Extend bootstrap_service.py**

프롬프트에 record_schema·vocab 지시 추가 — `_BOOTSTRAP_PROMPT`의 `## 섹션 규칙` 아래, `## 출력 형식` 위에 삽입:

```python
## 레코드 스키마 (record_schema)
- 이 그룹의 영상에서 반복 추출할 "사실 유형"을 1~3개 정의하라.
- 각 type: {"type_key": 영문스네이크, "label": 한글, "fields": [...]}.
- field: {"key": 영문스네이크, "label": 한글, "datatype": "entity|text|number|date", "required": bool}.
- datatype 4종만. entity는 브랜드/인물/종목 등 반복 등장 대상. 그룹 주제에 없으면 types를 빈 배열로.

## 통제 어휘 (vocab)
- 평가/감성처럼 값이 소수로 수렴하는 축을 정의. 없으면 생략(빈 객체).
- 형식: {"sentiment": {"label": "평가", "values": ["긍정","부정","혼조"], "synonyms": {"positive":"긍정"}}}.
```

`## 출력 형식 (JSON만)`의 예시 JSON에 두 key를 추가:

```python
  "record_schema": {"version": 1, "types": []},
  "vocab": {}
```

그리고 v2 정규화 함수 + 저장 배선:

```python
from app.services.records_schema import normalize_record_schema, normalize_vocab


def normalize_bootstrap_output_v2(raw: str) -> tuple[str, list[dict], dict, dict]:
    """LLM 응답 → (persona, sections, record_schema, vocab). 불량 JSON은 ValueError."""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("bootstrap 응답이 객체가 아님")
    persona = str(data.get("persona") or "").strip()
    sections = normalize_sections(data.get("digest_sections"))
    if len(sections) < 2:
        sections = DEFAULT_DIGEST_SECTIONS
    record_schema = normalize_record_schema(data.get("record_schema"))
    vocab = normalize_vocab(data.get("vocab"))
    return persona, sections, record_schema, vocab
```

`bootstrap_profile`의 파싱·저장부를 v2로 교체:

```python
        persona, sections, record_schema, vocab = normalize_bootstrap_output_v2(chat.content)
        await mgr.set_values(group.group_id, "profile", [
            {"key": "persona", "value": persona, "value_type": "string"},
            {"key": "digest_sections", "value": json.dumps(sections, ensure_ascii=False),
             "value_type": "json"},
            {"key": "record_schema", "value": json.dumps(record_schema, ensure_ascii=False),
             "value_type": "json"},
            {"key": "vocab", "value": json.dumps(vocab, ensure_ascii=False),
             "value_type": "json"},
            {"key": "bootstrap_status", "value": "done", "value_type": "string"},
            {"key": "bootstrap_at", "value": datetime.now(timezone.utc).isoformat(),
             "value_type": "string"},
        ])
```

(기존 `normalize_bootstrap_output`는 남겨둔다 — 기존 테스트 `test_bootstrap_service.py`가 참조. 신규 코드만 v2 사용.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_bootstrap_records.py tests/test_bootstrap_service.py -v`
Expected: PASS(신규 + 기존 부트스트랩 테스트 모두).

- [ ] **Step 5: Commit**

```bash
git add app/services/bootstrap_service.py tests/test_bootstrap_records.py
git commit -m "feat: 부트스트랩 v2 record_schema·vocab 생성(Phase 2)"
```

---

### Task 6: 엔티티 사전 서비스 — upsert/alias 조회

**Files:**
- Create: `app/services/entity_service.py`
- Test: `tests/test_entity_service.py`

**Note:** 실 DB 없이 순수 로직(정규화·매칭 판정)을 단위 테스트하고, DB 왕복은 실 DB E2E(계획 말미)에서 검증한다. 여기서는 in-memory 스텁 세션으로 upsert 흐름을 검증한다.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_entity_service.py
import pytest
from app.services.entity_service import canon_key, pick_canonical_match


def test_canon_key_normalizes():
    assert canon_key("  SoftBank ") == "softbank"
    assert canon_key("소프트뱅크") == "소프트뱅크"


def test_pick_canonical_match_by_canonical():
    existing = [
        {"canonical_name": "SoftBank", "aliases": ["소프트뱅크"]},
        {"canonical_name": "KDDI", "aliases": []},
    ]
    assert pick_canonical_match("softbank", existing) == "SoftBank"


def test_pick_canonical_match_by_alias():
    existing = [{"canonical_name": "SoftBank", "aliases": ["소프트뱅크", "SB"]}]
    assert pick_canonical_match("sb", existing) == "SoftBank"


def test_pick_canonical_match_miss():
    existing = [{"canonical_name": "SoftBank", "aliases": []}]
    assert pick_canonical_match("라쿠텐", existing) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_entity_service.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.entity_service`.

- [ ] **Step 3: Implement entity_service.py**

```python
# app/services/entity_service.py
"""데이터 평면 엔티티 사전: 자동 upsert, alias 조회, 병합 배치.

record 저장 시 entity 값을 canonical/alias로 조회해 치환·카운트하고,
미적중이면 status='auto'로 신규 등록한다(사용자 등록 대기 없음).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pg.entity import Entity


def canon_key(name: Any) -> str:
    """대소문자·양끝 공백 무시 매칭 키."""
    return str(name or "").strip().lower()


def pick_canonical_match(key: str, existing: list[dict]) -> str | None:
    """정규화 key에 대해 기존 엔티티(dict: canonical_name, aliases[]) 중 canonical 반환."""
    for e in existing:
        if canon_key(e["canonical_name"]) == key:
            return e["canonical_name"]
        for a in e.get("aliases") or []:
            if canon_key(a) == key:
                return e["canonical_name"]
    return None


async def resolve_and_register(session: AsyncSession, raw_name: str) -> str:
    """엔티티 원문 → canonical. 적중 시 카운트 갱신, 미적중 시 auto 신규 등록.

    같은 데이터 평면 세션(그룹 스키마 바인딩)에서 호출. 커밋은 호출부 책임.
    """
    key = canon_key(raw_name)
    if not key:
        return str(raw_name or "").strip()
    now = datetime.now(timezone.utc)

    rows = (await session.execute(
        select(Entity.entity_pk, Entity.canonical_name, Entity.aliases)
    )).all()
    existing = [{"entity_pk": r[0], "canonical_name": r[1], "aliases": r[2] or []} for r in rows]

    for e in existing:
        if canon_key(e["canonical_name"]) == key or any(canon_key(a) == key for a in e["aliases"]):
            await session.execute(
                update(Entity)
                .where(Entity.entity_pk == e["entity_pk"])
                .values(mention_count=Entity.mention_count + 1, last_seen=now)
            )
            return e["canonical_name"]

    canonical = str(raw_name).strip()
    await session.execute(
        Entity.__table__.insert().values(
            canonical_name=canonical, aliases=[], attrs={}, status="auto",
            mention_count=1, first_seen=now, last_seen=now,
        )
    )
    return canonical
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_entity_service.py -v`
Expected: PASS(순수 함수 4건).

- [ ] **Step 5: Commit**

```bash
git add app/services/entity_service.py tests/test_entity_service.py
git commit -m "feat: 엔티티 사전 서비스 resolve_and_register(Phase 2)"
```

---

### Task 7: records_extractor — 2차 경량 LLM 패스

**Files:**
- Create: `app/services/records_extractor.py`
- Test: `tests/test_records_extractor.py`

- [ ] **Step 1: Write the failing test (프롬프트 조립 + 관대 파싱)**

```python
# tests/test_records_extractor.py
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
    # unknown_type drop, required(entity) 없는 3번째 drop → 1건.
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_records_extractor.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.records_extractor`.

- [ ] **Step 3: Implement records_extractor.py (순수 부분 먼저)**

```python
# app/services/records_extractor.py
"""분석 산출물 텍스트에서 구조화 레코드를 추출하는 2차 경량 LLM 패스.

분석 본 호출을 건드리지 않는다(공유 캐시 보존). save_analysis_to_group 완료 후
best-effort로 실행 — 실패·지연이 분석을 깨뜨리지 않는다.
record_schema 없는 그룹은 전체 skip(무비용).
"""

from __future__ import annotations

import json
from typing import Any

from app.services.records_schema import promote_fields

_RECORDS_PROMPT = """너는 분석 결과에서 구조화된 사실을 뽑아내는 추출기다.
아래 분석 본문에서 record_schema에 정의된 유형의 사실만 추출하라.
본문에 근거 없는 내용은 만들지 마라. 없으면 records를 빈 배열로.

## record_schema
{schema}

## 알려진 엔티티(표기 통일용, 같은 대상이면 이 표기를 써라)
{entities}

## 통제 어휘(해당 축의 값은 아래 표기로 정규화)
{vocab}

## 분석 본문
{analysis_text}

## 출력(JSON만)
{{"records": [{{"type": "<type_key>", "fields": {{"<field_key>": "<값>"}}}}]}}"""


def build_records_prompt(
    analysis_text: str, record_schema: dict, top_entities: list[str], vocab: dict
) -> str:
    return _RECORDS_PROMPT.format(
        schema=json.dumps(record_schema, ensure_ascii=False),
        entities=", ".join(top_entities) if top_entities else "(없음)",
        vocab=json.dumps(vocab, ensure_ascii=False) if vocab else "(없음)",
        analysis_text=analysis_text[:8000],
    )


def parse_records_response(raw: str, record_schema: dict) -> list[dict]:
    """LLM 응답 → AnalysisRecord 컬럼 dict 리스트. 관대 파싱(불량 전부 drop)."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    records = data.get("records")
    if not isinstance(records, list):
        return []
    types_by_key = {t["type_key"]: t for t in record_schema.get("types") or []}
    out: list[dict] = []
    pos_by_type: dict[str, int] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        tkey = str(item.get("type") or "").strip()
        type_def = types_by_key.get(tkey)
        if type_def is None:
            continue
        fields = item.get("fields")
        if not isinstance(fields, dict):
            continue
        row = promote_fields(type_def, fields)
        if row is None:
            continue
        pos = pos_by_type.get(tkey, 0)
        pos_by_type[tkey] = pos + 1
        row["record_type"] = tkey
        row["position"] = pos
        row["schema_version"] = record_schema.get("version", 1)
        out.append(row)
    return out


def analysis_text_for_extraction(analysis: dict) -> str:
    """저장된 분석 dict에서 추출 입력 텍스트를 만든다(영상 재시청 없음)."""
    parts: list[str] = []
    if analysis.get("one_line"):
        parts.append(str(analysis["one_line"]))
    for sec in analysis.get("analysis_sections") or []:
        if isinstance(sec, dict):
            title = sec.get("title") or sec.get("key") or ""
            bullets = sec.get("bullets") or []
            body = " / ".join(str(b) for b in bullets) if isinstance(bullets, list) else ""
            parts.append(f"{title}: {body}")
    for k in ("insights", "key_points", "entities"):
        v = analysis.get(k)
        if isinstance(v, list):
            parts.append(f"{k}: " + ", ".join(str(x) for x in v))
    return "\n".join(p for p in parts if p.strip())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_records_extractor.py -v`
Expected: PASS.

- [ ] **Step 5: Add the orchestration entrypoint — failing test**

`tests/test_records_extractor.py`에 추가(가짜 LLM·스텁 세션으로 오케스트레이션 검증):

```python
import pytest
from app.services import records_extractor as rx


class _FakeChat:
    content = '{"records": [{"type": "campaign", "fields": {"entity": "SoftBank", "message": "5G"}}]}'
    input_tokens = 10
    output_tokens = 5


class _FakeLLM:
    def __init__(self, ai): pass
    async def chat(self, **kw): return _FakeChat()
    async def aclose(self): pass


@pytest.mark.asyncio
async def test_run_records_extraction_skips_without_schema(monkeypatch):
    # record_schema 없으면 LLM 호출 0회.
    called = {"llm": 0}

    async def fake_profile(gid):
        from app.services.group_profile import GroupProfile
        return GroupProfile()  # record_schema 빈 상태

    monkeypatch.setattr(rx, "_load_profile", fake_profile)
    monkeypatch.setattr(rx, "LiteLLMClient", lambda ai: (_ for _ in ()).throw(AssertionError("호출되면 안 됨")))
    # group/session은 스키마 없으면 도달 전 반환.
    await rx.run_records_extraction(group=_stub_group(), video_pk=1, analysis={"one_line": "x"})
    assert called["llm"] == 0


def _stub_group():
    class G: group_id = 7; owner_user_id = 1; slug = "g"
    return G()
```

Run: `python -m pytest tests/test_records_extractor.py -k skips_without_schema -v`
Expected: FAIL — `AttributeError: module has no attribute 'run_records_extraction'`.

- [ ] **Step 6: Implement run_records_extraction**

`app/services/records_extractor.py`에 추가:

```python
from datetime import datetime, timezone

from sqlalchemy import delete

from app.models.control.group import Group
from app.models.pg.analysis_record import AnalysisRecord
from app.models.pg.entity import Entity
from app.models.pg.video_analysis import VideoAnalysis
from app.services.ai_usage_service import budget_ok_for_group, record_usage
from app.services.entity_service import resolve_and_register
from app.services.global_settings import resolve_ai_gateway
from app.services.llm_client import LiteLLMClient
from app.services.records_schema import map_vocab_value
from app.services.settings_manager import get_settings_manager


async def _load_profile(group_id: int):
    return await get_settings_manager().get_profile(group_id)


async def _top_entities(session, limit: int = 30) -> list[str]:
    from sqlalchemy import select
    rows = (await session.execute(
        select(Entity.canonical_name).order_by(Entity.mention_count.desc()).limit(limit)
    )).all()
    return [r[0] for r in rows if r[0]]


async def run_records_extraction(*, group: Group, video_pk: int, analysis: dict) -> None:
    """best-effort. 예외는 전부 삼킨다(분석 성공을 지연·실패시키지 않음)."""
    try:
        profile = await _load_profile(group.group_id)
        record_schema = profile.record_schema
        if not record_schema.get("types"):
            return  # 무비용 skip

        ok, _ = await budget_ok_for_group(group)
        if not ok:
            return

        text_in = analysis_text_for_extraction(analysis)
        if not text_in.strip():
            return

        from app.services.db_engine import data_plane_engine_manager as dpm
        async with dpm.group_session(group) as session:
            top = await _top_entities(session)

        ai = await resolve_ai_gateway(group.group_id)
        model = ai.tagging_model or ai.primary_model
        prompt = build_records_prompt(text_in, record_schema, top, profile.vocab)
        client = LiteLLMClient(ai)
        try:
            chat = await client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=min(ai.max_tokens or 2048, 2048),
                response_format={"type": "json_object"},
            )
        finally:
            await client.aclose()

        await record_usage(
            user_id=group.owner_user_id, group_id=group.group_id,
            purpose="records", model=model,
            input_tokens=chat.input_tokens, output_tokens=chat.output_tokens,
        )

        rows = parse_records_response(chat.content, record_schema)
        vocab = profile.vocab

        async with dpm.group_session(group) as session:
            async with session.begin():
                # 엔티티 정규화(사전 조회·등록) + vocab 매핑.
                pending: list[str] = []
                for row in rows:
                    if row.get("entity_name"):
                        row["entity_name"] = await resolve_and_register(session, row["entity_name"])
                    # attrs 내 vocab 대상 필드 정규화.
                    new_attrs = {}
                    for k, v in (row.get("attrs") or {}).items():
                        if k in vocab:
                            mapped, is_pending = map_vocab_value(v, vocab[k])
                            new_attrs[k] = mapped
                            if is_pending:
                                pending.append(f"{k}:{v}")
                        else:
                            new_attrs[k] = v
                    row["attrs"] = new_attrs

                # delete-insert 멱등.
                await session.execute(
                    delete(AnalysisRecord).where(AnalysisRecord.video_pk == video_pk)
                )
                for row in rows:
                    await session.execute(AnalysisRecord.__table__.insert().values(
                        video_pk=video_pk,
                        record_type=row["record_type"],
                        schema_version=row.get("schema_version", 1),
                        position=row["position"],
                        entity_name=row.get("entity_name"),
                        value_text=row.get("value_text"),
                        value_num=row.get("value_num"),
                        event_date=row.get("event_date"),
                        attrs=row.get("attrs") or {},
                        created_at=datetime.now(timezone.utc),
                    ))

                # 영상 sentiment vocab 정규화(digest sentiment_breakdown 집계 정합).
                if "sentiment" in vocab and analysis.get("sentiment"):
                    mapped, is_pending = map_vocab_value(analysis["sentiment"], vocab["sentiment"])
                    if mapped != analysis["sentiment"]:
                        await session.execute(
                            VideoAnalysis.__table__.update()
                            .where(VideoAnalysis.video_pk == video_pk)
                            .values(sentiment=mapped)
                        )
                    if is_pending:
                        pending.append(f"sentiment:{analysis['sentiment']}")

        if pending:
            await _append_vocab_pending(group.group_id, pending)
    except Exception as e:  # noqa: BLE001 — 후처리 실패는 분석을 막지 않는다
        print(f"[records] {getattr(group, 'slug', '?')} video_pk={video_pk} 실패: {e}")


async def _append_vocab_pending(group_id: int, new_items: list[str]) -> None:
    """profile.vocab_pending(최근 50개)에 미매핑 값을 적재 — Phase 3 보강 제안 입력."""
    mgr = get_settings_manager()
    d = await mgr.get_typed(group_id, "profile")
    existing = d.get("vocab_pending") or []
    if not isinstance(existing, list):
        existing = []
    merged = (existing + new_items)[-50:]
    await mgr.set_values(group_id, "profile", [
        {"key": "vocab_pending", "value": json.dumps(merged, ensure_ascii=False),
         "value_type": "json"},
    ])
```

- [ ] **Step 7: Run tests**

Run: `python -m pytest tests/test_records_extractor.py -v`
Expected: PASS(순수 파싱 + skip 오케스트레이션).

- [ ] **Step 8: Commit**

```bash
git add app/services/records_extractor.py tests/test_records_extractor.py
git commit -m "feat: records_extractor 2차 경량 LLM 패스(Phase 2)"
```

---

### Task 8: 분석 두 경로에 post-pass 훅 + 관리자 백필 엔드포인트

**Files:**
- Modify: `app/services/monitor_service.py`
- Modify: `app/routers/admin.py`
- Test: `tests/test_records_extractor.py`(회귀 훅), `tests/test_admin_backfill_records.py`

**Note:** 훅은 분석 커밋 완료 **후** best-effort로 `run_records_extraction`을 await한다(create_task는 데이터 평면 세션/엔진 수명 관리가 어려워 지양 — 이미 커밋됐으므로 지연이 분석 정확성에 영향 없음). record_schema 없는 그룹은 함수 진입 직후 반환하므로 사실상 무비용.

- [ ] **Step 1: Write the failing regression test (훅이 저장된 분석을 넘기는지)**

```python
# tests/test_records_extractor.py 에 추가
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

    class _Sess: pass
    await ms._records_post_pass(group=_stub_group(), make_session=lambda: None, video_pk=42)
    assert captured["video_pk"] == 42
    assert captured["one_line"] == "요약"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_records_extractor.py -k post_pass -v`
Expected: FAIL — `AttributeError: module 'monitor_service' has no attribute '_records_post_pass'`.

- [ ] **Step 3: Implement the hook helper in monitor_service.py**

import 추가(파일 상단 import 블록):

```python
from app.services.records_extractor import run_records_extraction
```

헬퍼 추가(모듈 함수):

```python
async def _load_analysis_for_records(session, video_pk: int) -> dict | None:
    """저장된 분석을 records 추출 입력용 dict로 로드."""
    from sqlalchemy import select
    from app.models.pg.video_analysis import VideoAnalysis
    row = (await session.execute(
        select(
            VideoAnalysis.one_line, VideoAnalysis.analysis_sections,
            VideoAnalysis.insights, VideoAnalysis.key_points,
            VideoAnalysis.entities, VideoAnalysis.sentiment,
        ).where(VideoAnalysis.video_pk == video_pk)
    )).first()
    if row is None:
        return None
    return {
        "one_line": row[0], "analysis_sections": row[1], "insights": row[2],
        "key_points": row[3], "entities": row[4], "sentiment": row[5],
    }


async def _records_post_pass(*, group, make_session, video_pk: int) -> None:
    """분석 저장 후 records 추출을 best-effort 실행. 예외 삼킴."""
    try:
        async with make_session() as sess:
            analysis = await _load_analysis_for_records(sess, video_pk)
        if analysis is None:
            return
        await run_records_extraction(group=group, video_pk=video_pk, analysis=analysis)
    except Exception as e:  # noqa: BLE001
        print(f"[records] post-pass 실패 (video_pk={video_pk}): {e}")
```

두 경로에 배선:
1. 캐시 적중 경로(`monitor_service.py:788` `_notify_after_analysis` 호출 **직후**):

```python
            await _notify_after_analysis(group, make_session, video_pk, channel_pk)
            await _records_post_pass(group=group, make_session=make_session, video_pk=video_pk)
```

2. 신규 분석 성공 경로: `save_to_db`/`run_and_save`가 성공하고 커밋된 뒤, 해당 함수가 job_log SUCCESS를 쓰는 지점 직후에 동일하게 `await _records_post_pass(...)` 추가. (신규 분석 성공 블록을 `grep -n "STATUS_SUCCESS" app/services/monitor_service.py`로 찾아 캐시 적중 블록과 대칭 배치. `group`/`make_session`/`video_pk` 모두 스코프에 있음.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_records_extractor.py -k post_pass -v`
Expected: PASS.

- [ ] **Step 5: Admin backfill endpoint — failing test**

```python
# tests/test_admin_backfill_records.py
from app.services import records_backfill


def test_backfill_module_exports():
    assert hasattr(records_backfill, "backfill_records_for_group")
```

Run: `python -m pytest tests/test_admin_backfill_records.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.records_backfill`.

- [ ] **Step 6: Implement records_backfill.py + admin endpoint**

```python
# app/services/records_backfill.py
"""관리자 수동 트리거: 기존 구조화 분석 행에 records_extractor를 소급 실행."""

from __future__ import annotations

from sqlalchemy import select

from app.models.control.group import Group
from app.models.pg.video_analysis import VideoAnalysis
from app.services.db_engine import data_plane_engine_manager as dpm
from app.services.records_extractor import run_records_extraction


async def backfill_records_for_group(group: Group, *, limit: int = 500) -> dict:
    """그룹의 분석 완료 영상에 records 추출을 순차 실행. {processed} 반환."""
    await dpm.ensure_schema(group)
    processed = 0
    async with dpm.group_session(group) as session:
        rows = (await session.execute(
            select(
                VideoAnalysis.video_pk, VideoAnalysis.one_line,
                VideoAnalysis.analysis_sections, VideoAnalysis.insights,
                VideoAnalysis.key_points, VideoAnalysis.entities, VideoAnalysis.sentiment,
            ).limit(limit)
        )).all()
    for r in rows:
        analysis = {
            "one_line": r[1], "analysis_sections": r[2], "insights": r[3],
            "key_points": r[4], "entities": r[5], "sentiment": r[6],
        }
        await run_records_extraction(group=group, video_pk=r[0], analysis=analysis)
        processed += 1
    return {"processed": processed}
```

`app/routers/admin.py`에 엔드포인트 추가(패턴: `migrate-schemas` 참고, group 로드는 기존 admin 라우터의 그룹 조회 헬퍼 재사용):

```python
@router.post("/groups/{group_id}/backfill-records")
async def backfill_records(
    group_id: int,
    admin: CurrentUser = Depends(require_admin),
):
    from app.services.records_backfill import backfill_records_for_group
    group = await _load_group_or_404(group_id)  # admin.py 기존 그룹 로더 재사용(없으면 control 세션으로 조회)
    return await backfill_records_for_group(group)
```

(주의: `_load_group_or_404`가 admin.py에 없으면 `app/routers/admin.py`의 기존 그룹 조회 방식 — control DB 세션에서 `Group`을 group_id로 SELECT — 을 그대로 인라인한다. 신규 헬퍼를 만들지 말고 파일 내 기존 패턴을 따를 것.)

- [ ] **Step 7: Run tests**

Run: `python -m pytest tests/test_admin_backfill_records.py -v && python -m pytest tests/ -k "admin" -q`
Expected: PASS(신규) + 기존 admin 테스트 회귀 없음.

- [ ] **Step 8: Full suite regression**

Run: `python -m pytest tests/ -q`
Expected: 신규 테스트 추가분만큼 증가, 사전 실패 항목(`*_route_registered` starlette 핀 미설치·`test_instant_analyze_daily_quota_400`)만 유지. **record_schema 없는 그룹 분석 경로 완전 무변경** 확인.

- [ ] **Step 9: Commit**

```bash
git add app/services/monitor_service.py app/services/records_backfill.py app/routers/admin.py tests/test_admin_backfill_records.py tests/test_records_extractor.py
git commit -m "feat: 분석 두 경로 records post-pass 훅 + 관리자 백필(Phase 2)"
```

---

### Task 9: 엔티티 별칭 병합 일일 배치 + 스케줄러 등록

**Files:**
- Modify: `app/services/entity_service.py`
- Modify: `app/services/scheduler.py`
- Test: `tests/test_entity_service.py`

**Note:** 병합 판정은 경량 LLM 1회(신규 auto 엔티티가 있을 때만). 순수 판정 로직(응답 파싱·자동/보류 분기)을 단위 테스트하고, 실 병합(records UPDATE·job_log)은 실 DB E2E에서 검증한다.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_entity_service.py 에 추가
from app.services.entity_service import parse_merge_response


def test_parse_merge_response_auto_vs_hold():
    raw = '''{"clusters": [
        {"canonical": "SoftBank", "aliases": ["소프트뱅크"], "confidence": "high"},
        {"canonical": "라쿠텐", "aliases": ["Rakuten"], "confidence": "low"}
    ]}'''
    auto, hold = parse_merge_response(raw)
    assert auto == [{"canonical": "SoftBank", "aliases": ["소프트뱅크"]}]
    assert hold == [{"canonical": "라쿠텐", "aliases": ["Rakuten"]}]


def test_parse_merge_response_bad_json():
    assert parse_merge_response("nope") == ([], [])


def test_parse_merge_response_skips_empty_aliases():
    raw = '{"clusters": [{"canonical": "A", "aliases": [], "confidence": "high"}]}'
    auto, hold = parse_merge_response(raw)
    assert auto == [] and hold == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_entity_service.py -k merge -v`
Expected: FAIL — `ImportError: cannot import name 'parse_merge_response'`.

- [ ] **Step 3: Implement parse_merge_response + run_entity_merge_once**

`app/services/entity_service.py`에 추가:

```python
import json


_MERGE_PROMPT = """다음은 한 그룹에서 자동 수집된 엔티티 목록이다.
같은 실체를 가리키는 서로 다른 표기를 클러스터로 묶어라(예: SoftBank/소프트뱅크).
확신이 높은 것만 confidence를 high로. 애매하면 low.

## 엔티티 목록
{names}

## 출력(JSON만)
{{"clusters": [{{"canonical": "<대표표기>", "aliases": ["<흡수될 표기>"], "confidence": "high|low"}}]}}"""


def parse_merge_response(raw: str) -> tuple[list[dict], list[dict]]:
    """(auto[high], hold[그외]). 각 원소 {canonical, aliases[]}. aliases 빈 건 skip."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return [], []
    if not isinstance(data, dict):
        return [], []
    auto, hold = [], []
    for c in data.get("clusters") or []:
        if not isinstance(c, dict):
            continue
        canon = str(c.get("canonical") or "").strip()
        aliases = [str(a).strip() for a in (c.get("aliases") or []) if str(a).strip()]
        if not canon or not aliases:
            continue
        entry = {"canonical": canon, "aliases": aliases}
        if str(c.get("confidence") or "").strip().lower() == "high":
            auto.append(entry)
        else:
            hold.append(entry)
    return auto, hold


async def run_entity_merge_once() -> None:
    """전 활성 그룹 순차: 신규 auto 엔티티 있으면 경량 LLM으로 별칭 병합.

    high confidence만 자동 병합(alias 흡수 + analysis_records.entity_name UPDATE +
    job_log), 그 외는 attrs.merge_candidates로 보류(Phase 3 승인 UI 입력).
    실패는 그룹 단위 격리(전체 배치를 멈추지 않음).
    """
    from sqlalchemy import func, select, update
    from app.control_db import get_sessionmaker
    from app.models.control.group import Group
    from app.services.db_engine import data_plane_engine_manager as dpm
    from app.services.global_settings import resolve_ai_gateway
    from app.services.llm_client import LiteLLMClient
    from app.services.job_log_service import write_job_log  # 실제 경로는 grep로 확인

    Session = get_sessionmaker()
    async with Session() as csess:
        groups = (await csess.execute(select(Group).where(Group.is_active.is_(True)))).scalars().all()

    for group in groups:
        try:
            async with dpm.group_session(group) as session:
                # 신규 auto 엔티티가 없으면 skip(무비용).
                new_count = (await session.execute(
                    select(func.count()).select_from(Entity).where(Entity.status == "auto")
                )).scalar_one()
                if not new_count:
                    continue
                names = [r[0] for r in (await session.execute(
                    select(Entity.canonical_name).order_by(Entity.mention_count.desc()).limit(100)
                )).all()]
            if len(names) < 2:
                continue

            ai = await resolve_ai_gateway(group.group_id)
            model = ai.tagging_model or ai.primary_model
            client = LiteLLMClient(ai)
            try:
                chat = await client.chat(
                    model=model,
                    messages=[{"role": "user", "content": _MERGE_PROMPT.format(names=", ".join(names))}],
                    temperature=0.0,
                    max_tokens=min(ai.max_tokens or 2048, 2048),
                    response_format={"type": "json_object"},
                )
            finally:
                await client.aclose()

            auto, hold = parse_merge_response(chat.content)
            async with dpm.group_session(group) as session:
                async with session.begin():
                    for cluster in auto:
                        await _apply_merge(session, cluster, group)
                    for cluster in hold:
                        await _hold_merge(session, cluster)
        except Exception as e:  # noqa: BLE001
            print(f"[entity-merge] {group.slug} 실패: {e}")


async def _apply_merge(session, cluster: dict, group) -> None:
    """canonical로 aliases를 흡수: alias 엔티티 삭제, canonical.aliases 확장,
    analysis_records.entity_name UPDATE, status='confirmed'."""
    from sqlalchemy import select, update, delete as sa_delete
    from app.models.pg.analysis_record import AnalysisRecord
    from app.services.entity_service import canon_key

    canon = cluster["canonical"]
    aliases = cluster["aliases"]
    crow = (await session.execute(
        select(Entity).where(func_lower_eq(Entity.canonical_name, canon))
    )).scalars().first()
    if crow is None:
        return
    absorbed = list(crow.aliases or [])
    for alias in aliases:
        if canon_key(alias) == canon_key(canon):
            continue
        await session.execute(
            update(AnalysisRecord)
            .where(func_lower_eq(AnalysisRecord.entity_name, alias))
            .values(entity_name=canon)
        )
        await session.execute(
            sa_delete(Entity).where(func_lower_eq(Entity.canonical_name, alias))
        )
        if alias not in absorbed:
            absorbed.append(alias)
    await session.execute(
        update(Entity).where(Entity.entity_pk == crow.entity_pk)
        .values(aliases=absorbed, status="confirmed")
    )
    from app.services.job_log_service import write_job_log_in_session  # 실제 경로 grep 확인
    # job_log 기록은 파일 내 기존 write_job_log 시그니처에 맞춰 배선(from→to 메시지).


async def _hold_merge(session, cluster: dict) -> None:
    """보류 후보를 canonical 엔티티 attrs.merge_candidates에 적재."""
    from sqlalchemy import select, update
    crow = (await session.execute(
        select(Entity).where(func_lower_eq(Entity.canonical_name, cluster["canonical"]))
    )).scalars().first()
    if crow is None:
        return
    attrs = dict(crow.attrs or {})
    cands = attrs.get("merge_candidates") or []
    for a in cluster["aliases"]:
        if a not in cands:
            cands.append(a)
    attrs["merge_candidates"] = cands
    await session.execute(
        update(Entity).where(Entity.entity_pk == crow.entity_pk).values(attrs=attrs)
    )


def func_lower_eq(col, value):
    from sqlalchemy import func
    return func.lower(col) == str(value).strip().lower()
```

**구현 주의(에이전트):** `write_job_log`의 정확한 시그니처·모듈은 `grep -rn "def write_job_log" app/services/` 로 확인해 `_apply_merge`의 job_log 기록을 실제 함수에 맞춰 배선하라(from→to 메시지, `JOB_TYPE`은 기존 상수 재사용 또는 신규 `entity_merge` 문자열). `get_sessionmaker` 임포트 경로도 `grep -rn "def get_sessionmaker" app/` 로 확인(메모리 기준 `app.control_db`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_entity_service.py -v`
Expected: PASS(순수 파싱 테스트).

- [ ] **Step 5: Register in scheduler — add job**

`app/services/scheduler.py`에 import + job 등록(일일):

```python
from app.services.entity_service import run_entity_merge_once
```

`setup_jobs`의 `return scheduler` 직전에:

```python
    scheduler.add_job(
        run_entity_merge_once,        # Phase 2: 엔티티 별칭 병합 배치
        trigger="interval",
        minutes=1440,
        id="entity_merge",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
```

- [ ] **Step 6: Run scheduler + full suite**

Run: `python -m pytest tests/ -k "scheduler or entity" -q && python -m pytest tests/ -q`
Expected: 스케줄러 테스트 통과(job 등록 검증이 있으면), 전체 스위트 사전 실패 외 통과.

- [ ] **Step 7: Commit**

```bash
git add app/services/entity_service.py app/services/scheduler.py tests/test_entity_service.py
git commit -m "feat: 엔티티 별칭 병합 일일 배치 + 스케줄러 등록(Phase 2)"
```

---

### Task 10: 전체 검증 + 리뷰 준비

**Files:** 없음(검증 전용)

- [ ] **Step 1: 전체 백엔드 스위트**

Run: `python -m pytest tests/ -q`
Expected: 신규 테스트 전부 PASS. 사전 실패 baseline만 유지(`*_route_registered` starlette 핀 미설치, `test_instant_analyze_daily_quota_400`). 실패 목록을 Phase 1 완료 시점(415 passed)과 대조해 **신규 회귀 0** 확인.

- [ ] **Step 2: 프론트 회귀(백엔드 전용 변경이므로 무영향 확인)**

Run: `cd frontend && npm run test 2>/dev/null || echo "vitest 스킵(프론트 변경 없음)"`
Expected: Phase 2는 백엔드만 — vitest 45 유지(변경 없음).

- [ ] **Step 3: record_schema 없는 그룹 무변경 회귀 명시 확인**

`test_records_extractor.py::test_run_records_extraction_skips_without_schema`가 LLM 호출 0회를 보장하는지 재확인. 프로덕션 4그룹은 전부 custom digest_prompt 운영이고 record_schema 미보유 → post-pass no-op → **분석 경로 완전 무변경**.

- [ ] **Step 4: 커밋 로그 정리 확인**

Run: `git log --oneline -10`
Expected: Task 1~9 커밋이 순서대로. 작업 트리 clean.

- [ ] **Step 5: 실 DB E2E 체크리스트(사용자/다음 세션이 실행 — DB 도달 필요)**

테스트 DB(`100.115.13.102`, 그룹 `e2e_a`/`e2e_b`)에서 다음을 확인(계획엔 절차만, 실행은 별도):
1. `ensure_schema`가 신규 스키마·기존 스키마 모두에 `analysis_records`·`entities` 테이블 + UNIQUE/인덱스 생성.
2. record_schema 보유 그룹에서 실제 분석 1건 → records/entities 행 생성, 재분석 시 delete-insert 멱등(중복 0).
3. 캐시 적중 경로에서도 records 추출 실행(두 번째 그룹 즉시분석).
4. vocab 매핑: `positive`→`긍정`으로 sentiment 정규화, 미매핑은 `profile.vocab_pending` 적재.
5. 엔티티: SoftBank/소프트뱅크가 병합 배치로 high면 자동 병합(analysis_records.entity_name UPDATE + job_log), low면 merge_candidates 보류.
6. `POST /api/admin/groups/{id}/backfill-records`로 기존 분석 소급 → processed 카운트.
7. **회귀**: record_schema 없는 그룹(u2 등)은 분석해도 records 0행, LLM 호출 0(원장 purpose='records' 행 없음).

---

## Self-Review 결과

**1. 스펙 커버리지(§2.1~2.6):**
- §2.1 프로필 확장(record_schema/vocab) → Task 2·5. ✅
- §2.2 analysis_records 테이블(승격 컬럼·UNIQUE·인덱스) → Task 1·3. ✅
- §2.3 records_extractor(경량 모델·purpose='records'·캐시 적중 포함·관대 파싱·무스키마 skip·백필) → Task 7·8. ✅
- §2.4 통제 어휘 매핑(synonym→canonical·미매핑 pending·breakdown 정합) → Task 4·7(sentiment update). ✅
- §2.5 엔티티 자동 축적(upsert·alias·mention_count) + 병합 배치(auto/hold·records UPDATE·job_log) → Task 6·9. ✅
- §2.6 테스트 항목 전부 태스크에 대응. ✅

**2. Placeholder 스캔:** Task 8·9에 "기존 패턴 grep 확인" 지시가 있으나(파일 내 기존 `write_job_log`/그룹 로더/`get_sessionmaker` 시그니처는 리포지토리 실측이 정확), 이는 placeholder가 아니라 **기존 코드 재사용 지시**다. 그 외 모든 코드 스텝은 실제 코드 포함.

**3. 타입 일관성:** `record_schema`={version,types[{type_key,label,fields[{key,label,datatype,required}]}]}, `promote_fields`→{entity_name,value_text,value_num,event_date,attrs} + `parse_records_response`가 record_type/position/schema_version 추가, `AnalysisRecord` 컬럼과 정확히 일치. `map_vocab_value`→(value, is_pending) 튜플이 Task 7에서 일관 사용. `resolve_and_register`→canonical str. ✅

**주의(에이전트 공통):** 로컬 실행은 드라이브 `.venv` 깨짐 이슈로 homebrew python(`python -m pytest`) 사용. `app.main` import 필요 시 `pip install --break-system-packages argon2-cffi`(메모리 [[digest-sections-group-profile]] §환경 주의). `postgres-ytdb` MCP는 **프로덕션 — 쓰기 절대 금지**.
