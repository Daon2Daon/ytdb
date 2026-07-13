# Phase D-2 설계: YouTube 쿼터 카운터 + 전 스키마 순회 마이그레이션 도구

- 상태: 확정 (2026-07-13, 브레인스토밍 완료 — 사용자 승인)
- 상위 스펙: `2026-07-03-multi-tenant-design.md` §5(YouTube API 쿼터 관리)·§7 row D
- 선행: Phase D-1(공용 봇 텔레그램 연결·온보딩) — main 머지·push·배포 검증 완료(2026-07-13)

## 0. 범위·확정 결정

Phase D 잔여 2항목. 서로 독립적인 두 서브시스템이지만 둘 다 소규모 운영 도구라
하나의 스펙·구현 계획으로 묶는다(사용자 승인).

- **A. YouTube 쿼터 카운터**: 상위 §5의 `yt_quota_usage` 영속 원장 + 80%/100% 게이트.
- **B. 전 스키마 순회 마이그레이션 도구**: lazy `ensure_schema`를 선제·가시적으로
  전 그룹에 적용하는 운영 도구.

**브레인스토밍 확정 결정:**

| 결정 | 내용 |
|------|------|
| D1. 카운팅 범위 | **키별 카운트**. `yt_quota_usage`에 키 지문(key_fp) 컬럼 추가, `(usage_date, key_fp)` 단위 UPSERT. 쿼터는 Google 프로젝트(=키) 단위 부과라 키 혼합 합산은 게이트 왜곡 — 기각. 80%/100% 게이트는 시스템 키 사용량만으로 판정. 그룹 키 사용량도 기록돼 관리자 대시보드에서 관찰 가능 |
| D2. 날짜 경계 | **PT(America/Los_Angeles) 자정** — Google 실제 쿼터 리셋 시점과 일치(DST 자동 반영). KST/UTC는 실제 리셋과 어긋나 게이트가 이르거나 늦게 풀림 — 기각 |
| D3. 기록 방식 | **호출마다 즉시 UPSERT**(선택적 recorder 콜백 주입). 크래시에도 유실 0, 구현 단순. 틱당 호출 수십 건 수준이라 부하 무시 가능. 인스턴스 누적 후 flush(유실 위험)·주기적 백그라운드 flush(복잡도 최대) — 기각 |
| D4. 마이그레이션 도구 형태 | **관리자 API+버튼 + 부팅 자동 둘 다**. 부팅 백그라운드 1회 자동 적용(관리자 개입 불필요) + `POST /api/admin/migrate-schemas`로 수동 재실행·그룹별 리포트 |
| D5. 기록 실패 처리 | **best-effort** — 쿼터 기록 실패는 삼킴(ai_usage 패턴). 원장 장애가 폴링/분석을 절대 깨뜨리지 않음 |

**배경 사실(탐색으로 확인):**

- `YouTubeAPIClient._consume_quota`는 인스턴스 메모리 카운터인데, 클라이언트가
  호출 지점마다 새로 생성돼(6개 지점) 사실상 틱/요청 단위로 리셋 — 일일 추적 기능 없음.
- 유닛 단가는 코드에 이미 정확히 반영돼 있음: `channels`/`playlistItems`/`videos`=1,
  `search`=100. D-2는 이 값을 그대로 영속화한다.
- `ensure_schema`는 프로세스 캐시 `_initialized`(server_sig, schema_name)로 중복 DDL을
  막고, lazy(그룹 첫 세션 오픈 시) 실행 + additive 컬럼 자가치유를 수행. 문제는
  ①적용 시점 불확정 ②실패가 사용자 요청 중 발생·비가시적 ③그룹별 현황 조회 불가.

## 1. A. YouTube 쿼터 카운터

### 1.1 데이터 모델 — `app.yt_quota_usage` (제어 평면, 신규)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| usage_date | DATE | PT 자정 기준 날짜 (`America/Los_Angeles`로 계산) |
| key_fp | TEXT | API 키 SHA-256 hex 앞 12자. **키 원문 저장 안 함**(유출 면적 0) |
| units | INTEGER | 누적 유닛 |
| updated_at | TIMESTAMPTZ | 마지막 누적 시각 |

