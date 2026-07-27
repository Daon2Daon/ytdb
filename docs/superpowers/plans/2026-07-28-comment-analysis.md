# 댓글 반응 분석 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** YouTube 영상의 댓글을 AI로 긍정/부정/중립 분류하고 카테고리별 요약·인사이트·대표 댓글을 보여주는 기능을 추가한다. 관리자는 무제한, 일반 사용자는 월 5회 가중 크레딧.

**Architecture:** 기존 구조에 얹는다. 쿼터는 `quota_service.EffectiveLimits`를 확장하여 admin이 `None`을 받는 기존 동작으로 무제한을 자동 획득한다. 댓글 수집은 기존 `YouTubeAPIClient._get(quota_units=1)` 경로를 타서 YouTube 원장에 자동 기록되고, LLM 호출은 기존 `LiteLLMClient.chat` + `ai_usage_service.record_usage`를 재사용한다. 결과는 그룹 스키마의 신규 테이블 1개에, 사용자별 월 집계용 원장은 제어 평면 신규 테이블 1개에 저장한다.

**Tech Stack:** Python 3.10+ / FastAPI / SQLAlchemy 2.x async / PostgreSQL / pytest (asyncio_mode=auto) / React 18 + TypeScript / Vite / vitest

**설계 문서:** `docs/superpowers/specs/2026-07-28-comment-analysis-design.md`

---

## 파일 구조

**신규 생성**

| 파일 | 책임 |
|---|---|
| `app/models/control/comment_analysis_run.py` | 제어 평면 크레딧 원장 모델 |
| `app/models/pg/comment_analysis.py` | 그룹 스키마 분석 결과 모델 |
| `app/services/comment_analysis_service.py` | 수집 → 분류 → 인사이트 파이프라인 |
| `app/services/comment_prompts.py` | 기본 프롬프트 상수 2개 (분류/인사이트) |
| `app/schemas/comment_analysis.py` | Pydantic 입출력 스키마 |
| `app/routers/comment_analysis.py` | 그룹 스코프 API |
| `frontend/src/api/comments.ts` | API 클라이언트 |
| `frontend/src/components/CommentAnalysis.logic.ts` | 크레딧·신선도 순수 함수 |
| `frontend/src/components/CommentAnalysisCard.tsx` | VideoDetail 하단 카드 |
| `frontend/src/pages/VideoComments.tsx` | 결과 상세 페이지 |
| `frontend/src/pages/CommentAnalysis.tsx` | URL 입력 진입 페이지 |

**수정**

| 파일 | 변경 |
|---|---|
| `app/services/quota_service.py` | `EffectiveLimits` 필드 2개, `credits_for`, `quota_verdict`, `count_monthly_credits`, `check_comment_analysis_quota` |
| `app/models/control/plan.py`, `user_limit.py` | 컬럼 2개씩 |
| `app/control_db.py` | `ensure_control_schema()`에 ALTER 2건 |
| `app/services/auth_service.py` | `PLAN_SEEDS`에 키 2개 |
| `app/services/youtube_api.py` | `CommentMeta`, `list_comment_threads`, `VideoMeta.comment_count`, `CommentsDisabledError` |
| `app/services/db_engine.py` | `additive_columns`에 `videos.comment_count` |
| `app/models/pg/video.py` | `comment_count` 컬럼 |
| `app/services/settings_types.py` | `PromptSettings.comment_analysis_prompt` |
| `app/routers/videos.py` | 영상 상세 응답에 `comment_count` 노출 |
| `app/routers/auth.py` | `me_router`에 `/comment-credits` |
| `app/main.py` | 라우터 등록 |
| `frontend/src/App.tsx` | 라우트 2개 |
| `frontend/src/components/Layout.tsx` | NAV 1개 |
| `frontend/src/pages/VideoDetail.tsx` | 카드 삽입 |
| `frontend/src/api/types.ts` | 타입 추가 |

---

## Task 1: 크레딧 계산과 한도 판정 (순수 함수)

DB 없이 검증 가능한 로직부터 만든다. 이후 모든 태스크가 이 시그니처에 의존한다.

**Files:**
- Modify: `app/services/quota_service.py`
- Test: `tests/test_comment_quota.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_comment_quota.py` 생성:

```python
"""댓글 분석 쿼터 순수 함수 테스트 (DB 불필요)."""

from app.services.quota_service import EffectiveLimits, credits_for, quota_verdict


def _limits(*, per_month: int = 5, per_analysis: int = 1000) -> EffectiveLimits:
    return EffectiveLimits(
        max_groups=1,
        max_channels_total=10,
        max_analyses_per_day=10,
        max_video_minutes=60,
        min_poll_interval_min=10,
        plan_slug="free",
        plan_name="Free",
        has_override=False,
        monthly_cost_budget_usd=None,
        max_comment_analyses_per_month=per_month,
        max_comments_per_analysis=per_analysis,
    )


def test_credits_for_rounds_up_with_floor_of_one():
    assert credits_for(0) == 1
    assert credits_for(1) == 1
    assert credits_for(999) == 1
    assert credits_for(1000) == 1
    assert credits_for(1001) == 2
    assert credits_for(2000) == 2
    assert credits_for(5000) == 5


def test_admin_none_limits_always_pass():
    assert quota_verdict(None, used_credits=999, requested_limit=100000) is None


def test_requested_limit_over_plan_cap_is_rejected():
    msg = quota_verdict(_limits(), used_credits=0, requested_limit=2000)
    assert msg is not None
    assert "1,000건" in msg


def test_exact_cap_is_allowed():
    assert quota_verdict(_limits(), used_credits=0, requested_limit=1000) is None


def test_credits_exhausted_is_rejected():
    msg = quota_verdict(_limits(), used_credits=5, requested_limit=1000)
    assert msg is not None
    assert "5회" in msg


def test_partial_remaining_cannot_cover_weighted_cost():
    # 잔여 1회인데 2,000건(2회)을 요청 → 거절
    lim = _limits(per_month=5, per_analysis=5000)
    msg = quota_verdict(lim, used_credits=4, requested_limit=2000)
    assert msg is not None


def test_remaining_exactly_covers_weighted_cost():
    lim = _limits(per_month=5, per_analysis=5000)
    assert quota_verdict(lim, used_credits=3, requested_limit=2000) is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_comment_quota.py -v`
Expected: FAIL — `ImportError: cannot import name 'credits_for'`

- [ ] **Step 3: 구현**

`app/services/quota_service.py`의 `EffectiveLimits`에 필드 2개를 추가한다. 기본값이 있는 `monthly_cost_budget_usd` 뒤에 와야 하므로 둘 다 기본값을 준다:

```python
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
    monthly_cost_budget_usd: Optional[float] = None  # None = 예산 무제한
    max_comment_analyses_per_month: int = 0
    max_comments_per_analysis: int = 0
```

`_merge_limits()` 반환문에 두 줄을 추가한다:

```python
    return EffectiveLimits(
        max_groups=pick("max_groups"),
        max_channels_total=pick("max_channels_total"),
        max_analyses_per_day=pick("max_analyses_per_day"),
        max_video_minutes=pick("max_video_minutes"),
        min_poll_interval_min=pick("min_poll_interval_min"),
        plan_slug=plan.slug,
        plan_name=plan.name,
        has_override=override is not None,
        monthly_cost_budget_usd=pick_budget(),
        max_comment_analyses_per_month=pick("max_comment_analyses_per_month"),
        max_comments_per_analysis=pick("max_comments_per_analysis"),
    )
```

파일 끝에 순수 함수 2개를 추가한다:

```python
# ── 댓글 분석 가중 크레딧 ────────────────────────────────────────────────────

CREDIT_UNIT_COMMENTS = 1000


def credits_for(requested_limit: int) -> int:
    """가중 크레딧: 1회 = 댓글 1,000건. 하한 1회.

    500건도 1회, 2,000건은 2회. 수량에 비례해 차감하여 실제 비용과 정합을 맞춘다.
    """
    import math

    return max(1, math.ceil(max(0, requested_limit) / CREDIT_UNIT_COMMENTS))


def quota_verdict(
    limits: Optional[EffectiveLimits], used_credits: int, requested_limit: int
) -> Optional[str]:
    """통과면 None, 거절이면 사용자에게 보여줄 사유 문자열.

    async 검사 함수(check_comment_analysis_quota)와 분리한 순수 판정 로직 —
    DB 없이 경계 조건을 테스트할 수 있다.
    """
    if limits is None:
        return None
    if requested_limit > limits.max_comments_per_analysis:
        return (
            f"플랜 상한({limits.max_comments_per_analysis:,}건)을 초과합니다: "
            f"요청 {requested_limit:,}건"
        )
    need = credits_for(requested_limit)
    cap = limits.max_comment_analyses_per_month
    if used_credits + need > cap:
        remaining = max(0, cap - used_credits)
        return (
            f"이번 달 댓글 분석 한도를 초과합니다: 이번 요청 {need}회 / "
            f"잔여 {remaining}회 (월 {cap}회, KST 월초 초기화)"
        )
    return None
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_comment_quota.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: 기존 테스트 회귀 확인**

`EffectiveLimits` 생성자를 쓰는 기존 테스트가 깨지지 않아야 한다(새 필드에 기본값을 줬으므로 통과해야 함).

Run: `python -m pytest tests/ -q`
Expected: 기존과 동일한 통과 수 + 7

- [ ] **Step 6: 커밋**

```bash
git add app/services/quota_service.py tests/test_comment_quota.py
git commit -m "feat: 댓글 분석 가중 크레딧 계산·한도 판정 순수 함수"
```

---

## Task 2: 제어 평면 스키마 (원장 테이블 + 플랜 컬럼)

**Files:**
- Create: `app/models/control/comment_analysis_run.py`
- Modify: `app/models/control/plan.py`, `app/models/control/user_limit.py`, `app/control_db.py`, `app/services/auth_service.py`
- Test: `tests/test_comment_analysis_models.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_comment_analysis_models.py` 생성:

```python
"""댓글 분석 제어 평면 모델 구조 테스트 (DB 연결 불필요 — 메타데이터만 검사)."""

from app.control_db import APP_SCHEMA
from app.models.control.comment_analysis_run import CommentAnalysisRun
from app.models.control.plan import Plan
from app.models.control.user_limit import UserLimit


def test_run_table_name_and_schema():
    assert CommentAnalysisRun.__tablename__ == "comment_analysis_runs"
    assert CommentAnalysisRun.__table__.schema == APP_SCHEMA


def test_run_has_required_columns():
    cols = set(CommentAnalysisRun.__table__.columns.keys())
    assert {
        "run_id", "user_id", "group_id", "video_id",
        "credits", "requested_limit", "comment_count", "created_at",
    } <= cols


def test_group_id_has_no_foreign_key():
    # 그룹 삭제 후에도 원장이 보존되도록 FK를 걸지 않는다.
    assert len(CommentAnalysisRun.__table__.c.group_id.foreign_keys) == 0


def test_user_created_index_exists():
    names = {ix.name for ix in CommentAnalysisRun.__table__.indexes}
    assert "comment_analysis_runs_user_created" in names


def test_plan_has_comment_quota_columns():
    cols = Plan.__table__.columns
    assert cols["max_comment_analyses_per_month"].nullable is False
    assert cols["max_comments_per_analysis"].nullable is False


def test_user_limit_comment_columns_are_nullable():
    # NULL = 플랜 값 사용 (COALESCE는 quota_service 담당)
    cols = UserLimit.__table__.columns
    assert cols["max_comment_analyses_per_month"].nullable is True
    assert cols["max_comments_per_analysis"].nullable is True
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_comment_analysis_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.control.comment_analysis_run'`

- [ ] **Step 3: 원장 모델 생성**

`app/models/control/comment_analysis_run.py`:

```python
"""app.comment_analysis_runs — 사용자별 댓글 분석 크레딧 원장.

그룹 스키마는 서로 격리되어 있어 사용자별 월 집계를 데이터 평면에서 할 수 없다.
analysis_deliveries와 같은 이유로 제어 평면에 둔다.

analysis_deliveries와 달리 UNIQUE 제약이 없다 — 재분석은 실제 비용이 다시
발생하는 새 사건이므로 행이 쌓이는 것이 맞다.
group_id에 FK를 두지 않아 그룹 삭제 후에도 원장이 보존된다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class CommentAnalysisRun(Base):
    __tablename__ = "comment_analysis_runs"
    __table_args__ = (
        Index("comment_analysis_runs_user_created", "user_id", "created_at"),
        {"schema": APP_SCHEMA},
    )

    run_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{APP_SCHEMA}.users.user_id"), nullable=False
    )
    group_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    video_id: Mapped[str] = mapped_column(Text, nullable=False)
    credits: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    comment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

- [ ] **Step 4: 플랜/한도 컬럼 추가**

`app/models/control/plan.py`의 `min_poll_interval_min` 아래에 추가:

```python
    max_comment_analyses_per_month: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="5"
    )
    max_comments_per_analysis: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1000"
    )
```

`app/models/control/user_limit.py`의 `min_poll_interval_min` 아래에 추가:

```python
    max_comment_analyses_per_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_comments_per_analysis: Mapped[int | None] = mapped_column(Integer, nullable=True)
```

- [ ] **Step 5: 모델을 메타데이터에 등록**

`app/control_db.py`의 `ensure_control_schema()`가 `Base.metadata.create_all`을 호출하려면 모델이 임포트되어 있어야 한다. `ensure_control_schema()` 안에서 다른 모델을 임포트하는 기존 위치를 찾아 같은 방식으로 추가한다:

```python
    from app.models.control.comment_analysis_run import CommentAnalysisRun  # noqa: F401
```

`ensure_control_schema()` 안에 이미 모델 임포트 블록이 없다면, `app/models/control/__init__.py`에 다음 줄을 추가한다:

```python
from app.models.control.comment_analysis_run import CommentAnalysisRun  # noqa: F401
```

- [ ] **Step 6: 기존 설치용 ALTER 추가**

`create_all`은 기존 테이블에 컬럼을 추가하지 않는다. `app/control_db.py:85` 근처의 기존 ALTER 블록과 같은 자리에 추가한다:

```python
        await conn.execute(
            text(
                f'ALTER TABLE "{APP_SCHEMA}".plans '
                "ADD COLUMN IF NOT EXISTS max_comment_analyses_per_month "
                "INTEGER NOT NULL DEFAULT 5"
            )
        )
        await conn.execute(
            text(
                f'ALTER TABLE "{APP_SCHEMA}".plans '
                "ADD COLUMN IF NOT EXISTS max_comments_per_analysis "
                "INTEGER NOT NULL DEFAULT 1000"
            )
        )
        await conn.execute(
            text(
                f'ALTER TABLE "{APP_SCHEMA}".user_limits '
                "ADD COLUMN IF NOT EXISTS max_comment_analyses_per_month INTEGER"
            )
        )
        await conn.execute(
            text(
                f'ALTER TABLE "{APP_SCHEMA}".user_limits '
                "ADD COLUMN IF NOT EXISTS max_comments_per_analysis INTEGER"
            )
        )
```

DEFAULT가 있으므로 기존 플랜 행은 자동으로 5 / 1000을 갖는다. 별도 백필 불필요.

- [ ] **Step 7: 플랜 시드 갱신**

`app/services/auth_service.py`의 `PLAN_SEEDS`. 각 시드 dict에 키 2개를 추가한다. `free`(또는 기본 플랜)에는 `5` / `1000`, `unlimited`에는 `100000` / `100000`:

```python
    {
        "slug": "unlimited", "name": "Unlimited", "max_groups": 1000,
        "max_channels_total": 100000, "max_analyses_per_day": 100000,
        # NUMERIC(10,4) 컬럼의 최대값(10^6 미만)에 맞춘 사실상의 무제한 값.
        "max_video_minutes": 100000, "monthly_cost_budget_usd": "999999.9999",
        "min_poll_interval_min": 1, "is_default": False,
        "max_comment_analyses_per_month": 100000,
        "max_comments_per_analysis": 100000,
    },
```

기본 플랜 시드에는:

```python
        "max_comment_analyses_per_month": 5,
        "max_comments_per_analysis": 1000,
```

- [ ] **Step 8: 테스트 통과 확인**

Run: `python -m pytest tests/test_comment_analysis_models.py tests/test_comment_quota.py -v`
Expected: PASS (6 + 7 = 13 passed)

- [ ] **Step 9: 커밋**

```bash
git add app/models/control/ app/control_db.py app/services/auth_service.py tests/test_comment_analysis_models.py
git commit -m "feat: 댓글 분석 크레딧 원장 테이블 + 플랜 한도 컬럼"
```

---

## Task 3: 월 집계와 비동기 쿼터 검사

**Files:**
- Modify: `app/services/quota_service.py`
- Test: `tests/test_comment_quota.py` (추가)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_comment_quota.py` 끝에 추가:

```python
import pytest

from app.services.quota_service import QuotaExceeded, check_comment_analysis_quota


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value


class _FakeSession:
    """execute()가 미리 정한 값을 돌려주는 최소 스텁."""

    def __init__(self, used_credits: int):
        self._used = used_credits

    async def execute(self, _stmt):
        return _FakeResult(self._used)


async def test_check_passes_for_admin(monkeypatch):
    async def fake_limits(session, user_id):
        return None

    monkeypatch.setattr(
        "app.services.quota_service.effective_limits", fake_limits
    )
    # 예외가 나지 않으면 통과
    await check_comment_analysis_quota(_FakeSession(999), user_id=1, requested_limit=100000)


async def test_check_raises_when_exhausted(monkeypatch):
    async def fake_limits(session, user_id):
        return _limits()

    monkeypatch.setattr(
        "app.services.quota_service.effective_limits", fake_limits
    )
    with pytest.raises(QuotaExceeded) as ei:
        await check_comment_analysis_quota(
            _FakeSession(5), user_id=1, requested_limit=1000
        )
    assert ei.value.limit == 5
    assert ei.value.current == 5


async def test_check_passes_when_within_limit(monkeypatch):
    async def fake_limits(session, user_id):
        return _limits()

    monkeypatch.setattr(
        "app.services.quota_service.effective_limits", fake_limits
    )
    await check_comment_analysis_quota(_FakeSession(4), user_id=1, requested_limit=1000)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_comment_quota.py -v -k check_`
Expected: FAIL — `ImportError: cannot import name 'check_comment_analysis_quota'`

- [ ] **Step 3: 구현**

`app/services/quota_service.py`의 `quota_verdict` 아래에 추가:

```python
async def count_monthly_credits(session: AsyncSession, user_id: int) -> int:
    """당월(KST) 본인 귀속 크레딧 합. 원장이 비면 0."""
    from app.models.control.comment_analysis_run import CommentAnalysisRun
    from app.services.ai_usage_service import kst_month_start_utc

    since = kst_month_start_utc(datetime.now(timezone.utc))
    return int(
        (
            await session.execute(
                select(sa_func.coalesce(sa_func.sum(CommentAnalysisRun.credits), 0)).where(
                    CommentAnalysisRun.user_id == user_id,
                    CommentAnalysisRun.created_at >= since,
                )
            )
        ).scalar_one()
    )


async def check_comment_analysis_quota(
    session: AsyncSession, user_id: int, requested_limit: int
) -> None:
    """댓글 분석 쿼터 검사. admin/미존재 사용자는 통과. 초과 시 QuotaExceeded."""
    limits = await effective_limits(session, user_id)
    if limits is None:
        return
    used = await count_monthly_credits(session, user_id)
    reason = quota_verdict(limits, used, requested_limit)
    if reason is not None:
        raise QuotaExceeded(
            reason,
            limit=limits.max_comment_analyses_per_month,
            current=used,
        )
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_comment_quota.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: 커밋**

```bash
git add app/services/quota_service.py tests/test_comment_quota.py
git commit -m "feat: 댓글 분석 월 크레딧 집계·검사 함수"
```

---

## Task 4: 그룹 스키마 결과 테이블 + videos.comment_count

**Files:**
- Create: `app/models/pg/comment_analysis.py`
- Modify: `app/models/pg/video.py`, `app/services/db_engine.py`, `app/models/pg/__init__.py`
- Test: `tests/test_comment_analysis_models.py` (추가)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_comment_analysis_models.py` 끝에 추가:

```python
from app.models.pg.base import SCHEMA_TOKEN
from app.models.pg.comment_analysis import CommentAnalysis
from app.models.pg.video import Video


def test_comment_analysis_uses_schema_token():
    assert CommentAnalysis.__tablename__ == "comment_analyses"
    assert CommentAnalysis.__table__.schema == SCHEMA_TOKEN


def test_comment_analysis_video_pk_is_unique():
    # 영상당 최신 1건. 재분석은 덮어쓰기이며, 이 제약이 동시 실행 방어선을 겸한다.
    col = CommentAnalysis.__table__.c.video_pk
    assert col.unique is True
    assert len(col.foreign_keys) == 1


def test_comment_analysis_has_required_columns():
    cols = set(CommentAnalysis.__table__.columns.keys())
    assert {
        "analysis_id", "video_pk", "status", "requested_limit", "fetched_count",
        "total_count", "positive_count", "negative_count", "neutral_count",
        "result", "model", "partial", "error", "analyzed_at",
    } <= cols


def test_video_has_comment_count():
    assert "comment_count" in Video.__table__.columns
    assert Video.__table__.c.comment_count.nullable is True
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_comment_analysis_models.py -v -k comment_analysis_uses`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.pg.comment_analysis'`

- [ ] **Step 3: 모델 생성**

`app/models/pg/comment_analysis.py`:

```python
"""데이터 평면: comment_analyses (영상당 최신 댓글 반응 분석 1건).

댓글 원문은 전량 저장하지 않는다 — result JSONB 안에 카테고리별 대표 댓글
10개씩만 스냅샷한다. 재분석은 같은 행을 갱신하므로 UNIQUE(video_pk)가
동시 실행 방어선을 겸한다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean, BigInteger, DateTime, ForeignKey, Integer, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.pg.base import SCHEMA_TOKEN, PgBase

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"


class CommentAnalysis(PgBase):
    __tablename__ = "comment_analyses"
    __table_args__ = ({"schema": SCHEMA_TOKEN},)

    analysis_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    video_pk: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{SCHEMA_TOKEN}.videos.video_pk", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default=STATUS_PENDING)
    requested_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    fetched_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    positive_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    negative_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    neutral_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    partial: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    analyzed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 4: videos.comment_count 컬럼 추가**

`app/models/pg/video.py`의 `like_count` 아래에 추가:

```python
    comment_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
```

- [ ] **Step 5: 모델 등록과 기존 스키마 자가치유**

`app/models/pg/__init__.py`에 다음 줄을 추가한다(`PgBase.metadata`에 테이블이 등록되어야 `ensure_schema`의 `_create_missing`이 생성한다):

```python
from app.models.pg.comment_analysis import CommentAnalysis  # noqa: F401
```

`app/services/db_engine.py:181`의 `additive_columns` 리스트에 한 줄을 추가한다(기존 그룹의 `videos` 테이블에 컬럼을 멱등 패치):

```python
                    ("videos", "comment_count", "bigint"),
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `python -m pytest tests/test_comment_analysis_models.py -v`
Expected: PASS (10 passed)

- [ ] **Step 7: 커밋**

```bash
git add app/models/pg/ app/services/db_engine.py tests/test_comment_analysis_models.py
git commit -m "feat: comment_analyses 테이블 + videos.comment_count"
```

---

## Task 5: 댓글 수집 (YouTube API)

**Files:**
- Modify: `app/services/youtube_api.py`
- Test: `tests/test_comment_fetch.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_comment_fetch.py` 생성:

