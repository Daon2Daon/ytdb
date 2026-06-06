# 그룹 발송 기준선(notify_baseline_at) 설계

작성일: 2026-06-06

## 배경 / 문제

설정 · Notification에서 발송 모드를 `immediate`로 설정하면, 과거에 분석됐지만
미발송 상태(`notified_at IS NULL`)로 쌓여 있던 영상들이 한꺼번에 발송된다.
서비스 개시 시점에 대량 메시지가 한 번에 나가 사용자 불편을 초래한다.

원인: 채널 단위에는 `channel.notify_from`(알림을 켠 순간 이후 게시된 영상만 발송)
이라는 기준선이 있으나, **그룹 알림 설정 레벨에는 동등한 기준선이 없다.**
채널이 먼저 켜져 backlog가 쌓인 뒤 발송 기능을 뒤늦게 켜면, 자동 발송 경로가
backlog 전체를 풀어 버린다.

관련 기존 코드:
- 채널 기준선: `_passes_notify_baseline()` — `app/services/monitor_service.py:368`
- 자동 발송 경로 ①(분석 직후 즉시발송): `_notify_after_analysis()` — `app/services/monitor_service.py:378`
- 자동 발송 경로 ②(틱 배치): `notify_pending_batch()` — `app/services/notify_service.py:314`
- 설정 저장/로드: `settings_manager.get_notification()` — `app/services/settings_manager.py:186`
- 수동 발송 API: `POST /{video_pk}/notify` — `app/routers/videos.py:311`

## 목표

발송 기능을 활성화한 **이후 게시된(`published_at`) 콘텐츠만** 자동 발송한다.
활성화 이전의 backlog는 자동 발송에서 제외하되, 데이터는 보존하여 필요 시
기존 수동 "Telegram 발송" 버튼으로 선택 발송할 수 있게 한다.

## 비목표

- backlog를 선택적으로 푸는 신규 UI/엔드포인트는 만들지 않는다.
  기존 미발송 필터 + 수동 발송 버튼으로 충분하다.
- 영상별 발송 상태 컬럼(`notify_status` 등)은 도입하지 않는다.
  (마이그레이션·상태 이원화 비용 대비 이득 없음.)

## 설계

### 1. 데이터 모델

- notification 설정 카테고리에 키 `notify_baseline_at` 추가.
  - `value_type`: `"string"` (ISO 8601, UTC, 예: `2026-06-06T12:00:00+00:00`)
  - 기본값: 미설정(null).
- `NotificationSettings`(`app/services/settings_types.py`)에
  `notify_baseline_at: Optional[datetime] = None` 필드 추가.
- `settings_manager.get_notification()`에서 해당 키를 파싱해 채운다.
  파싱 실패/빈 값은 `None`으로 처리.

### 2. 기준선 스탬프 (트리거)

- 알림 설정을 저장할 때 `is_sendable`(= `enabled AND bot_token AND chat_ids`)이
  **false → true 로 전환**되면 `notify_baseline_at = now(UTC)`로 기록한다.
  채널의 OFF→ON 시 `notify_from`을 찍는 것과 동일한 의미다.
- 저장 핸들러에서 저장 전 `get_notification()`(이전 상태)과 새로 저장될 상태의
  `is_sendable`을 비교해 전환을 판정한다.
- **재활성 정책**: 매 false→true 전환마다 재스탬프한다. 비활성 구간에 게시된
  영상은 자동 발송에서 제외되지만, `notified_at IS NULL`로 남아 미발송 목록에서
  수동 발송할 수 있다.

### 3. 게이트 (두 자동 발송 경로)

두 자동 경로에 그룹 기준선 검사를 추가한다. 채널 기준선과 **둘 다** 통과해야
발송한다(실효 기준 = 둘 중 늦은 시각).

- `_notify_after_analysis()`: `published_at`이 그룹 기준선 이전이면 발송하지 않고
  skip job_log를 남긴다(사유: 그룹 baseline 이전).
- `notify_pending_batch()`: candidate 필터에 동일 조건을 추가한다.

backlog 영상은 `notified_at`을 건드리지 않는다 — **자동 발송에서만 제외**되고
데이터는 보존된다.

### 4. 안전장치 (필수)

#### A. 안전측 기본값 — "발송 활성 + 기준선 null = 자동발송 보류"

