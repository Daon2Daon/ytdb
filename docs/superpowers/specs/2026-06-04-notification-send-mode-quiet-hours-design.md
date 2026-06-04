# 알림 발송 고도화 설계 (발송모드 · 야간 제한 · 보조 기능)

작성일: 2026-06-04
대상 앱: ytdb (멀티그룹 YouTube 모니터)
참고 앱: youtube_monitor (단일 사용자 버전)

## 배경 / 목적

youtube_monitor의 "알림 발송" 설정을 ytdb에 이식한다. 핵심 요구:

1. **발송 모드**: 분석 즉시 발송(immediate) vs 예약 발송(scheduled)
2. **야간 알림 제한**: 지정 시간대 Telegram 발송 중단
3. 보조 기능(아래 결정에 따라 선정): 예약발송 회당 제한, 야간 보정 발송,
   그룹별 타임존, 저신뢰도 배지

ytdb는 youtube_monitor와 구조가 다르므로 그대로 복붙하지 않고 ytdb 패턴에 맞춰 이식한다.

## ytdb vs youtube_monitor 구조 차이 (이식 시 핵심)

| 항목 | youtube_monitor | ytdb |
|---|---|---|
| 멀티테넌시 | 단일 사용자 | **멀티그룹** — 모든 설정이 `(group_id, category, key, value)`로 control DB 저장 |
| 설정 UI | 수제 전용 페이지(카드/칩 에디터) | **범용 폼** `SETTING_DEFS` → `SettingsForm.tsx` 렌더 |
| 알림 현황 | send_mode/quiet/예약 모두 존재 | `enabled, bot_token, chat_ids, parse_mode`만. 분석 직후 즉시 발송 |
| 스케줄러 | 시각별 동적 Cron(`youtube_notify_HHMM`) | **AsyncIOScheduler 단일 루프 + 매 1분 `digest_tick`**이 활성 그룹 순회 |
| 타임존 | KST 고정 | 그룹별 timezone 보유(digest에 이미 존재) |

## 확정된 결정 사항

- **UI**: 범용 폼(`SETTING_DEFS`) 확장 — 새 필드 타입 + 조건부 표시. 수제 페이지 신설 안 함.
- **보조 기능**: 4종 모두 채택 — 예약발송 회당 제한, 야간 보정 발송, 그룹별 타임존, 저신뢰도 배지.
- **즉시발송 + 야간**: 야간엔 보류(`notified_at=null`), 제한 종료 후 자동 보정 발송.
- **발송 메커니즘**: youtube_monitor의 동적 Cron 대신 **ytdb 틱(매 1분, 그룹 순회)** 방식.
- **scheduled 모드에서 예약 시각이 야간 제한과 겹칠 때**: 해당 회차 skip.

## 1. 데이터 모델 (`notification` 카테고리 키 추가)

모두 그룹별 설정. 기존 key/value 저장 구조 그대로 사용(스키마 변경 없음).

| key | value_type | 기본값 | 설명 |
|---|---|---|---|
| `send_mode` | string | `immediate` | `immediate` \| `scheduled` |
| `scheduled_times` | json(list[str]) | `[]` | 예약 시각 HH:MM (최대 10) |
| `scheduled_max_per_run` | int | `5` | 회당 최대 발송 건수 (1~50) |
| `wait_between_messages_sec` | int | `30` | 건별 대기(초, 0~600) |
| `quiet_hours_enabled` | bool | `false` | 야간 제한 활성 |
| `quiet_hours_start` | string | `22:00` | 제한 시작 HH:MM |
| `quiet_hours_end` | string | `07:00` | 제한 종료 HH:MM (익일 가능) |
| `timezone` | string | `Asia/Seoul` | 야간·예약 판정 기준 |
| `low_confidence_threshold` | float | `0.5` | 저신뢰도 배지 임계값 (0.0~1.0) |

### `NotificationSettings` dataclass (`app/services/settings_types.py`) 확장

기존 필드(`enabled, bot_token, chat_ids, parse_mode`)에 위 9개 필드 추가.
`is_sendable` 의미는 유지(enabled + bot_token + chat_ids).

### `settings_manager.get_notification()` 확장

`get_typed`로 읽은 값을 새 필드까지 타입 변환하여 채운다. 기존 chat_ids/legacy chat_id 처리 유지.
`scheduled_times`는 `json` value_type(list[str]) 또는 콤마 문자열 모두 허용(방어적 파싱).

