# Phase D-2 Implementation Plan: YouTube 쿼터 카운터 + 전 스키마 순회 마이그레이션 도구

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** YouTube API 호출 유닛을 키별·PT일자별로 영속 기록하고 시스템 키 80%/100% 게이트를 강제하며, 전 그룹 스키마를 선제·가시적으로 마이그레이션하는 운영 도구를 추가한다.

**Architecture:** ①`app.yt_quota_usage(usage_date, key_fp)` PK 테이블에 호출마다 즉시 UPSERT(best-effort recorder 콜백을 `YouTubeAPIClient`에 주입), 중앙 폴링 진입 게이트 + 시스템 키 폴백 거부. ②`ensure_schema(force=True)` 순차 순회 서비스 + 부팅 백그라운드 1회 + 관리자 API/버튼.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, PostgreSQL(제어 평면 app 스키마), pytest(FakeSession/monkeypatch 컨벤션), React+TS(frontend), vitest.

**스펙:** `docs/superpowers/specs/2026-07-13-phase-d2-yt-quota-schema-migrator-design.md`

**브랜치:** `feat/phase-d2-yt-quota-schema-migrator` (main에서 분기)

---

## File Structure

| 파일 | 역할 |
|------|------|
| Create `app/models/control/yt_quota_usage.py` | `app.yt_quota_usage` ORM 모델 |
| Create `app/services/yt_quota_service.py` | 지문·PT날짜·UPSERT recorder·게이트 판정 단일 소유 |
| Create `app/services/schema_migrator.py` | 전 그룹 순회 ensure_schema + 리포트 |
| Modify `app/control_db.py` | 모델 임포트 등록 |
| Modify `app/services/youtube_api.py` | `recorder` 파라미터(선택, 기본 no-op) |
| Modify `app/services/global_settings.py` | `youtube_daily_quota` 키 + 폴백 하드 게이트 |
| Modify `app/services/central_poller.py` | 진입 게이트 + 상태 전환 로그 + recorder 배선 |
| Modify `app/services/monitor_service.py` | recorder 배선 3곳 + 폴백 거부 처리 |
| Modify `app/routers/channels.py`, `app/routers/videos.py` | recorder 배선 + 폴백 거부 → 400 |
| Modify `app/services/db_engine.py` | `ensure_schema(force=)` |
| Modify `app/main.py` | 부팅 백그라운드 마이그레이션 태스크 |
| Modify `app/routers/admin.py` | `_GLOBAL_KEYS`·검증, usage youtube 섹션, `POST /migrate-schemas` |
| Modify `app/schemas/admin.py` | YtQuota·MigrateSchemas 스키마 |
| Modify `frontend/src/api/admin.ts`, `frontend/src/pages/Admin.tsx` | 쿼터 카드 + 마이그레이션 버튼 |
| Test `tests/test_yt_quota_service.py`, `tests/test_yt_quota_gate.py`, `tests/test_schema_migrator.py` | 신규 테스트 |
| Test `tests/test_youtube_api_recorder.py` | recorder 주입 동작 |
| Modify `tests/test_admin_api.py`(등록 확인), `tests/test_central_poller.py`(게이트) | 기존 테스트 확장 |

---

### Task 0: 브랜치 생성

- [ ] **Step 1: main에서 브랜치 분기**

```bash
git checkout main && git checkout -b feat/phase-d2-yt-quota-schema-migrator
```

---

### Task 1: YtQuotaUsage 모델 + 제어 스키마 등록

**Files:**
- Create: `app/models/control/yt_quota_usage.py`
- Modify: `app/control_db.py:62-77` (모델 임포트 목록)
- Test: `tests/test_yt_quota_service.py` (신규 — 모델 검증 포함)

- [ ] **Step 1: 실패하는 테스트 작성**

```python
"""yt_quota_usage 모델·서비스 검증. SQL은 FakeSession, 실 SQL은 E2E."""

from app.control_db import APP_SCHEMA


def test_yt_quota_usage_model_shape():
    from app.models.control.yt_quota_usage import YtQuotaUsage

    t = YtQuotaUsage.__table__
    assert t.schema == APP_SCHEMA
    assert t.name == "yt_quota_usage"
    # 복합 PK (usage_date, key_fp) — 키별 카운트 (스펙 D1)
    assert {c.name for c in t.primary_key.columns} == {"usage_date", "key_fp"}
    assert t.c.units.nullable is False


def test_model_registered_in_control_metadata():
    # ensure_control_schema의 임포트 목록에 등록돼 create_all 대상이어야 한다
    import app.models.control.yt_quota_usage  # noqa: F401
    from app.control_db import Base

    assert f"{APP_SCHEMA}.yt_quota_usage" in Base.metadata.tables
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_yt_quota_service.py -v`
Expected: FAIL — `ModuleNotFoundError: app.models.control.yt_quota_usage`

- [ ] **Step 3: 모델 구현**

`app/models/control/yt_quota_usage.py`:

```python
"""app.yt_quota_usage — YouTube API 쿼터 원장 (스펙 D-2 §1.1).

키 원문은 저장하지 않는다 — SHA-256 앞 12자 지문(key_fp)만. usage_date는
PT(America/Los_Angeles) 자정 기준: Google 실제 쿼터 리셋 시점과 일치.
날짜가 바뀌면 새 행이 시작되므로 별도 리셋 잡 불필요.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class YtQuotaUsage(Base):
    __tablename__ = "yt_quota_usage"
    __table_args__ = {"schema": APP_SCHEMA}

    usage_date: Mapped[date] = mapped_column(Date, primary_key=True)
    key_fp: Mapped[str] = mapped_column(Text, primary_key=True)
    units: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

`app/control_db.py` 임포트 목록(62행 `from app.models.control import (`)에 `yt_quota_usage,`를 알파벳 순서(user_limit 다음)에 추가.

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_yt_quota_service.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add app/models/control/yt_quota_usage.py app/control_db.py tests/test_yt_quota_service.py
git commit -m "feat: app.yt_quota_usage 테이블 — YouTube 쿼터 원장 (키지문×PT일자 복합 PK)"
```

---

### Task 2: yt_quota_service — 지문·PT날짜·recorder·조회

**Files:**
- Create: `app/services/yt_quota_service.py`
- Test: `tests/test_yt_quota_service.py` (확장)

- [ ] **Step 1: 실패하는 테스트 추가** (`tests/test_yt_quota_service.py`에 append)

```python
from datetime import datetime, timezone
from types import SimpleNamespace

from app.services import yt_quota_service as yq


def test_key_fingerprint_deterministic_and_short():
    fp = yq.key_fingerprint("AIza-example-key")
    assert fp == yq.key_fingerprint("AIza-example-key")
    assert len(fp) == 12
    assert fp != yq.key_fingerprint("AIza-other-key")
    # 원문이 지문에 노출되지 않음
    assert "AIza" not in fp


def test_pt_today_crosses_date_line():
    # UTC 07-13 06:00 = PT 07-12 23:00 (PDT, UTC-7) → PT 날짜는 아직 07-12
    now = datetime(2026, 7, 13, 6, 0, tzinfo=timezone.utc)
    assert yq.pt_today(now).isoformat() == "2026-07-12"
    # UTC 07-13 08:00 = PT 07-13 01:00 → 07-13
    now = datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc)
    assert yq.pt_today(now).isoformat() == "2026-07-13"


async def test_make_recorder_swallows_db_failure(monkeypatch):
    # DB가 완전히 죽어도 recorder는 예외를 던지지 않는다 (스펙 D5 best-effort)
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(yq, "get_sessionmaker", boom)
    rec = yq.make_recorder("AIza-x")
    await rec(3)  # 예외 없이 통과해야 함


async def test_units_today_returns_zero_when_no_row():
    class FakeResult:
        def scalar_one_or_none(self):
            return None

    class FakeSession:
        async def execute(self, stmt):
            return FakeResult()

    assert await yq.units_today(FakeSession(), "abc123def456") == 0
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_yt_quota_service.py -v`
Expected: 신규 4개 FAIL — `AttributeError`/`ImportError`

- [ ] **Step 3: 서비스 구현**

`app/services/yt_quota_service.py`:

```python
"""YouTube 쿼터 원장 서비스 (스펙 D-2 §1).

- key_fingerprint: 키 원문 대신 SHA-256 앞 12자만 저장(유출 면적 0).
- pt_today: Google 쿼터 리셋(PT 자정) 기준 날짜. DST는 zoneinfo가 처리.
- make_recorder: 호출마다 즉시 UPSERT. 기록 실패는 삼킨다 —
  원장 장애가 폴링/분석을 절대 깨뜨리지 않는다(ai_usage 패턴).
- 게이트 판정(system_gate_state)은 Task 5에서 추가.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.control_db import get_sessionmaker
from app.models.control.yt_quota_usage import YtQuotaUsage

QuotaRecorder = Callable[[int], Awaitable[None]]

_PT = ZoneInfo("America/Los_Angeles")


def key_fingerprint(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]


def pt_today(now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(_PT).date()


async def record_units(session: AsyncSession, key_fp: str, units: int) -> None:
    """(오늘PT, key_fp) 행에 units 누적 UPSERT. 커밋은 호출부 책임."""
    stmt = pg_insert(YtQuotaUsage).values(
        usage_date=pt_today(), key_fp=key_fp, units=units
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[YtQuotaUsage.usage_date, YtQuotaUsage.key_fp],
        set_={"units": YtQuotaUsage.units + stmt.excluded.units, "updated_at": func.now()},
    )
    await session.execute(stmt)


def make_recorder(api_key: str) -> QuotaRecorder:
    """YouTubeAPIClient에 주입할 best-effort recorder."""
    fp = key_fingerprint(api_key)

    async def _rec(units: int) -> None:
        try:
            sf = get_sessionmaker()
            async with sf() as session:
                async with session.begin():
                    await record_units(session, fp, units)
        except Exception as e:  # noqa: BLE001 — 스펙 D5: 원장 실패는 호출을 안 깨뜨림
            print(f"[yt-quota] 기록 실패(무시): {e}")

    return _rec


async def units_today(session: AsyncSession, key_fp: str) -> int:
    row = (
        await session.execute(
            select(YtQuotaUsage.units).where(
                YtQuotaUsage.usage_date == pt_today(), YtQuotaUsage.key_fp == key_fp
            )
        )
    ).scalar_one_or_none()
    return int(row or 0)
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_yt_quota_service.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/yt_quota_service.py tests/test_yt_quota_service.py
git commit -m "feat: yt_quota_service — 키 지문·PT날짜·즉시 UPSERT recorder(best-effort)·당일 조회"
```

---

### Task 3: YouTubeAPIClient recorder 파라미터

**Files:**
- Modify: `app/services/youtube_api.py:91-123` (`__init__`, `_get`)
- Test: `tests/test_youtube_api_recorder.py` (신규)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_youtube_api_recorder.py`:

```python
"""YouTubeAPIClient recorder 주입 — 시도마다 유닛 기록, 미주입 시 무변경."""

import httpx
import pytest

from app.services.settings_types import PollingSettings
from app.services.youtube_api import YouTubeAPIClient, YouTubeAPIError


def _client(status=200, payload=None):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload if payload is not None else {"items": []})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _polling():
    return PollingSettings(youtube_api_key="AIza-test")


async def test_recorder_called_with_units_on_success():
    recorded = []

    async def rec(units: int) -> None:
        recorded.append(units)

    api = YouTubeAPIClient(_polling(), client=_client(), recorder=rec)
    await api._get("videos", {"part": "id"}, 1)
    assert recorded == [1]


async def test_recorder_called_even_on_http_error():
    # Google은 실패 호출도 과금 — 시도 기준 기록 (스펙 §1.2)
    recorded = []

    async def rec(units: int) -> None:
        recorded.append(units)

    api = YouTubeAPIClient(_polling(), client=_client(status=500), recorder=rec)
    with pytest.raises(YouTubeAPIError):
        await api._get("videos", {"part": "id"}, 1)
    assert recorded == [1]


async def test_search_records_100_units():
    recorded = []

    async def rec(units: int) -> None:
        recorded.append(units)

    api = YouTubeAPIClient(
        _polling(),
        client=_client(payload={"items": [{"snippet": {"channelId": "UCx"}}]}),
        recorder=rec,
    )
    await api._resolve_by_search("some channel")
    assert recorded == [100]


async def test_no_recorder_keeps_existing_behavior():
    api = YouTubeAPIClient(_polling(), client=_client())
    data = await api._get("videos", {"part": "id"}, 1)
    assert data == {"items": []}
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_youtube_api_recorder.py -v`
Expected: FAIL — `__init__() got an unexpected keyword argument 'recorder'`

- [ ] **Step 3: 구현** — `app/services/youtube_api.py`

`__init__` 시그니처 교체(91행):

```python
    def __init__(
        self,
        polling: PollingSettings,
        client: httpx.AsyncClient | None = None,
        recorder: "Callable[[int], Awaitable[None]] | None" = None,
    ) -> None:
```

(파일 상단 `from typing import Any, Dict, Iterable, List, Tuple`에 `Awaitable, Callable` 추가.)
`__init__` 본문 끝에 `self._recorder = recorder` 추가.

`_get`(116행)의 `self._consume_quota(quota_units)` 직후에 삽입:

```python
        if self._recorder is not None:
            await self._recorder(quota_units)
```

모듈 docstring 5행의 "쿼터는 런타임 메모리에서 일자 기준으로만 관리(초기 버전)"를
"쿼터: 인스턴스 메모리 가드(2차 방어) + 선택적 recorder로 영속 기록(yt_quota_service)"로 갱신.

- [ ] **Step 4: 통과 확인 + 기존 회귀 없음**

Run: `python -m pytest tests/test_youtube_api_recorder.py tests/test_yt_parsing.py -v`
Expected: 전부 passed (test_yt_parsing.py가 없으면 해당 파일 생략)

- [ ] **Step 5: Commit**

```bash
git add app/services/youtube_api.py tests/test_youtube_api_recorder.py
git commit -m "feat: YouTubeAPIClient recorder 주입 — HTTP 시도마다 유닛 기록(실패 응답 포함)"
```

---

### Task 4: 전역 youtube_daily_quota 키 + 관리자 노출

**Files:**
- Modify: `app/services/global_settings.py` (키 상수·접근자)
- Modify: `app/routers/admin.py:70-79` (`_GLOBAL_KEYS`), PUT 검증(244행 이후)
- Test: `tests/test_global_settings.py`, `tests/test_admin_api.py` (확장)

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_global_settings.py`에 append (파일의 FakeSession/FakeResult 재사용):

