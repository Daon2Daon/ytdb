# ytdb 설계 문서: 다중 모니터링 그룹 YouTube 모니터

작성 기준일: 2026-06-02
대상 코드베이스: `/Users/mook/Library/CloudStorage/SynologyDrive-Mook_Cloud/04.Coding/ytdb`
모태: `my-assistant`의 `app/services/youtube/*` 모듈 (단일 그룹 구조)

## 1. 목적과 범위

`my-assistant`에 통합돼 있던 YouTube 자동 모니터링 기능을 독립 프로젝트(`ytdb`)로 분리하고, 단일 모니터링 대상만 다루던 구조를 다중 모니터링 그룹 구조로 발전시킨다.

핵심 요구사항:

- 모니터링 그룹을 여러 개 생성하여 관리한다.
- 그룹별로 사용하는 AI agent(게이트웨이/모델/프롬프트/파라미터), DB, 알림을 각각 설정하여 관리한다.
- 그룹 간 데이터는 서로 격리한다.

비목표:

- 다중 사용자(멀티 테넌트) 권한 분리. 본 프로젝트는 단일 운영자 기준.
- 영상 다운로드/저장.
- 라이브 스트림 실시간 모니터링.

## 2. 핵심 설계 결정

본 설계는 다음 결정을 전제로 한다.

| 항목 | 결정 | 근거 |
|------|------|------|
| 그룹 간 DB 격리 | 스키마 분리 (옵션 B) + 공유 연결 풀 | 1개 PG 서버 안에서 그룹별 스키마로 격리. 그룹이 늘어도 연결 풀은 서버당 1개로 공유 |
| 주력 DB | PostgreSQL 단일 (SQLite 미사용) | PG 주력 환경. 백업/도구/코드 일원화 |
| 부트스트랩 | 제어 평면 DSN을 `.env`에 고정 | PG 안에 접속정보를 두는 순환을 회피하면서 SQLite 의존 제거 |
| AI agent 단위 | 설정 수준 | 게이트웨이/모델/프롬프트/파라미터를 그룹별로 다르게. 에이전트 백엔드 교체는 범위 외 |
| 알림 | 그룹별 분리 | 그룹마다 별도 텔레그램 봇 토큰/채팅 대상 |
| 프로젝트 시작 | 클린 재작성 | youtube 모듈만 독립 FastAPI 앱으로 신규 구성. 기존 코드 복사가 아닌 새 작성 |
| 기존 데이터 이관 | 무이관 자동 채택 | `default` 그룹의 DB 설정을 기존 `youtube` 스키마로 지정하면 멱등 마이그레이션이 기존 데이터를 그대로 인식 |

### 구현 원칙

`my-assistant`는 날씨/금융/캘린더 등 여러 기능이 복합된 프로젝트라, youtube 모듈에도 그 맥락에서 비롯된 불필요하거나 비효율적인 부분이 있을 수 있다. ytdb는 다음 원칙으로 구현한다.

- 기존 코드를 그대로 복사하지 않는다. 검증된 로직과 설계 개념만 선별 참조하여 새로 작성한다.
- 멀티 그룹 구조에 맞지 않거나 단일 그룹 가정에 묶인 코드는 과감히 재설계한다.
- 단순성을 우선한다. 추측성 추상화/설정/예외처리를 넣지 않는다.
- 본 문서에서 "모태"는 참조 출처를 가리키며, 코드 이식 의무를 뜻하지 않는다.

## 3. 평면 분리 모델

단일 PostgreSQL 데이터베이스 안에서 스키마로 제어 평면과 데이터 평면을 나눈다.

```
PostgreSQL (단일 서버 / 단일 DB)
├── app (제어 평면)
│     ├── groups               그룹 정의
│     ├── settings             그룹별 설정 (category/key/value, 시크릿은 암호화)
│     └── apscheduler_jobs     APScheduler jobstore
│
├── youtube_invest (그룹 A 데이터 평면)
│     ├── channels / videos / video_details / video_summaries
│     ├── tags / video_tags
│     ├── job_logs / deleted_videos
│     └── schema_migrations
├── youtube_tech   (그룹 B 데이터 평면)
└── youtube_...    (그룹 N)
```

원칙:

- 제어 평면(`app` 스키마) 접속 정보(DSN)는 `.env`에만 둔다. 부트스트랩 시크릿이다.
- 데이터 평면 스키마는 그룹마다 1개. 보통 같은 서버의 다른 스키마지만, 그룹 설정에서 다른 서버 DSN을 지정할 수도 있다(그 접속정보는 제어 평면에 암호화 저장).
- PG 테이블에는 `group_id` 컬럼을 넣지 않는다. 스키마 자체가 그룹 경계이므로, 모든 그룹이 동일한 테이블 구조를 공유한다.
- 연결 풀은 그룹 단위가 아니라 물리 서버(DSN에서 스키마를 뺀 단위) 단위로 공유한다. 그룹이 늘어나도 풀 수는 늘지 않는다. 그룹 격리는 연결마다 `schema_translate_map`으로 스키마를 바인딩하여 달성한다(아래 5.2).

## 4. 데이터 모델

### 4.1 제어 평면 (app 스키마)

```sql
CREATE SCHEMA IF NOT EXISTS app;

CREATE TABLE IF NOT EXISTS app.groups (
    group_id    BIGSERIAL   PRIMARY KEY,
    slug        TEXT        NOT NULL UNIQUE,   -- 잡 ID/스키마 접미사용 식별자 (예: invest)
    name        TEXT        NOT NULL,          -- 표시명
    schema_name TEXT        NOT NULL UNIQUE,   -- 데이터 평면 스키마명 (예: youtube_invest)
    is_active   BOOLEAN     NOT NULL DEFAULT TRUE,
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS app.settings (
    setting_id  BIGSERIAL   PRIMARY KEY,
    group_id    BIGINT      NOT NULL REFERENCES app.groups(group_id) ON DELETE CASCADE,
    category    TEXT        NOT NULL,    -- database / ai_gateway / prompts / polling / notification / digest
    key         TEXT        NOT NULL,
    value       TEXT,                    -- 평문 값
    value_enc   BYTEA,                   -- Fernet 암호화 값 (is_secret=true 시)
    value_type  TEXT        NOT NULL DEFAULT 'string',  -- string/int/float/bool/json
    is_secret   BOOLEAN     NOT NULL DEFAULT FALSE,
    description TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (group_id, category, key)
);
```

설정 카테고리와 주요 키:

| category | 주요 key | 비고 |
|----------|----------|------|
| database | host, port, dbname, username, password(secret), schema, sslmode | 그룹 데이터 평면 접속. 같은 서버면 schema만 다름 |
| ai_gateway | base_url, api_key(secret), primary_model, fallback_model, tagging_model, digest_model, temperature, max_tokens, daily_budget_usd | 그룹별 AI agent |
| prompts | analysis_prompt, digest_prompt | 그룹별 프롬프트 |
| polling | master_interval_min, pending_analysis_interval_min, default_channel_interval_min, youtube_api_key(secret), youtube_daily_quota, window_hours, max_concurrent_channels, max_concurrent_analyses, analysis_interval_sec | |
| notification | telegram_enabled, telegram_bot_token(secret), telegram_chat_id, send_mode, scheduled_times, wait_between_messages_sec, low_confidence_threshold, quiet_hours_* | 그룹별 봇/채팅 |
| digest | enabled, period_weeks, schedule_times, telegram_enabled, categories, channel_pks, tags | 주간 리뷰 |

### 4.2 데이터 평면 (그룹별 스키마)

데이터 평면 테이블 구조는 모태의 설계를 참조하되 새로 정의한다. 스키마명만 그룹별로 다르다.

- `channels`, `videos`, `video_details`, `video_summaries`, `tags`, `video_tags`, `job_logs`, `deleted_videos`, `schema_migrations`

테이블 정의는 모태 명세서의 DDL을 참조해 새로 작성한다. `group_id` 컬럼은 추가하지 않는다.

모델 선언 시 schema를 하드코딩(`{"schema": "youtube"}`)하지 않고 심볼릭 토큰(`{"schema": "ytgroup"}`)으로 둔다. 런타임에 세션의 `schema_translate_map`이 토큰을 그룹의 실제 schema_name으로 변환한다. 이로써 모델 1벌로 모든 그룹 스키마를 다룬다.

## 5. 컴포넌트 설계

리팩터링의 본질은 전역 싱글톤을 그룹 키 기반 레지스트리로 일반화하는 것이다.

