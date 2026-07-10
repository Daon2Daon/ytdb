# Phase C 설계: AI 사용량 원장·전역 게이트웨이

- 상태: 확정 (2026-07-10, 브레인스토밍 완료 — 사용자 승인)
- 상위 스펙: `2026-07-03-multi-tenant-design.md` §2.4(ai_usage)·§2.5(global_settings)·§3.3(설정 권한)·§4(쿼터 강제)·§7 row C
- 선행: Phase B(쿼터·관리자 콘솔) — 프로덕션 배포·검증 완료(2026-07-10)

## 0. 범위·확정 결정

**범위 = 상위 스펙 row C 5항목만** (사용자 확정):
① `app.ai_usage` 원장(시스템 몫 귀속 규칙), ② 전역 AI 게이트웨이(global_settings 확장),
③ 설정 카테고리 권한 분리(§3.3), ④ `monthly_cost_budget_usd` 예산 강제,
⑤ 사용량 대시보드(관리자) + 마이페이지 비용 표시.

**제외**: YouTube API 쿼터 관리(상위 스펙 §5 yt_quota_usage) — 별도 Phase로 이연.

**브레인스토밍 확정 결정:**

| 결정 | 내용 |
|------|------|
| D1. 예산 귀속 | 사용자 월 예산에는 **본인 귀속 ai_usage만** 합산(다이제스트 + 커스텀/직접 프롬프트 분석). 프리셋 캐시 분석은 user_id=NULL(시스템 몫)이라 안 잡힘 — 건수 쿼터(max_analyses_per_day)가 이미 방어하므로 이중 과금 안 함 |
| D2. 단가표 | `global_settings`의 JSON 한 키(`ai_model_prices`). 별도 테이블·CRUD 없음(YAGNI) |
| D3. 대시보드 | 집계 테이블만(기간 필터), 차트 없음 |
| D4. 기록 방식 | `ai_usage_service` 단일 소유 + 호출부 3곳 명시 기록(A안). 계측 래퍼(B안) 기각 — 같은 파이프라인이 경로에 따라 시스템 몫/사용자 몫으로 갈려 래퍼가 귀속을 알 수 없음 |
| D5. purpose | `'analysis' \| 'digest'`만. 'tagging' 제외 — 태깅 LLM 호출이 코드에 실존하지 않음(태그는 분석 JSON에서 추출, `tagging_model` 설정은 미사용 잔재) |
| D6. 월 경계 | **KST 달력 월**(Asia/Seoul 1일 00:00) — Phase B의 KST 자정 일일 경계와 일관 |
| D7. 예산 차단 범위 | **상위 스펙 §4와 의도적 편차** — 아래 §7 참조. 사용자 승인됨 |

## 1. `app.ai_usage` 원장 (신규 테이블)

```sql
CREATE TABLE app.ai_usage (
    usage_id      BIGSERIAL   PRIMARY KEY,
    user_id       BIGINT      REFERENCES app.users(user_id),  -- NULL = 시스템 몫(공유 캐시 분석)
    group_id      BIGINT,                             -- FK 없음(그룹 삭제 후 원장 보존). 시스템 몫도 발생 그룹 기록
    purpose       TEXT        NOT NULL,               -- 'analysis' | 'digest'
    model         TEXT        NOT NULL,
    input_tokens  INT         NOT NULL DEFAULT 0,
    output_tokens INT         NOT NULL DEFAULT 0,
    cost_usd      NUMERIC(12,6),                      -- 단가 미상/토큰 미상이면 NULL(관리자 화면 경고)
    video_pk      BIGINT,                             -- 분석 건이면 대상 영상(그룹 스키마 pk, FK 없음)
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ai_usage_user_created ON app.ai_usage (user_id, created_at);
```

- ORM: `app/models/control/ai_usage.py` 신규, 기본값은 전부 `server_default`(B-0b 교훈 — raw insert가 ORM default 무시).
- 마이그레이션: 신규 테이블이므로 `ensure_control_schema()`의 `create_all`로 충분. ALTER 불필요.
- **귀속 원칙(상위 스펙 2026-07-04 확정 재인용)**: 이 원장은 "실제 LLM API 지불 금액"의 신뢰원.
  사용자별 사용량 카운트(건수)는 `analysis_deliveries`가 담당 — 역할 분리 유지.

