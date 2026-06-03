# 채널 알림 기준 시점(notify_from) 설계

- 작성일: 2026-06-03
- 상태: 설계 승인됨

## 배경 / 문제

채널을 추가하고 `window_hours`를 길게 잡거나 backfill을 켜면, 수집된 과거 영상이 전부 분석되고 각 영상이 [`_notify_after_analysis`](app/services/monitor_service.py:357)에서 발송된다. 현재 발송 게이트는 ① 그룹 알림 설정(`is_sendable`) ② 채널 `notify_enabled` 뿐이고 **시점 기반 게이팅이 없다**. 게다가 새 채널은 기본 `notify_enabled=true`라 추가만 해도 백로그가 쏟아진다(알림 폭주).

## 목표

분석/저장은 백로그 포함 전부 진행하되, **알림은 "알림을 켠 시점 이후 유튜브에 업로드된 영상"만** 발송한다.

## 결정 (승인됨)

- 비교 기준: **`video.published_at ≥ channel.notify_from`** (게시 시각 ≥ 알림 켠 시각).
- `notify_from`(기준)은 **알림이 켜진 순간**의 시각. `published_at`은 각 영상의 유튜브 업로드 시각. 둘은 서로 다른 값이며, 게이트는 둘을 비교한다.
- `notify_from = NULL`이면 "기준 없음 = 전부 발송"(기존 채널 호환, 동작 변화 없음).
- **수동 발송**(`POST /videos/{pk}/notify`)은 기준을 무시한다(사용자 명시 행위).

## 비목표

- UI 보강(채널 행에 "알림 시작" 표시, "지금부터 알림" 재설정 버튼)은 v2로 미룬다.
- 기존 채널에 대한 소급 baseline 설정은 하지 않는다(NULL=전부 발송 유지). 필요 시 알림 OFF→ON 토글로 baseline을 얻는다.

## 데이터 모델

`channels`에 컬럼 추가:
```
notify_from TIMESTAMPTZ NULL  -- 이 시각 이후 게시된 영상만 자동 발송. NULL=전부 발송.
```

## 기준 시점 라이프사이클 (자동 관리)

- 채널 **생성 시**: `notify_enabled`가 true(기본값)면 `notify_from = now()`.
- 채널 **PATCH로 알림 OFF→ON 전환** 시: `notify_from = now()`.
- ON→OFF: `notify_from` 그대로 둠(다시 켜면 재설정).

## 발송 게이트

[`_notify_after_analysis`](app/services/monitor_service.py:357)에 순수 판단 헬퍼를 추가하고 적용:
```python
def _passes_notify_baseline(notify_from, published_at) -> bool:
    if notify_from is None:
        return True
    return published_at >= notify_from
```
- 게이트가 False면 발송하지 않고 로그 SKIP("기준 시점 이전 영상")을 남긴다. 분석·저장·`notified_at` 미설정 상태 유지(추후 수동 발송 가능).
- 기존 게이트(`is_sendable`, `notify_enabled`)는 그대로.

## 스키마 마이그레이션 (자동·자가치유)

`ensure_schema`가 테이블 생성 후 **멱등 컬럼 패치**를 수행한다:
```sql
ALTER TABLE {schema}.channels ADD COLUMN IF NOT EXISTS notify_from timestamptz;
```
- 새 그룹 스키마: 모델 metadata로 컬럼 포함 생성.
- 기존 스키마(telco_monitoring·marketing_trend 등): 다음 접근 시(백엔드 재시작 후 `ensure_schema` 재실행) 멱등 ALTER로 자동 추가.
- `group_session`은 항상 `ensure_schema`를 먼저 실행하므로, 컬럼 참조 쿼리 전에 컬럼이 보장된다.

## 스키마 노출

`ChannelOut`에 `notify_from: Optional[datetime]` 추가(프론트가 추후 표시할 수 있도록, 핵심 동작과 무관하게 정보 제공).

## 테스트

- `_passes_notify_baseline` 순수 단위 테스트: NULL→True, published_at ≥/< notify_from → True/False, 동일 시각 경계.

## 범위

백엔드만: `app/models/pg/channel.py`, `app/services/db_engine.py`(ensure_schema 패치), `app/routers/channels.py`(생성/PATCH), `app/services/monitor_service.py`(게이트), `app/schemas/channel.py`(노출), 테스트. 프론트 핵심 변경 없음.
