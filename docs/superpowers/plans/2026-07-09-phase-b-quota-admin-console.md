# Phase B: 쿼터·관리자 콘솔 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 플랜/오버라이드 기반 개수 쿼터 5종을 강제하고, 관리자 콘솔(정지·플랜·한도·임시비번)과 마이페이지를 추가한다.

**Architecture:** 신규 `quota_service`가 유효 한도 해석(`COALESCE(user_limits, plan)`)과 검사 함수를 단일 소유. 강제 지점은 기존 라우터/스케줄러 5곳에 삽입. `analysis_deliveries`는 `UNIQUE(user_id, cache_id)`로 전환해 재분석 과카운트를 제거. admin/owner 없음/개발 모드는 전부 무제한 통과.

**Tech Stack:** FastAPI + SQLAlchemy async(제어 평면 `app` 스키마), pytest(asyncio auto), React+TS(vite).

**설계 문서:** `docs/superpowers/specs/2026-07-09-phase-b-quota-admin-console-design.md`

**중요 배경 (엔지니어 필독):**
- 제어 평면 모델은 `app/models/control/`, `Base`는 `app/control_db.py`. 테이블 생성은 `ensure_control_schema()`가 부팅 시 멱등 수행 — 마이그레이션 파일 없음, ORM `create_all` + 명시적 ALTER 패턴.
- ORM 컬럼 기본값은 반드시 `server_default` 사용(raw `pg_insert`가 ORM `default=`를 무시함 — B-0b에서 실버그였음).
- `CurrentUser`(app/routers/auth.py:26)는 dataclass. 개발 모드(인증 비활성)에서는 `DEV_ADMIN`(user_id=0, role=admin)이 반환됨.
- 그룹의 `owner_user_id`는 NULL 가능(레거시 admin 그룹) — NULL이면 쿼터 미적용.
- 테스트는 `tests/`에 flat 배치. 라우터 테스트는 `app.dependency_overrides` + `monkeypatch`로 서비스 함수를 치환하는 패턴(tests/test_admin_api.py 참고). DB 불필요 단위 테스트가 기본.
- 전체 테스트: `.venv_e2e/bin/python -m pytest tests/ -q` (main repo의 `.venv`는 깨져 있음 — 건드리지 말 것).

---

### Task 1: KST 일일 경계·순수 검사 함수 (quota_service 기초)

**Files:**
- Create: `app/services/quota_service.py`
- Test: `tests/test_quota_service.py`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
"""quota_service 순수 함수 단위 테스트 (DB 불필요)."""

from datetime import datetime, timezone

from app.services.quota_service import (
    EffectiveLimits,
    check_video_duration,
    kst_day_start_utc,
    validate_poll_interval,
)

LIMITS = EffectiveLimits(
    max_groups=1, max_channels_total=5, max_analyses_per_day=10,
    max_video_minutes=60, min_poll_interval_min=60,
    plan_slug="free", plan_name="Free", has_override=False,
)


def test_kst_day_start_utc_afternoon():
    # KST 2026-07-09 14:00 = UTC 05:00 → 당일 KST 자정 = UTC 2026-07-08 15:00
    now = datetime(2026, 7, 9, 5, 0, tzinfo=timezone.utc)
    assert kst_day_start_utc(now) == datetime(2026, 7, 8, 15, 0, tzinfo=timezone.utc)


def test_kst_day_start_utc_crosses_utc_date():
    # UTC 7/8 16:00 = KST 7/9 01:00 → KST 자정은 UTC 7/8 15:00 (UTC 날짜와 다름)
    now = datetime(2026, 7, 8, 16, 0, tzinfo=timezone.utc)
    assert kst_day_start_utc(now) == datetime(2026, 7, 8, 15, 0, tzinfo=timezone.utc)


def test_check_video_duration():
    assert check_video_duration(LIMITS, 60 * 60) is True        # 정확히 한도
    assert check_video_duration(LIMITS, 60 * 60 + 1) is False   # 초과
    assert check_video_duration(LIMITS, None) is True           # 길이 미상은 통과
    assert check_video_duration(None, 999999) is True           # 무제한(admin/owner 없음)


def test_validate_poll_interval():
    assert validate_poll_interval(LIMITS, 60) is True
    assert validate_poll_interval(LIMITS, 59) is False
    assert validate_poll_interval(LIMITS, None) is True         # 미지정=그룹 기본값 사용
    assert validate_poll_interval(None, 1) is True
```

- [ ] **Step 2: 실패 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_quota_service.py -q`
Expected: FAIL — `ModuleNotFoundError: app.services.quota_service`

- [ ] **Step 3: 최소 구현**

```python
"""쿼터 서비스 (Phase B): 유효 한도 해석 + 검사 함수의 단일 소유 지점.

유효 한도 = COALESCE(user_limits.값, plan.값). admin/owner 없음/개발 모드는
limits=None으로 표현하며 모든 검사가 무조건 통과한다.
"당일" 기준은 KST(Asia/Seoul) 자정 — created_at 범위 비교로 기존
(user_id, created_at) 인덱스를 그대로 탄다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class EffectiveLimits:
    max_groups: int
    max_channels_total: int
    max_analyses_per_day: int
    max_video_minutes: int
    min_poll_interval_min: int
    plan_slug: str
    plan_name: str
    has_override: bool


def kst_day_start_utc(now: datetime) -> datetime:
    """now가 속한 KST 날짜의 자정(00:00 KST)을 UTC로 반환."""
    kst_now = now.astimezone(KST)
    start = kst_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc)


def check_video_duration(
    limits: Optional[EffectiveLimits], duration_seconds: Optional[int]
) -> bool:
    if limits is None or duration_seconds is None:
        return True
    return duration_seconds <= limits.max_video_minutes * 60


def validate_poll_interval(
    limits: Optional[EffectiveLimits], interval_min: Optional[int]
) -> bool:
    if limits is None or interval_min is None:
        return True
    return interval_min >= limits.min_poll_interval_min
```

- [ ] **Step 4: 통과 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_quota_service.py -q`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/quota_service.py tests/test_quota_service.py
git commit -m "feat: quota_service 기초 — KST 일일 경계·duration/poll interval 순수 검사"
```

---

### Task 2: UserLimit 모델 + deliveries UNIQUE 마이그레이션

**Files:**
- Create: `app/models/control/user_limit.py`
- Modify: `app/models/control/analysis_delivery.py` (UniqueConstraint 추가)
- Modify: `app/control_db.py:59-87` (`ensure_control_schema`)
- Modify: `app/services/analysis_cache_service.py:119-122` (`record_delivery` upsert 전환)
- Test: `tests/test_control_models.py` (추가), `tests/test_analysis_cache_service.py` (추가)

- [ ] **Step 1: 실패하는 테스트 작성 — 모델 정의 검증 (tests/test_control_models.py에 추가)**

기존 파일 끝에 추가:

```python
def test_user_limits_model_columns():
    from app.models.control.user_limit import UserLimit

    cols = {c.name for c in UserLimit.__table__.columns}
    assert cols == {
        "user_id", "max_groups", "max_channels_total", "max_analyses_per_day",
        "max_video_minutes", "monthly_cost_budget_usd", "min_poll_interval_min",
        "note", "updated_at",
    }
    # user_id 외 한도 컬럼은 전부 NULL 허용(NULL=플랜 값 사용)
    for name in ("max_groups", "max_channels_total", "max_analyses_per_day",
                 "max_video_minutes", "monthly_cost_budget_usd", "min_poll_interval_min"):
        assert UserLimit.__table__.columns[name].nullable is True


def test_analysis_delivery_unique_constraint():
    from sqlalchemy import UniqueConstraint

    from app.models.control.analysis_delivery import AnalysisDelivery

    uqs = [c for c in AnalysisDelivery.__table__.constraints if isinstance(c, UniqueConstraint)]
    assert any(
        {col.name for col in uq.columns} == {"user_id", "cache_id"} for uq in uqs
    )
```