기존 `_passes_notify_baseline(None, ...)`은 `None`을 "전부 발송"으로 해석한다
(레거시 채널 호환). 그러나 본 기능의 목적은 정반대(flood 차단)이므로,
**그룹 기준선에는 이 규칙을 그대로 쓰지 않는다.**

규칙:
- 그룹 알림이 sendable이고 `notify_baseline_at`이 `None`이면 → **자동 발송 보류**
  (gate = False). 트리거 누락 등으로 기준선이 비어 있어도 flood가 발생하지 않게 한다.
- sendable이 아니면 어차피 발송 안 함(기존 `is_sendable` 가드).

구현: 그룹 전용 게이트 헬퍼를 두거나, 호출부에서 `notify_baseline_at is None`을
명시적으로 보류 처리한다. 채널용 `_passes_notify_baseline`의 `None→True`
동작은 변경하지 않는다(채널 레거시 호환 유지).

#### B. 업그레이드 1회 보정

기존에 이미 발송을 켜둔 채 backlog가 쌓인 배포가 본 기능을 받으면
`notify_baseline_at`은 null이다. 안전장치 A에 의해 자동 발송이 보류되지만,
이는 "정상 운영 중인 그룹의 신규 발송까지 멈추는" 부작용을 낳을 수 있다.
이를 막기 위해 기동/마이그레이션 시 **1회 보정**한다:

- 그룹 알림이 이미 sendable인데 `notify_baseline_at`이 null이면 `now(UTC)`로 스탬프.
- 이 보정 이후부터는 신규(게시 시각이 보정 시점 이후)만 자동 발송되고,
  기존 backlog는 보류된다. 의도한 동작.

### 5. 가시화 (권장)

게이트로 인해 skip된 건은 job_log에 사유를 남겨("그룹 baseline 이전") 무음
제외로 인한 혼란을 줄인다. (경로 ②의 배치 필터에서 제외된 건수는 최소한
집계 로그로 남기는 것을 권장.)

## 데이터 흐름

```
[설정 저장] is_sendable false→true ──► notify_baseline_at = now(UTC)
[기동/마이그레이션] sendable & baseline null ──► notify_baseline_at = now(UTC)  (1회 보정)

[분석 완료] _notify_after_analysis
    ├─ is_sendable? ──no──► skip
    ├─ 채널 notify_from 통과? ──no──► skip
    ├─ 그룹 baseline 통과?(null이면 보류) ──no──► skip(log: 그룹 baseline 이전)
    └─ yes ──► 발송, notified_at 기록

[틱 배치] notify_pending_batch
    candidates = done & notified_at NULL & 채널 notify_enabled
                 & 채널 notify_from 통과 & 그룹 baseline 통과(null이면 제외)

[수동 발송] POST /{video_pk}/notify  ──► 기준선 무시, 무조건 발송(기존 동작 유지)
```

## 영향 범위 (변경 파일)

- `app/services/settings_types.py` — 필드 추가
- `app/services/settings_manager.py` — 파싱
- `app/services/default_settings.py` — 키 기본 정의(필요 시)
- 알림 설정 저장 핸들러(`app/routers/settings.py`) — false→true 스탬프
- `app/services/monitor_service.py` — `_notify_after_analysis` 게이트 + 그룹 게이트 헬퍼
- `app/services/notify_service.py` — `notify_pending_batch` candidate 필터
- 기동/마이그레이션 보정 지점 — 1회 보정(B)
- 수동 발송 경로(`videos.py`)는 **변경 없음**(기존 동작 유지)

## 테스트

- 그룹 게이트 헬퍼 단위 테스트:
  - 기준선 이전 published → 보류, 이후 → 통과.
  - 기준선 null + sendable → 보류(안전장치 A).
- `_notify_after_analysis`: 기준선 이전=skip(+log), 이후=발송.
- `notify_pending_batch`: 기준선 이전 backlog 제외, 이후만 배치.
- 트리거: is_sendable false→true 저장 시 스탬프 기록 / 이미 설정 시 보존.
- 업그레이드 보정(B): sendable & null → now() 스탬프.
- 수동 발송: 기준선과 무관하게 발송되고 `notified_at` 기록(회귀 방지).

## 미해결/결정 사항

- 없음. (재활성 재스탬프, null=보류, 업그레이드 보정 모두 위에서 확정.)
