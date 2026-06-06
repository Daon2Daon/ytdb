# 그룹 발송 기준선(notify_baseline_at) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 발송 기능 활성화 이후 게시된 영상만 자동 발송하고, 활성화 이전 backlog는 자동 발송에서 제외(데이터는 보존)하여 서비스 개시 시 대량 발송을 막는다.

**Architecture:** 그룹 알림 설정에 `notify_baseline_at`(UTC ISO 문자열) 기준선을 둔다. 알림이 발송 가능(sendable)으로 전환될 때 `now()`로 스탬프하고, 두 자동 발송 경로(`_notify_after_analysis`, `notify_pending_batch`)에서 `published_at >= notify_baseline_at`을 통과한 영상만 발송한다. 기준선이 없고 sendable이면 안전측으로 자동 발송을 보류한다. 선택적 발송은 기존 수동 "Telegram 발송" 버튼을 그대로 쓴다.

**Tech Stack:** Python, FastAPI, SQLAlchemy(async), pytest. 설정은 그룹별 key/value row(`Setting`)로 저장.

설계 문서: `docs/superpowers/specs/2026-06-06-group-notify-baseline-design.md`

---

## File Structure

- `app/services/settings_types.py` — `NotificationSettings`에 `notify_baseline_at` 필드 추가.
- `app/services/settings_manager.py` — datetime 파싱 헬퍼 `_as_dt` + `get_notification` 파싱.
- `app/services/default_settings.py` — notification 기본 키 정의(빈 기준선).
- `app/services/monitor_service.py` — 그룹 게이트 헬퍼 `_passes_group_baseline` + `_notify_after_analysis` 적용.
- `app/services/notify_service.py` — `notify_pending_batch` candidate 필터 + 스탬프 판정 순수 헬퍼(`_should_stamp_on_save`, `_needs_baseline_backfill`).
- `app/routers/settings.py` — 알림 저장 시 false→true 전환 스탬프.
- `app/main.py` — 기동 시 업그레이드 1회 보정 호출.
- `tests/` — 각 순수 헬퍼 단위 테스트.

---

## Task 1: NotificationSettings에 기준선 필드 추가

**Files:**
- Modify: `app/services/settings_types.py:25-37`
- Modify: `app/services/settings_manager.py` (imports + `_as_dt` + `get_notification`)
- Modify: `app/services/default_settings.py:38-50`
- Test: `tests/test_notification_baseline_parse.py`

- [ ] **Step 1: 실패하는 파싱 테스트 작성**

`tests/test_notification_baseline_parse.py`:

```python
"""notification 설정에서 notify_baseline_at(UTC ISO) 파싱 검증."""

from datetime import datetime, timezone

from app.services.settings_manager import _as_dt


def test_as_dt_none_and_empty():
    assert _as_dt(None) is None
    assert _as_dt("") is None


def test_as_dt_parses_iso_utc():
    assert _as_dt("2026-06-06T12:00:00+00:00") == datetime(
        2026, 6, 6, 12, 0, tzinfo=timezone.utc
    )


def test_as_dt_naive_treated_as_utc():
    assert _as_dt("2026-06-06T12:00:00") == datetime(
        2026, 6, 6, 12, 0, tzinfo=timezone.utc
    )


def test_as_dt_invalid_returns_none():
    assert _as_dt("not-a-date") is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_notification_baseline_parse.py -v`
Expected: FAIL — `ImportError: cannot import name '_as_dt'`

- [ ] **Step 3: `_as_dt` 헬퍼 구현**

`app/services/settings_manager.py` 상단 import에 datetime 추가(파일 맨 위 import 블록):

```python
from datetime import datetime, timezone
```

기존 `_as_int`/`_as_float` 헬퍼 근처(약 `:31-45`)에 추가:

```python
def _as_dt(v: Any) -> datetime | None:
    """UTC ISO 문자열을 tz-aware datetime으로. 빈 값/파싱 실패는 None.

    naive datetime은 UTC로 간주한다.
    """
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(v))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_notification_baseline_parse.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: `NotificationSettings` 필드 추가**

`app/services/settings_types.py`, `message_detail` 필드 아래(약 `:37`)에 추가:

```python
    # 발송 기준선: 이 시각 이후 게시(published_at)된 영상만 자동 발송.
    # None이면(sendable인데도) 자동 발송 보류(안전측). 기존 backlog flood 방지.
    notify_baseline_at: Optional[datetime] = None
```

파일 상단 import에 datetime이 없으면 추가:

```python
from datetime import datetime
```

(`Optional`은 기존 import 확인 후 없으면 `from typing import Optional` 추가.)

- [ ] **Step 6: `get_notification`에서 파싱**

`app/services/settings_manager.py`의 `get_notification` 반환부(약 `:204-219`),
`message_detail=...` 줄 다음에 추가:

```python
            notify_baseline_at=_as_dt(d.get("notify_baseline_at")),