- [ ] **Step 2: 실패 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_control_models.py -q`
Expected: FAIL — `ModuleNotFoundError: app.models.control.user_limit` / UNIQUE 미존재

- [ ] **Step 3: UserLimit 모델 작성**

`app/models/control/user_limit.py`:

```python
"""app.user_limits — 관리자의 사용자별 한도 오버라이드 (스펙 §2.3).

모든 한도 컬럼 NULL 허용 — NULL이면 플랜 값 사용(COALESCE는 quota_service가 담당).
monthly_cost_budget_usd는 Phase C에서 강제 — 스키마만 선반영.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class UserLimit(Base):
    __tablename__ = "user_limits"
    __table_args__ = {"schema": APP_SCHEMA}

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{APP_SCHEMA}.users.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    max_groups: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_channels_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_analyses_per_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_video_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monthly_cost_budget_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    min_poll_interval_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 4: AnalysisDelivery에 UNIQUE 추가**

`app/models/control/analysis_delivery.py`의 `__table_args__`를 다음으로 교체:

```python
from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, UniqueConstraint, func
```

```python
    __table_args__ = (
        Index("analysis_deliveries_user_created", "user_id", "created_at"),
        # 같은 사용자가 같은 캐시 분석을 재수신해도 원장 행이 늘지 않는다
        # (재분석은 캐시 복사일 뿐 새 가치가 아님 — 일일 쿼터 과카운트 방지, 스펙 §2.2).
        UniqueConstraint("user_id", "cache_id", name="uq_analysis_deliveries_user_cache"),
        {"schema": APP_SCHEMA},
    )
```

docstring의 `캐시 히트/미스 무관하게 "그룹에 분석이 전달된 사건"을 1행씩 기록한다.` 다음 줄에 추가: `같은 (user_id, cache_id) 재전달은 기록하지 않는다(UNIQUE).`

- [ ] **Step 5: ensure_control_schema에 업그레이드 마이그레이션 추가**

`app/control_db.py`의 모델 임포트 목록에 `user_limit` 추가:

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
        user_limit,
    )
```

`ensure_control_schema` 끝(기존 owner_user_id ALTER 뒤)에 추가:

```python
        # Phase B 업그레이드: 기존 설치의 deliveries 중복 행 정리 후 UNIQUE 추가.
        # create_all은 기존 테이블에 제약을 추가하지 않으므로 명시적으로 처리한다.
        has_uq = (
            await conn.execute(
                text(
                    "SELECT 1 FROM pg_constraint "
                    "WHERE conname = 'uq_analysis_deliveries_user_cache'"
                )
            )
        ).first()
        if has_uq is None:
            # 중복 중 가장 오래된 행(delivery_id 최소)만 유지
            await conn.execute(
                text(
                    f'DELETE FROM "{APP_SCHEMA}".analysis_deliveries a '
                    f'USING "{APP_SCHEMA}".analysis_deliveries b '
                    "WHERE a.user_id = b.user_id AND a.cache_id = b.cache_id "
                    "AND a.delivery_id > b.delivery_id"
                )
            )
            await conn.execute(
                text(
                    f'ALTER TABLE "{APP_SCHEMA}".analysis_deliveries '
                    "ADD CONSTRAINT uq_analysis_deliveries_user_cache "
                    "UNIQUE (user_id, cache_id)"
                )
            )
```

- [ ] **Step 6: record_delivery를 upsert로 전환**

`app/services/analysis_cache_service.py:119-122`를 교체:

```python
async def record_delivery(
    session: AsyncSession, user_id: int, group_id: int, cache_id: int
) -> None:
    """전달 원장 1행 기록. 같은 (user_id, cache_id) 재전달은 무시(쿼터 과카운트 방지)."""
    await session.execute(
        pg_insert(AnalysisDelivery)
        .values(user_id=user_id, group_id=group_id, cache_id=cache_id)
        .on_conflict_do_nothing(constraint="uq_analysis_deliveries_user_cache")
    )
```

- [ ] **Step 7: record_delivery 멱등 테스트 추가 (tests/test_analysis_cache_service.py에 추가)**

기존 파일의 FakeSession 패턴을 확인하고 그 스타일로 추가. FakeSession이 `execute`
호출을 수집한다면 다음처럼 SQL 형태를 검증:

```python
async def test_record_delivery_is_conflict_free():
    """record_delivery가 ON CONFLICT DO NOTHING insert를 발행한다."""
    from app.services.analysis_cache_service import record_delivery

    captured = []

    class _S:
        async def execute(self, stmt):
            captured.append(stmt)

    await record_delivery(_S(), user_id=1, group_id=2, cache_id=3)
    assert len(captured) == 1
    sql = str(captured[0].compile(compile_kwargs={"literal_binds": False}))
    assert "ON CONFLICT" in sql
```

- [ ] **Step 8: 통과 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_control_models.py tests/test_analysis_cache_service.py -q`
Expected: all passed

- [ ] **Step 9: Commit**

```bash
git add app/models/control/user_limit.py app/models/control/analysis_delivery.py app/control_db.py app/services/analysis_cache_service.py tests/test_control_models.py tests/test_analysis_cache_service.py
git commit -m "feat: user_limits 테이블 + deliveries UNIQUE(user_id,cache_id) 전환 — 재분석 과카운트 제거"
```

---

### Task 3: 유효 한도 해석 + DB 집계 검사 (quota_service 완성)

**Files:**
- Modify: `app/services/quota_service.py`
- Test: `tests/test_quota_service.py` (추가)

- [ ] **Step 1: 실패하는 테스트 작성 (tests/test_quota_service.py에 추가)**

```python
import pytest

from app.services.quota_service import (
    QuotaExceeded,
    _merge_limits,
)
from app.models.control.plan import Plan
from app.models.control.user_limit import UserLimit


def _plan(**over):
    base = dict(
        plan_id=1, slug="free", name="Free", max_groups=1, max_channels_total=5,
        max_analyses_per_day=10, max_video_minutes=60,
        monthly_cost_budget_usd=5, min_poll_interval_min=60, is_default=True,
    )
    base.update(over)
    return Plan(**base)


def test_merge_limits_no_override():
    lim = _merge_limits(_plan(), None)
    assert lim.max_groups == 1
    assert lim.min_poll_interval_min == 60
    assert lim.has_override is False
    assert lim.plan_slug == "free"


def test_merge_limits_partial_override():
    ul = UserLimit(user_id=2, max_groups=3, min_poll_interval_min=None)
    lim = _merge_limits(_plan(), ul)
    assert lim.max_groups == 3           # 오버라이드 적용
    assert lim.max_channels_total == 5   # NULL → 플랜 값
    assert lim.min_poll_interval_min == 60
    assert lim.has_override is True


def test_quota_exceeded_detail():
    exc = QuotaExceeded("그룹 한도 초과", limit=1, current=1)
    assert exc.limit == 1 and exc.current == 1
    assert "그룹 한도 초과" in str(exc)
```

- [ ] **Step 2: 실패 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_quota_service.py -q`
Expected: FAIL — `_merge_limits`/`QuotaExceeded` 미정의

- [ ] **Step 3: 구현 — quota_service.py에 추가**

```python
from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.control_db import get_sessionmaker
from app.models.control.analysis_delivery import AnalysisDelivery
from app.models.control.channel_subscription import ChannelSubscription
from app.models.control.group import Group
from app.models.control.plan import Plan
from app.models.control.user import User
from app.models.control.user_limit import UserLimit


class QuotaExceeded(Exception):
    """쿼터 초과. 라우터는 400, 스케줄러는 skip+job log로 변환한다."""

    def __init__(self, detail: str, *, limit: int, current: int) -> None:
        super().__init__(detail)
        self.detail = detail
        self.limit = limit
        self.current = current


def _merge_limits(plan: Plan, override: Optional[UserLimit]) -> EffectiveLimits:
    def pick(field: str) -> int:
        if override is not None:
            v = getattr(override, field)
            if v is not None:
                return int(v)
        return int(getattr(plan, field))

    return EffectiveLimits(
        max_groups=pick("max_groups"),
        max_channels_total=pick("max_channels_total"),
        max_analyses_per_day=pick("max_analyses_per_day"),
        max_video_minutes=pick("max_video_minutes"),
        min_poll_interval_min=pick("min_poll_interval_min"),
        plan_slug=plan.slug,
        plan_name=plan.name,
        has_override=override is not None,
    )


async def effective_limits(session: AsyncSession, user_id: int) -> Optional[EffectiveLimits]:
    """유효 한도. admin/미존재 사용자는 None(무제한)."""
    row = (
        await session.execute(
            select(User, Plan, UserLimit)
            .join(Plan, Plan.plan_id == User.plan_id)
            .outerjoin(UserLimit, UserLimit.user_id == User.user_id)
            .where(User.user_id == user_id)
        )
    ).one_or_none()
    if row is None:
        return None
    user, plan, override = row
    if user.role == "admin":
        return None
    return _merge_limits(plan, override)


async def limits_for_group_owner(group: Group) -> Optional[EffectiveLimits]:
    """스케줄러/그룹 스코프 라우터용: 그룹 owner 기준 한도. None=무제한."""
    if group.owner_user_id is None:
        return None
    async with get_sessionmaker()() as session:
        return await effective_limits(session, group.owner_user_id)


# ── 현재 사용량 집계 ─────────────────────────────────────────────────────────


async def count_owned_groups(session: AsyncSession, user_id: int) -> int:
    return (
        await session.execute(
            select(sa_func.count()).select_from(Group).where(Group.owner_user_id == user_id)
        )
    ).scalar_one()


async def count_owned_channels(session: AsyncSession, user_id: int) -> int:
    """사용자 소유 전 그룹의 채널 합계 — channel_subscriptions 역방향 매핑 사용
    (그룹 스키마 순회 없이 제어 평면 쿼리 1회, B-0b 재사용)."""
    return (
        await session.execute(
            select(sa_func.count())
            .select_from(ChannelSubscription)
            .join(Group, Group.group_id == ChannelSubscription.group_id)
            .where(Group.owner_user_id == user_id)
        )
    ).scalar_one()


async def count_daily_deliveries(session: AsyncSession, user_id: int) -> int:
    from datetime import datetime as _dt

    since = kst_day_start_utc(_dt.now(timezone.utc))
    return (
        await session.execute(
            select(sa_func.count())
            .select_from(AnalysisDelivery)
            .where(AnalysisDelivery.user_id == user_id, AnalysisDelivery.created_at >= since)
        )
    ).scalar_one()


# ── 검사 함수 (초과 시 QuotaExceeded) ────────────────────────────────────────


async def check_group_quota(session: AsyncSession, user_id: int) -> None:
    limits = await effective_limits(session, user_id)
    if limits is None:
        return
    current = await count_owned_groups(session, user_id)
    if current >= limits.max_groups:
        raise QuotaExceeded(
            f"그룹 한도 초과: 현재 {current}개 / 한도 {limits.max_groups}개",
            limit=limits.max_groups, current=current,
        )


async def check_channel_quota(session: AsyncSession, user_id: int) -> None:
    limits = await effective_limits(session, user_id)
    if limits is None:
        return
    current = await count_owned_channels(session, user_id)
    if current >= limits.max_channels_total:
        raise QuotaExceeded(
            f"채널 한도 초과: 현재 {current}개 / 한도 {limits.max_channels_total}개",
            limit=limits.max_channels_total, current=current,
        )


async def check_daily_analysis_quota(session: AsyncSession, user_id: int) -> None:
    limits = await effective_limits(session, user_id)
    if limits is None:
        return
    current = await count_daily_deliveries(session, user_id)
    if current >= limits.max_analyses_per_day:
        raise QuotaExceeded(
            f"일일 분석 한도 초과: 오늘 {current}건 / 한도 {limits.max_analyses_per_day}건 "
            "(KST 자정에 초기화)",
            limit=limits.max_analyses_per_day, current=current,
        )
```

- [ ] **Step 4: 통과 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_quota_service.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add app/services/quota_service.py tests/test_quota_service.py
git commit -m "feat: 유효 한도 해석(COALESCE)·사용량 집계·쿼터 검사 함수 — admin은 무제한"
```

---

### Task 4: 그룹 생성 쿼터 강제 (max_groups)

**Files:**
- Modify: `app/routers/groups.py:35-69` (`create_group`)
- Test: `tests/test_quota_enforcement.py` (신규)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_quota_enforcement.py`:

```python
"""쿼터 강제 지점 라우터 테스트 — quota_service를 monkeypatch로 치환."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.auth import CurrentUser, require_user
from app.services.auth_service import set_users_exist
from app.services.quota_service import QuotaExceeded