## 2. usage 추출 (`llm_client.py` 확장)

- `AnalyzerResult`에 `input_tokens: int | None`, `output_tokens: int | None` 추가.
  `analyze_video_native`가 Gemini 응답 `usageMetadata`에서 추출:
  - input = `promptTokenCount`
  - output = `candidatesTokenCount` + `thoughtsTokenCount`(존재 시 — thinking 모델 대응)
  - 파싱 실패/부재 → None. **추출 실패가 호출을 깨뜨리지 않는다**(try/except).
- `ChatResult`에 동일 필드 추가 — `raw["usage"]`의 `prompt_tokens`/`completion_tokens`에서 추출(동일 원칙).
- `analyzer.AnalysisPipelineResult`에 두 필드 전파(경로 A 결과에서 복사).
- 클라이언트는 **추출만** 하고 기록하지 않는다(순수 유지) — 기록은 §4의 호출부.

## 3. `app/services/ai_usage_service.py` (신규 — 단일 소유 지점)

quota_service 패턴을 따른다. 공개 함수:

```
record_usage(*, user_id: int|None, group_id: int|None, purpose: str, model: str,
             input_tokens: int|None, output_tokens: int|None, video_pk: int|None) -> None
    # 제어평면 별도 세션으로 원장 1행 기록 + 단가 환산.
    # best-effort: 모든 예외를 삼키고 경고 로그만 — 기록 실패가 분석/다이제스트를 깨뜨리면 안 됨.
    # 토큰 None → 0으로 기록하되 cost_usd=NULL (상위 스펙 §2.4).

compute_cost_usd(model: str, input_tokens: int|None, output_tokens: int|None,
                 prices: dict) -> Decimal | None          # 순수 함수(테스트 용이)
    # prices = ai_model_prices JSON: {"모델prefix": {"input": $/1M, "output": $/1M}}
    # 최장 prefix 매칭(예: "gemini/gemini-2.5" > "gemini/"). 매칭 실패/토큰 None → None.

kst_month_start_utc(now: datetime) -> datetime            # 순수 함수 — KST 달력 월 1일 00:00의 UTC

month_cost_usd(session, user_id: int) -> Decimal
    # 당월(KST) SUM(cost_usd), NULL 행은 0 취급. (user_id, created_at) 인덱스 사용.

check_monthly_budget(session, user_id: int) -> None        # 초과 시 BudgetExceeded
    # 유효 예산 = quota_service.effective_limits 확장분(아래). None(무제한)이면 통과.

class BudgetExceeded(Exception)                             # QuotaExceeded와 동형(detail/limit/current)
```

**quota_service 확장**: `EffectiveLimits`에 `monthly_cost_budget_usd: float` 필드 추가.
`_merge_limits`의 `pick`은 int 캐스팅이므로 이 필드만 float 처리(별도 분기). plans/user_limits
컬럼은 Phase A/B에서 이미 선반영돼 있어 DB 변경 없음. admin/오버라이드 규칙 동일
(COALESCE, admin=None=무제한, 오버라이드 0도 존중).

**단가표 무결성**: 단가 미등록 모델은 cost_usd=NULL → 예산 강제가 그 행에 무력.
보완책은 §8 대시보드의 NULL-cost 경고 플래그(관리자가 단가 등록하도록 유도). 이는
상위 스펙 §2.4의 "관리자 화면에 경고" 요구 그대로.

## 4. 기록 3지점 배선 (`monitor_service.py`, `digest_service.py`)

| # | 지점 | user_id | purpose | 비고 |
|---|------|---------|---------|------|
| 1 | `_run_analysis` 직접/커스텀 프롬프트 경로(비캐시 분기) | `group.owner_user_id` (NULL이면 NULL=시스템) | analysis | run_and_save 성공 후 기록 |
| 2 | `_run_analysis_cached`의 claimed(캐시 미스) 경로 | **NULL(시스템 몫)** | analysis | + `complete_cached(cache_id, data, input_tokens, output_tokens)`로 시그니처 확장해 `analysis_cache.input_tokens/output_tokens` 배선(기존 미배선 컬럼, 상위 스펙 완료 기준) |
| 3 | `digest_service.synthesize_with_llm` chat 성공 후 | `group.owner_user_id` | digest | 현재 시그니처가 group_id만 받으므로 **호출자(generate_digest_for_group 등, Group 객체 보유)가 owner_user_id를 인자로 전달** — 함수 내 재조회 없음 |