```

- [ ] **Step 7: 기본 키 정의**

`app/services/default_settings.py`의 `"notification"` 리스트(약 `:38-50`)
`message_detail` 항목 뒤에 추가:

```python
        {"key": "notify_baseline_at", "value": "", "value_type": "string"},
```

- [ ] **Step 8: 전체 테스트 + import 확인**

Run: `pytest tests/test_notification_baseline_parse.py tests/test_notification_settings_defaults.py tests/test_default_settings.py -v`
Expected: PASS (모든 기존 notification 테스트 포함 통과)

- [ ] **Step 9: 커밋**

```bash
git add app/services/settings_types.py app/services/settings_manager.py app/services/default_settings.py tests/test_notification_baseline_parse.py
git commit -m "feat: NotificationSettings에 notify_baseline_at 기준선 필드"
```

---

## Task 2: 그룹 기준선 게이트 헬퍼 (안전측 null 처리)

**Files:**
- Modify: `app/services/monitor_service.py:368-375` (바로 아래에 신규 함수)
- Test: `tests/test_group_notify_baseline.py`

- [ ] **Step 1: 실패하는 단위 테스트 작성**

`tests/test_group_notify_baseline.py`:

```python
"""그룹 발송 기준선 게이트 검증.

채널용 _passes_notify_baseline과 달리, baseline None이면 '보류(False)'다
(sendable인데 기준선이 비면 flood 방지를 위해 자동 발송하지 않는다).
"""

from datetime import datetime, timedelta, timezone

from app.services.monitor_service import _passes_group_baseline

BASE = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)


def test_none_baseline_blocks():
    assert _passes_group_baseline(None, BASE) is False


def test_published_after_baseline_passes():
    assert _passes_group_baseline(BASE, BASE + timedelta(hours=1)) is True


def test_published_before_baseline_blocked():
    assert _passes_group_baseline(BASE, BASE - timedelta(seconds=1)) is False


def test_equal_timestamp_passes():
    assert _passes_group_baseline(BASE, BASE) is True
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_group_notify_baseline.py -v`
Expected: FAIL — `ImportError: cannot import name '_passes_group_baseline'`

- [ ] **Step 3: 헬퍼 구현**

`app/services/monitor_service.py`, 기존 `_passes_notify_baseline` 함수 바로 아래(`:375` 다음)에 추가:

```python
def _passes_group_baseline(
    baseline: Optional[datetime], published_at: datetime
) -> bool:
    """그룹 발송 기준선 게이트. baseline 이후 게시된 영상만 자동 발송.

    채널용과 달리 baseline이 None이면 보류(False)한다. sendable인데 기준선이
    비어 있으면(트리거 누락 등) 과거 backlog가 한꺼번에 나가는 것을 막는다.
    """
    if baseline is None:
        return False
    return published_at >= baseline
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_group_notify_baseline.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 커밋**

```bash
git add app/services/monitor_service.py tests/test_group_notify_baseline.py
git commit -m "feat: 그룹 발송 기준선 게이트 헬퍼(null=보류)"
```

---

## Task 3: 즉시발송 경로에 그룹 게이트 적용

**Files:**
- Modify: `app/services/monitor_service.py:456-466`

분석 직후 즉시발송(`_notify_after_analysis`)에서, 기존 채널 기준선 검사 직후에 그룹 기준선 검사를 추가한다. `notif`는 함수 상단(`:389`)에서 이미 로드돼 있다.

- [ ] **Step 1: 그룹 게이트 분기 추가**

`app/services/monitor_service.py`, 기존 채널 기준선 블록(`:456-466`) 바로 다음에 추가:

```python
    # 그룹 발송 기준선 게이트: 발송 활성화 이전 게시분(backlog)은 자동 발송 안 함.
    if not _passes_group_baseline(notif.notify_baseline_at, video.published_at):
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_NOTIFY,
            status=STATUS_SKIP,
            message="그룹 baseline 이전(자동발송 보류)",
            channel_pk=channel_pk,
            video_pk=video_pk,
        )
        return
```

- [ ] **Step 2: import/구문 점검**

Run: `python -c "import app.services.monitor_service"`
Expected: 오류 없음 (이미 같은 모듈의 함수이므로 추가 import 불필요)

- [ ] **Step 3: 회귀 테스트 실행**

Run: `pytest tests/ -k "notify or monitor or baseline" -v`
Expected: PASS (기존 테스트 깨지지 않음)

- [ ] **Step 4: 커밋**

```bash
git add app/services/monitor_service.py
git commit -m "feat: 즉시발송 경로에 그룹 baseline 게이트 적용"
```

