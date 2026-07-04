# Phase A: 계정·소유권 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 단일 계정(.env) 인증을 DB 기반 다중 사용자(users) 인증으로 교체하고, 그룹에 소유권을 부여해 "본인 소유 그룹만 접근"을 강제한다. 초대제 가입과 최소 관리자 API/UI를 포함한다.

**Architecture:** 제어 평면(`app` 스키마)에 `plans`/`users`/`invitations` 테이블과 `groups.owner_user_id` 컬럼을 추가한다. 인증은 기존 세션 쿠키 방식을 유지하되 세션에 `user_id`를 저장하고, 모든 그룹 스코프 API의 진입점인 `get_group_or_404`에 소유권 검사를 통합한다(라우터 코드는 무변경). 개발 모드(users 비어있음 + `AUTH_PASSWORD` 미설정)는 기존처럼 인증 비활성으로 동작한다.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, argon2-cffi(비밀번호 해시), React+TS(frontend), pytest(DB 없는 단위 테스트 — FakeSession/dependency_overrides 패턴).

**전제 스펙:** `docs/superpowers/specs/2026-07-03-multi-tenant-design.md` §2.1~2.3, §2.8, §3, §7(Phase A)

**Phase A에서 하지 않는 것:** 쿼터 강제(B), 설정 카테고리 권한 분리(C), ai_usage(C), 텔레그램 연결(D), 사용자 그룹 프로비저닝 마법사(D). 사용자 정지/해제 토글 UI는 B(콘솔)로 미룸 — A는 사용자 목록 조회까지.

---

## 배경: 기존 코드 이해 (실행 전 필독)