### 5.1 SettingsManager (그룹별)

- 모태: `app/services/youtube/settings_manager.py`의 전역 싱글톤.
- 변경: 캐시 키를 `category`에서 `(group_id, category)`로 확장.
- 조회 대상: SQLite 대신 제어 평면 `app.settings`.
- 인터페이스: `get_database(group_id)`, `get_ai_gateway(group_id)`, `get_prompts(group_id)`, `get_polling(group_id)`, `get_notification(group_id)`, `get_digest(group_id)`, `invalidate(group_id, category=None)`.
- 카테고리별 `*Settings` dataclass(database/ai_gateway/prompts/polling/notification/digest)는 모태 구조를 참조해 정의하고, 그룹으로 필터링한 rows를 받아 구성한다.
- Fernet 시크릿 복호화 로직 유지. 키는 `.env`의 `FERNET_KEY`.

### 5.2 DBEngineManager (서버당 공유 풀 + 스키마 바인딩)

그룹 수가 계속 증가하는 것을 전제로, 연결 풀을 그룹이 아니라 물리 서버 단위로 공유한다. 그룹 격리는 SQLAlchemy의 `schema_translate_map`으로 연결마다 스키마를 바인딩하여 달성한다.

설계 원칙:

- 엔진(연결 풀)은 서버 시그니처 단위로 캐시한다. 시그니처는 DSN에서 스키마를 제외한 `host:port:dbname:username:sslmode`이다.
- 같은 서버에 속한 모든 그룹은 단일 엔진(풀)을 공유한다. 다른 서버 DSN을 쓰는 그룹만 별도 엔진을 갖는다. 따라서 풀 수는 그룹 수가 아니라 "구별되는 물리 서버 수"에 비례한다.
- 데이터 평면 모델은 schema를 하드코딩하지 않고 심볼릭 토큰(예: `ytgroup`)으로 선언한다. 런타임에 세션/연결에 `execution_options(schema_translate_map={"ytgroup": group.schema_name})`를 적용해 실제 스키마로 변환한다.
- 연결 상태(`SET search_path`)를 변형하지 않으므로, 풀에서 재사용되는 연결에 상태 누수가 없다. 변환은 실행 단위로 무상태이다.

인터페이스:

- `get_engine(server_sig)` 또는 `get_engine_for_group(group_id)`: 그룹의 DatabaseSettings에서 서버 시그니처를 계산해 공유 엔진을 반환/생성.
- `session_for_group(group_id)`: 공유 엔진에서 세션을 만들고 해당 그룹의 `schema_translate_map`을 적용해 반환. 도메인 코드는 항상 이 진입점을 통해 그룹 스코프 세션을 얻는다.
- `recreate_engine(server_sig)`, `dispose_current_loop_engine()`.
- 루프별 엔진 분리(asyncpg "different loop" 회피)는 모태 구조를 유지하되, 키를 그룹에서 서버 시그니처로 바꾼다: `_engines[loop][server_sig] = (engine, sig)`.

스키마 보장과 마이그레이션:

- `ensure_schema(group_id)`는 그룹의 schema_name 기준으로 멱등 적용(`CREATE SCHEMA IF NOT EXISTS` + 테이블 DDL). 데이터 평면 DDL은 스키마를 파라미터로 받아 적용한다(스키마명을 토큰 치환하거나 적용 트랜잭션 한정으로 search_path 설정).
- 기존 데이터가 있는 스키마를 그룹에 지정하면 그대로 채택된다.
- 이미 초기화한 (서버, 스키마) 조합은 기록하여 새 이벤트 루프에서 DDL을 반복하지 않는다(모태의 `_initialized_sigs` 패턴을 (server_sig, schema_name) 키로 확장).

트레이드오프:

- 공유 풀은 그룹 간 연결 경합이 생길 수 있다. 폴링/분석 동시성(`max_concurrent_*`)과 풀 크기(`pool_size`, `max_overflow`)로 조절한다.
- 서로 다른 서버를 쓰는 그룹이 많아지면 그만큼 풀이 늘어난다. 단, 일반적으로 그룹은 같은 서버를 공유하므로 실질적으로 풀 1개에 수렴한다.

제어 평면 엔진은 별도로 둔다. `.env` DSN으로 부팅 시 1회 생성하며 그룹과 무관하게 항상 존재한다.