- PK `(usage_date, key_fp)`. `ensure_control_schema`가 create_all로 생성(순수 추가).
- 상위 §5의 `(date PK, units)`에서 key_fp 축 추가로 확장 — 결정 D1.
- PT 자정 리셋용 별도 잡 불필요: 날짜가 바뀌면 새 행이 시작되므로 자연 리셋.

### 1.2 기록 — 신규 `app/services/yt_quota_service.py`

- `key_fingerprint(api_key) -> str`: SHA-256 hex 앞 12자.
- `pt_today() -> date`: `America/Los_Angeles` 현재 날짜.
- `make_recorder(api_key) -> async (units:int) -> None`: 제어 평면 세션을 열어
  `INSERT … ON CONFLICT (usage_date, key_fp) DO UPDATE SET units = units + :n,
  updated_at = now()` 실행. 예외는 전부 삼키고 stderr 로그만(결정 D5).
- `units_today(key_fp) -> int`: 게이트·대시보드용 당일 조회.

`YouTubeAPIClient.__init__`에 선택적 `recorder` 파라미터 추가. `_get()`이 HTTP
**시도마다**(응답 성공 여부 무관 — Google은 실패 호출도 과금) `await recorder(units)`
호출. recorder 미주입 시 기존과 100% 동일 동작(테스트·하위 호환).

**배선(6개 생성 지점 전부):** `central_poller.run_central_poll_once`(시스템 키),
`routers/channels.py`(채널 등록), `routers/videos.py`(즉시분석),
`monitor_service` 3곳(그룹 폴링·통계 갱신·단건 폴링). 각 지점에서
`recorder=make_recorder(사용한 키)` 주입 — 그룹 키/시스템 키 폴백 결과가 무엇이든
실제 사용한 키의 지문으로 기록되므로 귀속이 자동으로 정확하다.

기존 인스턴스 메모리 가드(`_consume_quota`)는 무변경 유지(2차 방어, 틱 폭주 차단).

### 1.3 한도 — 전역 설정 `youtube_daily_quota` (신규 키)

- `global_settings`에 비밀 아님 키로 추가, 기본 10000(Google 기본 쿼터).
- 관리자 `GET/PUT /api/admin/global-settings`의 `_GLOBAL_KEYS`에 추가, 양의 정수 검증
  (central_poll_floor_min 패턴).
- 그룹 polling 설정의 `youtube_daily_quota`(그룹 키용 인스턴스 가드)는 무변경 별개.

### 1.4 게이트 — 80% / 100% (상위 §5)

`run_central_poll_once` 진입 시(시스템 키 확보 직후):

1. `used = units_today(fp(시스템 키))`, `limit = 전역 youtube_daily_quota`.
2. **`used ≥ limit` (100%)**: 중앙 폴링 중단 + 그룹 스코프 **시스템 키 폴백**도 거부
   — `resolve_youtube_key`가 폴백 경로에서 한도 초과 시 명확한 에러
   (`YouTubeQuotaExceededError`)를 던진다. 그룹 자체 키 호출은 무영향.
3. **`used ≥ limit × 0.8` (80%)**: 신규 중앙 폴링 skip(분석·알림·그룹 키 호출·
   사용자 발 시스템 키 폴백 호출은 계속 — 소량·사용자 주도라 허용).
4. **경고는 stdout 로그로, 상태 전환 시 1회만**(모듈 레벨 마지막 상태 기억 —
   80% 진입/100% 진입/해제 각각 1회). 스케줄러 최소 1분 주기라 틱마다 기록하면
   스팸이다. 상위 §5는 "job_logs 경고"라 했으나 **의도적 편차**: job_logs는 그룹
   스키마(데이터 평면) 테이블이라 특정 그룹에 귀속되지 않는 중앙 게이트 이벤트를
   기록할 자리가 없다(전 그룹 복제 기록은 과잉). stdout 로그 + 관리자 대시보드
   백분율 표시(§1.5)가 관찰성을 대신한다.

### 1.5 관리자 가시성

- `GET /api/admin/usage` 응답에 `youtube` 섹션 추가:
  `{ usage_date(PT), daily_quota, entries: [{key_fp, units, pct, is_system_key}] }`
  — `is_system_key`는 현재 시스템 키 지문과 비교해 계산(키 로테이션 시 과거 행은
  자연히 false).
- Admin 사용량 탭에 카드 렌더: 시스템 키 사용량/한도/백분율(80%↑ 경고색, 100%↑
  위험색) + 그룹 키 행 목록.