```python
"""댓글 수집 테스트 — httpx.MockTransport로 YouTube API를 대체한다."""

import httpx
import pytest

from app.services.settings_types import PollingSettings
from app.services.youtube_api import (
    CommentsDisabledError,
    YouTubeAPIClient,
    YouTubeAPIError,
)


def _polling() -> PollingSettings:
    return PollingSettings(youtube_api_key="test-key", youtube_daily_quota=10000)


def _thread_item(idx: int) -> dict:
    return {
        "id": f"c{idx}",
        "snippet": {
            "topLevelComment": {
                "snippet": {
                    "authorDisplayName": f"user{idx}",
                    "textDisplay": f"comment {idx}",
                    "likeCount": idx,
                    "publishedAt": "2026-07-20T10:00:00Z",
                }
            }
        },
    }


def _client_with(pages: list[dict]) -> YouTubeAPIClient:
    """pages를 순서대로 돌려주는 mock transport 클라이언트."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        page = pages[calls["n"]]
        calls["n"] += 1
        return httpx.Response(200, json=page)

    transport = httpx.MockTransport(handler)
    return YouTubeAPIClient(_polling(), client=httpx.AsyncClient(transport=transport))


async def test_stops_exactly_at_limit_across_pages():
    # 100건씩 2페이지가 있어도 limit=150이면 정확히 150건에서 멈춘다.
    page1 = {"items": [_thread_item(i) for i in range(100)], "nextPageToken": "tok"}
    page2 = {"items": [_thread_item(i) for i in range(100, 200)]}
    api = _client_with([page1, page2])
    try:
        out = await api.list_comment_threads("vid", limit=150)
    finally:
        await api.aclose()
    assert len(out) == 150
    assert out[0].author == "user0"
    assert out[149].text == "comment 149"


async def test_stops_when_no_next_page_token():
    page1 = {"items": [_thread_item(i) for i in range(30)]}
    api = _client_with([page1])
    try:
        out = await api.list_comment_threads("vid", limit=1000)
    finally:
        await api.aclose()
    assert len(out) == 30


async def test_maps_fields():
    api = _client_with([{"items": [_thread_item(7)]}])
    try:
        out = await api.list_comment_threads("vid", limit=10)
    finally:
        await api.aclose()
    c = out[0]
    assert c.comment_id == "c7"
    assert c.author == "user7"
    assert c.text == "comment 7"
    assert c.like_count == 7
    assert c.published_at == "2026-07-20T10:00:00Z"


async def test_comments_disabled_raises_specific_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"error": {"errors": [{"reason": "commentsDisabled"}]}},
        )

    api = YouTubeAPIClient(
        _polling(), client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    try:
        with pytest.raises(CommentsDisabledError):
            await api.list_comment_threads("vid", limit=10)
    finally:
        await api.aclose()


async def test_other_http_error_raises_generic():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    api = YouTubeAPIClient(
        _polling(), client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    try:
        with pytest.raises(YouTubeAPIError):
            await api.list_comment_threads("vid", limit=10)
    finally:
        await api.aclose()


async def test_quota_recorder_called_once_per_page():
    recorded: list[int] = []

    async def rec(units: int) -> None:
        recorded.append(units)

    page1 = {"items": [_thread_item(i) for i in range(100)], "nextPageToken": "tok"}
    page2 = {"items": [_thread_item(i) for i in range(100, 150)]}
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        page = [page1, page2][calls["n"]]
        calls["n"] += 1
        return httpx.Response(200, json=page)

    api = YouTubeAPIClient(
        _polling(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        recorder=rec,
    )
    try:
        await api.list_comment_threads("vid", limit=1000)
    finally:
        await api.aclose()
    assert recorded == [1, 1]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_comment_fetch.py -v`
Expected: FAIL — `ImportError: cannot import name 'CommentsDisabledError'`

- [ ] **Step 3: 구현**

`app/services/youtube_api.py`의 `YouTubeQuotaExceededError` 아래에 예외를 추가한다:

```python
class CommentsDisabledError(YouTubeAPIError):
    """영상의 댓글이 비활성화됨. LLM 호출 전에 판정되므로 크레딧을 차감하지 않는다."""
```

`VideoMeta` 아래에 dataclass를 추가한다:

```python
@dataclass(frozen=True)
class CommentMeta:
    comment_id: str
    author: str
    text: str
    like_count: int
    published_at: str
```

`VideoMeta`에 필드를 추가한다(기본값이 있는 필드 뒤에 와야 하므로 맨 끝):

```python
@dataclass(frozen=True)
class VideoMeta:
    video_id: str
    video_url: str
    title: str
    description: str | None
    thumbnail_url: str | None
    published_at: str
    duration: str | None
    view_count: int | None
    like_count: int | None
    channel_id: str | None = None
    channel_title: str | None = None
    comment_count: int | None = None
```

`get_video_details()`의 `VideoMeta(...)` 생성에 한 줄을 추가한다:

```python
                        channel_title=snippet.get("channelTitle"),
                        comment_count=to_int(stats.get("commentCount")),
```

`get_video_details` 아래에 수집 메서드를 추가한다:

```python
    async def list_comment_threads(
        self, video_id: str, limit: int, order: str = "relevance"
    ) -> List["CommentMeta"]:
        """최상위 댓글을 limit건까지 수집한다(대댓글 제외).

        commentThreads.list는 호출당 1유닛 — 기존 _get 경로를 타므로
        yt_quota_service 원장에 자동 기록된다. 1,000건 = 10유닛.
        마지막 페이지에서 limit을 넘는 초과분은 잘라낸다.
        """
        out: List[CommentMeta] = []
        page_token: str | None = None
        while len(out) < limit:
            params: Dict[str, Any] = {
                "part": "snippet",
                "videoId": video_id,
                "maxResults": min(100, limit - len(out)),
                "order": order,
                "textFormat": "plainText",
            }
            if page_token:
                params["pageToken"] = page_token
            data = await self._get("commentThreads", params, 1)
            for it in data.get("items") or []:
                top = (
                    ((it.get("snippet") or {}).get("topLevelComment") or {}).get("snippet")
                    or {}
                )
                out.append(
                    CommentMeta(
                        comment_id=it.get("id") or "",
                        author=top.get("authorDisplayName") or "",
                        text=top.get("textDisplay") or "",
                        like_count=int(top.get("likeCount") or 0),
                        published_at=top.get("publishedAt") or "",
                    )
                )
                if len(out) >= limit:
                    break
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return out[:limit]
```

`_get()`이 비200 응답에서 `YouTubeAPIError`만 던지므로, `commentsDisabled`를 구분하도록 `_get`을 수정한다. 기존 오류 분기를 다음으로 교체한다:

```python
        if resp.status_code != 200:
            reason = ""
            try:
                errors = ((resp.json().get("error") or {}).get("errors")) or []
                reason = (errors[0].get("reason") or "") if errors else ""
            except Exception:
                reason = ""
            if reason == "commentsDisabled":
                raise CommentsDisabledError("이 영상은 댓글이 비활성화되어 있습니다.")
            if reason in ("quotaExceeded", "rateLimitExceeded"):
                raise YouTubeQuotaExceededError(
                    f"YouTube API 할당량을 초과했습니다: {reason}"
                )
            raise YouTubeAPIError(f"YouTube API 오류: {resp.status_code} - {resp.text}")
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_comment_fetch.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: 회귀 확인**

`_get` 오류 처리를 바꿨으므로 기존 YouTube 관련 테스트가 깨지지 않아야 한다.

Run: `python -m pytest tests/ -q`
Expected: 전체 통과

- [ ] **Step 6: 커밋**

```bash
git add app/services/youtube_api.py tests/test_comment_fetch.py
git commit -m "feat: commentThreads 수집 + commentsDisabled 오류 구분"
```

---

## Task 6: 분류 응답 파서와 프롬프트

LLM 응답 파싱은 실패 모드가 많아 순수 함수로 분리해 먼저 굳힌다.

**Files:**
- Create: `app/services/comment_prompts.py`
- Modify: `app/services/comment_analysis_service.py` (신규 생성, 파서만)
- Test: `tests/test_comment_parsing.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_comment_parsing.py` 생성:

```python
"""분류 응답 파서 테스트 — LLM 응답의 실패 모드를 방어한다."""

from app.services.comment_analysis_service import (
    LABEL_NEGATIVE,
    LABEL_NEUTRAL,
    LABEL_POSITIVE,
    parse_label_map,
    pick_top_comments,
)
from app.services.youtube_api import CommentMeta


def _c(idx: int, likes: int = 0) -> CommentMeta:
    return CommentMeta(
        comment_id=f"c{idx}", author=f"u{idx}", text=f"t{idx}",
        like_count=likes, published_at="2026-07-20T10:00:00Z",
    )


def test_parses_compact_map():
    got = parse_label_map('{"0":"p","1":"n","2":"u"}', batch_size=3)
    assert got == {0: LABEL_POSITIVE, 1: LABEL_NEGATIVE, 2: LABEL_NEUTRAL}


def test_strips_code_fence():
    raw = '```json\n{"0":"p","1":"n"}\n```'
    assert parse_label_map(raw, batch_size=2) == {0: LABEL_POSITIVE, 1: LABEL_NEGATIVE}


def test_missing_index_falls_back_to_neutral():
    got = parse_label_map('{"0":"p"}', batch_size=3)
    assert got == {0: LABEL_POSITIVE, 1: LABEL_NEUTRAL, 2: LABEL_NEUTRAL}


def test_unknown_label_falls_back_to_neutral():
    got = parse_label_map('{"0":"x","1":"p"}', batch_size=2)
    assert got == {0: LABEL_NEUTRAL, 1: LABEL_POSITIVE}


def test_out_of_range_index_is_ignored():
    got = parse_label_map('{"0":"p","9":"n"}', batch_size=2)
    assert got == {0: LABEL_POSITIVE, 1: LABEL_NEUTRAL}


def test_truncated_json_yields_all_neutral():
    got = parse_label_map('{"0":"p","1":', batch_size=2)
    assert got == {0: LABEL_NEUTRAL, 1: LABEL_NEUTRAL}


def test_empty_response_yields_all_neutral():
    assert parse_label_map("", batch_size=2) == {0: LABEL_NEUTRAL, 1: LABEL_NEUTRAL}


def test_pick_top_comments_sorts_by_likes_desc_and_caps():
    comments = [_c(i, likes=i) for i in range(15)]
    top = pick_top_comments(comments, 10)
    assert len(top) == 10
    assert top[0]["like_count"] == 14
    assert top[0]["author"] == "u14"
    assert top[-1]["like_count"] == 5


def test_pick_top_comments_handles_fewer_than_cap():
    top = pick_top_comments([_c(1, 5), _c(2, 3)], 10)
    assert len(top) == 2
    assert set(top[0].keys()) == {"author", "text", "like_count", "published_at"}
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_comment_parsing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.comment_analysis_service'`

- [ ] **Step 3: 프롬프트 모듈 생성**

`app/services/comment_prompts.py`:

```python
"""댓글 반응 분석 기본 프롬프트.

그룹 설정 prompts.comment_analysis_prompt가 비어 있을 때 사용한다.
분류 프롬프트는 출력 토큰을 최소화하기 위해 인덱스→라벨 압축 맵을 요구한다 —
건별 id/confidence를 되돌려받으면 출력이 3~4배로 늘고, 출력 단가가 입력의
8배가량이라 비용을 지배한다.
"""

DEFAULT_CLASSIFY_PROMPT = """다음은 YouTube 영상의 댓글 목록이다. 각 댓글의 감정을 분류하라.

라벨:
- p = 긍정 (호평, 감사, 지지, 공감)
- n = 부정 (비판, 실망, 반대, 분노)
- u = 중립 (질문, 정보 공유, 판단 불가, 무관한 내용)

반드시 아래 형식의 JSON 객체만 출력한다. 설명·주석·코드펜스를 붙이지 않는다.
키는 댓글 번호 문자열, 값은 p/n/u 중 하나다.

{"0":"p","1":"n","2":"u"}

댓글 목록:
"""

DEFAULT_INSIGHT_PROMPT = """다음은 YouTube 영상의 댓글을 감정별로 분류한 결과다.
각 카테고리별로 시청자 반응을 요약하고 인사이트를 도출하라.

반드시 아래 구조의 JSON 객체만 출력한다. 설명·코드펜스를 붙이지 않는다.
댓글이 없는 카테고리는 summary와 insights를 빈 문자열, key_points를 빈 배열로 둔다.

{
  "positive": {"summary": "2~3문장", "key_points": ["3~5개"], "insights": "2~3문장"},
  "negative": {"summary": "", "key_points": [], "insights": ""},
  "neutral":  {"summary": "", "key_points": [], "insights": ""}
}

summary는 해당 카테고리 댓글의 주된 내용을, insights는 콘텐츠 제작자가
참고할 만한 시사점을 쓴다. 모든 텍스트는 한국어로 작성한다.

분류된 댓글:
"""
```

- [ ] **Step 4: 파서 구현**

`app/services/comment_analysis_service.py` 생성 (이번 태스크에서는 순수 함수만):