USER = CurrentUser(user_id=2, email="u@x.com", display_name="U", role="user")


@pytest.fixture(autouse=True)
def _cleanup():
    set_users_exist(True)
    yield
    set_users_exist(False)
    app.dependency_overrides.clear()


def _as_user():
    async def _dep():
        return USER
    app.dependency_overrides[require_user] = _dep


def test_create_group_quota_exceeded_400(monkeypatch):
    _as_user()

    async def _deny(session, user_id):
        raise QuotaExceeded("그룹 한도 초과: 현재 1개 / 한도 1개", limit=1, current=1)

    monkeypatch.setattr("app.routers.groups.check_group_quota", _deny)
    c = TestClient(app, raise_server_exceptions=False)
    resp = c.post("/api/groups", json={"name": "새 그룹"})
    assert resp.status_code == 400
    assert "그룹 한도 초과" in resp.json()["detail"]
```

- [ ] **Step 2: 실패 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_quota_enforcement.py -q`
Expected: FAIL — `AttributeError: app.routers.groups has no attribute 'check_group_quota'`

- [ ] **Step 3: 구현 — create_group에 검사 삽입**

`app/routers/groups.py` 임포트 추가:

```python
from app.services.quota_service import QuotaExceeded, check_group_quota
```

`create_group`의 `else:` 분기(일반 사용자, 49행 인근) 시작 부분에 삽입:

```python
    else:
        # 일반 사용자: slug/schema 자동 생성 (스펙 §2.8). 입력값은 무시.
        try:
            await check_group_quota(session, user.user_id)
        except QuotaExceeded as e:
            raise HTTPException(status_code=400, detail=e.detail)
        slug = f"u{user.user_id}_{_secrets.token_hex(3)}"
        schema_name = f"youtube_{slug}"
```

- [ ] **Step 4: 통과 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_quota_enforcement.py tests/test_ownership.py -q`
Expected: all passed (기존 그룹 테스트 리그레션 없음)

- [ ] **Step 5: Commit**

```bash
git add app/routers/groups.py tests/test_quota_enforcement.py
git commit -m "feat: 그룹 생성에 max_groups 쿼터 강제 (400)"
```

---

### Task 5: 채널 추가 쿼터 강제 (max_channels_total + min_poll_interval_min)

**Files:**
- Modify: `app/routers/channels.py:33-51` (`add_channel` 도입부)
- Test: `tests/test_quota_enforcement.py` (추가)

- [ ] **Step 1: 실패하는 테스트 작성 (test_quota_enforcement.py에 추가)**

채널 라우터는 그룹 스코프(`get_group_or_404`)이므로 그룹 의존성도 치환한다:

```python
from app.routers.deps import get_group_or_404