- 캐시 **히트**는 LLM 호출이 없으므로 원장 0행. 전달 카운트는 기존 `analysis_deliveries`.
- 분석 **실패**(LiteLLMError 등)는 기록하지 않는다 — 응답이 없어 usage를 모름. 실패 재시도로
  성공하면 그때 1행. (과금 관점에서 실패 호출 비용은 미미, YAGNI.)
- 기록은 항상 데이터평면 트랜잭션 **밖**에서(커밋 후) best-effort 호출 — 제어평면 세션 분리.

## 5. 전역 AI 게이트웨이 (`global_settings` 확장)

**신규 전역 키** (`app/services/global_settings.py` 상수 + `_GLOBAL_KEYS`/`SECRET_KEYS` 확장):

| key | secret | 용도 |
|-----|--------|------|
| ai_base_url | | 공용 게이트웨이 URL |
| ai_api_key | ✓ | 공용 키 (Fernet 암호화 — youtube_api_key와 동일 경로) |
| ai_primary_model | | 분석 기본 모델 |
| ai_digest_model | | 다이제스트 모델(빈값=primary 사용, 기존 규칙 유지) |
| ai_model_prices | | JSON 단가표 `{"모델prefix": {"input": n, "output": n}}` ($/1M tokens) |

`tagging_model`은 미사용(D5)이라 전역화하지 않는다. 그룹 설정의 `temperature`/`max_tokens`/
`daily_budget_usd`도 전역화 제외 — 그룹별 튜닝 값 성격이고 현행 기본값으로 충분.

**해석 함수** `resolve_ai_gateway(group_id) -> AIGatewaySettings` (global_settings.py에 배치 —
`resolve_youtube_key` 패턴): 그룹 `ai_gateway` 설정에 **명시된(비어 있지 않은) 필드는 그룹 값**,
없으면 **전역 값**, 그것도 없으면 현행 하드코딩 기본값. 교체 지점:

- `analyzer.build_analysis_pipeline` (분석)
- `digest_service.synthesize_with_llm` (다이제스트)
- `monitor_service._run_analysis_cached`의 `get_ai_gateway` (캐시 키 model 결정 — **파이프라인과
  동일 함수를 쓰므로 캐시 키·실행 모델 일관성 자동 보장**)
- `routers/settings.py`의 모델 목록 조회 등 ai_gateway 참조부(있으면)

**부트스트랩** (`bootstrap_global_settings` 확장, B-0b 패턴): 전역 `ai_base_url`/`ai_api_key`가
미시드일 때 admin 소유 그룹(최소 group_id)의 `ai_gateway` 설정에서 시드. 멱등(이미 있으면 no-op).
`ai_model_prices`는 자동 시드하지 않음(단가는 관리자가 입력 — 미입력 시 cost NULL 경고로 표면화).

**관리자 API**: 기존 `GET/PUT /api/admin/global-settings`가 `_GLOBAL_KEYS` 루프라 키 추가만으로
노출된다. **마스킹 라운드트립 가드**(GET의 마스킹 값을 그대로 PUT하면 무시)는 기존 로직이 SECRET_KEYS
기준으로 동작하므로 `ai_api_key`에 자동 적용됨 — 회귀 테스트만 추가. `ai_model_prices`는 PUT 시
JSON 파싱 검증(형식 오류 400).

**프로덕션 영향**: 4개 운영 그룹 모두 그룹 ai_gateway 설정이 이미 있으므로(오버라이드 우선)
**동작 불변**. 신규 user 그룹부터 전역값이 적용된다.

## 6. 설정 카테고리 권한 분리 (§3.3 — 현재 강제 전무, 신규)

`routers/settings.py`에 user 의존성 추가(`require_user` — 이미 라우터 보호 체인에 있음,
핸들러 시그니처에 노출만) 후 카테고리·필드 가드:

| 카테고리 | 일반 user | admin |
|----------|-----------|-------|
| database | **404 (존재 은닉)** | 전체 |
| ai_gateway | **404 (존재 은닉)** | 전체 |
| prompts | `preset_id`만 GET/PUT — 다른 키는 응답 제외·PUT 400 | 전체 |
| polling | `youtube_api_key` 응답 제외·PUT 400 | 전체 |
| notification | `bot_token`/`chat_id` 응답 제외·PUT 400 | 전체 |
| digest | 전체 | 전체 |

구현: 카테고리→(user 차단 여부, user 허용 필드 화이트리스트/차단 필드 블랙리스트) 매핑 상수 1개 +
GET 응답 필터·PUT 사전 검증 헬퍼. 404는 `get_group_or_404`의 은닉 패턴과 일관.

**프런트** (`frontend/src/settings/defs.ts` + `Settings.tsx`): `me.role`(이미 AuthContext에 있음)
기반으로 user에게 database/ai_gateway 탭 숨김, prompts는 프리셋 선택만 렌더, polling의
youtube_api_key·notification의 bot_token/chat_id 필드 숨김. admin은 현행 그대로.

**프로덕션 영향**: 현 사용자 전원이 admin 그룹 소유자(admin 계정)라 화면·동작 변화 없음.
user2(akatestsm)는 그룹 0개.

## 7. 예산 강제 — ⚠️ 상위 스펙 §4와 의도적 편차 (사용자 승인 2026-07-10)

상위 스펙 §4는 "스케줄러 claim 시 + 단일 분석 API에서 그룹 skip"이나, D1(귀속분만 합산)과
조합하면 모순: 프리셋 스케줄 분석은 시스템 몫이라 사용자 예산에 안 잡히는데 그걸 예산 초과로
막으면 이중 처벌이고, 건수는 `max_analyses_per_day`가 이미 방어한다.

**확정: 예산 초과 시 "사용자 귀속 비용이 발생하는 행위"만 차단:**

| 지점 | 동작 |
|------|------|
| 다이제스트 스케줄 틱(`run_digest_tick_once` 내 그룹 처리 전) | skip + job log(사유: 월 예산 초과) |
| 다이제스트 수동 생성 API(`POST .../digests/generate`) | 400 + detail |
| 커스텀 프롬프트 재분석 API(`analyze-now`, `custom_prompt` 지정 시에만) | 400 + detail |
| 직접 프롬프트 그룹의 스케줄 분석(`_run_analysis` 비캐시 분기 진입 시) | 영상 `analysis_status='skipped'` + 사유 + SKIP job log — duration 게이트와 동일 패턴(핫루프 없음: skipped는 재클레임 안 됨) |

- 프리셋 기반 스케줄/즉시 분석은 **계속 허용**(시스템 몫, 건수 쿼터가 방어).
- 직접 프롬프트 그룹은 §3.3상 admin 전용(user는 preset_id만)이고 admin은 무제한이라 4행은
  실질 방어선이 아니라 방어적 완결성. skipped 영상은 예산 리셋 후 자동 재개되지 않음 —
  사용자가 재분석으로 트리거(문서화된 트레이드오프).
- 검사 주체: `ai_usage_service.check_monthly_budget` (admin/owner NULL/예산 무제한 → 통과).
- 마이페이지에 당월 비용·예산 노출(§8) — "예산 초과 사용자 노출" 요구 충족.

## 8. 사용량 대시보드(관리자) + 마이페이지

**`GET /api/admin/usage?window=this_month|last_month|30d`** (require_admin):

```json
{
  "window": "this_month", "start": "...", "end": "...",
  "rows": [ { "user_id": null|n, "email": null|"...", "model": "...", "purpose": "analysis",
              "calls": n, "input_tokens": n, "output_tokens": n,
              "cost_usd": n|null, "has_null_cost": bool } ],
  "total_cost_usd": n, "null_cost_row_count": n
}
```

- 집계: `GROUP BY user_id, model, purpose` 1쿼리 + users 조인(email). user_id NULL 행 = 시스템 몫.
- `has_null_cost`/`null_cost_row_count` = 단가 미등록 경고 플래그(상위 스펙 §2.4 요구).
- 기간: this_month/last_month는 KST 달력 월, 30d는 최근 30일.
- Admin.tsx에 "AI 사용량" 섹션(기간 셀렉트 + 테이블 + 총비용 + NULL 경고 배너). 차트 없음(D3).

