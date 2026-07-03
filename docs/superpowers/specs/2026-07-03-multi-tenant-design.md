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
    user_id       BIGINT      NOT NULL REFERENCES app.users(user_id),
    group_id      BIGINT      NOT NULL,               -- 그룹 삭제 후에도 원장 보존 (FK 없음)
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

### 2.7 telegram_links (공용 봇 연결)

```sql
CREATE TABLE app.telegram_links (
    user_id    BIGINT      PRIMARY KEY REFERENCES app.users(user_id) ON DELETE CASCADE,
    chat_id    TEXT        NOT NULL,
    linked_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

연결 흐름: 마이페이지에서 "텔레그램 연결" 클릭 → 서버가 1회용 토큰 발급(메모리/짧은 TTL) →
`t.me/<공용봇>?start=<토큰>` 딥링크 → 봇 getUpdates(또는 webhook)에서 `/start <토큰>` 수신 →
chat_id를 계정에 바인딩. 사용자 그룹의 알림은 공용 봇 + 이 chat_id로 발송한다.
사용자는 notification 설정에서 on/off·발송 모드·조용 시간만 편집한다.

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
| max_analyses_per_day | 스케줄러 pending claim 시 + 단일 URL 분석 API | 그룹 skip / 400. 당일 집계는 `ai_usage`의 purpose='analysis' COUNT |
| max_video_minutes | 분석 파이프라인 진입 전 duration 검사 | 영상 상태를 skip 처리(사유 기록) |
| monthly_cost_budget_usd | 스케줄러 claim 시 + 단일 분석 API. 당월 `SUM(cost_usd)` | 그룹 skip + 사용자에게 노출(마이페이지) |
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
| A. 계정·소유권 | users/invitations, argon2 로그인, admin 시드, groups.owner_user_id, `get_owned_group_or_404` 전면 적용, 초대 가입 플로우, 관리자 사용자 목록(최소) | 초대→가입→로그인→본인 그룹만 접근. 타인 그룹 slug 접근 시 404. 기존 admin 그룹 무변경 동작 |
| B. 쿼터·관리자 콘솔 | plans/user_limits, 6개 강제 지점, 관리자 콘솔(사용자/초대/한도) | free 플랜 사용자가 한도 초과 생성 시 400. 관리자 오버라이드 반영 |
| C. AI 원장·전역 게이트웨이 | ai_usage 기록, global_settings, 설정 권한 분리(3.3), prompt_presets, 예산 강제, 사용량 대시보드 | 분석 1건 → 원장 1행(토큰/비용). 예산 초과 그룹 skip. 사용자 화면에 AI 설정 비노출 |
| D. 온보딩·운영 | 공용 봇 딥링크 연결, 사용자 그룹 자동 프로비저닝 마법사, YouTube 쿼터 카운터, 전 스키마 순회 마이그레이션 도구 | 신규 사용자가 UI만으로: 가입→그룹 생성→채널 추가→분석 결과 텔레그램 수신 |
| E. 유료화 (본 설계 범위 외) | 결제 연동, 약관/개인정보처리방침, 플랜 업그레이드 | 시장/방식 확정 후 별도 설계 |

Phase A가 모든 것의 전제. B와 C는 순서 교환 가능하나, 예산 강제(B의 일부)가 C의 원장에
의존하므로 B에서는 개수 기반 한도만, C에서 비용 기반 한도를 완성한다.

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
