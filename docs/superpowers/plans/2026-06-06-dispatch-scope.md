# 발송 범위 선택(dispatch_scope) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** scheduled 발송 모드에서 "활성화 이후 게시분만" / "과거 분석분 포함 전체 순차발송"을 그룹별로 선택할 수 있게 한다.

**Architecture:** notification에 `dispatch_scope ∈ {after_activation, all}` 설정을 추가하고, `send_mode == "scheduled" AND dispatch_scope == "all"`일 때만 `notify_pending_batch`의 그룹 baseline 게이트를 건너뛴다. 채널 `notify_from` 게이트와 페이스(`scheduled_max_per_run`/`wait_between_messages_sec`), 정렬(오래된 순)은 기존 그대로 재사용한다.

**Tech Stack:** Python, FastAPI, SQLAlchemy(async), pytest. 프론트엔드는 선언적 `defs.ts` + `showIf` 패턴(React/TS).

설계 문서: `docs/superpowers/specs/2026-06-06-dispatch-scope-design.md`

---

## File Structure

- `app/services/settings_types.py` — `NotificationSettings.dispatch_scope` 필드.
- `app/services/settings_manager.py` — `get_notification` 파싱(+폴백).
- `app/services/default_settings.py` — notification 기본 키.
- `app/services/notify_service.py` — `_should_apply_group_baseline` 헬퍼 + `notify_pending_batch` 필터.
- `frontend/src/settings/defs.ts` — 발송 범위 select.
- `tests/` — 헬퍼·파싱 단위 테스트.

---

## Task 1: NotificationSettings에 dispatch_scope 필드 추가

**Files:**
- Modify: `app/services/settings_types.py:39` (message_detail 아래)
- Modify: `app/services/settings_manager.py:236` (get_notification 반환부)
- Modify: `app/services/default_settings.py` (notification 리스트)
- Test: `tests/test_dispatch_scope_parse.py`

- [ ] **Step 1: 실패하는 파싱 테스트 작성**

`tests/test_dispatch_scope_parse.py`:

```python
"""notification dispatch_scope 파싱/폴백 검증."""

from app.services.settings_manager import _normalize_dispatch_scope


def test_default_is_after_activation():
    assert _normalize_dispatch_scope(None) == "after_activation"
    assert _normalize_dispatch_scope("") == "after_activation"


def test_valid_values_pass_through():
    assert _normalize_dispatch_scope("after_activation") == "after_activation"
    assert _normalize_dispatch_scope("all") == "all"


def test_unknown_falls_back_to_after_activation():
    assert _normalize_dispatch_scope("garbage") == "after_activation"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_dispatch_scope_parse.py -v`
Expected: FAIL — `ImportError: cannot import name '_normalize_dispatch_scope'`

- [ ] **Step 3: 정규화 헬퍼 구현**

`app/services/settings_manager.py`의 기존 `_as_int`/`_as_float`/`_as_dt` 헬퍼 근처에 추가:

```python
def _normalize_dispatch_scope(v: Any) -> str:
    """dispatch_scope 정규화. 유효값만 통과, 그 외는 안전측 기본값."""
    s = str(v or "").strip()
    return s if s in ("after_activation", "all") else "after_activation"
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_dispatch_scope_parse.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: NotificationSettings 필드 추가**

`app/services/settings_types.py`, `message_detail` 줄(`:39`) 아래에 추가:

```python
    # 발송 범위: scheduled 모드에서만 적용.
    # after_activation=활성화 이후 게시분만, all=과거 분석분 포함 전체 순차 발송.
    dispatch_scope: str = "after_activation"  # after_activation | all
```

- [ ] **Step 6: get_notification 파싱**

`app/services/settings_manager.py`의 `get_notification` 반환부, `notify_baseline_at=...` 줄(`:236`) 다음에 추가:

```python
            dispatch_scope=_normalize_dispatch_scope(d.get("dispatch_scope")),
```

- [ ] **Step 7: 기본 키 정의**

`app/services/default_settings.py`의 `"notification"` 리스트, `notify_baseline_at` 항목 뒤에 추가:

```python
        {"key": "dispatch_scope", "value": "after_activation", "value_type": "string"},
```

- [ ] **Step 8: 전체 설정 테스트 실행**

Run: `pytest tests/test_dispatch_scope_parse.py tests/test_notification_settings_defaults.py tests/test_default_settings.py -v`
Expected: PASS

- [ ] **Step 9: 커밋**

```bash
git add app/services/settings_types.py app/services/settings_manager.py app/services/default_settings.py tests/test_dispatch_scope_parse.py
git commit -m "feat: NotificationSettings에 dispatch_scope 발송 범위 필드"
```

---

## Task 2: 그룹 baseline 적용 판정 헬퍼

**Files:**
- Modify: `app/services/notify_service.py` (모듈 레벨 헬퍼 추가)
- Test: `tests/test_should_apply_group_baseline.py`

- [ ] **Step 1: 실패하는 단위 테스트 작성**

`tests/test_should_apply_group_baseline.py`:

```python
"""그룹 baseline 게이트 적용 판정 검증.

scheduled+all 조합에서만 게이트를 끈다(False). 그 외는 모두 적용(True).
"""

from app.services.notify_service import _should_apply_group_baseline


