# 발송 범위 선택(dispatch_scope) 설계

작성일: 2026-06-06

## 배경 / 문제

직전에 추가한 그룹 발송 기준선(`notify_baseline_at`)으로 "발송 활성화 이후 게시된
영상만 자동 발송"이 정상 동작한다. 그러나 일부 그룹은 **과거에 분석된 영상까지
오래된 순으로 순차적으로 받아보고 싶은** 니즈가 있다.

따라서 그룹별로 다음 둘 중 하나를 선택할 수 있어야 한다:
- 활성화 이후 게시분만 발송(현행, flood 방지)
- 과거 분석분 포함 전체를 오래된 순으로 순차 발송

## 핵심 결정

"전체 순차발송"은 본질적으로 **페이스가 있는 scheduled 발송의 변형**이다.
immediate에 "전체"를 적용하면 결국 한꺼번에 나가 flood가 되므로 의미가 없다.
따라서 **발송 범위 선택은 `send_mode == "scheduled"`일 때만 적용**한다.

페이스는 기존 notification 설정 `scheduled_max_per_run`(회당 최대 발송 건수)과
`wait_between_messages_sec`(건별 대기)를 그대로 따른다. 순서는 기존
`notify_pending_batch`가 이미 `published_at ASC`(오래된 순)로 정렬한다.

## 목표

- notification에 발송 범위 설정 `dispatch_scope ∈ {after_activation, all}` 추가.
- `send_mode == "scheduled" AND dispatch_scope == "all"`일 때만 그룹 baseline 게이트를
  건너뛰어 backlog를 포함해 예약 시각마다 오래된 순으로 순차 발송한다.
- 그 외 모든 경우(immediate, scheduled+after_activation)는 현행 동작을 그대로 유지한다.

## 비목표

- 채널 단위 `notify_from` 게이트는 "전체" 모드에서도 **유지**한다. 이는 "이 채널은
  X 시점부터"라는 사용자의 명시적 채널 설정이므로 존중한다. (그룹 차원의 자동
  flood 방지인 `notify_baseline_at`만 해제 대상.)
- immediate 모드에 "전체" 옵션을 노출하지 않는다(flood 방지).
- backlog 전용 별도 페이스 설정은 만들지 않는다(기존 배치 설정 재사용).

## 설계

### 1. 데이터 모델

- notification 설정 카테고리에 키 `dispatch_scope` 추가.
  - `value_type`: `"string"`, 값: `"after_activation"` | `"all"`
  - 기본값: `"after_activation"` (현행 동작 보존)
- `NotificationSettings`(`app/services/settings_types.py`)에
  `dispatch_scope: str = "after_activation"` 필드 추가.
- `settings_manager.get_notification()`에서 파싱. 알 수 없는 값은
  `"after_activation"`으로 폴백(안전측: 기본은 backlog 제외).

### 2. 조건부 게이트 판정 (순수 헬퍼)

`app/services/notify_service.py`에 순수 함수 추가:

```python
def _should_apply_group_baseline(send_mode: str, dispatch_scope: str) -> bool:
    """그룹 발송 기준선(notify_baseline_at) 게이트를 적용할지.

    scheduled + all 조합에서만 게이트를 끈다(backlog 포함). 그 외(immediate,
    scheduled+after_activation)는 모두 게이트를 적용해 현행 동작을 유지한다.
    """
    return not (send_mode == "scheduled" and dispatch_scope == "all")
```

### 3. 배치 경로 게이트 적용

`app/services/notify_service.py`의 `notify_pending_batch` candidate 필터를 수정한다.

- 채널 `notify_from` 게이트(`_passes_notify_baseline`): **항상 적용**.
- 그룹 `notify_baseline_at` 게이트(`_passes_group_baseline`):
  `_should_apply_group_baseline(notif.send_mode, notif.dispatch_scope)`가
  `True`일 때만 적용.

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

immediate 경로의 quiet-recovery 호출은 `send_mode == "immediate"`이므로
`apply_group == True` → 그룹 게이트 유지(현행 동작). `_notify_after_analysis`는
scheduled에서 이미 즉시발송을 보류하므로 **변경 없음**.

### 4. 프론트엔드

`frontend/src/settings/defs.ts`의 notification 블록, `scheduled_times` 항목 부근에
발송 범위 select를 추가한다. `send_mode == "scheduled"`일 때만 노출.

```ts
{ key: 'dispatch_scope', label: '발송 범위', type: 'select',
  options: ['after_activation', 'all'],
  help: 'after_activation=활성화 이후 게시분만, all=과거 분석분 포함 전체를 오래된 순으로 순차 발송',
  showIf: { key: 'send_mode', equals: 'scheduled' } },
```

## 동작 요약

| send_mode | dispatch_scope | 그룹 baseline | 채널 notify_from | backlog | 페이스 |
|---|---|---|---|---|---|
| immediate | (무시) | 적용 | 적용 | 제외 | 즉시 |
| scheduled | after_activation | 적용 | 적용 | 제외 | 예약 시각, max_per씩 |
| scheduled | all | **해제** | 적용 | **포함** | 예약 시각, max_per씩, 오래된 순 |

## 영향 범위 (변경 파일)

- `app/services/settings_types.py` — `dispatch_scope` 필드 추가
- `app/services/settings_manager.py` — `get_notification` 파싱(+폴백)
- `app/services/default_settings.py` — notification 기본 키
- `app/services/notify_service.py` — `_should_apply_group_baseline` 헬퍼 + `notify_pending_batch` 필터 수정
- `frontend/src/settings/defs.ts` — 발송 범위 select 추가
- `app/services/monitor_service.py` — **변경 없음**(즉시 경로는 scheduled에서 이미 보류)
- `app/routers/settings.py` — **변경 없음**(generic set_values로 저장)

## 테스트

- `_should_apply_group_baseline` 4조합 단위 테스트:
  - immediate+after_activation → True
  - immediate+all → True
  - scheduled+after_activation → True
  - scheduled+all → False
- `dispatch_scope` 파싱: 기본값("after_activation"), 유효값("all"), 알 수 없는 값 폴백.
- `notify_pending_batch` 동작:
  - scheduled+all: 그룹 baseline 이전 backlog가 candidate에 포함된다.
  - scheduled+after_activation: backlog 제외(현행).
  - 모든 경우 채널 notify_from 이전 영상은 제외(채널 게이트 유지).

## 미해결/결정 사항

- 없음.