## 2. 백엔드 로직

### (a) 야간 제한 헬퍼 — `app/services/quiet_hours.py` (신규)

youtube_monitor `quiet_hours.py` 포팅하되 **타임존을 인자화**:

```python
def is_quiet_hours_now(enabled, start_hhmm, end_hhmm, *, tz: ZoneInfo, now=None) -> bool
def is_in_quiet_hours(start_hhmm, end_hhmm, *, tz, now=None) -> bool   # [start, end) 판정, 익일 래핑
```

- `start < end`: 같은 날 구간
- `start > end`: 자정 넘는 구간
- `start == end`: 종일 제한
- 형식 오류 시 `False`(발송 허용) — 안전 기본.

### (b) 즉시발송 경로 변경 — `monitor_service._notify_after_analysis`

분석 커밋 직후 호출되는 기존 함수에 게이트 추가(아래 순서):

1. `notif.is_sendable` 아니면 기존처럼 조용히 skip.
2. 채널 `notify_enabled`/baseline 게이트는 기존 유지.
3. **신규**: `send_mode == 'scheduled'` → 즉시 발송하지 않음. `notified_at` 유지(null),
   `job_log` SKIP("예약발송 대기"). 발송은 틱이 담당.
4. **신규**: `send_mode == 'immediate'` 이고 그룹 tz로 야간 제한 중이면 → skip.
   `notified_at` 유지(null), `job_log` SKIP("야간 보류"). 보정 발송은 틱이 담당.
5. 그 외 → 기존처럼 즉시 `notify_video(...)` + `notified_at` 기록.

저신뢰도 배지는 메시지 빌드 단계((e))에서 처리하므로 여기서 threshold만 전달.

### (c) 공통 배치 발송 — `notify_service.notify_pending_batch(...)` (신규)

```python
async def notify_pending_batch(
    group, make_session, *, max_per: int, wait_sec: int, threshold: float, log_label: str
) -> int
```

- 미발송 대상 조회: `analysis_status == 'done'` AND `notified_at IS NULL`
  AND 채널 `notify_enabled` AND `_passes_notify_baseline` 통과, `published_at` 오래된 순.
- 최대 `max_per`건, 건당 `wait_sec` 대기.
- 각 건 `notify_video` 성공 시 `notified_at = now(utc)` 기록.
- 배치 종료 후 `job_log`(NOTIFY) 1건 기록(성공/실패 카운트, 잔여 건수).
- `monitor_service`의 baseline/채널 게이트 로직과 중복되지 않도록 쿼리에 조인으로 반영.

### (d) 발송 틱 — `notify_service.run_notify_tick_once()` (신규)

`digest_service.run_digest_tick_once` 패턴 그대로. 매 1분 실행, 활성 그룹 순회:

```
for group in active_groups:
    notif = get_notification(group)
    if not notif.is_sendable: continue
    tz = ZoneInfo(notif.timezone)
    now_local = datetime.now(tz)
    quiet_now = is_quiet_hours_now(notif.quiet_hours_enabled, start, end, tz=tz, now=now_local)

    if send_mode == 'scheduled':
        if quiet_now: continue                       # 야간 겹치면 회차 skip(결정사항)
        if not _matches_scheduled_time(now_local, notif.scheduled_times): continue
        notify_pending_batch(... log_label="예약발송 회차")

    elif send_mode == 'immediate':
        if not notif.quiet_hours_enabled: continue    # 즉시모드+야간미사용 → 보류분 없음
        if quiet_now: continue                        # 아직 야간 → 대기
        notify_pending_batch(... log_label="야간 보정 발송")   # 야간 종료 후 드레인
```

- `_matches_scheduled_time`: `now_local`의 `HH:MM`이 `scheduled_times` 중 하나와 일치(분 단위).
- 보정 발송은 매 분 `max_per`씩 자연 배수되어 백로그를 점진 드레인(레이트리밋 친화).
  immediate 모드에서 보류분은 야간에만 쌓이므로, 야간 종료 후 몇 분이면 소진.
- DB 미설정 그룹은 `DBNotConfiguredError`로 skip(digest 틱과 동일).

### (e) 저신뢰도 배지 — `notify_service.build_message`

