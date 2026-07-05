# B-0b 중앙 채널 레지스트리 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** YouTube 폴링을 채널당 1회로 중앙화하고(시스템 전역 키), 신규 영상을 구독 그룹 스키마에 푸시 팬아웃한다.

**Architecture:** 제어 평면(app 스키마)에 `channel_registry`/`channel_subscriptions`(역방향 매핑, 유효값 비정규화)/`global_settings`(최소 골격) 3개 테이블 추가. 새 모듈 `central_poller.py`가 due 채널을 시스템 키로 1회 조회 후 구독 그룹들에 팬아웃하며, 기존 `MonitorService.process_channel`을 API 조회/그룹 삽입 두 함수로 분해해 필터 로직(중복·`deleted_videos`·window)을 이중화 없이 재사용한다. 그룹 스코프 호출(수동 폴링·통계 갱신·채널 등록)은 그룹 키→시스템 키 폴백.

**Tech Stack:** FastAPI + SQLAlchemy async + APScheduler + pytest (기존 스택, 신규 의존성 없음)

**승인된 스펙:** `docs/superpowers/specs/2026-07-05-b0b-channel-registry-design.md`

**환경 주의 (메모리 기록):** repo의 `.venv`는 다른 머신의 깨진 venv — 건드리지 말 것. 로컬 테스트는 `.venv_e2e/bin/python -m pytest` 사용 (없으면 `python3 -m venv .venv_e2e && .venv_e2e/bin/pip install -r requirements.txt "fastapi<0.130" "starlette<1.0"`). 아래 `pytest` 표기는 모두 `.venv_e2e/bin/python -m pytest`를 의미한다. 커밋은 로컬만 — **push는 배포 트리거이므로 절대 하지 않는다.**

---

## 파일 구조

| 파일 | 책임 |
|------|------|
| Create `app/models/control/channel_registry.py` | ChannelRegistry ORM (전역 채널 원장) |
| Create `app/models/control/channel_subscription.py` | ChannelSubscription ORM (역방향 매핑, 유효값 비정규화) |
| Create `app/models/control/global_setting.py` | GlobalSetting ORM (키-값, 시크릿은 Fernet) |
| Create `app/services/global_settings.py` | 전역 설정 접근자 + `resolve_youtube_key` 폴백 + 부트스트랩 시드 |
| Create `app/services/channel_registry_service.py` | 구독 동기화(subscribe/unsubscribe/resync/backfill), due 산출, recount |
| Create `app/services/central_poller.py` | `run_central_poll_once` — 중앙 폴링 + 푸시 팬아웃 |
| Modify `app/control_db.py` | ensure_control_schema에 새 모델 임포트 |
| Modify `app/services/monitor_service.py` | process_channel 분해(fetch/insert), 그룹 스코프 키 폴백 |
| Modify `app/services/scheduler.py` | JOB_MASTER_POLL → run_central_poll_once |
| Modify `app/routers/channels.py` | 채널 추가/수정/삭제 동기화 훅 + 키 폴백 |
| Modify `app/routers/settings.py` | polling 저장 시 그룹 구독 재동기화 |
| Modify `app/routers/groups.py` | 그룹 활성 토글/삭제 시 구독 정리 |
| Modify `app/routers/admin.py`, `app/schemas/admin.py` | 전역 설정 조회/수정 API |
| Modify `app/main.py` | lifespan에 전역 설정 시드 + 레지스트리 백필 |
| Test `tests/test_global_settings.py`, `tests/test_channel_registry_service.py`, `tests/test_central_poller.py`, `tests/test_admin_api.py`(확장) | 위 모듈 단위/통합 테스트 |

---

### Task 1: 제어 평면 모델 3개 + 스키마 등록

**Files:**
- Create: `app/models/control/channel_registry.py`
- Create: `app/models/control/channel_subscription.py`
- Create: `app/models/control/global_setting.py`
- Modify: `app/control_db.py` (ensure_control_schema의 모델 임포트 블록)
- Test: `tests/test_control_models.py` (기존 파일에 테스트 추가)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_control_models.py` 끝에 추가:

```python
def test_b0b_tables_registered():
    """B-0b 테이블 3개가 Base.metadata에 app 스키마로 등록된다."""
    from app.models.control.channel_registry import ChannelRegistry
    from app.models.control.channel_subscription import ChannelSubscription
    from app.models.control.global_setting import GlobalSetting

    assert ChannelRegistry.__table__.schema == "app"
    assert ChannelSubscription.__table__.schema == "app"
    assert GlobalSetting.__table__.schema == "app"

    # 비정규화 컬럼은 NOT NULL (스펙 §2 — 동기화 시점에 유효값 해석 완료)
    sub = ChannelSubscription.__table__
    assert sub.c.poll_interval_min.nullable is False
    assert sub.c.window_hours.nullable is False
    # 복합 PK
    assert {c.name for c in sub.primary_key.columns} == {"channel_id", "group_id"}
    # 그룹 삭제 캐스케이드 백스톱
    fk_group = next(fk for fk in sub.c.group_id.foreign_keys)
    assert fk_group.ondelete == "CASCADE"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_control_models.py::test_b0b_tables_registered -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.control.channel_registry'`

- [ ] **Step 3: 모델 구현**

`app/models/control/channel_registry.py`:

```python
"""app.channel_registry — 전역 채널 레지스트리 (스펙 B-0b).

중앙 폴러가 채널당 1회 폴링하기 위한 신뢰원. 구독 관계는
app.channel_subscriptions가 보관하며 subscriber_groups는 참고용 캐시
(동기화 지점에서 COUNT 재계산 — 증감 누적 드리프트 방지).
구독 0이어도 행은 유지한다(이력 보존, due 쿼리 join에서 자연 제외).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class ChannelRegistry(Base):
    __tablename__ = "channel_registry"
    __table_args__ = {"schema": APP_SCHEMA}

    channel_id: Mapped[str] = mapped_column(Text, primary_key=True)  # YouTube 채널 ID
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    upload_playlist_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_video_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    subscriber_groups: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

`app/models/control/channel_subscription.py`:

```python
"""app.channel_subscriptions — channel_id → 구독 그룹 역방향 매핑 (스펙 B-0b).

스키마-per-그룹 구조에서 "이 채널을 누가 구독하나"를 그룹 스키마 스캔 없이
답하는 유일한 수단. poll_interval_min/window_hours는 동기화 시점에 해석
완료된 유효값(NULL 없음 — 그룹 기본값 해석은 동기화가 담당).
"""

from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class ChannelSubscription(Base):
    __tablename__ = "channel_subscriptions"
    __table_args__ = (
        Index("channel_subscriptions_group", "group_id"),
        {"schema": APP_SCHEMA},
    )

    channel_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey(f"{APP_SCHEMA}.channel_registry.channel_id"),
        primary_key=True,
    )
    group_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{APP_SCHEMA}.groups.group_id", ondelete="CASCADE"),
        primary_key=True,
    )
    poll_interval_min: Mapped[int] = mapped_column(Integer, nullable=False)
    window_hours: Mapped[int] = mapped_column(Integer, nullable=False)