def test_immediate_after_activation_applies():
    assert _should_apply_group_baseline("immediate", "after_activation") is True


def test_immediate_all_still_applies():
    assert _should_apply_group_baseline("immediate", "all") is True


def test_scheduled_after_activation_applies():
    assert _should_apply_group_baseline("scheduled", "after_activation") is True


def test_scheduled_all_skips():
    assert _should_apply_group_baseline("scheduled", "all") is False
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_should_apply_group_baseline.py -v`
Expected: FAIL — `ImportError: cannot import name '_should_apply_group_baseline'`

- [ ] **Step 3: 헬퍼 구현**

`app/services/notify_service.py` 모듈 레벨(다른 순수 헬퍼 `_should_stamp_on_save` 근처)에 추가:

```python
def _should_apply_group_baseline(send_mode: str, dispatch_scope: str) -> bool:
    """그룹 발송 기준선(notify_baseline_at) 게이트를 적용할지.

    scheduled + all 조합에서만 게이트를 끈다(backlog 포함). 그 외(immediate,
    scheduled+after_activation)는 모두 게이트를 적용해 현행 동작을 유지한다.
    """
    return not (send_mode == "scheduled" and dispatch_scope == "all")
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_should_apply_group_baseline.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 커밋**

```bash
git add app/services/notify_service.py tests/test_should_apply_group_baseline.py
git commit -m "feat: 그룹 baseline 적용 판정 헬퍼(scheduled+all만 해제)"
```

---

## Task 3: notify_pending_batch에 조건부 게이트 적용

**Files:**
- Modify: `app/services/notify_service.py:350-355`

`notify_pending_batch`의 candidate 필터에서, 그룹 baseline 게이트를 조건부로 적용한다. 채널 `notify_from` 게이트는 항상 유지.

- [ ] **Step 1: candidate 필터 수정**

`app/services/notify_service.py`의 candidate 컴프리헨션(`:350-355`)을 다음으로 교체:

```python
    apply_group = _should_apply_group_baseline(notif.send_mode, notif.dispatch_scope)
    candidates = [
        (v, a, ch)
        for (v, a, ch) in rows
        if _passes_notify_baseline(ch.notify_from, v.published_at)
        and (
            not apply_group
            or _passes_group_baseline(notif.notify_baseline_at, v.published_at)
        )
    ]
```

- [ ] **Step 2: import/구문 점검**

Run: `python -c "import app.services.notify_service"`
Expected: 오류 없음 (`_should_apply_group_baseline`은 동일 모듈 함수)

- [ ] **Step 3: 회귀 테스트 실행**

Run: `pytest tests/ -k "notify or schedule or baseline or dispatch" -v`
Expected: PASS

- [ ] **Step 4: 커밋**

```bash
git add app/services/notify_service.py
git commit -m "feat: notify_pending_batch에 dispatch_scope 조건부 baseline 적용"
```

---

## Task 4: 프론트엔드 발송 범위 select 추가

**Files:**
- Modify: `frontend/src/settings/defs.ts:66` (scheduled_times 항목 부근)

- [ ] **Step 1: defs.ts에 발송 범위 항목 추가**

`frontend/src/settings/defs.ts`의 notification 블록에서 `scheduled_times` 항목 바로 다음 줄에 추가:

```ts
    { key: 'dispatch_scope', label: '발송 범위', type: 'select',
      options: ['after_activation', 'all'],
      help: 'after_activation=활성화 이후 게시분만, all=과거 분석분 포함 전체를 오래된 순으로 순차 발송',
      showIf: { key: 'send_mode', equals: 'scheduled' } },
```

- [ ] **Step 2: 타입체크/빌드 점검**

Run: `cd frontend && npm run build`
Expected: 빌드 성공(타입 오류 없음)

참고: `select` 타입과 `showIf`는 기존 `parse_mode`/`scheduled_times` 항목이 이미 쓰는 검증된 필드라 추가 컴포넌트 변경 불필요.

- [ ] **Step 3: 프론트 단위 테스트(존재 시) 실행**

Run: `cd frontend && npm test -- --run`
Expected: PASS (기존 convert 테스트 깨지지 않음)

- [ ] **Step 4: 커밋**

```bash
git add frontend/src/settings/defs.ts
git commit -m "feat: Notification 설정에 발송 범위(dispatch_scope) select 추가"
```

---

## Self-Review (작성자 체크 결과)

**Spec coverage:**
- 1. 데이터 모델(dispatch_scope 필드/파싱/기본키) → Task 1 ✅
- 2. 조건부 게이트 판정 헬퍼 → Task 2 ✅
- 3. 배치 경로 게이트 적용 → Task 3 ✅
- 4. 프론트엔드 select → Task 4 ✅
- 비목표(채널 notify_from 유지) → Task 3에서 `_passes_notify_baseline` 무조건 유지 ✅
- 테스트(헬퍼 4조합, 파싱 폴백) → Task 1/2 ✅

**Placeholder scan:** 없음. 모든 코드 스텝에 실제 코드 포함.

**Type consistency:** `_normalize_dispatch_scope(v) -> str`, `_should_apply_group_baseline(send_mode, dispatch_scope) -> bool`, `NotificationSettings.dispatch_scope`, 설정 키 `dispatch_scope`, 값 `after_activation`/`all` — 전 태스크에서 동일 명칭/값 사용 확인.
