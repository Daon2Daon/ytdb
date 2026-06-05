# 조회수·좋아요 주기 갱신 설계

작성일: 2026-06-04
대상: ytdb

## 목적 / 배경
현재 `view_count`/`like_count`는 최초 수집 시 1회만 기록되고(`on_conflict_do_nothing`) 이후 갱신되지 않아, 게시 직후 스냅샷에 고정된다. 업로드 후 며칠간 폭증하는 조회수 특성상 이 값은 곧 실제와 크게 달라진다. 최근 영상의 stats를 주기적으로 갱신해 의미 있는 지표로 만든다.

## 핵심 설계 결정

### 별도 스냅샷 컬럼 없음 (DB 마이그레이션 회피)
영상의 `view_count`/`like_count`를 윈도우 기간 동안 **제자리 갱신**한다. 윈도우(`stats_refresh_days`)를 벗어나면 갱신을 멈추므로, 졸업한 영상의 저장값은 자연히 "게시 후 ~N일 시점 값"이 되어 **암묵적 최종 스냅샷** 역할을 한다. 데이터평면(3개 스키마) 마이그레이션 불필요.

### 나이 윈도우로 부하 상수화
갱신 대상 = `published_at >= now - stats_refresh_days일`. 갱신 working set이 전체 DB 크기가 아니라 발행 속도에만 비례 → 데이터가 누적돼도 부하 일정.

## 확정 사항
- 기본 윈도우 `stats_refresh_days = 30`, **0이면 비활성**. 그룹별 설정.
- 갱신 주기: 하루 1회 고정(1440분 interval).
- 별도 스냅샷 컬럼 없이 기존 `view_count`/`like_count` 제자리 UPDATE.

## 구성

### 1. 설정 — `PollingSettings` (그룹별, `app/services/settings_types.py`)
`analysis_interval_sec` 다음에 추가:
```python
stats_refresh_days: int = 30  # 게시 후 N일 이내 영상 stats 갱신. 0이면 비활성.
```
- `settings_manager.get_polling`이 `stats_refresh_days`를 로드(`_as_int(d.get("stats_refresh_days"), 30)`).
- `default_settings.py` polling 시드에 `{"key":"stats_refresh_days","value":"30","value_type":"int"}` 추가.

### 2. 갱신 잡 — `app/services/monitor_service.py`
신규 `run_stats_refresh_once()`:
- 활성 그룹 순회(`_active_groups()` 재사용), `is_active`·`DBNotConfiguredError` skip.
- 각 그룹:
  - `polling = get_polling(group_id)`; `stats_refresh_days <= 0`이면 skip.
  - `youtube_api_key` 없으면 skip.
  - 데이터평면 세션으로 `published_at >= now - N일`인 `Video.video_pk, Video.video_id` 조회(analysis_status 무관).
  - 대상 없으면 skip.
  - `YouTubeAPIClient.get_video_details(video_ids)`(이미 50개 batch 처리)로 fresh `VideoMeta` 조회.
  - 반환된 각 `VideoMeta.video_id → (view_count, like_count)` 매핑으로 해당 `Video` row UPDATE(batch, 트랜잭션).
  - 응답에 없는 video_id(삭제/비공개)는 미갱신.
  - `job_log`(신규 job_type 또는 기존 재사용) 1건: "stats 갱신: N건".
- 대상 선별을 순수 함수로 분리할 수 있는 부분(윈도우 cutoff 계산, 0=비활성 판정)은 테스트 용이하게 헬퍼로.

신규 job_type 상수: `job_logger.py`에 `JOB_TYPE_STATS = "stats"` 추가(또는 기존 CHANNEL_POLL 재사용 — 신규 권장).

### 3. 스케줄러 — `app/services/scheduler.py`
```python
JOB_STATS_REFRESH = "youtube_stats_refresh"
scheduler.add_job(run_stats_refresh_once, trigger="interval", minutes=1440,
                  id=JOB_STATS_REFRESH, replace_existing=True,
                  max_instances=1, coalesce=True)
```
`setup_jobs`에 추가. import `run_stats_refresh_once`.

### 4. 프론트 — `frontend/src/settings/defs.ts`
`polling` 카테고리에 추가:
```typescript
{ key: 'stats_refresh_days', label: '조회수 갱신 기간(일)', type: 'int', help: '게시 후 N일 이내 영상의 조회수·좋아요를 매일 갱신. 0이면 끔.' }
```

## 데이터 흐름
```
매 1440분 → run_stats_refresh_once
  for group in active:
    polling = get_polling(group); if stats_refresh_days<=0 or no api_key: skip
    ids = SELECT video_id WHERE published_at >= now - N days
    metas = YouTubeAPIClient.get_video_details(ids)   # 50개 batch, 1 unit/call
    UPDATE Video SET view_count, like_count WHERE video_id = ...   # batch
    write_job_log(STATS, "N건 갱신")
```

## 에러 처리
- `YouTubeQuotaExceededError` → 그룹 skip + 로그, 다음 날 재시도.
- API 일반 오류 → 그룹 skip + 로그(다른 그룹은 계속).
- 응답 누락 영상 → 미갱신(삭제/비공개 추정).
- `DBNotConfiguredError` → 그룹 skip.

## 테스트
- 순수 헬퍼(윈도우 cutoff, `stats_refresh_days<=0` 비활성 판정, video_id→stats 매핑 적용) 유닛 테스트.
- API/DB 통합(run_stats_refresh_once 전체) → 구현 + 수동 검증(갱신 후 view_count 변경 확인, job_log 확인).
- 프론트: 필드 추가 후 tsc/build.

## 비목표
- 시계열 stats 이력 테이블(여러 시점 기록) — 미포함. 현재 값 1개만 유지.
- 별도 final 스냅샷 컬럼 — 불필요(제자리 UPDATE가 암묵 스냅샷).
- 갱신 주기 설정화 — 하루 1회 고정.
- comment/engagement 등 추가 지표 — 미포함.