---

## Task 4: 틱 배치 경로에 그룹 게이트 적용

**Files:**
- Modify: `app/services/notify_service.py:330-351`

`notify_pending_batch`의 candidate 필터에 그룹 기준선 통과 조건을 추가한다. `notif.notify_baseline_at`은 인자로 받은 `notif`에서 바로 쓸 수 있다.

- [ ] **Step 1: import에 그룹 게이트 추가**

`app/services/notify_service.py`, `notify_pending_batch` 내부의 기존 import 줄(`:330`)을 수정:

```python
    from app.services.monitor_service import (
        _passes_group_baseline,
        _passes_notify_baseline,
    )
```

- [ ] **Step 2: candidate 필터에 조건 추가**

`app/services/notify_service.py`의 candidate 컴프리헨션(`:347-351`)을 수정:

```python
    candidates = [
        (v, a, ch)
        for (v, a, ch) in rows
        if _passes_notify_baseline(ch.notify_from, v.published_at)
        and _passes_group_baseline(notif.notify_baseline_at, v.published_at)
    ]
```

- [ ] **Step 3: import/구문 점검**

Run: `python -c "import app.services.notify_service"`
Expected: 오류 없음

- [ ] **Step 4: 회귀 테스트 실행**

Run: `pytest tests/ -k "notify or schedule" -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add app/services/notify_service.py
git commit -m "feat: 틱 배치 경로 candidate에 그룹 baseline 필터 적용"
```

---

## Task 5: 알림 저장 시 false→true 전환 스탬프

**Files:**
- Modify: `app/services/notify_service.py` (순수 판정 헬퍼 추가)
- Modify: `app/routers/settings.py:44-59`
- Test: `tests/test_baseline_stamp_decision.py`

저장 시 `is_sendable`이 false→true로 바뀌면 기준선을 `now()`로 (재)스탬프한다. 판정 로직을 순수 함수로 분리해 단위 테스트한다.

- [ ] **Step 1: 실패하는 판정 테스트 작성**

`tests/test_baseline_stamp_decision.py`:

```python
"""발송 기준선 스탬프 판정(순수 로직) 검증."""

from app.services.notify_service import (
    _needs_baseline_backfill,
    _should_stamp_on_save,
)


def test_stamp_on_false_to_true():
    assert _should_stamp_on_save(before_sendable=False, after_sendable=True) is True


def test_no_stamp_when_already_sendable():
    assert _should_stamp_on_save(before_sendable=True, after_sendable=True) is False


def test_no_stamp_when_becomes_unsendable():
    assert _should_stamp_on_save(before_sendable=True, after_sendable=False) is False


def test_no_stamp_when_stays_unsendable():
    assert _should_stamp_on_save(before_sendable=False, after_sendable=False) is False


def test_backfill_when_sendable_and_no_baseline():
    assert _needs_baseline_backfill(sendable=True, baseline=object()) is False
    assert _needs_baseline_backfill(sendable=True, baseline=None) is True
    assert _needs_baseline_backfill(sendable=False, baseline=None) is False
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_baseline_stamp_decision.py -v`
Expected: FAIL — `ImportError: cannot import name '_should_stamp_on_save'`

- [ ] **Step 3: 순수 헬퍼 구현**

`app/services/notify_service.py` 모듈 레벨(파일 끝 또는 다른 헬퍼 근처)에 추가:

```python
def _should_stamp_on_save(*, before_sendable: bool, after_sendable: bool) -> bool:
    """알림 저장 시 발송 기준선을 (재)스탬프할지. false→true 전환에서만 True."""
    return (not before_sendable) and after_sendable


def _needs_baseline_backfill(*, sendable: bool, baseline: object | None) -> bool:
    """기동 업그레이드 보정: 이미 sendable인데 기준선이 비어 있으면 True."""
    return sendable and baseline is None
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_baseline_stamp_decision.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: 저장 핸들러에 스탬프 적용**

`app/routers/settings.py`의 `put_settings`(`:44-59`)를 수정. import 블록 상단에 추가:

```python
from datetime import datetime, timezone

from app.services.notify_service import _should_stamp_on_save
```

`put_settings` 본문을 다음으로 교체:

```python
    _check_category(category)
    mgr = get_settings_manager()

    before_sendable = False
    if category == "notification":
        before_sendable = (await mgr.get_notification(group.group_id)).is_sendable

    await mgr.set_values(
        group.group_id,
        category,
        [item.model_dump() for item in payload.items],
    )

    if category == "notification":
        after = await mgr.get_notification(group.group_id)
        if _should_stamp_on_save(
            before_sendable=before_sendable, after_sendable=after.is_sendable
        ):
            now_iso = datetime.now(timezone.utc).isoformat()
            await mgr.set_values(
                group.group_id,
                "notification",
                [{"key": "notify_baseline_at", "value": now_iso, "value_type": "string"}],
            )

    if category == "polling" and app_settings.SCHEDULER_ENABLED:
        await apply_pending_analysis_schedule()
    return await mgr.list_for_api(group.group_id, category)