- **인증 현행**: `app/routers/auth.py` — `.env`의 `AUTH_USERNAME/AUTH_PASSWORD` 단일 계정. `AUTH_PASSWORD` 비면 인증 비활성. `require_auth`가 `app/main.py:65`에서 모든 보호 라우터에 걸림.
- **그룹 스코프**: 모든 도메인 라우터가 `app/routers/deps.py`의 `get_group_or_404`를 Depends로 사용(`grep -rn get_group_or_404 app/routers`로 확인 가능). 여기에 소유권 검사를 넣으면 라우터 수정이 불필요하다.
- **제어 평면 DDL**: `app/control_db.py`의 `ensure_control_schema()`가 부팅 시 `Base.metadata.create_all`로 멱등 생성. **create_all은 기존 테이블에 컬럼을 추가하지 못하므로** `groups.owner_user_id`는 별도 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`가 필요하다.
- **테스트 스타일**: `tests/`는 DB 없이 돈다(순수 함수 + `TestClient` + 라우트 등록 확인). `TestClient(app)`를 `with` 없이 쓰므로 lifespan(DB 부트스트랩)이 실행되지 않는다. DB가 필요한 로직은 FakeSession(아래 Task 5에 정의)과 `app.dependency_overrides`로 대체한다.
- **프론트**: React SPA(`frontend/src`), 빌드 산출물은 `app/static/ui`. `AuthProvider`가 미인증 시 `Login` 페이지로 게이트. API 헬퍼는 `frontend/src/api/http.ts`의 `rootApi`(전역)/`groupClient`(그룹 스코프).

## 파일 구조 (전체 조망)

```
생성:
  app/models/control/plan.py          app.plans 모델
  app/models/control/user.py          app.users 모델
  app/models/control/invitation.py    app.invitations 모델
  app/services/auth_service.py        비밀번호 해시/토큰/시드/인증 상태
  app/schemas/auth.py                 로그인/가입/me 스키마
  app/schemas/admin.py                관리자 API 스키마
  app/routers/admin.py                /api/admin/* (users, invitations, plans)
  tests/test_auth_service.py          해시/이메일 규칙/토큰 단위 테스트
  tests/test_control_models.py        모델 컬럼/스키마 검증
  tests/test_signup.py                초대 가입 플로우 (FakeSession)
  tests/test_ownership.py             can_access_group + 404 은닉
  tests/test_admin_api.py             관리자 API 권한/라우트
  frontend/src/pages/Signup.tsx       초대 가입 페이지
  frontend/src/pages/Admin.tsx        관리자 페이지(사용자/초대)
  frontend/src/api/admin.ts           관리자 API 클라이언트
수정:
  app/routers/auth.py                 DB 기반 재작성 (require_user/require_admin/signup)
  app/routers/deps.py                 get_group_or_404 소유권 통합
  app/routers/groups.py               목록 필터/생성 시 owner·자동 slug
  app/schemas/group.py                slug Optional화, GroupOut.owner_user_id
  app/control_db.py                   ensure_control_schema에 ALTER 추가
  app/main.py                         lifespan bootstrap_auth, require_user 교체, admin 라우터
  requirements.txt                    argon2-cffi
  tests/test_auth.py                  DB 기반 인증에 맞춰 재작성
  frontend/src/api/auth.ts            me/login/signup 시그니처 변경
  frontend/src/api/http.ts            rootApi.del 추가
  frontend/src/auth/useAuth.ts        user 객체/role 노출
  frontend/src/auth/AuthProvider.tsx  변경된 me 응답 반영
  frontend/src/pages/Login.tsx        이메일 로그인
  frontend/src/components/Layout.tsx  관리자 링크 + 표시명
  frontend/src/main.tsx               /signup 라우트(인증 게이트 밖)
  frontend/src/App.tsx                /admin 라우트
  docs/architecture.md                비목표(멀티테넌트) 갱신 각주
```

---

### Task 1: argon2-cffi 의존성 추가

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: requirements.txt에 추가**

`# Secrets encryption` 블록 아래에 추가:

```
# 비밀번호 해시 (users 계정)
argon2-cffi>=23.1.0
```

- [ ] **Step 2: 설치 확인**

Run: `source .venv/bin/activate 2>/dev/null; pip install -r requirements.txt -q && python -c "from argon2 import PasswordHasher; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "chore: argon2-cffi 의존성 추가 (Phase A 계정)"
```

---

### Task 2: auth_service 유틸 (해시/이메일 규칙/초대 토큰)

**Files:**
- Create: `app/services/auth_service.py`
- Test: `tests/test_auth_service.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_auth_service.py`:

```python
"""auth_service 순수 유틸 검증 (DB 불필요)."""

from app.services.auth_service import (
    admin_bootstrap_email,
    generate_invite_token,
    hash_password,
    verify_password,
)


def test_password_hash_roundtrip():
    h = hash_password("secret123")
    assert h != "secret123" and h.startswith("$argon2")
    assert verify_password("secret123", h) is True
    assert verify_password("wrong", h) is False


def test_verify_password_bad_hash_returns_false():
    assert verify_password("x", "not-a-hash") is False


def test_admin_bootstrap_email():
    assert admin_bootstrap_email("admin@example.com") == "admin@example.com"
    assert admin_bootstrap_email("admin") == "admin@local"
    assert admin_bootstrap_email("Admin") == "admin@local"


def test_invite_token_unique_and_urlsafe():
    tokens = {generate_invite_token() for _ in range(20)}
    assert len(tokens) == 20
    for t in tokens:
        assert len(t) >= 32
        assert all(c.isalnum() or c in "-_" for c in t)
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_auth_service.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.auth_service`

- [ ] **Step 3: 구현**

`app/services/auth_service.py`:

```python
"""계정 인증 서비스: 비밀번호 해시, 초대 토큰, 부트스트랩 시드, 인증 상태.

인증 활성 여부는 "users 행 존재 여부(부팅 시 캐시) 또는 AUTH_PASSWORD 설정"으로
판정한다. 둘 다 없으면 개발 모드(인증 비활성)로 기존 동작을 유지한다.
"""

from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.config import settings

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def admin_bootstrap_email(username: str) -> str:
    """AUTH_USERNAME이 이메일 형식이면 그대로, 아니면 {username}@local (스펙 §3.1)."""
    u = (username or "admin").strip().lower()
    return u if "@" in u else f"{u}@local"


def generate_invite_token() -> str:
    return secrets.token_urlsafe(32)


# ---- 인증 상태 (부팅 시 refresh, 가입 시 set) ----

_users_exist = False


def set_users_exist(value: bool) -> None:
    global _users_exist
    _users_exist = value


def users_exist() -> bool:
    return _users_exist


def is_auth_enabled() -> bool:
    return _users_exist or bool((settings.AUTH_PASSWORD or "").strip())
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_auth_service.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/auth_service.py tests/test_auth_service.py
git commit -m "feat: auth_service 비밀번호 해시/초대 토큰/인증 상태"
```

---

### Task 3: 제어 평면 모델 (plans / users / invitations / groups.owner_user_id)

**Files:**
- Create: `app/models/control/plan.py`, `app/models/control/user.py`, `app/models/control/invitation.py`
- Modify: `app/models/control/group.py`, `app/control_db.py:59-67`
- Test: `tests/test_control_models.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_control_models.py`:

```python
"""제어 평면 신규 모델(plans/users/invitations)과 groups.owner_user_id 검증."""

from app.control_db import APP_SCHEMA, Base
from app.models.control.group import Group
from app.models.control.invitation import Invitation
from app.models.control.plan import Plan
from app.models.control.user import User


def test_tables_registered_in_app_schema():
    tables = Base.metadata.tables
    for name in ("plans", "users", "invitations"):
        assert f"{APP_SCHEMA}.{name}" in tables


def test_user_columns():
    cols = {c.name for c in User.__table__.columns}
    assert {"user_id", "email", "password_hash", "display_name", "role",
            "status", "plan_id", "last_login_at", "created_at", "updated_at"} <= cols


def test_plan_columns_match_spec():
    cols = {c.name for c in Plan.__table__.columns}
    assert {"plan_id", "slug", "name", "max_groups", "max_channels_total",
            "max_analyses_per_day", "max_video_minutes",
            "monthly_cost_budget_usd", "min_poll_interval_min", "is_default"} <= cols


def test_invitation_columns():
    cols = {c.name for c in Invitation.__table__.columns}
    assert {"invite_id", "token", "plan_id", "memo", "invited_by",
            "expires_at", "used_by", "used_at", "created_at"} <= cols


def test_group_has_owner():
    assert "owner_user_id" in {c.name for c in Group.__table__.columns}
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_control_models.py -v`
Expected: FAIL — `ModuleNotFoundError: app.models.control.plan`

- [ ] **Step 3: 모델 구현**

`app/models/control/plan.py`:

```python
"""app.plans — 사용자 플랜(쿼터 정의). Phase A는 테이블·시드만, 강제는 Phase B."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class Plan(Base):
    __tablename__ = "plans"
    __table_args__ = {"schema": APP_SCHEMA}

    plan_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    max_groups: Mapped[int] = mapped_column(Integer, nullable=False)
    max_channels_total: Mapped[int] = mapped_column(Integer, nullable=False)
    max_analyses_per_day: Mapped[int] = mapped_column(Integer, nullable=False)
    max_video_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_cost_budget_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    min_poll_interval_min: Mapped[int] = mapped_column(Integer, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
```

`app/models/control/user.py`:

```python
"""app.users — 서비스 계정. role: admin | user, status: active | suspended."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": APP_SCHEMA}

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(Text, nullable=False, default="user")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    plan_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{APP_SCHEMA}.plans.plan_id"), nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
```

`app/models/control/invitation.py`:

```python
"""app.invitations — 초대제 가입 토큰 (1회용, 만료 있음)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class Invitation(Base):
    __tablename__ = "invitations"
    __table_args__ = {"schema": APP_SCHEMA}

    invite_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    plan_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{APP_SCHEMA}.plans.plan_id"), nullable=False
    )
    memo: Mapped[str | None] = mapped_column(Text, nullable=True)
    invited_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{APP_SCHEMA}.users.user_id"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{APP_SCHEMA}.users.user_id"), nullable=True
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

`app/models/control/group.py` — `description` 컬럼 위에 추가:

```python
    # 소유자. NULL은 마이그레이션 이전 상태(관리자만 접근 가능). ON DELETE는 두지 않는다
    # (사용자 삭제 전 그룹 정리를 강제).
    owner_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
```

(주의: FK 제약은 `ensure_control_schema`의 ALTER에서만 건다. 모델에 `ForeignKey`를 걸면
create_all의 테이블 생성 순서에는 문제없지만, 기존 DB와 신규 DB의 제약 이름이 달라질 수
있어 컬럼만 선언한다.)

`app/control_db.py`의 `ensure_control_schema()` 교체:

```python
async def ensure_control_schema() -> None:
    """app 스키마와 제어 평면 테이블을 멱등 생성한다."""
    # 모델을 임포트해 Base.metadata에 등록되도록 한다.
    from app.models.control import group, invitation, plan, setting, user  # noqa: F401

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{APP_SCHEMA}"'))
        await conn.run_sync(Base.metadata.create_all)
        # 기존 설치 업그레이드: create_all은 기존 테이블에 컬럼을 추가하지 않는다.
        await conn.execute(
            text(
                f'ALTER TABLE "{APP_SCHEMA}".groups '
                f"ADD COLUMN IF NOT EXISTS owner_user_id BIGINT "
                f'REFERENCES "{APP_SCHEMA}".users(user_id)'
            )
        )
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_control_models.py tests/test_auth_service.py -v`
Expected: 전부 PASS

- [ ] **Step 5: Commit**

```bash
git add app/models/control/plan.py app/models/control/user.py app/models/control/invitation.py \
        app/models/control/group.py app/control_db.py tests/test_control_models.py
git commit -m "feat: 제어 평면 plans/users/invitations 모델 + groups.owner_user_id"
```

---

### Task 4: 부트스트랩 시드 (plans 시드 / admin 시드 / 그룹 소유자 백필)

**Files:**
- Modify: `app/services/auth_service.py`, `app/main.py:27-42`
- Test: `tests/test_auth_service.py` (추가)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_auth_service.py`에 추가:

```python
from app.services.auth_service import PLAN_SEEDS, is_auth_enabled, set_users_exist


def test_plan_seeds_have_default_free_and_unlimited():
    slugs = {p["slug"] for p in PLAN_SEEDS}
    assert slugs == {"free", "unlimited"}
    defaults = [p for p in PLAN_SEEDS if p["is_default"]]
    assert len(defaults) == 1 and defaults[0]["slug"] == "free"


def test_is_auth_enabled_matrix(monkeypatch):
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "AUTH_PASSWORD", "")
    set_users_exist(False)
    assert is_auth_enabled() is False       # 개발 모드
    set_users_exist(True)
    assert is_auth_enabled() is True        # 사용자 존재 → 항상 활성
    set_users_exist(False)
    monkeypatch.setattr(app_settings, "AUTH_PASSWORD", "pw")
    assert is_auth_enabled() is True        # env 자격증명만 있어도 활성
    set_users_exist(False)
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_auth_service.py -v`
Expected: FAIL — `ImportError: PLAN_SEEDS`

- [ ] **Step 3: 구현** — `app/services/auth_service.py` 끝에 추가:

```python
# ---- 부트스트랩 시드 (lifespan에서 ensure_control_schema 이후 호출) ----

PLAN_SEEDS: list[dict] = [
    {
        "slug": "free", "name": "Free", "max_groups": 1, "max_channels_total": 5,
        "max_analyses_per_day": 10, "max_video_minutes": 60,
        "monthly_cost_budget_usd": "5.0", "min_poll_interval_min": 60, "is_default": True,
    },
    {
        "slug": "unlimited", "name": "Unlimited", "max_groups": 1000,
        "max_channels_total": 100000, "max_analyses_per_day": 100000,
        "max_video_minutes": 100000, "monthly_cost_budget_usd": "1000000.0",
        "min_poll_interval_min": 1, "is_default": False,
    },
]


async def bootstrap_auth() -> None:
    """플랜 시드 → admin 시드 → 그룹 소유자 백필 → 인증 상태 캐시. 멱등."""
    from decimal import Decimal

    from sqlalchemy import select, update

    from app.control_db import get_sessionmaker
    from app.models.control.group import Group
    from app.models.control.plan import Plan
    from app.models.control.user import User

    async with get_sessionmaker()() as session:
        # 1) 플랜 시드 (slug 기준 멱등)
        existing = {
            s for (s,) in (await session.execute(select(Plan.slug))).all()
        }
        for seed in PLAN_SEEDS:
            if seed["slug"] not in existing:
                session.add(Plan(**{**seed, "monthly_cost_budget_usd": Decimal(seed["monthly_cost_budget_usd"])}))
        await session.flush()

        # 2) users 비어있고 AUTH_PASSWORD 설정 시 admin 시드
        has_user = (await session.execute(select(User.user_id).limit(1))).first() is not None
        if not has_user and (settings.AUTH_PASSWORD or "").strip():
            plan_id = (
                await session.execute(select(Plan.plan_id).where(Plan.slug == "unlimited"))
            ).scalar_one()
            session.add(
                User(
                    email=admin_bootstrap_email(settings.AUTH_USERNAME),
                    password_hash=hash_password(settings.AUTH_PASSWORD),
                    display_name=settings.AUTH_USERNAME,
                    role="admin",
                    status="active",
                    plan_id=plan_id,
                )
            )
            await session.flush()
            has_user = True

        # 3) 소유자 없는 기존 그룹은 첫 admin 소유로 백필 (스펙 §2.8)
        admin_id = (
            await session.execute(
                select(User.user_id).where(User.role == "admin").order_by(User.user_id).limit(1)
            )
        ).scalar_one_or_none()
        if admin_id is not None:
            await session.execute(
                update(Group).where(Group.owner_user_id.is_(None)).values(owner_user_id=admin_id)
            )

        await session.commit()
        set_users_exist(has_user)
```

- [ ] **Step 4: main.py lifespan에 연결** — `app/main.py`의 lifespan에서 `await ensure_control_schema()` 바로 다음 줄에 추가:

```python
    from app.services.auth_service import bootstrap_auth

    await bootstrap_auth()
```

- [ ] **Step 5: 통과 확인**

Run: `pytest tests/test_auth_service.py tests/test_control_models.py -v`
Expected: 전부 PASS

- [ ] **Step 6: (선택·환경 가용 시) 실 DB 부팅 확인**

로컬 PG가 설정돼 있으면: `python -c "
import asyncio
from app.control_db import ensure_control_schema
from app.services.auth_service import bootstrap_auth
asyncio.run(ensure_control_schema()); asyncio.run(bootstrap_auth()); print('boot ok')
"`
Expected: `boot ok` (plans 2행, admin 1행, 기존 그룹 owner 백필). PG 없으면 skip.

- [ ] **Step 7: Commit**

```bash
git add app/services/auth_service.py app/main.py tests/test_auth_service.py
git commit -m "feat: 부팅 시 플랜/admin 시드 및 그룹 소유자 백필"
```

---

### Task 5: 인증 라우터 DB 기반 재작성 (login/logout/me + require_user/require_admin)

**Files:**
- Modify: `app/routers/auth.py` (전면 재작성), `app/main.py:20,61-75`
- Create: `app/schemas/auth.py`
- Test: `tests/test_auth.py` (전면 재작성)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_auth.py` 전체 교체:

```python
"""DB 기반 다중 사용자 인증 검증 (DB 없이 FakeSession/monkeypatch로 대체).

- 개발 모드(users 없음 + AUTH_PASSWORD 미설정): 인증 비활성, require_user는 가상 admin.
- 활성 모드: 미로그인 401, 로그인은 users 테이블 조회(argon2 검증).
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import settings as app_settings
from app.control_db import get_session
from app.main import app
from app.models.control.user import User
from app.routers import auth as auth_router
from app.services.auth_service import hash_password, set_users_exist


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    """session.execute() 호출 순서대로 미리 준비한 값을 돌려준다."""

    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.committed = False

    async def execute(self, stmt):
        return FakeResult(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "user_id", None) is None:
                obj.user_id = 999

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        pass


def make_user(**kw) -> User:
    defaults = dict(
        user_id=1, email="alice@example.com", password_hash=hash_password("pw1234"),
        display_name="Alice", role="user", status="active", plan_id=1,
        last_login_at=None, created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    defaults.update(kw)
    u = User()
    for k, v in defaults.items():
        setattr(u, k, v)
    return u


def override_session(fake: FakeSession):
    async def _dep():
        yield fake
    app.dependency_overrides[get_session] = _dep


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    set_users_exist(False)
    monkeypatch.setattr(app_settings, "AUTH_PASSWORD", "")
    yield
    set_users_exist(False)
    app.dependency_overrides.clear()


def _client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ---- 개발 모드 ----

def test_me_disabled_dev_mode():
    body = _client().get("/api/auth/me").json()
    assert body["auth_enabled"] is False and body["authenticated"] is True
    assert body["user"]["role"] == "admin"  # 가상 admin


def test_protected_open_when_disabled():
    assert _client().get("/api/groups").status_code != 401


# ---- 활성 모드 ----

def test_protected_requires_login_when_enabled():
    set_users_exist(True)
    c = _client()
    assert c.get("/api/groups").status_code == 401
    assert c.get("/api/auth/me").json()["authenticated"] is False


def test_login_success_and_me(monkeypatch):
    set_users_exist(True)
    user = make_user()
    fake = FakeSession([user])          # login의 email 조회 1회
    override_session(fake)

    async def fake_load(user_id):
        return user if user_id == 1 else None

    monkeypatch.setattr(auth_router, "_load_user", fake_load)

    c = _client()
    r = c.post("/api/auth/login", json={"email": "Alice@Example.com", "password": "pw1234"})
    assert r.status_code == 200 and r.json()["email"] == "alice@example.com"
    assert fake.committed is True       # last_login_at 갱신 커밋
    me = c.get("/api/auth/me").json()
    assert me["authenticated"] is True and me["user"]["role"] == "user"
    assert c.post("/api/auth/logout").status_code == 204
    assert c.get("/api/groups").status_code == 401


def test_login_wrong_password():
    set_users_exist(True)
    override_session(FakeSession([make_user()]))
    r = _client().post("/api/auth/login", json={"email": "alice@example.com", "password": "no"})
    assert r.status_code == 401


def test_login_unknown_email():
    set_users_exist(True)
    override_session(FakeSession([None]))
    r = _client().post("/api/auth/login", json={"email": "who@example.com", "password": "x"})
    assert r.status_code == 401


def test_suspended_user_rejected_at_login():
    set_users_exist(True)
    override_session(FakeSession([make_user(status="suspended")]))
    r = _client().post("/api/auth/login", json={"email": "alice@example.com", "password": "pw1234"})
    assert r.status_code == 403


def test_suspended_user_rejected_at_request(monkeypatch):
    set_users_exist(True)
    active = make_user()
    override_session(FakeSession([active]))
    state = {"user": active}

    async def fake_load(user_id):
        return state["user"]

    monkeypatch.setattr(auth_router, "_load_user", fake_load)
    c = _client()
    c.post("/api/auth/login", json={"email": "alice@example.com", "password": "pw1234"})
    state["user"] = make_user(status="suspended")   # 로그인 후 정지됨
    assert c.get("/api/groups").status_code == 403


def test_health_and_root_open_without_auth():
    set_users_exist(True)
    c = _client()
    assert c.get("/health").status_code == 200
    assert c.get("/").status_code in (200, 503)
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_auth.py -v`
Expected: FAIL (me 응답 형식 상이, login이 email을 안 받음 등)

- [ ] **Step 3: 스키마 작성** — `app/schemas/auth.py`:

```python
"""인증 입출력 스키마."""

from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, field_validator

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")


def normalize_email(v: str) -> str:
    v = (v or "").strip().lower()
    if not EMAIL_RE.fullmatch(v):
        raise ValueError("올바른 이메일 형식이 아닙니다.")
    return v


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return normalize_email(v)


class SignupRequest(BaseModel):
    token: str
    email: str
    password: str
    display_name: Optional[str] = None

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return normalize_email(v)

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("비밀번호는 8자 이상이어야 합니다.")
        return v


class UserOut(BaseModel):
    email: str
    display_name: Optional[str]
    role: str


class MeResponse(BaseModel):
    auth_enabled: bool
    authenticated: bool
    user: Optional[UserOut] = None
```

- [ ] **Step 4: auth 라우터 재작성** — `app/routers/auth.py` 전체 교체:

```python
"""DB 기반 다중 사용자 인증 (httpOnly 세션 쿠키).

- 개발 모드(users 없음 + AUTH_PASSWORD 미설정): 인증 비활성. require_user는 가상 admin.
- 활성 모드: 세션의 user_id로 매 요청 사용자 로드(정지 계정 즉시 차단).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.control_db import get_session, get_sessionmaker
from app.models.control.user import User
from app.schemas.auth import LoginRequest, MeResponse, UserOut
from app.services.auth_service import is_auth_enabled, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@dataclass(frozen=True)
class CurrentUser:
    user_id: int
    email: str
    display_name: str | None
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


# 개발 모드(인증 비활성)에서 모든 요청에 부여되는 가상 관리자.
DEV_ADMIN = CurrentUser(user_id=0, email="dev@local", display_name="개발 모드", role="admin")


async def _load_user(user_id: int) -> User | None:
    """세션 user_id → users 행. 테스트에서 monkeypatch 대상."""
    async with get_sessionmaker()() as session:
        return await session.get(User, user_id)


def _to_current(user: User) -> CurrentUser:
    return CurrentUser(
        user_id=user.user_id, email=user.email,
        display_name=user.display_name, role=user.role,
    )


async def require_user(request: Request) -> CurrentUser:
    """보호 라우터 의존성. 인증 비활성이면 가상 admin, 활성이면 세션+DB 검증."""
    if not is_auth_enabled():
        return DEV_ADMIN
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")
    user = await _load_user(int(user_id))
    if user is None:
        request.session.clear()
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="정지된 계정입니다.")
    return _to_current(user)


async def require_admin(user: CurrentUser = Depends(require_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    return user


@router.get("/me", response_model=MeResponse)
async def me(request: Request) -> MeResponse:
    if not is_auth_enabled():
        return MeResponse(
            auth_enabled=False, authenticated=True,
            user=UserOut(email=DEV_ADMIN.email, display_name=DEV_ADMIN.display_name, role="admin"),
        )
    user_id = request.session.get("user_id")
    user = await _load_user(int(user_id)) if user_id else None
    if user is None or user.status != "active":
        return MeResponse(auth_enabled=True, authenticated=False, user=None)
    return MeResponse(
        auth_enabled=True, authenticated=True,
        user=UserOut(email=user.email, display_name=user.display_name, role=user.role),
    )


@router.post("/login", response_model=UserOut)
async def login(
    payload: LoginRequest, request: Request, session: AsyncSession = Depends(get_session)
) -> UserOut:
    if not is_auth_enabled():
        raise HTTPException(status_code=400, detail="인증이 설정되지 않았습니다.")
    result = await session.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다.")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="정지된 계정입니다.")
    user.last_login_at = datetime.now(timezone.utc)
    await session.commit()
    request.session["user_id"] = user.user_id
    return UserOut(email=user.email, display_name=user.display_name, role=user.role)


@router.post("/logout", status_code=204)
async def logout(request: Request) -> None:
    request.session.clear()
```

- [ ] **Step 5: main.py 배선 교체** — `app/main.py`:

19-20행 import 교체:

```python
from app.routers import actions, admin, auth, channels, digests, groups, health, logs, settings, share, stats, tags, videos
from app.routers.auth import require_user
```

(admin 라우터는 Task 8에서 생성 — 그 전까지는 `admin` import를 빼고 Task 8에서 추가한다)

65행 교체:

```python
_protected = [Depends(require_user)]
```

- [ ] **Step 6: 통과 확인**

Run: `pytest tests/test_auth.py -v`
Expected: 전부 PASS

Run: `pytest -q`
Expected: 기존 테스트 포함 전부 PASS (require_auth를 참조하던 코드가 남아있으면 grep으로 제거: `grep -rn require_auth app/ tests/`)

- [ ] **Step 7: Commit**

```bash
git add app/routers/auth.py app/schemas/auth.py app/main.py tests/test_auth.py
git commit -m "feat: DB 기반 다중 사용자 인증 (require_user/require_admin)"
```

---

### Task 6: 초대 가입 API (POST /api/auth/signup)

**Files:**
- Modify: `app/routers/auth.py`
- Test: `tests/test_signup.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_signup.py`:

```python
"""초대 토큰 가입 플로우 (FakeSession, DB 불필요)."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.control_db import get_session
from app.main import app
from app.models.control.invitation import Invitation
from app.services.auth_service import set_users_exist
from tests.test_auth import FakeSession, override_session


def make_invite(**kw) -> Invitation:
    inv = Invitation()
    defaults = dict(
        invite_id=10, token="tok-valid", plan_id=1, memo=None, invited_by=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        used_by=None, used_at=None, created_at=datetime.now(timezone.utc),
    )
    defaults.update(kw)
    for k, v in defaults.items():
        setattr(inv, k, v)
    return inv


@pytest.fixture(autouse=True)
def _reset():
    set_users_exist(True)
    yield
    set_users_exist(False)
    app.dependency_overrides.clear()


def _client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _payload(**kw):
    base = {"token": "tok-valid", "email": "bob@example.com",
            "password": "pw123456", "display_name": "Bob"}
    base.update(kw)
    return base


def test_signup_success_and_autologin():
    invite = make_invite()
    fake = FakeSession([invite, None])   # 1) 토큰 조회 → invite, 2) 이메일 중복 조회 → 없음
    override_session(fake)
    c = _client()
    r = c.post("/api/auth/signup", json=_payload())
    assert r.status_code == 201
    assert r.json()["email"] == "bob@example.com" and r.json()["role"] == "user"
    assert invite.used_at is not None and invite.used_by == 999
    assert fake.committed is True
    # 자동 로그인: 세션 쿠키가 발급됨 (_load_user는 실 DB 경로라 여기서는 쿠키 존재만 확인).
    assert c.cookies.get("session")


def test_signup_unknown_token():
    override_session(FakeSession([None]))
    r = _client().post("/api/auth/signup", json=_payload(token="nope"))
    assert r.status_code == 400


def test_signup_expired_token():
    invite = make_invite(expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
    override_session(FakeSession([invite]))
    r = _client().post("/api/auth/signup", json=_payload())
    assert r.status_code == 400


def test_signup_used_token():
    invite = make_invite(used_at=datetime.now(timezone.utc), used_by=5)
    override_session(FakeSession([invite]))
    r = _client().post("/api/auth/signup", json=_payload())
    assert r.status_code == 400


def test_signup_duplicate_email():
    from tests.test_auth import make_user
    override_session(FakeSession([make_invite(), make_user()]))
    r = _client().post("/api/auth/signup", json=_payload(email="alice@example.com"))
    assert r.status_code == 409


def test_signup_short_password_rejected():
    # 검증 실패(422) 경로도 get_session 의존성은 해석되므로, DB 없는 환경에서
    # 엔진 생성 500을 피하기 위해 세션을 오버라이드한다(핸들러는 실행되지 않음).
    override_session(FakeSession([]))
    r = _client().post("/api/auth/signup", json=_payload(password="short"))
    assert r.status_code == 422
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_signup.py -v`
Expected: FAIL — 404 (signup 라우트 없음)

- [ ] **Step 3: 구현** — `app/routers/auth.py`에 추가:

import 블록에 추가:

```python
from app.models.control.invitation import Invitation
from app.schemas.auth import SignupRequest
from app.services.auth_service import hash_password, set_users_exist
```

라우터 끝에 추가:

```python
@router.post("/signup", response_model=UserOut, status_code=201)
async def signup(
    payload: SignupRequest, request: Request, session: AsyncSession = Depends(get_session)
) -> UserOut:
    """초대 토큰으로 가입. 성공 시 초대 소진 + 자동 로그인."""
    result = await session.execute(
        select(Invitation).where(Invitation.token == payload.token)
    )
    invite = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if invite is None or invite.used_at is not None or invite.expires_at <= now:
        raise HTTPException(status_code=400, detail="유효하지 않거나 만료된 초대입니다.")

    dup = await session.execute(select(User).where(User.email == payload.email))
    if dup.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="이미 가입된 이메일입니다.")

    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
        role="user",
        status="active",
        plan_id=invite.plan_id,
    )
    session.add(user)
    await session.flush()
    invite.used_by = user.user_id
    invite.used_at = now
    await session.commit()

    set_users_exist(True)
    request.session["user_id"] = user.user_id
    return UserOut(email=user.email, display_name=user.display_name, role=user.role)
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_signup.py tests/test_auth.py -v`
Expected: 전부 PASS

- [ ] **Step 5: Commit**

```bash
git add app/routers/auth.py tests/test_signup.py
git commit -m "feat: 초대 토큰 가입 API (POST /api/auth/signup)"
```

---

### Task 7: 그룹 소유권 강제 (get_group_or_404 통합 + groups 라우터)

**Files:**
- Modify: `app/routers/deps.py`, `app/routers/groups.py`, `app/schemas/group.py`
- Test: `tests/test_ownership.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_ownership.py`:

```python
"""그룹 소유권 접근 제어. 타인 그룹은 존재 은닉을 위해 404."""

import pytest
from fastapi.testclient import TestClient

from app.control_db import get_session
from app.main import app
from app.models.control.group import Group
from app.routers.auth import CurrentUser, require_user
from app.routers.deps import can_access_group
from app.services.auth_service import set_users_exist
from tests.test_auth import FakeSession, override_session

ALICE = CurrentUser(user_id=1, email="a@x.com", display_name="A", role="user")
BOB = CurrentUser(user_id=2, email="b@x.com", display_name="B", role="user")
ADMIN = CurrentUser(user_id=9, email="adm@x.com", display_name="Adm", role="admin")


def test_can_access_group_rules():
    assert can_access_group(1, ALICE) is True     # 본인 소유
    assert can_access_group(1, BOB) is False      # 타인 소유
    assert can_access_group(1, ADMIN) is True     # admin은 전부
    assert can_access_group(None, ALICE) is False # 소유자 미지정(레거시) → admin만
    assert can_access_group(None, ADMIN) is True


def make_group(owner: int | None) -> Group:
    from datetime import datetime, timezone

    g = Group()
    g.group_id, g.slug, g.name, g.schema_name = 1, "invest", "투자", "youtube_invest"
    g.is_active, g.owner_user_id, g.description = True, owner, None
    # response_model=GroupOut 직렬화에 필요 (DB server_default가 없는 인메모리 객체).
    g.created_at = g.updated_at = datetime.now(timezone.utc)
    return g


@pytest.fixture(autouse=True)
def _cleanup():
    set_users_exist(True)
    yield
    set_users_exist(False)
    app.dependency_overrides.clear()


def _as(user: CurrentUser):
    async def _dep():
        return user
    app.dependency_overrides[require_user] = _dep


def _client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_owner_group_404_for_stranger():
    _as(BOB)
    override_session(FakeSession([make_group(owner=1)]))
    assert _client().get("/api/groups/invest").status_code == 404


def test_owner_group_ok_for_owner():
    _as(ALICE)
    override_session(FakeSession([make_group(owner=1)]))
    assert _client().get("/api/groups/invest").status_code == 200


def test_owner_group_ok_for_admin():
    _as(ADMIN)
    override_session(FakeSession([make_group(owner=1)]))
    assert _client().get("/api/groups/invest").status_code == 200
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_ownership.py -v`
Expected: FAIL — `ImportError: can_access_group`

- [ ] **Step 3: deps.py 수정** — `app/routers/deps.py` 전체 교체:

```python
"""라우터 공용 의존성."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.control_db import get_session, get_sessionmaker
from app.models.control.group import Group
from app.routers.auth import CurrentUser, require_user


def can_access_group(owner_user_id: int | None, user: CurrentUser) -> bool:
    """admin은 전 그룹, 일반 사용자는 본인 소유 그룹만. owner 미지정(레거시)은 admin만."""
    if user.is_admin:
        return True
    return owner_user_id is not None and owner_user_id == user.user_id


async def get_group_or_404(
    slug: str = Path(..., description="그룹 slug"),
    user: CurrentUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> Group:
    result = await session.execute(select(Group).where(Group.slug == slug))
    group = result.scalar_one_or_none()
    # 타인 그룹은 존재 여부를 노출하지 않도록 미존재와 동일하게 404.
    if group is None or not can_access_group(group.owner_user_id, user):
        raise HTTPException(status_code=404, detail=f"그룹을 찾을 수 없습니다: {slug}")
    return group


async def get_group_by_slug_or_404(slug: str) -> Group:
    """slug로 그룹을 조회한다(일반 async 함수, FastAPI Depends 아님).

    공개 공유 페이지처럼 인증 체인 밖에서 호출할 때 사용한다(소유권 미검사 —
    공유 페이지는 서명 토큰으로 접근이 통제된다).
    """
    async with get_sessionmaker()() as session:
        result = await session.execute(select(Group).where(Group.slug == slug))
        group = result.scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=404, detail=f"그룹을 찾을 수 없습니다: {slug}")
    return group
```

- [ ] **Step 4: groups 라우터 수정** — `app/routers/groups.py`:

`list_groups`와 `create_group`를 교체하고 import를 보강한다:

```python
import secrets as _secrets

from app.routers.auth import CurrentUser, require_user
```

```python
@router.get("", response_model=list[GroupOut])
async def list_groups(
    user: CurrentUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> list[Group]:
    stmt = select(Group).order_by(Group.group_id)
    if not user.is_admin:
        stmt = stmt.where(Group.owner_user_id == user.user_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.post("", response_model=GroupOut, status_code=201)
async def create_group(
    payload: GroupCreate,
    user: CurrentUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> Group:
    if user.is_admin:
        # 관리자: 기존과 동일하게 slug/schema_name 직접 지정.
        if not payload.slug:
            raise HTTPException(status_code=422, detail="slug는 필수입니다.")
        slug = payload.slug
        schema_name = payload.schema_name or f"youtube_{slug}"
    else:
        # 일반 사용자: slug/schema 자동 생성 (스펙 §2.8). 입력값은 무시.
        slug = f"u{user.user_id}_{_secrets.token_hex(3)}"
        schema_name = f"youtube_{slug}"
    group = Group(
        slug=slug,
        name=payload.name,
        schema_name=schema_name,
        description=payload.description,
        owner_user_id=user.user_id if user.user_id != 0 else None,
    )
    session.add(group)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="slug 또는 schema_name이 이미 존재합니다."
        )
    await session.refresh(group)
    # 사용자 편의: 추천 기본 설정값을 미리 채워 둔다(시크릿/접속정보 제외).
    await seed_default_settings(group.group_id)
    return group
```

(주의: `user_id == 0`은 개발 모드 가상 admin — FK 위반을 피하려고 owner를 NULL로 둔다.)

- [ ] **Step 5: 스키마 수정** — `app/schemas/group.py`:

`GroupCreate.slug`를 Optional로, validator가 None 통과하도록 교체:

```python
class GroupCreate(BaseModel):
    # 관리자는 지정, 일반 사용자는 서버가 자동 생성(값 무시).
    slug: Optional[str] = None
    name: str
    # 미지정 시 'youtube_{slug}' 로 자동 생성
    schema_name: Optional[str] = None
    description: Optional[str] = None

    @field_validator("slug")
    @classmethod
    def _check_slug(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().lower()
        if not SLUG_RE.fullmatch(v):
            raise ValueError("slug는 소문자/숫자/밑줄(a-z0-9_)만 허용합니다.")
        return v
```

`GroupOut`에 필드 추가 (`is_active` 아래):

```python
    owner_user_id: Optional[int] = None
```

- [ ] **Step 6: 통과 확인**

Run: `pytest tests/test_ownership.py -v && pytest -q`
Expected: 전부 PASS

- [ ] **Step 7: Commit**

```bash
git add app/routers/deps.py app/routers/groups.py app/schemas/group.py tests/test_ownership.py
git commit -m "feat: 그룹 소유권 강제 — 본인 그룹만 접근, 타인 그룹 404 은닉"
```

---

### Task 8: 관리자 API (/api/admin/users, /api/admin/invitations, /api/admin/plans)

**Files:**
- Create: `app/routers/admin.py`, `app/schemas/admin.py`
- Modify: `app/main.py`
- Test: `tests/test_admin_api.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_admin_api.py`:

```python
"""관리자 API 권한(비관리자 403)과 라우트 등록 검증."""

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


def test_admin_routes_registered():
    paths = {r.path for r in app.routes}
    assert "/api/admin/users" in paths
    assert "/api/admin/invitations" in paths
    assert "/api/admin/invitations/{invite_id}" in paths
    assert "/api/admin/plans" in paths


def test_non_admin_forbidden():
    async def _dep():
        return USER
    app.dependency_overrides[require_user] = _dep
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/api/admin/users").status_code == 403
    assert c.get("/api/admin/invitations").status_code == 403
    assert c.post("/api/admin/invitations", json={}).status_code == 403


def test_unauthenticated_401():
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/api/admin/users").status_code == 401
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_admin_api.py -v`
Expected: FAIL — 라우트 미등록

- [ ] **Step 3: 스키마 작성** — `app/schemas/admin.py`:

```python
"""관리자 API 입출력 스키마."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    email: str
    display_name: Optional[str]
    role: str
    status: str
    plan_id: int
    last_login_at: Optional[datetime]
    created_at: datetime


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    plan_id: int
    slug: str
    name: str
    max_groups: int
    max_channels_total: int
    max_analyses_per_day: int
    max_video_minutes: int
    min_poll_interval_min: int
    is_default: bool


class InviteCreate(BaseModel):
    plan_slug: Optional[str] = None          # 미지정 시 기본 플랜(free)
    memo: Optional[str] = None
    expires_days: int = Field(default=7, ge=1, le=90)


class InviteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    invite_id: int
    token: str
    plan_id: int
    memo: Optional[str]
    expires_at: datetime
    used_by: Optional[int]
    used_at: Optional[datetime]
    created_at: datetime


class InviteCreated(InviteOut):
    signup_url: str
```

- [ ] **Step 4: 라우터 작성** — `app/routers/admin.py`:

```python
"""관리자 전용 API: 사용자 목록, 초대 발급/회수, 플랜 조회."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.control_db import get_session
from app.models.control.invitation import Invitation
from app.models.control.plan import Plan
from app.models.control.user import User
from app.routers.auth import CurrentUser, require_admin
from app.schemas.admin import AdminUserOut, InviteCreate, InviteCreated, InviteOut, PlanOut
from app.services.auth_service import generate_invite_token

router = APIRouter(
    prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)]
)


def _signup_url(token: str) -> str:
    base = (app_settings.PUBLIC_BASE_URL or "").rstrip("/")
    return f"{base}/signup?token={token}"


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(session: AsyncSession = Depends(get_session)) -> list[User]:
    result = await session.execute(select(User).order_by(User.user_id))
    return list(result.scalars().all())


@router.get("/plans", response_model=list[PlanOut])
async def list_plans(session: AsyncSession = Depends(get_session)) -> list[Plan]:
    result = await session.execute(select(Plan).order_by(Plan.plan_id))
    return list(result.scalars().all())


@router.get("/invitations", response_model=list[InviteOut])
async def list_invitations(session: AsyncSession = Depends(get_session)) -> list[Invitation]:
    result = await session.execute(select(Invitation).order_by(Invitation.invite_id.desc()))
    return list(result.scalars().all())


@router.post("/invitations", response_model=InviteCreated, status_code=201)
async def create_invitation(
    payload: InviteCreate,
    admin: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> InviteCreated:
    if payload.plan_slug:
        stmt = select(Plan).where(Plan.slug == payload.plan_slug)
    else:
        stmt = select(Plan).where(Plan.is_default.is_(True))
    plan = (await session.execute(stmt)).scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=400, detail="플랜을 찾을 수 없습니다.")
    if admin.user_id == 0:
        raise HTTPException(
            status_code=400, detail="개발 모드에서는 초대를 발급할 수 없습니다."
        )
    invite = Invitation(
        token=generate_invite_token(),
        plan_id=plan.plan_id,
        memo=payload.memo,
        invited_by=admin.user_id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=payload.expires_days),
    )
    session.add(invite)
    await session.commit()
    await session.refresh(invite)
    return InviteCreated(
        **{c.name: getattr(invite, c.name) for c in Invitation.__table__.columns},
        signup_url=_signup_url(invite.token),
    )


@router.delete("/invitations/{invite_id}", status_code=204)
async def revoke_invitation(
    invite_id: int, session: AsyncSession = Depends(get_session)
) -> None:
    invite = await session.get(Invitation, invite_id)
    if invite is None:
        raise HTTPException(status_code=404, detail="초대를 찾을 수 없습니다.")
    if invite.used_at is not None:
        raise HTTPException(status_code=400, detail="이미 사용된 초대는 회수할 수 없습니다.")
    await session.delete(invite)
    await session.commit()
```

- [ ] **Step 5: main.py 등록** — `app/main.py` import에 `admin` 추가(19행), 보호 라우터 블록에 추가:

```python
app.include_router(admin.router)  # 자체적으로 require_admin 의존성을 가짐
```

- [ ] **Step 6: 통과 확인**

Run: `pytest tests/test_admin_api.py -v && pytest -q`
Expected: 전부 PASS

- [ ] **Step 7: Commit**

```bash
git add app/routers/admin.py app/schemas/admin.py app/main.py tests/test_admin_api.py
git commit -m "feat: 관리자 API (사용자 목록/초대 발급·회수/플랜 조회)"
```

---

### Task 9: 프론트 — 인증 API/Provider/로그인 이메일화

**Files:**
- Modify: `frontend/src/api/auth.ts`, `frontend/src/api/http.ts`, `frontend/src/auth/useAuth.ts`, `frontend/src/auth/AuthProvider.tsx`, `frontend/src/pages/Login.tsx`, `frontend/src/components/Layout.tsx`

- [ ] **Step 1: api/auth.ts 교체**

```typescript
import { rootApi } from './http'

export interface AuthUser {
  email: string
  display_name: string | null
  role: 'admin' | 'user'
}

export interface MeResponse {
  auth_enabled: boolean
  authenticated: boolean
  user: AuthUser | null
}

export const authApi = {
  me: () => rootApi.get<MeResponse>('/auth/me'),
  login: (email: string, password: string) =>
    rootApi.post<AuthUser>('/auth/login', { email, password }),
  signup: (token: string, email: string, password: string, displayName: string) =>
    rootApi.post<AuthUser>('/auth/signup', {
      token, email, password, display_name: displayName || null,
    }),
  logout: () => rootApi.post<void>('/auth/logout'),
}
```

- [ ] **Step 2: http.ts rootApi에 del 추가** — `rootApi` 객체의 `patch` 아래:

```typescript
  del: <T>(path: string) => request<T>(`/api${path}`, { method: 'DELETE' }),
```

- [ ] **Step 3: useAuth.ts 교체**

```typescript
import { createContext, useContext } from 'react'
import type { AuthUser } from '../api/auth'

export interface AuthContextValue {
  user: AuthUser | null
  authEnabled: boolean
  logout: () => Promise<void>
}

export const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
```

- [ ] **Step 4: AuthProvider.tsx 수정** — me 응답 형식 변경 반영. 변경점만:

```typescript
// catch 폴백:
setState({ auth_enabled: true, authenticated: false, user: null })
// 401 핸들러/logout의 상태 초기화:
setState((s) => (s ? { ...s, authenticated: false, user: null } : s))
// Provider value:
<AuthContext.Provider value={{ user: state.user, authEnabled: state.auth_enabled, logout }}>
```

- [ ] **Step 5: Login.tsx 수정** — `username` state를 `email`로, 라벨 "아이디"→"이메일", `authApi.login(email, password)`, input `type="email" autoComplete="email"`. 폼 하단에 안내 추가:

```tsx
<p className="text-xs text-gray-400">
  계정이 없나요? 초대 링크를 통해 가입할 수 있습니다.
</p>
```

- [ ] **Step 6: Layout.tsx 수정** — `useAuth()` 반환 변경 반영:

```tsx
const { authEnabled, user, logout } = useAuth()
```

`{username && ...}` 두 곳을 다음으로 교체:

```tsx
{user && <span className="text-xs text-gray-400">{user.display_name || user.email}</span>}
```

네비게이션 링크 목록에 admin 전용 링크 추가(기존 네비 항목들과 같은 스타일로, 데스크톱/모바일 두 곳 모두):

```tsx
{user?.role === 'admin' && (
  <a href="/admin" className="text-xs text-amber-600 hover:underline">관리자</a>
)}
```

- [ ] **Step 7: 빌드 확인**

Run: `cd frontend && npm run build`
Expected: tsc 오류 0, 빌드 성공. (`useAuth`의 `username` 참조가 남아 있으면 tsc가 알려준다 — 모두 `user`로 수정)

- [ ] **Step 8: Commit**

```bash
git add frontend/src app/static/ui
git commit -m "feat(ui): 이메일 로그인 + 사용자 컨텍스트(role) 노출"
```

---

### Task 10: 프론트 — 초대 가입 페이지 (/signup)

**Files:**
- Create: `frontend/src/pages/Signup.tsx`
- Modify: `frontend/src/main.tsx`

- [ ] **Step 1: Signup.tsx 작성**

```tsx
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { authApi } from '../api/auth'

export default function Signup() {
  const [params] = useSearchParams()
  const token = params.get('token') || ''
  const [email, setEmail] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [password2, setPassword2] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (password !== password2) {
      setError('비밀번호가 일치하지 않습니다.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await authApi.signup(token, email, password, displayName)
      window.location.href = '/' // 가입 시 자동 로그인 → 앱으로
    } catch (err) {
      setError((err as Error).message || '가입에 실패했습니다.')
    } finally {
      setBusy(false)
    }
  }

  if (!token) {
    return (
      <div className="min-h-screen flex items-center justify-center text-gray-500 px-4 text-center">
        초대 링크가 올바르지 않습니다. 관리자에게 초대를 요청하세요.
      </div>
    )
  }

  const input =
    'w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500'
  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 p-4">
      <form onSubmit={submit} className="bg-white rounded-xl shadow-sm p-6 w-full max-w-sm space-y-4">
        <h1 className="text-xl font-bold text-gray-900">초대 가입</h1>
        {error && (
          <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</p>
        )}
        <div>
          <label className="block text-sm text-gray-600 mb-1">이메일</label>
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)}
            autoFocus autoComplete="email" required className={input} />
        </div>
        <div>
          <label className="block text-sm text-gray-600 mb-1">표시 이름 (선택)</label>
          <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} className={input} />
        </div>
        <div>
          <label className="block text-sm text-gray-600 mb-1">비밀번호 (8자 이상)</label>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
            autoComplete="new-password" required minLength={8} className={input} />
        </div>
        <div>
          <label className="block text-sm text-gray-600 mb-1">비밀번호 확인</label>
          <input type="password" value={password2} onChange={(e) => setPassword2(e.target.value)}
            autoComplete="new-password" required minLength={8} className={input} />
        </div>
        <button type="submit" disabled={busy}
          className="w-full bg-blue-600 text-white rounded-lg py-2 text-sm font-medium hover:bg-blue-700 disabled:opacity-50">
          {busy ? '가입 중…' : '가입하기'}
        </button>
      </form>
    </div>
  )
}
```

- [ ] **Step 2: main.tsx 수정** — `/signup`은 인증 게이트(AuthProvider) 밖에서 렌더:

```tsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import App from './App'
import AuthProvider from './auth/AuthProvider'
import Signup from './pages/Signup'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter basename="/">
      <Routes>
        <Route path="/signup" element={<Signup />} />
        <Route
          path="*"
          element={
            <AuthProvider>
              <App />
            </AuthProvider>
          }
        />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
)
```

- [ ] **Step 3: 빌드 확인**

Run: `cd frontend && npm run build`
Expected: 성공. (백엔드 `spa_fallback`은 `/signup`을 React로 서빙한다 — `app/main.py:111` 제외 목록에 없음을 확인)

- [ ] **Step 4: Commit**

```bash
git add frontend/src app/static/ui
git commit -m "feat(ui): 초대 가입 페이지 (/signup?token=)"
```

---

### Task 11: 프론트 — 관리자 페이지 (/admin)

**Files:**
- Create: `frontend/src/api/admin.ts`, `frontend/src/pages/Admin.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: api/admin.ts 작성**

```typescript
import { rootApi } from './http'

export interface AdminUser {
  user_id: number
  email: string
  display_name: string | null
  role: string
  status: string
  plan_id: number
  last_login_at: string | null
  created_at: string
}

export interface PlanInfo {
  plan_id: number
  slug: string
  name: string
  max_groups: number
  max_channels_total: number
  max_analyses_per_day: number
  max_video_minutes: number
  min_poll_interval_min: number
  is_default: boolean
}

export interface Invite {
  invite_id: number
  token: string
  plan_id: number
  memo: string | null
  expires_at: string
  used_by: number | null
  used_at: string | null
  created_at: string
}

export interface InviteCreated extends Invite {
  signup_url: string
}

export const adminApi = {
  users: () => rootApi.get<AdminUser[]>('/admin/users'),
  plans: () => rootApi.get<PlanInfo[]>('/admin/plans'),
  invites: () => rootApi.get<Invite[]>('/admin/invitations'),
  createInvite: (planSlug: string | null, memo: string, expiresDays: number) =>
    rootApi.post<InviteCreated>('/admin/invitations', {
      plan_slug: planSlug, memo: memo || null, expires_days: expiresDays,
    }),
  revokeInvite: (inviteId: number) => rootApi.del<void>(`/admin/invitations/${inviteId}`),
}
```

- [ ] **Step 2: Admin.tsx 작성**

```tsx
import { useCallback, useEffect, useState } from 'react'
import { adminApi, type AdminUser, type Invite, type PlanInfo } from '../api/admin'

export default function Admin() {
  const [users, setUsers] = useState<AdminUser[]>([])
  const [plans, setPlans] = useState<PlanInfo[]>([])
  const [invites, setInvites] = useState<Invite[]>([])
  const [error, setError] = useState<string | null>(null)
  const [memo, setMemo] = useState('')
  const [planSlug, setPlanSlug] = useState<string>('')
  const [createdUrl, setCreatedUrl] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const [u, p, i] = await Promise.all([adminApi.users(), adminApi.plans(), adminApi.invites()])
      setUsers(u); setPlans(p); setInvites(i); setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const createInvite = async () => {
    try {
      const r = await adminApi.createInvite(planSlug || null, memo, 7)
      setCreatedUrl(r.signup_url)
      setMemo('')
      await load()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const revoke = async (id: number) => {
    try { await adminApi.revokeInvite(id); await load() } catch (e) { setError((e as Error).message) }
  }

  const planName = (id: number) => plans.find((p) => p.plan_id === id)?.name ?? id

  return (
    <div className="max-w-4xl mx-auto p-6 space-y-8">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900">관리자</h1>
        <a href="/" className="text-sm text-blue-600 hover:underline">← 앱으로</a>
      </div>
      {error && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</p>}

      <section className="space-y-3">
        <h2 className="font-semibold text-gray-800">사용자</h2>
        <div className="bg-white rounded-xl shadow-sm overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-gray-500 border-b">
                <th className="px-3 py-2">이메일</th><th className="px-3 py-2">이름</th>
                <th className="px-3 py-2">역할</th><th className="px-3 py-2">상태</th>
                <th className="px-3 py-2">플랜</th><th className="px-3 py-2">최근 로그인</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.user_id} className="border-b last:border-0">
                  <td className="px-3 py-2">{u.email}</td>
                  <td className="px-3 py-2">{u.display_name || '-'}</td>
                  <td className="px-3 py-2">{u.role}</td>
                  <td className="px-3 py-2">{u.status}</td>
                  <td className="px-3 py-2">{planName(u.plan_id)}</td>
                  <td className="px-3 py-2 text-gray-400">
                    {u.last_login_at ? new Date(u.last_login_at).toLocaleString() : '-'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="font-semibold text-gray-800">초대</h2>
        <div className="bg-white rounded-xl shadow-sm p-4 space-y-3">
          <div className="flex flex-wrap gap-2 items-end">
            <div>
              <label className="block text-xs text-gray-500 mb-1">플랜</label>
              <select value={planSlug} onChange={(e) => setPlanSlug(e.target.value)}
                className="border border-gray-300 rounded-lg px-2 py-1.5 text-sm">
                <option value="">기본 (free)</option>
                {plans.map((p) => <option key={p.slug} value={p.slug}>{p.name}</option>)}
              </select>
            </div>
            <div className="flex-1 min-w-40">
              <label className="block text-xs text-gray-500 mb-1">메모 (초대 대상)</label>
              <input value={memo} onChange={(e) => setMemo(e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-2 py-1.5 text-sm" />
            </div>
            <button onClick={createInvite}
              className="bg-blue-600 text-white rounded-lg px-4 py-1.5 text-sm hover:bg-blue-700">
              초대 링크 발급 (7일)
            </button>
          </div>
          {createdUrl && (
            <p className="text-xs bg-green-50 border border-green-200 rounded-lg px-3 py-2 break-all">
              발급됨: <code>{createdUrl}</code>
              <button onClick={() => navigator.clipboard.writeText(createdUrl)}
                className="ml-2 text-blue-600 hover:underline">복사</button>
            </p>
          )}
          <ul className="divide-y">
            {invites.map((i) => (
              <li key={i.invite_id} className="py-2 flex items-center justify-between text-sm">
                <span>
                  #{i.invite_id} {i.memo || '(메모 없음)'} · {planName(i.plan_id)} ·
                  만료 {new Date(i.expires_at).toLocaleDateString()} ·
                  {i.used_at ? ` 사용됨(user ${i.used_by})` : ' 미사용'}
                </span>
                {!i.used_at && (
                  <button onClick={() => revoke(i.invite_id)}
                    className="text-red-600 text-xs hover:underline">회수</button>
                )}
              </li>
            ))}
          </ul>
        </div>
      </section>
    </div>
  )
}
```

- [ ] **Step 3: App.tsx 라우트 추가** — import에 `import Admin from './pages/Admin'`, `<Route path="/" .../>` 위에:

```tsx
<Route path="/admin" element={<Admin />} />
```

(비관리자가 URL로 진입하면 API가 403을 반환해 에러 배너가 뜬다 — Phase A에서는 이 수준으로 충분)

- [ ] **Step 4: 빌드 + 프론트 테스트**

Run: `cd frontend && npm run build && npm test`
Expected: 빌드 성공, vitest PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src app/static/ui
git commit -m "feat(ui): 관리자 페이지 — 사용자 목록/초대 발급·회수"
```

---

### Task 12: 통합 검증 + 문서 갱신

**Files:**
- Modify: `docs/architecture.md:17-21`, `README.md`

- [ ] **Step 1: 전체 테스트**

Run: `pytest -q`
Expected: 전부 PASS

- [ ] **Step 2: (환경 가용 시) 실 DB E2E 수동 검증**

로컬 PG + `.env` 구성 상태에서 `uvicorn app.main:app` 기동 후:

1. 부팅 로그에 오류 없음, `app.plans` 2행 / `app.users`에 admin 1행 / 기존 그룹 `owner_user_id` 백필 확인 (`SELECT slug, owner_user_id FROM app.groups`).
2. admin으로 로그인(email = `admin_bootstrap_email(AUTH_USERNAME)`) → 기존 그룹 전부 보임.
3. `/admin`에서 초대 발급 → 시크릿 창에서 `signup_url` 접속 → 가입 → 로그인됨 → 그룹 목록 비어 있음 → 그룹 생성 → 본인 그룹만 보임.
4. 새 계정으로 admin 그룹 slug 직접 접근(`/api/groups/{admin그룹slug}`) → 404.

PG 없으면 skip하고 보고에 명시.

- [ ] **Step 3: architecture.md 비목표 갱신** — `docs/architecture.md`의 비목표 항목:

```
- 다중 사용자(멀티 테넌트) 권한 분리. 본 프로젝트는 단일 운영자 기준.
```

을 다음으로 교체:

```
- ~~다중 사용자(멀티 테넌트) 권한 분리~~ → 2026-07-03부로 범위 편입.
  `docs/superpowers/specs/2026-07-03-multi-tenant-design.md` 참고.
```

- [ ] **Step 4: README 인증 안내 갱신** — README.md 실행 섹션 아래에 추가:

```markdown
## 계정

- 최초 부팅 시 `.env`의 `AUTH_USERNAME`/`AUTH_PASSWORD`로 admin 계정이 자동 생성된다
  (로그인 ID는 이메일 형식 — `AUTH_USERNAME`이 이메일이 아니면 `{username}@local`).
- 일반 사용자는 관리자가 발급한 초대 링크(`/signup?token=...`)로 가입한다.
- `AUTH_PASSWORD` 미설정 + 사용자 0명이면 인증 비활성(개발 모드).
```

- [ ] **Step 5: Commit**

```bash
git add docs/architecture.md README.md
git commit -m "docs: 멀티테넌트 범위 편입 반영 및 계정 안내 추가"
```

---

## 셀프 리뷰 체크 결과 (계획 작성 시 수행)

- **스펙 커버리지**: §2.1(users)→T3, §2.2(invitations)→T3/T6, §2.3(plans, 테이블만)→T3/T4, §2.8(owner/자동 slug/백필)→T3/T4/T7, §3.1(argon2/시드/세션)→T1/T4/T5, §3.2(require_user/admin/get_owned)→T5/T7, Phase A 검증 기준→T12. user_limits 테이블은 Phase B(강제 시점)로 미룸 — 스펙 §7 Phase B 산출물에 명시돼 있어 일관.
- **타입 일관성**: `CurrentUser`(auth.py) ↔ deps/groups/admin의 참조 일치. FakeSession은 test_auth.py 정의를 test_signup/test_ownership이 import.
- **주의점**: Task 5에서 main.py의 admin import는 Task 8 전까지 없음 — Task 5 Step 5에 명시함.
