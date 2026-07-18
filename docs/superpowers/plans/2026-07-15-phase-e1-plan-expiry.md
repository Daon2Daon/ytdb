# Phase E-1 Implementation Plan: 초대 기반 B2B 최소 유료화 — pro 플랜·만료일·자동 강등

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** pro 플랜 시드 + `users.plan_expires_at` 만료 관리 + 30분 주기 자동 강등·D-7 텔레그램 임박 알림 + 관리자 만료일 설정/마이페이지 표시.

**Architecture:** 만료는 DB의 단일 진실(`plan_id` 실제 UPDATE — A안 명시적 강등). 신규 `plan_expiry_service`가 후보 로드→분류(순수 함수)→강등/알림을 소유하고, 스케줄러 잡 1개가 30분마다 호출. 알림은 D-1 공용 봇의 사용자 첫 active destination으로 best-effort.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, APScheduler(기존 패턴), pytest(`.venv_e2e/bin/python -m pytest`, 오프라인 필수 — 원격 DB 가정 금지), React+TS.

**스펙:** `docs/superpowers/specs/2026-07-15-phase-e1-manual-billing-plan-expiry-design.md`

**브랜치:** `feat/phase-e1-plan-expiry` (main에서 분기)

---

## File Structure

| 파일 | 역할 |
|------|------|
| Modify `app/models/control/user.py` | `plan_expires_at`·`plan_expiry_notified_at` 컬럼 |
| Modify `app/control_db.py` | users ALTER ADD COLUMN IF NOT EXISTS ×2 |
| Modify `app/services/auth_service.py` | `PLAN_SEEDS`에 pro 추가 |
| Create `app/services/plan_expiry_service.py` | 후보 로드·분류·강등·알림 단일 소유 |
| Modify `app/services/scheduler.py` | `JOB_PLAN_EXPIRY` 30분 잡 |
| Modify `app/routers/admin.py`, `app/schemas/admin.py` | PATCH user 만료일 설정·해제 + Out 노출 |
| Modify `app/routers/auth.py`, `app/schemas/auth.py` | me/usage에 `plan_expires_at` |
| Modify `frontend/src/api/admin.ts`, `frontend/src/api/me.ts`, `frontend/src/pages/Admin.tsx`, `frontend/src/pages/MyPage.tsx` | 만료일 편집·표시 |
| Test `tests/test_plan_expiry.py` (신규), 기존 `tests/test_admin_users_api.py`·`tests/test_me_usage.py` 확장 | |

베이스라인: `.venv_e2e/bin/python -m pytest tests/ -q` → **337 passed** (main 기준).

---

### Task 0: 브랜치 생성

- [ ] `git checkout main && git checkout -b feat/phase-e1-plan-expiry`

---

### Task 1: users 컬럼 2개 + 부팅 마이그레이션

**Files:** Modify `app/models/control/user.py`, `app/control_db.py` (groups ALTER 블록 근처, ~88행); Test: `tests/test_plan_expiry.py` (신규)

- [ ] **Step 1: 실패하는 테스트** — `tests/test_plan_expiry.py`:

```python
"""Phase E-1 — 플랜 만료·자동 강등 (스펙 2026-07-15-phase-e1)."""

from datetime import datetime, timedelta, timezone


def test_user_model_has_expiry_columns():
    from app.models.control.user import User

    t = User.__table__
    assert t.c.plan_expires_at.nullable is True
    assert t.c.plan_expiry_notified_at.nullable is True
```

- [ ] **Step 2: 실행 → FAIL** (`.venv_e2e/bin/python -m pytest tests/test_plan_expiry.py -v`)

- [ ] **Step 3: 구현** — `user.py` 컬럼 추가(`last_login_at` 위):

```python
    # Phase E-1: 유료 플랜 만료 관리. NULL=무기한(free·unlimited·기존 사용자).
    plan_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 만료 임박(D-7) 알림 1회 가드. 플랜/만료일 변경 시 리셋.
    plan_expiry_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

`control_db.py`의 groups ALTER와 같은 블록에(기존 패턴 그대로):

```python
        for col in ("plan_expires_at", "plan_expiry_notified_at"):
            await conn.execute(
                text(
                    f'ALTER TABLE "{APP_SCHEMA}".users '
                    f'ADD COLUMN IF NOT EXISTS {col} TIMESTAMPTZ'
                )
            )
