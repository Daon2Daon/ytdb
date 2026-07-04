# Phase B-0a: 공유 분석 캐시 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 같은 영상 × 같은 프리셋 × 같은 모델 조합의 AI 분석을 시스템 전체에서 1회만 수행하고, 구독한 모든 그룹에 결과를 복사한다 (스펙 §2.9).

**Architecture:** 제어 평면에 `prompt_presets`(불변 프리셋), `analysis_cache`(UNIQUE 제약이 동시 분석 락 겸용), `analysis_deliveries`(사용자별 전달 원장) 3개 테이블을 추가한다. 분석 진입점(`monitor_service._run_analysis`)에서 그룹의 프롬프트를 해석해 프리셋 그룹만 캐시에 참여시키고, 직접 프롬프트 그룹(기존 admin 그룹)과 즉시 분석(custom_prompt)은 기존 경로를 그대로 탄다. 그룹 스키마 구조는 무변경 — 캐시 적중 시에도 그룹 스키마에 결과를 복사하므로 UI/알림/다이제스트가 전부 기존대로 동작한다.

**Tech Stack:** SQLAlchemy 2 async(제어 평면 app 스키마), PostgreSQL `INSERT ... ON CONFLICT`(레이스 방지 선점), FastAPI(프리셋 관리자 API), pytest(DB-less: 순수 함수 + FakeSession + 라우트 등록 검증).

**스코프 노트:** 스펙 §2.9의 B-0 중 **중앙 채널 레지스트리(channel_registry) 폴링은 본 계획 범위 외**(후속 B-0b 계획). 분석 캐시는 현행 그룹별 폴링 그대로 동작한다 — 각 그룹이 같은 영상을 각자 발견해도 분석 단계에서 캐시가 중복을 제거한다. YouTube 쿼터 최적화는 B-0b에서.

**전제 스펙:** `docs/superpowers/specs/2026-07-03-multi-tenant-design.md` §2.6(프리셋), §2.9(캐시), §8(프리셋 불변성·레이스 위험 대응)

---

## 배경: 기존 코드 이해 (실행 전 필독)

- **분석 흐름**: `scheduler` 틱 → `monitor_service.run_pending_analysis_once()` → 그룹 순회 `_analyze_group()` → pending 1건 claim(FOR UPDATE SKIP LOCKED, 상태→processing) → `_run_analysis()` → `analyzer.build_analysis_pipeline()`(그룹 AI설정+프롬프트) → `AnalysisPipeline.run_and_save()`(Gemini native 호출 + 그룹 스키마 저장) → 성공 시 `_notify_after_analysis()`.
- **즉시 분석**: `monitor_service.analyze_specific_video(group, video_pk, custom_prompt)` → 같은 `_run_analysis(custom_prompt=...)`.
- **프롬프트**: `settings_manager.get_prompts(group_id)` → `PromptSettings(analysis_prompt, digest_prompt)` (그룹 설정 `prompts` 카테고리). 비어 있으면 `analyzer.DEFAULT_ANALYSIS_PROMPT` 폴백.
- **다이제스트 프롬프트**: `digest_service.py:389` — `mgr.get_prompts(group_id).digest_prompt` 폴백 사용.
- **설정 PUT**: `routers/settings.py`의 `put_settings`는 카테고리만 검증하고 key는 자유 — `prompts` 카테고리에 `preset_id` key를 저장하는 데 라우터 수정 불필요.
- **저장 로직**: `AnalysisPipeline.save_to_db()`가 video_analysis upsert + 태그 저장 + 상태 done 전환을 담당. 캐시 적중 경로가 LLM 클라이언트 없이 이 로직을 재사용해야 하므로 모듈 함수로 추출한다(Task 5).
- **토큰 수**: 현행 `analyze_video_native`는 usageMetadata를 노출하지 않는다. 캐시의 input/output_tokens는 nullable로 두고 **Phase C에서 배선**한다(본 계획에서는 None 저장).
- **테스트 스타일**: DB-less. `tests/test_auth.py`의 `FakeSession`(scripted results) 패턴 재사용 가능. SQL 자체는 최종 실 DB E2E로 검증(Task 8).
- **admin API 패턴**: `routers/admin.py` — 라우터 레벨 `dependencies=[Depends(require_admin)]`. 스키마는 `schemas/admin.py`.

## 파일 구조

```
생성:
  app/models/control/prompt_preset.py     app.prompt_presets 모델
  app/models/control/analysis_cache.py    app.analysis_cache 모델
  app/models/control/analysis_delivery.py app.analysis_deliveries 모델
  app/services/preset_service.py          프리셋 로드(TTL 캐시) + resolve_prompts
  app/services/analysis_cache_service.py  claim/complete/fail/deliver
  tests/test_preset_models.py             모델 등록/컬럼
  tests/test_preset_admin_api.py          프리셋 관리자 API
  tests/test_preset_resolution.py         resolve_prompts 분기
  tests/test_analysis_cache_service.py    claim 분기 (FakeSession)
  tests/test_cache_integration.py         _should_use_cache + result_from_cache
수정:
  app/control_db.py                       ensure_control_schema 모델 import 추가
  app/schemas/admin.py                    PresetCreate/PresetPatch/PresetOut
  app/routers/admin.py                    /api/admin/presets CRUD
  app/services/settings_types.py          PromptSettings.preset_id 추가
  app/services/settings_manager.py        get_prompts에서 preset_id 파싱
  app/services/analyzer.py                저장 로직 모듈 함수 추출 + result_from_cache
  app/services/digest_service.py          다이제스트 프롬프트도 resolve_prompts 경유
  app/services/monitor_service.py         _run_analysis 캐시 통합
```

---

### Task 1: prompt_presets 모델

**Files:**
- Create: `app/models/control/prompt_preset.py`
- Modify: `app/control_db.py` (ensure_control_schema의 모델 import)
- Test: `tests/test_preset_models.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_preset_models.py`:

```python
"""B-0a 제어 평면 신규 모델(prompt_presets/analysis_cache/analysis_deliveries) 검증."""

from app.control_db import APP_SCHEMA, Base


def test_prompt_presets_registered():
    from app.models.control.prompt_preset import PromptPreset

    assert f"{APP_SCHEMA}.prompt_presets" in Base.metadata.tables
    cols = {c.name for c in PromptPreset.__table__.columns}
    assert {"preset_id", "name", "description", "analysis_prompt",
            "digest_prompt", "is_active", "created_at", "updated_at"} <= cols
```

- [ ] **Step 2: 실패 확인**

Run: `source .venv/bin/activate && pytest tests/test_preset_models.py -v`
Expected: FAIL — `ModuleNotFoundError: app.models.control.prompt_preset`

- [ ] **Step 3: 구현** — `app/models/control/prompt_preset.py`:

```python
"""app.prompt_presets — 관리자가 만드는 분석/다이제스트 프롬프트 프리셋.

프리셋은 불변(immutable)이다: analysis_prompt/digest_prompt 본문은 생성 후 수정하지
않는다. 본문을 바꾸려면 새 프리셋을 만들고 구버전을 비활성화한다(is_active=false).
공유 분석 캐시(§2.9)의 캐시 키가 preset_id이므로, 본문이 바뀌면 캐시 정합성이 깨진다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class PromptPreset(Base):
    __tablename__ = "prompt_presets"
    __table_args__ = {"schema": APP_SCHEMA}

    preset_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    digest_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
```

`app/control_db.py`의 `ensure_control_schema()` import 줄을 다음으로 교체:

```python
    from app.models.control import group, invitation, plan, prompt_preset, setting, user  # noqa: F401
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_preset_models.py -v`
Expected: 1 PASS

- [ ] **Step 5: Commit**

```bash
git add app/models/control/prompt_preset.py app/control_db.py tests/test_preset_models.py
git commit -m "feat: prompt_presets 모델 (불변 프리셋 — 캐시 키의 전제)"
```

---

### Task 2: analysis_cache / analysis_deliveries 모델

**Files:**
- Create: `app/models/control/analysis_cache.py`, `app/models/control/analysis_delivery.py`
- Modify: `app/control_db.py`
- Test: `tests/test_preset_models.py` (추가)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_preset_models.py`에 추가:

```python
def test_analysis_cache_registered():
    from app.models.control.analysis_cache import AnalysisCache

    assert f"{APP_SCHEMA}.analysis_cache" in Base.metadata.tables
    cols = {c.name for c in AnalysisCache.__table__.columns}
    assert {"cache_id", "video_id", "preset_id", "model", "status", "analysis",
            "input_tokens", "output_tokens", "created_at", "completed_at"} <= cols
    # UNIQUE(video_id, preset_id, model)가 동시 분석 방지 락 역할 (스펙 §2.9)
    uniques = [
        {c.name for c in con.columns}
        for con in AnalysisCache.__table__.constraints
        if con.__class__.__name__ == "UniqueConstraint"
    ]
    assert {"video_id", "preset_id", "model"} in uniques


def test_analysis_deliveries_registered():
    from app.models.control.analysis_delivery import AnalysisDelivery

    assert f"{APP_SCHEMA}.analysis_deliveries" in Base.metadata.tables
    cols = {c.name for c in AnalysisDelivery.__table__.columns}
    assert {"delivery_id", "user_id", "group_id", "cache_id", "created_at"} <= cols
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_preset_models.py -v`
Expected: 신규 2건 FAIL (ModuleNotFoundError)

- [ ] **Step 3: 구현**

`app/models/control/analysis_cache.py`:

```python
"""app.analysis_cache — 공유 분석 캐시 (스펙 §2.9).

캐시 키 = (video_id, preset_id, model). UNIQUE 제약이 동시 분석 방지 락을 겸한다:
INSERT ... ON CONFLICT DO NOTHING의 성공 여부로 분석 수행권을 선점한다.
status: pending(선점됨/분석 중) | completed(analysis 사용 가능) | failed(재클레임 가능).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class AnalysisCache(Base):
    __tablename__ = "analysis_cache"
    __table_args__ = (
        UniqueConstraint("video_id", "preset_id", "model", name="uq_analysis_cache_key"),
        {"schema": APP_SCHEMA},
    )

    cache_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(Text, nullable=False)  # YouTube 영상 ID
    preset_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{APP_SCHEMA}.prompt_presets.preset_id"), nullable=False
    )
    model: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    analysis: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    # 토큰 수는 Phase C(사용량 원장)에서 배선. 현행 LLM 클라이언트는 usage 미노출.
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

`app/models/control/analysis_delivery.py`:

```python
"""app.analysis_deliveries — 사용자별 분석 전달 원장 (스펙 §2.9).

캐시 히트/미스 무관하게 "그룹에 분석이 전달된 사건"을 1행씩 기록한다.
Phase B의 max_analyses_per_day 쿼터 카운트와 향후 과금의 기반 데이터.
group_id에 FK를 두지 않아 그룹 삭제 후에도 원장이 보존된다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class AnalysisDelivery(Base):
    __tablename__ = "analysis_deliveries"
    __table_args__ = (
        Index("analysis_deliveries_user_created", "user_id", "created_at"),
        {"schema": APP_SCHEMA},
    )

    delivery_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{APP_SCHEMA}.users.user_id"), nullable=False
    )
    group_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cache_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{APP_SCHEMA}.analysis_cache.cache_id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

`app/control_db.py` import 줄 교체:

```python
    from app.models.control import (  # noqa: F401
        analysis_cache,
        analysis_delivery,
        group,
        invitation,
        plan,
        prompt_preset,
        setting,
        user,
    )
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_preset_models.py -v && pytest -q`
Expected: 전부 PASS (전체 175 예상: 172 + 3)

- [ ] **Step 5: Commit**

```bash
git add app/models/control/analysis_cache.py app/models/control/analysis_delivery.py \
        app/control_db.py tests/test_preset_models.py
git commit -m "feat: analysis_cache/analysis_deliveries 모델 (공유 분석 캐시 §2.9)"
```

---

### Task 3: 프리셋 관리자 API

**Files:**
- Modify: `app/schemas/admin.py`, `app/routers/admin.py`
- Test: `tests/test_preset_admin_api.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_preset_admin_api.py`:

```python
"""프리셋 관리자 API: 라우트 등록 + 비관리자 403 + 불변성(본문 PATCH 불가)."""

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


def test_preset_routes_registered():
    paths = {r.path for r in app.routes}
    assert "/api/admin/presets" in paths
    assert "/api/admin/presets/{preset_id}" in paths


def test_non_admin_forbidden():
    async def _dep():
        return USER
    app.dependency_overrides[require_user] = _dep
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/api/admin/presets").status_code == 403
    assert c.post("/api/admin/presets", json={}).status_code == 403


def test_patch_schema_rejects_prompt_body_changes():
    """PresetPatch에 analysis_prompt/digest_prompt 필드가 없어야 한다(불변성)."""
    from app.schemas.admin import PresetPatch

    fields = set(PresetPatch.model_fields.keys())
    assert fields == {"name", "description", "is_active"}
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_preset_admin_api.py -v`
Expected: FAIL (라우트 미등록 / ImportError)

- [ ] **Step 3: 스키마 추가** — `app/schemas/admin.py` 끝에 추가:

```python
class PresetCreate(BaseModel):
    name: str
    description: Optional[str] = None
    analysis_prompt: str
    digest_prompt: str = ""


class PresetPatch(BaseModel):
    """프리셋 본문(analysis_prompt/digest_prompt)은 불변 — 여기 두지 않는다(스펙 §8).

    본문 변경은 새 프리셋 생성 + 구버전 is_active=false로 처리한다.
    """

    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class PresetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    preset_id: int
    name: str
    description: Optional[str]
    analysis_prompt: str
    digest_prompt: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
```

