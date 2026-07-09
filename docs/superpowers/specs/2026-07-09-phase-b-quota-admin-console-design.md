# Phase B 설계: 쿼터·관리자 콘솔 (2026-07-09)

상위 스펙 `2026-07-03-multi-tenant-design.md` §2.3(plans/user_limits)·§4(강제 지점)·
§6(콘솔/마이페이지)·§10(B-0a 보류 항목)을 구현 수준으로 구체화한다. Phase A에서
`app.plans` 테이블·시드(free/unlimited)·`users.plan_id`는 이미 존재하므로, Phase B의
신규 작업은 오버라이드 테이블·강제 로직·콘솔 확장이다.

## 1. 범위

**포함:**

- `app.user_limits` 테이블 + 유효 한도 해석(`COALESCE(user_limits.값, plan.값)`)
- 개수 기반 5개 쿼터 강제 지점(max_groups, max_channels_total, max_analyses_per_day,
  max_video_minutes, min_poll_interval_min)
- `analysis_deliveries`에 `UNIQUE (user_id, cache_id)` + upsert 전환 (§10 보류 항목 해소)
- 관리자 콘솔 확장: 사용자 정지/해제·플랜 변경·한도 오버라이드·임시 비밀번호 발급,
  플랜 한도값 편집, 사용자별 사용량 요약
- 마이페이지: 본인 플랜·유효 한도·현재 사용량(개수 지표)

**제외 (이연):**

| 항목 | 사유 | 행선지 |
|------|------|--------|
| monthly_cost_budget_usd 강제 | ai_usage 원장 필요 | Phase C (컬럼은 user_limits에 미리 포함, 강제만 보류) |
| 첫 로그인 비밀번호 변경 강제 | 간소화 — 임시 비번 발급 후 사용자가 마이페이지에서 자발 변경 | 생략 (필요 시 후속) |
| 플랜 생성/삭제 UI | free/unlimited 2종 시드 고정, 새 플랜은 SQL로 | Phase E 검토 |
| YouTube 쿼터 카운터(상위 스펙 §5) | 상위 스펙 Phase 표 그대로 | Phase D |

## 2. 데이터 모델

### 2.1 app.user_limits (신규)

상위 스펙 §2.3 그대로:

```sql
CREATE TABLE app.user_limits (
    user_id                 BIGINT PRIMARY KEY REFERENCES app.users(user_id) ON DELETE CASCADE,
    max_groups              INT,
    max_channels_total      INT,
    max_analyses_per_day    INT,
    max_video_minutes       INT,
    monthly_cost_budget_usd NUMERIC(10,4),   -- Phase C에서 강제. 스키마만 선반영
    min_poll_interval_min   INT,
    note                    TEXT,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

모든 한도 컬럼 NULL 허용 — NULL이면 플랜 값 사용. ORM 모델은 기존 Plan 모델 패턴
(server_default, B-0b 리뷰 교훈: raw insert 대비 DB 기본값 필수)을 따른다.

### 2.2 analysis_deliveries 중복 제거 (§10 보류 항목)

- 마이그레이션: 기존 (user_id, cache_id) 중복 행에서 **가장 오래된 행만 유지**하고
  삭제 → `UNIQUE (user_id, cache_id)` 제약 추가. `ensure_control_schema`의 기존
  idempotent ALTER 패턴(카탈로그 확인 후 추가)을 따른다.
- `record_delivery()`(app/services/analysis_cache_service.py:119)를
  `INSERT ... ON CONFLICT (user_id, cache_id) DO NOTHING`으로 전환.
- 효과: 같은 사용자가 같은 캐시 분석을 재수신(수동 재분석 등)해도 원장 행이 늘지
  않아, 일일 쿼터 집계가 단순 `COUNT(*)`로 정확해진다. 재분석은 새 가치가 아니라
  캐시 복사이므로 쿼터를 다시 쓰지 않는 것이 의미상 옳다.

### 2.3 plans 편집

기존 행의 한도값만 PATCH 허용. `slug`/`is_default`는 불변(시드 정합성 보호).
행 추가/삭제는 UI 밖(SQL).

## 3. 유효 한도 해석 — 신규 `app/services/quota_service.py`

현재 코드에는 플랜 해석 헬퍼가 없다(User.plan_id는 가입 시 할당에만 쓰임). 신규
서비스에 집중한다:

```python
@dataclass(frozen=True)
class EffectiveLimits:
    max_groups: int
    max_channels_total: int
    max_analyses_per_day: int
    max_video_minutes: int
    min_poll_interval_min: int
    plan_slug: str
    plan_name: str
    has_override: bool