```

`app/models/control/global_setting.py`:

```python
"""app.global_settings — 전역 설정 최소 골격 (스펙 B-0b, Phase C에서 항목 추가).

그룹별 app.settings와 동일한 평문/암호문 이원 저장 패턴(value/value_enc).
B-0b 시드 키: youtube_api_key(시크릿), central_poll_floor_min.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, LargeBinary, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class GlobalSetting(Base):
    __tablename__ = "global_settings"
    __table_args__ = {"schema": APP_SCHEMA}

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)       # 평문
    value_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)  # 암호문
    is_secret: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
```

`app/control_db.py`의 `ensure_control_schema` 모델 임포트 블록에 3개 추가:

```python
    from app.models.control import (  # noqa: F401
        analysis_cache,
        analysis_delivery,
        channel_registry,
        channel_subscription,
        global_setting,
        group,
        invitation,
        plan,
        prompt_preset,
        setting,
        user,
    )
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_control_models.py -v`
Expected: 전체 PASS

- [ ] **Step 5: 커밋**

```bash
git add app/models/control/channel_registry.py app/models/control/channel_subscription.py app/models/control/global_setting.py app/control_db.py tests/test_control_models.py
git commit -m "feat: channel_registry/channel_subscriptions/global_settings 모델 (B-0b §2)"
```

---

### Task 2: 전역 설정 서비스 (get/set + 암호화 + 키 해석 폴백)

**Files:**
- Create: `app/services/global_settings.py`
- Test: `tests/test_global_settings.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_global_settings.py`:

```python
"""전역 설정 접근자/폴백 검증. SQL은 FakeSession으로 대체(실 SQL은 E2E)."""

from types import SimpleNamespace

import pytest

from app.services import global_settings as gs


class FakeResult:
    def __init__(self, row=None):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class FakeSession:
    def __init__(self, results):
        self._results = list(results)
        self.executed = []

    async def execute(self, stmt):
        self.executed.append(stmt)
        return self._results.pop(0)

    async def commit(self):
        pass


def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(Fernet.generate_key())


async def test_get_global_plain():
    row = SimpleNamespace(key="central_poll_floor_min", value="10", value_enc=None, is_secret=False)
    out = await gs.get_global(FakeSession([FakeResult(row)]), "central_poll_floor_min")
    assert out == "10"


async def test_get_global_missing_returns_none():
    out = await gs.get_global(FakeSession([FakeResult(None)]), "youtube_api_key")
    assert out is None


async def test_get_global_secret_decrypts(monkeypatch):
    f = _fernet()
    monkeypatch.setattr(gs, "_get_fernet", lambda: f)
    row = SimpleNamespace(
        key="youtube_api_key", value=None,
        value_enc=f.encrypt(b"AIza-secret"), is_secret=True,
    )
    out = await gs.get_global(FakeSession([FakeResult(row)]), "youtube_api_key")
    assert out == "AIza-secret"


async def test_get_central_poll_floor_min_default_and_clamp():
    # 행 없음 → 기본 10
    assert await gs.get_central_poll_floor_min(FakeSession([FakeResult(None)])) == 10
    # 비정상 값(0 이하/비숫자) → 기본 10
    bad = SimpleNamespace(key="central_poll_floor_min", value="abc", value_enc=None, is_secret=False)
    assert await gs.get_central_poll_floor_min(FakeSession([FakeResult(bad)])) == 10


async def test_resolve_youtube_key_prefers_group_key(monkeypatch):
    async def fake_get_polling(group_id):
        return SimpleNamespace(youtube_api_key="group-key")

    monkeypatch.setattr(
        gs, "get_settings_manager",
        lambda: SimpleNamespace(get_polling=fake_get_polling),
    )
    assert await gs.resolve_youtube_key(1) == "group-key"


async def test_resolve_youtube_key_falls_back_to_system(monkeypatch):
    async def fake_get_polling(group_id):
        return SimpleNamespace(youtube_api_key="")

    monkeypatch.setattr(
        gs, "get_settings_manager",
        lambda: SimpleNamespace(get_polling=fake_get_polling),
    )

    async def fake_system_key():
        return "system-key"

    monkeypatch.setattr(gs, "get_system_youtube_key", fake_system_key)
    assert await gs.resolve_youtube_key(1) == "system-key"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_global_settings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.global_settings'`

- [ ] **Step 3: 구현** — `app/services/global_settings.py`:

```python
"""전역 설정 접근자 (스펙 B-0b §5).

- get/set: app.global_settings 키-값. 시크릿 키는 FERNET_KEY로 암호화.
- resolve_youtube_key: 그룹 스코프 호출용 폴백 — 그룹 polling 키 우선, 없으면 시스템 키.
- 중앙 폴링은 폴백 없이 항상 시스템 키(get_system_youtube_key)를 쓴다.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.control_db import get_sessionmaker
from app.models.control.global_setting import GlobalSetting
from app.services.settings_manager import (
    SettingsSecretError,
    _fernet_from_key,
    get_settings_manager,
)

GLOBAL_YOUTUBE_API_KEY = "youtube_api_key"
GLOBAL_CENTRAL_POLL_FLOOR_MIN = "central_poll_floor_min"
DEFAULT_CENTRAL_POLL_FLOOR_MIN = 10

SECRET_KEYS = frozenset({GLOBAL_YOUTUBE_API_KEY})


def _get_fernet():
    return _fernet_from_key(app_settings.FERNET_KEY)


async def get_global(session: AsyncSession, key: str) -> Optional[str]:
    row = (
        await session.execute(select(GlobalSetting).where(GlobalSetting.key == key))
    ).scalar_one_or_none()
    if row is None:
        return None
    if row.is_secret:
        if row.value_enc is None:
            return None
        fernet = _get_fernet()
        if fernet is None:
            return None  # 키 없이는 복호 불가 — 미설정과 동일 취급
        return fernet.decrypt(row.value_enc).decode("utf-8")
    return row.value


async def set_global(session: AsyncSession, key: str, value: str) -> None:
    is_secret = key in SECRET_KEYS
    if is_secret:
        fernet = _get_fernet()
        if fernet is None:
            raise SettingsSecretError("시크릿을 저장하려면 FERNET_KEY가 필요합니다.")
        values = {"key": key, "value": None, "value_enc": fernet.encrypt(value.encode("utf-8")), "is_secret": True}
    else:
        values = {"key": key, "value": value, "value_enc": None, "is_secret": False}
    stmt = pg_insert(GlobalSetting).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[GlobalSetting.key],
        set_={"value": stmt.excluded.value, "value_enc": stmt.excluded.value_enc, "is_secret": stmt.excluded.is_secret},
    )
    await session.execute(stmt)


async def get_central_poll_floor_min(session: AsyncSession) -> int:
    raw = await get_global(session, GLOBAL_CENTRAL_POLL_FLOOR_MIN)
    try:
        v = int(raw) if raw is not None else DEFAULT_CENTRAL_POLL_FLOOR_MIN
    except (TypeError, ValueError):
        return DEFAULT_CENTRAL_POLL_FLOOR_MIN
    return v if v > 0 else DEFAULT_CENTRAL_POLL_FLOOR_MIN


async def get_system_youtube_key() -> str:
    """자체 세션으로 시스템 YouTube 키를 읽는다. 미설정이면 ''."""
    async with get_sessionmaker()() as session:
        return (await get_global(session, GLOBAL_YOUTUBE_API_KEY)) or ""


async def resolve_youtube_key(group_id: int) -> str:
    """그룹 스코프 호출용: 그룹 polling 키 우선, 없으면 시스템 키. 둘 다 없으면 ''."""
    polling = await get_settings_manager().get_polling(group_id)
    if polling.youtube_api_key:
        return polling.youtube_api_key
    return await get_system_youtube_key()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_global_settings.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add app/services/global_settings.py tests/test_global_settings.py