- [ ] **Step 4: 라우터 추가** — `app/routers/admin.py`:

import에 추가:

```python
from app.models.control.prompt_preset import PromptPreset
from app.schemas.admin import PresetCreate, PresetOut, PresetPatch
from app.services.preset_service import invalidate_preset_cache
```

(주의: `preset_service`는 Task 4에서 생성 — Task 3 시점에는 `invalidate_preset_cache` import와
호출 줄을 생략하고, Task 4에서 추가한다.)

라우터 끝에 추가:

```python
@router.get("/presets", response_model=list[PresetOut])
async def list_presets(session: AsyncSession = Depends(get_session)) -> list[PromptPreset]:
    result = await session.execute(select(PromptPreset).order_by(PromptPreset.preset_id.desc()))
    return list(result.scalars().all())


@router.post("/presets", response_model=PresetOut, status_code=201)
async def create_preset(
    payload: PresetCreate, session: AsyncSession = Depends(get_session)
) -> PromptPreset:
    preset = PromptPreset(
        name=payload.name,
        description=payload.description,
        analysis_prompt=payload.analysis_prompt,
        digest_prompt=payload.digest_prompt,
        is_active=True,
    )
    session.add(preset)
    await session.commit()
    await session.refresh(preset)
    return preset


@router.patch("/presets/{preset_id}", response_model=PresetOut)
async def patch_preset(
    preset_id: int, payload: PresetPatch, session: AsyncSession = Depends(get_session)
) -> PromptPreset:
    preset = await session.get(PromptPreset, preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="프리셋을 찾을 수 없습니다.")
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(preset, field, value)
    await session.commit()
    await session.refresh(preset)
    return preset
```

- [ ] **Step 5: 통과 확인**

Run: `pytest tests/test_preset_admin_api.py -v && pytest -q`
Expected: 전부 PASS

- [ ] **Step 6: Commit**

```bash
git add app/schemas/admin.py app/routers/admin.py tests/test_preset_admin_api.py
git commit -m "feat: 프리셋 관리자 API (생성/목록/메타 수정 — 본문 불변)"
```

---

### Task 4: preset_service (프리셋 해석) + 설정/다이제스트 연동

**Files:**
- Create: `app/services/preset_service.py`
- Modify: `app/services/settings_types.py`, `app/services/settings_manager.py:199-204`, `app/services/digest_service.py:389-391`, `app/routers/admin.py` (invalidate 배선)
- Test: `tests/test_preset_resolution.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_preset_resolution.py`:

```python
"""resolve_prompts 분기: 프리셋 우선, 비활성/미존재 폴백, 미지정 직접 프롬프트."""

import pytest

from app.services import preset_service
from app.services.preset_service import PresetData, ResolvedPrompts, resolve_prompts
from app.services.settings_types import PromptSettings


class FakeManager:
    def __init__(self, prompts: PromptSettings):
        self._prompts = prompts

    async def get_prompts(self, group_id: int) -> PromptSettings:
        return self._prompts


def _patch(monkeypatch, prompts: PromptSettings, preset: PresetData | None):
    monkeypatch.setattr(preset_service, "get_settings_manager", lambda: FakeManager(prompts))

    async def fake_get_preset(preset_id: int):
        return preset

    monkeypatch.setattr(preset_service, "get_preset", fake_get_preset)


async def test_preset_active_wins(monkeypatch):
    _patch(
        monkeypatch,
        PromptSettings(analysis_prompt="직접", digest_prompt="직접d", preset_id=7),
        PresetData(preset_id=7, analysis_prompt="프리셋", digest_prompt="프리셋d", is_active=True),
    )
    r = await resolve_prompts(1)
    assert r == ResolvedPrompts(analysis_prompt="프리셋", digest_prompt="프리셋d", preset_id=7)


async def test_inactive_preset_falls_back_to_direct(monkeypatch):
    _patch(
        monkeypatch,
        PromptSettings(analysis_prompt="직접", digest_prompt="", preset_id=7),
        PresetData(preset_id=7, analysis_prompt="프리셋", digest_prompt="", is_active=False),
    )
    r = await resolve_prompts(1)
    assert r.preset_id is None and r.analysis_prompt == "직접"


async def test_missing_preset_falls_back(monkeypatch):
    _patch(monkeypatch, PromptSettings(analysis_prompt="직접", preset_id=99), None)
    r = await resolve_prompts(1)
    assert r.preset_id is None and r.analysis_prompt == "직접"


async def test_no_preset_id_uses_direct(monkeypatch):
    _patch(monkeypatch, PromptSettings(analysis_prompt="직접", digest_prompt="d"), None)
    r = await resolve_prompts(1)
    assert r == ResolvedPrompts(analysis_prompt="직접", digest_prompt="d", preset_id=None)


def test_prompt_settings_has_preset_id_field():
    assert PromptSettings().preset_id is None
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_preset_resolution.py -v`
Expected: FAIL (ModuleNotFoundError / preset_id 필드 없음)

- [ ] **Step 3: PromptSettings 확장** — `app/services/settings_types.py`의 PromptSettings 교체:

```python
@dataclass
class PromptSettings:
    """그룹별 프롬프트. 비어 있으면 코드 기본값 사용.

    preset_id가 설정되면 프리셋(app.prompt_presets)이 우선한다 — 해석은
    preset_service.resolve_prompts()가 담당. 직접 프롬프트는 관리자 그룹 전용.
    """

    analysis_prompt: str = ""
    digest_prompt: str = ""
    preset_id: Optional[int] = None
```

(`from typing import Optional`이 파일 상단에 없으면 추가.)

`app/services/settings_manager.py`의 `get_prompts` 교체:

```python
    async def get_prompts(self, group_id: int) -> PromptSettings:
        d = await self.get_typed(group_id, "prompts")
        raw_preset = d.get("preset_id")
        try:
            preset_id = int(raw_preset) if raw_preset not in (None, "", 0, "0") else None
        except (TypeError, ValueError):
            preset_id = None
        return PromptSettings(
            analysis_prompt=str(d.get("analysis_prompt") or ""),
            digest_prompt=str(d.get("digest_prompt") or ""),
            preset_id=preset_id,
        )
```

- [ ] **Step 4: preset_service 구현** — `app/services/preset_service.py`:

```python
"""프리셋 로드/해석. 분석 경로가 영상마다 제어 DB를 치지 않도록 TTL 캐시를 둔다.

resolve_prompts()가 그룹 프롬프트의 단일 진입점이다:
- preset_id가 설정되고 프리셋이 활성이면 프리셋 본문 사용 (캐시 참여 대상)
- 그 외(직접 프롬프트/비활성/미존재)는 직접 프롬프트 폴백 (캐시 비참여)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select

from app.control_db import get_sessionmaker
from app.models.control.prompt_preset import PromptPreset
from app.services.settings_manager import get_settings_manager

_CACHE_TTL_SEC = 60.0
_cache: dict[int, tuple[float, Optional["PresetData"]]] = {}


@dataclass(frozen=True)
class PresetData:
    preset_id: int
    analysis_prompt: str
    digest_prompt: str
    is_active: bool


@dataclass(frozen=True)
class ResolvedPrompts:
    analysis_prompt: str
    digest_prompt: str
    # None = 직접 프롬프트(공유 캐시 비참여). int = 캐시 키로 쓰는 프리셋.
    preset_id: Optional[int]


def invalidate_preset_cache(preset_id: Optional[int] = None) -> None:
    if preset_id is None:
        _cache.clear()
    else:
        _cache.pop(preset_id, None)


async def get_preset(preset_id: int) -> Optional[PresetData]:
    now = time.monotonic()
    hit = _cache.get(preset_id)
    if hit is not None and now < hit[0]:
        return hit[1]
    async with get_sessionmaker()() as session:
        row = (
            await session.execute(
                select(PromptPreset).where(PromptPreset.preset_id == preset_id)
            )
        ).scalar_one_or_none()
    data = (
        PresetData(
            preset_id=row.preset_id,
            analysis_prompt=row.analysis_prompt,
            digest_prompt=row.digest_prompt,
            is_active=row.is_active,
        )
        if row is not None
        else None
    )
    _cache[preset_id] = (now + _CACHE_TTL_SEC, data)
    return data


async def resolve_prompts(group_id: int) -> ResolvedPrompts:
    prompts = await get_settings_manager().get_prompts(group_id)
    if prompts.preset_id is not None:
        preset = await get_preset(prompts.preset_id)
        if preset is not None and preset.is_active:
            return ResolvedPrompts(
                analysis_prompt=preset.analysis_prompt,
                digest_prompt=preset.digest_prompt,
                preset_id=preset.preset_id,
            )
        # 비활성/미존재 프리셋 → 직접 프롬프트 폴백(분석은 계속되게, 캐시 비참여)
    return ResolvedPrompts(
        analysis_prompt=prompts.analysis_prompt,
        digest_prompt=prompts.digest_prompt,
        preset_id=None,
    )
```

- [ ] **Step 5: 다이제스트 연동** — `app/services/digest_service.py`의 프롬프트 폴백부(389행 부근):

```python
    prompts = await mgr.get_prompts(group_id)
```

를 다음으로 교체:

```python
    from app.services.preset_service import resolve_prompts

    prompts = await resolve_prompts(group_id)
```

(바로 아래 `prompts.digest_prompt` 사용은 ResolvedPrompts에도 같은 필드가 있으므로 무변경.)

- [ ] **Step 6: 관리자 API invalidate 배선** — `app/routers/admin.py`:

import 추가:

```python
from app.services.preset_service import invalidate_preset_cache
```

`create_preset`의 `await session.refresh(preset)` 다음 줄과
`patch_preset`의 `await session.refresh(preset)` 다음 줄에 각각 추가:

```python
    invalidate_preset_cache(preset.preset_id)
```

- [ ] **Step 7: 통과 확인**

Run: `pytest tests/test_preset_resolution.py tests/test_preset_admin_api.py -v && pytest -q`
Expected: 전부 PASS

- [ ] **Step 8: Commit**

```bash
git add app/services/preset_service.py app/services/settings_types.py \
        app/services/settings_manager.py app/services/digest_service.py \
        app/routers/admin.py tests/test_preset_resolution.py
git commit -m "feat: 프리셋 해석 서비스 (resolve_prompts) + 설정/다이제스트 연동"
```

---

### Task 5: analyzer 저장 로직 모듈화 + 캐시 결과 변환

**Files:**
- Modify: `app/services/analyzer.py`
- Test: `tests/test_cache_integration.py` (신규)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_cache_integration.py`:

```python
"""캐시 결과 → AnalysisPipelineResult 변환과 저장 함수 시그니처 검증."""

from app.services.analyzer import (
    PROMPT_VERSION,
    AnalysisPipelineResult,
    result_from_cache,
    save_analysis_to_group,
    save_tags_for_video,
)


def test_result_from_cache_shape():
    data = {"one_line": "요약", "short_summary_md": "본문", "tags": []}
    r = result_from_cache(data, model_name="gemini/gemini-2.5-flash", gateway_url="http://gw")
    assert isinstance(r, AnalysisPipelineResult)
    assert r.data == data
    assert r.route == "cache"
    assert r.model_name == "gemini/gemini-2.5-flash"
    assert r.gateway_url == "http://gw"
    assert r.prompt_version == PROMPT_VERSION


def test_module_level_save_functions_exist():
    # 캐시 적중 경로가 LLM 클라이언트 없이 호출할 수 있어야 한다(모듈 함수).
    import inspect

    assert inspect.iscoroutinefunction(save_analysis_to_group)
    assert inspect.iscoroutinefunction(save_tags_for_video)
    params = list(inspect.signature(save_analysis_to_group).parameters)
    assert params[:3] == ["session", "video_pk", "result"]
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_cache_integration.py -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: 구현** — `app/services/analyzer.py`:

(a) `AnalysisPipeline` 클래스 **앞**에 모듈 함수 추가. 본문은 기존
`AnalysisPipeline.save_to_db`/`_save_tags`의 코드를 그대로 옮긴다
(self 참조 제거, notify_callback은 인자로):

