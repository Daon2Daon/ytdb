# ytdb 멀티테넌트 확장 설계

작성일: 2026-07-03
전제 문서: `docs/architecture.md` (단일 운영자 + 다중 그룹 구조)

## 1. 목적과 범위

단일 운영자용 ytdb를 일반 가입자가 각자 독립적으로 사용하는 멀티테넌트 서비스로 확장한다.
핵심 전략: **기존 "그룹" 위에 "사용자(소유자)" 계층을 얹는다.** 데이터 평면(그룹별 스키마)은
변경하지 않고, 제어 평면(app 스키마)에 계정·쿼터·사용량 테이블을 추가한다.

### 확정된 설계 결정 (2026-07-03)

| 항목 | 결정 |
|------|------|
| 가입 방식 | 초대제 (관리자가 초대 링크 발급). 이메일 인증/SMTP 인프라 불필요 |
| AI 게이트웨이 | 관리자 소유 중앙 게이트웨이. 일반 사용자에게 AI 설정 비노출 |
| 토큰 트래킹 | 앱 레벨 사용량 원장(`app.ai_usage`)이 단일 소스. 게이트웨이 교체와 무관하게 동작 |
| 프롬프트 | 관리자가 만든 프리셋 중 선택만 허용. 커스텀 편집은 추후 |
| DB 설정 | 관리자 전용. 사용자 그룹은 관리 DB에 스키마 자동 프로비저닝 |
| 유료화 | 미정. plans 테이블만 선반영, 결제 연동 없음 |
| 테넌시 구조 | 스키마-per-그룹 유지. shared-schema 전환은 수천 그룹 도달 시 재검토 |
| 공유 분석 캐시 (2026-07-04 추가) | 동일 영상×프리셋 분석은 1회만 수행하고 구독 그룹 전체에 복사(§2.9). 실비용은 시스템 몫으로 원장 기록, 사용자 쿼터는 "전달 건수" 기준 |

### 비목표

- 결제 연동 (유료화 방식/시장 미정)
- BYOK (사용자 개인 API 키) — 추후 옵션으로 열어둠
- 커스텀 프롬프트 편집 — 추후
- 이메일 발송 인프라 (초대제이므로 불필요. 비밀번호 재설정은 관리자 리셋으로 대체)
- shared-schema + RLS 전환
- 기존 admin 그룹들의 동작 변경 (기존 그룹은 admin 소유로 귀속되어 그대로 동작)

## 2. 데이터 모델 (제어 평면 추가분)

모두 `app` 스키마. 데이터 평면(그룹별 스키마)은 무변경.

### 2.1 users