class _FakeGroup:
    group_id = 10
    slug = "g1"
    owner_user_id = 2
    schema_name = "youtube_g1"


def _as_group():
    async def _dep():
        return _FakeGroup()
    app.dependency_overrides[get_group_or_404] = _dep


def test_add_channel_quota_exceeded_400(monkeypatch):
    _as_user()
    _as_group()

    async def _limits(group):
        from app.services.quota_service import EffectiveLimits
        return EffectiveLimits(
            max_groups=1, max_channels_total=5, max_analyses_per_day=10,
            max_video_minutes=60, min_poll_interval_min=60,
            plan_slug="free", plan_name="Free", has_override=False,
        )

    async def _deny(session, user_id):
        raise QuotaExceeded("채널 한도 초과: 현재 5개 / 한도 5개", limit=5, current=5)

    monkeypatch.setattr("app.routers.channels.limits_for_group_owner", _limits)
    monkeypatch.setattr("app.routers.channels.check_channel_quota", _deny)
    c = TestClient(app, raise_server_exceptions=False)
    resp = c.post("/api/groups/g1/channels", json={"channel_input": "@x"})
    assert resp.status_code == 400
    assert "채널 한도 초과" in resp.json()["detail"]


def test_add_channel_poll_interval_below_plan_floor_400(monkeypatch):
    _as_user()
    _as_group()

    async def _limits(group):
        from app.services.quota_service import EffectiveLimits
        return EffectiveLimits(
            max_groups=1, max_channels_total=5, max_analyses_per_day=10,
            max_video_minutes=60, min_poll_interval_min=60,
            plan_slug="free", plan_name="Free", has_override=False,
        )

    async def _ok(session, user_id):
        return None

    monkeypatch.setattr("app.routers.channels.limits_for_group_owner", _limits)
    monkeypatch.setattr("app.routers.channels.check_channel_quota", _ok)
    c = TestClient(app, raise_server_exceptions=False)
    resp = c.post(
        "/api/groups/g1/channels",
        json={"channel_input": "@x", "poll_interval_min": 30},
    )
    assert resp.status_code == 400
    assert "폴링 주기" in resp.json()["detail"]
```

- [ ] **Step 2: 실패 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_quota_enforcement.py -q`
Expected: 신규 2건 FAIL (`limits_for_group_owner` 미존재)

- [ ] **Step 3: 구현 — add_channel 도입부에 검사 삽입**

`app/routers/channels.py` 임포트 추가:

```python
from app.control_db import get_sessionmaker
from app.services.quota_service import (
    QuotaExceeded,
    check_channel_quota,
    limits_for_group_owner,
    validate_poll_interval,
)
```

(`get_sessionmaker`는 이미 임포트돼 있음 — 중복 추가 금지.)

`add_channel` 함수 시작(37행 `polling = ...` 앞)에 삽입:

```python
    limits = await limits_for_group_owner(group)
    if limits is not None:
        async with get_sessionmaker()() as qs:
            try:
                await check_channel_quota(qs, group.owner_user_id)
            except QuotaExceeded as e:
                raise HTTPException(status_code=400, detail=e.detail)
        if not validate_poll_interval(limits, payload.poll_interval_min):
            raise HTTPException(
                status_code=400,
                detail=f"폴링 주기는 플랜 하한({limits.min_poll_interval_min}분) 이상이어야 합니다.",
            )
```

- [ ] **Step 4: 통과 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_quota_enforcement.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add app/routers/channels.py tests/test_quota_enforcement.py
git commit -m "feat: 채널 추가에 max_channels_total·플랜 폴링 하한 강제 (400)"
```

---

### Task 6: polling 설정 저장 시 플랜 하한 강제

**Files:**
- Modify: `app/routers/settings.py:48-84` (`put_settings`)
- Test: `tests/test_quota_enforcement.py` (추가)

- [ ] **Step 1: 실패하는 테스트 작성 (test_quota_enforcement.py에 추가)**

```python
def test_put_polling_settings_below_plan_floor_400(monkeypatch):
    _as_user()
    _as_group()

    async def _limits(group):
        from app.services.quota_service import EffectiveLimits
        return EffectiveLimits(
            max_groups=1, max_channels_total=5, max_analyses_per_day=10,
            max_video_minutes=60, min_poll_interval_min=60,
            plan_slug="free", plan_name="Free", has_override=False,
        )

    monkeypatch.setattr("app.routers.settings.limits_for_group_owner", _limits)
    c = TestClient(app, raise_server_exceptions=False)
    resp = c.put(
        "/api/groups/g1/settings/polling",
        json={"items": [{"key": "default_channel_interval_min", "value": "30",
                         "value_type": "int"}]},
    )
    assert resp.status_code == 400
    assert "폴링 주기" in resp.json()["detail"]
```

- [ ] **Step 2: 실패 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_quota_enforcement.py -q`
Expected: 신규 1건 FAIL

- [ ] **Step 3: 구현 — put_settings에 검증 삽입**

`app/routers/settings.py` 임포트 추가:

```python
from app.services.quota_service import limits_for_group_owner, validate_poll_interval
```

`put_settings`의 `_check_category(category)` 직후, `mgr.set_values` 앞에 삽입:

```python
    if category == "polling":
        limits = await limits_for_group_owner(group)
        if limits is not None:
            for item in payload.items:
                if item.key != "default_channel_interval_min":
                    continue
                try:
                    interval = int(item.value)
                except (TypeError, ValueError):
                    continue  # 타입 오류는 기존 set_values 검증에 맡긴다
                if not validate_poll_interval(limits, interval):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"폴링 주기는 플랜 하한({limits.min_poll_interval_min}분) "
                            "이상이어야 합니다."
                        ),
                    )
```

- [ ] **Step 4: 통과 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_quota_enforcement.py tests/test_plan4_endpoints.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add app/routers/settings.py tests/test_quota_enforcement.py
git commit -m "feat: polling 설정 저장에 플랜 폴링 하한 강제 (400)"
```

---

### Task 7: 스케줄러 쿼터 게이트 — 일일 한도·영상 길이·정지 owner 제외

**Files:**
- Modify: `app/services/monitor_service.py` — `_active_groups`(293), `_analyze_group`(782), `_run_analysis`(553)
- Modify: `app/routers/videos.py:467-555` (`instant_analyze_video`)
- Test: `tests/test_scheduler_quota.py` (신규), `tests/test_quota_enforcement.py` (추가)

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/test_scheduler_quota.py`**

```python
"""스케줄러 경로 쿼터 게이트 단위 테스트 (monkeypatch, DB 불필요)."""

from app.services.quota_service import EffectiveLimits

FREE = EffectiveLimits(
    max_groups=1, max_channels_total=5, max_analyses_per_day=10,
    max_video_minutes=60, min_poll_interval_min=60,
    plan_slug="free", plan_name="Free", has_override=False,
)


def test_video_duration_gate_skips_over_limit():
    """duration 초과 영상은 분석 진입 전 skip 판정."""
    from app.services.quota_service import check_video_duration

    assert check_video_duration(FREE, 61 * 60) is False
    assert check_video_duration(FREE, 59 * 60) is True
    assert check_video_duration(None, 10**9) is True  # admin/owner 없음 그룹


async def test_daily_quota_gate_blocks(monkeypatch):
    """일일 한도 도달 시 _daily_quota_ok가 False + 사유 반환."""
    from app.services import monitor_service as ms

    class _G:
        group_id = 10
        slug = "g1"
        owner_user_id = 2

    async def _limits(group):
        return FREE

    async def _count(session, user_id):
        return 10  # 한도와 동일 → 초과

    monkeypatch.setattr(ms, "limits_for_group_owner", _limits)
    monkeypatch.setattr(ms, "count_daily_deliveries", _count)

    ok, reason = await ms._daily_quota_ok(_G())
    assert ok is False
    assert "일일 분석 한도" in reason


async def test_daily_quota_gate_unlimited_owner(monkeypatch):
    from app.services import monitor_service as ms

    class _G:
        group_id = 10
        slug = "g1"
        owner_user_id = None

    async def _limits(group):
        return None

    monkeypatch.setattr(ms, "limits_for_group_owner", _limits)
    ok, reason = await ms._daily_quota_ok(_G())
    assert ok is True and reason == ""
```