```python
async def test_get_youtube_daily_quota_default_and_clamp():
    from types import SimpleNamespace as NS

    # 행 없음 → 기본 10000
    assert await gs.get_youtube_daily_quota(FakeSession([FakeResult(None)])) == 10000
    # 비정상(비숫자/0 이하) → 기본
    bad = NS(key="youtube_daily_quota", value="abc", value_enc=None, is_secret=False)
    assert await gs.get_youtube_daily_quota(FakeSession([FakeResult(bad)])) == 10000
    zero = NS(key="youtube_daily_quota", value="0", value_enc=None, is_secret=False)
    assert await gs.get_youtube_daily_quota(FakeSession([FakeResult(zero)])) == 10000
    # 정상값
    ok = NS(key="youtube_daily_quota", value="50000", value_enc=None, is_secret=False)
    assert await gs.get_youtube_daily_quota(FakeSession([FakeResult(ok)])) == 50000
```

`tests/test_admin_api.py`에 append (기존 `test_put_global_settings_poll_floor_must_be_positive_int` 패턴 그대로 — 그 테스트의 monkeypatch·클라이언트 구성을 복사해 키만 교체):

```python
def test_global_settings_includes_youtube_daily_quota():
    from app.routers import admin as admin_router

    assert "youtube_daily_quota" in admin_router._GLOBAL_KEYS
```

(PUT 양의 정수 검증 테스트는 기존 poll_floor 테스트와 동일 구조로
`youtube_daily_quota` 키에 `"abc"`, `"0"` → 400, `"50000"` → 저장 확인을 추가한다.
기존 테스트 코드 76-131행의 픽스처 구성을 그대로 따른다.)

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_global_settings.py tests/test_admin_api.py -v`
Expected: 신규 테스트 FAIL

- [ ] **Step 3: 구현**

`app/services/global_settings.py` — 상수(31행 아래):

```python
# Phase D-2: YouTube 쿼터 원장 (스펙 §1.3)
GLOBAL_YOUTUBE_DAILY_QUOTA = "youtube_daily_quota"
DEFAULT_YOUTUBE_DAILY_QUOTA = 10000
```

접근자(get_central_poll_floor_min 아래, 같은 패턴):

```python
async def get_youtube_daily_quota(session: AsyncSession) -> int:
    raw = await get_global(session, GLOBAL_YOUTUBE_DAILY_QUOTA)
    try:
        v = int(raw) if raw is not None else DEFAULT_YOUTUBE_DAILY_QUOTA
    except (TypeError, ValueError):
        return DEFAULT_YOUTUBE_DAILY_QUOTA
    return v if v > 0 else DEFAULT_YOUTUBE_DAILY_QUOTA
```

`app/routers/admin.py` — import에 `GLOBAL_YOUTUBE_DAILY_QUOTA` 추가, `_GLOBAL_KEYS` 튜플에 `GLOBAL_YOUTUBE_DAILY_QUOTA,` 추가(GLOBAL_CENTRAL_POLL_FLOOR_MIN 다음). PUT 핸들러의 central_poll_floor_min 검증 블록 아래에 동일 패턴 추가:

```python
        if item.key == GLOBAL_YOUTUBE_DAILY_QUOTA:
            try:
                quota = int(value)
            except ValueError:
                quota = 0
            if quota <= 0:
                raise HTTPException(
                    status_code=400, detail="youtube_daily_quota는 양의 정수여야 합니다."
                )
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_global_settings.py tests/test_admin_api.py -v`
Expected: 전부 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/global_settings.py app/routers/admin.py tests/test_global_settings.py tests/test_admin_api.py
git commit -m "feat: 전역 youtube_daily_quota 키 — 기본 10000, 관리자 편집(양의 정수 검증)"
```

---

### Task 5: 게이트 — system_gate_state + 중앙 폴링 진입 + 폴백 거부

**Files:**
- Modify: `app/services/yt_quota_service.py` (게이트 판정)
- Modify: `app/services/central_poller.py:132-145` (`run_central_poll_once` 진입)
- Modify: `app/services/global_settings.py:102-107` (`resolve_youtube_key` 하드 게이트)
- Test: `tests/test_yt_quota_gate.py` (신규), `tests/test_central_poller.py` (확장)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_yt_quota_gate.py`:

```python
"""80%/100% 게이트 판정 + 시스템 키 폴백 거부 (스펙 §1.4)."""

from types import SimpleNamespace

import pytest

from app.services import yt_quota_service as yq


def test_gate_state_boundaries():
    # limit=10000: 7999=ok, 8000=soft(80%), 10000=hard(100%)
    assert yq.gate_state(7999, 10000) == yq.GATE_OK
    assert yq.gate_state(8000, 10000) == yq.GATE_SOFT
    assert yq.gate_state(9999, 10000) == yq.GATE_SOFT
    assert yq.gate_state(10000, 10000) == yq.GATE_HARD
    assert yq.gate_state(15000, 10000) == yq.GATE_HARD


async def test_system_gate_state_no_key_is_ok(monkeypatch):
    async def no_key():
        return ""

    monkeypatch.setattr(yq, "get_system_youtube_key", no_key)
    state, used, limit = await yq.system_gate_state()
    assert state == yq.GATE_OK


async def test_system_gate_state_reads_usage(monkeypatch):
    async def key():
        return "AIza-sys"

    class FakeResult:
        def __init__(self, v):
            self._v = v

        def scalar_one_or_none(self):
            return self._v

    class FakeSession:
        # 구현의 호출 순서: ①get_youtube_daily_quota(행 없음→기본 10000) ②units_today(8500)
        def __init__(self):
            self.calls = 0

        async def execute(self, stmt):
            self.calls += 1
            return FakeResult(None) if self.calls == 1 else FakeResult(8500)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(yq, "get_system_youtube_key", key)
    monkeypatch.setattr(yq, "get_sessionmaker", lambda: FakeSession)
    state, used, limit = await yq.system_gate_state()
    assert (state, used, limit) == (yq.GATE_SOFT, 8500, 10000)


async def test_resolve_youtube_key_hard_gate_blocks_fallback(monkeypatch):
    from app.services import global_settings as gs
    from app.services.youtube_api import YouTubeQuotaExceededError

    async def fake_get_polling(group_id):
        return SimpleNamespace(youtube_api_key="")  # 그룹 키 없음 → 폴백 경로

    monkeypatch.setattr(
        gs, "get_settings_manager",
        lambda: SimpleNamespace(get_polling=fake_get_polling),
    )

    async def hard():
        return True

    monkeypatch.setattr(yq, "system_hard_blocked", hard)
    with pytest.raises(YouTubeQuotaExceededError):
        await gs.resolve_youtube_key(1)


async def test_resolve_youtube_key_group_key_unaffected_by_hard_gate(monkeypatch):
    from app.services import global_settings as gs

    async def fake_get_polling(group_id):
        return SimpleNamespace(youtube_api_key="group-key")

    monkeypatch.setattr(
        gs, "get_settings_manager",
        lambda: SimpleNamespace(get_polling=fake_get_polling),
    )

    async def hard():
        raise AssertionError("그룹 키가 있으면 게이트를 조회조차 안 한다")

    monkeypatch.setattr(yq, "system_hard_blocked", hard)
    assert await gs.resolve_youtube_key(1) == "group-key"