```sql
CREATE TABLE app.users (
    user_id       BIGSERIAL   PRIMARY KEY,
    email         TEXT        NOT NULL UNIQUE,      -- 로그인 ID
    password_hash TEXT        NOT NULL,             -- argon2
    display_name  TEXT,
    role          TEXT        NOT NULL DEFAULT 'user',    -- 'admin' | 'user'
    status        TEXT        NOT NULL DEFAULT 'active',  -- 'active' | 'suspended'
    plan_id       BIGINT      NOT NULL REFERENCES app.plans(plan_id),
    last_login_at TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 2.2 invitations (초대제)

```sql
CREATE TABLE app.invitations (
    invite_id  BIGSERIAL   PRIMARY KEY,
    token      TEXT        NOT NULL UNIQUE,   -- secrets.token_urlsafe(32)
    plan_id    BIGINT      NOT NULL REFERENCES app.plans(plan_id),
    memo       TEXT,                          -- 관리자용 메모 (예: 초대 대상)
    invited_by BIGINT      NOT NULL REFERENCES app.users(user_id),
    expires_at TIMESTAMPTZ NOT NULL,
    used_by    BIGINT      REFERENCES app.users(user_id),  -- 사용되면 기록
    used_at    TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

가입 흐름: 관리자가 초대 생성 → `/signup?token=...` 링크 전달 → 사용자가 email/비밀번호
입력 → 계정 생성 + 초대 소진(1회용). 만료·소진 토큰은 400.

### 2.3 plans / user_limits (쿼터)

```sql
CREATE TABLE app.plans (
    plan_id                 BIGSERIAL PRIMARY KEY,
    slug                    TEXT      NOT NULL UNIQUE,  -- 'free', 'unlimited'
    name                    TEXT      NOT NULL,
    max_groups              INT       NOT NULL,
    max_channels_total      INT       NOT NULL,         -- 사용자 전체 채널 합계
    max_analyses_per_day    INT       NOT NULL,
    max_video_minutes       INT       NOT NULL,         -- 초과 영상은 분석 skip
    monthly_cost_budget_usd NUMERIC(10,4) NOT NULL,     -- ai_usage 합산으로 강제
    min_poll_interval_min   INT       NOT NULL,         -- 채널 폴링 주기 하한
    is_default              BOOLEAN   NOT NULL DEFAULT FALSE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE app.user_limits (           -- 관리자의 사용자별 오버라이드 (모두 NULL 허용)
    user_id                 BIGINT PRIMARY KEY REFERENCES app.users(user_id) ON DELETE CASCADE,
    max_groups              INT,
    max_channels_total      INT,
    max_analyses_per_day    INT,
    max_video_minutes       INT,
    monthly_cost_budget_usd NUMERIC(10,4),
    min_poll_interval_min   INT,
    note                    TEXT,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

유효 한도 = `COALESCE(user_limits.값, plan.값)`. 시드: `free`(기본, is_default),
`unlimited`(admin용, 사실상 무제한 값).

### 2.4 ai_usage (사용량 원장 — 토큰 트래킹의 단일 소스)

```sql
CREATE TABLE app.ai_usage (
    usage_id      BIGSERIAL   PRIMARY KEY,
    user_id       BIGINT      REFERENCES app.users(user_id),  -- NULL = 시스템 몫(공유 캐시 분석, §2.9)
    group_id      BIGINT,                             -- 그룹 삭제 후에도 원장 보존 (FK 없음). 시스템 몫이면 NULL 가능
    purpose       TEXT        NOT NULL,               -- 'analysis' | 'digest' | 'tagging'
    model         TEXT        NOT NULL,
    input_tokens  INT         NOT NULL DEFAULT 0,
    output_tokens INT         NOT NULL DEFAULT 0,
    cost_usd      NUMERIC(12,6),                      -- 단가표 없으면 NULL (관리자 화면에 경고)
    video_pk      BIGINT,                             -- 분석 건이면 대상 영상
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ai_usage_user_created ON app.ai_usage (user_id, created_at);
```

기록 지점: `analyzer`(Gemini native 응답의 `usageMetadata`), `digest_service`(chat 응답의
`usage`). 응답에 토큰 정보가 없으면 0으로 기록하되 `cost_usd=NULL`로 남겨 관리자가 인지한다.
비용 환산은 전역 설정의 모델 단가표(모델명 prefix 매칭, $/1M tokens) 사용.

**귀속 원칙 (2026-07-04 확정):** 이 원장은 "실제 LLM API에 지불한 금액"의 신뢰원이다.
공유 캐시(§2.9)로 수행된 영상 분석은 `user_id=NULL`(시스템 몫)로 1회만 기록해, 관리자가
실 지출을 정확히 파악한다. 사용자별 사용량 카운트(향후 과금 기반)는 이 테이블이 아니라
§2.9의 `analysis_deliveries`가 담당한다. 다이제스트처럼 그룹 개인화된 호출은 기존대로
해당 사용자 몫으로 기록한다.

### 2.5 global_settings (전역 관리자 설정)

기존 `app.settings`는 그룹 스코프라 전역 값을 둘 곳이 없다. 동일 구조(key/value/value_enc/
value_type/is_secret)의 그룹 무관 테이블 `app.global_settings`를 추가한다. 저장 항목:

| key | 용도 |
|-----|------|
| youtube_api_key (secret) | 공용 YouTube Data API 키 |
| youtube_daily_quota | 일일 쿼터 예산 (기본 10000) |
| ai_base_url, ai_api_key(secret), ai_primary_model, ai_fallback_model, ai_tagging_model, ai_digest_model | 공용 AI 게이트웨이 |
| model_prices (json) | 모델 단가표 `{"gemini-2.5-flash": {"input": 0.30, "output": 2.50}, ...}` |
| telegram_bot_token (secret) | 공용 알림 봇 |
| signup_enabled | 초대 가입 일시 중지 스위치 |

우선순위: 그룹 설정에 값이 있으면 그룹 값(관리자만 설정 가능), 없으면 전역 값.
기존 admin 그룹들은 그룹별 설정을 이미 가지므로 동작 불변.

### 2.6 prompt_presets (프리셋)

```sql
CREATE TABLE app.prompt_presets (
    preset_id       BIGSERIAL PRIMARY KEY,
    name            TEXT      NOT NULL,
    description     TEXT,
    analysis_prompt TEXT      NOT NULL,
    digest_prompt   TEXT      NOT NULL,
    is_active       BOOLEAN   NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

사용자 그룹은 `prompts` 카테고리에 `preset_id`만 저장. SettingsManager의 `get_prompts`가
preset_id → 프리셋 본문으로 해석한다. 기존 admin 그룹의 직접 저장된 프롬프트는 그대로 인정
(preset_id 없으면 기존 키 사용).

### 2.7 telegram_destinations (공용 봇 연결 — 개인 DM + 그룹채팅방)

```sql
CREATE TABLE app.telegram_destinations (
    dest_id    BIGSERIAL   PRIMARY KEY,
    user_id    BIGINT      NOT NULL REFERENCES app.users(user_id) ON DELETE CASCADE,
    chat_id    TEXT        NOT NULL,
    chat_type  TEXT        NOT NULL,   -- 'private' | 'group'  (채널은 추후)
    title      TEXT,                   -- 그룹방 이름 (표시용)
    linked_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, chat_id)
);
```

연결 흐름 — 두 경우 모두 사용자 입장에서 클릭 두 번이며, 봇 토큰·chat_id 개념 비노출:

- **개인 DM(기본)**: 마이페이지 "텔레그램 연결" 클릭 → 서버가 1회용 토큰 발급(짧은 TTL) →
  `t.me/<공용봇>?start=<토큰>` 딥링크 → 사용자가 "시작" 탭 → 봇이 `/start <토큰>` 수신 →
  private chat_id 바인딩.
- **그룹채팅방**: "그룹방에 연결" 클릭 → `t.me/<공용봇>?startgroup=<토큰>` 딥링크 →
  텔레그램이 그룹 선택창 표시 → 선택 시 봇이 그룹에 추가되며 토큰이 담긴 `/start` 메시지가
  자동 전송 → 그룹 chat_id 바인딩(title 저장). 봇이 그룹에서 제거되면(my_chat_member 수신)
  해당 destination을 비활성/삭제 처리.
- **채널**: 봇을 채널 관리자로 수동 추가해야 하고 바인딩 토큰 전달 경로가 없어 절차가
  복잡하다. 일반 사용자 수요 대비 복잡도가 높아 **본 설계 범위에서 제외**(추후 고급 기능).

봇 업데이트 수신은 getUpdates 폴링(또는 webhook) 워커 1개가 담당한다. 모니터링 그룹의
notification 설정은 `dest_id`로 발송 대상을 선택한다(기본값: 첫 private destination).
사용자는 notification 설정에서 발송 대상 선택·on/off·발송 모드·조용 시간만 편집한다.

### 2.8 groups 변경

```sql
ALTER TABLE app.groups ADD COLUMN owner_user_id BIGINT REFERENCES app.users(user_id);
```

- 마이그레이션 시 기존 그룹은 모두 admin 계정 소유로 귀속.
- 사용자 그룹 생성: 이름만 입력 → slug/schema_name 자동 생성(`u{user_id}-{임의접미사}` /
  `youtube_u{user_id}_{접미사}` — 전역 unique 충돌 방지). database 카테고리는 제어 평면과
  같은 서버 DSN으로 자동 세팅, `ensure_schema`로 프로비저닝. 사용자에게 비노출.
- 사용자 그룹 삭제: UI에서 사용자 본인이 그룹명 재입력으로 확인하면 그룹 행 + 설정 삭제
  (기존 CASCADE)와 데이터 평면 `DROP SCHEMA`를 즉시 실행한다. 관리자 승인은 두지 않는다
  (본인 데이터). `ai_usage` 원장은 보존(group_id에 FK 없음).

### 2.9 공유 분석 캐시 + 중앙 채널 레지스트리 (2026-07-04 추가)

> 이 절 중 분석 캐시는 B-0a로 구현 완료. 중앙 채널 레지스트리는 B-0b로 분리 —
> 상세 설계(역방향 매핑 `channel_subscriptions`, `global_settings` 최소 골격,
> 시스템 키 폴백 등)는 `2026-07-05-b0b-channel-registry-design.md` 참조.

**문제:** 일반 사용자 확장 시 서로 다른 사용자가 동일 채널을 모니터링하면, 같은 신규
영상을 사용자 수만큼 중복 AI 분석하게 된다(영상 멀티모달 분석은 가장 비싼 호출).
사용자가 늘수록 비효율이 선형 증가한다.

**해법이 성립하는 근거:** 분석 결과는 (영상 × 프롬프트 × 모델)의 함수인데, 일반 사용자는
관리자 프리셋(§2.6) 중 선택만 가능하므로 분석 동일성 키가 유한하게 닫힌다:

```
캐시 키 = (video_id, preset_id, model)
```

프리셋이 3~5개면 사용자 수와 무관하게 영상당 분석은 최대 프리셋 개수. 추후 커스텀
프롬프트(유료 기능 후보)는 캐시를 우회해 본인 비용으로 분석 — 과금 체계와 자연 정합.

**채택안: 공유 캐시 + 그룹 사본 유지 (완전 중앙화 아님).** 그룹 스키마 구조는 무변경 —
각 그룹은 여전히 분석 결과 사본을 가지므로 기존 UI/알림/다이제스트/공유페이지가 그대로
동작한다. 분석 JSON 저장 중복은 AI 비용 대비 무시 가능. 완전 중앙화(공유 videos/analyses
테이블)는 데이터 평면 해체급 재작성이라 보류 — shared-schema 재검토 시점(수천 그룹)에 함께.

```sql
-- 전역 채널 레지스트리: 폴링은 채널당 1회 (YouTube API 쿼터도 절약)
CREATE TABLE app.channel_registry (
    channel_id      TEXT        PRIMARY KEY,          -- YouTube 채널 ID
    title           TEXT,
    last_polled_at  TIMESTAMPTZ,
    last_video_at   TIMESTAMPTZ,
    subscriber_groups INT       NOT NULL DEFAULT 0,   -- 구독 그룹 수 (참고용 캐시)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 공유 분석 캐시: 같은 (영상, 프리셋, 모델) 분석은 1회만
CREATE TABLE app.analysis_cache (
    cache_id      BIGSERIAL   PRIMARY KEY,
    video_id      TEXT        NOT NULL,               -- YouTube 영상 ID
    preset_id     BIGINT      NOT NULL REFERENCES app.prompt_presets(preset_id),
    model         TEXT        NOT NULL,
    status        TEXT        NOT NULL DEFAULT 'pending',  -- 'pending' | 'completed' | 'failed'
    analysis      JSONB,                              -- 완료 시 분석 결과
    input_tokens  INT,
    output_tokens INT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at  TIMESTAMPTZ,
    UNIQUE (video_id, preset_id, model)               -- 동시 분석 방지 락 역할
);

-- 사용자별 전달 원장: 쿼터 카운트·향후 과금의 기반
CREATE TABLE app.analysis_deliveries (
    delivery_id BIGSERIAL   PRIMARY KEY,
    user_id     BIGINT      NOT NULL REFERENCES app.users(user_id),
    group_id    BIGINT      NOT NULL,                 -- FK 없음 (그룹 삭제 후 보존)
    cache_id    BIGINT      NOT NULL REFERENCES app.analysis_cache(cache_id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX analysis_deliveries_user_created ON app.analysis_deliveries (user_id, created_at);
```

**흐름:**

1. 중앙 폴링: `channel_registry` 기준으로 채널당 1회 폴링(구독 그룹들의 주기 중 최솟값,
   플랜 하한 준수). 신규 영상 발견 시 구독 중인 각 그룹 스키마에 영상 행 삽입(pending).
2. 분석 claim 시 캐시 조회: `(video_id, preset_id, model)` →
   - **히트(completed)**: 캐시 결과를 그룹 스키마에 복사만. AI 호출 없음.
   - **미스**: UNIQUE 제약으로 `pending` 행 선점(INSERT ... ON CONFLICT DO NOTHING +
     rowcount 확인 — 동시 분석 레이스 방지) → 1회 분석 → 캐시 `completed` + 그룹 복사.
     실패 시 `failed` 기록 후 재시도 정책은 기존 분석 재시도와 동일.
3. 복사 시점마다 `analysis_deliveries`에 1행 기록 (히트/미스 무관 — 전달 자체를 카운트).

**비용·쿼터 귀속 (확정, 근거 포함):**

- **실비용(ai_usage)**: 캐시 미스로 발생한 실제 API 호출은 `user_id=NULL`(시스템 몫)로
  1회만 기록. → 관리자가 실제 LLM API 지출 금액을 정확히 관리하기 위함. 최초 트리거
  사용자에게 귀속시키면 "누가 먼저 걸리느냐" 복불복 과금이 됨.
- **사용자 쿼터(max_analyses_per_day)**: `analysis_deliveries`의 당일 COUNT 기준 —
  캐시 히트든 미스든 "전달받은 분석 건수"로 동일하게 카운트. → 공정하고 예측 가능하며,
  향후 과금 체계(사용량 기반)의 기반 데이터가 됨.
- 다이제스트 등 그룹 개인화 LLM 호출은 공유 불가(그룹별 필터·기간이 다름) → 기존대로
  해당 사용자 몫으로 `ai_usage`에 기록.

**기존 admin 그룹과의 호환:** admin 그룹은 프리셋이 아닌 직접 프롬프트를 쓸 수 있다(§2.6).
직접 프롬프트 그룹은 캐시를 우회해 기존 경로로 분석한다(커스텀 프롬프트와 동일 취급).
프리셋을 쓰는 그룹만 캐시에 참여한다.

## 3. 인증·권한

### 3.1 인증 개편

- 현행 `.env` 단일 계정(`AUTH_USERNAME/AUTH_PASSWORD`) → `app.users` 기반 로그인으로 교체.
- 세션 쿠키(SessionMiddleware) 방식 유지. 세션에 `user_id`, `role` 저장.
- 비밀번호 해시: argon2 (`argon2-cffi`). 로그인 시 `last_login_at` 갱신, `suspended`는 403.
- 부트스트랩: 부팅 시 users가 비어 있고 `AUTH_PASSWORD`가 설정돼 있으면 그 자격증명으로
  admin 계정을 시드한다. email은 `AUTH_USERNAME`이 이메일 형식이면 그대로, 아니면
  `{AUTH_USERNAME}@local`로 저장한다.
- 비밀번호 재설정: 관리자 콘솔에서 임시 비밀번호 발급(첫 로그인 시 변경 강제).

### 3.2 권한 의존성 (deps.py 확장)

- `require_user(request) -> User`: 세션 → users 조회. 모든 보호 라우터의 기본.
- `require_admin(user)`: role 검사. 관리자 라우터 전용.
- `get_owned_group_or_404(slug, user)`: 기존 `get_group_or_404`에 소유권 검사 추가.
  admin은 모든 그룹 접근 가능. **모든 그룹 스코프 API가 이 의존성으로 교체된다** —
  이것이 테넌트 격리의 핵심 지점.
- 그룹 목록 API는 본인 소유만 반환(admin은 전체 + 소유자 표시).

### 3.3 설정 카테고리 권한

| 카테고리 | user | admin |
|----------|------|-------|
| database | 접근 불가(404 수준 은닉) | 편집 가능 |
| ai_gateway | 접근 불가 | 편집 가능(그룹 오버라이드) |
| prompts | preset_id 선택만 | 직접 편집 + 프리셋 관리 |
| polling | youtube_api_key 제외, 주기는 플랜 하한 검증 | 전체 |
| notification | bot_token/chat_id 제외(자동), 나머지 편집 | 전체 |
| digest | 전체 편집 | 전체 |

## 4. 쿼터 강제 지점

| 한도 | 강제 지점 | 초과 시 |
|------|-----------|---------|
| max_groups | `POST /api/groups` | 400 + 안내 |
| max_channels_total | 채널 추가 API | 400 + 안내 |
| max_analyses_per_day | 스케줄러 pending claim 시 + 단일 URL 분석 API | 그룹 skip / 400. 당일 집계는 `analysis_deliveries`의 COUNT (§2.9 — 캐시 히트/미스 무관 "전달 건수" 기준) |
| max_video_minutes | 분석 파이프라인 진입 전 duration 검사 | 영상 상태를 skip 처리(사유 기록) |
| monthly_cost_budget_usd | 다이제스트 생성(스케줄 skip/수동 400) + 커스텀 프롬프트 재분석 400 + 직접 프롬프트 스케줄 skipped — 프리셋 캐시 분석은 시스템 몫이라 차단하지 않음(설계 2026-07-10 §7, 승인된 편차). 당월 `SUM(cost_usd)`은 본인 귀속분만 | 그룹 skip + 사용자에게 노출(마이페이지) |
| min_poll_interval_min | 채널/폴링 설정 저장 시 검증 | 400 |

집계 쿼리는 (user_id, created_at) 인덱스로 충분. 호출량이 커지면 일/월 롤업 테이블 추가(추후).

## 5. YouTube API 쿼터 관리

- `app.yt_quota_usage(date DATE PK, units INT)`: youtube_api 래퍼가 호출 유닛을 UPSERT 누적.
- 스케줄러 폴링 진입 시 당일 누적이 `youtube_daily_quota`의 80% 초과면 신규 폴링 skip
  (분석·알림은 계속), 100% 초과면 전면 중단 + job_logs 경고.
- 사용자별 폴링 주기 하한(min_poll_interval_min)이 1차 방어선.

## 6. 관리자 콘솔 / 사용자 UI

### 관리자 (role=admin에게만 노출되는 탭)

- 사용자: 목록(가입일/플랜/상태/사용량 요약), 정지/해제, 플랜 변경, 한도 오버라이드,
  임시 비밀번호 발급.
- 초대: 발급(플랜/만료 지정)·목록·회수.
- 사용량: 사용자별/모델별 토큰·비용 집계(기간 필터), YouTube 쿼터 현황.
- 전역 설정: AI 게이트웨이, 모델 단가표, 공용 봇, YouTube 키, 프리셋 관리.

### 일반 사용자

- 가입(초대 링크) / 로그인 / 비밀번호 변경.
- 마이페이지: 플랜·한도·당월 사용량(분석 건수, 비용), 텔레그램 연결 버튼.
- 그룹 화면: 본인 그룹만. 그룹 생성은 이름 입력만(내부 자동 프로비저닝).
- 설정 화면: 3.3의 권한에 맞게 카테고리/필드 축소 렌더링.

## 7. 구현 단계

각 Phase는 독립 배포 가능하며, Phase마다 별도 구현 계획을 수립한다.

| Phase | 내용 | 검증 기준 |
|-------|------|-----------|
| A. 계정·소유권 (완료 2026-07-04) | users/invitations, argon2 로그인, admin 시드, groups.owner_user_id, `get_owned_group_or_404` 전면 적용, 초대 가입 플로우, 관리자 사용자 목록(최소) | 초대→가입→로그인→본인 그룹만 접근. 타인 그룹 slug 접근 시 404. 기존 admin 그룹 무변경 동작 — 실 DB E2E 통과 |
| B-0a. 공유 분석 캐시 (완료 2026-07-04) | §2.9 — analysis_cache, analysis_deliveries, prompt_presets(§2.6, C에서 앞당김 — 캐시 키의 전제), 직접 프롬프트 그룹 캐시 우회 | 두 그룹이 같은 채널 구독 + 같은 프리셋 → 신규 영상 AI 호출 1회, 두 그룹 모두 분석 보유, deliveries 2행. 직접 프롬프트 그룹은 기존 경로 — 실 DB E2E 통과 |
| B-0b. 중앙 채널 레지스트리 (완료 2026-07-08, 실 DB E2E 통과) | §2.9 + 별도 설계 문서 — channel_registry 중앙 폴링(채널당 1회), channel_subscriptions 역방향 매핑, global_settings 최소 골격(시스템 YouTube 키·폴링 하한), 그룹 키 폴백 | 두 그룹이 같은 채널 구독 → 중앙 틱 1회에 채널 API 조회 1회, 두 그룹 모두 신규 영상 보유. 기존 단일 운영자 배포는 설정 변경 없이 폴링 무중단 — 실 SK telecom 채널로 관통 검증 완료 |
| B. 쿼터·관리자 콘솔 (구현 완료 2026-07-10 — 비용 한도는 C로 이연) | plans/user_limits, 5개 개수 기반 강제 지점(분석 카운트는 deliveries 기준, monthly_cost는 C), 관리자 콘솔(사용자 정지·플랜·한도·임시비번), 마이페이지 | free 플랜 사용자가 한도 초과 생성 시 400. 관리자 오버라이드 반영. 설계 `2026-07-09-phase-b-quota-admin-console-design.md` |
| C. AI 원장·전역 게이트웨이 (구현 완료 2026-07-11 — 설계 2026-07-10-phase-c-ai-usage-global-gateway-design.md) | ai_usage 기록(시스템 몫 규칙 포함), global_settings, 설정 권한 분리(3.3), 예산 강제, 사용량 대시보드 | 캐시 미스 분석 1건 → 원장 1행(user_id=NULL, 토큰/비용). 예산 초과 그룹 skip. 사용자 화면에 AI 설정 비노출 |
| D. 온보딩·운영 | 공용 봇 딥링크 연결, 사용자 그룹 자동 프로비저닝 마법사, YouTube 쿼터 카운터, 전 스키마 순회 마이그레이션 도구 | 신규 사용자가 UI만으로: 가입→그룹 생성→채널 추가→분석 결과 텔레그램 수신 |
| E. 유료화 (본 설계 범위 외) | 결제 연동, 약관/개인정보처리방침, 플랜 업그레이드 | 시장/방식 확정 후 별도 설계 |

Phase A가 모든 것의 전제. B-0가 B보다 앞서는 이유: B의 분석 쿼터 카운트 의미론("전달
건수")이 B-0의 deliveries 원장에 의존한다. prompt_presets는 캐시 키의 전제이므로 C에서
B-0로 앞당긴다. B와 C는 순서 교환 가능하나, 예산 강제(B의 일부)가 C의 원장에 의존하므로
B에서는 개수 기반 한도만, C에서 비용 기반 한도를 완성한다.

## 8. 위험 요소와 대응

| 위험 | 대응 |
|------|------|
| Gemini native passthrough 응답에 usageMetadata 누락 | 0 토큰 + cost NULL로 기록, 관리자 화면 경고. 지속되면 영상 길이 기반 추정치 폴백 |
| 예산 강제의 시차(claim 시점 집계 vs 동시 실행) | max_concurrent_analyses가 낮아(그룹당 순차) 초과 폭은 1건 수준. 허용 |
| 세션 탈취 | HTTPS 배포 시 `SESSION_HTTPS_ONLY=true`, 쿠키 SameSite=Lax(현행), 공개 배포 전 rate limit(로그인 시도) 추가 |
| 사용자 급증으로 스키마 수 증가 | 플랜 max_groups로 상한 제어. 수천 그룹 도달 시 shared-schema 재검토 |
| 공용 YouTube 키 쿼터 고갈 | 5절 카운터 + 폴링 자동 완화. 필요 시 Google 쿼터 증설 신청 |
| FERNET_KEY 분실 | 기존과 동일(키 백업). global_settings 시크릿도 동일 키 사용 |
| 관리자가 사용자 그룹 삭제/정지 시 데이터 처리 | 정지: 스케줄러 순회에서 owner status 검사로 제외. 삭제: 스키마 DROP은 별도 확인 단계 |
| 공유 캐시 동시 분석 레이스 | analysis_cache의 UNIQUE(video_id, preset_id, model) + INSERT ON CONFLICT 선점으로 이중 분석 방지. pending이 오래 지속되면(워커 사망) 타임아웃 후 재클레임 |
| 프리셋 수정 시 캐시 정합성 | 프리셋 본문 수정은 기존 캐시와 불일치 발생 → 프리셋은 불변(immutable)으로 하고 수정 시 새 preset_id 발급(구버전 비활성화). B-0 구현 시 강제 |

## 9. Phase A 구현 보류 사항 (2026-07-04 코드 리뷰에서 확인)

구현 리뷰에서 확인했으나 Phase A 범위에서는 의도적으로 보류한 항목. Phase B~D 진행 시 반영 검토.

| 항목 | 내용 | 수정 방향 |
|------|------|-----------|
| 초대 토큰 이중 사용 레이스 | 동시 가입 요청 2건이 같은 토큰을 각각 소진 가능 (심각도 Low-Medium, 초대제 소규모 서비스 전제) | `UPDATE ... WHERE used_at IS NULL` 조건부 클레임 + rowcount 확인 |
| 로그인 타이밍 오라클 | 미등록 이메일은 argon2 검증 없이 빠른 401 → 이메일 존재 여부 추정 가능 | 미스 경로에 고정 해시 더미 verify 추가 |
| 신규 설치 owner FK 부재 | fresh install은 create_all이 FK 없는 owner_user_id를 만들고 ALTER가 스킵됨 (업그레이드 설치만 FK 보유). 동작 영향 없음 — 접근 제어는 값 비교로 수행 | 카탈로그 확인 후 ADD CONSTRAINT 하는 가드 추가 |
| 자동 slug 충돌 시 UX | 일반 사용자 그룹 생성 시 24bit 접미사 충돌이면 409 (재시도 없음, 확률 극히 낮음) | 충돌 시 서버 측 재생성 1회 재시도 |

## 10. Phase B-0a 구현 보류 사항 (2026-07-04 코드 리뷰에서 확인)

| 항목 | 내용 | 수정 방향 |
|------|------|-----------|
| 전달 원장 중복 카운트 | analysis_deliveries에 (user_id, cache_id) 중복 방지가 없어, 같은 영상을 수동 재분석하면 전달 행이 추가로 쌓임. Phase B가 이 원장으로 일일 쿼터를 세면 재분석이 과카운트됨 | 완료(Phase B) — `UNIQUE(user_id, cache_id)` + `record_delivery` ON CONFLICT DO NOTHING. 기존 설치는 부팅 마이그레이션이 중복 정리 후 제약 추가 |
| 프리셋 캐시 다중 워커 staleness | preset_service의 TTL 캐시·invalidate는 프로세스 로컬. 멀티 워커 배포 시 비활성화 반영이 최대 60초 지연(본문은 불변이라 내용 오염은 없음) | 멀티 워커 도입 시 TTL 단축 또는 공유 캐시로 전환 |
| 캐시 완료 전 워커 사망 시 중복 비용 | run_and_save 커밋 후 complete_cached 전에 실패하면 캐시가 pending으로 남아 30분 뒤 재클레임·재분석(비용만, 정합성 무해) | 허용 (스펙 §8 타임아웃 정책과 일관) |
| 중앙 채널 레지스트리 미구현 | B-0 스펙 중 channel_registry(폴링 채널당 1회)는 B-0b로 분리 — 분석 캐시와 독립적으로 동작 | 완료(2026-07-08). 설계 `2026-07-05-b0b-channel-registry-design.md`, 계획 `2026-07-05-b0b-central-channel-registry.md`, 실 DB E2E 통과 |
| bootstrap_auth 부팅 하드 의존 | 시드 실패 시 앱 부팅 실패 (의도된 동작 — 스펙 §7 부트 의존성과 일관) | 유지 |