```python
def result_from_cache(
    data: Dict[str, Any], model_name: str, gateway_url: str = ""
) -> AnalysisPipelineResult:
    """공유 캐시(app.analysis_cache)의 analysis JSON → 파이프라인 결과 객체."""
    return AnalysisPipelineResult(
        data=data, route="cache", model_name=model_name, gateway_url=gateway_url
    )


async def save_tags_for_video(
    session: AsyncSession, video_pk: int, raw_tags: List[Dict[str, Any]]
) -> None:
    involved: list[int] = []
    for t in raw_tags:
        name = (t.get("name") or "").strip()
        if not name:
            continue
        tag_type = (t.get("type") or DEFAULT_TAG_TYPE).strip().lower()
        if tag_type not in ALLOWED_TAG_TYPES:
            tag_type = DEFAULT_TAG_TYPE
        weight = t.get("weight")
        ins = (
            pg_insert(Tag)
            .values(name=name, tag_type=tag_type)
            .on_conflict_do_nothing(index_elements=["name"])
            .returning(Tag.tag_pk)
        )
        tag_pk = (await session.execute(ins)).scalar()
        if tag_pk is None:
            tag_pk = (
                await session.execute(select(Tag.tag_pk).where(Tag.name == name))
            ).scalar()
        if tag_pk is None:
            continue
        await session.execute(
            pg_insert(VideoTag)
            .values(video_pk=video_pk, tag_pk=tag_pk, weight=weight)
            .on_conflict_do_nothing(index_elements=["video_pk", "tag_pk"])
        )
        involved.append(tag_pk)

    for tag_pk in involved:
        cnt = (
            await session.execute(
                select(func.count()).select_from(VideoTag).where(VideoTag.tag_pk == tag_pk)
            )
        ).scalar()
        await session.execute(
            update(Tag).where(Tag.tag_pk == tag_pk).values(video_count=cnt)
        )


async def save_analysis_to_group(
    session: AsyncSession,
    video_pk: int,
    result: AnalysisPipelineResult,
    notify_callback: Optional[Callable[[int], Awaitable[Any]]] = None,
) -> None:
    """분석 결과를 그룹 스키마에 저장 (video_analysis upsert + 태그 + 상태 done).

    LLM 호출 여부와 무관한 순수 저장 경로 — 캐시 적중 복사와 신규 분석이 공용.
    """
    data = result.data
    await session.execute(
        update(Video)
        .where(Video.video_pk == video_pk, Video.share_token.is_(None))
        .values(share_token=generate_share_token(), share_visibility=DEFAULT_VISIBILITY)
    )
    stmt = pg_insert(VideoAnalysis).values(
        video_pk=video_pk,
        one_line=data.get("one_line", ""),
        headline=data.get("headline"),
        short_summary_md=data.get("short_summary_md", ""),
        bullet_points=data.get("bullet_points"),
        full_analysis_md=data.get("full_analysis_md"),
        analysis_sections=data.get("analysis_sections"),
        key_points=data.get("key_points"),
        insights=data.get("insights"),
        entities=data.get("entities"),
        sentiment=data.get("sentiment"),
        confidence_score=_coerce_confidence(data.get("confidence_score")),
        model_name=result.model_name,
        gateway_url=result.gateway_url,
        prompt_version=result.prompt_version,
        analyzed_at=datetime.now(timezone.utc),
    )
    upsert = stmt.on_conflict_do_update(
        index_elements=["video_pk"],
        set_={
            c: stmt.excluded[c]
            for c in (
                "one_line",
                "headline",
                "short_summary_md",
                "bullet_points",
                "full_analysis_md",
                "analysis_sections",
                "key_points",
                "insights",
                "entities",
                "sentiment",
                "confidence_score",
                "model_name",
                "gateway_url",
                "prompt_version",
                "analyzed_at",
            )
        },
    )
    await session.execute(upsert)

    await save_tags_for_video(session, video_pk, data.get("tags") or [])

    await session.execute(
        update(Video).where(Video.video_pk == video_pk).values(analysis_status="done")
    )

    if notify_callback:
        await notify_callback(video_pk)
```

(b) 기존 `AnalysisPipeline.save_to_db`와 `AnalysisPipeline._save_tags`의 본문을
모듈 함수 위임으로 교체 (메서드 시그니처는 유지 — 기존 호출부 무변경):

```python
    async def save_to_db(
        self, session: AsyncSession, video_pk: int, result: AnalysisPipelineResult
    ) -> None:
        await save_analysis_to_group(
            session, video_pk, result, notify_callback=self._notify_callback
        )
```

(`_save_tags` 메서드는 삭제하고, 남은 참조가 없는지 `grep -n "_save_tags" app/`로 확인.)

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_cache_integration.py -v && pytest -q`
Expected: 전부 PASS (기존 분석 관련 테스트 회귀 없음)

- [ ] **Step 5: Commit**

```bash
git add app/services/analyzer.py tests/test_cache_integration.py
git commit -m "refactor: 분석 저장 로직 모듈 함수화 (캐시 적중 경로 공용화)"
```

---

### Task 6: analysis_cache_service (선점/완료/실패/전달)

**Files:**
- Create: `app/services/analysis_cache_service.py`
- Test: `tests/test_analysis_cache_service.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_analysis_cache_service.py`:

```python
"""캐시 선점(claim) 분기 검증. SQL 실행은 FakeSession으로 대체(실 SQL은 E2E에서)."""

from datetime import datetime, timedelta, timezone

import pytest

from app.services.analysis_cache_service import (
    CACHE_STALE_PENDING_MINUTES,
    ClaimOutcome,
    claim_or_get,
)


class FakeRow:
    def __init__(self, cache_id=1, status="completed", analysis=None, created_at=None):
        self.cache_id = cache_id
        self.status = status
        self.analysis = analysis if analysis is not None else {"one_line": "x"}
        self.created_at = created_at or datetime.now(timezone.utc)