```

`tests/test_central_poller.py`의 `wired` 픽스처에 게이트 통과 배선 추가 —
픽스처 끝(`return calls` 직전)에:

```python
    async def fake_gate():
        return (cp.yq.GATE_OK, 0, 10000)

    monkeypatch.setattr(cp.yq, "system_gate_state", fake_gate)
```

(주의: central_poller가 `from app.services import yt_quota_service as yq` 형태로
임포트하도록 구현한다 — 모듈 속성 monkeypatch가 가능해야 함.)

그리고 신규 테스트 append:

```python
async def test_soft_gate_skips_central_polling(wired, monkeypatch):
    async def soft_gate():
        return (cp.yq.GATE_SOFT, 8000, 10000)

    monkeypatch.setattr(cp.yq, "system_gate_state", soft_gate)
    await cp.run_central_poll_once()
    assert wired.fetched == []  # 신규 폴링 없음


async def test_hard_gate_skips_central_polling(wired, monkeypatch):
    async def hard_gate():
        return (cp.yq.GATE_HARD, 10000, 10000)

    monkeypatch.setattr(cp.yq, "system_gate_state", hard_gate)
    await cp.run_central_poll_once()
    assert wired.fetched == []
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_yt_quota_gate.py tests/test_central_poller.py -v`
Expected: 신규 테스트 FAIL

- [ ] **Step 3: 구현**

`app/services/yt_quota_service.py`에 추가:

```python
from app.services.global_settings import (  # noqa: E402 — 파일 상단 import 블록에 배치
    get_system_youtube_key,
    get_youtube_daily_quota,
)

GATE_OK = "ok"
GATE_SOFT = "soft"   # ≥80%: 신규 중앙 폴링 skip (사용자 발 호출은 계속)
GATE_HARD = "hard"   # ≥100%: 시스템 키 호출 전면 중단 (그룹 자체 키는 무영향)


def gate_state(used: int, limit: int) -> str:
    if used >= limit:
        return GATE_HARD
    if used >= limit * 0.8:
        return GATE_SOFT
    return GATE_OK


async def system_gate_state() -> tuple[str, int, int]:
    """(상태, 당일 사용량, 한도). 시스템 키 미설정이면 ok(중앙 폴링이 자체 skip)."""
    key = await get_system_youtube_key()
    if not key:
        return GATE_OK, 0, 0
    sf = get_sessionmaker()
    async with sf() as session:
        limit = await get_youtube_daily_quota(session)
        used = await units_today(session, key_fingerprint(key))
    return gate_state(used, limit), used, limit


async def system_hard_blocked() -> bool:
    state, _used, _limit = await system_gate_state()
    return state == GATE_HARD
```

**순환 임포트 주의:** `global_settings`는 `yt_quota_service`를 함수 내부에서 지연
임포트한다(아래). `yt_quota_service` → `global_settings` 방향만 모듈 레벨.

`app/services/global_settings.py` — `resolve_youtube_key` 교체:

```python
async def resolve_youtube_key(group_id: int) -> str:
    """그룹 스코프 호출용: 그룹 polling 키 우선, 없으면 시스템 키. 둘 다 없으면 ''.

    시스템 키 폴백은 하드 게이트(당일 100% 소진) 시 YouTubeQuotaExceededError.
    그룹 자체 키는 게이트와 무관 (스펙 §1.4).
    """
    polling = await get_settings_manager().get_polling(group_id)
    if polling.youtube_api_key:
        return polling.youtube_api_key
    from app.services import yt_quota_service as yq  # 순환 임포트 회피

    if await yq.system_hard_blocked():
        from app.services.youtube_api import YouTubeQuotaExceededError

        raise YouTubeQuotaExceededError(
            "시스템 YouTube 키 일일 쿼터 소진 — PT 자정 리셋까지 시스템 키 사용 불가"
        )
    return await get_system_youtube_key()
```

(테스트가 `yq.system_hard_blocked`를 monkeypatch하므로 `from app.services import
yt_quota_service as yq` 임포트 형태 유지 필수.)

`app/services/central_poller.py` — 모듈 상단에 `from app.services import
yt_quota_service as yq` 추가. 모듈 레벨 상태 + 전환 로그:

```python
_last_gate_state = yq.GATE_OK


def _log_gate_transition(state: str, used: int, limit: int) -> None:
    """상태 전환 시 1회만 stdout 경고 — 틱마다 스팸 방지 (스펙 §1.4)."""
    global _last_gate_state
    if state == _last_gate_state:
        return
    print(
        f"[central-poll] 쿼터 게이트 {_last_gate_state} → {state}: "
        f"시스템 키 당일 {used}/{limit} 유닛"
    )
    _last_gate_state = state
```

`run_central_poll_once`의 `system_key` 확보 직후(`if not system_key: return` 다음)에 삽입:

```python
    state, used, limit = await yq.system_gate_state()
    _log_gate_transition(state, used, limit)
    if state != yq.GATE_OK:
        return
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_yt_quota_gate.py tests/test_central_poller.py tests/test_global_settings.py -v`
Expected: 전부 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/yt_quota_service.py app/services/global_settings.py app/services/central_poller.py tests/test_yt_quota_gate.py tests/test_central_poller.py
git commit -m "feat: 쿼터 게이트 — 80% 중앙폴링 skip·100% 시스템키 전면 차단, 상태전환 1회 로그"
```

---

### Task 6: resolve_youtube_key 폴백 거부의 호출부 처리 + recorder 배선 6곳

**Files:**
- Modify: `app/routers/channels.py:64-71`, `app/routers/videos.py:500,525`
- Modify: `app/services/monitor_service.py:330-352,932-961,1022-1045`
- Modify: `app/services/central_poller.py:145`
- Test: `tests/test_yt_quota_gate.py` (확장)

- [ ] **Step 1: 실패하는 테스트 추가** (`tests/test_yt_quota_gate.py`에 append)

```python
async def test_channels_router_maps_quota_error_to_400(monkeypatch):
    """resolve_youtube_key가 쿼터 소진을 던지면 500이 아니라 400."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers import channels as ch
    from app.routers.auth import CurrentUser, require_user
    from app.routers.deps import get_owned_group_or_404
    from app.services.auth_service import set_users_exist
    from app.services.youtube_api import YouTubeQuotaExceededError

    set_users_exist(True)
    try:
        async def _u():
            return CurrentUser(user_id=1, email="a@x.com", display_name="A", role="admin")

        async def _g():
            return SimpleNamespace(group_id=1, slug="g", schema_name="s", owner_user_id=1)

        app.dependency_overrides[require_user] = _u
        app.dependency_overrides[get_owned_group_or_404] = _g

        async def boom(group_id):
            raise YouTubeQuotaExceededError("시스템 키 소진")

        monkeypatch.setattr(ch, "resolve_youtube_key", boom)
        # 쿼터 검사 우회(다른 관심사)
        async def no_limits(*a, **k):
            return None
        monkeypatch.setattr(ch, "limits_for_user", no_limits, raising=False)

        c = TestClient(app, raise_server_exceptions=False)
        resp = c.post("/api/groups/g/channels", json={"channel_input": "@x"})
        assert resp.status_code == 400
        assert "쿼터" in resp.json()["detail"]
    finally:
        set_users_exist(False)
        app.dependency_overrides.clear()
```