- [ ] **Step 2: 실패 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_scheduler_quota.py -q`
Expected: FAIL — `_daily_quota_ok` 미정의

- [ ] **Step 3: 구현 — monitor_service.py**

임포트 추가(파일 상단, 기존 `from app.services.analysis_cache_service import ...` 인근):

```python
from app.models.control.user import User
from app.services.quota_service import (
    check_video_duration,
    count_daily_deliveries,
    limits_for_group_owner,
)
```

`_active_groups`(293행)를 교체 — 정지 owner 그룹을 순회에서 제외(상위 스펙 §8):

```python
async def _active_groups() -> List[Group]:
    sf = get_sessionmaker()
    async with sf() as session:
        stmt = (
            select(Group)
            .outerjoin(User, User.user_id == Group.owner_user_id)
            .where(Group.is_active.is_(True))
            .where(or_(Group.owner_user_id.is_(None), User.status == "active"))
        )
        return list((await session.execute(stmt)).scalars().all())
```

(`or_`는 기존 sqlalchemy 임포트 줄에 추가: `from sqlalchemy import or_, select, update` 형태로.)

`_analyze_group`(782행) 위에 헬퍼 추가:

```python
async def _daily_quota_ok(group: Group) -> tuple[bool, str]:
    """그룹 owner의 일일 분석 한도 검사. (통과 여부, 초과 사유)."""
    limits = await limits_for_group_owner(group)
    if limits is None:
        return True, ""
    async with get_sessionmaker()() as session:
        current = await count_daily_deliveries(session, group.owner_user_id)
    if current >= limits.max_analyses_per_day:
        return False, (
            f"일일 분석 한도 초과: 오늘 {current}건 / 한도 "
            f"{limits.max_analyses_per_day}건 (KST 자정 초기화)"
        )
    return True, ""
```

`_analyze_group`의 `claimed` 확인 직후(`if not claimed: return` 다음)에 삽입:

```python
    ok, reason = await _daily_quota_ok(group)
    if not ok:
        # claim한 영상을 pending으로 되돌리고 skip 기록 — 내일(KST) 자동 재개.
        async with make_session() as sess:
            async with sess.begin():
                await sess.execute(
                    update(Video)
                    .where(Video.video_pk == claimed[0])
                    .values(analysis_status="pending")
                )
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_VIDEO_ANALYZE,
            status=STATUS_SKIP,
            message=reason,
            video_pk=claimed[0],
        )
        return
```

`_run_analysis`(553행)의 영상 메타 조회 직후(`title, channel_pk = ...` 앞)에 duration 게이트 삽입:

```python
    limits = await limits_for_group_owner(group)
    if not check_video_duration(limits, video.duration_seconds):
        assert limits is not None
        async with make_session() as sess:
            async with sess.begin():
                await sess.execute(
                    update(Video)
                    .where(Video.video_pk == video_pk)
                    .values(
                        analysis_status="skipped",
                        analysis_error=(
                            f"영상 길이 초과: {(video.duration_seconds or 0) // 60}분 "
                            f"> 플랜 한도 {limits.max_video_minutes}분"
                        ),
                    )
                )
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_VIDEO_ANALYZE,
            status=STATUS_SKIP,
            message=f"영상 길이 초과(플랜 한도 {limits.max_video_minutes}분)",
            channel_pk=video.channel_pk,
            video_pk=video_pk,
        )
        return
```

`analysis_status="skipped"`는 신규 상태값: `claim_pending_video_pks`(pending만 claim)와
`reset_eligible_failed_videos`(failed만 리셋)가 건드리지 않으므로 재시도 루프에서
자연히 제외된다. 프런트는 미지의 상태를 회색 배지로 표시(기존 fallback) — 별도 작업 불필요.

- [ ] **Step 4: instant_analyze 일일 한도 (test_quota_enforcement.py에 추가)**

```python
def test_instant_analyze_daily_quota_400(monkeypatch):
    _as_user()
    _as_group()

    async def _deny(group):
        return (False, "일일 분석 한도 초과: 오늘 10건 / 한도 10건 (KST 자정 초기화)")

    monkeypatch.setattr("app.routers.videos._instant_quota_check", _deny)
    c = TestClient(app, raise_server_exceptions=False)
    resp = c.post(
        "/api/groups/g1/videos/instant",
        json={"video_url": "https://youtu.be/dQw4w9WgXcQ"},
    )
    assert resp.status_code == 400
    assert "일일 분석 한도" in resp.json()["detail"]
```

- [ ] **Step 5: 구현 — videos.py**

임포트 추가:

```python
from app.services.monitor_service import _daily_quota_ok
```

모듈 수준에 간접 참조 추가(테스트 monkeypatch 지점 — 라우터가 monitor_service
내부명을 직접 참조하지 않게):

```python
async def _instant_quota_check(group) -> tuple[bool, str]:
    return await _daily_quota_ok(group)
```

`instant_analyze_video`의 `video_id` 검증 직후(477행 `polling = ...` 앞)에 삽입:

```python
    ok, reason = await _instant_quota_check(group)
    if not ok:
        raise HTTPException(status_code=400, detail=reason)
```

(duration 게이트는 별도 불필요 — 즉시분석도 `analyze_specific_video → _run_analysis`를
경유하므로 Step 3의 게이트가 커버한다.)

- [ ] **Step 6: 통과 확인 + 리그레션**

Run: `.venv_e2e/bin/python -m pytest tests/test_scheduler_quota.py tests/test_quota_enforcement.py tests/test_cache_integration.py tests/test_stats_refresh.py -q`
Expected: all passed

- [ ] **Step 7: Commit**

```bash
git add app/services/monitor_service.py app/routers/videos.py tests/test_scheduler_quota.py tests/test_quota_enforcement.py
git commit -m "feat: 스케줄러·즉시분석에 일일 한도/영상 길이 게이트 + 정지 owner 순회 제외"
```

---

### Task 8: 관리자 API — 사용자 상태/플랜/한도/임시비번, 플랜 편집, 사용량 요약

**Files:**
- Modify: `app/schemas/admin.py` (스키마 추가)
- Modify: `app/routers/admin.py` (엔드포인트 추가)
- Test: `tests/test_admin_users_api.py` (신규)

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/test_admin_users_api.py`**