git commit -m "feat: 전역 설정 서비스 — get/set(Fernet)·폴링 하한·YouTube 키 폴백 (B-0b §5)"
```

---

### Task 3: 레지스트리·구독 동기화 서비스

**Files:**
- Create: `app/services/channel_registry_service.py`
- Test: `tests/test_channel_registry_service.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_channel_registry_service.py`:

```python
"""due 판정·유효값 계산 등 순수 로직 검증. SQL 실행은 E2E에서."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.channel_registry_service import (
    DueChannel,
    desired_subscription_values,
    filter_due,
)

NOW = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)


def _row(channel_id="UC1", last_polled_at=None, interval=60, window=24):
    return SimpleNamespace(
        channel_id=channel_id,
        upload_playlist_id=f"UU{channel_id[2:]}",
        last_polled_at=last_polled_at,
        interval_min=interval,
        window_hours=window,
    )


def test_never_polled_is_due():
    due = filter_due([_row(last_polled_at=None)], now=NOW, floor_min=10)
    assert [d.channel_id for d in due] == ["UC1"]


def test_not_due_within_interval():
    row = _row(last_polled_at=NOW - timedelta(minutes=30), interval=60)
    assert filter_due([row], now=NOW, floor_min=10) == []


def test_due_after_interval():
    row = _row(last_polled_at=NOW - timedelta(minutes=61), interval=60)
    assert len(filter_due([row], now=NOW, floor_min=10)) == 1


def test_floor_clamps_short_interval():
    # 구독 최솟값 1분이어도 하한 10분 미만이면 due 아님
    row = _row(last_polled_at=NOW - timedelta(minutes=5), interval=1)
    assert filter_due([row], now=NOW, floor_min=10) == []
    row2 = _row(last_polled_at=NOW - timedelta(minutes=11), interval=1)
    assert len(filter_due([row2], now=NOW, floor_min=10)) == 1


def test_naive_last_polled_treated_as_utc():
    row = _row(last_polled_at=(NOW - timedelta(minutes=61)).replace(tzinfo=None), interval=60)
    assert len(filter_due([row], now=NOW, floor_min=10)) == 1


def test_due_channel_carries_max_window():
    row = _row(last_polled_at=None, window=72)
    d = filter_due([row], now=NOW, floor_min=10)[0]
    assert d == DueChannel(
        channel_id="UC1", upload_playlist_id="UU1",
        effective_interval_min=60, fetch_window_hours=72,
    )


def test_desired_subscription_values_resolves_group_default():
    polling = SimpleNamespace(default_channel_interval_min=720, window_hours=24)
    ch_with = SimpleNamespace(channel_id="UC1", poll_interval_min=60, is_active=True)
    ch_default = SimpleNamespace(channel_id="UC2", poll_interval_min=None, is_active=True)
    ch_inactive = SimpleNamespace(channel_id="UC3", poll_interval_min=30, is_active=False)
    out = desired_subscription_values([ch_with, ch_default, ch_inactive], polling)
    # 비활성 채널은 구독 대상 아님, NULL 주기는 그룹 기본값으로 해석
    assert out == {"UC1": (60, 24), "UC2": (720, 24)}
```

- [ ] **Step 2: 실패 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_channel_registry_service.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 구현** — `app/services/channel_registry_service.py`:

```python
"""중앙 채널 레지스트리·구독 동기화 (스펙 B-0b §2·§4).

동기화 원칙: channel_subscriptions에는 해석 완료된 유효값만 저장한다
(채널 주기 NULL → 그룹 default_channel_interval_min). subscriber_groups는
참고용 캐시 — 변경 지점마다 COUNT(*) 재계산.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Sequence

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.control_db import get_sessionmaker
from app.models.control.channel_registry import ChannelRegistry
from app.models.control.channel_subscription import ChannelSubscription
from app.models.control.group import Group
from app.models.pg.channel import Channel
from app.services.db_engine import DBNotConfiguredError, data_plane_engine_manager as dpm
from app.services.settings_manager import get_settings_manager


@dataclass(frozen=True)
class DueChannel:
    channel_id: str
    upload_playlist_id: Optional[str]
    effective_interval_min: int
    fetch_window_hours: int


def desired_subscription_values(channels: Iterable, polling) -> dict[str, tuple[int, int]]:
    """그룹 스키마 channels + 그룹 polling 설정 → {channel_id: (유효 주기, 윈도)}.

    비활성 채널은 제외(중앙 폴링 대상 아님). 순수 함수 — 단위 테스트 대상.
    """
    default_interval = int(polling.default_channel_interval_min or 720)
    window = int(polling.window_hours or 24)
    return {
        ch.channel_id: (int(ch.poll_interval_min or default_interval), window)
        for ch in channels
        if ch.is_active
    }


def filter_due(rows: Sequence, now: datetime, floor_min: int) -> list[DueChannel]:
    """집계 행(interval_min=MIN, window_hours=MAX) → due 채널 목록. 순수 함수."""
    due: list[DueChannel] = []
    for r in rows:
        interval = max(int(r.interval_min), int(floor_min))
        lp = r.last_polled_at
        if lp is not None and lp.tzinfo is None:
            lp = lp.replace(tzinfo=timezone.utc)
        if lp is None or now - lp >= timedelta(minutes=interval):
            due.append(
                DueChannel(
                    channel_id=r.channel_id,
                    upload_playlist_id=r.upload_playlist_id,
                    effective_interval_min=int(r.interval_min),
                    fetch_window_hours=int(r.window_hours),
                )
            )
    return due


async def list_due_channels(
    session: AsyncSession, now: datetime, floor_min: int
) -> list[DueChannel]:
    """구독 있는 채널만 join으로 자연 포함(구독 0 채널 제외 — 스펙 §2)."""
    rows = (
        await session.execute(
            select(
                ChannelRegistry.channel_id,
                ChannelRegistry.upload_playlist_id,
                ChannelRegistry.last_polled_at,
                func.min(ChannelSubscription.poll_interval_min).label("interval_min"),
                func.max(ChannelSubscription.window_hours).label("window_hours"),
            )
            .join(
                ChannelSubscription,
                ChannelSubscription.channel_id == ChannelRegistry.channel_id,
            )
            .group_by(ChannelRegistry.channel_id)
        )
    ).all()
    return filter_due(rows, now=now, floor_min=floor_min)


async def subscriptions_for_channels(
    session: AsyncSession, channel_ids: Sequence[str]
) -> dict[str, list[ChannelSubscription]]:
    """중앙 폴러용: due 채널들의 구독을 한 번에 조회해 channel_id로 묶는다."""
    if not channel_ids:
        return {}
    rows = (
        await session.execute(
            select(ChannelSubscription).where(
                ChannelSubscription.channel_id.in_(list(channel_ids))
            )
        )
    ).scalars()
    out: dict[str, list[ChannelSubscription]] = {}
    for s in rows:
        out.setdefault(s.channel_id, []).append(s)
    return out


async def upsert_registry(
    session: AsyncSession,
    channel_id: str,
    title: Optional[str] = None,
    upload_playlist_id: Optional[str] = None,
) -> None:
    """registry 행 멱등 생성. 기존 행의 메타는 새 값이 있을 때만 갱신."""
    stmt = pg_insert(ChannelRegistry).values(
        channel_id=channel_id, title=title, upload_playlist_id=upload_playlist_id
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[ChannelRegistry.channel_id],
        set_={
            "title": func.coalesce(stmt.excluded.title, ChannelRegistry.title),
            "upload_playlist_id": func.coalesce(
                stmt.excluded.upload_playlist_id, ChannelRegistry.upload_playlist_id
            ),
        },
    )
    await session.execute(stmt)


async def _recount(session: AsyncSession, channel_id: str) -> None:
    count = (
        await session.execute(
            select(func.count())
            .select_from(ChannelSubscription)
            .where(ChannelSubscription.channel_id == channel_id)
        )
    ).scalar_one()
    await session.execute(
        update(ChannelRegistry)
        .where(ChannelRegistry.channel_id == channel_id)
        .values(subscriber_groups=int(count))
    )


async def subscribe(
    session: AsyncSession,
    channel_id: str,
    group_id: int,
    poll_interval_min: int,
    window_hours: int,
) -> None:
    """구독 upsert (registry 행이 이미 있어야 한다 — 호출자가 upsert_registry 선행)."""
    stmt = pg_insert(ChannelSubscription).values(
        channel_id=channel_id,
        group_id=group_id,
        poll_interval_min=poll_interval_min,
        window_hours=window_hours,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[ChannelSubscription.channel_id, ChannelSubscription.group_id],
        set_={
            "poll_interval_min": stmt.excluded.poll_interval_min,
            "window_hours": stmt.excluded.window_hours,
        },
    )
    await session.execute(stmt)
    await _recount(session, channel_id)