(주의: 채널 추가 엔드포인트의 실제 경로·페이로드 필드명은 `app/routers/channels.py`의
라우트 데코레이터에서 확인해 맞춘다. 쿼터 한도 검사 의존성이 먼저 실행되면
해당 의존성도 override — 구현 시 실제 코드에 맞게 조정하되 검증 대상은
"YouTubeQuotaExceededError → 400 + 메시지"로 동일.)

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_yt_quota_gate.py -v`
Expected: 신규 테스트 FAIL (현재는 예외가 500으로 전파)

- [ ] **Step 3: 구현 — 호출부 5곳 + recorder 배선 6곳**

**패턴 1 — 라우터 2곳(400 매핑 + recorder):**

`app/routers/channels.py` (64행 부근) — import에 `YouTubeQuotaExceededError`,
`from app.services.yt_quota_service import make_recorder` 추가:

```python
    try:
        api_key = await resolve_youtube_key(group.group_id)
    except YouTubeQuotaExceededError as e:
        raise HTTPException(status_code=400, detail=f"YouTube 쿼터 소진: {e}")
```

71행 클라이언트 생성을:

```python
    api = YouTubeAPIClient(polling, recorder=make_recorder(api_key))
```

`app/routers/videos.py` (500행 부근 `resolve_youtube_key` 호출)에 동일한 try/except
400 매핑, 525행 `YouTubeAPIClient(polling)` →
`YouTubeAPIClient(polling, recorder=make_recorder(polling.youtube_api_key))`.

**패턴 2 — monitor_service 3곳(skip 처리 + recorder):**

`_poll_group`(330행), 통계 갱신(932행), 단건 폴링(1022행) 각각의
`api_key = await resolve_youtube_key(group.group_id)`를:

```python
    try:
        api_key = await resolve_youtube_key(group.group_id)
    except YouTubeQuotaExceededError as e:
        print(f"[{group.slug}] 시스템 키 쿼터 소진 - SKIP: {e}")
        return
```

(932행 통계 갱신은 그룹 루프 내부이므로 `return` 대신 `continue` — 실제 제어 구조에
맞춘다.) 각 지점의 `YouTubeAPIClient(polling)` →
`YouTubeAPIClient(polling, recorder=make_recorder(polling.youtube_api_key))`.
import에 `YouTubeQuotaExceededError`(이미 있으면 재사용), `make_recorder` 추가.

**패턴 3 — central_poller(145행):**

```python
    api_client = YouTubeAPIClient(
        PollingSettings(youtube_api_key=system_key),
        recorder=yq.make_recorder(system_key),
    )
```

- [ ] **Step 4: 통과 확인 + 전체 회귀**

Run: `python -m pytest tests/ -x -q`
Expected: 전부 passed (기존 306 + 신규)

- [ ] **Step 5: Commit**

```bash
git add app/routers/channels.py app/routers/videos.py app/services/monitor_service.py app/services/central_poller.py tests/test_yt_quota_gate.py
git commit -m "feat: recorder 6개 지점 배선 + 폴백 쿼터 소진 400/SKIP 매핑"
```

---

### Task 7: 관리자 usage 응답 youtube 섹션

**Files:**
- Modify: `app/schemas/admin.py:165-171` (AdminUsageResponse)
- Modify: `app/routers/admin.py:365-417` (usage_summary)
- Test: `tests/test_admin_usage_api.py` (확장)

- [ ] **Step 1: 실패하는 테스트 추가** (`tests/test_admin_usage_api.py`에 append)

```python
def test_build_yt_quota_entries_marks_system_key():
    from app.routers.admin import build_yt_quota_entries

    rows = [("aaa111bbb222", 8000), ("ccc333ddd444", 120)]
    entries = build_yt_quota_entries(rows, daily_quota=10000, system_fp="aaa111bbb222")
    assert entries[0].key_fp == "aaa111bbb222"
    assert entries[0].is_system_key is True
    assert entries[0].pct == 80.0
    assert entries[1].is_system_key is False
    assert entries[1].pct == 1.2


def test_admin_usage_response_has_youtube_field():
    from app.schemas.admin import AdminUsageResponse

    assert "youtube" in AdminUsageResponse.model_fields
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_admin_usage_api.py -v`
Expected: 신규 2개 FAIL

- [ ] **Step 3: 구현**

`app/schemas/admin.py` — AdminUsageResponse 위에 추가:

```python
class YtQuotaEntry(BaseModel):
    key_fp: str
    units: int
    pct: float                 # daily_quota 대비 백분율(소수 1자리)
    is_system_key: bool


class YtQuotaStatus(BaseModel):
    usage_date: date           # PT 기준 오늘
    daily_quota: int
    entries: list[YtQuotaEntry]
```

(파일 상단 `from datetime import datetime`에 `date` 추가.)
`AdminUsageResponse`에 필드 추가:

```python
    youtube: Optional[YtQuotaStatus] = None   # D-2: 당일(PT) 키별 YouTube 쿼터
```

`app/routers/admin.py` — 순수 헬퍼(모듈 레벨, 테스트 대상):

```python
def build_yt_quota_entries(
    rows: list[tuple[str, int]], daily_quota: int, system_fp: str
) -> list[YtQuotaEntry]:
    """(key_fp, units) 행 → 엔트리. 시스템 키 우선 정렬, pct는 소수 1자리."""
    entries = [
        YtQuotaEntry(
            key_fp=fp,
            units=units,
            pct=round(units * 100.0 / daily_quota, 1) if daily_quota > 0 else 0.0,
            is_system_key=(fp == system_fp),
        )
        for fp, units in rows
    ]
    entries.sort(key=lambda e: (not e.is_system_key, -e.units))
    return entries
```

`usage_summary` 반환 직전에 youtube 섹션 구성:

```python
    # D-2: 당일(PT) YouTube 쿼터 현황
    from app.services.global_settings import get_system_youtube_key, get_youtube_daily_quota
    from app.services.yt_quota_service import key_fingerprint, pt_today
    from app.models.control.yt_quota_usage import YtQuotaUsage

    today_pt = pt_today()
    yt_rows = (
        await session.execute(
            select(YtQuotaUsage.key_fp, YtQuotaUsage.units).where(
                YtQuotaUsage.usage_date == today_pt
            )
        )
    ).all()
    daily_quota = await get_youtube_daily_quota(session)
    system_key = await get_system_youtube_key()
    system_fp = key_fingerprint(system_key) if system_key else ""
    youtube = YtQuotaStatus(
        usage_date=today_pt,
        daily_quota=daily_quota,
        entries=build_yt_quota_entries([(r[0], r[1]) for r in yt_rows], daily_quota, system_fp),
    )
```

`AdminUsageResponse(..., youtube=youtube)`로 반환에 포함. import 정리는 파일 상단
스타일에 맞춰 상단으로 이동(스키마 `YtQuotaEntry`, `YtQuotaStatus` 포함).

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_admin_usage_api.py -v`
Expected: 전부 passed

