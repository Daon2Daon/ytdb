# B-0b: 중앙 채널 레지스트리 설계

작성일: 2026-07-05
상위 문서: `2026-07-03-multi-tenant-design.md` §2.9 (공유 분석 캐시 + 중앙 채널 레지스트리)
선행 완료: Phase A(계정·소유권), B-0a(공유 분석 캐시 — 실 DB E2E 통과)

## 1. 목적과 배경

B-0a로 AI 분석 중복은 제거됐지만(같은 영상×프리셋×모델 = 호출 1회), **YouTube 폴링은
여전히 그룹 수만큼 중복**된다. 현재 `run_master_poll_once`가 그룹을 순회하며 각 그룹이
자기 채널을 독립적으로 폴링하므로, N개 그룹이 같은 채널을 구독하면 같은 채널에 API 호출이
N번 나간다. 사용자가 늘수록 YouTube API 쿼터(기본 일 10,000 유닛) 소모가 선형 증가 —
인기 채널에 구독이 몰리면 쿼터 고갈이 현실적 병목이다.

B-0b는 §2.9가 약속한 `channel_registry`를 구현해 **폴링을 채널당 1회**로 만들고, 발견한
신규 영상을 구독 그룹들에 팬아웃한다.

### 확정된 설계 결정 (2026-07-05 브레인스토밍)

| 항목 | 결정 |
|------|------|
| 중앙 폴링 API 키 | 시스템 전역 키 도입. `app.global_settings` 최소 골격을 Phase C에서 앞당김 |
| 전역 설정 범위 | B-0b 필요분만 (YouTube 키, 폴링 주기 하한). AI 게이트웨이 전역화는 Phase C 잔류 |
| 그룹별 YouTube 키 | 폴백 구조 유지 — 그룹 스코프 호출(통계 갱신·수동 단건 폴링·채널 등록 조회)은 그룹 키 우선, 없으면 시스템 키. 중앙 폴링은 항상 시스템 키 |
| 팬아웃 방식 | 푸시 — 중앙 폴러가 API 응답을 들고 구독 그룹 스키마에 직접 삽입 (스테이징 풀·응답 캐시 대안 기각, §7) |
| 구독 0 채널 | registry 행 유지, 폴링만 제외 (이력 `last_video_at` 보존, 재구독 시 재활용) |
| 수동 단건 폴링 | 그룹 스코프 유지 (중앙 팬아웃 없음). 다른 구독 그룹은 다음 중앙 틱에서 수령 — 중복은 기존 필터가 방지 |
| 시스템 키 부트스트랩 | `global_settings.youtube_api_key` 부재 시 admin 그룹 polling 키로 1회 시드 — 기존 배포 무중단 업그레이드 |

### 비목표

- 통계 갱신(stats refresh)의 중앙화 — 그룹 스코프 유지 (그룹별 영상 사본에 대한 제자리
  UPDATE라 중앙화 실익이 작음. 쿼터가 문제 되면 후속 검토)
- AI 게이트웨이 전역 설정 — Phase C
- 플랜별 폴링 하한의 플랜 테이블 연동 — Phase B에서 `plans`와 연결. B-0b는 전역 하한
  단일 값(`central_poll_floor_min`)만
- YouTube 쿼터 카운터/대시보드 — Phase D

## 2. 데이터 모델 (app 스키마)

```sql
-- 전역 채널 레지스트리 (§2.9 원안 + upload_playlist_id 추가 — API 호출에 필수)
CREATE TABLE app.channel_registry (
    channel_id         TEXT        PRIMARY KEY,          -- YouTube 채널 ID
    title              TEXT,
    upload_playlist_id TEXT,
    last_polled_at     TIMESTAMPTZ,
    last_video_at      TIMESTAMPTZ,
    subscriber_groups  INT         NOT NULL DEFAULT 0,   -- 참고용 캐시 (동기화 지점에서 재계산)
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 역방향 매핑: channel_id → 구독 그룹. 스키마-per-그룹 구조에서
-- "이 채널을 누가 구독하나"를 그룹 스키마 스캔 없이 답하는 유일한 수단.
CREATE TABLE app.channel_subscriptions (
    channel_id        TEXT   NOT NULL REFERENCES app.channel_registry(channel_id),
    group_id          BIGINT NOT NULL REFERENCES app.groups(group_id) ON DELETE CASCADE,
    poll_interval_min INT    NOT NULL,  -- 동기화 시점에 해석 완료된 유효값
                                        -- (채널값 없으면 그룹 default_channel_interval_min 적용)
    window_hours      INT    NOT NULL,  -- 동기화 시점에 해석 완료된 그룹 polling.window_hours
    PRIMARY KEY (channel_id, group_id)
);
CREATE INDEX channel_subscriptions_group ON app.channel_subscriptions (group_id);

-- 전역 설정 최소 골격 (Phase C에서 항목 추가만)
CREATE TABLE app.global_settings (
    key        TEXT        PRIMARY KEY,
    value      TEXT        NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- 시드 키: 'youtube_api_key'(암호화 저장 — 기존 FERNET_KEY 사용, §8 위험표와 일관),
--          'central_poll_floor_min' (기본 '10')
```