```python
"""관리자 사용자 관리 API: 라우트 등록·자기 정지 가드·권한."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.auth import CurrentUser, require_user
from app.services.auth_service import set_users_exist

ADMIN = CurrentUser(user_id=1, email="a@x.com", display_name="A", role="admin")
USER = CurrentUser(user_id=2, email="b@x.com", display_name="B", role="user")


@pytest.fixture(autouse=True)
def _cleanup():
    set_users_exist(True)
    yield
    set_users_exist(False)
    app.dependency_overrides.clear()


def test_admin_user_routes_registered():
    paths = {r.path for r in app.routes}
    assert "/api/admin/users/{user_id}" in paths
    assert "/api/admin/users/{user_id}/limits" in paths
    assert "/api/admin/users/{user_id}/temp-password" in paths
    assert "/api/admin/plans/{plan_id}" in paths


def test_non_admin_forbidden():
    async def _dep():
        return USER
    app.dependency_overrides[require_user] = _dep
    c = TestClient(app, raise_server_exceptions=False)
    assert c.patch("/api/admin/users/2", json={}).status_code == 403
    assert c.put("/api/admin/users/2/limits", json={}).status_code == 403
    assert c.post("/api/admin/users/2/temp-password").status_code == 403
    assert c.patch("/api/admin/plans/1", json={}).status_code == 403


def test_admin_cannot_suspend_self():
    async def _dep():
        return ADMIN
    app.dependency_overrides[require_user] = _dep
    c = TestClient(app, raise_server_exceptions=False)
    resp = c.patch("/api/admin/users/1", json={"status": "suspended"})
    assert resp.status_code == 400
    assert "자기 자신" in resp.json()["detail"]
```

- [ ] **Step 2: 실패 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_admin_users_api.py -q`
Expected: FAIL — 라우트 미등록

- [ ] **Step 3: 스키마 추가 — app/schemas/admin.py 끝에**

```python
class AdminUserPatch(BaseModel):
    status: Optional[str] = None      # 'active' | 'suspended'
    plan_id: Optional[int] = None


class UserLimitsIn(BaseModel):
    """NULL 필드 = 플랜 값 사용."""

    max_groups: Optional[int] = Field(default=None, ge=0)
    max_channels_total: Optional[int] = Field(default=None, ge=0)
    max_analyses_per_day: Optional[int] = Field(default=None, ge=0)
    max_video_minutes: Optional[int] = Field(default=None, ge=0)
    min_poll_interval_min: Optional[int] = Field(default=None, ge=1)
    note: Optional[str] = None


class UserLimitsOut(UserLimitsIn):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    updated_at: datetime


class TempPasswordOut(BaseModel):
    temp_password: str               # 평문은 이 응답에 1회만 노출


class PlanPatch(BaseModel):
    """slug/is_default는 불변(시드 정합성). 한도값만 편집."""

    name: Optional[str] = None
    max_groups: Optional[int] = Field(default=None, ge=0)
    max_channels_total: Optional[int] = Field(default=None, ge=0)
    max_analyses_per_day: Optional[int] = Field(default=None, ge=0)
    max_video_minutes: Optional[int] = Field(default=None, ge=0)
    min_poll_interval_min: Optional[int] = Field(default=None, ge=1)


class AdminUserUsage(BaseModel):
    group_count: int
    channel_count: int
    today_analyses: int
    has_override: bool


class AdminUserOutV2(AdminUserOut):
    usage: Optional[AdminUserUsage] = None
```

- [ ] **Step 4: 엔드포인트 추가 — app/routers/admin.py**

임포트 추가:

```python
import secrets as _secrets

from sqlalchemy import func as sa_func

from app.models.control.analysis_delivery import AnalysisDelivery
from app.models.control.channel_subscription import ChannelSubscription
from app.models.control.group import Group
from app.models.control.user_limit import UserLimit
from app.schemas.admin import (
    AdminUserOutV2,
    AdminUserPatch,
    AdminUserUsage,
    PlanPatch,
    TempPasswordOut,
    UserLimitsIn,
    UserLimitsOut,
)
from app.services.auth_service import hash_password
from app.services.quota_service import kst_day_start_utc
```

기존 `list_users`(GET /users)를 사용량 요약 포함으로 교체:

```python
@router.get("/users", response_model=list[AdminUserOutV2])
async def list_users(session: AsyncSession = Depends(get_session)) -> list[AdminUserOutV2]:
    users = list((await session.execute(select(User).order_by(User.user_id))).scalars().all())

    group_counts = dict(
        (await session.execute(
            select(Group.owner_user_id, sa_func.count())
            .where(Group.owner_user_id.is_not(None))
            .group_by(Group.owner_user_id)
        )).all()
    )
    channel_counts = dict(
        (await session.execute(
            select(Group.owner_user_id, sa_func.count())
            .select_from(ChannelSubscription)
            .join(Group, Group.group_id == ChannelSubscription.group_id)
            .where(Group.owner_user_id.is_not(None))
            .group_by(Group.owner_user_id)
        )).all()
    )
    since = kst_day_start_utc(datetime.now(timezone.utc))
    today_counts = dict(
        (await session.execute(
            select(AnalysisDelivery.user_id, sa_func.count())
            .where(AnalysisDelivery.created_at >= since)
            .group_by(AnalysisDelivery.user_id)
        )).all()
    )
    override_ids = {
        uid for (uid,) in (await session.execute(select(UserLimit.user_id))).all()
    }

    out = []
    for u in users:
        item = AdminUserOutV2.model_validate(u)
        item.usage = AdminUserUsage(
            group_count=group_counts.get(u.user_id, 0),
            channel_count=channel_counts.get(u.user_id, 0),
            today_analyses=today_counts.get(u.user_id, 0),
            has_override=u.user_id in override_ids,
        )
        out.append(item)
    return out
```

신규 엔드포인트들(파일 끝에 추가):

```python
@router.patch("/users/{user_id}", response_model=AdminUserOut)
async def patch_user(
    user_id: int,
    payload: AdminUserPatch,
    admin: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    if payload.status is not None:
        if payload.status not in ("active", "suspended"):
            raise HTTPException(status_code=400, detail="status는 active|suspended만 허용됩니다.")
        if user_id == admin.user_id and payload.status == "suspended":
            raise HTTPException(status_code=400, detail="자기 자신은 정지할 수 없습니다.")
        user.status = payload.status
    if payload.plan_id is not None:
        plan = await session.get(Plan, payload.plan_id)
        if plan is None:
            raise HTTPException(status_code=400, detail="플랜을 찾을 수 없습니다.")
        user.plan_id = payload.plan_id
    await session.commit()
    await session.refresh(user)
    return user


@router.put("/users/{user_id}/limits", response_model=UserLimitsOut)
async def put_user_limits(
    user_id: int,
    payload: UserLimitsIn,
    session: AsyncSession = Depends(get_session),
) -> UserLimit:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    limit = await session.get(UserLimit, user_id)
    if limit is None:
        limit = UserLimit(user_id=user_id)
        session.add(limit)
    for field, value in payload.model_dump().items():
        setattr(limit, field, value)
    await session.commit()
    await session.refresh(limit)
    return limit


@router.delete("/users/{user_id}/limits", status_code=204)
async def delete_user_limits(
    user_id: int, session: AsyncSession = Depends(get_session)
) -> None:
    limit = await session.get(UserLimit, user_id)
    if limit is not None:
        await session.delete(limit)
        await session.commit()


@router.post("/users/{user_id}/temp-password", response_model=TempPasswordOut)
async def issue_temp_password(
    user_id: int, session: AsyncSession = Depends(get_session)
) -> TempPasswordOut:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    temp = _secrets.token_urlsafe(9)
    user.password_hash = hash_password(temp)
    await session.commit()
    return TempPasswordOut(temp_password=temp)


@router.patch("/plans/{plan_id}", response_model=PlanOut)
async def patch_plan(
    plan_id: int, payload: PlanPatch, session: AsyncSession = Depends(get_session)
) -> Plan:
    plan = await session.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="플랜을 찾을 수 없습니다.")
    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(plan, field, value)
    await session.commit()
    await session.refresh(plan)
    return plan