class FakeResult:
    def __init__(self, scalar=None, row=None, rowcount=0):
        self._scalar = scalar
        self._row = row
        self.rowcount = rowcount

    def scalar(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._row


class FakeSession:
    """execute() 호출 순서대로 준비된 FakeResult를 돌려준다."""

    def __init__(self, results):
        self._results = list(results)
        self.committed = False

    async def execute(self, stmt):
        return self._results.pop(0)

    async def commit(self):
        self.committed = True


async def test_insert_wins_returns_claimed():
    # 1) INSERT ... RETURNING cache_id → 42 (선점 성공)
    fake = FakeSession([FakeResult(scalar=42)])
    out = await claim_or_get(fake, "vid1", 7, "m")
    assert out == ClaimOutcome(kind="claimed", cache_id=42, analysis=None)


async def test_conflict_completed_returns_hit():
    # 1) INSERT → None(충돌), 2) SELECT → completed 행
    row = FakeRow(cache_id=9, status="completed", analysis={"one_line": "요약"})
    fake = FakeSession([FakeResult(scalar=None), FakeResult(row=row)])
    out = await claim_or_get(fake, "vid1", 7, "m")
    assert out.kind == "hit" and out.cache_id == 9 and out.analysis == {"one_line": "요약"}


async def test_conflict_fresh_pending_returns_in_progress():
    row = FakeRow(status="pending", created_at=datetime.now(timezone.utc))
    fake = FakeSession([FakeResult(scalar=None), FakeResult(row=row)])
    out = await claim_or_get(fake, "vid1", 7, "m")
    assert out.kind == "in_progress"


async def test_conflict_stale_pending_reclaims():
    stale = datetime.now(timezone.utc) - timedelta(minutes=CACHE_STALE_PENDING_MINUTES + 5)
    row = FakeRow(cache_id=3, status="pending", created_at=stale)
    # 3) UPDATE(재클레임) → rowcount 1
    fake = FakeSession(
        [FakeResult(scalar=None), FakeResult(row=row), FakeResult(rowcount=1)]
    )
    out = await claim_or_get(fake, "vid1", 7, "m")
    assert out == ClaimOutcome(kind="claimed", cache_id=3, analysis=None)


async def test_conflict_failed_reclaims():
    row = FakeRow(cache_id=5, status="failed")
    fake = FakeSession(
        [FakeResult(scalar=None), FakeResult(row=row), FakeResult(rowcount=1)]
    )
    out = await claim_or_get(fake, "vid1", 7, "m")
    assert out == ClaimOutcome(kind="claimed", cache_id=5, analysis=None)


async def test_reclaim_lost_race_returns_in_progress():
    row = FakeRow(cache_id=5, status="failed")
    fake = FakeSession(
        [FakeResult(scalar=None), FakeResult(row=row), FakeResult(rowcount=0)]
    )
    out = await claim_or_get(fake, "vid1", 7, "m")
    assert out.kind == "in_progress"
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_analysis_cache_service.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: 구현** — `app/services/analysis_cache_service.py`:

```python
"""공유 분석 캐시 서비스 (스펙 §2.9).

선점 프로토콜: analysis_cache의 UNIQUE(video_id, preset_id, model)를 락으로 사용.
- INSERT ON CONFLICT DO NOTHING RETURNING이 성공하면 이 워커가 분석 수행권을 가진다.
- 충돌 시 기존 행 상태에 따라: completed=적중, pending(신선)=다른 워커 진행 중,
  pending(오래됨: 워커 사망 추정)/failed=조건부 UPDATE로 재클레임(rowcount로 판정).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.control_db import get_sessionmaker
from app.models.control.analysis_cache import AnalysisCache
from app.models.control.analysis_delivery import AnalysisDelivery

# pending이 이 시간을 넘기면 분석 워커가 죽은 것으로 보고 재클레임을 허용한다(스펙 §8).
CACHE_STALE_PENDING_MINUTES = 30


@dataclass(frozen=True)
class ClaimOutcome:
    kind: str  # 'hit' | 'claimed' | 'in_progress'
    cache_id: Optional[int] = None
    analysis: Optional[Dict[str, Any]] = None


async def claim_or_get(
    session: AsyncSession, video_id: str, preset_id: int, model: str
) -> ClaimOutcome:
    # 1) 선점 시도
    ins = (
        pg_insert(AnalysisCache)
        .values(video_id=video_id, preset_id=preset_id, model=model, status="pending")
        .on_conflict_do_nothing(constraint="uq_analysis_cache_key")
        .returning(AnalysisCache.cache_id)
    )
    cache_id = (await session.execute(ins)).scalar()
    if cache_id is not None:
        return ClaimOutcome(kind="claimed", cache_id=cache_id)

    # 2) 기존 행 조회
    row = (
        await session.execute(
            select(AnalysisCache).where(
                AnalysisCache.video_id == video_id,
                AnalysisCache.preset_id == preset_id,
                AnalysisCache.model == model,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        # 삽입 충돌 직후 삭제된 극단 케이스 — 다음 틱에 재시도
        return ClaimOutcome(kind="in_progress")
    if row.status == "completed":
        return ClaimOutcome(kind="hit", cache_id=row.cache_id, analysis=row.analysis)

    # 3) pending(오래됨) 또는 failed → 조건부 재클레임
    reclaimable = row.status == "failed"
    if row.status == "pending":
        created = row.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=CACHE_STALE_PENDING_MINUTES)
        reclaimable = created < cutoff
    if not reclaimable:
        return ClaimOutcome(kind="in_progress")

    result = await session.execute(
        update(AnalysisCache)
        .where(
            AnalysisCache.cache_id == row.cache_id,
            AnalysisCache.status == row.status,  # 상태가 그대로일 때만(동시 재클레임 방지)
        )
        .values(status="pending", created_at=datetime.now(timezone.utc), completed_at=None)
    )
    if int(result.rowcount or 0) == 1:
        return ClaimOutcome(kind="claimed", cache_id=row.cache_id)
    return ClaimOutcome(kind="in_progress")


async def mark_completed(
    session: AsyncSession,
    cache_id: int,
    analysis: Dict[str, Any],
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
) -> None:
    await session.execute(
        update(AnalysisCache)
        .where(AnalysisCache.cache_id == cache_id)
        .values(
            status="completed",
            analysis=analysis,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            completed_at=datetime.now(timezone.utc),
        )
    )


async def mark_failed(session: AsyncSession, cache_id: int) -> None:
    await session.execute(
        update(AnalysisCache)
        .where(AnalysisCache.cache_id == cache_id, AnalysisCache.status == "pending")
        .values(status="failed")
    )


async def record_delivery(
    session: AsyncSession, user_id: int, group_id: int, cache_id: int
) -> None:
    session.add(AnalysisDelivery(user_id=user_id, group_id=group_id, cache_id=cache_id))


# ── 제어 평면 세션을 여는 편의 래퍼 (monitor_service가 사용) ──────────────────


async def claim_or_get_cached(video_id: str, preset_id: int, model: str) -> ClaimOutcome:
    async with get_sessionmaker()() as session:
        outcome = await claim_or_get(session, video_id, preset_id, model)
        await session.commit()
        return outcome


async def complete_cached(cache_id: int, analysis: Dict[str, Any]) -> None:
    async with get_sessionmaker()() as session:
        await mark_completed(session, cache_id, analysis)
        await session.commit()


async def fail_cached(cache_id: int) -> None:
    async with get_sessionmaker()() as session:
        await mark_failed(session, cache_id)
        await session.commit()


async def record_delivery_for(user_id: int, group_id: int, cache_id: int) -> None:
    async with get_sessionmaker()() as session:
        await record_delivery(session, user_id, group_id, cache_id)
        await session.commit()
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_analysis_cache_service.py -v && pytest -q`
Expected: 전부 PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/analysis_cache_service.py tests/test_analysis_cache_service.py
git commit -m "feat: 공유 분석 캐시 서비스 (UNIQUE 선점/완료/실패/전달 원장)"
```

---

### Task 7: monitor_service 캐시 통합

**Files:**
- Modify: `app/services/monitor_service.py` (`_run_analysis`)
- Test: `tests/test_cache_integration.py` (추가)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_cache_integration.py`에 추가:

```python
def test_should_use_cache_matrix():
    from app.services.monitor_service import _should_use_cache

    assert _should_use_cache(preset_id=7, custom_prompt=None) is True
    assert _should_use_cache(preset_id=None, custom_prompt=None) is False   # 직접 프롬프트
    assert _should_use_cache(preset_id=7, custom_prompt="커스텀") is False  # 즉시 분석 오버라이드
    assert _should_use_cache(preset_id=None, custom_prompt="커스텀") is False
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_cache_integration.py -v`
Expected: 신규 1건 FAIL (ImportError)

- [ ] **Step 3: 구현** — `app/services/monitor_service.py`:

(a) import 추가:

```python
from app.services.analysis_cache_service import (
    claim_or_get_cached,
    complete_cached,
    fail_cached,
    record_delivery_for,
)
from app.services.analyzer import build_analysis_pipeline, result_from_cache, save_analysis_to_group
from app.services.preset_service import ResolvedPrompts, resolve_prompts
```

(기존 `from app.services.analyzer import build_analysis_pipeline` 줄은 위 줄로 대체.)

(b) 순수 결정 헬퍼 추가 (`_run_analysis` 위):

```python
def _should_use_cache(preset_id: Optional[int], custom_prompt: Optional[str]) -> bool:
    """프리셋 그룹만 공유 캐시에 참여. 직접 프롬프트/커스텀 오버라이드는 기존 경로."""
    return preset_id is not None and not custom_prompt
```

(c) `_run_analysis` 전체를 다음으로 교체:

```python
async def _run_analysis(
    group: Group,
    make_session: MakeSession,
    video_pk: int,
    *,
    custom_prompt: Optional[str] = None,
    label: str = "분석",
) -> None:
    """단일 영상 분석 실행 + 성공/실패 로깅 + 커밋 후 알림.

    프리셋 그룹은 공유 분석 캐시(§2.9)를 경유한다: 적중 시 LLM 호출 없이 복사,
    미스 시 선점 후 1회 분석 + 캐시 기록. 직접 프롬프트/커스텀 오버라이드는
    기존 경로 그대로.
    """
    resolved = await resolve_prompts(group.group_id)

    # 영상 메타 조회 (양 경로 공용)
    async with make_session() as sess:
        video = (
            await sess.execute(select(Video).where(Video.video_pk == video_pk))
        ).scalar_one_or_none()
        if not video:
            return
        channel = (
            await sess.execute(
                select(Channel).where(Channel.channel_pk == video.channel_pk)
            )
        ).scalar_one_or_none()
    title, channel_pk = video.title, video.channel_pk
    channel_name = channel.channel_name if channel else ""

    if _should_use_cache(resolved.preset_id, custom_prompt):
        await _run_analysis_cached(
            group, make_session, video, channel_name, resolved,
            channel_pk=channel_pk, label=label,
        )
        return

    # ── 기존 경로 (직접 프롬프트 / 커스텀 오버라이드) ──────────────────────────
    pipeline = await build_analysis_pipeline(
        group.group_id, analysis_prompt_override=custom_prompt
    )
    timer = JobTimer()
    try:
        with timer:
            async with make_session() as sess:
                async with sess.begin():
                    await pipeline.run_and_save(
                        session=sess,
                        video_pk=video_pk,
                        video_url=video.video_url,
                        channel_name=channel_name,
                        published_at_str=video.published_at.isoformat(),
                        duration_seconds=video.duration_seconds,
                    )
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_VIDEO_ANALYZE,
            status=STATUS_SUCCESS,
            message=f"{label} 완료 - {title}" if title else f"{label} 완료",
            duration_ms=timer.elapsed_ms,
            channel_pk=channel_pk,
            video_pk=video_pk,
        )
        await _notify_after_analysis(group, make_session, video_pk, channel_pk)
    except Exception as e:
        print(f"[{group.slug}] {label} 실패 (video_pk={video_pk}): {e}")
        await _mark_video_failed(group, make_session, video_pk, e, label)
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_VIDEO_ANALYZE,
            status=STATUS_FAIL,
            message=str(e),
            duration_ms=timer.elapsed_ms,
            channel_pk=channel_pk,
            video_pk=video_pk,
        )
    finally:
        await pipeline.aclose()


async def _mark_video_failed(
    group: Group, make_session: MakeSession, video_pk: int, e: Exception, label: str
) -> None:
    """분석 실패 상태 기록 (기존 _run_analysis except 블록에서 추출)."""
    try:
        async with make_session() as fs:
            async with fs.begin():
                await fs.execute(
                    update(Video)
                    .where(Video.video_pk == video_pk)
                    .values(
                        analysis_status="failed",
                        analysis_error=str(e)[:500],
                        retry_count=Video.retry_count + 1,
                    )
                )
    except Exception as upd:
        print(f"[{group.slug}] {label} 실패 상태 기록 오류 (video_pk={video_pk}): {upd}")


async def _run_analysis_cached(
    group: Group,
    make_session: MakeSession,
    video: Video,
    channel_name: str,
    resolved: ResolvedPrompts,
    *,
    channel_pk: Optional[int],
    label: str,
) -> None:
    """공유 캐시 경유 분석. 적중=복사, 선점=1회 분석+캐시 기록, 진행중=다음 틱 연기."""
    mgr = get_settings_manager()
    ai = await mgr.get_ai_gateway(group.group_id)
    video_pk = video.video_pk
    assert resolved.preset_id is not None

    outcome = await claim_or_get_cached(video.video_id, resolved.preset_id, ai.primary_model)

    if outcome.kind == "in_progress":
        # 다른 워커가 분석 중 — 영상을 pending으로 되돌려 다음 틱에 캐시 적중을 노린다.
        async with make_session() as sess:
            async with sess.begin():
                await sess.execute(
                    update(Video)
                    .where(Video.video_pk == video_pk)
                    .values(analysis_status="pending")
                )
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_VIDEO_ANALYZE,
            status=STATUS_SKIP,
            message="공유 캐시 분석 진행 중 — 다음 틱 재시도",
            channel_pk=channel_pk,
            video_pk=video_pk,
        )
        return

    timer = JobTimer()
    if outcome.kind == "hit":
        try:
            with timer:
                result = result_from_cache(
                    outcome.analysis or {}, model_name=ai.primary_model, gateway_url=ai.base_url
                )
                async with make_session() as sess:
                    async with sess.begin():
                        await save_analysis_to_group(sess, video_pk, result)
            await _record_delivery_safe(group, outcome.cache_id)
            await write_job_log(
                make_session,
                job_type=JOB_TYPE_VIDEO_ANALYZE,
                status=STATUS_SUCCESS,
                message=f"{label} 완료(캐시 적중) - {video.title}",
                duration_ms=timer.elapsed_ms,
                channel_pk=channel_pk,
                video_pk=video_pk,
            )
            await _notify_after_analysis(group, make_session, video_pk, channel_pk)
        except Exception as e:
            print(f"[{group.slug}] 캐시 복사 실패 (video_pk={video_pk}): {e}")
            await _mark_video_failed(group, make_session, video_pk, e, label)
            await write_job_log(
                make_session,
                job_type=JOB_TYPE_VIDEO_ANALYZE,
                status=STATUS_FAIL,
                message=f"캐시 복사 실패: {e}",
                duration_ms=timer.elapsed_ms,
                channel_pk=channel_pk,
                video_pk=video_pk,
            )
        return

    # outcome.kind == "claimed" — 이 워커가 분석 수행권을 가진다.
    pipeline = await build_analysis_pipeline(group.group_id)
    try:
        with timer:
            async with make_session() as sess:
                async with sess.begin():
                    result = await pipeline.run_and_save(
                        session=sess,
                        video_pk=video_pk,
                        video_url=video.video_url,
                        channel_name=channel_name,
                        published_at_str=video.published_at.isoformat(),
                        duration_seconds=video.duration_seconds,
                    )
        await complete_cached(outcome.cache_id, result.data)
        await _record_delivery_safe(group, outcome.cache_id)
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_VIDEO_ANALYZE,
            status=STATUS_SUCCESS,
            message=f"{label} 완료(캐시 신규) - {video.title}",
            duration_ms=timer.elapsed_ms,
            channel_pk=channel_pk,
            video_pk=video_pk,
        )
        await _notify_after_analysis(group, make_session, video_pk, channel_pk)
    except Exception as e:
        print(f"[{group.slug}] {label} 실패 (video_pk={video_pk}): {e}")
        try:
            await fail_cached(outcome.cache_id)
        except Exception as ce:
            print(f"[{group.slug}] 캐시 실패 기록 오류 (cache_id={outcome.cache_id}): {ce}")
        await _mark_video_failed(group, make_session, video_pk, e, label)
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_VIDEO_ANALYZE,
            status=STATUS_FAIL,
            message=str(e),
            duration_ms=timer.elapsed_ms,
            channel_pk=channel_pk,
            video_pk=video_pk,
        )
    finally:
        await pipeline.aclose()


async def _record_delivery_safe(group: Group, cache_id: Optional[int]) -> None:
    """전달 원장 기록. 실패해도 분석 흐름을 깨지 않는다(원장은 쿼터/과금용 부가 데이터)."""
    if cache_id is None or group.owner_user_id is None:
        return
    try:
        await record_delivery_for(group.owner_user_id, group.group_id, cache_id)
    except Exception as e:
        print(f"[{group.slug}] 전달 원장 기록 실패 (cache_id={cache_id}): {e}")
```

(주의: `run_and_save`는 `AnalysisPipelineResult`를 반환한다 — `result.data`를
`complete_cached`에 넘긴다. 기존 `_run_analysis`의 영상 조회가 트랜잭션 안에 있었지만
새 코드는 조회를 트랜잭션 밖으로 뺐다 — 조회는 읽기 전용이라 동작 차이 없음.)

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_cache_integration.py -v && pytest -q`
Expected: 전부 PASS (기존 회귀 없음 — 특히 test_plan4_endpoints, test_reset_failed)

- [ ] **Step 5: Commit**

```bash
git add app/services/monitor_service.py tests/test_cache_integration.py
git commit -m "feat: 분석 경로에 공유 캐시 통합 (적중=복사, 선점=1회 분석, 진행중=연기)"
```

---

### Task 8: 통합 검증 + 실 DB E2E 가이드

**Files:**
- Modify: `README.md` (프리셋·캐시 한 줄 안내)

- [ ] **Step 1: 전체 테스트**

Run: `pytest -q` — 전부 PASS 확인 (예상: 기존 172 + 신규 ~13 = 185 내외).
Run: `python -c "from app.main import app; print('import ok')"` — 순환 import 없음 확인.
Run: `cd frontend && npm run build` — 프론트 무변경이지만 회귀 확인.

- [ ] **Step 2: README 안내 추가** — `## 계정` 섹션 아래:

```markdown
## 프리셋과 공유 분석 캐시

- 관리자는 `/api/admin/presets`로 분석 프롬프트 프리셋을 만든다. 프리셋 본문은 불변 —
  수정하려면 새 프리셋을 만들고 구버전을 비활성화한다.
- 그룹 설정 `prompts` 카테고리에 `preset_id`(int)를 저장하면 그 그룹은 프리셋을 사용하며,
  같은 영상×프리셋×모델 분석은 시스템 전체에서 1회만 수행된다(공유 캐시).
- `preset_id`가 없는 그룹(기존 admin 그룹의 직접 프롬프트)은 기존 경로로 동작한다.
```

- [ ] **Step 3: 실 DB E2E (환경 가용 시 — 없으면 skip하고 보고에 명시)**

`.env`가 구성된 환경에서:

1. 프리셋 생성: `POST /api/admin/presets` (admin 로그인 상태)
   `{"name": "기본 분석", "analysis_prompt": "<analyzer.DEFAULT_ANALYSIS_PROMPT 내용 복사>"}`
2. 테스트 그룹 2개를 만들고 **같은 채널**을 추가, 두 그룹 모두
   `PUT /api/groups/{slug}/settings/prompts`에 `{"items": [{"key": "preset_id", "value": "<id>", "value_type": "int"}]}` 저장.
3. 두 그룹에 같은 영상을 즉시 분석 등록(`POST .../videos/instant`, custom_prompt 없이) 또는
   폴링으로 같은 신규 영상 수집 후 분석 틱 2회 대기.
4. 검증 SQL:
   ```sql
   SELECT status, count(*) FROM app.analysis_cache GROUP BY status;   -- completed 1행
   SELECT count(*) FROM app.analysis_deliveries;                       -- 2행
   ```
   두 그룹 UI 모두에서 같은 분석 내용 확인. AI 게이트웨이 로그에서 분석 호출이 1회인지 확인.
5. 직접 프롬프트 그룹(기존 invest 등)의 분석이 캐시를 우회해 기존대로 동작하는지 1건 확인.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: 프리셋·공유 분석 캐시 사용 안내"
```

---

## 셀프 리뷰 체크 결과 (계획 작성 시 수행)

- **스펙 커버리지**: §2.6 프리셋 테이블→T1, 프리셋 관리→T3, preset_id 해석→T4, §2.9 캐시 테이블→T2, 선점 프로토콜→T6, 분석 경로 통합/직접 프롬프트 우회→T7, deliveries 기록→T6/T7, §8 프리셋 불변성→T3(PresetPatch에 본문 필드 없음), §8 pending 타임아웃 재클레임→T6. **범위 외(명시)**: channel_registry 중앙 폴링(B-0b), ai_usage 시스템 몫 기록(Phase C), 토큰 수 배선(Phase C), 프리셋 선택 UI(Phase C).
- **타입 일관성**: `ClaimOutcome(kind, cache_id, analysis)` T6 정의 ↔ T7 사용 일치. `ResolvedPrompts(analysis_prompt, digest_prompt, preset_id)` T4 정의 ↔ T7 사용 일치. `result_from_cache(data, model_name, gateway_url)` T5 정의 ↔ T7 호출 일치. `save_analysis_to_group(session, video_pk, result, notify_callback)` T5 ↔ T7 일치.
- **주의점**: T3의 admin 라우터가 T4의 `invalidate_preset_cache`를 import하는 순서 의존 — T3 시점에는 생략하고 T4 Step 6에서 배선하도록 명시함. `digest_service`의 `mgr` 변수가 get_prompts 외 다른 용도로도 쓰이는지 실행 시 확인 필요(쓰이면 mgr 줄 유지하고 prompts 줄만 교체).