**`GET /api/me/usage` 확장**: `month_cost_usd`(본인 귀속 당월 합산),
`limits.monthly_cost_budget_usd` 추가(무제한이면 limits=null 기존 구조 유지).
MyPage.tsx에 "당월 AI 비용 n / 예산 m" 행 추가(KST 월초 리셋 표기).

## 9. 테스트 전략

- **단위(DB 불필요)**: usageMetadata/usage 추출(정상·결손·thinking 토큰), `compute_cost_usd`
  prefix 매칭(최장 우선·미등록 NULL·토큰 None), `kst_month_start_utc`(월초·연말 경계),
  `_merge_limits` float 필드, BudgetExceeded 속성.
- **라우터**(dependency_overrides+monkeypatch, 기존 패턴): 설정 권한 매트릭스(user 404/필드
  필터/PUT 400 × admin 전체), admin usage 응답 형태, me/usage 확장, digest 수동 생성 400,
  analyze-now(custom) 400, global-settings 새 키 + ai_api_key 마스킹 라운드트립 회귀.
- **통합**: 기록 3지점 호출 검증(FakeSession/monkeypatch), record_usage 예외 삼킴(분석 성공 유지),
  complete_cached 토큰 전달.
- **전체 리그레션**: `.venv_e2e/bin/python -m pytest tests/ -q` (기준 244) + `cd frontend && npm run test && npm run build`.
- **실 DB E2E**(구현 완료 후 별도 체크포인트, 테스트 DB `100.115.13.102`만 — postgres-ytdb MCP 금지):
  ① 부팅 → ai_usage 생성·전역 ai 키 시드 확인, ② 실 분석 1건(캐시 미스) → 원장 1행(user NULL,
  토큰>0, 단가 등록 시 cost 계산) + analysis_cache 토큰 배선, ③ 다이제스트 1건 → 사용자 귀속 1행,
  ④ 예산 오버라이드 소액 설정 → 다이제스트 400/skip 실증, ⑤ user 계정으로 ai_gateway 설정 404,
  ⑥ 대시보드/마이페이지 응답 확인.

## 10. 배포·운영 고려

- 부팅 마이그레이션: ai_usage 생성(create_all) + 전역 ai 키 시드(admin 그룹에서) — 전부 멱등.
  롤백은 이전 이미지 재배포(신규 테이블·키는 구버전이 안 읽어 무해).
- 프로덕션 즉시 효과: 다음 캐시 미스 분석부터 시스템 몫 원장 적재 시작. 단가표는 관리자가
  전역설정에서 입력해야 cost 환산 시작(미입력 동안 NULL + 대시보드 경고).
- 기존 4그룹 동작 불변(그룹 ai_gateway 오버라이드 우선). 사용자 화면 AI 설정 비노출은
  user 계정에만 적용(현 프로덕션 실사용자는 admin뿐).

## 11. 영향 파일 요약

- 모델: `app/models/control/ai_usage.py`(신규), `control_db.py`(임포트 추가)
- 서비스: `ai_usage_service.py`(신규), `llm_client.py`(usage 추출), `analyzer.py`(전파·resolve 교체),
  `digest_service.py`(기록·게이트·resolve 교체), `monitor_service.py`(기록 2지점·skipped 게이트),
  `analysis_cache_service.py`(complete_cached 확장), `global_settings.py`(키·resolve_ai_gateway·부트스트랩),
  `quota_service.py`(EffectiveLimits 확장)
- 라우터: `settings.py`(권한 분리), `admin.py`(usage 집계·global-settings 검증), `auth.py`(me/usage 확장),
  `videos.py`(analyze-now 게이트), `digests.py`(generate 게이트)
- 스키마: `schemas/admin.py`, `schemas/auth.py`
- 프런트: `settings/defs.ts`·`Settings.tsx`(role 필터), `Admin.tsx`(사용량 섹션), `MyPage.tsx`(비용),
  `api/admin.ts`·`api/me.ts`
