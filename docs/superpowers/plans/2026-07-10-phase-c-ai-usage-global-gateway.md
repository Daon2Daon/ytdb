# Phase C: AI 사용량 원장·전역 게이트웨이 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** LLM 호출의 토큰·비용을 `app.ai_usage` 원장에 기록하고(시스템 몫 규칙), AI 게이트웨이 설정을 전역화하며, §3.3 설정 권한 분리·월 예산 강제·사용량 대시보드를 구현한다.

**Architecture:** `ai_usage_service`(신규)가 원장 기록·단가 환산·월 예산 검사를 단일 소유(quota_service 패턴). LLM 클라이언트는 usage **추출만**(순수 유지), 기록은 호출부 3곳에서 명시적·best-effort. 게이트웨이 해석은 그룹 명시값→전역→코드 기본값(`resolve_ai_gateway`). 예산은 "사용자 귀속 비용 발생 행위"만 차단(설계 §7 편차, 승인됨).

**Tech Stack:** FastAPI + SQLAlchemy async(제어 평면 `app` 스키마), pytest(asyncio auto), React+TS(vite).

**설계 문서:** `docs/superpowers/specs/2026-07-10-phase-c-ai-usage-global-gateway-design.md`

**중요 배경 (엔지니어 필독):**
- 제어 평면 모델은 `app/models/control/`, Base는 `app/control_db.py`. 테이블 생성은 부팅 시 `ensure_control_schema()`의 `create_all`(멱등). ORM 기본값은 반드시 `server_default`(raw insert가 ORM `default=` 무시 — B-0b 실버그).
- 전체 테스트: `.venv_e2e/bin/python -m pytest tests/ -q` (main repo `.venv`는 깨져 있음 — 건드리지 말 것). 기준 244 passed.
- 라우터 테스트는 `app.dependency_overrides` + `monkeypatch` 패턴(tests/test_quota_enforcement.py 참고). DB 불필요 단위 테스트가 기본.
- `postgres-ytdb` MCP는 프로덕션 — 절대 사용 금지. 실 DB 검증은 별도 E2E 체크포인트(말미).
- 기록/게이트 대상 경로: `_run_analysis` 직접 분기(monitor_service.py:627 인근 "기존 경로" 주석), `_run_analysis_cached` claimed 분기(:765 인근), `digest_service.synthesize_with_llm`(:377).

---

### Task 1: usage 추출 — llm_client (AnalyzerResult/ChatResult 확장)

**Files:**
- Modify: `app/services/llm_client.py`
- Test: `tests/test_llm_usage_extract.py` (신규)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_llm_usage_extract.py`:

```python
"""LLM 응답 usage 추출 단위 테스트 (DB·네트워크 불필요)."""

from app.services.llm_client import (
    AnalyzerResult,
    ChatResult,
    _extract_chat_usage,
    _extract_gemini_usage,
)


def test_extract_gemini_usage_basic():
    payload = {"usageMetadata": {"promptTokenCount": 1200, "candidatesTokenCount": 340}}
    assert _extract_gemini_usage(payload) == (1200, 340)


def test_extract_gemini_usage_with_thoughts():
    # thinking 모델: 출력 = candidates + thoughts (과금 기준)
    payload = {"usageMetadata": {
        "promptTokenCount": 100, "candidatesTokenCount": 40, "thoughtsTokenCount": 60,
    }}
    assert _extract_gemini_usage(payload) == (100, 100)


def test_extract_gemini_usage_missing():
    assert _extract_gemini_usage({}) == (None, None)
    assert _extract_gemini_usage({"usageMetadata": {}}) == (None, None)
    assert _extract_gemini_usage({"usageMetadata": "broken"}) == (None, None)


def test_extract_chat_usage():
    payload = {"usage": {"prompt_tokens": 500, "completion_tokens": 200}}
    assert _extract_chat_usage(payload) == (500, 200)
    assert _extract_chat_usage({}) == (None, None)
    assert _extract_chat_usage({"usage": None}) == (None, None)


def test_result_dataclasses_have_usage_fields():
    a = AnalyzerResult(data={}, raw_text="")
    assert a.input_tokens is None and a.output_tokens is None
    c = ChatResult(content="", raw={})
    assert c.input_tokens is None and c.output_tokens is None
```

- [ ] **Step 2: 실패 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_llm_usage_extract.py -q`
Expected: FAIL — `_extract_gemini_usage` import 불가

- [ ] **Step 3: 구현**

`app/services/llm_client.py`의 `_pick_text_from_gemini` 아래에 추가:

```python
def _extract_gemini_usage(payload: Dict[str, Any]) -> tuple[int | None, int | None]:
    """Gemini native 응답의 usageMetadata → (input, output). 실패 시 (None, None).

    output은 candidatesTokenCount + thoughtsTokenCount(thinking 모델) 합 — 과금 기준.
    """
    try:
        um = payload.get("usageMetadata") or {}
        inp = um.get("promptTokenCount")
        out = um.get("candidatesTokenCount")
        if out is not None:
            out = int(out) + int(um.get("thoughtsTokenCount") or 0)
        return (int(inp) if inp is not None else None, out)
    except Exception:
        return (None, None)


def _extract_chat_usage(payload: Dict[str, Any]) -> tuple[int | None, int | None]:
    """OpenAI 호환 응답의 usage → (input, output). 실패 시 (None, None)."""
    try:
        u = payload.get("usage") or {}
        inp = u.get("prompt_tokens")
        out = u.get("completion_tokens")
        return (
            int(inp) if inp is not None else None,
            int(out) if out is not None else None,
        )
    except Exception:
        return (None, None)
```

두 dataclass에 필드 추가:

```python
@dataclass(frozen=True)
class AnalyzerResult:
    data: Dict[str, Any]
    raw_text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class ChatResult:
    content: str
    raw: Dict[str, Any]
    input_tokens: int | None = None
    output_tokens: int | None = None
```

`analyze_video_native`의 성공 경로(155행 인근)를 교체 — `resp.json()`을 변수로 잡아 usage 추출:

```python
        payload = resp.json()
        raw_text = _pick_text_from_gemini(payload)
        if not raw_text:
            raise LiteLLMError("Gemini 응답에서 텍스트를 찾지 못했습니다.")
        in_tok, out_tok = _extract_gemini_usage(payload)
        try:
            return AnalyzerResult(
                data=json.loads(_strip_code_fence(raw_text)),
                raw_text=raw_text,
                input_tokens=in_tok,
                output_tokens=out_tok,
            )
        except Exception as e:
            raise LiteLLMError(f"Gemini 구조화 출력 JSON 파싱 실패: {e}") from e
```

`chat`의 반환부(193행)를 교체:

```python
        in_tok, out_tok = _extract_chat_usage(payload)
        return ChatResult(
            content=content or "", raw=payload,
            input_tokens=in_tok, output_tokens=out_tok,
        )
```

- [ ] **Step 4: 통과 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_llm_usage_extract.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/llm_client.py tests/test_llm_usage_extract.py
git commit -m "feat: LLM 응답 usage 추출 — AnalyzerResult/ChatResult에 토큰 필드 (기록은 안 함)"
```

---

### Task 2: AIUsage 모델 + AnalysisPipelineResult 토큰 전파

**Files:**
- Create: `app/models/control/ai_usage.py`
- Modify: `app/control_db.py` (모델 임포트 목록)
- Modify: `app/services/analyzer.py:110-116` (AnalysisPipelineResult), `:297-328` (run)
- Test: `tests/test_control_models.py` (추가)

- [ ] **Step 1: 실패하는 테스트 작성 (tests/test_control_models.py 끝에 추가)**

```python
def test_ai_usage_model_columns():
    from app.models.control.ai_usage import AIUsage

    cols = {c.name for c in AIUsage.__table__.columns}
    assert cols == {
        "usage_id", "user_id", "group_id", "purpose", "model",
        "input_tokens", "output_tokens", "cost_usd", "video_pk", "created_at",
    }
    # user_id NULL = 시스템 몫(공유 캐시 분석). group_id는 FK 없음(원장 보존).
    assert AIUsage.__table__.columns["user_id"].nullable is True
    assert AIUsage.__table__.columns["group_id"].nullable is True
    assert AIUsage.__table__.columns["cost_usd"].nullable is True
    fk_cols = {fk.parent.name for fk in AIUsage.__table__.foreign_keys}
    assert fk_cols == {"user_id"}
```

- [ ] **Step 2: 실패 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_control_models.py -q`
Expected: 신규 1건 FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 모델 작성**

`app/models/control/ai_usage.py`:

```python
"""app.ai_usage — AI 사용량 원장 (스펙 §2.4). "실제 LLM API 지불 금액"의 신뢰원.

user_id NULL = 시스템 몫(공유 캐시 분석 — 최초 트리거 사용자 귀속은 복불복 과금이라 금지).
사용자별 건수 카운트는 analysis_deliveries가 담당 — 역할 분리.
group_id는 FK 없음: 그룹 삭제 후에도 원장 보존.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, Numeric, Text, func, text

from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class AIUsage(Base):
    __tablename__ = "ai_usage"
    __table_args__ = (
        Index("ai_usage_user_created", "user_id", "created_at"),
        {"schema": APP_SCHEMA},
    )

    usage_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{APP_SCHEMA}.users.user_id"), nullable=True
    )
    group_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)  # 'analysis' | 'digest'
    model: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    video_pk: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

- [ ] **Step 4: control_db 임포트 추가**

`app/control_db.py`의 `ensure_control_schema` 내 모델 임포트 목록 맨 앞에 `ai_usage` 추가
(알파벳 순 유지):

```python
    from app.models.control import (  # noqa: F401
        ai_usage,
        analysis_cache,
        ...
    )