```

`datetime`/`timezone`은 admin.py에 이미 임포트돼 있는지 확인(있음 — 상단 3행).

- [ ] **Step 5: 통과 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_admin_users_api.py tests/test_admin_api.py -q`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add app/schemas/admin.py app/routers/admin.py tests/test_admin_users_api.py
git commit -m "feat: 관리자 API — 사용자 정지/플랜/한도 오버라이드/임시비번·플랜 편집·사용량 요약"
```

---

### Task 9: 사용자 사용량 API — GET /api/me/usage

**Files:**
- Modify: `app/routers/auth.py` (me_router 추가)
- Modify: `app/main.py` (include_router — 기존 auth router include 지점 바로 아래)
- Modify: `app/schemas/auth.py` (MyUsageResponse 추가)
- Test: `tests/test_me_usage.py` (신규)

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/test_me_usage.py`**

```python
"""GET /api/me/usage — 본인 플랜·한도·사용량."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.auth import CurrentUser, require_user
from app.services.auth_service import set_users_exist

USER = CurrentUser(user_id=2, email="b@x.com", display_name="B", role="user")


@pytest.fixture(autouse=True)
def _cleanup():
    set_users_exist(True)
    yield
    set_users_exist(False)
    app.dependency_overrides.clear()


def test_route_registered():
    assert "/api/me/usage" in {r.path for r in app.routes}


def test_me_usage_shape(monkeypatch):
    async def _dep():
        return USER
    app.dependency_overrides[require_user] = _dep

    from app.services.quota_service import EffectiveLimits

    async def _limits(session, user_id):
        return EffectiveLimits(
            max_groups=1, max_channels_total=5, max_analyses_per_day=10,
            max_video_minutes=60, min_poll_interval_min=60,
            plan_slug="free", plan_name="Free", has_override=False,
        )

    async def _n(session, user_id):
        return 3

    monkeypatch.setattr("app.routers.auth.effective_limits", _limits)
    monkeypatch.setattr("app.routers.auth.count_owned_groups", _n)
    monkeypatch.setattr("app.routers.auth.count_owned_channels", _n)
    monkeypatch.setattr("app.routers.auth.count_daily_deliveries", _n)

    c = TestClient(app, raise_server_exceptions=False)
    data = c.get("/api/me/usage").json()
    assert data["plan_name"] == "Free"
    assert data["limits"]["max_groups"] == 1
    assert data["usage"]["group_count"] == 3
```

- [ ] **Step 2: 실패 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_me_usage.py -q`
Expected: FAIL — 라우트 미등록

- [ ] **Step 3: 스키마 — app/schemas/auth.py 끝에 추가**

```python
class MyLimits(BaseModel):
    max_groups: int
    max_channels_total: int
    max_analyses_per_day: int
    max_video_minutes: int
    min_poll_interval_min: int


class MyUsage(BaseModel):
    group_count: int
    channel_count: int
    today_analyses: int


class MyUsageResponse(BaseModel):
    plan_name: str
    plan_slug: str
    unlimited: bool = False          # admin/개발 모드
    limits: Optional[MyLimits] = None
    usage: MyUsage
```

(`Optional` 임포트가 없으면 추가.)

- [ ] **Step 4: 라우터 — app/routers/auth.py 끝에 추가**

임포트 추가:

```python
from app.schemas.auth import LoginRequest, MeResponse, MyLimits, MyUsage, MyUsageResponse, SignupRequest, UserOut
from app.services.quota_service import (
    count_daily_deliveries,
    count_owned_channels,
    count_owned_groups,
    effective_limits,
)
```

```python
me_router = APIRouter(prefix="/api/me", tags=["me"])