async def unsubscribe(session: AsyncSession, channel_id: str, group_id: int) -> None:
    await session.execute(
        delete(ChannelSubscription).where(
            ChannelSubscription.channel_id == channel_id,
            ChannelSubscription.group_id == group_id,
        )
    )
    await _recount(session, channel_id)


async def remove_group_subscriptions(session: AsyncSession, group_id: int) -> None:
    """그룹 비활성/삭제 시: 구독 제거 + 영향받은 채널 재계산."""
    affected = [
        cid
        for (cid,) in (
            await session.execute(
                select(ChannelSubscription.channel_id).where(
                    ChannelSubscription.group_id == group_id
                )
            )
        ).all()
    ]
    await session.execute(
        delete(ChannelSubscription).where(ChannelSubscription.group_id == group_id)
    )
    for cid in affected:
        await _recount(session, cid)


async def mark_polled(
    session: AsyncSession,
    channel_id: str,
    polled_at: datetime,
    last_video_at: Optional[datetime] = None,
) -> None:
    values: dict = {"last_polled_at": polled_at}
    if last_video_at is not None:
        values["last_video_at"] = last_video_at
    await session.execute(
        update(ChannelRegistry)
        .where(ChannelRegistry.channel_id == channel_id)
        .values(**values)
    )


async def resync_group(group: Group) -> None:
    """그룹 스키마 channels → 구독 테이블을 원하는 상태로 수렴시킨다. 멱등.

    사용처: 부팅 백필, polling 설정 변경, 그룹 재활성 (스펙 §4·§6).
    """
    try:
        await dpm.ensure_schema(group)
        engine = await dpm.get_engine_for_group(group)
    except DBNotConfiguredError:
        return  # DB 미설정 그룹은 폴링 대상이 아니므로 구독도 없음
    from app.services.monitor_service import _make_session_factory

    make_session = _make_session_factory(engine, group.schema_name)
    async with make_session() as gsession:
        channels = list(
            (await gsession.execute(select(Channel))).scalars().all()
        )
    polling = await get_settings_manager().get_polling(group.group_id)
    desired = desired_subscription_values(channels, polling)
    meta = {ch.channel_id: ch for ch in channels}

    sf = get_sessionmaker()
    async with sf() as session:
        async with session.begin():
            current = {
                s.channel_id
                for s in (
                    await session.execute(
                        select(ChannelSubscription).where(
                            ChannelSubscription.group_id == group.group_id
                        )
                    )
                ).scalars()
            }
            for channel_id, (interval, window) in desired.items():
                ch = meta[channel_id]
                await upsert_registry(
                    session, channel_id,
                    title=ch.channel_name, upload_playlist_id=ch.upload_playlist_id,
                )
                await subscribe(session, channel_id, group.group_id, interval, window)
            for stale in current - set(desired):
                await unsubscribe(session, stale, group.group_id)


async def backfill_channel_registry() -> None:
    """부팅 시 전 활성 그룹의 채널을 레지스트리·구독에 백필. 멱등 (스펙 §6)."""
    sf = get_sessionmaker()
    async with sf() as session:
        groups = list(
            (
                await session.execute(select(Group).where(Group.is_active.is_(True)))
            ).scalars().all()
        )
    for group in groups:
        try:
            await resync_group(group)
        except Exception as e:
            print(f"[registry-backfill] 그룹 {group.slug} 동기화 실패: {e}")
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_channel_registry_service.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add app/services/channel_registry_service.py tests/test_channel_registry_service.py
git commit -m "feat: 레지스트리·구독 동기화 서비스 — due 산출/유효값 해석/resync/백필 (B-0b §2·§4·§6)"
```

---

### Task 4: MonitorService 분해 — API 조회와 그룹 삽입 분리

**Files:**
- Modify: `app/services/monitor_service.py:99-158` (`process_channel`)
- Test: 기존 스위트 회귀 확인 (동작 보존 리팩토링 — 새 테스트는 Task 5에서 팬아웃 경유로 검증)

- [ ] **Step 1: 분해 구현** — `MonitorService.process_channel`(99행)을 아래처럼 대체. `_filter_new_videos`/`_next_sequence`/`_update_last_checked`는 무변경:

```python
    async def process_channel(
        self,
        channel: Channel,
        session: AsyncSession,
        api_client: YouTubeAPIClient,
    ) -> List[int]:
        """채널 폴링 → 신규 영상 INSERT → 새 video_pk 목록 반환.

        polling.window_hours(최신 영상 수집 범위) 안의 영상을 모두 수집한다.
        """
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=int(self.polling.window_hours or 24))
        metas = await fetch_channel_updates(api_client, channel.upload_playlist_id, cutoff)
        return await self.insert_group_videos(channel, session, metas, cutoff, now=now)

    async def insert_group_videos(
        self,
        channel: Channel,
        session: AsyncSession,
        metas: Sequence[VideoMeta],
        cutoff: datetime,
        now: Optional[datetime] = None,
    ) -> List[int]:
        """조회 결과를 이 그룹 스키마에 삽입한다 (그룹별 필터·채번·last_checked).

        중앙 폴러 팬아웃과 그룹 스코프 폴링이 공유하는 삽입 경로 — 필터 이중화 금지.
        cutoff: 이 그룹의 window_hours 컷 (중앙 폴러는 최대 윈도로 넓게 조회 후 재컷).
        """
        now = now or datetime.now(timezone.utc)
        latest_video_id = metas[0].video_id if metas else None
        metas = [m for m in metas if parse_iso_datetime(m.published_at) >= cutoff]
        if not metas:
            await self._update_last_checked(session, channel, now, latest_video_id)
            return []

        new_ids = await self._filter_new_videos(session, [m.video_id for m in metas])
        by_id = {m.video_id: m for m in metas}
        new_metas = [by_id[v] for v in new_ids]
        if not new_metas:
            await self._update_last_checked(session, channel, now, latest_video_id)
            return []

        seq_start = await self._next_sequence(session, channel.channel_pk)
        inserted: List[int] = []
        for idx, vm in enumerate(new_metas):
            stmt = (
                pg_insert(Video)
                .values(
                    channel_pk=channel.channel_pk,
                    video_id=vm.video_id,
                    video_url=vm.video_url,
                    title=vm.title,
                    description=vm.description,
                    thumbnail_url=vm.thumbnail_url,
                    published_at=parse_iso_datetime(vm.published_at),
                    duration_seconds=parse_duration_seconds(vm.duration),
                    view_count=vm.view_count,
                    like_count=vm.like_count,
                    sequence_in_channel=seq_start + idx,
                    analysis_status="pending",
                    retry_count=0,
                )
                .on_conflict_do_nothing(index_elements=["video_id"])
                .returning(Video.video_pk)
            )
            pk = (await session.execute(stmt)).scalar()
            if pk:
                inserted.append(pk)

        await session.flush()
        await self._update_last_checked(session, channel, now, latest_video_id)
        return inserted
```

모듈 레벨(클래스 밖, `MonitorService` 정의 위)에 API 조회 함수 추가:

```python
async def fetch_channel_updates(
    api_client: YouTubeAPIClient, upload_playlist_id: str, cutoff: datetime
) -> List[VideoMeta]:
    """채널 업로드 목록 조회 → 상세 일괄 조회. 그룹 무관 — 중앙 폴러가 채널당 1회 호출.

    상세 조회는 window 내 전체 항목 대상(그룹별 '신규' 판정은 삽입 단계 몫).
    videos.list는 50개당 1유닛이라 쿼터 영향 무시 가능.
    """
    items = await api_client.get_latest_playlist_items(
        upload_playlist_id, published_after=cutoff
    )
    if not items:
        return []
    metas = await api_client.get_video_details([it.video_id for it in items])
    return [m for m in metas if parse_iso_datetime(m.published_at) >= cutoff]