```python
"""댓글 반응 분석 파이프라인: 수집 → 분류 → 인사이트.

분류는 200건 배치를 순차 호출한다. 병렬 호출은 게이트웨이 레이트리밋을
건드릴 위험이 있고, 이 기능은 지연보다 안정성이 중요하다.
배치 하나가 실패해도 그 배치만 중립 처리하고 계속 진행한다 —
전체 분석을 버리지 않는다.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List

from app.services.youtube_api import CommentMeta

LABEL_POSITIVE = "positive"
LABEL_NEGATIVE = "negative"
LABEL_NEUTRAL = "neutral"

_LABEL_MAP = {"p": LABEL_POSITIVE, "n": LABEL_NEGATIVE, "u": LABEL_NEUTRAL}

BATCH_SIZE = 200
TOP_COMMENTS_PER_CATEGORY = 10


def _strip_fence(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = "\n".join(
            line for line in t.splitlines() if not line.startswith("```")
        ).strip()
    return t


def parse_label_map(raw: str, batch_size: int) -> Dict[int, str]:
    """LLM 응답을 {배치내 인덱스: 라벨}로 변환한다.

    파싱 실패·인덱스 누락·알 수 없는 라벨은 모두 중립으로 폴백한다.
    반환 dict은 항상 0..batch_size-1 전체를 포함한다.
    """
    out: Dict[int, str] = {i: LABEL_NEUTRAL for i in range(batch_size)}
    try:
        parsed = json.loads(_strip_fence(raw))
    except (json.JSONDecodeError, TypeError):
        return out
    if not isinstance(parsed, dict):
        return out
    for key, value in parsed.items():
        try:
            idx = int(key)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < batch_size:
            out[idx] = _LABEL_MAP.get(str(value).strip().lower(), LABEL_NEUTRAL)
    return out


def pick_top_comments(
    comments: Iterable[CommentMeta], cap: int = TOP_COMMENTS_PER_CATEGORY
) -> List[Dict[str, Any]]:
    """좋아요순 상위 cap개를 저장용 dict로 변환한다.

    result JSONB에 들어가는 유일한 댓글 원문 — 전량은 저장하지 않는다.
    """
    ordered = sorted(comments, key=lambda c: c.like_count, reverse=True)[:cap]
    return [
        {
            "author": c.author,
            "text": c.text,
            "like_count": c.like_count,
            "published_at": c.published_at,
        }
        for c in ordered
    ]
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/test_comment_parsing.py -v`
Expected: PASS (9 passed)

- [ ] **Step 6: 커밋**

```bash
git add app/services/comment_prompts.py app/services/comment_analysis_service.py tests/test_comment_parsing.py
git commit -m "feat: 댓글 분류 응답 파서 + 기본 프롬프트"
```

---

## Task 7: 분석 파이프라인 조립

**Files:**
- Modify: `app/services/comment_analysis_service.py`, `app/services/settings_types.py`
- Test: `tests/test_comment_pipeline.py`

- [ ] **Step 1: 프롬프트 설정 필드 추가**

`app/services/settings_types.py`의 `PromptSettings`에 필드를 추가한다:

```python
@dataclass
class PromptSettings:
    analysis_prompt: str = ""
    digest_prompt: str = ""
    preset_id: Optional[int] = None
    comment_analysis_prompt: str = ""
```

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_comment_pipeline.py` 생성:

```python
"""분류·집계 파이프라인 테스트 — LLM 호출은 스텁으로 대체한다."""

from app.services.comment_analysis_service import (
    LABEL_NEGATIVE,
    LABEL_NEUTRAL,
    LABEL_POSITIVE,
    classify_comments,
    group_by_label,
)
from app.services.youtube_api import CommentMeta


def _c(idx: int, likes: int = 0) -> CommentMeta:
    return CommentMeta(
        comment_id=f"c{idx}", author=f"u{idx}", text=f"t{idx}",
        like_count=likes, published_at="2026-07-20T10:00:00Z",
    )


async def test_classify_splits_into_batches_and_merges():
    seen_sizes: list[int] = []

    async def fake_call(prompt_text: str, batch_size: int) -> str:
        seen_sizes.append(batch_size)
        return "{" + ",".join(f'"{i}":"p"' for i in range(batch_size)) + "}"

    comments = [_c(i) for i in range(450)]
    labels, failures = await classify_comments(comments, fake_call, batch_size=200)

    assert seen_sizes == [200, 200, 50]
    assert len(labels) == 450
    assert all(v == LABEL_POSITIVE for v in labels)
    assert failures == 0


async def test_failed_batch_becomes_neutral_and_counts():
    async def fake_call(prompt_text: str, batch_size: int) -> str:
        if batch_size == 200:
            raise RuntimeError("gateway down")
        return "{" + ",".join(f'"{i}":"n"' for i in range(batch_size)) + "}"

    comments = [_c(i) for i in range(250)]
    labels, failures = await classify_comments(comments, fake_call, batch_size=200)

    assert failures == 1
    assert labels[:200] == [LABEL_NEUTRAL] * 200
    assert labels[200:] == [LABEL_NEGATIVE] * 50


async def test_empty_comments_returns_empty():
    async def fake_call(prompt_text: str, batch_size: int) -> str:
        raise AssertionError("호출되면 안 됨")

    labels, failures = await classify_comments([], fake_call, batch_size=200)
    assert labels == []
    assert failures == 0


def test_group_by_label_partitions_all_comments():
    comments = [_c(0), _c(1), _c(2)]
    labels = [LABEL_POSITIVE, LABEL_NEGATIVE, LABEL_POSITIVE]
    grouped = group_by_label(comments, labels)

    assert [c.comment_id for c in grouped[LABEL_POSITIVE]] == ["c0", "c2"]
    assert [c.comment_id for c in grouped[LABEL_NEGATIVE]] == ["c1"]
    assert grouped[LABEL_NEUTRAL] == []


def test_group_by_label_tolerates_length_mismatch():
    # 라벨이 부족하면 남은 댓글은 중립으로 처리한다.
    comments = [_c(0), _c(1), _c(2)]
    grouped = group_by_label(comments, [LABEL_POSITIVE])
    assert len(grouped[LABEL_POSITIVE]) == 1
    assert len(grouped[LABEL_NEUTRAL]) == 2
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `python -m pytest tests/test_comment_pipeline.py -v`
Expected: FAIL — `ImportError: cannot import name 'classify_comments'`

- [ ] **Step 4: 구현**

먼저 `app/services/comment_analysis_service.py` 상단의 typing 임포트를 다음으로 교체한다:

```python
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Tuple
```

그리고 `pick_top_comments` 아래에 추가:

```python
# (프롬프트 본문, 배치 크기) → LLM 원문 응답
ClassifyCall = Callable[[str, int], Awaitable[str]]


def build_classify_prompt(base_prompt: str, batch: List[CommentMeta]) -> str:
    """번호가 매겨진 댓글 목록을 프롬프트에 붙인다."""
    lines = [f"{i}. {c.text}" for i, c in enumerate(batch)]
    return base_prompt + "\n".join(lines)


async def classify_comments(
    comments: List[CommentMeta],
    call: ClassifyCall,
    batch_size: int = BATCH_SIZE,
    base_prompt: str = "",
) -> Tuple[List[str], int]:
    """댓글 전체를 배치로 순차 분류한다. (라벨 리스트, 실패 배치 수) 반환.

    call은 프롬프트와 배치 크기를 받아 LLM 원문을 돌려주는 주입 함수 —
    테스트에서 게이트웨이 없이 파이프라인을 검증할 수 있다.
    base_prompt가 비면 코드 기본 프롬프트를 쓴다(그룹 설정 미지정 시).
    배치 하나가 예외를 던지면 그 배치만 전부 중립 처리하고 계속 진행한다.
    """
    from app.services.comment_prompts import DEFAULT_CLASSIFY_PROMPT

    prompt_base = base_prompt or DEFAULT_CLASSIFY_PROMPT
    labels: List[str] = []
    failures = 0
    for start in range(0, len(comments), batch_size):
        batch = comments[start : start + batch_size]
        prompt = build_classify_prompt(prompt_base, batch)
        try:
            raw = await call(prompt, len(batch))
            label_map = parse_label_map(raw, len(batch))
        except Exception as e:  # noqa: BLE001 — 배치 격리: 한 배치 실패가 전체를 깨지 않음
            print(f"[comment-analysis] 배치 분류 실패(중립 처리): {e}")
            failures += 1
            label_map = {i: LABEL_NEUTRAL for i in range(len(batch))}
        labels.extend(label_map[i] for i in range(len(batch)))
    return labels, failures


def group_by_label(
    comments: List[CommentMeta], labels: List[str]
) -> Dict[str, List[CommentMeta]]:
    """댓글을 라벨별로 분할한다. 라벨이 부족하면 남은 댓글은 중립."""
    grouped: Dict[str, List[CommentMeta]] = {
        LABEL_POSITIVE: [], LABEL_NEGATIVE: [], LABEL_NEUTRAL: [],
    }
    for i, c in enumerate(comments):
        label = labels[i] if i < len(labels) else LABEL_NEUTRAL
        grouped.get(label, grouped[LABEL_NEUTRAL]).append(c)
    return grouped
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/test_comment_pipeline.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: 커밋**

```bash
git add app/services/comment_analysis_service.py app/services/settings_types.py tests/test_comment_pipeline.py
git commit -m "feat: 댓글 분류 배치 파이프라인 + 라벨 그룹화"
```

---

## Task 8: 실행 오케스트레이터 (수집 → 분석 → 저장 → 원장)

**Files:**
- Modify: `app/services/comment_analysis_service.py`
- Test: 없음 (DB·게이트웨이 통합 지점 — Task 13에서 수동 검증)

- [ ] **Step 1: 인사이트 생성 함수 추가**

`app/services/comment_analysis_service.py`에 추가:

```python
def build_insight_prompt(
    base_prompt: str, grouped: Dict[str, List[CommentMeta]], cap: int = 40
) -> str:
    """카테고리별 좋아요순 상위 댓글만 추려 인사이트 프롬프트를 만든다.

    전량을 다시 보내면 입력 토큰이 두 배가 되므로 상위 cap개만 보낸다.
    """
    parts: List[str] = [base_prompt]
    for label in (LABEL_POSITIVE, LABEL_NEGATIVE, LABEL_NEUTRAL):
        items = sorted(grouped[label], key=lambda c: c.like_count, reverse=True)[:cap]
        parts.append(f"\n[{label}] ({len(grouped[label])}건)")
        if not items:
            parts.append("(없음)")
        else:
            parts.extend(f"- {c.text}" for c in items)
    return "\n".join(parts)


def parse_insight_response(raw: str) -> Dict[str, Dict[str, Any]]:
    """인사이트 응답을 카테고리별 dict로 변환한다. 실패 시 빈 구조."""
    empty = {"summary": "", "key_points": [], "insights": ""}
    out = {
        LABEL_POSITIVE: dict(empty),
        LABEL_NEGATIVE: dict(empty),
        LABEL_NEUTRAL: dict(empty),
    }
    try:
        parsed = json.loads(_strip_fence(raw))
    except (json.JSONDecodeError, TypeError):
        return out
    if not isinstance(parsed, dict):
        return out
    for label in (LABEL_POSITIVE, LABEL_NEGATIVE, LABEL_NEUTRAL):
        entry = parsed.get(label)
        if not isinstance(entry, dict):
            continue
        kp = entry.get("key_points")
        out[label] = {
            "summary": str(entry.get("summary") or ""),
            "key_points": [str(x) for x in kp] if isinstance(kp, list) else [],
            "insights": str(entry.get("insights") or ""),
        }
    return out
```

- [ ] **Step 2: 오케스트레이터 추가**

같은 파일 끝에 추가:

```python
async def run_comment_analysis(
    group, video_pk: int, video_id: str, user_id: int, requested_limit: int
) -> None:
    """BackgroundTasks 진입점. 예외를 밖으로 던지지 않는다.

    크레딧은 수집 완료 후에 확정 기록하고, LLM 실패 시 환급(원장 행 삭제)한다.
    실행되지 않은 분석에 크레딧을 물리지 않기 위해서다.
    """
    from dataclasses import replace
    from datetime import datetime, timezone

    from sqlalchemy import delete, select, update

    from app.control_db import get_sessionmaker
    from app.models.control.comment_analysis_run import CommentAnalysisRun
    from app.models.pg.comment_analysis import (
        STATUS_DONE, STATUS_FAILED, CommentAnalysis,
    )
    from app.models.pg.video import Video
    from app.services.ai_usage_service import record_usage
    from app.services.comment_prompts import DEFAULT_INSIGHT_PROMPT
    from app.services.db_engine import data_plane_engine_manager as dpm
    from app.services.global_settings import resolve_youtube_key
    from app.services.llm_client import LiteLLMClient
    from app.services.quota_service import (
        QuotaExceeded, check_comment_analysis_quota, credits_for,
    )
    from app.services.settings_manager import get_settings_manager
    from app.services.yt_quota_service import make_recorder
    from app.services.youtube_api import YouTubeAPIClient

    run_id: int | None = None

    async def _fail(message: str) -> None:
        if run_id is not None:
            async with get_sessionmaker()() as s:
                async with s.begin():
                    await s.execute(
                        delete(CommentAnalysisRun).where(
                            CommentAnalysisRun.run_id == run_id
                        )
                    )
        async with dpm.group_session(group) as s:
            async with s.begin():
                await s.execute(
                    update(CommentAnalysis)
                    .where(CommentAnalysis.video_pk == video_pk)
                    .values(status=STATUS_FAILED, error=message[:2000])
                )

    try:
        # 1) 수집 — 이 단계 실패는 원장 기록 이전이므로 자연히 무차감이다.
        polling = await get_settings_manager().get_polling(group.group_id)
        api_key = await resolve_youtube_key(group.group_id)
        if not api_key:
            await _fail("YouTube API 키가 없습니다.")
            return
        polling = replace(polling, youtube_api_key=api_key)
        api = YouTubeAPIClient(polling, recorder=make_recorder(api_key))
        try:
            comments = await api.list_comment_threads(video_id, requested_limit)
        finally:
            await api.aclose()

        if not comments:
            await _fail("분석할 댓글이 없습니다.")
            return

        # 2) 크레딧 확정 — 요청 시점 검사와의 경합을 닫기 위해 같은 트랜잭션에서 재검사.
        credits = credits_for(requested_limit)
        async with get_sessionmaker()() as s:
            async with s.begin():
                try:
                    await check_comment_analysis_quota(s, user_id, requested_limit)
                except QuotaExceeded as e:
                    await _fail(e.detail)
                    return
                row = CommentAnalysisRun(
                    user_id=user_id,
                    group_id=group.group_id,
                    video_id=video_id,
                    credits=credits,
                    requested_limit=requested_limit,
                    comment_count=len(comments),
                )
                s.add(row)
                await s.flush()
                run_id = row.run_id

        # 3) 분류 + 인사이트
        gateway = await get_settings_manager().get_ai_gateway(group.group_id)
        prompts = await get_settings_manager().get_prompts(group.group_id)
        model = gateway.primary_model
        client = LiteLLMClient(gateway)
        totals = {"input": 0, "output": 0}

        async def _call(prompt_text: str, _batch_size: int) -> str:
            res = await client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt_text}],
                response_format={"type": "json_object"},
                temperature=gateway.temperature,
            )
            totals["input"] += res.input_tokens or 0
            totals["output"] += res.output_tokens or 0
            return res.content

        try:
            labels, failures = await classify_comments(
                comments, _call, base_prompt=prompts.comment_analysis_prompt
            )
            grouped = group_by_label(comments, labels)
            insight_raw = await _call(
                build_insight_prompt(DEFAULT_INSIGHT_PROMPT, grouped), 0
            )
            insights = parse_insight_response(insight_raw)
        finally:
            await client.aclose()
            await record_usage(
                user_id=user_id,
                group_id=group.group_id,
                purpose="comment_analysis",
                model=model,
                input_tokens=totals["input"],
                output_tokens=totals["output"],
                video_pk=video_pk,
            )

        # 4) 저장
        result = {
            "categories": {
                label: {
                    **insights[label],
                    "top_comments": pick_top_comments(grouped[label]),
                }
                for label in (LABEL_POSITIVE, LABEL_NEGATIVE, LABEL_NEUTRAL)
            },
            "order": "relevance",
            "batch_failures": failures,
        }
        async with dpm.group_session(group) as s:
            async with s.begin():
                # 분석 시점 영상 전체 댓글 수를 스냅샷한다 — 이후 videos.comment_count와
                # 비교해 신선도 배너를 띄운다. 통계 미갱신 영상은 수집분으로 폴백.
                current_total = (
                    await s.execute(
                        select(Video.comment_count).where(Video.video_pk == video_pk)
                    )
                ).scalar_one_or_none()
                await s.execute(
                    update(CommentAnalysis)
                    .where(CommentAnalysis.video_pk == video_pk)
                    .values(
                        status=STATUS_DONE,
                        fetched_count=len(comments),
                        total_count=current_total or len(comments),
                        positive_count=len(grouped[LABEL_POSITIVE]),
                        negative_count=len(grouped[LABEL_NEGATIVE]),
                        neutral_count=len(grouped[LABEL_NEUTRAL]),
                        result=result,
                        model=model,
                        partial=failures > 0,
                        error=None,
                        analyzed_at=datetime.now(timezone.utc),
                    )
                )
    except Exception as e:  # noqa: BLE001 — 백그라운드 작업은 예외를 삼키고 상태로 남긴다
        print(f"[comment-analysis] 실패: {e}")
        await _fail(str(e))
```

- [ ] **Step 3: 임포트 정리 확인**

Run: `python -c "import app.services.comment_analysis_service"`
Expected: 오류 없이 종료

- [ ] **Step 4: 기존 테스트 회귀 확인**

Run: `python -m pytest tests/ -q`
Expected: 전체 통과

- [ ] **Step 5: 커밋**

```bash
git add app/services/comment_analysis_service.py
git commit -m "feat: 댓글 분석 오케스트레이터 (수집→분류→인사이트→저장)"
```

---

## Task 9: API 라우터

**Files:**
- Create: `app/schemas/comment_analysis.py`, `app/routers/comment_analysis.py`
- Modify: `app/main.py`, `app/routers/auth.py`

- [ ] **Step 1: 스키마 작성**

`app/schemas/comment_analysis.py`:

```python
"""댓글 반응 분석 입출력 스키마."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class StartCommentAnalysisRequest(BaseModel):
    limit: int = Field(default=1000, ge=100, le=100000)


class StartByUrlRequest(BaseModel):
    video_url: str
    limit: int = Field(default=1000, ge=100, le=100000)


class StartCommentAnalysisResponse(BaseModel):
    video_pk: int
    status: str
    queued: bool


class CommentAnalysisOut(BaseModel):
    video_pk: int
    status: str
    requested_limit: int
    fetched_count: Optional[int] = None
    total_count: Optional[int] = None
    current_comment_count: Optional[int] = None  # videos.comment_count (신선도 비교용)
    positive_count: Optional[int] = None
    negative_count: Optional[int] = None
    neutral_count: Optional[int] = None
    result: Optional[dict[str, Any]] = None
    model: Optional[str] = None
    partial: bool = False
    error: Optional[str] = None
    analyzed_at: Optional[datetime] = None


class CommentAnalysisListItem(BaseModel):
    video_pk: int
    video_id: str
    title: str
    thumbnail_url: Optional[str] = None
    status: str
    positive_count: Optional[int] = None
    negative_count: Optional[int] = None
    neutral_count: Optional[int] = None
    analyzed_at: Optional[datetime] = None


class CommentCreditsOut(BaseModel):
    used: int
    limit: int
    unlimited: bool
    per_analysis_max: int
```

- [ ] **Step 2: 라우터 작성**

`app/routers/comment_analysis.py`:

```python
"""그룹 댓글 반응 분석 API.

videos.py가 이미 590줄이라 여기에 더 얹지 않고 별도 라우터로 분리한다.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select, update

from app.models.control.group import Group
from app.models.pg.comment_analysis import (
    STATUS_RUNNING,
    CommentAnalysis,
)
from app.models.pg.video import Video
from app.routers.auth import CurrentUser, require_user
from app.routers.deps import get_group_or_404
from app.schemas.comment_analysis import (
    CommentAnalysisListItem,
    CommentAnalysisOut,
    StartByUrlRequest,
    StartCommentAnalysisRequest,
    StartCommentAnalysisResponse,
)
from app.control_db import get_sessionmaker
from app.services.ai_usage_service import BudgetExceeded, check_monthly_budget
from app.services.comment_analysis_service import run_comment_analysis
from app.services.db_engine import data_plane_engine_manager as dpm
from app.services.quota_service import QuotaExceeded, check_comment_analysis_quota
from app.services.yt_quota_service import system_hard_blocked

router = APIRouter(prefix="/api/groups/{slug}", tags=["comment-analysis"])


async def _preflight(user: CurrentUser, requested_limit: int) -> None:
    """쿼터 → 예산 → YouTube 하드 게이트 순으로 검사. 모두 400으로 변환."""
    async with get_sessionmaker()() as session:
        try:
            await check_comment_analysis_quota(session, user.user_id, requested_limit)
            await check_monthly_budget(session, user.user_id)
        except QuotaExceeded as e:
            raise HTTPException(status_code=400, detail=e.detail) from e
        except BudgetExceeded as e:
            raise HTTPException(status_code=400, detail=e.detail) from e
    if await system_hard_blocked():
        raise HTTPException(
            status_code=400, detail="YouTube API 할당량을 초과했습니다."
        )


async def _start(
    group: Group, video_pk: int, video_id: str, user: CurrentUser,
    requested_limit: int, background: BackgroundTasks,
) -> StartCommentAnalysisResponse:
    """행을 running으로 만들고 백그라운드 작업을 등록한다."""
    async with dpm.group_session(group) as session:
        async with session.begin():
            existing = (
                await session.execute(
                    select(CommentAnalysis).where(CommentAnalysis.video_pk == video_pk)
                )
            ).scalar_one_or_none()
            if existing is not None and existing.status == STATUS_RUNNING:
                raise HTTPException(status_code=409, detail="이미 분석이 진행 중입니다.")
            if existing is None:
                session.add(
                    CommentAnalysis(
                        video_pk=video_pk,
                        status=STATUS_RUNNING,
                        requested_limit=requested_limit,
                    )
                )
            else:
                await session.execute(
                    update(CommentAnalysis)
                    .where(CommentAnalysis.video_pk == video_pk)
                    .values(
                        status=STATUS_RUNNING,
                        requested_limit=requested_limit,
                        error=None,
                    )
                )
    background.add_task(
        run_comment_analysis, group, video_pk, video_id, user.user_id, requested_limit
    )
    return StartCommentAnalysisResponse(
        video_pk=video_pk, status=STATUS_RUNNING, queued=True
    )


@router.post(
    "/videos/{video_pk}/comment-analysis",
    response_model=StartCommentAnalysisResponse,
    status_code=202,
)
async def start_comment_analysis(
    video_pk: int,
    payload: StartCommentAnalysisRequest,
    background: BackgroundTasks,
    group: Group = Depends(get_group_or_404),
    user: CurrentUser = Depends(require_user),
) -> StartCommentAnalysisResponse:
    await _preflight(user, payload.limit)
    async with dpm.group_session(group) as session:
        video_id = (
            await session.execute(
                select(Video.video_id).where(Video.video_pk == video_pk)
            )
        ).scalar_one_or_none()
    if video_id is None:
        raise HTTPException(status_code=404, detail="영상을 찾을 수 없습니다.")
    return await _start(group, video_pk, video_id, user, payload.limit, background)


@router.get(
    "/videos/{video_pk}/comment-analysis", response_model=CommentAnalysisOut
)
async def get_comment_analysis(
    video_pk: int,
    group: Group = Depends(get_group_or_404),
    user: CurrentUser = Depends(require_user),
) -> CommentAnalysisOut:
    async with dpm.group_session(group) as session:
        row = (
            await session.execute(
                select(CommentAnalysis).where(CommentAnalysis.video_pk == video_pk)
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="분석 결과가 없습니다.")
        current = (
            await session.execute(
                select(Video.comment_count).where(Video.video_pk == video_pk)
            )
        ).scalar_one_or_none()
    return CommentAnalysisOut(
        video_pk=row.video_pk,
        status=row.status,
        requested_limit=row.requested_limit,
        fetched_count=row.fetched_count,
        total_count=row.total_count,
        current_comment_count=current,
        positive_count=row.positive_count,
        negative_count=row.negative_count,
        neutral_count=row.neutral_count,
        result=row.result,
        model=row.model,
        partial=row.partial,
        error=row.error,
        analyzed_at=row.analyzed_at,
    )


@router.get("/comment-analyses", response_model=list[CommentAnalysisListItem])
async def list_comment_analyses(
    limit: int = 20,
    group: Group = Depends(get_group_or_404),
    user: CurrentUser = Depends(require_user),
) -> list[CommentAnalysisListItem]:
    """최근 갱신순 목록 — URL로 분석한 영상을 다시 찾아가는 경로."""
    async with dpm.group_session(group) as session:
        rows = (
            await session.execute(
                select(CommentAnalysis, Video)
                .join(Video, Video.video_pk == CommentAnalysis.video_pk)
                .order_by(CommentAnalysis.updated_at.desc())
                .limit(min(max(1, limit), 100))
            )
        ).all()
    return [
        CommentAnalysisListItem(
            video_pk=ca.video_pk,
            video_id=v.video_id,
            title=v.title,
            thumbnail_url=v.thumbnail_url,
            status=ca.status,
            positive_count=ca.positive_count,
            negative_count=ca.negative_count,
            neutral_count=ca.neutral_count,
            analyzed_at=ca.analyzed_at,
        )
        for ca, v in rows
    ]


@router.post(
    "/comment-analysis/by-url",
    response_model=StartCommentAnalysisResponse,
    status_code=202,
)
async def start_by_url(
    payload: StartByUrlRequest,
    background: BackgroundTasks,
    group: Group = Depends(get_group_or_404),
    user: CurrentUser = Depends(require_user),
) -> StartCommentAnalysisResponse:
    """미등록 영상은 videos에 먼저 등록한 뒤 같은 경로로 합류시킨다."""
    from app.routers.videos import ensure_video_row

    await _preflight(user, payload.limit)
    video_pk, video_id = await ensure_video_row(group, payload.video_url)
    return await _start(group, video_pk, video_id, user, payload.limit, background)
```