```

(control_db의 기존 ALTER 실행 방식 — conn/session 어느 쪽인지 88행 주변을 읽고 동일하게. `text` import 확인.)

- [ ] **Step 4: PASS + 전체 회귀** (337→338)
- [ ] **Step 5: Commit** — `feat: users.plan_expires_at·plan_expiry_notified_at — 만료 관리 기반 컬럼`

---

### Task 2: pro 플랜 시드

**Files:** Modify `app/services/auth_service.py:60-74` (`PLAN_SEEDS`); Test: `tests/test_plan_expiry.py` 확장

- [ ] **Step 1: 실패하는 테스트** (append):

```python
def test_plan_seeds_include_pro():
    from app.services.auth_service import PLAN_SEEDS

    pro = next(s for s in PLAN_SEEDS if s["slug"] == "pro")
    assert pro["is_default"] is False
    assert (pro["max_groups"], pro["max_channels_total"]) == (3, 30)
    assert (pro["max_analyses_per_day"], pro["max_video_minutes"]) == (100, 120)
    assert pro["min_poll_interval_min"] == 10
    # free가 여전히 유일한 기본 플랜
    assert [s["slug"] for s in PLAN_SEEDS if s["is_default"]] == ["free"]
```

- [ ] **Step 2: FAIL 확인**
- [ ] **Step 3: 구현** — `PLAN_SEEDS`에 free 다음 항목 추가:

```python
    {
        "slug": "pro", "name": "Pro", "max_groups": 3, "max_channels_total": 30,
        "max_analyses_per_day": 100, "max_video_minutes": 120,
        "monthly_cost_budget_usd": "30.0", "min_poll_interval_min": 10, "is_default": False,
    },
```

(기존 시드 로직이 slug 기준 멱등이라 기존 배포에도 안전하게 신규 행만 추가됨 — 코드 변경 불필요.)

- [ ] **Step 4: PASS + 전체 회귀**
- [ ] **Step 5: Commit** — `feat: pro 플랜 시드 — 3그룹/30채널/일100건/120분/$30/폴링 10분 (B2B 단일 유료 플랜)`

---

### Task 3: plan_expiry_service — 분류·강등·알림

**Files:** Create `app/services/plan_expiry_service.py`; Test: `tests/test_plan_expiry.py` 확장

- [ ] **Step 1: 실패하는 테스트** (append):

```python
NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def _cand(expires_delta_days, notified=False):
    from app.services.plan_expiry_service import ExpiryCandidate

    return ExpiryCandidate(
        user_id=2, email="u@x.com",
        plan_expires_at=NOW + timedelta(days=expires_delta_days),
        plan_expiry_notified_at=NOW - timedelta(days=1) if notified else None,
    )


def test_classify_boundaries():
    from app.services.plan_expiry_service import classify

    assert classify(_cand(-0.01), NOW) == "demote"       # 만료 지남
    assert classify(_cand(3), NOW) == "notify"            # 7일 이내·미통지
    assert classify(_cand(3, notified=True), NOW) == "none"  # 이미 통지
    assert classify(_cand(8), NOW) == "none"              # 7일 초과
    assert classify(_cand(7), NOW) == "notify"            # 경계: 정확히 7일


async def test_run_once_demotes_and_notifies_with_isolation(monkeypatch):
    from app.services import plan_expiry_service as pes

    cands = [_cand(-1), _cand(3), _cand(8)]
    cands[0] = pes.ExpiryCandidate(10, "expired@x.com", cands[0].plan_expires_at, None)
    cands[1] = pes.ExpiryCandidate(11, "soon@x.com", cands[1].plan_expires_at, None)
    cands[2] = pes.ExpiryCandidate(12, "far@x.com", cands[2].plan_expires_at, None)
    actions = {"demoted": [], "notified": []}

    async def fake_load():
        return cands

    async def fake_demote(user_id):
        if user_id == 10:
            actions["demoted"].append(user_id)

    async def fake_mark(user_id):
        actions["notified"].append(user_id)

    async def fake_send(user_id, text):
        if user_id == 10:
            raise RuntimeError("텔레그램 실패")  # 알림 실패가 강등을 못 막는다

    monkeypatch.setattr(pes, "_load_candidates", fake_load)
    monkeypatch.setattr(pes, "_demote_user", fake_demote)
    monkeypatch.setattr(pes, "_mark_notified", fake_mark)
    monkeypatch.setattr(pes, "_send_user_telegram", fake_send)
    monkeypatch.setattr(pes, "_now", lambda: NOW)

    await pes.run_plan_expiry_once()
    assert actions["demoted"] == [10]     # 만료자만 강등
    assert actions["notified"] == [11]    # 임박자만 통지 마킹 (far=12 제외)