@me_router.get("/usage", response_model=MyUsageResponse)
async def my_usage(
    user: CurrentUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> MyUsageResponse:
    limits = await effective_limits(session, user.user_id)
    usage = MyUsage(
        group_count=await count_owned_groups(session, user.user_id),
        channel_count=await count_owned_channels(session, user.user_id),
        today_analyses=await count_daily_deliveries(session, user.user_id),
    )
    if limits is None:
        # admin 또는 개발 모드 — 무제한
        return MyUsageResponse(
            plan_name="Unlimited", plan_slug="unlimited", unlimited=True, usage=usage
        )
    return MyUsageResponse(
        plan_name=limits.plan_name,
        plan_slug=limits.plan_slug,
        limits=MyLimits(
            max_groups=limits.max_groups,
            max_channels_total=limits.max_channels_total,
            max_analyses_per_day=limits.max_analyses_per_day,
            max_video_minutes=limits.max_video_minutes,
            min_poll_interval_min=limits.min_poll_interval_min,
        ),
        usage=usage,
    )
```

`app/main.py`에서 auth router include 지점을 찾아(`grep -n "auth" app/main.py`)
바로 아래에 추가:

```python
from app.routers.auth import me_router
app.include_router(me_router)
```

(기존 import 스타일에 맞춰 상단 임포트로 배치.)

- [ ] **Step 5: 통과 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_me_usage.py tests/test_auth.py -q`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add app/routers/auth.py app/schemas/auth.py app/main.py tests/test_me_usage.py
git commit -m "feat: GET /api/me/usage — 본인 플랜·유효 한도·사용량"
```

---

### Task 10: 프런트 — 관리자 콘솔 확장

**Files:**
- Modify: `frontend/src/api/admin.ts`
- Modify: `frontend/src/pages/Admin.tsx`

- [ ] **Step 1: API 클라이언트 확장 — admin.ts**

`AdminUser`에 usage 필드 추가 + 새 타입/메서드:

```typescript
export interface AdminUserUsage {
  group_count: number
  channel_count: number
  today_analyses: number
  has_override: boolean
}

// AdminUser에 추가:
//   usage: AdminUserUsage | null

export interface UserLimits {
  max_groups: number | null
  max_channels_total: number | null
  max_analyses_per_day: number | null
  max_video_minutes: number | null
  min_poll_interval_min: number | null
  note: string | null
}

// adminApi에 추가:
export const adminApi = {
  // ...기존 메서드 유지...
  patchUser: (userId: number, body: { status?: string; plan_id?: number }) =>
    rootApi.patch<AdminUser>(`/admin/users/${userId}`, body),
  putUserLimits: (userId: number, body: UserLimits) =>
    rootApi.put<UserLimits & { user_id: number }>(`/admin/users/${userId}/limits`, body),
  deleteUserLimits: (userId: number) =>
    rootApi.del<void>(`/admin/users/${userId}/limits`),
  issueTempPassword: (userId: number) =>
    rootApi.post<{ temp_password: string }>(`/admin/users/${userId}/temp-password`),
  patchPlan: (planId: number, body: Partial<Omit<PlanInfo, 'plan_id' | 'slug' | 'is_default'>>) =>
    rootApi.patch<PlanInfo>(`/admin/plans/${planId}`, body),
}
```

`rootApi`(frontend/src/api/http.ts:46)에는 `put`이 없다(확인 완료: get/post/patch/del만).
`rootApi` 객체에 다음 메서드를 추가:

```typescript
  put: <T>(path: string, body: unknown) =>
    request<T>(`/api${path}`, { method: 'PUT', body: JSON.stringify(body) }),
```

- [ ] **Step 2: Admin.tsx 확장**

기존 사용자 테이블에 컬럼·액션 추가. 기존 코드 스타일(인라인 핸들러, load() 재호출) 유지:

- 컬럼 추가: 사용량(`{u.usage?.group_count}그룹 · {u.usage?.channel_count}채널 · 오늘 {u.usage?.today_analyses}건`), 오버라이드 뱃지(`u.usage?.has_override && <span>한도 조정됨</span>`)
- 행 액션:
  - 정지/해제 토글: `adminApi.patchUser(u.user_id, { status: u.status === 'active' ? 'suspended' : 'active' })` → `load()`
  - 플랜 변경: `<select>`로 plans 중 선택 → `patchUser(u.user_id, { plan_id })`
  - 임시 비번: `issueTempPassword` 호출 후 반환된 `temp_password`를 1회 표시(기존 `createdUrl` 표시 패턴 재사용)
  - 한도 편집: 행 클릭 시 인라인 폼(5개 숫자 입력 + note, 빈칸=플랜 값) → `putUserLimits` / "플랜 값으로 초기화" 버튼 → `deleteUserLimits`
- 플랜 섹션 추가: plans 테이블 + 각 행 편집 폼 → `patchPlan`
- 에러는 기존 `setError((e as Error).message)` 패턴.

- [ ] **Step 3: 빌드 확인**

Run: `cd frontend && npm run build`
Expected: tsc + vite 성공, 타입 에러 없음

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/admin.ts frontend/src/pages/Admin.tsx
git commit -m "feat: 관리자 콘솔 — 정지/플랜/한도 오버라이드/임시비번/플랜 편집 UI"
```

---

### Task 11: 프런트 — 마이페이지

**Files:**
- Create: `frontend/src/pages/MyPage.tsx`
- Create: `frontend/src/api/me.ts`
- Modify: `frontend/src/App.tsx:52` (라우트 추가)
- Modify: `frontend/src/components/Layout.tsx:57,85` (마이페이지 링크)

- [ ] **Step 1: API 클라이언트 — me.ts**

```typescript
import { rootApi } from './http'

export interface MyUsageResponse {
  plan_name: string
  plan_slug: string
  unlimited: boolean
  limits: {
    max_groups: number
    max_channels_total: number
    max_analyses_per_day: number
    max_video_minutes: number
    min_poll_interval_min: number
  } | null
  usage: {
    group_count: number
    channel_count: number
    today_analyses: number
  }
}

export const meApi = {
  usage: () => rootApi.get<MyUsageResponse>('/me/usage'),
}
```

(`rootApi` 경로 prefix가 `/api`인지 http.ts에서 확인 — admin.ts가 `/admin/users`로 호출하므로 동일 패턴 `/me/usage`.)

- [ ] **Step 2: MyPage.tsx**

Admin.tsx의 스타일(max-w, bg-white rounded-xl 카드)을 따라 작성:

```tsx
import { useEffect, useState } from 'react'
import { meApi, type MyUsageResponse } from '../api/me'

export default function MyPage() {
  const [data, setData] = useState<MyUsageResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    meApi.usage().then(setData).catch((e) => setError((e as Error).message))
  }, [])

  return (
    <div className="max-w-2xl mx-auto p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900">마이페이지</h1>
        <a href="/" className="text-sm text-blue-600 hover:underline">← 앱으로</a>
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
      {data && (
        <section className="bg-white rounded-xl shadow-sm p-4 space-y-3">
          <h2 className="font-semibold text-gray-800">
            플랜: {data.plan_name}
            {data.unlimited && <span className="ml-2 text-xs text-gray-500">무제한</span>}
          </h2>
          <table className="w-full text-sm">
            <tbody>
              <tr><td className="py-1 text-gray-500">그룹</td>
                <td>{data.usage.group_count}{data.limits && ` / ${data.limits.max_groups}`}</td></tr>
              <tr><td className="py-1 text-gray-500">채널</td>
                <td>{data.usage.channel_count}{data.limits && ` / ${data.limits.max_channels_total}`}</td></tr>
              <tr><td className="py-1 text-gray-500">오늘 분석</td>
                <td>{data.usage.today_analyses}{data.limits && ` / ${data.limits.max_analyses_per_day}`}
                  <span className="text-xs text-gray-400 ml-1">(KST 자정 초기화)</span></td></tr>
              {data.limits && (<>
                <tr><td className="py-1 text-gray-500">영상 길이 한도</td>
                  <td>{data.limits.max_video_minutes}분</td></tr>
                <tr><td className="py-1 text-gray-500">폴링 주기 하한</td>
                  <td>{data.limits.min_poll_interval_min}분</td></tr>
              </>)}
            </tbody>
          </table>
        </section>
      )}
    </div>
  )
}
```

- [ ] **Step 3: 라우트·진입점**

`App.tsx`의 `/admin` 라우트 아래에 추가:

```tsx
<Route path="/me" element={<MyPage />} />
```

진입점은 `frontend/src/components/Layout.tsx` — 로그아웃 버튼이 두 곳(57행, 85행:
데스크톱/모바일 헤더)에 있다. 각 로그아웃 버튼 바로 앞에 링크 추가:

```tsx
<a href="/me" className="text-xs px-2 py-1 border border-gray-300 rounded-lg hover:bg-gray-50">마이페이지</a>
```

- [ ] **Step 4: 빌드 확인**

Run: `cd frontend && npm run build`
Expected: 성공

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/MyPage.tsx frontend/src/api/me.ts frontend/src/App.tsx frontend/src/components/
git commit -m "feat: 마이페이지 — 플랜·유효 한도·당일 사용량"
```

---

### Task 12: 전체 리그레션 + 문서 갱신

**Files:**
- Modify: `docs/superpowers/specs/2026-07-03-multi-tenant-design.md` (Phase 표 §7, §10 표)

- [ ] **Step 1: 전체 테스트**

Run: `.venv_e2e/bin/python -m pytest tests/ -q`
Expected: all passed (기존 220 + 신규 전부). 실패 시 원인 수정 후 재실행.

- [ ] **Step 2: 프런트 테스트/빌드**

Run: `cd frontend && npm run test && npm run build`
Expected: 성공

- [ ] **Step 3: 상위 스펙 갱신**

`2026-07-03-multi-tenant-design.md`:
- §7 Phase 표의 B 행에 `(구현 완료 2026-XX-XX — 비용 한도는 C로 이연)` 주석 추가(실제 날짜로).
- §10 표의 "전달 원장 중복 카운트" 행 수정 방향 칸에 `완료 — UNIQUE(user_id, cache_id) + ON CONFLICT DO NOTHING (Phase B)` 기입.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-07-03-multi-tenant-design.md
git commit -m "docs: Phase B 구현 반영 — 쿼터 강제·deliveries UNIQUE 완료 표기"
```

---

## 실 DB E2E (구현 완료 후, 별도 세션 체크포인트)

테스트 DB `100.115.13.102`(`.env`의 `CONTROL_DATABASE_URL`), 기존 그룹 `e2e_a`/`e2e_b` 재활용.
**주의: `postgres-ytdb` MCP는 프로덕션 연결 — 절대 사용 금지. 앱 자체 엔진으로만 접근**
(`PYTHONPATH=. .venv_e2e/bin/python script.py`, httpx.AsyncClient + ASGITransport 패턴 —
TestClient는 이벤트루프 충돌).

1. 부팅 마이그레이션: `ensure_control_schema()` 실행 → user_limits 생성 +
   deliveries UNIQUE 추가(기존 중복 있으면 정리) 확인.
2. free 플랜 사용자 생성(초대 플로우 또는 직접 INSERT) → 그룹 1개 생성 → 2번째 그룹 400.
3. 채널 5개 등록 → 6번째 400. poll_interval_min=30 등록 시도 → 400.
4. max_analyses_per_day를 오버라이드로 1로 설정 → 분석 1건 후 스케줄러 skip(job log 확인),
   즉시분석 400. 오버라이드 해제 → 재개.
5. 같은 캐시 재분석 → deliveries 행 불변(UNIQUE 동작 실증).
6. 관리자 API: 정지 → 그 사용자 요청 403 + 스케줄러 순회 제외. 임시 비번 발급 → 새 비번 로그인.
7. admin 그룹(owner admin)은 전부 무제한 통과 확인.