```

- [ ] **Step 5: AnalysisPipelineResult 토큰 전파**

`app/services/analyzer.py:110` dataclass에 필드 추가:

```python
@dataclass(frozen=True)
class AnalysisPipelineResult:
    data: Dict[str, Any]
    route: str
    model_name: str
    gateway_url: str
    prompt_version: str = PROMPT_VERSION
    raw_text: str = ""
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
```

`run()`(:317 인근)의 반환을 교체:

```python
            return AnalysisPipelineResult(
                data=result.data,
                route="A",
                model_name=self._ai.primary_model,
                gateway_url=self._ai.base_url,
                raw_text=result.raw_text,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
```

(`result_from_cache`는 변경 없음 — 캐시 복사는 LLM 호출이 아니므로 토큰 None 유지.)

- [ ] **Step 6: 통과 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_control_models.py tests/test_llm_usage_extract.py -q`
Expected: all passed

- [ ] **Step 7: Commit**

```bash
git add app/models/control/ai_usage.py app/control_db.py app/services/analyzer.py tests/test_control_models.py
git commit -m "feat: app.ai_usage 원장 테이블 + 파이프라인 결과에 토큰 전파"
```

---

### Task 3: ai_usage_service 순수 함수 (월 경계·단가 환산·예외)

**Files:**
- Create: `app/services/ai_usage_service.py`
- Test: `tests/test_ai_usage_service.py` (신규)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_ai_usage_service.py`:

```python
"""ai_usage_service 순수 함수 단위 테스트 (DB 불필요)."""

from datetime import datetime, timezone
from decimal import Decimal

from app.services.ai_usage_service import (
    BudgetExceeded,
    compute_cost_usd,
    kst_month_start_utc,
)

PRICES = {
    "gemini/": {"input": 0.10, "output": 0.40},
    "gemini/gemini-2.5-flash": {"input": 0.30, "output": 2.50},
}


def test_kst_month_start_utc():
    # KST 2026-07-15 10:00 = UTC 01:00 → 7/1 00:00 KST = UTC 6/30 15:00
    now = datetime(2026, 7, 15, 1, 0, tzinfo=timezone.utc)
    assert kst_month_start_utc(now) == datetime(2026, 6, 30, 15, 0, tzinfo=timezone.utc)


def test_kst_month_start_utc_crosses_month():
    # UTC 6/30 16:00 = KST 7/1 01:00 → KST 월초는 UTC 6/30 15:00 (UTC 달과 다름)
    now = datetime(2026, 6, 30, 16, 0, tzinfo=timezone.utc)
    assert kst_month_start_utc(now) == datetime(2026, 6, 30, 15, 0, tzinfo=timezone.utc)


def test_compute_cost_longest_prefix_wins():
    # 2.5-flash는 더 긴 prefix 단가 적용: (1M×0.30 + 1M×2.50)/1M ... 토큰 1_000_000씩
    cost = compute_cost_usd("gemini/gemini-2.5-flash", 1_000_000, 1_000_000, PRICES)
    assert cost == Decimal("2.80")
    # 다른 gemini 모델은 짧은 prefix로 폴백
    cost2 = compute_cost_usd("gemini/gemini-3.1-flash-lite", 1_000_000, 1_000_000, PRICES)
    assert cost2 == Decimal("0.50")


def test_compute_cost_unknown_model_or_tokens_none():
    assert compute_cost_usd("gpt-4o", 100, 100, PRICES) is None
    assert compute_cost_usd("gemini/x", None, 100, PRICES) is None
    assert compute_cost_usd("gemini/x", 100, None, PRICES) is None
    assert compute_cost_usd("gemini/x", 100, 100, {}) is None


def test_compute_cost_malformed_price_entry():
    assert compute_cost_usd("bad/x", 100, 100, {"bad/": {"input": "oops"}}) is None


def test_budget_exceeded_detail():
    exc = BudgetExceeded("월 AI 예산 초과", limit=5.0, current=5.2)
    assert exc.limit == 5.0 and exc.current == 5.2
    assert "월 AI 예산 초과" in str(exc)
```

- [ ] **Step 2: 실패 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_ai_usage_service.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 최소 구현**

`app/services/ai_usage_service.py`:

```python
"""AI 사용량 원장 서비스 (Phase C): 기록·단가 환산·월 예산 검사의 단일 소유 지점.

귀속 원칙(스펙 §2.4): 캐시 미스 분석은 user_id=NULL(시스템 몫), 다이제스트·
직접/커스텀 프롬프트 분석은 그룹 owner 몫. 월 경계는 KST 달력 월 —
Phase B의 KST 일일 경계와 일관.
기록은 best-effort: 실패해도 분석/다이제스트를 깨뜨리지 않는다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


class BudgetExceeded(Exception):
    """월 예산 초과. 라우터는 400, 스케줄러/틱은 skip+job log로 변환한다."""

    def __init__(self, detail: str, *, limit: float, current: float) -> None:
        super().__init__(detail)
        self.detail = detail
        self.limit = limit
        self.current = current


def kst_month_start_utc(now: datetime) -> datetime:
    """now가 속한 KST 달력 월의 1일 00:00(KST)을 UTC로 반환."""
    kst_now = now.astimezone(KST)
    start = kst_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc)


def compute_cost_usd(
    model: str,
    input_tokens: Optional[int],
    output_tokens: Optional[int],
    prices: dict,
) -> Optional[Decimal]:
    """단가표(모델 prefix → {input, output} $/1M) 기반 비용. 계산 불가면 None.

    최장 prefix 매칭 — "gemini/gemini-2.5-flash"가 "gemini/"보다 우선.
    None 반환 = cost_usd NULL 기록 → 관리자 대시보드 경고로 표면화(스펙 §2.4).
    """
    if input_tokens is None or output_tokens is None or not prices:
        return None
    best = None
    for prefix in prices:
        if model.startswith(prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
    if best is None:
        return None
    entry = prices[best]
    try:
        return (
            Decimal(str(entry["input"])) * input_tokens
            + Decimal(str(entry["output"])) * output_tokens
        ) / Decimal(1_000_000)
    except (KeyError, TypeError, InvalidOperation):
        return None
```

- [ ] **Step 4: 통과 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_ai_usage_service.py -q`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/ai_usage_service.py tests/test_ai_usage_service.py
git commit -m "feat: ai_usage_service 기초 — KST 월 경계·prefix 단가 환산·BudgetExceeded"
```

---

### Task 4: EffectiveLimits 예산 필드 + DB 함수 (record/month_cost/check)

**Files:**
- Modify: `app/services/quota_service.py:31-39` (EffectiveLimits), `:75-92` (_merge_limits)
- Modify: `app/services/ai_usage_service.py` (DB 함수 추가)
- Test: `tests/test_quota_service.py`, `tests/test_ai_usage_service.py` (추가)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_quota_service.py` 끝에 추가:

```python
def test_merge_limits_includes_budget():
    from decimal import Decimal

    lim = _merge_limits(_plan(monthly_cost_budget_usd=Decimal("5")), None)
    assert lim.monthly_cost_budget_usd == 5.0

    ul = UserLimit(user_id=2, monthly_cost_budget_usd=Decimal("0"))
    lim2 = _merge_limits(_plan(monthly_cost_budget_usd=Decimal("5")), ul)
    assert lim2.monthly_cost_budget_usd == 0.0  # 오버라이드 0도 존중
```

`tests/test_ai_usage_service.py` 끝에 추가:

```python
import pytest

from app.services.ai_usage_service import check_monthly_budget, record_usage


async def test_record_usage_swallows_errors(monkeypatch):
    """원장 기록 실패는 분석을 깨뜨리지 않는다 — 예외를 삼키고 경고만."""
    from app.services import ai_usage_service as aus

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(aus, "get_sessionmaker", _boom)
    # 예외가 밖으로 새면 테스트 실패
    await record_usage(
        user_id=None, group_id=1, purpose="analysis", model="m",
        input_tokens=10, output_tokens=20,
    )


async def test_check_monthly_budget_exceeded(monkeypatch):
    from app.services import ai_usage_service as aus
    from app.services.quota_service import EffectiveLimits

    LIMITS = EffectiveLimits(
        max_groups=1, max_channels_total=5, max_analyses_per_day=10,
        max_video_minutes=60, min_poll_interval_min=60,
        plan_slug="free", plan_name="Free", has_override=False,
        monthly_cost_budget_usd=5.0,
    )

    async def _limits(session, user_id):
        return LIMITS

    async def _cost(session, user_id):
        from decimal import Decimal
        return Decimal("5.1")

    monkeypatch.setattr(aus, "effective_limits", _limits)
    monkeypatch.setattr(aus, "month_cost_usd", _cost)
    with pytest.raises(aus.BudgetExceeded) as ei:
        await check_monthly_budget(None, user_id=2)
    assert "월 AI 예산 초과" in ei.value.detail


async def test_check_monthly_budget_unlimited(monkeypatch):
    from app.services import ai_usage_service as aus

    async def _none(session, user_id):
        return None

    monkeypatch.setattr(aus, "effective_limits", _none)
    await check_monthly_budget(None, user_id=1)  # admin — 통과
```

- [ ] **Step 2: 실패 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_quota_service.py tests/test_ai_usage_service.py -q`
Expected: 신규들 FAIL — `monthly_cost_budget_usd` 필드/함수 미존재

- [ ] **Step 3: quota_service 확장**

`EffectiveLimits`(:31)에 **기본값 있는 필드로** 추가(기존 테스트 생성자 호환):

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
```

`_merge_limits`(:75) 반환에 float 처리 추가:

```python
def _merge_limits(plan: Plan, override: Optional[UserLimit]) -> EffectiveLimits:
    def pick(field: str) -> int:
        if override is not None:
            v = getattr(override, field)
            if v is not None:
                return int(v)
        return int(getattr(plan, field))

    def pick_budget() -> Optional[float]:
        if override is not None and override.monthly_cost_budget_usd is not None:
            return float(override.monthly_cost_budget_usd)
        v = plan.monthly_cost_budget_usd
        return float(v) if v is not None else None

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
    )
```

- [ ] **Step 4: ai_usage_service DB 함수 추가 (파일 끝에)**

```python
from sqlalchemy import func as sa_func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.control_db import get_sessionmaker
from app.models.control.ai_usage import AIUsage
from app.services.quota_service import effective_limits


async def record_usage(
    *,
    user_id: Optional[int],
    group_id: Optional[int],
    purpose: str,
    model: str,
    input_tokens: Optional[int],
    output_tokens: Optional[int],
    video_pk: Optional[int] = None,
) -> None:
    """원장 1행 기록 + 단가 환산. best-effort — 모든 예외를 삼킨다."""
    try:
        from app.services.global_settings import get_ai_model_prices

        prices = await get_ai_model_prices()
        cost = compute_cost_usd(model, input_tokens, output_tokens, prices)
        async with get_sessionmaker()() as session:
            async with session.begin():
                await session.execute(
                    pg_insert(AIUsage).values(
                        user_id=user_id,
                        group_id=group_id,
                        purpose=purpose,
                        model=model,
                        input_tokens=input_tokens or 0,
                        output_tokens=output_tokens or 0,
                        cost_usd=cost,
                        video_pk=video_pk,
                    )
                )
    except Exception as e:  # noqa: BLE001 — 원장 실패가 본 작업을 깨뜨리면 안 됨
        print(f"[ai_usage] 원장 기록 실패(무시): {e}")


async def month_cost_usd(session: AsyncSession, user_id: int) -> Decimal:
    """당월(KST) 본인 귀속 비용 합. cost_usd NULL 행은 0 취급."""
    since = kst_month_start_utc(datetime.now(timezone.utc))
    return (
        await session.execute(
            select(sa_func.coalesce(sa_func.sum(AIUsage.cost_usd), 0))
            .where(AIUsage.user_id == user_id, AIUsage.created_at >= since)
        )
    ).scalar_one()


async def check_monthly_budget(session: AsyncSession, user_id: int) -> None:
    """월 예산 검사. admin/예산 미설정은 통과. 초과 시 BudgetExceeded."""
    limits = await effective_limits(session, user_id)
    if limits is None or limits.monthly_cost_budget_usd is None:
        return
    current = float(await month_cost_usd(session, user_id))
    if current >= limits.monthly_cost_budget_usd:
        raise BudgetExceeded(
            f"월 AI 예산 초과: 당월 ${current:.4f} / 예산 "
            f"${limits.monthly_cost_budget_usd:.2f} (KST 월초 초기화)",
            limit=limits.monthly_cost_budget_usd,
            current=current,
        )


async def budget_ok_for_group(group) -> tuple[bool, str]:
    """스케줄러/틱용: 그룹 owner 예산 검사. (통과 여부, 초과 사유)."""
    if group.owner_user_id is None:
        return True, ""
    async with get_sessionmaker()() as session:
        try:
            await check_monthly_budget(session, group.owner_user_id)
        except BudgetExceeded as e:
            return False, e.detail
    return True, ""
```

(주의: `get_ai_model_prices`는 Task 5에서 생성 — 이 시점 테스트는 record_usage의
예외 삼킴 경로만 타므로 통과한다. import를 함수 내부에 둔 것은 순환 참조 방지 겸
Task 순서 독립성.)

- [ ] **Step 5: 통과 확인 + 리그레션**

Run: `.venv_e2e/bin/python -m pytest tests/test_quota_service.py tests/test_ai_usage_service.py tests/test_quota_enforcement.py tests/test_scheduler_quota.py -q`
Expected: all passed (기존 EffectiveLimits 생성자 호환 — 기본값 필드라 리그레션 없음)

- [ ] **Step 6: Commit**

```bash
git add app/services/quota_service.py app/services/ai_usage_service.py tests/test_quota_service.py tests/test_ai_usage_service.py
git commit -m "feat: 월 예산 유효 한도(COALESCE)·원장 기록(best-effort)·월 비용 집계·예산 검사"
```

---

### Task 5: 전역 AI 게이트웨이 — global_settings 키·resolve·부트스트랩·admin 검증

**Files:**
- Modify: `app/services/global_settings.py` (키 상수·get_ai_model_prices·resolve_ai_gateway·부트스트랩)
- Modify: `app/routers/admin.py:59` (_GLOBAL_KEYS), PUT 검증
- Test: `tests/test_global_settings.py` (추가)

- [ ] **Step 1: 실패하는 테스트 작성 (tests/test_global_settings.py 끝에 추가)**

기존 파일의 테스트 스타일(세션 fake/monkeypatch)을 확인 후 추가:

```python
def test_ai_global_keys_registered():
    from app.services.global_settings import (
        GLOBAL_AI_API_KEY,
        GLOBAL_AI_BASE_URL,
        GLOBAL_AI_DIGEST_MODEL,
        GLOBAL_AI_MODEL_PRICES,
        GLOBAL_AI_PRIMARY_MODEL,
        SECRET_KEYS,
    )

    assert GLOBAL_AI_API_KEY in SECRET_KEYS          # 암호화 저장
    assert GLOBAL_AI_MODEL_PRICES not in SECRET_KEYS # 단가표는 평문 JSON
    assert GLOBAL_AI_BASE_URL == "ai_base_url"
    assert GLOBAL_AI_PRIMARY_MODEL == "ai_primary_model"
    assert GLOBAL_AI_DIGEST_MODEL == "ai_digest_model"


async def test_resolve_ai_gateway_group_overrides_global(monkeypatch):
    """그룹 명시값 > 전역 > 코드 기본값."""
    from app.services import global_settings as gs

    async def _typed(group_id, category):
        # 그룹은 base_url만 명시
        return {"base_url": "http://group:4000", "api_key": "", "primary_model": ""}

    class _Mgr:
        get_typed = staticmethod(_typed)

    monkeypatch.setattr(gs, "get_settings_manager", lambda: _Mgr())

    async def _global(session, key):
        return {
            "ai_base_url": "http://global:4000",
            "ai_api_key": "gk",
            "ai_primary_model": "gemini/global-model",
            "ai_digest_model": "",
        }.get(key)

    monkeypatch.setattr(gs, "get_global", _global)

    class _S:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None

    monkeypatch.setattr(gs, "get_sessionmaker", lambda: (lambda: _S()))

    ai = await gs.resolve_ai_gateway(7)
    assert ai.base_url == "http://group:4000"        # 그룹 명시값 우선
    assert ai.api_key == "gk"                        # 전역 폴백
    assert ai.primary_model == "gemini/global-model" # 전역 폴백
    assert ai.digest_model == ""                     # 전역도 빈값 → 기본값(빈값)


def test_get_ai_model_prices_parsing():
    from app.services.global_settings import _parse_model_prices

    assert _parse_model_prices('{"gemini/": {"input": 0.1, "output": 0.4}}') == {
        "gemini/": {"input": 0.1, "output": 0.4}
    }
    assert _parse_model_prices("") == {}
    assert _parse_model_prices(None) == {}
    assert _parse_model_prices("not json") == {}
    assert _parse_model_prices('["list"]') == {}
```

- [ ] **Step 2: 실패 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_global_settings.py -q`
Expected: 신규 3건 FAIL

- [ ] **Step 3: global_settings.py 구현**

키 상수 블록(:26 인근)을 확장:

```python
GLOBAL_YOUTUBE_API_KEY = "youtube_api_key"
GLOBAL_CENTRAL_POLL_FLOOR_MIN = "central_poll_floor_min"
DEFAULT_CENTRAL_POLL_FLOOR_MIN = 10

# Phase C: 전역 AI 게이트웨이 (스펙 §5). tagging_model은 미사용이라 전역화 제외.
GLOBAL_AI_BASE_URL = "ai_base_url"
GLOBAL_AI_API_KEY = "ai_api_key"
GLOBAL_AI_PRIMARY_MODEL = "ai_primary_model"
GLOBAL_AI_DIGEST_MODEL = "ai_digest_model"
GLOBAL_AI_MODEL_PRICES = "ai_model_prices"  # JSON: {"모델prefix": {"input": n, "output": n}} ($/1M)

SECRET_KEYS = frozenset({GLOBAL_YOUTUBE_API_KEY, GLOBAL_AI_API_KEY})
```

파일 끝에 추가 (`json` 임포트 상단 추가):

```python
def _parse_model_prices(raw: Optional[str]) -> dict:
    """단가표 JSON 파싱. 형식 오류는 빈 dict(=단가 없음, cost NULL 경고로 표면화)."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def get_ai_model_prices() -> dict:
    async with get_sessionmaker()() as session:
        raw = await get_global(session, GLOBAL_AI_MODEL_PRICES)
    return _parse_model_prices(raw)


def _f(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


async def resolve_ai_gateway(group_id: int) -> "AIGatewaySettings":
    """유효 AI 게이트웨이 해석: 그룹 명시값 → 전역 → 코드 기본값 (스펙 §5).

    settings_manager.get_ai_gateway와 달리 raw(get_typed)로 그룹 '명시 여부'를
    판별한다 — get_ai_gateway는 기본값을 채워 반환하므로 폴백 판단이 불가능.
    """
    from app.services.settings_types import AIGatewaySettings

    d = await get_settings_manager().get_typed(group_id, "ai_gateway")
    async with get_sessionmaker()() as session:
        g_base = await get_global(session, GLOBAL_AI_BASE_URL)
        g_key = await get_global(session, GLOBAL_AI_API_KEY)
        g_primary = await get_global(session, GLOBAL_AI_PRIMARY_MODEL)
        g_digest = await get_global(session, GLOBAL_AI_DIGEST_MODEL)

    def pick(group_val, global_val, default: str) -> str:
        v = str(group_val or "").strip()
        if v:
            return v
        v = (global_val or "").strip()
        return v if v else default

    return AIGatewaySettings(
        base_url=pick(d.get("base_url"), g_base, "http://litellm:4000"),
        api_key=pick(d.get("api_key"), g_key, ""),
        primary_model=pick(d.get("primary_model"), g_primary, "gemini/gemini-2.5-flash"),
        tagging_model=str(d.get("tagging_model") or "gemini/gemini-2.5-flash"),
        digest_model=pick(d.get("digest_model"), g_digest, ""),
        temperature=_f(d.get("temperature"), 0.3),
        max_tokens=_i(d.get("max_tokens"), 8192),
        daily_budget_usd=_f(d.get("daily_budget_usd"), 2.0),
    )


async def _seed_global_ai_from_admin_groups() -> None:
    """전역 AI 게이트웨이 미시드 시 admin 그룹 설정에서 1회 시드. 멱등.

    bootstrap_global_settings의 YouTube 키 시드와 같은 철학 — 기존 단일 운영자
    배포가 업그레이드 직후 설정 변경 없이 동작. 단가표는 시드하지 않음(관리자 입력).
    """
    from app.models.control.group import Group
    from app.models.control.user import User

    sf = get_sessionmaker()
    async with sf() as session:
        if await get_global(session, GLOBAL_AI_BASE_URL) or await get_global(
            session, GLOBAL_AI_API_KEY
        ):
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
    for group in groups:
        d = await get_settings_manager().get_typed(group.group_id, "ai_gateway")
        base = str(d.get("base_url") or "").strip()
        key = str(d.get("api_key") or "").strip()
        if not (base and key):
            continue
        try:
            async with sf() as session:
                async with session.begin():
                    await set_global(session, GLOBAL_AI_BASE_URL, base)
                    await set_global(session, GLOBAL_AI_API_KEY, key)
                    primary = str(d.get("primary_model") or "").strip()
                    digest = str(d.get("digest_model") or "").strip()
                    if primary:
                        await set_global(session, GLOBAL_AI_PRIMARY_MODEL, primary)
                    if digest:
                        await set_global(session, GLOBAL_AI_DIGEST_MODEL, digest)
            print(f"[bootstrap] 전역 AI 게이트웨이를 그룹 {group.slug} 설정에서 시드했습니다.")
        except SettingsSecretError as e:
            print(f"[bootstrap] 전역 AI 키 시드 건너뜀({e}) — FERNET_KEY 설정 후 관리자 API로 등록하세요.")
        return
```

`bootstrap_global_settings()` 함수 끝에 한 줄 추가:

```python
    await _seed_global_ai_from_admin_groups()
```

(주의: 기존 함수는 YouTube 키가 이미 있으면 `return`으로 조기 종료한다 — AI 시드 호출이
그 return에 걸리지 않도록, **조기 return을 `_seed_youtube_key` 분리** 또는 함수 구조상
AI 시드 호출을 YouTube 로직보다 앞/독립 배치로 조정할 것. 가장 단순한 방법:
`bootstrap_global_settings` 본문 맨 앞에 `await _seed_global_ai_from_admin_groups()`를 두면
기존 YouTube 로직은 그대로 두어도 된다.)

- [ ] **Step 4: admin.py — _GLOBAL_KEYS 확장 + 단가표 JSON 검증**

`app/routers/admin.py:59`:

```python
_GLOBAL_KEYS = (
    GLOBAL_YOUTUBE_API_KEY,
    GLOBAL_CENTRAL_POLL_FLOOR_MIN,
    GLOBAL_AI_BASE_URL,
    GLOBAL_AI_API_KEY,
    GLOBAL_AI_PRIMARY_MODEL,
    GLOBAL_AI_DIGEST_MODEL,
    GLOBAL_AI_MODEL_PRICES,
)
```

(임포트도 `from app.services.global_settings import ...`에 5개 키 추가.)

`put_global_settings`의 `central_poll_floor_min` 검증 블록 옆에 추가:

```python
        if item.key == GLOBAL_AI_MODEL_PRICES:
            try:
                parsed = _json.loads(value)
            except ValueError:
                raise HTTPException(status_code=400, detail="ai_model_prices는 JSON이어야 합니다.")
            if not isinstance(parsed, dict):
                raise HTTPException(status_code=400, detail="ai_model_prices는 JSON 객체여야 합니다.")
```

(admin.py에 `import json as _json`이 없으면 추가. `ai_api_key`의 마스킹 라운드트립 가드는
기존 SECRET_KEYS 분기가 자동 처리 — 별도 코드 불필요.)

- [ ] **Step 5: admin 라우터 테스트 추가 (tests/test_admin_api.py 끝에)**

```python
def test_global_settings_includes_ai_keys():
    from app.routers.admin import _GLOBAL_KEYS

    assert "ai_base_url" in _GLOBAL_KEYS
    assert "ai_api_key" in _GLOBAL_KEYS
    assert "ai_model_prices" in _GLOBAL_KEYS
```

- [ ] **Step 6: 통과 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_global_settings.py tests/test_admin_api.py -q`
Expected: all passed

- [ ] **Step 7: Commit**

```bash
git add app/services/global_settings.py app/routers/admin.py tests/test_global_settings.py tests/test_admin_api.py
git commit -m "feat: 전역 AI 게이트웨이 — global_settings 키·resolve(그룹→전역→기본)·부트스트랩 시드·단가표"
```

---

### Task 6: 호출부 resolve_ai_gateway 교체 (3곳)

**Files:**
- Modify: `app/services/analyzer.py:365-392` (build_analysis_pipeline)
- Modify: `app/services/digest_service.py:377-395` (synthesize_with_llm 도입부)
- Modify: `app/services/monitor_service.py:700-706` (_run_analysis_cached 도입부)
- Test: 기존 스위트 리그레션으로 검증(치환은 동작 동등 — 전역 미설정 시 결과 동일)

- [ ] **Step 1: analyzer.build_analysis_pipeline**

`mgr = get_settings_manager()` / `ai = await mgr.get_ai_gateway(group_id)` 두 줄을 교체:

```python
    from app.services.global_settings import resolve_ai_gateway

    ai = await resolve_ai_gateway(group_id)
```

(함수 내 `mgr` 다른 용도 사용 여부 확인 — resolve_prompts는 별도 임포트라 무관.)

- [ ] **Step 2: digest_service.synthesize_with_llm**

도입부의 `ai = await mgr.get_ai_gateway(group_id)`를 교체:

```python
    from app.services.global_settings import resolve_ai_gateway

    ai = await resolve_ai_gateway(group_id)
```

- [ ] **Step 3: monitor_service._run_analysis_cached**

`ai = await mgr.get_ai_gateway(group.group_id)`(:702)를 교체:

```python
    from app.services.global_settings import resolve_ai_gateway

    ai = await resolve_ai_gateway(group.group_id)
```

**중요**: 캐시 키(`claim_or_get_cached(..., ai.primary_model)`)와 실행 파이프라인
(`build_analysis_pipeline` — Step 1에서 동일 함수 사용)이 같은 해석을 쓰므로
캐시 키·실행 모델 일관성이 자동 보장된다(스펙 §5). `mgr` 변수가 이 함수에서 다른 곳에
쓰이면 그 부분은 유지.

- [ ] **Step 4: 리그레션**

Run: `.venv_e2e/bin/python -m pytest tests/ -q`
Expected: all passed (전역 키 미설정 환경에서는 기존과 동일 동작 — get_global이 None을
반환해 코드 기본값 폴백)

- [ ] **Step 5: Commit**

```bash
git add app/services/analyzer.py app/services/digest_service.py app/services/monitor_service.py
git commit -m "feat: AI 게이트웨이 해석을 resolve_ai_gateway로 통일 (그룹 오버라이드→전역 폴백)"
```

---

### Task 7: 기록 3지점 배선 + complete_cached 토큰

**Files:**
- Modify: `app/services/analysis_cache_service.py:140-143` (complete_cached)
- Modify: `app/services/monitor_service.py` (직접 경로 :627-656, claimed 경로 :765-790)
- Modify: `app/services/digest_service.py` (synthesize_with_llm 시그니처+기록, 호출부 :563)
- Test: `tests/test_usage_recording.py` (신규), `tests/test_analysis_cache_service.py` (추가)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_analysis_cache_service.py` 끝에 추가:

```python
async def test_complete_cached_passes_tokens(monkeypatch):
    """complete_cached가 토큰을 mark_completed로 전달한다 (Phase C 배선)."""
    from app.services import analysis_cache_service as acs

    captured = {}

    async def _fake_mark(session, cache_id, analysis, input_tokens=None, output_tokens=None):
        captured.update(cache_id=cache_id, input_tokens=input_tokens, output_tokens=output_tokens)

    class _S:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def commit(self): pass

    monkeypatch.setattr(acs, "mark_completed", _fake_mark)
    monkeypatch.setattr(acs, "get_sessionmaker", lambda: (lambda: _S()))
    await acs.complete_cached(9, {"a": 1}, input_tokens=11, output_tokens=22)
    assert captured == {"cache_id": 9, "input_tokens": 11, "output_tokens": 22}
```

`tests/test_usage_recording.py` (신규):

```python
"""기록 지점 배선 테스트 — 다이제스트 경로 (monkeypatch, DB·LLM 불필요)."""

from types import SimpleNamespace

from app.services.llm_client import ChatResult
from app.services.settings_types import AIGatewaySettings

FAKE_AI = AIGatewaySettings(
    base_url="http://x:4000", api_key="k",
    primary_model="gemini/m", tagging_model="gemini/m", digest_model="",
    temperature=0.2, max_tokens=1024, daily_budget_usd=2.0,
)


async def test_synthesize_records_user_attributed_usage(monkeypatch):
    from datetime import datetime, timezone

    from app.services import digest_service as ds

    recorded = {}

    async def _fake_resolve(group_id):
        return FAKE_AI

    class _FakeClient:
        def __init__(self, ai): pass
        async def chat(self, **kw):
            return ChatResult(
                content='{"headline":"h","summary_md":"s","telegram_summary":"t"}',
                raw={}, input_tokens=500, output_tokens=200,
            )
        async def aclose(self): pass

    async def _fake_record(**kw):
        recorded.update(kw)

    async def _fake_prompts(group_id):
        return SimpleNamespace(digest_prompt="", analysis_prompt="")

    monkeypatch.setattr(ds, "LiteLLMClient", _FakeClient)
    monkeypatch.setattr(ds, "record_usage", _fake_record)
    monkeypatch.setattr("app.services.global_settings.resolve_ai_gateway", _fake_resolve)
    monkeypatch.setattr("app.services.preset_service.resolve_prompts", _fake_prompts)

    agg = SimpleNamespace(
        video_count=1, sentiment_breakdown={}, top_tags=[], top_channels=[], videos=[],
    )
    now = datetime.now(timezone.utc)
    await ds.synthesize_with_llm(
        group_id=10, aggregate=agg, period_start=now, period_end=now,
        owner_user_id=2,
    )
    assert recorded["user_id"] == 2          # 사용자 귀속 (스펙 §4 표 3행)
    assert recorded["group_id"] == 10
    assert recorded["purpose"] == "digest"
    assert recorded["input_tokens"] == 500 and recorded["output_tokens"] == 200
```

- [ ] **Step 2: 실패 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_usage_recording.py tests/test_analysis_cache_service.py -q`
Expected: 신규 FAIL — `owner_user_id` 파라미터/`record_usage` 참조 없음, complete_cached 토큰 미전달

- [ ] **Step 3: complete_cached 확장**

`app/services/analysis_cache_service.py:140`:

```python
async def complete_cached(
    cache_id: int,
    analysis: Dict[str, Any],
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
) -> None:
    async with get_sessionmaker()() as session:
        await mark_completed(
            session, cache_id, analysis,
            input_tokens=input_tokens, output_tokens=output_tokens,
        )
        await session.commit()
```

(`Optional` 임포트 확인. `mark_completed`는 이미 두 파라미터를 받는다 — :91.)

- [ ] **Step 4: digest_service — 시그니처·기록**

`synthesize_with_llm` 시그니처에 파라미터 추가:

```python
async def synthesize_with_llm(
    group_id: int,
    aggregate: DigestAggregate,
    period_start: datetime,
    period_end: datetime,
    category: str = "",
    previous_digest: str = "없음",
    digest_prompt: str = "",
    period_days: int = 7,
    owner_user_id: Optional[int] = None,
) -> DigestGenerated:
```

임포트 추가:

```python
from app.services.ai_usage_service import record_usage
```

`chat = await client.chat(...)` 성공 직후(json.loads 앞)에 기록 삽입:

```python
        # 다이제스트는 그룹 개인화 호출 — 그룹 owner 몫으로 원장 기록 (스펙 §2.4)
        await record_usage(
            user_id=owner_user_id,
            group_id=group_id,
            purpose="digest",
            model=model,
            input_tokens=chat.input_tokens,
            output_tokens=chat.output_tokens,
        )
```

호출부(:563 `generated = await synthesize_with_llm(`)에 인자 추가:

```python
                owner_user_id=group.owner_user_id,
```

- [ ] **Step 5: monitor_service — 분석 2지점**

임포트 추가(상단):

```python
from app.services.ai_usage_service import record_usage
```

**직접 경로**(:636 인근): `await pipeline.run_and_save(` → 결과를 변수로 받고 기록:

```python
                    result = await pipeline.run_and_save(
                        session=sess,
                        video_pk=video_pk,
                        video_url=video.video_url,
                        channel_name=channel_name,
                        published_at_str=video.published_at.isoformat(),
                        duration_seconds=video.duration_seconds,
                    )
```

트랜잭션 블록(`async with make_session()` 블록) **밖**, `await write_job_log(... STATUS_SUCCESS ...)` 앞에:

```python
        # 직접/커스텀 프롬프트 분석은 캐시 우회 = 그룹 owner 몫 (스펙 §4 표 1행)
        await record_usage(
            user_id=group.owner_user_id,
            group_id=group.group_id,
            purpose="analysis",
            model=result.model_name,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            video_pk=video_pk,
        )
```

**claimed 경로**(:776): `complete_cached` 호출을 토큰 포함으로 교체 + 시스템 몫 기록:

```python
        await complete_cached(
            outcome.cache_id, result.data,
            input_tokens=result.input_tokens, output_tokens=result.output_tokens,
        )
        # 캐시 미스 실호출은 시스템 몫(user_id=NULL) 1회 기록 (스펙 §2.4 귀속 원칙)
        await record_usage(
            user_id=None,
            group_id=group.group_id,
            purpose="analysis",
            model=ai.primary_model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            video_pk=video_pk,
        )
```

- [ ] **Step 6: 통과 확인 + 리그레션**

Run: `.venv_e2e/bin/python -m pytest tests/test_usage_recording.py tests/test_analysis_cache_service.py tests/test_cache_integration.py -q`
Expected: all passed

- [ ] **Step 7: Commit**

```bash
git add app/services/analysis_cache_service.py app/services/digest_service.py app/services/monitor_service.py tests/test_usage_recording.py tests/test_analysis_cache_service.py
git commit -m "feat: ai_usage 기록 3지점 배선 — 직접분석(owner)·캐시미스(시스템)·다이제스트(owner) + 캐시 토큰"
```

---

### Task 8: 예산 강제 — 다이제스트·커스텀 재분석·직접 프롬프트 스케줄

**Files:**
- Modify: `app/services/digest_service.py` (generate_digest_for_group 게이트, run_digest_tick_once 예외 처리)
- Modify: `app/routers/digests.py:55-75` (generate 400)
- Modify: `app/routers/videos.py` (analyze_now custom 400)
- Modify: `app/services/monitor_service.py` (직접 경로 skipped 게이트)
- Test: `tests/test_budget_enforcement.py` (신규)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_budget_enforcement.py`:

```python
"""월 예산 강제 지점 테스트 (설계 §7 — 사용자 귀속 비용 행위만 차단)."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.auth import CurrentUser, require_user
from app.routers.deps import get_group_or_404
from app.services.ai_usage_service import BudgetExceeded
from app.services.auth_service import set_users_exist

USER = CurrentUser(user_id=2, email="u@x.com", display_name="U", role="user")


class _FakeGroup:
    group_id = 10
    slug = "g1"
    owner_user_id = 2
    schema_name = "youtube_g1"


@pytest.fixture(autouse=True)
def _cleanup():
    set_users_exist(True)
    yield
    set_users_exist(False)
    app.dependency_overrides.clear()


def _as_user_with_group():
    async def _u():
        return USER
    async def _g():
        return _FakeGroup()
    app.dependency_overrides[require_user] = _u
    app.dependency_overrides[get_group_or_404] = _g


def test_digest_generate_budget_exceeded_400(monkeypatch):
    _as_user_with_group()

    async def _deny(group):
        raise BudgetExceeded("월 AI 예산 초과: 당월 $5.10 / 예산 $5.00", limit=5.0, current=5.1)

    monkeypatch.setattr("app.routers.digests._budget_gate", _deny)
    c = TestClient(app, raise_server_exceptions=False)
    resp = c.post("/api/groups/g1/digests/generate", json={})
    assert resp.status_code == 400
    assert "월 AI 예산 초과" in resp.json()["detail"]


def test_analyze_now_custom_prompt_budget_400(monkeypatch):
    _as_user_with_group()

    async def _deny(group):
        raise BudgetExceeded("월 AI 예산 초과", limit=5.0, current=5.1)

    monkeypatch.setattr("app.routers.videos._budget_gate", _deny)
    c = TestClient(app, raise_server_exceptions=False)
    resp = c.post(
        "/api/groups/g1/videos/1/analyze",
        json={"custom_prompt": "요약해줘"},
    )
    assert resp.status_code == 400
    assert "월 AI 예산" in resp.json()["detail"]


async def test_budget_gate_helper_passes_without_budget(monkeypatch):
    """owner 없음/예산 없음이면 통과."""
    from app.services import ai_usage_service as aus

    class _G:
        owner_user_id = None

    ok, reason = await aus.budget_ok_for_group(_G())
    assert ok is True and reason == ""
```

주의: analyze 엔드포인트의 실제 경로는 `app/routers/videos.py`에서
`@router.post("/{video_pk}/analyze")`(:285 인근, 함수 docstring "즉시 분석") — 테스트 전
실제 경로를 `grep -n "analyze" app/routers/videos.py`로 확인해 위 URL을 맞출 것.

- [ ] **Step 2: 실패 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_budget_enforcement.py -q`
Expected: FAIL — `_budget_gate` 미존재

- [ ] **Step 3: digest_service — 중앙 게이트**

`generate_digest_for_group`(:514) 도입부(첫 줄 `await dpm.ensure_schema(group)` 앞)에 삽입:

```python
    # 월 예산 게이트 (설계 §7): 다이제스트는 owner 귀속 비용 — 초과 시 생성 자체를 막는다.
    # 수동 API는 라우터가 400으로, 스케줄 틱은 아래 run_digest_tick_once가 skip으로 변환.
    from app.services.ai_usage_service import BudgetExceeded, budget_ok_for_group

    ok, reason = await budget_ok_for_group(group)
    if not ok:
        raise BudgetExceeded(reason, limit=0, current=0)
```

`run_digest_tick_once`(:720)의 그룹 루프 안 `try:` 블록에서, `generate_digest_for_group`
호출을 감싸는 지점에 BudgetExceeded 분기 추가 — 기존 `except Exception as e: print(...)`
**앞에**:

```python
                except BudgetExceeded as e:
                    print(f"[digest] {group.slug} 월 예산 초과로 skip: {e.detail}")
                    continue
```

(임포트는 파일 상단 `from app.services.ai_usage_service import BudgetExceeded, budget_ok_for_group, record_usage`로 통합. 실제 try/except 중첩 구조는 tick 코드를 열어 확인 후
BudgetExceeded가 그룹 단위 skip이 되도록 배치할 것 — cfg 루프가 아니라 group 루프 레벨.)

- [ ] **Step 4: 라우터 게이트 — digests.py / videos.py**

`app/routers/digests.py` — 모듈 수준 간접 참조(테스트 monkeypatch 지점) + generate에서 사용:

```python
from app.services.ai_usage_service import BudgetExceeded, budget_ok_for_group


async def _budget_gate(group) -> None:
    ok, reason = await budget_ok_for_group(group)
    if not ok:
        raise BudgetExceeded(reason, limit=0, current=0)
```

`generate_digest` 함수 도입부(설정 조회 앞)에:

```python
    try:
        await _budget_gate(group)
    except BudgetExceeded as e:
        raise HTTPException(status_code=400, detail=e.detail)
```

`app/routers/videos.py` — 동일 패턴의 `_budget_gate` 추가 후, analyze 엔드포인트에서
`custom = payload.custom_prompt if payload else None` 직후 삽입:

```python
    if custom and custom.strip():
        # 커스텀 프롬프트는 캐시 우회 = owner 귀속 비용 (설계 §7)
        try:
            await _budget_gate(group)
        except BudgetExceeded as e:
            raise HTTPException(status_code=400, detail=e.detail)
```

- [ ] **Step 5: monitor_service — 직접 프롬프트 스케줄 경로 skipped**

`_run_analysis`의 `# ── 기존 경로 (직접 프롬프트 / 커스텀 오버라이드) ──` 주석 직후,
`pipeline = await build_analysis_pipeline(...)` **앞**에 삽입:

```python
    # 월 예산 게이트 (설계 §7 표 4행): 직접 프롬프트 분석은 owner 귀속 비용.
    # skipped는 재클레임되지 않아 핫루프 없음(duration 게이트와 동일 패턴).
    # 현재 직접 프롬프트는 admin 전용(§3.3)이라 실질 방어선이 아닌 방어적 완결성.
    from app.services.ai_usage_service import budget_ok_for_group

    b_ok, b_reason = await budget_ok_for_group(group)
    if not b_ok:
        async with make_session() as sess:
            async with sess.begin():
                await sess.execute(
                    update(Video)
                    .where(Video.video_pk == video_pk)
                    .values(analysis_status="skipped", analysis_error=b_reason)
                )
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_VIDEO_ANALYZE,
            status=STATUS_SKIP,
            message=b_reason,
            channel_pk=channel_pk,
            video_pk=video_pk,
        )
        return
```

- [ ] **Step 6: 통과 확인 + 리그레션**

Run: `.venv_e2e/bin/python -m pytest tests/test_budget_enforcement.py tests/test_scheduler_quota.py tests/test_quota_enforcement.py -q`
Expected: all passed

- [ ] **Step 7: Commit**

```bash
git add app/services/digest_service.py app/routers/digests.py app/routers/videos.py app/services/monitor_service.py tests/test_budget_enforcement.py
git commit -m "feat: 월 예산 강제 — 다이제스트 400/skip·커스텀 재분석 400·직접 프롬프트 스케줄 skipped"
```

---

### Task 9: 설정 카테고리 권한 분리 (§3.3) + GET /api/presets

**Files:**
- Modify: `app/routers/settings.py` (권한 가드·필드 필터·models 엔드포인트 admin 가드)
- Modify: `app/routers/prompts.py` 또는 신규 위치 — `GET /api/groups/{slug}/presets` (사용자용 활성 프리셋 목록; 기존 라우터 구조 확인 후 적절한 파일에 배치, 없으면 settings.py에)
- Test: `tests/test_settings_permissions.py` (신규)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_settings_permissions.py`:

```python
"""§3.3 설정 카테고리 권한 분리: user 차단/필드 필터, admin 전체."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.auth import CurrentUser, require_user
from app.routers.deps import get_group_or_404
from app.services.auth_service import set_users_exist

ADMIN = CurrentUser(user_id=1, email="a@x.com", display_name="A", role="admin")
USER = CurrentUser(user_id=2, email="u@x.com", display_name="U", role="user")


class _FakeGroup:
    group_id = 10
    slug = "g1"
    owner_user_id = 2
    schema_name = "youtube_g1"


@pytest.fixture(autouse=True)
def _cleanup():
    set_users_exist(True)
    yield
    set_users_exist(False)
    app.dependency_overrides.clear()


def _as(user):
    async def _u():
        return user
    async def _g():
        return _FakeGroup()
    app.dependency_overrides[require_user] = _u
    app.dependency_overrides[get_group_or_404] = _g


def test_user_blocked_categories_404():
    _as(USER)
    c = TestClient(app, raise_server_exceptions=False)
    for cat in ("database", "ai_gateway"):
        assert c.get(f"/api/groups/g1/settings/{cat}").status_code == 404, cat
        r = c.put(f"/api/groups/g1/settings/{cat}", json={"items": []})
        assert r.status_code == 404, cat


def test_user_blocked_fields_put_400(monkeypatch):
    _as(USER)
    c = TestClient(app, raise_server_exceptions=False)
    r = c.put(
        "/api/groups/g1/settings/polling",
        json={"items": [{"key": "youtube_api_key", "value": "x", "value_type": "string"}]},
    )
    assert r.status_code == 400
    r2 = c.put(
        "/api/groups/g1/settings/notification",
        json={"items": [{"key": "bot_token", "value": "x", "value_type": "string"}]},
    )
    assert r2.status_code == 400
    r3 = c.put(
        "/api/groups/g1/settings/prompts",
        json={"items": [{"key": "analysis_prompt", "value": "x", "value_type": "string"}]},
    )
    assert r3.status_code == 400


def test_user_get_filters_secret_fields(monkeypatch):
    _as(USER)

    async def _fake_list(group_id, category):
        return [
            {"key": "youtube_api_key", "value": "***", "value_type": "string"},
            {"key": "window_hours", "value": "48", "value_type": "int"},
        ]

    class _Mgr:
        list_for_api = staticmethod(_fake_list)

    monkeypatch.setattr("app.routers.settings.get_settings_manager", lambda: _Mgr())
    c = TestClient(app, raise_server_exceptions=False)
    keys = {i["key"] for i in c.get("/api/groups/g1/settings/polling").json()}
    assert "youtube_api_key" not in keys
    assert "window_hours" in keys


def test_admin_keeps_full_access(monkeypatch):
    _as(ADMIN)

    async def _fake_list(group_id, category):
        return [{"key": "base_url", "value": "http://x", "value_type": "string"}]

    class _Mgr:
        list_for_api = staticmethod(_fake_list)

    monkeypatch.setattr("app.routers.settings.get_settings_manager", lambda: _Mgr())
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/api/groups/g1/settings/ai_gateway").status_code == 200
```

- [ ] **Step 2: 실패 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_settings_permissions.py -q`
Expected: FAIL — user가 ai_gateway 200 등

- [ ] **Step 3: settings.py 가드 구현**

상단에 상수·헬퍼 추가:

```python
from app.routers.auth import CurrentUser, require_user

# §3.3 설정 카테고리 권한 (설계 §6). admin은 전체, user는 아래 제한.
ADMIN_ONLY_CATEGORIES = {"database", "ai_gateway"}
# user에게 허용되는 키만 나열(화이트리스트) — 그 외 키는 GET 제외·PUT 400
USER_FIELD_ALLOWLIST: dict[str, set[str]] = {"prompts": {"preset_id"}}
# user에게 차단되는 키(블랙리스트) — 나머지는 허용
USER_FIELD_BLOCKLIST: dict[str, set[str]] = {
    "polling": {"youtube_api_key"},
    "notification": {"bot_token", "chat_ids"},
}


def _check_user_category(category: str, user: CurrentUser) -> None:
    if not user.is_admin and category in ADMIN_ONLY_CATEGORIES:
        # 타 카테고리 존재를 노출하지 않도록 미존재와 동일 취급 (§3.3 은닉)
        raise HTTPException(status_code=404, detail="설정을 찾을 수 없습니다.")


def _filter_items_for_user(category: str, user: CurrentUser, items: list[dict]) -> list[dict]:
    if user.is_admin:
        return items
    allow = USER_FIELD_ALLOWLIST.get(category)
    if allow is not None:
        return [i for i in items if i["key"] in allow]
    block = USER_FIELD_BLOCKLIST.get(category, set())
    return [i for i in items if i["key"] not in block]


def _reject_blocked_puts(category: str, user: CurrentUser, items) -> None:
    if user.is_admin:
        return
    allow = USER_FIELD_ALLOWLIST.get(category)
    block = USER_FIELD_BLOCKLIST.get(category, set())
    for item in items:
        if allow is not None and item.key not in allow:
            raise HTTPException(status_code=400, detail=f"수정 권한이 없는 항목: {item.key}")
        if item.key in block:
            raise HTTPException(status_code=400, detail=f"수정 권한이 없는 항목: {item.key}")
```

`get_settings`/`put_settings` 시그니처에 `user: CurrentUser = Depends(require_user)` 추가,
본문 도입부(`_check_category` 직후)에 `_check_user_category(category, user)` 호출,
GET 반환을 `_filter_items_for_user(category, user, items)`로, PUT은 `mgr.set_values` 앞에
`_reject_blocked_puts(category, user, payload.items)` 호출.

`list_ai_gateway_models`(GET /ai_gateway/models)에도 동일하게 user dep 추가 후
`if not user.is_admin: raise HTTPException(404, ...)`.

**주의**: notification 차단 키는 `chat_ids`(복수형, settings_types.py:37) — 스펙 표의
`chat_id` 표기와 다름. 코드 실측값(`chat_ids`)을 따른다.

- [ ] **Step 4: GET /api/groups/{slug}/presets (사용자용 활성 프리셋 목록)**

user가 preset_id를 고르려면 프리셋 이름 목록이 필요(현재 admin 전용 API뿐).
settings.py에 추가:

```python
from app.models.control.prompt_preset import PromptPreset
from app.control_db import get_sessionmaker as _ctrl_sessionmaker
from sqlalchemy import select as _select


@router.get("/prompts/presets")
async def list_active_presets(group: Group = Depends(get_group_or_404)) -> list[dict]:
    """활성 프리셋 id/이름/설명 — 사용자 프리셋 선택용(본문 비노출)."""
    async with _ctrl_sessionmaker()() as session:
        rows = (
            await session.execute(
                _select(PromptPreset)
                .where(PromptPreset.is_active.is_(True))
                .order_by(PromptPreset.preset_id)
            )
        ).scalars().all()
    return [
        {"preset_id": p.preset_id, "name": p.name, "description": p.description or ""}
        for p in rows
    ]
```

(라우트 순서 주의: `/{category}`보다 **먼저** 선언해야 `prompts/presets`가 카테고리로
매칭되지 않는다 — FastAPI는 선언 순 매칭. 파일 내 위치를 `get_settings` 위로.)

테스트 추가(tests/test_settings_permissions.py 끝에):

```python
def test_presets_route_registered():
    paths = {r.path for r in app.routes}
    assert "/api/groups/{slug}/settings/prompts/presets" in paths
```

- [ ] **Step 5: 통과 확인 + 리그레션**

Run: `.venv_e2e/bin/python -m pytest tests/test_settings_permissions.py tests/test_plan4_endpoints.py tests/test_notification_settings_defaults.py -q`
Expected: all passed (기존 설정 테스트가 user 컨텍스트 없이 돌던 경우 — 개발 모드
DEV_ADMIN(role=admin)이라 admin 경로로 통과)

- [ ] **Step 6: Commit**

```bash
git add app/routers/settings.py tests/test_settings_permissions.py
git commit -m "feat: §3.3 설정 권한 분리 — user는 database/ai_gateway 404·시크릿 필드 차단·프리셋 선택만"
```

---

### Task 10: admin 사용량 API + me/usage 확장

**Files:**
- Modify: `app/schemas/admin.py` (AdminUsageRow/Response), `app/routers/admin.py` (GET /usage)
- Modify: `app/schemas/auth.py` (MyLimits/MyUsage 확장), `app/routers/auth.py` (my_usage)
- Test: `tests/test_admin_usage_api.py` (신규), `tests/test_me_usage.py` (추가)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_admin_usage_api.py`:

```python
"""GET /api/admin/usage — 사용자·모델·purpose별 집계."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.auth import CurrentUser, require_user
from app.services.auth_service import set_users_exist

ADMIN = CurrentUser(user_id=1, email="a@x.com", display_name="A", role="admin")
USER = CurrentUser(user_id=2, email="u@x.com", display_name="U", role="user")


@pytest.fixture(autouse=True)
def _cleanup():
    set_users_exist(True)
    yield
    set_users_exist(False)
    app.dependency_overrides.clear()


def test_route_registered():
    assert "/api/admin/usage" in {r.path for r in app.routes}


def test_non_admin_403():
    async def _u():
        return USER
    app.dependency_overrides[require_user] = _u
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/api/admin/usage").status_code == 403


def test_invalid_window_400():
    async def _a():
        return ADMIN
    app.dependency_overrides[require_user] = _a
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/api/admin/usage?window=yesterday").status_code == 400
```

`tests/test_me_usage.py`의 `test_me_usage_shape`에 확장 검증 추가 — monkeypatch 대상에
`month_cost_usd` 추가 후:

```python
    monkeypatch.setattr("app.routers.auth.month_cost_usd", _n)  # 기존 _n 재사용(3 반환)
```

응답 assert에 추가:

```python
    assert data["usage"]["month_cost_usd"] == 3
    assert data["limits"]["monthly_cost_budget_usd"] is None or isinstance(
        data["limits"]["monthly_cost_budget_usd"], (int, float)
    )
```

(참고: 기존 `_limits`가 반환하는 EffectiveLimits는 Task 4에서 기본값
`monthly_cost_budget_usd=None`을 갖는다.)

- [ ] **Step 2: 실패 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_admin_usage_api.py tests/test_me_usage.py -q`
Expected: 신규 FAIL — 라우트/필드 미존재

- [ ] **Step 3: 스키마 — app/schemas/admin.py 끝에**

```python
class AdminUsageRow(BaseModel):
    user_id: Optional[int] = None     # None = 시스템 몫(공유 캐시 분석)
    email: Optional[str] = None
    model: str
    purpose: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: Optional[float] = None  # 전 행 단가 미상이면 None
    null_cost_calls: int = 0          # 단가 미상 호출 수(경고 표시용)


class AdminUsageResponse(BaseModel):
    window: str
    start: datetime
    end: datetime
    rows: list[AdminUsageRow]
    total_cost_usd: float
    null_cost_row_count: int          # 단가 미상 원장 행 총수 (스펙 §2.4 경고)
```

`app/schemas/auth.py`:

```python
class MyLimits(BaseModel):
    max_groups: int
    max_channels_total: int
    max_analyses_per_day: int
    max_video_minutes: int
    min_poll_interval_min: int
    monthly_cost_budget_usd: Optional[float] = None


class MyUsage(BaseModel):
    group_count: int
    channel_count: int
    today_analyses: int
    month_cost_usd: float = 0.0
```

- [ ] **Step 4: admin.py — GET /usage**

임포트 추가:

```python
from datetime import timedelta

from app.models.control.ai_usage import AIUsage
from app.schemas.admin import AdminUsageResponse, AdminUsageRow
from app.services.ai_usage_service import kst_month_start_utc
```

엔드포인트(파일 끝에):

```python
@router.get("/usage", response_model=AdminUsageResponse)
async def usage_summary(
    window: str = "this_month",
    session: AsyncSession = Depends(get_session),
) -> AdminUsageResponse:
    now = datetime.now(timezone.utc)
    if window == "this_month":
        start, end = kst_month_start_utc(now), now
    elif window == "last_month":
        end = kst_month_start_utc(now)
        start = kst_month_start_utc(end - timedelta(seconds=1))
    elif window == "30d":
        start, end = now - timedelta(days=30), now
    else:
        raise HTTPException(status_code=400, detail="window는 this_month|last_month|30d")

    rows_q = (
        await session.execute(
            select(
                AIUsage.user_id,
                User.email,
                AIUsage.model,
                AIUsage.purpose,
                sa_func.count(AIUsage.usage_id),
                sa_func.coalesce(sa_func.sum(AIUsage.input_tokens), 0),
                sa_func.coalesce(sa_func.sum(AIUsage.output_tokens), 0),
                sa_func.sum(AIUsage.cost_usd),
                sa_func.count(AIUsage.usage_id).filter(AIUsage.cost_usd.is_(None)),
            )
            .outerjoin(User, User.user_id == AIUsage.user_id)
            .where(AIUsage.created_at >= start, AIUsage.created_at < end)
            .group_by(AIUsage.user_id, User.email, AIUsage.model, AIUsage.purpose)
            .order_by(AIUsage.user_id.asc().nulls_first(), AIUsage.model)
        )
    ).all()

    out_rows = [
        AdminUsageRow(
            user_id=r[0], email=r[1], model=r[2], purpose=r[3], calls=r[4],
            input_tokens=r[5], output_tokens=r[6],
            cost_usd=float(r[7]) if r[7] is not None else None,
            null_cost_calls=r[8],
        )
        for r in rows_q
    ]
    return AdminUsageResponse(
        window=window, start=start, end=end, rows=out_rows,
        total_cost_usd=sum(r.cost_usd or 0.0 for r in out_rows),
        null_cost_row_count=sum(r.null_cost_calls for r in out_rows),
    )
```

(`sa_func`는 Task 8(Phase B)에서 admin.py에 이미 임포트됨 — 확인 후 없으면 추가.)

- [ ] **Step 5: auth.py — my_usage 확장**

임포트에 `month_cost_usd` 추가:

```python
from app.services.ai_usage_service import month_cost_usd
```

`my_usage`에서 usage 구성 교체:

```python
    usage = MyUsage(
        group_count=await count_owned_groups(session, user.user_id),
        channel_count=await count_owned_channels(session, user.user_id),
        today_analyses=await count_daily_deliveries(session, user.user_id),
        month_cost_usd=float(await month_cost_usd(session, user.user_id)),
    )
```

limits 분기의 MyLimits 생성에 추가:

```python
            monthly_cost_budget_usd=limits.monthly_cost_budget_usd,
```

- [ ] **Step 6: 통과 확인**

Run: `.venv_e2e/bin/python -m pytest tests/test_admin_usage_api.py tests/test_me_usage.py tests/test_admin_users_api.py -q`
Expected: all passed

- [ ] **Step 7: Commit**

```bash
git add app/schemas/admin.py app/schemas/auth.py app/routers/admin.py app/routers/auth.py tests/test_admin_usage_api.py tests/test_me_usage.py
git commit -m "feat: 관리자 AI 사용량 집계 API(window·NULL단가 경고) + 마이페이지 당월 비용"
```

---

### Task 11: 프런트 — 설정 role 필터·Admin 사용량 섹션·MyPage 비용

**Files:**
- Modify: `frontend/src/settings/defs.ts` (role 필터 헬퍼)
- Modify: `frontend/src/pages/Settings.tsx` (카테고리·필드 필터, user 프리셋 선택)
- Modify: `frontend/src/api/admin.ts`, `frontend/src/pages/Admin.tsx` (사용량 섹션)
- Modify: `frontend/src/api/me.ts`, `frontend/src/pages/MyPage.tsx` (비용 행)
- Modify: `frontend/src/api/settings.ts` (presets 호출 추가 — 기존 구조 확인)

- [ ] **Step 1: defs.ts — role 필터 헬퍼**

```typescript
// §3.3 설정 권한 (백엔드와 동일 규칙 — UI 은닉용, 강제는 서버가 담당)
const ADMIN_ONLY_CATEGORIES = new Set(['database', 'ai_gateway'])
const USER_FIELD_BLOCKLIST: Record<string, Set<string>> = {
  polling: new Set(['youtube_api_key']),
  notification: new Set(['bot_token', 'chat_ids']),
}

export function visibleCategories(role: 'admin' | 'user' | undefined): SettingCategory[] {
  if (role === 'admin') return SETTING_CATEGORIES
  return SETTING_CATEGORIES.filter((c) => !ADMIN_ONLY_CATEGORIES.has(c.key))
}

export function visibleFields(
  role: 'admin' | 'user' | undefined, category: string, defs: FieldDef[],
): FieldDef[] {
  if (role === 'admin') return defs
  const block = USER_FIELD_BLOCKLIST[category]
  return block ? defs.filter((d) => !block.has(d.key)) : defs
}
```

- [ ] **Step 2: Settings.tsx 적용**

- `const { user } = useAuth()` 추가(`../auth/useAuth`).
- 탭 렌더의 `SETTING_CATEGORIES.map` → `visibleCategories(user?.role).map`.
- 리다이렉트 기본 카테고리도 `visibleCategories(user?.role)[0].key`.
- 필드 렌더에 `visibleFields(user?.role, category, defs)` 적용.
- user이고 `category === 'prompts'`일 때: 텍스트영역 대신 프리셋 셀렉터 렌더 —
  `GET /api/groups/{slug}/settings/prompts/presets`로 목록을 불러와
  `<select>`(값=preset_id), 저장은 기존 PUT prompts에 `{key: 'preset_id', value, value_type: 'int'}`
  1건만 전송. admin은 기존 화면 유지.

- [ ] **Step 3: admin.ts + Admin.tsx 사용량 섹션**

`admin.ts`에 타입·메서드 추가:

```typescript
export interface AdminUsageRow {
  user_id: number | null
  email: string | null
  model: string
  purpose: string
  calls: number
  input_tokens: number
  output_tokens: number
  cost_usd: number | null
  null_cost_calls: number
}

export interface AdminUsageResponse {
  window: string
  start: string
  end: string
  rows: AdminUsageRow[]
  total_cost_usd: number
  null_cost_row_count: number
}

// adminApi에 추가:
  usage: (window: string) =>
    rootApi.get<AdminUsageResponse>(`/admin/usage?window=${window}`),
```

`Admin.tsx`에 "AI 사용량" 섹션(기존 카드 스타일): window 셀렉트(this_month/last_month/30d)
+ 테이블(사용자(이메일 또는 "시스템")·모델·purpose·호출·토큰·비용) + 하단 총비용.
`null_cost_row_count > 0`이면 경고 배너: "단가 미등록 호출 N건 — 전역설정의
ai_model_prices에 단가를 등록하세요."

- [ ] **Step 4: me.ts + MyPage.tsx**

`me.ts` MyUsageResponse에 필드 추가:

```typescript
  limits: { ...; monthly_cost_budget_usd: number | null } | null
  usage: { ...; month_cost_usd: number }
```

`MyPage.tsx` 테이블에 행 추가:

```tsx
<tr><td className="py-1 text-gray-500">당월 AI 비용</td>
  <td>${data.usage.month_cost_usd.toFixed(4)}
    {data.limits?.monthly_cost_budget_usd != null && ` / $${data.limits.monthly_cost_budget_usd}`}
    <span className="text-xs text-gray-400 ml-1">(KST 월초 초기화)</span></td></tr>
```

- [ ] **Step 5: 빌드·테스트 확인**

Run: `cd frontend && npm run test && npm run build`
Expected: 성공, 타입 에러 없음

- [ ] **Step 6: Commit**

```bash
git add frontend/src/settings/defs.ts frontend/src/pages/Settings.tsx frontend/src/api/admin.ts frontend/src/pages/Admin.tsx frontend/src/api/me.ts frontend/src/pages/MyPage.tsx frontend/src/api/settings.ts
git commit -m "feat: 프런트 — 설정 role 필터·user 프리셋 선택·AI 사용량 대시보드·마이페이지 비용"
```

---

### Task 12: 전체 리그레션 + 문서 갱신

**Files:**
- Modify: `docs/superpowers/specs/2026-07-03-multi-tenant-design.md` (§7 Phase 표 C행, §4 표)

- [ ] **Step 1: 전체 테스트**

Run: `.venv_e2e/bin/python -m pytest tests/ -q`
Expected: all passed (기존 244 + 신규 전부)

- [ ] **Step 2: 프런트 테스트/빌드**

Run: `cd frontend && npm run test && npm run build`
Expected: 성공

- [ ] **Step 3: 상위 스펙 갱신**

`2026-07-03-multi-tenant-design.md`:
- §7 Phase 표 C행에 `(구현 완료 2026-XX-XX)` 주석(실제 날짜).
- §4 표의 monthly_cost_budget_usd 행의 "강제 지점"을 실제 구현으로 수정:
  `다이제스트 생성(스케줄 skip/수동 400) + 커스텀 프롬프트 재분석 400 + 직접 프롬프트 스케줄 skipped — 프리셋 캐시 분석은 시스템 몫이라 차단하지 않음(설계 2026-07-10 §7, 승인된 편차)`.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-07-03-multi-tenant-design.md
git commit -m "docs: Phase C 구현 반영 — ai_usage 원장·전역 게이트웨이·권한 분리·예산 강제 완료 표기"
```

---

## 실 DB E2E (구현 완료 후, 별도 세션 체크포인트)

테스트 DB `100.115.13.102`(`.env`의 `CONTROL_DATABASE_URL`), 그룹 `e2e_a`/`e2e_b` 재활용.
**주의: `postgres-ytdb` MCP는 프로덕션 — 절대 사용 금지. 앱 자체 엔진으로만**
(`PYTHONPATH=. .venv_e2e/bin/python script.py`, httpx.AsyncClient + ASGITransport —
TestClient는 이벤트루프 충돌. 로그인 경로는 `/api/auth/login`).

1. 부팅: `ensure_control_schema()` → ai_usage 생성. `bootstrap_global_settings()` →
   전역 ai_base_url/ai_api_key가 admin 그룹 설정에서 시드(멱등 재확인).
2. 단가표 등록(관리자 API PUT ai_model_prices) 후 실 분석 1건(캐시 미스, 프리셋) →
   ai_usage 1행: user_id=NULL·토큰>0·cost_usd 계산됨 + analysis_cache.input/output_tokens 채워짐.
   같은 영상 재분석(캐시 히트) → ai_usage 행 불변.
3. 다이제스트 수동 생성 1건 → user_id=owner·purpose=digest 1행.
4. free 유저 예산 오버라이드 0.0001로 설정 → 다이제스트 생성 400, 커스텀 재분석 400,
   마이페이지 month_cost/예산 표시. 해제 → 재개.
5. user 계정으로 settings/ai_gateway·database GET/PUT → 404. polling GET에
   youtube_api_key 부재. prompts PUT analysis_prompt → 400, preset_id → 200.
6. GET /api/admin/usage 세 window 응답 + 시스템/사용자 행 분리 + NULL 단가 경고 카운트.
7. 전역 게이트웨이 폴백: 신규 그룹(그룹 ai 설정 없음)에서 resolve가 전역값 반환 확인.