**비정규화가 핵심.** 채널별 폴링 주기는 그룹 스키마 안(`channels.poll_interval_min`)에
있어 매 틱 전 그룹 스키마를 조회할 수 없다. 그룹이 채널/폴링 설정을 바꾸는 시점에
"해석 완료된 유효값"으로 subscription 행을 동기화하고(NULL 없음 — 그룹 기본값 해석은
동기화 시점에 끝남), 중앙 폴러의 due 판정은 app 스키마 단일 쿼리로 끝낸다:

```sql
SELECT r.channel_id, r.upload_playlist_id, r.last_polled_at,
       MIN(s.poll_interval_min) AS effective_interval,
       MAX(s.window_hours)      AS fetch_window
FROM app.channel_registry r
JOIN app.channel_subscriptions s USING (channel_id)   -- 구독 0 채널 자연 제외
GROUP BY r.channel_id, r.upload_playlist_id, r.last_polled_at;
```

유효주기는 `central_poll_floor_min`으로 클램프(하한 준수). due = `last_polled_at IS NULL
OR last_polled_at + effective_interval ≤ now`.

## 3. 중앙 폴링 흐름 (`run_central_poll_once`)

기존 `run_master_poll_once`(그룹 순회 폴링)를 대체한다.

1. **due 채널 산출**: 위 쿼리 + 클램프.
2. **채널당 1회 API 조회** (시스템 키): playlist items(`published_after` = 구독 그룹
   `window_hours` **최댓값** 컷) → 신규 후보 video details. 동시성은 기존 세마포어
   패턴 재사용, 상한은 코드 상수 5 (그룹별 `max_concurrent_channels`는 그룹 폴링용
   설정이라 중앙 폴러에 부적합 — 전역 설정 키 추가는 필요해질 때).
3. **구독 그룹 팬아웃**: 각 구독 그룹에 대해 —
   - 그룹 엔진/스키마 세션 확보 (`DBNotConfiguredError`면 그 그룹만 skip).
   - 기존 필터 로직 그대로 재사용: `_filter_new_videos`(기존 영상 중복 +
     `deleted_videos` 재유입 방지) → 그룹 자신의 `window_hours`로 재컷 →
     `sequence_in_channel` 채번 → INSERT(`analysis_status='pending'`) →
     그룹 `channels.last_checked_at`/`last_video_id` 갱신 → 그룹 job log 기록.
   - **그룹 단위 try/except 격리** — 한 그룹 실패가 다른 그룹 팬아웃을 막지 않는다.
4. **registry 갱신**: `last_polled_at=now`, 신규 영상 있으면 `last_video_at` 갱신.
5. **쿼터 초과**(`YouTubeQuotaExceededError`): 해당 틱 전체 중단 + 로그 (기존 정책 동일).

**코드 구조**: `MonitorService.process_channel`을 두 단계로 분해한다 —
(a) `fetch_channel_updates(api_client, playlist_id, cutoff) -> list[VideoMeta]` (API 조회,
그룹 무관), (b) `insert_group_videos(session, channel, metas) -> list[video_pk]` (그룹
스키마 삽입, 기존 필터·채번 로직). 중앙 폴러는 (a) 1회 + 구독 그룹마다 (b). 수동 단건
폴링은 (a)+(b)를 그룹 스코프로 그대로 사용 — **필터 로직 이중화 없음**.

팬아웃 후 분석은 무변경: 각 그룹 스키마에 pending 영상이 생기면 기존 분석 스케줄러가
집어가고, B-0a 공유 캐시가 AI 호출 1회를 보장한다. UI·알림·다이제스트도 "그룹 스키마에
영상 행이 생긴다"는 계약만 보므로 무변경.

## 4. 동기화·생애주기

| 이벤트 | 동작 |
|--------|------|
| 그룹이 채널 추가 (channels 라우터) | registry upsert(없으면 생성, title/playlist 채움) + subscription insert + `subscriber_groups` 재계산 |
| 그룹이 채널 삭제/비활성 토글 | subscription 삭제/삽입 + 카운트 재계산. 구독 0이면 registry 행 유지, due 쿼리 join에서 자연 제외 |
| 채널별 주기 변경 / 그룹 polling 설정 변경 | 해당 subscription 행 갱신 (`poll_interval_min`, `window_hours`) |
| 그룹 비활성(`is_active=false`) | 그룹의 subscription 전체 삭제. 재활성 시 그룹 단위 재동기화 함수로 복원 (부팅 백필과 동일 로직을 그룹 스코프로 재사용) |
| 그룹 삭제 | FK CASCADE가 백스톱, 삭제 훅에서 명시적으로 제거 + 카운트 재계산 |

`subscriber_groups`는 참고용 캐시(§2.9 원안 명시)로, 위 동기화 지점에서
`COUNT(*)` 재계산으로 갱신한다 — 증감 연산 누적 드리프트 방지.