```

임포트 정리: 파일 상단에 `Sequence`가 이미 있는지 확인(`from typing import ...`), `VideoMeta`는 `from app.services.youtube_api import VideoMeta` 추가 (기존 임포트 블록에 병합).

**동작 변화 주의(의도됨, 스펙 §3):** 기존에는 `_filter_new_videos` **후** `get_video_details`를 호출했지만, 분해 후에는 window 내 전체 항목의 상세를 조회한 뒤 삽입 단계에서 신규 필터링한다. 상세 조회는 50개당 1유닛이라 쿼터 영향은 무시 가능하고, 중앙 팬아웃에서는 "신규"가 그룹마다 달라 이 순서가 필수다.

- [ ] **Step 2: 회귀 확인**

Run: `.venv_e2e/bin/python -m pytest tests/ -x -q`
Expected: 전체 PASS (process_channel의 외부 계약 불변)

- [ ] **Step 3: 커밋**

```bash
git add app/services/monitor_service.py
git commit -m "refactor: process_channel 분해 — API 조회(fetch)/그룹 삽입(insert) 분리 (B-0b §3)"
```

---

### Task 5: 중앙 폴러 (`run_central_poll_once`)

**Files:**
- Create: `app/services/central_poller.py`
- Test: `tests/test_central_poller.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_central_poller.py`:

```python
"""중앙 폴링 팬아웃 검증 — 채널당 API 조회 1회, 그룹 실패 격리, 쿼터 중단.

모듈 경계(fetch/팬아웃/mark)를 monkeypatch해 오케스트레이션만 검증한다.
실제 SQL·API는 실 DB E2E에서.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services import central_poller as cp
from app.services.channel_registry_service import DueChannel
from app.services.youtube_api import YouTubeQuotaExceededError

DUE = [
    DueChannel("UC1", "UU1", effective_interval_min=60, fetch_window_hours=24),
    DueChannel("UC2", "UU2", effective_interval_min=60, fetch_window_hours=48),
]
GROUP_A = SimpleNamespace(group_id=1, slug="a", schema_name="youtube_a")
GROUP_B = SimpleNamespace(group_id=2, slug="b", schema_name="youtube_b")


@pytest.fixture
def wired(monkeypatch):
    """공통 배선: 시스템 키/플로어/due/구독/그룹 + 기록용 스파이."""
    calls = SimpleNamespace(fetched=[], fanned=[], marked=[])

    async def fake_system_key():
        return "sys-key"

    async def fake_prepare():
        subs = {
            "UC1": [SimpleNamespace(group_id=1, window_hours=24),
                    SimpleNamespace(group_id=2, window_hours=24)],
            "UC2": [SimpleNamespace(group_id=1, window_hours=48)],
        }
        return DUE, subs, {1: GROUP_A, 2: GROUP_B}

    async def fake_fetch(api, playlist_id, cutoff):
        calls.fetched.append(playlist_id)
        return [SimpleNamespace(video_id="v1", published_at="2026-07-05T00:00:00Z")]

    async def fake_fan_out(group, channel_id, metas, window_hours, now):
        calls.fanned.append((group.slug, channel_id))
        return 1

    async def fake_mark(channel_id, now, last_video_at):
        calls.marked.append(channel_id)

    monkeypatch.setattr(cp, "get_system_youtube_key", fake_system_key)
    monkeypatch.setattr(cp, "_prepare_tick", fake_prepare)
    monkeypatch.setattr(cp, "fetch_channel_updates", fake_fetch)
    monkeypatch.setattr(cp, "_fan_out_group", fake_fan_out)
    monkeypatch.setattr(cp, "_mark_polled", fake_mark)
    return calls


async def test_one_fetch_per_channel_fanout_all_groups(wired):
    await cp.run_central_poll_once()
    # 채널 2개 → API 조회 2회 (그룹 3구독이어도 3회 아님)
    assert sorted(wired.fetched) == ["UU1", "UU2"]
    # UC1은 두 그룹, UC2는 한 그룹에 팬아웃
    assert sorted(wired.fanned) == [("a", "UC1"), ("a", "UC2"), ("b", "UC1")]
    assert sorted(wired.marked) == ["UC1", "UC2"]


async def test_group_failure_isolated(wired, monkeypatch):
    async def failing_fan_out(group, channel_id, metas, window_hours, now):
        if group.slug == "a":
            raise RuntimeError("boom")
        wired.fanned.append((group.slug, channel_id))
        return 1

    monkeypatch.setattr(cp, "_fan_out_group", failing_fan_out)
    await cp.run_central_poll_once()  # 예외 전파 없음
    assert ("b", "UC1") in wired.fanned          # 다른 그룹은 계속
    assert sorted(wired.marked) == ["UC1", "UC2"]  # 채널 자체는 폴링 완료 처리


async def test_quota_exceeded_aborts_tick(wired, monkeypatch):
    async def quota_fetch(api, playlist_id, cutoff):
        wired.fetched.append(playlist_id)
        raise YouTubeQuotaExceededError("quota")

    monkeypatch.setattr(cp, "fetch_channel_updates", quota_fetch)
    await cp.run_central_poll_once()  # 예외 전파 없음
    assert wired.fanned == []
    assert wired.marked == []  # 폴링 실패 → 다음 틱 재시도 (idempotent, 스펙 §8)


async def test_no_system_key_skips(wired, monkeypatch):
    async def no_key():
        return ""

    monkeypatch.setattr(cp, "get_system_youtube_key", no_key)
    await cp.run_central_poll_once()
    assert wired.fetched == []
```

- [ ] **Step 2: 실패 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_central_poller.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 구현** — `app/services/central_poller.py`:

```python
"""중앙 폴링 (스펙 B-0b §3): 채널당 1회 API 조회 → 구독 그룹 푸시 팬아웃.

- 항상 시스템 키 사용 (그룹 키 폴백 없음 — 그룹 스코프 호출과 구별).
- 그룹 단위 try/except 격리: 한 그룹 실패가 다른 그룹을 막지 않는다.
- 쿼터 초과는 틱 전체 중단. last_polled_at 미갱신 채널은 다음 틱 재폴링되며
  이미 삽입된 그룹은 _filter_new_videos가 중복을 막는다 (idempotent).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from sqlalchemy import select

from app.control_db import get_sessionmaker
from app.models.control.group import Group
from app.models.pg.channel import Channel
from app.services.channel_registry_service import (
    DueChannel,
    list_due_channels,
    mark_polled,
    subscriptions_for_channels,
)
from app.services.db_engine import DBNotConfiguredError, data_plane_engine_manager as dpm
from app.services.global_settings import get_central_poll_floor_min, get_system_youtube_key
from app.services.job_logger import (
    JOB_TYPE_CHANNEL_POLL,
    STATUS_FAIL,
    STATUS_SKIP,
    STATUS_SUCCESS,
    JobTimer,
    write_job_log,
)
from app.services.monitor_service import (
    MonitorService,
    _make_session_factory,
    fetch_channel_updates,
)
from app.services.settings_manager import get_settings_manager
from app.services.settings_types import PollingSettings
from app.services.youtube_api import YouTubeAPIClient, YouTubeQuotaExceededError
from app.services.yt_parsing import parse_iso_datetime

# 중앙 폴러 동시 채널 상한. 그룹별 max_concurrent_channels는 그룹 폴링용 설정이라
# 부적합 — 전역 설정 키로 승격은 필요해질 때 (스펙 §3).
CENTRAL_MAX_CONCURRENT_CHANNELS = 5


async def _prepare_tick():
    """due 채널 + 채널별 구독 + 활성 그룹 맵을 한 번에 준비한다."""
    now = datetime.now(timezone.utc)
    sf = get_sessionmaker()
    async with sf() as session:
        floor = await get_central_poll_floor_min(session)
        due = await list_due_channels(session, now=now, floor_min=floor)
        subs = await subscriptions_for_channels(session, [d.channel_id for d in due])
        groups = {
            g.group_id: g
            for g in (
                await session.execute(select(Group).where(Group.is_active.is_(True)))
            ).scalars()
        }
    return due, subs, groups