- [ ] **Step 5: Commit**

```bash
git add app/schemas/admin.py app/routers/admin.py tests/test_admin_usage_api.py
git commit -m "feat: 관리자 usage 응답 youtube 섹션 — 당일(PT) 키별 사용량·한도·시스템키 표시"
```

---

### Task 8: ensure_schema force 파라미터

**Files:**
- Modify: `app/services/db_engine.py:124-132`
- Test: `tests/test_schema_migrator.py` (신규 — force 검증 포함)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_schema_migrator.py`:

```python
"""ensure_schema force + migrate_all_schemas 리포트 (스펙 §2)."""

from types import SimpleNamespace

import pytest

from app.services.db_engine import DataPlaneEngineManager, DBNotConfiguredError

GROUP = SimpleNamespace(group_id=1, slug="g1", schema_name="s1")


class Sentinel(Exception):
    pass


@pytest.fixture
def dpm(monkeypatch):
    m = DataPlaneEngineManager()

    async def fake_cfg(group):
        return SimpleNamespace(server_signature=lambda: "srv1")

    async def boom(cfg):
        raise Sentinel("DDL 경로 진입")

    monkeypatch.setattr(m, "_cfg", fake_cfg)
    monkeypatch.setattr(m, "_shared_engine", boom)
    return m


async def test_ensure_schema_cached_returns_early(dpm):
    dpm._initialized.add(("srv1", "s1"))
    await dpm.ensure_schema(GROUP)  # 캐시 히트 — DDL 경로 진입 안 함


async def test_ensure_schema_force_bypasses_cache(dpm):
    dpm._initialized.add(("srv1", "s1"))
    with pytest.raises(Sentinel):
        await dpm.ensure_schema(GROUP, force=True)  # 캐시 우회 — DDL 경로 진입
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_schema_migrator.py -v`
Expected: FAIL — `unexpected keyword argument 'force'`

- [ ] **Step 3: 구현** — `app/services/db_engine.py:124`

시그니처와 조기 반환 2곳 수정:

```python
    async def ensure_schema(self, group: GroupRef, *, force: bool = False) -> None:
        """그룹 스키마와 데이터 평면 테이블을 멱등 생성한다.

        force=True는 프로세스 캐시를 우회해 DDL을 재실행한다(마이그레이터용).
        락은 유지 — 동시 실행 안전. 기존 호출부는 전부 기본값이라 동작 무변경.
        """
        cfg = await self._cfg(group)
        key = (cfg.server_signature(), group.schema_name)
        if not force and key in self._initialized:
            return
        async with self._ensure_lock(key):
            if not force and key in self._initialized:
                return
```

(이후 본문 무변경 — 성공 시 `self._initialized.add(key)`도 기존 그대로.)

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_schema_migrator.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/db_engine.py tests/test_schema_migrator.py
git commit -m "feat: ensure_schema(force=) — 프로세스 캐시 우회 재실행(락 유지), 기존 호출 무변경"
```

---

### Task 9: schema_migrator 서비스

**Files:**
- Create: `app/services/schema_migrator.py`
- Test: `tests/test_schema_migrator.py` (확장)

- [ ] **Step 1: 실패하는 테스트 추가** (`tests/test_schema_migrator.py`에 append)