### 5.3 분석 파이프라인 (그룹 컨텍스트 주입)

- 모태: `build_analysis_pipeline()`가 전역 설정을 참조.
- 변경: 그룹 컨텍스트(해당 그룹의 ai_gateway 설정, prompts, 데이터 평면 엔진)를 인자로 받는다.
- 이로써 같은 영상이라도 소속 그룹의 AI agent와 프롬프트로 분석된다.

### 5.4 스케줄러 (전역 잡이 그룹을 순회)

- `youtube_master_poll` (전역 1개): 활성 그룹 순회 → 그룹별 엔진/설정으로 채널 폴링. 채널별 주기 판정 로직(due-channel 선별)을 그룹 스코프로 새로 작성.
- `youtube_pending_analysis` (전역 1개): 활성 그룹 순회 → 그룹별 pending 1건 claim → 그룹 AI agent로 분석.
- `youtube_gateway_health`: 그룹별 게이트웨이가 다를 수 있으므로 그룹별 잡(`youtube_gateway_health_{slug}`).
- 예약발송/다이제스트 잡: `job_id`에 slug 접미사(`youtube_digest_{slug}_{dow}_{HHMM}`, `youtube_notify_{slug}_{HHMM}`).
- jobstore는 PG(`app.apscheduler_jobs`) 또는 메모리. 부팅 시 `setup_*_jobs()`로 재등록하므로 영속성은 필수가 아니다.

### 5.5 API와 UI 그룹 스코프

- 모든 도메인 API는 그룹 스코프를 받는다: `/api/groups/{slug}/channels`, `/api/groups/{slug}/videos`, `/api/groups/{slug}/settings/...`.
- 그룹 자체 CRUD: `/api/groups`.
- UI 상단에 그룹 선택기(드롭다운)를 두고 선택된 그룹 컨텍스트로 화면을 구성한다.

## 6. 디렉터리 구조

```
ytdb/
├── app/
│   ├── main.py                  FastAPI 진입점 (youtube 전용)
│   ├── config.py                FERNET_KEY, 제어 평면 DSN, 기본 텔레그램 폴백
│   ├── control_db.py            제어 평면 PG 엔진/세션 (app 스키마)
│   ├── models/
│   │   ├── control/
│   │   │   ├── group.py         app.groups
│   │   │   └── setting.py       app.settings
│   │   └── pg/
│   │       ├── base.py          그룹 데이터 평면 Declarative Base
│   │       ├── channel.py video.py video_detail.py video_summary.py
│   │       ├── tag.py video_tag.py job_log.py deleted_video.py
│   ├── schemas/                 Pydantic (group, channel, video, settings...)
│   ├── routers/
│   │   ├── groups.py            그룹 CRUD
│   │   ├── channels.py videos.py tags.py jobs.py
│   │   └── settings.py          그룹별 설정 (db/ai/prompt/polling/notify/digest)
│   ├── services/
│   │   ├── settings_manager.py  그룹별 설정 로더/캐시
│   │   ├── db_engine.py         그룹별 데이터 평면 엔진 레지스트리
│   │   ├── llm_client.py        litellm 클라이언트
│   │   ├── youtube_api.py       YouTube Data API 래퍼
│   │   ├── analyzer.py          그룹 컨텍스트 분석 파이프라인
│   │   ├── monitor_service.py   그룹 순회 폴링/분석
│   │   ├── notify_service.py    그룹별 텔레그램 발송
│   │   ├── digest_service.py    주간 리뷰
│   │   ├── job_logger.py
│   │   └── scheduler.py
│   ├── static/                  프론트 빌드 산출물
│   └── templates/               SPA 셸
├── migrations/
│   ├── control/                 app 스키마 DDL (groups, settings, jobstore)
│   └── pg/                       그룹 스키마마다 적용되는 데이터 평면 DDL
├── requirements.txt
├── README.md
├── .env.example
└── docs/
    └── architecture.md          본 문서
```

## 7. 환경변수 (.env)

```bash
# 제어 평면 PG 접속 (부트스트랩 필수)
CONTROL_DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/ytdb

# 설정 시크릿 암호화 키 (32바이트 base64 url-safe)
# 생성: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
FERNET_KEY=...

# 그룹에 봇 토큰이 미설정일 때 사용할 기본 텔레그램 봇 (선택)
DEFAULT_TELEGRAM_BOT_TOKEN=
```