## 2. B. 전 스키마 순회 마이그레이션 도구

### 2.1 서비스 — 신규 `app/services/schema_migrator.py`

- `migrate_all_schemas() -> list[GroupMigrationResult]`:
  전체 그룹(활성+비활성 — 스키마는 데이터 자산이라 비활성도 패치) **순차** 순회,
  그룹당 `dpm.ensure_schema(group, force=True)` 호출.
- `GroupMigrationResult`: `group_id, slug, schema_name, status(ok|failed|skipped),
  error(실패 메시지), duration_ms`.
  - `skipped` = `DBNotConfiguredError`(DB 미설정 그룹).
  - `failed` = 그 외 예외(메시지 포함, 다음 그룹 계속 — 그룹 단위 격리).
- 순차 실행 이유: 수십 그룹 규모에서 충분히 빠르고, DDL 동시 실행으로 인한 데이터
  평면 부하 스파이크·락 경합을 피한다.

### 2.2 `ensure_schema(group, force=False)` 확장

- `force=True`면 프로세스 캐시 `_initialized` 조기 반환을 우회(락은 유지 —
  동시 실행 안전). 성공 시 캐시에 기록하는 기존 동작은 동일.
- 기존 호출부는 전부 기본값 `force=False` — 동작 100% 무변경.

### 2.3 부팅 자동 적용

- `main.py` lifespan에서 startup 완료 후 `asyncio.create_task`로 1회 실행
  (부팅 블로킹 없음). 결과는 stdout 로그(그룹별 상태 요약, 실패 그룹은 에러 명시).
- 실패해도 앱은 정상 기동 — lazy `ensure_schema`가 기존처럼 안전망으로 남는다.
- 부팅 직후라 캐시가 비어 있으므로 사실상 최초 적용을 선제 수행하는 효과.

### 2.4 관리자 API + UI

- `POST /api/admin/migrate-schemas` (admin 전용, 기존 admin 라우터 의존성):
  동기 실행, `{ results: [GroupMigrationResult…], summary: {ok, failed, skipped} }`
  반환. 리포트 영속화는 안 함(YAGNI) — 응답으로 즉시 확인, 부팅분은 로그.
- Admin 페이지에 "전 스키마 마이그레이션 실행" 버튼 + 결과 테이블
  (그룹/상태/소요시간/에러). 실행 중 버튼 비활성(재진입 가드).

## 3. 테스트 계획

단위·통합(기존 FakeSession/ASGITransport 패턴):

- recorder: UPSERT 누적(같은 날 2회 → units 합산), 키 지문별 행 분리, 예외 삼킴.
- PT 날짜 경계: 고정 시각 주입으로 UTC/KST와 날짜가 갈리는 시각에 PT 날짜 검증.
- 게이트: 79%/80%/100% 경계에서 중앙 폴링 skip 여부, 100%에서 시스템 키 폴백
  거부·그룹 자체 키 통과, 상태 전환 로그 1회성.
- `YouTubeAPIClient`: recorder 미주입 시 기존 동작 무변경, 주입 시 시도마다 호출
  (실패 응답 포함), search=100 유닛 전달.
- migrator: 성공/실패/스킵 혼합 리포트, 그룹 단위 격리(중간 실패에도 계속),
  `force=True` 캐시 우회, admin 엔드포인트 권한(비 admin 403)·응답 스키마.
- 관리자 usage 응답 youtube 섹션: is_system_key 판정, 한도 반영.

실 DB E2E(구현 완료 후 체크리스트): 부팅 자동 순회 로그 확인, 수동 API 실행
리포트, 실 폴링 1틱 후 yt_quota_usage 행 생성·누적 확인.

## 4. 프로덕션 호환성 (무중단 원칙)

- 새 테이블 1개(`yt_quota_usage`) 순수 추가, 전역 설정 키 1개 추가(기본값 폴백).
- 게이트는 사용량 데이터가 쌓이기 전엔 0%라 발동하지 않음 — 배포 직후 동작 무변경.
- recorder는 주입 안 되면 no-op이고, 주입돼도 best-effort라 호출 경로 무영향.
- 마이그레이터는 기존 `ensure_schema` 로직 재사용 — 실행되는 DDL이 기존 lazy 경로와
  동일(멱등). 프로덕션 4그룹 기준 순회 비용은 초 단위.