```

- [ ] **Step 6: 회귀 + 구문 점검**

Run: `python -c "import app.routers.settings" && pytest tests/test_baseline_stamp_decision.py -v`
Expected: import 오류 없음, 테스트 PASS

- [ ] **Step 7: 커밋**

```bash
git add app/services/notify_service.py app/routers/settings.py tests/test_baseline_stamp_decision.py
git commit -m "feat: 알림 sendable false→true 전환 시 발송 기준선 스탬프"
```

---

## Task 6: 기동 시 업그레이드 1회 보정

**Files:**
- Modify: `app/services/notify_service.py` (보정 함수 추가)
- Modify: `app/main.py:27-36` (lifespan에서 호출)

기존 배포(이미 sendable + 기준선 null)가 본 기능을 받을 때, 기동 시 활성 그룹을 순회하며 기준선이 비어 있으면 `now()`로 1회 스탬프한다. 이후 backlog는 보류되고 신규만 발송된다.

- [ ] **Step 1: 보정 함수 구현**

`app/services/notify_service.py` 모듈 레벨에 추가:

```python
async def backfill_notify_baselines() -> int:
    """기동 보정: sendable인데 기준선이 빈 활성 그룹에 now()를 스탬프한다.

    업그레이드 직후 기존 backlog가 한꺼번에 발송되는 것을 막는다.
    반환: 스탬프한 그룹 수.
    """
    from datetime import datetime, timezone

    from app.control_db import get_sessionmaker
    from app.models.control.group import Group
    from app.services.settings_manager import get_settings_manager

    sf = get_sessionmaker()
    mgr = get_settings_manager()
    async with sf() as session:
        groups = list(
            (await session.execute(select(Group).where(Group.is_active.is_(True))))
            .scalars()
            .all()
        )

    stamped = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    for group in groups:
        notif = await mgr.get_notification(group.group_id)
        if _needs_baseline_backfill(
            sendable=notif.is_sendable, baseline=notif.notify_baseline_at
        ):
            await mgr.set_values(
                group.group_id,
                "notification",
                [{"key": "notify_baseline_at", "value": now_iso, "value_type": "string"}],
            )
            stamped += 1
    return stamped
```

(`select`는 `notify_service.py` 상단에서 이미 import됨 — 확인만.)

- [ ] **Step 2: lifespan에서 호출**

`app/main.py`의 `lifespan`(`:27-36`)을 수정. `ensure_control_schema()` 다음 줄에 추가:

```python
    from app.services.notify_service import backfill_notify_baselines

    await backfill_notify_baselines()
```

- [ ] **Step 3: 구문/import 점검**

Run: `python -c "import app.main"`
Expected: 오류 없음

- [ ] **Step 4: 전체 테스트 실행**

Run: `pytest tests/ -v`
Expected: PASS (전체 그린)

- [ ] **Step 5: 커밋**

```bash
git add app/services/notify_service.py app/main.py
git commit -m "feat: 기동 시 발송 기준선 업그레이드 1회 보정"
```

---

## Self-Review (작성자 체크 결과)

**Spec coverage:**
- 1. 데이터 모델 → Task 1 ✅
- 2. 기준선 스탬프(false→true) → Task 5 ✅ (재스탬프 정책 포함)
- 3. 게이트(두 경로) → Task 3(즉시), Task 4(배치) ✅
- 4. 안전장치 A(null=보류) → Task 2 `_passes_group_baseline` ✅
- 4. 안전장치 B(업그레이드 보정) → Task 6 ✅
- 5. 가시화(skip job_log) → Task 3 message="그룹 baseline 이전" ✅ (배치 경로는 candidate 필터로 제외되며 기존 배치 요약 로그가 남음)
- 5. 선택적 발송 → 기존 수동 버튼 재사용(비목표, 변경 없음) ✅
- 테스트 → Task 1/2/5 단위 테스트 ✅

**Placeholder scan:** 없음. 모든 코드 스텝에 실제 코드 포함.

**Type consistency:** `_passes_group_baseline(baseline, published_at)`, `_should_stamp_on_save(before_sendable, after_sendable)`, `_needs_baseline_backfill(sendable, baseline)`, `backfill_notify_baselines()`, `NotificationSettings.notify_baseline_at`, 설정 키 `notify_baseline_at` — 전 태스크에서 동일 명칭 사용 확인.