부트 의존성: 제어 평면 PG가 접속 가능해야 앱이 정상 동작한다. 데이터 평면(그룹 스키마)은 그룹 설정 입력 후 멱등 적용된다.

## 8. 기존 데이터 채택 절차

1. ytdb를 기동하고 `default` 그룹을 생성한다.
2. `default` 그룹의 database 설정에 기존과 동일한 host/계정 + `schema=youtube`를 입력한다.
3. 스키마 적용을 실행하면 `ensure_schema()`가 `IF NOT EXISTS`로 동작하여 기존 테이블/데이터를 그대로 인식한다.
4. 별도 이관 스크립트 없이 기존 채널/영상/분석 데이터가 `default` 그룹에 노출된다.

## 9. 구현 단계 (Phase)

각 Phase 종료 시 검증을 통과하고 승인 후 다음으로 진행한다.

| Phase | 산출물 | 검증 기준 |
|-------|--------|-----------|
| P1 | 클린 스캐폴드, 제어 평면 PG(app 스키마), groups/settings 모델, 제어 엔진/세션 | 앱 부팅, 그룹 CRUD, 설정 저장/조회 동작 |
| P2 | SettingsManager 그룹키 일반화, DBEngineManager 서버당 공유 풀 + schema_translate_map | 같은 서버의 두 그룹이 단일 풀을 공유하면서 각자 스키마로 격리, ensure_schema 멱등 |
| P3 | 데이터 평면 PG 모델/마이그레이션 신규 작성 | default 그룹을 기존 schema로 지정 시 기존 데이터 자동 인식 |
| P4 | youtube_api, llm_client, analyzer 그룹 컨텍스트화 | 그룹별 AI/프롬프트로 영상 1건 분석 및 DB 저장 |
| P5 | 스케줄러 그룹 순회, 그룹별 알림/다이제스트 | 2개 그룹 동시 폴링 및 그룹별 봇으로 발송 |
| P6 | REST API, 그룹 선택 UI | UI에서 그룹 생성 → 채널 추가 → 분석 → 발송 E2E |

## 10. 위험 요소와 대응

| 위험 | 영향 | 대응 |
|------|------|------|
| 제어 평면 PG 다운 | 앱 부팅 불가 | restart=always, 헬스체크, 제어 DSN 검증 후 기동 |
| 그룹 증가에 따른 연결 슬롯 고갈 | PG 연결 부족 | 서버당 공유 풀(schema_translate_map)로 풀 수를 서버 수에 고정. pool_size/동시성으로 경합 조절 |
| FERNET_KEY 분실 | 시크릿 복호화 불가 | `.env` 키 별도 백업, 키 회전 시 전체 시크릿 재암호화 |
| 그룹 schema 오타로 빈 스키마 생성 | 데이터 분리 오류 | slug 기반 schema_name 자동 생성, 수동 입력 시 검증 |
| 그룹별 AI 키/모델 오설정 | 분석 실패 | 저장 전 연결 테스트 및 샘플 분석 통과 시에만 반영 |

## 11. 모태 대비 변경 요약

| 영역 | my-assistant (단일 그룹) | ytdb (다중 그룹) |
|------|--------------------------|-------------------|
| 설정 저장 | SQLite youtube_settings, UNIQUE(category,key) | PG app.settings, UNIQUE(group_id,category,key) |
| 그룹 개념 | 없음 (암묵적 단일) | app.groups 테이블로 명시 |
| 설정 로더 | 전역 싱글톤 | 그룹키 캐시 레지스트리 |
| DB 엔진 | 루프당 1개 | 루프 x 물리서버당 1개 (그룹은 schema_translate_map으로 공유 풀에서 격리) |
| 데이터 격리 | youtube 스키마 단일 | 그룹별 스키마 |
| 스케줄러 | 고정 job_id | 전역 잡의 그룹 순회 + 그룹별 알림/다이제스트 잡 |
| 분석 파이프라인 | 전역 설정 참조 | 그룹 컨텍스트 주입 |
| 알림 | 전역 봇 | 그룹별 봇/채팅 |
| 부트스트랩 | SQLite 파일 | 제어 평면 PG DSN(.env) |