`build_message(video, analysis, threshold: float)`로 시그니처 확장.
`analysis.confidence_score is not None and confidence_score < threshold`면
제목 앞에 `⚠️ ` 접두. 기존 호출부(`notify_video`)에 threshold 전달.

### (f) 스케줄러 — `scheduler.setup_jobs()`

`run_notify_tick_once`를 1분 interval 잡으로 추가:

```python
JOB_NOTIFY_TICK = "youtube_notify_tick"
scheduler.add_job(run_notify_tick_once, trigger="interval", minutes=1,
                  id=JOB_NOTIFY_TICK, replace_existing=True,
                  max_instances=1, coalesce=True)
```

동적 Cron 등록/해제 로직 불필요(설정 변경 시 다음 틱이 자동 반영, 캐시 TTL 범위 내).

## 3. 프론트엔드 (범용 폼 확장)

### `frontend/src/settings/defs.ts`

- `FieldType`에 `'time' | 'timelist'` 추가.
- `FieldDef`에 조건부 표시 필드 추가: `showIf?: { key: string; equals: string | boolean }`.
- `SETTING_DEFS.notification`에 9개 필드 추가:
  - `send_mode` (`select`, options `['immediate','scheduled']`)
  - `scheduled_times` (`timelist`, `showIf: {key:'send_mode', equals:'scheduled'}`)
  - `scheduled_max_per_run` (`int`, showIf scheduled)
  - `wait_between_messages_sec` (`int`, showIf scheduled)
  - `quiet_hours_enabled` (`bool`)
  - `quiet_hours_start` (`time`, `showIf: {key:'quiet_hours_enabled', equals:true}`)
  - `quiet_hours_end` (`time`, showIf quiet)
  - `timezone` (`string`, 기본 표시)
  - `low_confidence_threshold` (`float`)
  - 도움말(help) 텍스트 포함.

### `frontend/src/settings/convert.ts`

- `initialValue`: `time` → 문자열(`raw` 그대로), `timelist` → json array 파싱(chatlist와 동일 패턴).
- `toSaveItem`: `time` → `{value_type:'string'}`, `timelist` → `{value:JSON.stringify(arr), value_type:'json'}`.

### `frontend/src/components/SettingsForm.tsx`

- `time` 렌더: `<input type="time">`.
- `timelist` 렌더: youtube_monitor `ScheduledTimeEditor` 포팅(HH:MM 칩 추가/삭제, 정렬, 중복/최대 10 검증).
- `showIf` 평가: 현재 `form` 값 기준으로 표시 여부 결정(숨김 필드는 렌더 제외, 저장 시에는 항상 포함해도 무방하나 일관성 위해 정의 순서대로 저장).

## 4. 기본 시드 — `app/services/default_settings.py`

`notification` 카테고리에 비밀 아닌 기본값 추가:

```
send_mode=immediate (string)
scheduled_max_per_run=5 (int)
wait_between_messages_sec=30 (int)
quiet_hours_enabled=false (bool)
quiet_hours_start=22:00 (string)
quiet_hours_end=07:00 (string)
timezone=Asia/Seoul (string)
low_confidence_threshold=0.5 (float)
```

`scheduled_times`는 빈 리스트라 시드 불필요(미존재 시 `[]` 기본).

## 테스트 전략

- `quiet_hours.py`: 같은날/익일래핑/종일/형식오류 경계값 유닛 테스트(tz 인자 포함).
- `_matches_scheduled_time`: HH:MM 일치/불일치 유닛 테스트.
- `notify_pending_batch`: 대상 선별 쿼리(baseline/notify_enabled/done/notified_at)와
  max_per 절단·notified_at 기록을 인메모리/모의 세션으로 검증(기존 테스트 패턴 따름).
- 틱 분기: scheduled(야간 겹침 skip) / immediate(야간중 skip, 야간후 드레인) 분기 단위 테스트.
- 프론트 `convert.ts`: `time`/`timelist` 라운드트립 단위 테스트(기존 `convert.test.ts`에 추가).

## 영향 범위 / 비목표

- DB 스키마 변경 없음(설정 key/value만 추가).
- 기존 즉시발송 동작은 `send_mode=immediate` + 야간 미사용 시 그대로 유지(하위호환).
- 비목표: 채널별 발송 모드, 발송 재시도 큐, 다중 메신저(Slack 등) — 본 작업 범위 외.