async def _fan_out_group(
    group: Group,
    channel_id: str,
    metas: Sequence,
    window_hours: int,
    now: datetime,
) -> Optional[int]:
    """한 그룹 스키마에 삽입. 반환: 신규 영상 수(그룹 채널 미존재 시 None)."""
    await dpm.ensure_schema(group)
    engine = await dpm.get_engine_for_group(group)
    make_session = _make_session_factory(engine, group.schema_name)
    polling = await get_settings_manager().get_polling(group.group_id)
    service = MonitorService(polling=polling)
    cutoff = now - timedelta(hours=int(window_hours))

    timer = JobTimer()
    with timer:
        async with make_session() as session:
            async with session.begin():
                channel = (
                    await session.execute(
                        select(Channel).where(Channel.channel_id == channel_id)
                    )
                ).scalar_one_or_none()
                if channel is None or not channel.is_active:
                    return None  # 구독 테이블과 그룹 스키마 불일치 — 다음 resync가 복구
                new_pks = await service.insert_group_videos(
                    channel, session, metas, cutoff, now=now
                )
                channel_pk = channel.channel_pk
    await write_job_log(
        make_session,
        job_type=JOB_TYPE_CHANNEL_POLL,
        status=STATUS_SUCCESS,
        message=f"중앙폴링 신규 영상 {len(new_pks)}건" if new_pks else "중앙폴링 신규 영상 없음",
        duration_ms=timer.elapsed_ms,
        channel_pk=channel_pk,
    )
    return len(new_pks)


async def _mark_polled(
    channel_id: str, now: datetime, last_video_at: Optional[datetime]
) -> None:
    sf = get_sessionmaker()
    async with sf() as session:
        async with session.begin():
            await mark_polled(session, channel_id, now, last_video_at)


async def run_central_poll_once() -> None:
    """전역 중앙 폴링 틱: registry 기준 채널당 1회 폴링 후 구독 그룹 팬아웃."""
    system_key = await get_system_youtube_key()
    if not system_key:
        print("[central-poll] 시스템 YouTube 키 미설정 - 중앙 폴링 SKIP")
        return

    due, subs_by_channel, groups = await _prepare_tick()
    if not due:
        return
    print(f"[central-poll] 폴링 시작: {len(due)}개 채널")

    now = datetime.now(timezone.utc)
    api_client = YouTubeAPIClient(PollingSettings(youtube_api_key=system_key))
    sem = asyncio.Semaphore(CENTRAL_MAX_CONCURRENT_CHANNELS)
    quota_hit = asyncio.Event()

    async def _one(d: DueChannel) -> None:
        async with sem:
            if quota_hit.is_set():
                return
            if not d.upload_playlist_id:
                print(f"[central-poll] {d.channel_id} 플레이리스트 미상 - SKIP")
                return
            cutoff = now - timedelta(hours=d.fetch_window_hours)
            try:
                metas = await fetch_channel_updates(api_client, d.upload_playlist_id, cutoff)
            except YouTubeQuotaExceededError as e:
                print(f"[central-poll] 쿼터 초과 - 틱 중단: {e}")
                quota_hit.set()
                return
            except Exception as e:
                print(f"[central-poll] {d.channel_id} 조회 실패: {e}")
                return  # last_polled_at 미갱신 → 다음 틱 재시도

            for sub in subs_by_channel.get(d.channel_id, []):
                group = groups.get(sub.group_id)
                if group is None:
                    continue  # 비활성 그룹 — 구독은 남아 있어도 팬아웃 제외
                try:
                    await _fan_out_group(
                        group, d.channel_id, metas, sub.window_hours, now
                    )
                except DBNotConfiguredError:
                    continue
                except Exception as e:
                    print(f"[central-poll] [{group.slug}] {d.channel_id} 팬아웃 실패: {e}")

            last_video_at = (
                max(parse_iso_datetime(m.published_at) for m in metas) if metas else None
            )
            await _mark_polled(d.channel_id, now, last_video_at)

    try:
        await asyncio.gather(*[_one(d) for d in due], return_exceptions=True)
    finally:
        await api_client.aclose()
```

(`subscriptions_for_channels`는 Task 3에서 이미 구현됨.)

**주의:** 그룹 비활성 시 구독은 삭제되므로(§4) `groups.get() is None` 분기는 이중 방어다. `job_logger.JobTimer` 임포트 경로는 구현 시 확인 — `monitor_service.py`가 쓰는 것과 동일 출처를 쓴다(현재 `from app.services.job_logger import ...`).

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_central_poller.py tests/test_channel_registry_service.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add app/services/central_poller.py app/services/channel_registry_service.py tests/test_central_poller.py
git commit -m "feat: 중앙 폴러 — 채널당 1회 조회·구독 그룹 팬아웃·격리·쿼터 중단 (B-0b §3)"
```

---

### Task 6: 그룹 스코프 키 폴백 적용

**Files:**
- Modify: `app/services/monitor_service.py` (`_poll_group:282`, `run_stats_refresh_once:791`, `poll_single_channel:888`)
- Modify: `app/routers/channels.py:33-38` (`add_channel` 키 가드)
- Test: 기존 스위트 회귀 (폴백 로직 자체는 Task 2에서 단위 테스트 완료)

- [ ] **Step 1: monitor_service 세 지점 수정** — 패턴은 동일. `_poll_group`(282행 부근):

```python
async def _poll_group(group: Group) -> None:
    mgr = get_settings_manager()
    polling = await mgr.get_polling(group.group_id)
    api_key = await resolve_youtube_key(group.group_id)
    if not api_key:
        print(f"[{group.slug}] YouTube API 키 미설정(그룹·시스템 모두) - 폴링 SKIP")
        return
    polling = replace(polling, youtube_api_key=api_key)
    ...  # 이하 기존과 동일 (polling을 그대로 사용)
```

파일 상단 임포트에 추가:

```python
from dataclasses import replace

from app.services.global_settings import resolve_youtube_key
```

같은 패턴을 `run_stats_refresh_once`(804행 부근 `if not polling.youtube_api_key: continue` → resolve 후 replace, 없으면 continue)와 `poll_single_channel`(892행 부근)에 적용한다. 세 곳 모두 "`polling.youtube_api_key` 검사 → skip" 분기를 "`resolve_youtube_key` 결과 검사 → 없으면 skip, 있으면 `replace(polling, youtube_api_key=...)`"로 교체.

**순환 임포트 주의:** `global_settings`는 `settings_manager`만 임포트하므로 `monitor_service → global_settings` 방향은 안전. 반대로 `central_poller → monitor_service` 임포트가 이미 있으므로 `monitor_service`에서 `central_poller`를 임포트하지 말 것.

- [ ] **Step 2: add_channel 키 가드 교체** — `app/routers/channels.py:33-38`:

```python
    polling = await get_settings_manager().get_polling(group.group_id)
    api_key = await resolve_youtube_key(group.group_id)
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="YouTube API 키가 없습니다. 그룹 polling 설정 또는 시스템 전역 키를 설정하세요.",
        )
    polling = replace(polling, youtube_api_key=api_key)
    api = YouTubeAPIClient(polling)
```

임포트 추가: `from dataclasses import replace`, `from app.services.global_settings import resolve_youtube_key`.

- [ ] **Step 3: 회귀 확인**

Run: `.venv_e2e/bin/python -m pytest tests/ -x -q`
Expected: 전체 PASS

- [ ] **Step 4: 커밋**

```bash
git add app/services/monitor_service.py app/routers/channels.py
git commit -m "feat: 그룹 스코프 YouTube 호출에 시스템 키 폴백 적용 (B-0b §5)"
```

---

### Task 7: 라우터 동기화 훅 (채널/설정/그룹)

**Files:**
- Modify: `app/routers/channels.py` (`add_channel`, `update_channel`, `delete_channel`)
- Modify: `app/routers/settings.py` (`put_settings`의 polling 분기)
- Modify: `app/routers/groups.py` (`update_group`, `delete_group`)
- Test: `tests/test_channel_registry_service.py`에 훅 헬퍼 테스트 추가