async def effective_limits(session, user_id) -> EffectiveLimits
```

- users→plans JOIN + user_limits LEFT JOIN 쿼리 1회. 각 필드는
  `COALESCE(user_limits.x, plans.x)`.
- 검사 함수들도 이 모듈에 둔다(각각 현재 사용량 집계 + 한도 비교, 초과 시
  `QuotaExceeded(detail)` 예외 또는 bool 반환 — 라우터/스케줄러가 맥락에 맞게 400/skip 처리):
  - `check_group_quota(session, user)` — 소유 그룹 수(`groups.owner_user_id`)
  - `check_channel_quota(session, user)` — 소유 전 그룹의 채널 합계(그룹 스키마
    순회가 아니라 `app.channel_subscriptions`에서 소유 그룹 group_id IN 집계 —
    B-0b 역방향 매핑 재사용, 제어 평면 쿼리 1회)
  - `check_daily_analysis_quota(session, user)` — `analysis_deliveries`의 KST 당일
    COUNT (§4 참고)
  - `check_video_duration(limits, duration_seconds)` — 순수 함수
  - `validate_poll_interval(limits, interval_min)` — 순수 함수
- **admin(role=admin)은 모든 검사를 무조건 통과** — unlimited 플랜 시드와 이중
  안전망. 개발 모드 가상 admin(user_id=0)도 동일 경로로 통과.

**"당일" 기준: KST(Asia/Seoul) 자정.** 집계는
`created_at >= (KST 오늘 00:00을 UTC로 환산)` 범위 비교로 구현 — `(user_id,
created_at)` 기존 인덱스를 그대로 타며 함수 인덱스가 필요 없다.

## 4. 강제 지점 5곳

코드 조사(2026-07-09)로 확정한 삽입 위치:

| 한도 | 위치 | 초과 시 |
|------|------|---------|
| max_groups | `create_group()` app/routers/groups.py:36 — 비admin 분기, `session.add` 전 | 400 + 한도 안내 |
| max_channels_total | `add_channel()` app/routers/channels.py:34 — 그룹 owner 기준 | 400 + 한도 안내 |
| max_analyses_per_day | ① 스케줄러 claim 직전(app/services/monitor_service.py:670 인근, 그룹 owner 기준) ② `instant_analyze_video()` app/routers/videos.py:467 | ① 해당 owner의 나머지 pending을 그 틱에서 skip + job log 사유 기록 ② 400 |
| max_video_minutes | **신규 게이트** — monitor_service.py claim 직전(738 인근)에서 `video.duration_seconds > limits.max_video_minutes*60`이면 분석 skip. 즉시분석 경로에도 동일 적용 | 영상 상태 skip 처리 + 사유 기록(분석 실패와 구분되는 상태) |
| min_poll_interval_min | ① `add_channel()`의 `poll_interval_min` 인자(channels.py:69) ② polling 카테고리 설정 저장(app/routers/settings.py:79) | 400 |

주의점:

- **강제 기준 사용자 = 그룹 owner**(`groups.owner_user_id`) — 요청자가 admin이라도
  타인 그룹을 대리 조작하는 경우는 admin 통과 규칙이 적용된다(관리자 행위는 제한
  안 함).
- max_video_minutes skip은 실패(failed)와 구분되는 상태로 기록해 재시도 루프에
  다시 걸리지 않게 한다. 기존 영상 상태 체계에서 skip 계열 상태를 재사용하고,
  사유 텍스트에 한도값을 남긴다.
- min_poll_interval_min은 사용자 플랜 하한이며, B-0b의 전역 하한
  `central_poll_floor_min`과 별개로 **둘 다** 적용된다(실효 주기 = MAX(요청값,
  플랜 하한) 검증 후 저장, 중앙 폴러의 전역 클램프는 기존대로).
- 예산 강제의 시차 이슈(상위 스펙 §8)와 동일하게, daily 한도도 claim 시점 집계라
  동시 실행으로 1건 수준 초과 가능 — 허용.

## 5. 관리자 API 확장 (app/routers/admin.py)

| 엔드포인트 | 동작 |
|-----------|------|
| `PATCH /api/admin/users/{user_id}` | status(active/suspended), plan_id 변경. **자기 자신 정지·강등 금지 가드.** 정지는 다음 요청부터 차단 — `require_user`(app/routers/auth.py:65)의 status 검사가 이미 요청마다 수행되므로 추가 구현 불필요 |
| `PUT /api/admin/users/{user_id}/limits` | 오버라이드 upsert (부분 필드, NULL=플랜 값 사용) |
| `DELETE /api/admin/users/{user_id}/limits` | 오버라이드 전체 해제 |
| `POST /api/admin/users/{user_id}/temp-password` | `secrets` 기반 임시 비밀번호 생성 → argon2 해시 저장 → **평문은 이 응답에 1회만** 반환(관리자가 사용자에게 전달). 변경 강제 없음 |
| `PATCH /api/admin/plans/{plan_id}` | 한도값 편집(slug/is_default 제외) |
| `GET /api/admin/users` (확장) | 기존 응답에 사용량 요약 추가: 그룹 수, 채널 수, 당일 분석 건수, 오버라이드 유무 |

사용량 요약 집계는 사용자 수가 적은 초대제 전제로 목록 API에서 즉석 집계(그룹
수·채널 수는 제어 평면 GROUP BY 1회, 당일 분석은 deliveries GROUP BY 1회).

## 6. 사용자 API + 프런트

- **`GET /api/me/usage`** (app/routers/auth.py 또는 신규 me 라우터): 플랜명, 유효
  한도(EffectiveLimits), 현재 사용량(그룹 수/채널 수/당일 분석 건수). 개발 모드
  (인증 비활성)에서는 unlimited 상당 더미 반환.
- **프런트 (frontend/)**:
  - `Admin.tsx` 확장: 사용자 테이블 행에 정지/해제·플랜 변경·한도 오버라이드
    편집·임시 비번 발급 액션(임시 비번은 발급 직후 1회 표시), 플랜 한도 편집 섹션.
  - 신규 `MyPage.tsx`(라우트 `/me`): 플랜·한도·사용량 카드. 헤더에 진입점 추가.
  - 쿼터 초과 400의 detail 메시지는 기존 에러 표시 경로 그대로 노출.

## 7. 에러 처리 요약

| 상황 | 처리 |
|------|------|
| 쿼터 초과 (라우터 경로) | 400 + 어떤 한도·현재값·한도값을 담은 detail |
| 쿼터 초과 (스케줄러 경로) | 해당 owner 그룹 skip + job log 사유. 다음 날(KST) 자동 재개 |
| 영상 길이 초과 | 영상 skip 상태 + 사유. 재시도 루프 제외 |
| 오버라이드 대상 사용자 없음 | 404 |
| 자기 자신 정지/강등 시도 | 400 |
| suspended 사용자의 요청 | 기존 require_user가 403 (변경 없음) |
| suspended 사용자의 그룹 | 스케줄러 순회에서 owner status 검사로 제외(상위 스펙 §8 — 이번에 구현) |

## 8. 테스트 전략

- **단위**: EffectiveLimits COALESCE(오버라이드 유/무/부분), 5개 검사 함수
  경계값(한도-1/한도/한도+1), admin 통과, KST 자정 경계, deliveries upsert 멱등,
  자기 정지 가드.
- **통합** (FakeSession/기존 패턴): free 사용자 그룹/채널 초과 400 → 오버라이드
  후 통과. 스케줄러 daily 한도 도달 → 그 owner pending skip + job log, 타 owner
  정상. duration 초과 영상 skip 상태. suspended owner 그룹 순회 제외.
- **실 DB E2E** (테스트 DB `100.115.13.102`, 기존 e2e_a/e2e_b 재활용): free 플랜
  사용자 생성 → 그룹/채널/일일 분석 한도 관통 → 관리자 오버라이드 → 통과 확인.
  시나리오 상세는 구현 계획에서 확정.

## 9. 검증 기준 (상위 스펙 Phase 표)

free 플랜 사용자가 한도 초과 생성 시 400. 관리자 오버라이드 반영. 재분석이 일일
쿼터를 중복 소모하지 않음. 기존 admin 그룹(unlimited)은 동작 무변경.