- [ ] **Step 3: `ensure_video_row` 헬퍼 추출**

`app/routers/videos.py`의 `instant_analyze_video`가 하는 "URL → videos 행 확보" 로직을 재사용 가능한 함수로 꺼낸다. `_ensure_instant_channel` 아래에 추가한다:

```python
async def ensure_video_row(group: Group, video_url: str) -> tuple[int, str]:
    """URL/ID로 videos 행을 확보하고 (video_pk, video_id)를 돌려준다.

    이미 등록된 영상이면 그 행을 쓴다. 미등록이면 메타를 조회해 삽입하며,
    실제 채널이 없으면 __instant__ 가상 채널에 붙인다.
    댓글 분석의 by-url 진입점과 즉시분석이 이 함수를 공유해 저장 경로를 통일한다.
    """
    video_id = _extract_video_id(video_url)
    if not video_id:
        raise HTTPException(status_code=400, detail="유효한 YouTube 영상 URL/ID가 아닙니다.")

    async with dpm.group_session(group) as session:
        existing = (
            await session.execute(select(Video).where(Video.video_id == video_id))
        ).scalar_one_or_none()
        if existing is not None:
            return existing.video_pk, video_id

    polling = await get_settings_manager().get_polling(group.group_id)
    api_key = await resolve_youtube_key(group.group_id)
    if not api_key:
        raise HTTPException(status_code=400, detail="YouTube API 키가 없습니다.")
    polling = replace(polling, youtube_api_key=api_key)

    api = YouTubeAPIClient(polling, recorder=make_recorder(api_key))
    try:
        metas = await api.get_video_details([video_id])
    except YouTubeAPIError as e:
        raise HTTPException(status_code=400, detail=f"영상 메타 조회 실패: {e}") from e
    finally:
        await api.aclose()
    if not metas:
        raise HTTPException(status_code=404, detail="해당 영상을 찾을 수 없습니다.")
    vm = metas[0]

    async with dpm.group_session(group) as session:
        async with session.begin():
            channel = (
                await session.execute(select(Channel).where(Channel.channel_id == vm.channel_id))
            ).scalar_one_or_none()
            if channel is None:
                channel = await _ensure_instant_channel(
                    session, polling.default_channel_interval_min or 720
                )
            video = Video(
                channel_pk=channel.channel_pk,
                video_id=vm.video_id,
                video_url=vm.video_url,
                title=vm.title or vm.video_id,
                description=vm.description,
                thumbnail_url=vm.thumbnail_url,
                published_at=parse_iso_datetime(vm.published_at),
                duration_seconds=parse_duration_seconds(vm.duration),
                view_count=vm.view_count,
                like_count=vm.like_count,
                comment_count=vm.comment_count,
                source_channel_name=vm.channel_title,
                analysis_status="pending",
            )
            session.add(video)
            await session.flush()
            return video.video_pk, video_id
```

- [ ] **Step 4: 크레딧 조회 엔드포인트**

`app/routers/auth.py`의 `me_router` 블록에 추가한다:

```python
@me_router.get("/comment-credits", response_model=CommentCreditsOut)
async def my_comment_credits(
    user: CurrentUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> CommentCreditsOut:
    from app.services.quota_service import count_monthly_credits, effective_limits

    limits = await effective_limits(session, user.user_id)
    if limits is None:
        return CommentCreditsOut(
            used=0, limit=0, unlimited=True, per_analysis_max=100000
        )
    used = await count_monthly_credits(session, user.user_id)
    return CommentCreditsOut(
        used=used,
        limit=limits.max_comment_analyses_per_month,
        unlimited=False,
        per_analysis_max=limits.max_comments_per_analysis,
    )
```

파일 상단 임포트에 추가:

```python
from app.schemas.comment_analysis import CommentCreditsOut
```

- [ ] **Step 5: 라우터 등록**

`app/main.py`의 임포트에 `comment_analysis`를 추가하고, `videos.router` 등록 아래에 한 줄을 넣는다:

```python
app.include_router(comment_analysis.router, dependencies=_protected)
```

- [ ] **Step 6: 앱 부팅 확인**

Run: `python -c "from app.main import app; print(len(app.routes))"`
Expected: 오류 없이 라우트 수 출력

- [ ] **Step 7: 회귀 확인 후 커밋**

Run: `python -m pytest tests/ -q`
Expected: 전체 통과

```bash
git add app/schemas/comment_analysis.py app/routers/comment_analysis.py app/routers/videos.py app/routers/auth.py app/main.py
git commit -m "feat: 댓글 분석 API 라우터 + 크레딧 조회"
```

---

## Task 10: 프론트 순수 로직과 API 클라이언트

**Files:**
- Create: `frontend/src/components/CommentAnalysis.logic.ts`, `frontend/src/api/comments.ts`
- Modify: `frontend/src/api/types.ts`
- Test: `frontend/src/components/CommentAnalysis.logic.test.ts`

- [ ] **Step 1: 실패하는 테스트 작성**

`frontend/src/components/CommentAnalysis.logic.test.ts`:

```typescript
import { describe, it, expect } from 'vitest'
import {
  cardState,
  creditCost,
  freshness,
  optionEnabled,
  ratioPercents,
} from './CommentAnalysis.logic'

describe('creditCost', () => {
  it('1,000건 단위로 올림하되 하한은 1회', () => {
    expect(creditCost(500)).toBe(1)
    expect(creditCost(1000)).toBe(1)
    expect(creditCost(1001)).toBe(2)
    expect(creditCost(2000)).toBe(2)
    expect(creditCost(5000)).toBe(5)
  })
})

describe('optionEnabled', () => {
  it('관리자는 모든 수량이 열린다', () => {
    expect(optionEnabled(5000, { unlimited: true, perAnalysisMax: 1000 })).toBe(true)
  })
  it('플랜 상한을 넘는 수량은 비활성', () => {
    expect(optionEnabled(2000, { unlimited: false, perAnalysisMax: 1000 })).toBe(false)
    expect(optionEnabled(1000, { unlimited: false, perAnalysisMax: 1000 })).toBe(true)
  })
})

describe('freshness', () => {
  it('현재 댓글 수를 모르면 배너를 띄우지 않는다', () => {
    expect(freshness(null, 1000)).toEqual({ stale: false, added: 0 })
  })
  it('분석 시점 총수를 모르면 배너를 띄우지 않는다', () => {
    expect(freshness(1200, null)).toEqual({ stale: false, added: 0 })
  })
  it('변화가 없으면 신선하다', () => {
    expect(freshness(1000, 1000)).toEqual({ stale: false, added: 0 })
  })
  it('줄어들었으면 배너를 띄우지 않는다', () => {
    expect(freshness(900, 1000)).toEqual({ stale: false, added: 0 })
  })
  it('늘어난 만큼 added를 계산한다', () => {
    expect(freshness(1087, 1000)).toEqual({ stale: true, added: 87 })
  })
})

describe('ratioPercents', () => {
  it('합이 0이면 모두 0', () => {
    expect(ratioPercents(0, 0, 0)).toEqual({ positive: 0, negative: 0, neutral: 0 })
  })
  it('백분율을 반올림한다', () => {
    expect(ratioPercents(50, 30, 20)).toEqual({ positive: 50, negative: 30, neutral: 20 })
  })
  it('null은 0으로 취급한다', () => {
    expect(ratioPercents(null, null, null)).toEqual({ positive: 0, negative: 0, neutral: 0 })
  })
})

describe('cardState', () => {
  it('분석 기록이 없으면 none', () => {
    expect(cardState(null, null, null)).toBe('none')
    expect(cardState(undefined, 100, 100)).toBe('none')
  })
  it('pending과 running은 running', () => {
    expect(cardState('pending', null, null)).toBe('running')
    expect(cardState('running', null, null)).toBe('running')
  })
  it('failed는 failed', () => {
    expect(cardState('failed', null, null)).toBe('failed')
  })
  it('done이고 댓글이 안 늘었으면 done', () => {
    expect(cardState('done', 1000, 1000)).toBe('done')
  })
  it('done이고 댓글이 늘었으면 stale', () => {
    expect(cardState('done', 1087, 1000)).toBe('stale')
  })
  it('done이지만 현재 댓글 수를 모르면 done', () => {
    expect(cardState('done', null, 1000)).toBe('done')
  })
})
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd frontend && npx vitest run src/components/CommentAnalysis.logic.test.ts`
Expected: FAIL — `Failed to resolve import "./CommentAnalysis.logic"`

- [ ] **Step 3: 순수 로직 구현**

`frontend/src/components/CommentAnalysis.logic.ts`:

```typescript
// 댓글 분석 화면의 순수 계산. 백엔드 quota_service.credits_for와 같은 규칙을 따른다.

export const CREDIT_UNIT_COMMENTS = 1000
export const LIMIT_OPTIONS = [500, 1000, 2000, 5000]

export type CreditState = { unlimited: boolean; perAnalysisMax: number }

/** 가중 크레딧: 1회 = 댓글 1,000건. 하한 1회. */
export function creditCost(limit: number): number {
  return Math.max(1, Math.ceil(Math.max(0, limit) / CREDIT_UNIT_COMMENTS))
}

/** 드롭다운 항목 활성 여부. 관리자(unlimited)는 전부 열린다. */
export function optionEnabled(limit: number, state: CreditState): boolean {
  return state.unlimited || limit <= state.perAnalysisMax
}

/**
 * 신선도 판정. 현재 댓글 수(videos.comment_count)나 분석 시점 총수가 없으면
 * 판단하지 않는다 — 기존 영상은 comment_count가 아직 NULL일 수 있다.
 */
export function freshness(
  currentCount: number | null | undefined,
  analyzedTotal: number | null | undefined,
): { stale: boolean; added: number } {
  if (currentCount == null || analyzedTotal == null) return { stale: false, added: 0 }
  const added = currentCount - analyzedTotal
  return added > 0 ? { stale: true, added } : { stale: false, added: 0 }
}

export type CardState = 'none' | 'running' | 'done' | 'stale' | 'failed'

/**
 * 카드가 보여줄 상태 하나로 접는다. 이 저장소의 프론트 테스트 컨벤션대로
 * 렌더링 대신 이 순수 함수로 분기를 검증한다(@testing-library 미설치).
 */
export function cardState(
  status: string | null | undefined,
  currentCount: number | null | undefined,
  analyzedTotal: number | null | undefined,
): CardState {
  if (!status) return 'none'
  if (status === 'pending' || status === 'running') return 'running'
  if (status === 'failed') return 'failed'
  if (status === 'done') {
    return freshness(currentCount, analyzedTotal).stale ? 'stale' : 'done'
  }
  return 'none'
}

/** 비율 막대용 백분율. 합이 0이면 전부 0. */
export function ratioPercents(
  positive: number | null,
  negative: number | null,
  neutral: number | null,
): { positive: number; negative: number; neutral: number } {
  const p = positive ?? 0
  const n = negative ?? 0
  const u = neutral ?? 0
  const total = p + n + u
  if (total === 0) return { positive: 0, negative: 0, neutral: 0 }
  return {
    positive: Math.round((p / total) * 100),
    negative: Math.round((n / total) * 100),
    neutral: Math.round((u / total) * 100),
  }
}
```