```python
async def test_migrate_all_schemas_mixed_report(monkeypatch):
    from app.services import schema_migrator as sm

    groups = [
        SimpleNamespace(group_id=1, slug="ok1", schema_name="s1"),
        SimpleNamespace(group_id=2, slug="nodb", schema_name="s2"),
        SimpleNamespace(group_id=3, slug="boom", schema_name="s3"),
        SimpleNamespace(group_id=4, slug="ok2", schema_name="s4"),
    ]

    async def fake_all_groups():
        return groups

    async def fake_ensure(group, *, force=False):
        assert force is True
        if group.slug == "nodb":
            raise DBNotConfiguredError("no db")
        if group.slug == "boom":
            raise RuntimeError("ALTER 실패")

    monkeypatch.setattr(sm, "_all_groups", fake_all_groups)
    monkeypatch.setattr(sm.dpm, "ensure_schema", fake_ensure)

    results = await sm.migrate_all_schemas()
    by_slug = {r.slug: r for r in results}
    assert len(results) == 4  # 중간 실패에도 전 그룹 순회 (그룹 단위 격리)
    assert by_slug["ok1"].status == "ok" and by_slug["ok1"].error is None
    assert by_slug["nodb"].status == "skipped"
    assert by_slug["boom"].status == "failed" and "ALTER 실패" in by_slug["boom"].error
    assert by_slug["ok2"].status == "ok"
    assert all(r.duration_ms >= 0 for r in results)
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_schema_migrator.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 구현**

`app/services/schema_migrator.py`:

```python
"""전 스키마 순회 마이그레이션 (스펙 D-2 §2).

lazy ensure_schema를 선제·가시적으로 전 그룹에 적용한다. 순차 실행 —
수십 그룹 규모에서 충분히 빠르고 DDL 동시 실행 부하·락 경합을 피한다.
비활성 그룹도 포함(스키마는 데이터 자산). 그룹 단위 격리: 실패해도 계속.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import select

from app.control_db import get_sessionmaker
from app.models.control.group import Group
from app.services.db_engine import DBNotConfiguredError
from app.services.db_engine import data_plane_engine_manager as dpm


@dataclass
class GroupMigrationResult:
    group_id: int
    slug: str
    schema_name: str
    status: str            # 'ok' | 'failed' | 'skipped'(DB 미설정)
    error: str | None
    duration_ms: int


async def _all_groups() -> list[Group]:
    async with get_sessionmaker()() as session:
        return list(
            (await session.execute(select(Group).order_by(Group.group_id))).scalars().all()
        )


async def migrate_all_schemas() -> list[GroupMigrationResult]:
    results: list[GroupMigrationResult] = []
    for group in await _all_groups():
        t0 = time.monotonic()
        status, error = "ok", None
        try:
            await dpm.ensure_schema(group, force=True)
        except DBNotConfiguredError:
            status = "skipped"
        except Exception as e:  # noqa: BLE001 — 그룹 단위 격리
            status, error = "failed", str(e)
        results.append(
            GroupMigrationResult(
                group_id=group.group_id,
                slug=group.slug,
                schema_name=group.schema_name,
                status=status,
                error=error,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        )
    return results


def summarize(results: list[GroupMigrationResult]) -> dict[str, int]:
    return {
        "ok": sum(1 for r in results if r.status == "ok"),
        "failed": sum(1 for r in results if r.status == "failed"),
        "skipped": sum(1 for r in results if r.status == "skipped"),
    }
```

(주의: `dpm` 모듈 별칭 임포트 — 테스트가 `sm.dpm.ensure_schema`를 monkeypatch.)

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_schema_migrator.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/schema_migrator.py tests/test_schema_migrator.py
git commit -m "feat: schema_migrator — 전 그룹 순차 ensure_schema(force) + ok/failed/skipped 리포트"
```

---

### Task 10: 부팅 백그라운드 실행 + 관리자 API

**Files:**
- Modify: `app/main.py:42-76` (lifespan)
- Modify: `app/routers/admin.py` (POST /migrate-schemas), `app/schemas/admin.py`
- Test: `tests/test_schema_migrator.py` (확장)

- [ ] **Step 1: 실패하는 테스트 추가** (`tests/test_schema_migrator.py`에 append)

```python
def test_migrate_schemas_route_registered():
    from app.main import app

    assert "/api/admin/migrate-schemas" in {r.path for r in app.routes}


async def test_migrate_schemas_endpoint_returns_report(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers import admin as admin_router
    from app.routers.auth import CurrentUser, require_user
    from app.services.auth_service import set_users_exist
    from app.services.schema_migrator import GroupMigrationResult

    set_users_exist(True)
    try:
        async def _a():
            return CurrentUser(user_id=1, email="a@x.com", display_name="A", role="admin")

        app.dependency_overrides[require_user] = _a

        async def fake_migrate():
            return [
                GroupMigrationResult(1, "g1", "s1", "ok", None, 12),
                GroupMigrationResult(2, "g2", "s2", "failed", "boom", 5),
            ]

        monkeypatch.setattr(admin_router, "migrate_all_schemas", fake_migrate)
        c = TestClient(app, raise_server_exceptions=False)
        resp = c.post("/api/admin/migrate-schemas")
        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"] == {"ok": 1, "failed": 1, "skipped": 0}
        assert body["results"][1]["error"] == "boom"
    finally:
        set_users_exist(False)
        app.dependency_overrides.clear()


def test_migrate_schemas_non_admin_403():
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers.auth import CurrentUser, require_user
    from app.services.auth_service import set_users_exist

    set_users_exist(True)
    try:
        async def _u():
            return CurrentUser(user_id=2, email="u@x.com", display_name="U", role="user")

        app.dependency_overrides[require_user] = _u
        c = TestClient(app, raise_server_exceptions=False)
        assert c.post("/api/admin/migrate-schemas").status_code == 403
    finally:
        set_users_exist(False)
        app.dependency_overrides.clear()
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_schema_migrator.py -v`
Expected: 신규 3개 FAIL

- [ ] **Step 3: 구현**

`app/schemas/admin.py`:

```python
class MigrationResultOut(BaseModel):
    group_id: int
    slug: str
    schema_name: str
    status: str            # 'ok' | 'failed' | 'skipped'
    error: Optional[str] = None
    duration_ms: int


class MigrateSchemasResponse(BaseModel):
    results: list[MigrationResultOut]
    summary: dict[str, int]   # {'ok': n, 'failed': n, 'skipped': n}
```

`app/routers/admin.py` — import에 `from app.services.schema_migrator import
migrate_all_schemas, summarize` 추가(테스트가 `admin_router.migrate_all_schemas`를
monkeypatch하므로 이 형태 유지), 엔드포인트:

```python
@router.post("/migrate-schemas", response_model=MigrateSchemasResponse)
async def migrate_schemas() -> MigrateSchemasResponse:
    """전 그룹 스키마 순회 마이그레이션 — 동기 실행, 그룹별 리포트 반환."""
    results = await migrate_all_schemas()
    return MigrateSchemasResponse(
        results=[MigrationResultOut(**vars(r)) for r in results],
        summary=summarize(results),
    )
```

(admin 라우터의 기존 admin 전용 의존성이 라우터 레벨에 걸려 있는지 확인 —
`test_non_admin_forbidden` 패턴이 통과하는 구조 그대로 따른다.)

`app/main.py` lifespan — `apply_pending_analysis_schedule()` 다음, tg_task 생성 전에:

```python
    from app.services.schema_migrator import migrate_all_schemas, summarize

    async def _boot_migrate() -> None:
        try:
            results = await migrate_all_schemas()
            s = summarize(results)
            print(f"[startup] 전 스키마 마이그레이션: ok={s['ok']} failed={s['failed']} skipped={s['skipped']}")
            for r in results:
                if r.status == "failed":
                    print(f"[startup]   {r.slug}({r.schema_name}) 실패: {r.error}")
        except Exception as e:  # noqa: BLE001 — 부팅을 절대 막지 않는다
            print(f"[startup] 전 스키마 마이그레이션 실패(기동 계속): {e}")

    mig_task = asyncio.create_task(_boot_migrate())
```

finally 블록에 tg_task와 동일 패턴으로 추가:

```python
        mig_task.cancel()
        with suppress(asyncio.CancelledError):
            await mig_task
```

- [ ] **Step 4: 통과 확인 + 전체 회귀**

Run: `python -m pytest tests/ -q`
Expected: 전부 passed

- [ ] **Step 5: Commit**

```bash
git add app/main.py app/routers/admin.py app/schemas/admin.py tests/test_schema_migrator.py
git commit -m "feat: 부팅 백그라운드 전 스키마 마이그레이션 + POST /api/admin/migrate-schemas 리포트"
```

---

### Task 11: 프런트 — 쿼터 카드 + 마이그레이션 버튼

**Files:**
- Modify: `frontend/src/api/admin.ts`
- Modify: `frontend/src/pages/Admin.tsx`

- [ ] **Step 1: API 타입·엔드포인트 추가** — `frontend/src/api/admin.ts`

`AdminUsageResponse` 타입에 필드 추가:

```typescript
export interface YtQuotaEntry {
  key_fp: string
  units: number
  pct: number
  is_system_key: boolean
}

export interface YtQuotaStatus {
  usage_date: string
  daily_quota: number
  entries: YtQuotaEntry[]
}

// AdminUsageResponse에 추가:
//   youtube: YtQuotaStatus | null

export interface MigrationResultOut {
  group_id: number
  slug: string
  schema_name: string
  status: 'ok' | 'failed' | 'skipped'
  error: string | null
  duration_ms: number
}

export interface MigrateSchemasResponse {
  results: MigrationResultOut[]
  summary: { ok: number; failed: number; skipped: number }
}
```

adminApi 객체에 추가:

```typescript
  migrateSchemas: () =>
    rootApi.post<MigrateSchemasResponse>('/admin/migrate-schemas', {}),
```

(`rootApi.post`의 실제 시그니처는 `frontend/src/api/http.ts`에서 확인해 맞춘다 —
바디 없는 POST면 두 번째 인자 생략.)

- [ ] **Step 2: Admin.tsx — 사용량 탭에 YouTube 쿼터 카드**

기존 usage 렌더 영역(466행 window 셀렉트 부근)의 AI 사용량 테이블 위에 카드 추가.
기존 Tailwind 클래스 스타일을 그대로 따른다:

```tsx
{usage?.youtube && (
  <div className="bg-white rounded-xl border border-gray-200 p-4 space-y-2">
    <h3 className="text-sm font-semibold text-gray-700">
      YouTube 쿼터 (PT {usage.youtube.usage_date} · 한도 {usage.youtube.daily_quota.toLocaleString()})
    </h3>
    {usage.youtube.entries.length === 0 && (
      <p className="text-sm text-gray-500">오늘 기록된 호출이 없습니다.</p>
    )}
    {usage.youtube.entries.map((e) => (
      <div key={e.key_fp} className="flex items-center justify-between text-sm">
        <span className="font-mono text-gray-600">
          {e.key_fp} {e.is_system_key && <span className="ml-1 text-xs text-blue-600">시스템 키</span>}
        </span>
        <span className={
          e.pct >= 100 ? 'text-red-600 font-semibold'
          : e.pct >= 80 ? 'text-amber-600 font-semibold'
          : 'text-gray-700'
        }>
          {e.units.toLocaleString()} 유닛 ({e.pct}%)
        </span>
      </div>
    ))}
  </div>
)}
```

- [ ] **Step 3: Admin.tsx — 마이그레이션 실행 섹션**

상태 추가:

```tsx
const [migrating, setMigrating] = useState(false)
const [migration, setMigration] = useState<MigrateSchemasResponse | null>(null)
const [migrationError, setMigrationError] = useState<string | null>(null)

const runMigration = async () => {
  if (migrating) return          // 재진입 가드
  setMigrating(true)
  setMigrationError(null)
  try {
    setMigration(await adminApi.migrateSchemas())
  } catch (e) {
    setMigrationError(e instanceof Error ? e.message : '실행 실패')
  } finally {
    setMigrating(false)
  }
}
```

관리자 페이지 하단(전역 설정 섹션 아래)에 렌더:

```tsx
<section className="space-y-3">
  <h2 className="text-lg font-semibold">시스템 도구</h2>
  <button
    onClick={runMigration}
    disabled={migrating}
    className="px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-medium disabled:opacity-50"
  >
    {migrating ? '실행 중…' : '전 스키마 마이그레이션 실행'}
  </button>
  {migrationError && (
    <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{migrationError}</p>
  )}
  {migration && (
    <div className="space-y-2">
      <p className="text-sm text-gray-600">
        성공 {migration.summary.ok} · 실패 {migration.summary.failed} · 스킵 {migration.summary.skipped}
      </p>
      <table className="w-full text-sm">
        <thead><tr className="text-left text-gray-500">
          <th className="px-3 py-1">그룹</th><th className="px-3 py-1">스키마</th>
          <th className="px-3 py-1">상태</th><th className="px-3 py-1 text-right">소요(ms)</th>
        </tr></thead>
        <tbody>
          {migration.results.map((r) => (
            <tr key={r.group_id} className="border-t border-gray-100">
              <td className="px-3 py-1">{r.slug}</td>
              <td className="px-3 py-1 font-mono text-xs">{r.schema_name}</td>
              <td className={`px-3 py-1 ${r.status === 'failed' ? 'text-red-600' : r.status === 'skipped' ? 'text-gray-400' : 'text-green-600'}`}>
                {r.status}{r.error ? ` — ${r.error}` : ''}
              </td>
              <td className="px-3 py-1 text-right">{r.duration_ms}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )}
</section>
```

(정확한 삽입 위치·마크업은 Admin.tsx의 실제 섹션 구조를 따르되, 기능 요건은
"버튼 + 재진입 가드 + summary + 그룹별 결과 테이블 + 실패 에러 표시"로 고정.)

- [ ] **Step 4: 빌드·기존 프런트 테스트 확인**

Run: `cd frontend && npx tsc --noEmit && npm run build && npx vitest run`
Expected: 타입 에러 0, 빌드 성공, 기존 vitest 전부 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/admin.ts frontend/src/pages/Admin.tsx
git commit -m "feat: Admin — YouTube 쿼터 카드(80/100% 경고색) + 전 스키마 마이그레이션 버튼·리포트"
```

---

### Task 12: 전체 검증 + 문서 갱신

**Files:**
- Modify: `docs/superpowers/specs/2026-07-03-multi-tenant-design.md:384` (§7 D행)

- [ ] **Step 1: 전체 테스트**

Run: `python -m pytest tests/ -q && cd frontend && npx vitest run && npm run build`
Expected: 백엔드 전부 passed(기존 306 + 신규 ~20), 프런트 vitest passed, 빌드 클린

- [ ] **Step 2: 상위 스펙 §7 D행 갱신**

`docs/superpowers/specs/2026-07-03-multi-tenant-design.md` 384행 D행의
`(D-1 구현 완료 … D-2 잔여: YouTube 쿼터 카운터·전 스키마 마이그레이션 도구)`를
`(D-1 구현 완료 2026-07-11 — 공용 봇 딥링크 연결·온보딩 체크리스트. D-2 구현 완료 2026-XX-XX — YouTube 쿼터 카운터·전 스키마 마이그레이션 도구, 설계 2026-07-13-phase-d2-yt-quota-schema-migrator-design.md)`로 교체(실제 날짜, 표 구조 유지).

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-07-03-multi-tenant-design.md
git commit -m "docs: Phase D-2 구현 반영 — YouTube 쿼터 카운터·전 스키마 마이그레이션 완료 표기"
```

---

## 실 DB E2E 체크리스트 (구현·머지 후 — 별도 세션, 테스트 DB 100.115.13.102)

> ⚠️ `postgres-ytdb` MCP는 프로덕션 — 읽기 전용만. 쓰기는 앱 자체 엔진(`.env`의
> `CONTROL_DATABASE_URL`)으로. `.venv_e2e` + `PYTHONPATH=.` 필수(메모리 §61-65).

1. [ ] 부팅 시 `app.yt_quota_usage` 테이블 생성 확인(멱등 재부팅 포함).
2. [ ] 부팅 백그라운드 마이그레이션 stdout 로그 — 전 그룹 ok/skipped 리포트 확인.
3. [ ] `POST /api/admin/migrate-schemas` 실HTTP — 그룹별 리포트 응답, 재실행 멱등.
4. [ ] 실 폴링 1틱(또는 채널 등록 1회) 후 `yt_quota_usage`에 (오늘PT, 키지문) 행 생성·재호출 시 units 누적 확인.
5. [ ] `GET /api/admin/usage` youtube 섹션 — 시스템 키 is_system_key=true, pct 계산.
6. [ ] `youtube_daily_quota`를 현재 사용량 이하로 낮춰 하드 게이트 실동작: 중앙 폴링 skip 로그 + 시스템 키 폴백 그룹의 채널 추가 400 + 그룹 자체 키 그룹은 정상.
7. [ ] 한도 원복 후 게이트 해제(다음 틱 정상 폴링) 확인.

## Self-Review 결과 (작성 시 수행)

- 스펙 §1.1(테이블)=Task 1, §1.2(recorder·배선)=Task 2·3·6, §1.3(전역 한도)=Task 4,
  §1.4(게이트·전환로그·폴백거부)=Task 5·6, §1.5(관리자 가시성)=Task 7·11,
  §2.1(migrator)=Task 9, §2.2(force)=Task 8, §2.3(부팅)=Task 10, §2.4(API+UI)=Task 10·11,
  §3(테스트)=각 태스크 Step 1, §4(호환성)=기본값·no-op 설계로 충족. 갭 없음.
- 타입 일관성: `GroupMigrationResult` 필드 순서(Task 9 정의 ↔ Task 10 테스트 위치 인자),
  `yq` 모듈 별칭(Task 5 central_poller ↔ 테스트 monkeypatch), `make_recorder` 시그니처
  (Task 2 정의 ↔ Task 3·6 사용) 상호 확인 완료.
- 주의: Task 6 Step 1의 라우터 테스트는 실제 라우트 경로·의존성 체인에 맞춰 조정
  필요(검증 대상 동작은 고정) — 구현자가 라우트 데코레이터를 먼저 읽을 것.