## 5. 전역 설정·키 해석

- `global_settings` 접근자: `get_global(key)` / `set_global(key, value)` 서비스 함수.
  `youtube_api_key`는 기존 FERNET_KEY로 암호화 저장.
- 관리자 API: 조회/수정 (admin 전용, 키 값은 마스킹 반환). 일반 사용자 비노출.
- `resolve_youtube_key(group) -> str | None`: 그룹 polling 키가 있으면 그룹 키, 없으면
  시스템 키. **그룹 스코프 호출 전용** (통계 갱신 `refresh_stats`, 수동 단건 폴링
  `poll_single_channel`, 채널 등록 시 메타 조회). 기존 "키 미설정 → skip+로그" 분기는
  "양쪽 다 없음 → skip+로그"로 바뀐다.
- 중앙 폴링은 항상 시스템 키. 시스템 키 부재 시 중앙 폴링 skip + 로그.

## 6. 마이그레이션·부트스트랩 (부팅 시)

1. app 스키마에 테이블 3개 생성 (기존 create_all/ALTER 가드 패턴).
2. **백필**: 전 그룹 스키마의 `channels`를 스캔해 registry + subscriptions 시드.
   idempotent(ON CONFLICT DO NOTHING + 카운트 재계산) — 재부팅 반복 안전.
3. **시스템 키 시드**: `global_settings.youtube_api_key`가 없고 admin 그룹에 polling
   키가 있으면 그 값으로 1회 시드. Phase A의 `AUTH_PASSWORD` 부트스트랩과 같은 철학 —
   기존 단일 운영자 배포가 업그레이드 직후에도 폴링 무중단.
4. 롤백 안전성: B-0b 테이블은 순수 추가분. 코드 롤백 시 테이블은 잔류해도 무해.

## 7. 기각한 대안

- **스테이징 풀** (중앙 폴러 → 중앙 발견 테이블 → 그룹 틱이 pull): 단일 프로세스 앱에서
  디커플링 실익 없이 테이블+워터마크+정리 로직만 추가. 기각.
- **공유 API 응답 캐시** (그룹별 폴링 유지 + TTL 캐시): 변경량은 최소지만 §2.9가 약속한
  registry(관리 가시성, 주기 최솟값 스케줄링, Phase B/D 기반)가 안 생기고 그룹별
  window 차이로 캐시 키가 불결. 기각.
- **완전 중앙화** (공유 videos 테이블): 데이터 평면 해체급 재작성 — §2.9에서 이미 보류.
  본 설계도 동일 입장: 실행(API 호출)만 중앙화, 데이터 사본은 그룹 유지.

## 8. 에러 처리 요약

| 상황 | 처리 |
|------|------|
| 시스템 키 미설정 | 중앙 폴링 skip + 로그 (기존 그룹 키 미설정 동작과 대칭) |
| 특정 그룹 DB 미설정/삽입 실패 | 그 그룹만 skip/로그, 나머지 팬아웃 계속 |
| YouTube 쿼터 초과 | 틱 중단 + job log SKIP (기존 정책) |
| 워커 사망(팬아웃 도중) | registry `last_polled_at` 미갱신 → 다음 틱 재폴링. 이미 삽입된 그룹은 `_filter_new_videos`가 중복 방지 — idempotent |
| 동기화 누락(버그 등)으로 subscription 불일치 | 부팅 백필이 idempotent 복구 지점. 필요 시 관리자 재동기화 액션(Phase B 콘솔 후보) |

## 9. 테스트 전략

- **단위**: 유효주기 계산(MIN+클램프), due 판정, 동기화(추가/삭제/토글/카운트 재계산),
  `resolve_youtube_key` 폴백, 부트스트랩 시드(있음/없음/이미 시드됨).
- **통합** (FakeSession/mock API, B-0a 패턴): 두 그룹 같은 채널 → **API fetch 1회** +
  두 그룹 모두 삽입 + deliveries/분석은 B-0a 경로 그대로. 한 그룹 삽입 실패 →
  다른 그룹 정상. `deleted_videos` 있는 그룹 → 그 그룹만 미삽입. 그룹별
  `window_hours` 차이 → 넓게 fetch, 좁은 그룹은 재컷.
- **실 DB E2E** (구현 완료 후, 테스트 DB `100.115.13.102`): 실제 채널을 두 그룹에 등록 →
  중앙 폴링 1틱 → 두 그룹 스키마에 영상 행 + registry/subscriptions 상태 검증 →
  이어서 B-0a 캐시 경로까지 관통(폴링→분석→캐시 1회+복사). 시나리오 상세는 구현
  계획에서 확정.

## 10. 검증 기준 (Phase 표용)

두 그룹이 같은 채널 구독 → 중앙 틱 1회에 YouTube API 채널 조회 1회, 두 그룹 모두 신규
영상 보유(각자 필터 존중), registry `last_polled_at` 갱신, 이후 분석은 AI 호출 1회(B-0a).
기존 단일 운영자 배포는 업그레이드 후 설정 변경 없이 폴링 무중단.