- [ ] **Step 4: 타입과 API 클라이언트**

`frontend/src/api/types.ts`에 추가:

```typescript
export type CommentCategory = {
  summary: string
  key_points: string[]
  insights: string
  top_comments: { author: string; text: string; like_count: number; published_at: string }[]
}

export type CommentAnalysisResult = {
  categories: { positive: CommentCategory; negative: CommentCategory; neutral: CommentCategory }
  order: string
  batch_failures: number
}

export type CommentAnalysisOut = {
  video_pk: number
  status: 'pending' | 'running' | 'done' | 'failed'
  requested_limit: number
  fetched_count: number | null
  total_count: number | null
  current_comment_count: number | null
  positive_count: number | null
  negative_count: number | null
  neutral_count: number | null
  result: CommentAnalysisResult | null
  model: string | null
  partial: boolean
  error: string | null
  analyzed_at: string | null
}

export type CommentCredits = {
  used: number
  limit: number
  unlimited: boolean
  per_analysis_max: number
}

export type StartCommentAnalysisResponse = {
  video_pk: number
  status: string
  queued: boolean
}

export type CommentAnalysisListItem = {
  video_pk: number
  video_id: string
  title: string
  thumbnail_url: string | null
  status: string
  positive_count: number | null
  negative_count: number | null
  neutral_count: number | null
  analyzed_at: string | null
}
```

`frontend/src/api/comments.ts`:

```typescript
import { groupClient, client } from './http'
import type {
  CommentAnalysisListItem,
  CommentAnalysisOut,
  CommentCredits,
  StartCommentAnalysisResponse,
} from './types'

export const commentApi = (slug: string) => ({
  get: (videoPk: number) =>
    groupClient(slug).get<CommentAnalysisOut>(`/videos/${videoPk}/comment-analysis`),
  list: (n = 20) =>
    groupClient(slug).get<CommentAnalysisListItem[]>(`/comment-analyses?limit=${n}`),
  start: (videoPk: number, limit: number) =>
    groupClient(slug).post<StartCommentAnalysisResponse>(
      `/videos/${videoPk}/comment-analysis`,
      { limit },
    ),
  startByUrl: (video_url: string, limit: number) =>
    groupClient(slug).post<StartCommentAnalysisResponse>('/comment-analysis/by-url', {
      video_url,
      limit,
    }),
})

export const creditsApi = {
  get: () => client.get<CommentCredits>('/api/me/comment-credits'),
}
```

`frontend/src/api/http.ts`에서 export되는 실제 이름(`client` / `groupClient`)을 먼저 확인하고, 다르면 위 임포트를 그에 맞춘다.

Run: `cd frontend && grep -n "^export" src/api/http.ts`

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd frontend && npx vitest run src/components/CommentAnalysis.logic.test.ts`
Expected: PASS (17 passed)

- [ ] **Step 6: 타입 체크와 커밋**

Run: `cd frontend && npx tsc --noEmit`
Expected: 오류 없음

```bash
git add frontend/src/components/CommentAnalysis.logic.ts frontend/src/components/CommentAnalysis.logic.test.ts frontend/src/api/comments.ts frontend/src/api/types.ts
git commit -m "feat: 댓글 분석 프론트 순수 로직 + API 클라이언트"
```

---

## Task 11: VideoDetail 카드

**Files:**
- Create: `frontend/src/components/CommentAnalysisCard.tsx`
- Modify: `frontend/src/pages/VideoDetail.tsx`

- [ ] **Step 1: 카드 컴포넌트 작성**

`frontend/src/components/CommentAnalysisCard.tsx`:

```tsx
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { commentApi, creditsApi } from '../api/comments'
import type { CommentAnalysisOut, CommentCredits } from '../api/types'
import {
  LIMIT_OPTIONS,
  cardState,
  creditCost,
  freshness,
  optionEnabled,
  ratioPercents,
} from './CommentAnalysis.logic'

type Props = { slug: string; videoPk: number }