```

- [ ] **Step 2: FAIL 확인**
- [ ] **Step 3: 구현** — `app/services/plan_expiry_service.py`:

```python
"""유료 플랜 만료 관리 (스펙 E-1 §2).

- 강등: 만료된 비기본·비unlimited 사용자 → 기본(is_default) 플랜으로 UPDATE.
  DB의 plan_id가 단일 진실 — quota_service·관리자 화면·마이페이지 자동 반영.
- 임박 알림(D-7): plan_expiry_notified_at NULL 가드로 1회만.
- 알림은 공용 봇의 첫 active destination으로 best-effort — 실패가 강등을 안 막는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.control_db import get_sessionmaker
from app.models.control.plan import Plan
from app.models.control.telegram_destination import TelegramDestination
from app.models.control.user import User

NOTIFY_BEFORE_DAYS = 7


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ExpiryCandidate:
    user_id: int
    email: str
    plan_expires_at: datetime
    plan_expiry_notified_at: datetime | None


def classify(cand: ExpiryCandidate, now: datetime) -> str:
    """'demote' | 'notify' | 'none'. 경계: 만료 시각 지남=강등, 7일 이내(포함)=임박."""
    if cand.plan_expires_at < now:
        return "demote"
    if (
        cand.plan_expires_at <= now + timedelta(days=NOTIFY_BEFORE_DAYS)
        and cand.plan_expiry_notified_at is None
    ):
        return "notify"
    return "none"


async def _load_candidates() -> list[ExpiryCandidate]:
    """만료일이 설정된 비기본·비unlimited 플랜 사용자."""
    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(
                    User.user_id, User.email, User.plan_expires_at, User.plan_expiry_notified_at
                )
                .join(Plan, Plan.plan_id == User.plan_id)
                .where(
                    Plan.is_default.is_(False),
                    Plan.slug != "unlimited",
                    User.plan_expires_at.isnot(None),
                )
            )
        ).all()
    return [ExpiryCandidate(*r) for r in rows]


async def _demote_user(user_id: int) -> None:
    """기본 플랜으로 강등. plan_expires_at은 이력으로 보존."""
    async with get_sessionmaker()() as session:
        async with session.begin():
            default_id = (
                await session.execute(select(Plan.plan_id).where(Plan.is_default.is_(True)))
            ).scalar_one()
            await session.execute(
                update(User).where(User.user_id == user_id).values(plan_id=default_id)
            )


async def _mark_notified(user_id: int) -> None:
    async with get_sessionmaker()() as session:
        async with session.begin():
            await session.execute(
                update(User)
                .where(User.user_id == user_id)
                .values(plan_expiry_notified_at=_now())
            )


async def _send_user_telegram(user_id: int, text: str) -> None:
    """사용자 첫 active destination으로 발송. 미연결/실패는 호출부에서 삼킨다."""
    from app.services.global_settings import get_global_telegram_bot_token
    from app.services.telegram_link_service import _send_bot_message

    bot_token = await get_global_telegram_bot_token()
    if not bot_token:
        return
    async with get_sessionmaker()() as session:
        chat_id = (
            await session.execute(
                select(TelegramDestination.chat_id)
                .where(
                    TelegramDestination.user_id == user_id,
                    TelegramDestination.is_active.is_(True),
                )
                .order_by(TelegramDestination.dest_id)
                .limit(1)
            )
        ).scalar_one_or_none()
    if chat_id is None:
        return
    await _send_bot_message(bot_token, chat_id, text)


async def run_plan_expiry_once() -> None:
    """만료 틱: 강등 → 강등 알림, 임박 → 알림+마킹. 사용자별 실패 격리."""
    now = _now()
    for cand in await _load_candidates():
        action = classify(cand, now)
        if action == "none":
            continue
        try:
            if action == "demote":
                await _demote_user(cand.user_id)
                print(f"[plan-expiry] {cand.email} 플랜 만료 → 기본 플랜 강등")
                try:
                    await _send_user_telegram(
                        cand.user_id,
                        "이용 중인 플랜이 만료되어 Free로 전환되었습니다. "
                        "연장을 원하시면 관리자에게 문의해 주세요.",
                    )
                except Exception:
                    pass  # 알림 실패가 강등을 못 막는다 (스펙 §2)
            else:  # notify
                await _mark_notified(cand.user_id)
                d = cand.plan_expires_at.astimezone(timezone.utc).date().isoformat()
                try:
                    await _send_user_telegram(
                        cand.user_id,
                        f"이용 중인 플랜이 {d}에 만료될 예정입니다. "
                        "연장을 원하시면 관리자에게 문의해 주세요.",
                    )
                except Exception:
                    pass
        except Exception as e:  # noqa: BLE001 — 사용자 단위 격리
            print(f"[plan-expiry] {cand.email} 처리 실패(계속): {e}")
```

주의: 테스트가 `pes._send_user_telegram`을 monkeypatch하고 그 예외가 삼켜져야 하므로, 발송 호출은 위처럼 **개별 try/except로 감싼 상태**를 유지할 것. `_mark_notified`가 발송보다 먼저다(발송 실패해도 재발송 폭주 없음 — 마이페이지가 폴백).

- [ ] **Step 4: PASS + 전체 회귀**
- [ ] **Step 5: Commit** — `feat: plan_expiry_service — 만료 분류·기본플랜 강등·D-7 임박 알림(best-effort)`

---

### Task 4: 스케줄러 잡 등록 (30분)

**Files:** Modify `app/services/scheduler.py` (상수 블록 + `setup_jobs`); Test: `tests/test_plan_expiry.py` 확장

- [ ] **Step 1: 실패하는 테스트** (append):

```python
def test_plan_expiry_job_registered():
    from app.services import scheduler as sch

    s = sch.setup_jobs()
    job = s.get_job(sch.JOB_PLAN_EXPIRY)
    assert job is not None
```

- [ ] **Step 2: FAIL 확인** (`AttributeError: JOB_PLAN_EXPIRY`)
- [ ] **Step 3: 구현** — 기존 JOB_* 상수 블록에 `JOB_PLAN_EXPIRY = "plan_expiry"` 추가, import `from app.services.plan_expiry_service import run_plan_expiry_once`, `setup_jobs`에 기존 잡과 동일 패턴으로:

```python
    scheduler.add_job(
        run_plan_expiry_once,        # E-1: 유료 플랜 만료 강등·임박 알림
        trigger="interval",
        minutes=30,
        id=JOB_PLAN_EXPIRY,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
```

- [ ] **Step 4: PASS + 전체 회귀**
- [ ] **Step 5: Commit** — `feat: 플랜 만료 틱 스케줄러 잡 — 30분 주기`

---

### Task 5: 관리자 PATCH 만료일 + Out 노출

**Files:** Modify `app/schemas/admin.py` (`AdminUserOut`, `AdminUserPatch`), `app/routers/admin.py::patch_user`; Test: `tests/test_admin_users_api.py` 확장

- [ ] **Step 1: 실패하는 테스트** (append; 파일의 ADMIN/`_cleanup` 픽스처 재사용):

```python
async def test_patch_user_sets_and_clears_expiry(monkeypatch):
    """만료일 설정·해제 + notified 리셋. 세션은 가짜로 대체(실 SQL은 E2E)."""
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from app.routers import admin as admin_router
    from app.routers.deps import get_session

    fake_user = SimpleNamespace(
        user_id=2, email="b@x.com", display_name="B", role="user", status="active",
        plan_id=1, plan_expires_at=None, plan_expiry_notified_at=datetime.now(timezone.utc),
        last_login_at=None, created_at=datetime.now(timezone.utc),
    )

    class FakeSession:
        async def get(self, model, pk):
            return fake_user

        async def commit(self):
            pass

        async def refresh(self, obj):
            pass

    async def _dep():
        return ADMIN

    async def _sess():
        return FakeSession()

    app.dependency_overrides[require_user] = _dep
    app.dependency_overrides[get_session] = _sess
    c = TestClient(app, raise_server_exceptions=False)

    # 설정: 미래 시각 → 반영 + notified 리셋
    r = c.patch("/api/admin/users/2", json={"plan_expires_at": "2026-08-15T00:00:00Z"})
    assert r.status_code == 200
    assert fake_user.plan_expires_at is not None
    assert fake_user.plan_expiry_notified_at is None
    assert r.json()["plan_expires_at"] is not None

    # 해제: 명시적 null → NULL + notified 리셋 유지
    fake_user.plan_expiry_notified_at = datetime.now(timezone.utc)
    r = c.patch("/api/admin/users/2", json={"plan_expires_at": None})
    assert r.status_code == 200
    assert fake_user.plan_expires_at is None
    assert fake_user.plan_expiry_notified_at is None

    # 필드 생략 → 변경 없음 (tri-state)
    fake_user.plan_expires_at = "sentinel"
    r = c.patch("/api/admin/users/2", json={"status": "active"})
    assert fake_user.plan_expires_at == "sentinel"
```

(주의: `get_session` 의존성의 실제 임포트 경로는 admin.py 상단에서 확인 — `app.routers.deps`가 아니면 실제 위치로 교체. dependency_overrides 키는 **엔드포인트가 참조하는 그 함수 객체**여야 한다.)

- [ ] **Step 2: FAIL 확인**
- [ ] **Step 3: 구현**

`app/schemas/admin.py`:

```python
class AdminUserOut(BaseModel):
    ...  # 기존 필드 유지
    plan_expires_at: Optional[datetime] = None   # E-1: NULL=무기한


class AdminUserPatch(BaseModel):
    status: Optional[str] = None      # 'active' | 'suspended'
    plan_id: Optional[int] = None
    plan_expires_at: Optional[datetime] = None   # 생략=변경 없음, null=해제 (model_fields_set로 구분)
```

`app/routers/admin.py::patch_user` — plan_id 처리 블록 뒤에:

```python
    # E-1: 만료일 tri-state — 생략(변경 없음) / null(해제) / 값(설정). 어느 쪽이든
    # 플랜 수명이 바뀌므로 임박 알림 가드를 리셋해 다음 주기 알림을 다시 연다.
    if "plan_expires_at" in payload.model_fields_set:
        user.plan_expires_at = payload.plan_expires_at
        user.plan_expiry_notified_at = None
    if payload.plan_id is not None:
        user.plan_expiry_notified_at = None
```

(plan_id 블록 안에 리셋을 넣어도 됨 — 기존 코드 구조에 맞게. 과거 시각도 그대로 허용 — 다음 틱 즉시 강등 운영 동선, 검증 로직 추가하지 않는다.)

- [ ] **Step 4: PASS + 전체 회귀**
- [ ] **Step 5: Commit** — `feat: 관리자 사용자 만료일 설정·해제(tri-state) + notified 가드 리셋`

---

### Task 6: me/usage에 plan_expires_at

**Files:** Modify `app/schemas/auth.py::MyUsageResponse`, `app/routers/auth.py::my_usage`; Test: `tests/test_me_usage.py` 확장

- [ ] **Step 1: 실패하는 테스트** (append to tests/test_me_usage.py — 파일 기존 픽스처 컨벤션 확인 후 스타일 맞춤):

```python
def test_my_usage_response_has_plan_expires_at():
    from app.schemas.auth import MyUsageResponse

    assert "plan_expires_at" in MyUsageResponse.model_fields
```

- [ ] **Step 2: FAIL 확인**
- [ ] **Step 3: 구현** — `MyUsageResponse`에 `plan_expires_at: Optional[datetime] = None` (datetime import 확인). `my_usage` 핸들러에서:

```python
    db_user = await session.get(User, user.user_id)
    expires = db_user.plan_expires_at if db_user else None
```

두 반환 경로(unlimited 조기 반환 포함) 모두에 `plan_expires_at=expires` 전달. `User` 모델 import 추가.

- [ ] **Step 4: PASS + 전체 회귀**
- [ ] **Step 5: Commit** — `feat: me/usage에 plan_expires_at — 마이페이지 만료 표시용`

---

### Task 7: 프런트 — 관리자 만료일 편집 + 마이페이지 표시

**Files:** Modify `frontend/src/api/admin.ts`, `frontend/src/api/me.ts`, `frontend/src/pages/Admin.tsx`, `frontend/src/pages/MyPage.tsx`

- [ ] **Step 1: API 타입** — `admin.ts`: `AdminUser`에 `plan_expires_at: string | null`; `patchUser` payload 타입에 `plan_expires_at?: string | null`. `me.ts`: `MyUsageResponse`에 `plan_expires_at: string | null`.

- [ ] **Step 2: Admin.tsx 사용자 테이블** — 플랜 select 옆에 만료일 셀 추가(기존 마크업 컨벤션 준수):

```tsx
<td className="px-3 py-2">
  <input
    type="datetime-local"
    className="border rounded px-1 py-0.5 text-xs"
    value={u.plan_expires_at ? u.plan_expires_at.slice(0, 16) : ''}
    onChange={(e) => changeExpiry(u, e.target.value)}
  />
  {u.plan_expires_at && (
    <span className={
      new Date(u.plan_expires_at) < new Date() ? 'ml-1 text-xs text-red-600'
      : new Date(u.plan_expires_at).getTime() - Date.now() < 7 * 86400_000 ? 'ml-1 text-xs text-amber-600'
      : 'ml-1 text-xs text-gray-400'
    }>
      {new Date(u.plan_expires_at) < new Date() ? '만료' : '유효'}
    </span>
  )}
</td>
```

핸들러(기존 patchUser 핸들러 패턴):

```tsx
const changeExpiry = async (u: AdminUser, local: string) => {
  const iso = local ? new Date(local).toISOString() : null
  await adminApi.patchUser(u.user_id, { plan_expires_at: iso })
  await loadUsers()   // 기존 목록 재조회 함수명 확인 후 사용
}
```

- [ ] **Step 3: MyPage.tsx** — `플랜: {data.plan_name}` 표시부(85행 부근)에:

```tsx
{data.plan_expires_at && (
  <span className={
    new Date(data.plan_expires_at).getTime() - Date.now() < 7 * 86400_000
      ? 'ml-2 text-sm text-amber-600 font-medium'
      : 'ml-2 text-sm text-gray-500'
  }>
    (만료 {new Date(data.plan_expires_at).toLocaleDateString()} — 연장은 관리자에게 문의)
  </span>
)}
```

- [ ] **Step 4: 검증** — `cd frontend && npx tsc --noEmit && npm run build && npx vitest run` 전부 클린 + 백엔드 전체 회귀.
- [ ] **Step 5: Commit** — `feat: Admin 만료일 편집(달력 입력·경고색) + MyPage 만료 표시`

---

### Task 8: 전체 검증 + 상위 스펙 E행 갱신

- [ ] **Step 1:** `.venv_e2e/bin/python -m pytest tests/ -q` + 프런트 tsc/build/vitest 전부 통과 확인.
- [ ] **Step 2:** `docs/superpowers/specs/2026-07-03-multi-tenant-design.md` §7 E행을 `E. 유료화 (E-1 구현 완료 2026-XX-XX — pro 시드·만료 강등·임박 알림, 설계 2026-07-15-phase-e1-manual-billing-plan-expiry-design.md. E-2 잔여: PG 결제·약관·셀프서비스 업그레이드)`로 갱신(실제 날짜, 표 구조 유지).
- [ ] **Step 3: Commit** — `docs: Phase E-1 구현 반영 — pro 플랜·만료 강등 완료 표기 (E-2 잔여 명시)`

---

## 실 DB E2E 체크리스트 (구현·머지 후, 테스트 DB 100.115.13.102)

> `.venv_e2e` + `PYTHONPATH=.`, httpx ASGITransport 패턴. postgres-ytdb MCP는 프로덕션 — 접근 금지.

1. [ ] 부팅 마이그레이션: users 컬럼 2개 생성·멱등, pro 플랜 시드 1행(재부팅 시 중복 없음).
2. [ ] 테스트 유저를 pro+과거 만료일로 설정 → `run_plan_expiry_once()` 실행 → free 강등 + `effective_limits` 즉시 축소 실측.
3. [ ] 미래 만료일(D-7 이내) 설정 → 틱 1회 → `plan_expiry_notified_at` 마킹 + (실 봇 연결 시) 텔레그램 수신, 틱 재실행 시 재발송 없음.
4. [ ] 관리자 PATCH 만료일 설정/해제/과거시각 → 실 DB 반영 + notified 리셋.
5. [ ] `GET /api/me/usage`에 plan_expires_at 노출.
6. [ ] unlimited(관리자) 사용자에 만료일을 넣어도 틱이 건드리지 않음.
7. [ ] 정리: 테스트 유저 원복.

## Self-Review 결과 (작성 시 수행)

- 스펙 §1.1=Task 1, §1.2=Task 2, §2=Task 3·4, §3=Task 5, §4=Task 6·7, §5=각 태스크+E2E, §6=NULL 기본·시드 멱등으로 충족. 갭 없음.
- 타입 일관성: `ExpiryCandidate` 필드 순서(Task 3 정의↔테스트 위치 인자), `JOB_PLAN_EXPIRY` 명칭, `plan_expires_at` 직렬화(datetime↔ISO string) 상호 확인.
- 주의: Task 5의 `get_session` 의존성 임포트 경로와 Task 7의 목록 재조회 함수명은 실제 코드에서 확인 후 맞출 것(검증 대상 동작은 고정).