- [ ] **Step 1: 채널 라우터 훅** — `app/routers/channels.py`. 임포트 추가:

```python
from app.control_db import get_sessionmaker
from app.services import channel_registry_service as registry
```

`add_channel`: 채널 생성 트랜잭션 커밋 후(마지막 `return` 직전, `finally` 블록 밖 — s2 조회 뒤)에 구독 등록:

```python
            async with get_sessionmaker()() as cs:
                async with cs.begin():
                    await registry.upsert_registry(
                        cs, meta.channel_id,
                        title=meta.channel_name,
                        upload_playlist_id=meta.upload_playlist_id,
                    )
                    await registry.subscribe(
                        cs, meta.channel_id, group.group_id,
                        poll_interval_min=payload.poll_interval_min
                        or polling.default_channel_interval_min,
                        window_hours=polling.window_hours,
                    )
```

`update_channel`: 커밋 후 채널 객체로 구독 동기화 (`poll_interval_min`/`is_active` 변경 시):

```python
        if "poll_interval_min" in data or "is_active" in data:
            polling = await get_settings_manager().get_polling(group.group_id)
            async with get_sessionmaker()() as cs:
                async with cs.begin():
                    if channel.is_active:
                        await registry.upsert_registry(cs, channel.channel_id)
                        await registry.subscribe(
                            cs, channel.channel_id, group.group_id,
                            poll_interval_min=channel.poll_interval_min
                            or polling.default_channel_interval_min,
                            window_hours=polling.window_hours,
                        )
                    else:
                        await registry.unsubscribe(cs, channel.channel_id, group.group_id)
```

`delete_channel`: 삭제 트랜잭션에서 `channel.channel_id`를 로컬 변수로 잡아둔 뒤, 커밋 후:

```python
        async with get_sessionmaker()() as cs:
            async with cs.begin():
                await registry.unsubscribe(cs, deleted_channel_id, group.group_id)
```

- [ ] **Step 2: 설정 라우터 훅** — `app/routers/settings.py`의 `put_settings`, 기존 polling 분기(78행 부근)에 재동기화 추가:

```python
    if category == "polling":
        # default_channel_interval_min/window_hours 변경이 구독 유효값에 반영되도록
        await registry_resync_group(group)
        if app_settings.SCHEDULER_ENABLED:
            await apply_pending_analysis_schedule()
```

임포트: `from app.services.channel_registry_service import resync_group as registry_resync_group`.

- [ ] **Step 3: 그룹 라우터 훅** — `app/routers/groups.py`. 임포트:

```python
from app.services.channel_registry_service import remove_group_subscriptions, resync_group
```

`update_group`: is_active 전환 감지 (커밋 전 이전 값 캡처):

```python
    data = payload.model_dump(exclude_unset=True)
    was_active = group.is_active
    for field, value in data.items():
        setattr(group, field, value)
    await session.commit()
    await session.refresh(group)
    if "is_active" in data and group.is_active != was_active:
        if group.is_active:
            await resync_group(group)                      # 재활성 → 구독 복원 (스펙 §4)
        else:
            await remove_group_subscriptions(session, group.group_id)
            await session.commit()
    return group
```

`delete_group`: 삭제 전 구독 정리(recount 포함 — FK CASCADE만으로는 카운트가 안 맞음):

```python
    await remove_group_subscriptions(session, group.group_id)
    await session.delete(group)
    await session.commit()
```

- [ ] **Step 4: 회귀 확인**

Run: `.venv_e2e/bin/python -m pytest tests/ -x -q`
Expected: 전체 PASS

- [ ] **Step 5: 커밋**

```bash
git add app/routers/channels.py app/routers/settings.py app/routers/groups.py
git commit -m "feat: 채널/설정/그룹 변경 시 레지스트리·구독 동기화 훅 (B-0b §4)"
```

---

### Task 8: 부트스트랩 + 스케줄러 교체

**Files:**
- Modify: `app/services/global_settings.py` (부트스트랩 시드 함수 추가)
- Modify: `app/main.py` (lifespan)
- Modify: `app/services/scheduler.py` (JOB_MASTER_POLL 잡 교체)
- Test: `tests/test_global_settings.py`에 시드 로직 테스트 추가

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_global_settings.py`에 추가:

```python
async def test_pick_seed_key_prefers_admin_group_with_key():
    """admin 소유 그룹 중 polling 키가 있는 첫 그룹의 키를 고른다 (순수 판정 함수)."""
    from app.services.global_settings import pick_bootstrap_youtube_key

    groups = [
        SimpleNamespace(group_id=1),
        SimpleNamespace(group_id=2),
    ]
    keys = {1: "", 2: "admin-key"}

    async def get_polling(group_id):
        return SimpleNamespace(youtube_api_key=keys[group_id])

    out = await pick_bootstrap_youtube_key(groups, get_polling)
    assert out == "admin-key"


async def test_pick_seed_key_none_when_no_keys():
    from app.services.global_settings import pick_bootstrap_youtube_key

    async def get_polling(group_id):
        return SimpleNamespace(youtube_api_key="")

    assert await pick_bootstrap_youtube_key([SimpleNamespace(group_id=1)], get_polling) is None
```

- [ ] **Step 2: 실패 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_global_settings.py -v`
Expected: 새 테스트 2건 FAIL — `ImportError: cannot import name 'pick_bootstrap_youtube_key'`

- [ ] **Step 3: 시드 구현** — `app/services/global_settings.py`에 추가:

```python
async def pick_bootstrap_youtube_key(groups, get_polling) -> Optional[str]:
    """후보 그룹들(호출자가 admin 소유·group_id 순으로 전달) 중 첫 polling 키."""
    for group in groups:
        polling = await get_polling(group.group_id)
        if polling.youtube_api_key:
            return polling.youtube_api_key
    return None


async def bootstrap_global_settings() -> None:
    """시스템 YouTube 키 미설정 시 admin 소유 그룹 키로 1회 시드. 멱등 (스펙 §6).

    Phase A의 AUTH_PASSWORD 부트스트랩과 같은 철학 — 기존 단일 운영자 배포가
    업그레이드 직후에도 설정 변경 없이 폴링 무중단.
    """
    from app.models.control.group import Group
    from app.models.control.user import User

    sf = get_sessionmaker()
    async with sf() as session:
        existing = await get_global(session, GLOBAL_YOUTUBE_API_KEY)
        if existing:
            return
        groups = list(
            (
                await session.execute(
                    select(Group)
                    .join(User, User.user_id == Group.owner_user_id)
                    .where(User.role == "admin", Group.is_active.is_(True))
                    .order_by(Group.group_id)
                )
            ).scalars().all()
        )
    key = await pick_bootstrap_youtube_key(
        groups, get_settings_manager().get_polling
    )
    if not key:
        return
    async with sf() as session:
        async with session.begin():
            await set_global(session, GLOBAL_YOUTUBE_API_KEY, key)
    print("[bootstrap] 시스템 YouTube 키를 admin 그룹 키로 시드했습니다.")
```

- [ ] **Step 4: lifespan 배선** — `app/main.py`의 lifespan에서 `bootstrap_auth()` 다음에 추가 (`backfill_notify_baselines`와 같은 try/except 가드 스타일 확인 후 동일하게):

```python
    from app.services.channel_registry_service import backfill_channel_registry
    from app.services.global_settings import bootstrap_global_settings

    await bootstrap_global_settings()
    try:
        await backfill_channel_registry()
    except Exception as e:
        print(f"[startup] 채널 레지스트리 백필 실패(다음 부팅에서 재시도): {e}")
```

- [ ] **Step 5: 스케줄러 교체** — `app/services/scheduler.py`:

임포트에서 `run_master_poll_once` 제거하고 추가:

```python
from app.services.central_poller import run_central_poll_once
```

`setup_jobs`의 JOB_MASTER_POLL 잡을 교체:

```python
    scheduler.add_job(
        run_central_poll_once,       # B-0b: 그룹 순회 폴링 → 중앙 폴링
        trigger="interval",
        minutes=int(settings.MASTER_POLL_INTERVAL_MIN),
        id=JOB_MASTER_POLL,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
```

`monitor_service.py`의 `run_master_poll_once`와 `_poll_group` 처리: `_poll_group`은 수동 그룹 폴링 액션(`poll_group:878`, actions.py에서 사용)이 여전히 쓰므로 **유지**. `run_master_poll_once`는 호출처가 없어지므로 **삭제** (grep으로 잔여 참조 확인: `grep -rn "run_master_poll_once" app/ tests/`).

- [ ] **Step 6: 전체 회귀 확인**

Run: `.venv_e2e/bin/python -m pytest tests/ -x -q`
Expected: 전체 PASS

- [ ] **Step 7: 커밋**

```bash
git add app/services/global_settings.py app/main.py app/services/scheduler.py app/services/monitor_service.py tests/test_global_settings.py
git commit -m "feat: 시스템 키 부트스트랩·레지스트리 백필·스케줄러 중앙 폴링 전환 (B-0b §6)"
```

---

### Task 9: 관리자 전역 설정 API

**Files:**
- Modify: `app/routers/admin.py`
- Modify: `app/schemas/admin.py`
- Test: `tests/test_admin_api.py` (기존 파일 확장)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_admin_api.py`의 `test_admin_routes_registered`에 경로 추가 + 권한 테스트 확장:

```python
def test_admin_routes_registered():
    paths = {r.path for r in app.routes}
    assert "/api/admin/users" in paths
    assert "/api/admin/invitations" in paths
    assert "/api/admin/invitations/{invite_id}" in paths
    assert "/api/admin/plans" in paths
    assert "/api/admin/global-settings" in paths          # B-0b


def test_non_admin_forbidden_global_settings():
    async def _dep():
        return USER
    app.dependency_overrides[require_user] = _dep
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/api/admin/global-settings").status_code == 403
    assert c.put("/api/admin/global-settings", json={"items": []}).status_code == 403
```

- [ ] **Step 2: 실패 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_admin_api.py -v`
Expected: 새 assert FAIL (`/api/admin/global-settings` not in paths)

- [ ] **Step 3: 스키마 + 엔드포인트 구현**

`app/schemas/admin.py`에 추가:

```python
class GlobalSettingItem(BaseModel):
    key: str
    value: str  # 시크릿은 마스킹 반환
    is_secret: bool = False


class GlobalSettingsUpdate(BaseModel):
    items: list[GlobalSettingItem]
```

`app/routers/admin.py`에 추가 (라우터가 `dependencies=[Depends(require_admin)]`라 권한은 자동):

```python
from app.services.global_settings import (
    GLOBAL_CENTRAL_POLL_FLOOR_MIN,
    GLOBAL_YOUTUBE_API_KEY,
    SECRET_KEYS,
    get_global,
    set_global,
)
from app.services.settings_manager import mask_secret

_GLOBAL_KEYS = (GLOBAL_YOUTUBE_API_KEY, GLOBAL_CENTRAL_POLL_FLOOR_MIN)


@router.get("/global-settings", response_model=list[GlobalSettingItem])
async def list_global_settings(
    session: AsyncSession = Depends(get_session),
) -> list[GlobalSettingItem]:
    out = []
    for key in _GLOBAL_KEYS:
        raw = await get_global(session, key)
        is_secret = key in SECRET_KEYS
        value = mask_secret(raw) if (raw and is_secret) else (raw or "")
        out.append(GlobalSettingItem(key=key, value=value, is_secret=is_secret))
    return out


@router.put("/global-settings", response_model=list[GlobalSettingItem])
async def put_global_settings(
    payload: GlobalSettingsUpdate,
    session: AsyncSession = Depends(get_session),
) -> list[GlobalSettingItem]:
    for item in payload.items:
        if item.key not in _GLOBAL_KEYS:
            raise HTTPException(status_code=400, detail=f"허용되지 않은 키: {item.key}")
        if item.value.strip():
            await set_global(session, item.key, item.value.strip())
    await session.commit()
    return await list_global_settings(session)
```

(스키마 임포트 라인도 admin.py 상단에 추가: `GlobalSettingItem`, `GlobalSettingsUpdate`.)

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_admin_api.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add app/routers/admin.py app/schemas/admin.py tests/test_admin_api.py
git commit -m "feat: 관리자 전역 설정 API — 시스템 YouTube 키/폴링 하한 (B-0b §5)"
```

---

### Task 10: 전체 검증 + 문서 마감

**Files:**
- Modify: `docs/superpowers/specs/2026-07-03-multi-tenant-design.md` (§7 Phase 표의 B-0b 상태)
- Modify: `docs/architecture.md` (중앙 폴링 흐름 반영 — 기존 폴링 서술 확인 후 갱신)

- [ ] **Step 1: 전체 스위트**

Run: `.venv_e2e/bin/python -m pytest tests/ -q`
Expected: 전체 PASS. 실패 시 여기서 수정 (다음 단계로 넘어가지 않는다).

- [ ] **Step 2: 임포트 사이클·잔여 참조 점검**

Run: `grep -rn "run_master_poll_once" app/ tests/` → 결과 없음 확인.
Run: `.venv_e2e/bin/python -c "import app.main"` → 임포트 에러 없음 확인.

- [ ] **Step 3: 문서 갱신**

- `2026-07-03-multi-tenant-design.md` §7 표의 B-0b 행: "(설계 확정 2026-07-05)" → "(구현 완료 YYYY-MM-DD — 실 DB E2E는 별도 세션)".
- `docs/architecture.md`: 폴링 흐름 서술을 찾아(grep "폴링") 그룹별 폴링 → 중앙 폴링+팬아웃으로 갱신. 전역 설정/키 폴백 한 단락 추가.

- [ ] **Step 4: 커밋**

```bash
git add docs/
git commit -m "docs: B-0b 구현 반영 — 중앙 폴링 아키텍처·Phase 상태 갱신"
```

- [ ] **Step 5: 실 DB E2E 준비 안내 (구현 세션 종료 시 사용자에게 보고)**

구현 완료 후 push 전에 실 DB E2E 필요 (스펙 §9): 테스트 DB `100.115.13.102`에 두 그룹이 같은 실제 채널 구독 → 중앙 틱 1회 → API 조회 1회 + 두 그룹 영상 보유 + registry/subscriptions 상태 검증 → B-0a 캐시 경로 관통. **E2E는 사용자와 함께 별도 진행 — 이 계획 범위 밖.** push 금지 상태 유지.

---

## Self-Review 결과 (작성 시 수행)

- **스펙 커버리지**: §2(테이블 3종)=Task 1·3, §3(중앙 폴링·분해)=Task 4·5, §4(동기화·생애주기)=Task 3·7, §5(전역 설정·폴백·관리자 API)=Task 2·6·9, §6(백필·시드)=Task 8, §7(기각 대안)=구현 없음(정상), §8(에러 처리)=Task 5 테스트, §9(테스트)=각 Task+Task 10, §10(검증 기준)=Task 10 Step 5의 E2E로 이월.
- **타입 일관성**: `DueChannel`(Task 3 정의, Task 5 사용), `insert_group_videos(channel, session, metas, cutoff, now)`(Task 4 정의, Task 5 사용), `resolve_youtube_key(group_id)`(Task 2 정의, Task 6 사용), `subscriptions_for_channels`(Task 3 정의, Task 5 사용).
- **알려진 재량 지점**: `global_settings` DDL이 스펙의 `value TEXT NOT NULL` 대신 기존 `app.settings`와 같은 value/value_enc 이원 구조 — 스펙 §5의 "암호화 저장" 요구를 기존 패턴으로 구현한 것(스펙 우선순위: 동작 요구 > 예시 DDL).