export default function CommentAnalysisCard({ slug, videoPk }: Props) {
  const [data, setData] = useState<CommentAnalysisOut | null>(null)
  const [credits, setCredits] = useState<CommentCredits | null>(null)
  const [limit, setLimit] = useState(1000)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPoll = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  const load = async () => {
    try {
      setData(await commentApi(slug).get(videoPk))
    } catch {
      setData(null) // 404 = 아직 분석 없음
    }
  }

  useEffect(() => {
    load()
    creditsApi.get().then(setCredits).catch(() => {})
    return () => stopPoll()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug, videoPk])

  // 실행 중이면 3초 폴링, 10분 타임아웃(상한 5,000건이 2~4분 걸린다).
  useEffect(() => {
    if (data?.status !== 'running' && data?.status !== 'pending') {
      stopPoll()
      return
    }
    if (pollRef.current) return
    pollRef.current = setInterval(load, 3000)
    const timer = setTimeout(stopPoll, 10 * 60 * 1000)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.status])

  const start = async () => {
    setBusy(true)
    setErr(null)
    try {
      await commentApi(slug).start(videoPk, limit)
      await load()
      creditsApi.get().then(setCredits).catch(() => {})
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const state = credits
    ? { unlimited: credits.unlimited, perAnalysisMax: credits.per_analysis_max }
    : { unlimited: false, perAnalysisMax: 1000 }
  const cost = creditCost(limit)
  const remaining = credits ? Math.max(0, credits.limit - credits.used) : 0
  const fresh = freshness(data?.current_comment_count, data?.total_count)
  const st = cardState(data?.status, data?.current_comment_count, data?.total_count)
  const pct = ratioPercents(
    data?.positive_count ?? null,
    data?.negative_count ?? null,
    data?.neutral_count ?? null,
  )

  const running = st === 'running'
  const analyzed = st === 'done' || st === 'stale'

  return (
    <div className="bg-white rounded-xl shadow-sm p-6 space-y-3">
      <h2 className="font-semibold text-gray-800">💬 댓글 반응</h2>
      {err && <p className="text-sm text-red-600">{err}</p>}

      {running && (
        <p className="text-sm text-gray-500">분석 중입니다… 완료되면 자동으로 갱신됩니다.</p>
      )}

      {st === 'failed' && (
        <div className="text-sm text-red-600 space-y-1">
          <p>{data.error || '분석에 실패했습니다.'}</p>
          <p className="text-xs text-gray-500">크레딧은 차감되지 않았습니다.</p>
        </div>
      )}

      {analyzed && (
        <div className="space-y-2">
          <div className="flex h-3 rounded-full overflow-hidden bg-gray-100">
            <div className="bg-emerald-500" style={{ width: `${pct.positive}%` }} />
            <div className="bg-rose-500" style={{ width: `${pct.negative}%` }} />
            <div className="bg-gray-400" style={{ width: `${pct.neutral}%` }} />
          </div>
          <div className="flex gap-3 text-xs text-gray-600">
            <span>긍정 {data.positive_count ?? 0}건 ({pct.positive}%)</span>
            <span>부정 {data.negative_count ?? 0}건 ({pct.negative}%)</span>
            <span>중립 {data.neutral_count ?? 0}건 ({pct.neutral}%)</span>
          </div>
          {data.partial && (
            <p className="text-xs text-amber-600">일부 댓글은 분류에 실패해 중립으로 처리했습니다.</p>
          )}
          {fresh.stale && (
            <p className="text-xs text-amber-600">
              분석 이후 댓글 {fresh.added.toLocaleString()}건이 추가됐습니다. 다시 분석할 수 있습니다.
            </p>
          )}
          <Link
            to={`/g/${slug}/videos/${videoPk}/comments`}
            className="inline-block text-sm text-blue-600 hover:underline"
          >
            자세히 보기 →
          </Link>
        </div>
      )}

      {!running && (
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <select
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            className="border border-gray-300 rounded-lg px-2 py-1.5 text-sm"
          >
            {LIMIT_OPTIONS.map((n) => (
              <option key={n} value={n} disabled={!optionEnabled(n, state)}>
                {n.toLocaleString()}건{optionEnabled(n, state) ? '' : ' (플랜 상한 초과)'}
              </option>
            ))}
          </select>
          <button
            onClick={start}
            disabled={busy || !optionEnabled(limit, state)}
            className="px-3 py-1.5 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-60"
          >
            {analyzed ? '다시 분석' : '분석하기'}
          </button>
          {credits && !credits.unlimited && (
            <span className="text-xs text-gray-500">
              {cost}회 차감 · 잔여 {remaining} → {Math.max(0, remaining - cost)}회
            </span>
          )}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: VideoDetail에 삽입**

`frontend/src/pages/VideoDetail.tsx` 상단 임포트에 추가:

```tsx
import CommentAnalysisCard from '../components/CommentAnalysisCard'
```

`분석 정보` 섹션(약 383행의 `<h2 className="font-semibold text-gray-800">분석 정보</h2>`가 속한 카드) **다음**에 카드를 넣는다. 기존 섹션 순서는 그대로 두고 맨 아래에 붙인다:

```tsx
        <CommentAnalysisCard slug={activeSlug} videoPk={video.video_pk} />
```

`activeSlug`가 이 컴포넌트에 없으면 `const { activeSlug } = useGroup()`으로 가져온다(파일 상단에 `import { useGroup } from '../group/useGroup'`).

- [ ] **Step 3: 타입 체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 오류 없음

- [ ] **Step 4: 기존 프론트 테스트 회귀**

Run: `cd frontend && npm test`
Expected: 전체 통과

카드의 상태 분기는 Task 10의 `cardState` 테스트가 이미 5종을 모두 덮는다. 이 저장소에는 `@testing-library/react`가 없고 기존 컴포넌트 테스트(`ProfileCard.test.tsx` 등)도 전부 순수 함수만 검증하므로, 렌더링 테스트를 위해 의존성을 추가하지 않는다.

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/components/CommentAnalysisCard.tsx frontend/src/pages/VideoDetail.tsx
git commit -m "feat: 영상 상세에 댓글 반응 요약 카드"
```

---

## Task 12: 결과 상세 페이지와 진입 페이지

**Files:**
- Create: `frontend/src/pages/VideoComments.tsx`, `frontend/src/pages/CommentAnalysis.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/components/Layout.tsx`

- [ ] **Step 1: 결과 상세 페이지**

`frontend/src/pages/VideoComments.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { commentApi } from '../api/comments'
import type { CommentAnalysisOut, CommentCategory } from '../api/types'
import { useGroup } from '../group/useGroup'
import { ratioPercents } from '../components/CommentAnalysis.logic'

const TABS: { key: 'positive' | 'negative' | 'neutral'; label: string; color: string }[] = [
  { key: 'positive', label: '긍정', color: 'bg-emerald-500' },
  { key: 'negative', label: '부정', color: 'bg-rose-500' },
  { key: 'neutral', label: '중립', color: 'bg-gray-400' },
]

function CategoryPanel({ data }: { data: CommentCategory }) {
  return (
    <div className="space-y-4">
      {data.summary && <p className="text-sm text-gray-700">{data.summary}</p>}
      {data.key_points.length > 0 && (
        <ul className="list-disc pl-5 space-y-1 text-sm text-gray-700">
          {data.key_points.map((p, i) => (
            <li key={i}>{p}</li>
          ))}
        </ul>
      )}
      {data.insights && (
        <div className="bg-blue-50 rounded-lg p-3 text-sm text-gray-700">{data.insights}</div>
      )}
      <div className="space-y-2">
        {data.top_comments.map((c, i) => (
          <div key={i} className="border border-gray-200 rounded-lg p-3">
            <div className="flex justify-between text-xs text-gray-500">
              <span>{c.author}</span>
              <span>👍 {c.like_count}</span>
            </div>
            <p className="mt-1 text-sm text-gray-800 whitespace-pre-wrap">{c.text}</p>
          </div>
        ))}
        {data.top_comments.length === 0 && (
          <p className="text-sm text-gray-400">해당 카테고리의 댓글이 없습니다.</p>
        )}
      </div>
    </div>
  )
}

export default function VideoComments() {
  const { activeSlug } = useGroup()
  const { videoPk } = useParams()
  const pk = Number(videoPk)
  const [data, setData] = useState<CommentAnalysisOut | null>(null)
  const [tab, setTab] = useState<'positive' | 'negative' | 'neutral'>('positive')
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    commentApi(activeSlug)
      .get(pk)
      .then(setData)
      .catch((e) => setErr((e as Error).message))
  }, [activeSlug, pk])

  if (err) return <p className="text-sm text-red-600">{err}</p>
  if (!data) return <p className="text-sm text-gray-500">불러오는 중…</p>
  if (data.status !== 'done' || !data.result)
    return <p className="text-sm text-gray-500">아직 분석 결과가 없습니다.</p>

  const pct = ratioPercents(data.positive_count, data.negative_count, data.neutral_count)
  const counts = {
    positive: data.positive_count ?? 0,
    negative: data.negative_count ?? 0,
    neutral: data.neutral_count ?? 0,
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">댓글 반응</h1>
        <Link to={`/g/${activeSlug}/videos/${pk}`} className="text-sm text-blue-600 hover:underline">
          ← 영상 상세
        </Link>
      </div>

      <div className="bg-white rounded-xl shadow-sm p-6 space-y-3">
        <div className="flex h-3 rounded-full overflow-hidden bg-gray-100">
          <div className="bg-emerald-500" style={{ width: `${pct.positive}%` }} />
          <div className="bg-rose-500" style={{ width: `${pct.negative}%` }} />
          <div className="bg-gray-400" style={{ width: `${pct.neutral}%` }} />
        </div>
        <p className="text-xs text-gray-500">
          전체 {(data.total_count ?? data.fetched_count ?? 0).toLocaleString()}건 중 상위{' '}
          {(data.fetched_count ?? 0).toLocaleString()}건 분석
          {data.model ? ` · ${data.model}` : ''}
        </p>
        {data.partial && (
          <p className="text-xs text-amber-600">일부 댓글은 분류에 실패해 중립으로 처리했습니다.</p>
        )}
      </div>

      <div className="bg-white rounded-xl shadow-sm p-6 space-y-4">
        <div className="flex gap-2 border-b border-gray-200">
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`px-3 py-2 text-sm border-b-2 -mb-px ${
                tab === t.key
                  ? 'border-blue-600 text-blue-600 font-medium'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              {t.label} {counts[t.key]}
            </button>
          ))}
        </div>
        <CategoryPanel data={data.result.categories[tab]} />
      </div>
    </div>
  )
}
```

- [ ] **Step 2: URL 입력 진입 페이지**

`frontend/src/pages/CommentAnalysis.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { commentApi, creditsApi } from '../api/comments'
import type { CommentAnalysisListItem, CommentCredits } from '../api/types'
import { useGroup } from '../group/useGroup'
import { LIMIT_OPTIONS, creditCost, optionEnabled } from '../components/CommentAnalysis.logic'

export default function CommentAnalysis() {
  const { activeSlug } = useGroup()
  const navigate = useNavigate()
  const [url, setUrl] = useState('')
  const [limit, setLimit] = useState(1000)
  const [credits, setCredits] = useState<CommentCredits | null>(null)
  const [recent, setRecent] = useState<CommentAnalysisListItem[]>([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    creditsApi.get().then(setCredits).catch(() => {})
    commentApi(activeSlug).list().then(setRecent).catch(() => {})
  }, [activeSlug])

  const state = credits
    ? { unlimited: credits.unlimited, perAnalysisMax: credits.per_analysis_max }
    : { unlimited: false, perAnalysisMax: 1000 }
  const cost = creditCost(limit)
  const remaining = credits ? Math.max(0, credits.limit - credits.used) : 0

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!url.trim()) return
    setBusy(true)
    setErr(null)
    try {
      const res = await commentApi(activeSlug).startByUrl(url.trim(), limit)
      navigate(`/g/${activeSlug}/videos/${res.video_pk}`)
    } catch (e2) {
      setErr((e2 as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">댓글 반응</h1>
        <p className="mt-1 text-sm text-gray-500">
          YouTube URL을 입력하면 댓글을 긍정·부정·중립으로 분류하고 인사이트를 뽑습니다.
        </p>
      </div>

      {credits && !credits.unlimited && (
        <div className="bg-white rounded-xl shadow-sm p-4 text-sm text-gray-700">
          이번 달 <b>{credits.used}</b> / {credits.limit}회 사용 · 잔여 <b>{remaining}</b>회
          <span className="block text-xs text-gray-400 mt-1">KST 월초에 초기화됩니다.</span>
        </div>
      )}

      <form onSubmit={submit} className="bg-white rounded-xl shadow-sm p-6 space-y-4">
        {err && <p className="text-sm text-red-600">{err}</p>}
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://www.youtube.com/watch?v=..."
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
        />
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            className="border border-gray-300 rounded-lg px-2 py-2 text-sm"
          >
            {LIMIT_OPTIONS.map((n) => (
              <option key={n} value={n} disabled={!optionEnabled(n, state)}>
                {n.toLocaleString()}건{optionEnabled(n, state) ? '' : ' (플랜 상한 초과)'}
              </option>
            ))}
          </select>
          <button
            type="submit"
            disabled={busy || !url.trim() || !optionEnabled(limit, state)}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-60"
          >
            {busy ? '시작 중…' : '분석하기'}
          </button>
          {credits && !credits.unlimited && (
            <span className="text-xs text-gray-500">
              {cost}회 차감 · 잔여 {remaining} → {Math.max(0, remaining - cost)}회
            </span>
          )}
        </div>
      </form>

      {recent.length > 0 && (
        <div className="bg-white rounded-xl shadow-sm p-6 space-y-2">
          <h2 className="font-semibold text-gray-800">최근 분석</h2>
          {recent.map((r) => (
            <Link
              key={r.video_pk}
              to={`/g/${activeSlug}/videos/${r.video_pk}/comments`}
              className="flex items-center gap-3 py-2 hover:bg-gray-50 rounded-lg px-2"
            >
              {r.thumbnail_url && (
                <img src={r.thumbnail_url} alt="" className="w-16 h-9 object-cover rounded" />
              )}
              <span className="flex-1 min-w-0 text-sm text-gray-800 truncate">{r.title}</span>
              <span className="text-xs text-gray-500 whitespace-nowrap">
                {r.status === 'done'
                  ? `긍 ${r.positive_count ?? 0} · 부 ${r.negative_count ?? 0}`
                  : r.status}
              </span>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3: 라우트 등록**

`frontend/src/App.tsx` 임포트에 추가:

```tsx
import CommentAnalysis from './pages/CommentAnalysis'
import VideoComments from './pages/VideoComments'
```

`<Route path="videos/:videoPk" element={<VideoDetail />} />` 아래에 두 줄을 추가:

```tsx
          <Route path="videos/:videoPk/comments" element={<VideoComments />} />
          <Route path="comment-analysis" element={<CommentAnalysis />} />
```

- [ ] **Step 4: 사이드바 메뉴 추가**

`frontend/src/components/Layout.tsx`의 `NAV` 배열에서 `instant-analyze` 항목 **아래**에 추가한다(`adminOnly` 없음 — 모든 사용자에게 노출):

```tsx
  { sub: 'comment-analysis', label: '댓글 반응', icon: '💬' },
```

- [ ] **Step 5: 타입 체크와 테스트**

Run: `cd frontend && npx tsc --noEmit && npm test`
Expected: 타입 오류 없음, 전체 테스트 통과

- [ ] **Step 6: 빌드 확인**

Run: `cd frontend && npm run build`
Expected: 빌드 성공

- [ ] **Step 7: 커밋**

```bash
git add frontend/src/pages/VideoComments.tsx frontend/src/pages/CommentAnalysis.tsx frontend/src/App.tsx frontend/src/components/Layout.tsx
git commit -m "feat: 댓글 반응 결과 페이지 + URL 입력 진입 페이지"
```

---

## Task 13: 통합 검증

DB와 실제 게이트웨이가 필요한 지점을 수동으로 확인한다.

**Files:** 없음 (검증만)

- [ ] **Step 1: 전체 테스트**

Run: `python -m pytest tests/ -q`
Expected: 전체 통과

Run: `cd frontend && npm test`
Expected: 전체 통과

- [ ] **Step 2: 앱 기동과 스키마 적용**

Run: `uvicorn app.main:app --reload`

부팅 로그에 오류가 없어야 한다. `ensure_control_schema()`가 `comment_analysis_runs` 테이블과 `plans`/`user_limits` 컬럼을 만든다.

- [ ] **Step 3: 기존 그룹 스키마 마이그레이션**

관리자 UI → **관리자** → **도구** 탭에서 전 스키마 마이그레이션을 실행한다.
기존 그룹에 `comment_analyses` 테이블과 `videos.comment_count` 컬럼이 생겨야 한다.

확인 쿼리:

```sql
SELECT table_schema FROM information_schema.tables WHERE table_name = 'comment_analyses';
SELECT table_schema FROM information_schema.columns
WHERE table_name = 'videos' AND column_name = 'comment_count';
```

- [ ] **Step 4: 관리자 계정으로 E2E**

1. 사이드바 **💬 댓글 반응** → URL 입력 → 1,000건 → 분석하기
2. 영상 상세로 이동 → 하단 카드가 "분석 중"을 표시
3. 완료 후 비율 막대가 나타나고 **자세히 보기** → 3탭과 대표 댓글이 보임
4. 관리자는 차감 안내가 뜨지 않고 5,000건 옵션이 활성

- [ ] **Step 5: 일반 사용자 계정으로 쿼터 검증**

1. 일반 사용자로 로그인 → 진입 페이지에 `이번 달 0 / 5회 사용`이 보임
2. 2,000건 선택 시 드롭다운이 비활성(기본 플랜 상한 1,000건)
3. 1,000건으로 분석 → 잔여가 4회로 줄어듦
4. 관리자 화면에서 해당 사용자의 `user_limits.max_comments_per_analysis`를 5000으로 올린 뒤, 2,000건 옵션이 열리고 차감 안내가 `2회 차감`으로 바뀌는지 확인

- [ ] **Step 6: 오류 경로 확인**

1. 댓글이 비활성화된 영상 URL로 분석 → `이 영상은 댓글이 비활성화되어 있습니다` + **크레딧 미차감** 확인:

```sql
SELECT COUNT(*) FROM app.comment_analysis_runs WHERE user_id = <id>;
```

2. 같은 영상에 분석이 진행 중일 때 다시 누르면 409

- [ ] **Step 7: 사용량 원장 확인**

```sql
SELECT purpose, model, input_tokens, output_tokens, cost_usd
FROM app.ai_usage WHERE purpose = 'comment_analysis' ORDER BY created_at DESC LIMIT 5;
```

`cost_usd`가 NULL이면 단가표에 해당 모델 prefix가 없는 것이다 — 관리자 전역 설정의 모델 단가표를 확인한다(원장 모델명과 단가표 키가 정확히 맞아야 한다).

- [ ] **Step 8: YouTube 유닛 원장 확인**

```sql
SELECT usage_date, key_fp, units FROM app.yt_quota_usage ORDER BY usage_date DESC LIMIT 3;
```

1,000건 분석 후 units가 10 증가해야 한다.

- [ ] **Step 9: 최종 커밋**

```bash
git add -A
git commit -m "test: 댓글 반응 분석 통합 검증"
```

---

## 완료 기준

- [ ] `python -m pytest tests/ -q` 전체 통과
- [ ] `cd frontend && npm test` 전체 통과
- [ ] `cd frontend && npm run build` 성공
- [ ] 관리자가 수량 제한 없이 분석 가능
- [ ] 일반 사용자가 월 5회 가중 크레딧으로 제한됨
- [ ] 댓글 비활성화·LLM 실패 시 크레딧이 차감되지 않음
- [ ] 기존 사이드바 항목·영상 분석·스케줄러·알림 동작 무변경
