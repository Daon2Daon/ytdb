# ytdb 댓글 반응 분석 설계

작성일: 2026-07-28
전제 문서: `docs/architecture.md`, `docs/superpowers/specs/2026-07-03-multi-tenant-design.md`
참고 프로젝트: `../youtube-comment-crawling` (Next.js, 댓글 감정 분류 + 카테고리별 인사이트)

## 1. 목적과 범위

사용자가 원하는 영상의 **댓글 반응**을 AI로 빠르게 파악하는 기능을 ytdb에 추가한다.
긍정/부정/중립으로 분류한 비율과 카테고리별 요약·키포인트·인사이트, 그리고 각 카테고리의
대표 댓글을 한 화면에서 확인한다.

관리자는 제약 없이 사용하고, 일반 사용자에게는 월간 사용 제한을 둔다.

### 확정된 설계 결정

| 항목 | 결정 | 근거 |
|------|------|------|
| 진입점 | 영상 상세의 요약 카드 + 사이드바 신규 메뉴(URL 직접 입력) 둘 다 | 등록된 영상과 미등록 영상 모두 커버 |
| 저장 범위 | 분석 결과 + 카테고리별 대표 댓글 10개씩 스냅샷. **댓글 원문 전량은 저장하지 않음** | 재조회 UX를 살리면서 스키마 비대화 회피 |
| 신선도 | 분석 시점 댓글 총수를 스냅샷하고 현재값과 비교해 배너 노출 | 결과는 낡되, 낡았다는 사실이 표시됨 |
| 분석 깊이 | 관련성 상위 N건만 개별 분류 (기본 1,000건, 확대 가능) | 롱테일 댓글은 인사이트 기여가 없고 비용만 선형 증가 |
| 쿼터 | **가중 크레딧** (1회 = 댓글 1,000건). 기본 월 5회 | 비용 공정성(토큰 방식) + 실행 전 예측 가능성(횟수 방식) |
| 안전망 | 기존 `monthly_cost_budget_usd`를 그대로 재사용 | 이상 사용·모델 단가 급변 차단. 정상 사용자는 부딪히지 않음 |
| 관리자 무제한 | `quota_service.effective_limits()`가 admin에 `None`을 반환하는 기존 동작에 의존 | 별도 분기 불필요 |
| 결과 보존 | 영상당 최신 1건(`UNIQUE(video_pk)`). 재분석은 덮어쓰기 | 히스토리 수요가 확인되지 않음 (YAGNI) |
| 실행 방식 | `BackgroundTasks` + 프론트 폴링 | 1,000건 분석 30~60초 → 동기 응답은 프록시 타임아웃 위험 |

### 비목표

- 댓글 원문 전량 보관 및 검색
- 대댓글(replies) 분석 — 최상위 댓글만 대상
- 댓글 분석 결과의 텔레그램 발송 / 다이제스트 편입
- 분석 이력 보존 및 시계열 비교
- 실시간 갱신 (신선도 배너 + 수동 재분석으로 대체)
- 기존 영상 분석(`video_analysis`) 파이프라인 변경

## 2. 데이터 모델

### 2.1 그룹 스키마 — `comment_analyses` (신규)

`app/models/pg/comment_analysis.py`. 스키마는 심볼릭 토큰(`ytgroup`)으로 선언하여
`schema_translate_map`이 그룹별 실제 스키마로 변환한다(기존 데이터 평면 모델과 동일).

```sql
CREATE TABLE ytgroup.comment_analyses (
    analysis_id      BIGSERIAL   PRIMARY KEY,
    video_pk         BIGINT      NOT NULL UNIQUE
                                 REFERENCES ytgroup.videos(video_pk) ON DELETE CASCADE,
    status           TEXT        NOT NULL DEFAULT 'pending',  -- pending|running|done|failed
    requested_limit  INTEGER     NOT NULL,     -- 사용자가 고른 수집 상한
    fetched_count    INTEGER,                  -- 실제 수집된 최상위 댓글 수
    total_count      INTEGER,                  -- 분석 시점 영상 전체 댓글 수 (스냅샷)
    positive_count   INTEGER,
    negative_count   INTEGER,
    neutral_count    INTEGER,
    result           JSONB,                    -- §2.2 구조
    model            TEXT,                     -- 실제 사용된 모델명
    partial          BOOLEAN     NOT NULL DEFAULT FALSE,  -- 일부 배치 분류 실패
    error            TEXT,
    analyzed_at      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

`UNIQUE(video_pk)`가 영상당 1행을 보장한다. 재분석은 같은 행을 `status='running'`으로
되돌려 갱신하므로, 이 제약이 동시 중복 실행 방지의 1차 방어선을 겸한다.

### 2.2 `result` JSONB 구조

```json
{
  "categories": {
    "positive": {
      "summary": "…2~3문장…",
      "key_points": ["…", "…", "…"],
      "insights": "…2~3문장…",
      "top_comments": [
        {"author": "사용자명", "text": "댓글 본문", "like_count": 123,
         "published_at": "2026-07-20T10:00:00Z"}
      ]
    },
    "negative": { … },
    "neutral":  { … }
  },
  "order": "relevance",
  "batch_failures": 0
}
```

`top_comments`는 카테고리별 좋아요순 상위 10개. 이것이 저장되는 유일한 댓글 원문이다.
행당 크기는 대략 10~30KB 수준으로, JSONB 한 컬럼에 충분히 담긴다.

### 2.3 `videos.comment_count` (신규 컬럼)

신선도 판정에 매번 YouTube API를 호출하지 않기 위해, 영상 통계에 댓글 수를 추가한다.

- `youtube_api.get_video_details()`가 이미 `statistics`를 조회하므로 `commentCount`
  필드만 추가로 뽑아 `VideoMeta`에 싣는다. **추가 API 호출·유닛 소모 없음.**
- 영상 등록·통계 갱신 경로가 `videos.comment_count`를 채운다.
- 신선도 = `videos.comment_count` vs `comment_analyses.total_count` 비교.
  `comment_count`가 NULL(기존 영상, 아직 갱신 전)이면 배너를 표시하지 않는다.

### 2.4 제어 평면 — `app.comment_analysis_runs` (신규)

그룹 스키마는 서로 격리되어 있어 사용자별 월 집계를 데이터 평면에서 할 수 없다.
`app.analysis_deliveries`와 같은 패턴으로 제어 평면에 원장을 둔다.

```sql
CREATE TABLE app.comment_analysis_runs (
    run_id        BIGSERIAL   PRIMARY KEY,
    user_id       BIGINT      NOT NULL REFERENCES app.users(user_id),
    group_id      BIGINT      NOT NULL,   -- FK 없음: 그룹 삭제 후에도 원장 보존
    video_id      TEXT        NOT NULL,   -- YouTube video id (그룹 무관 전역 식별자)
    credits       INTEGER     NOT NULL,   -- 가중 차감량 = ceil(requested_limit / 1000)
    requested_limit INTEGER   NOT NULL,
    comment_count INTEGER,                -- 실제 수집된 댓글 수 (사후 기록)
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX comment_analysis_runs_user_created
    ON app.comment_analysis_runs (user_id, created_at);
```

`analysis_deliveries`와 달리 UNIQUE 제약을 두지 않는다. 재분석은 실제 비용이 다시
발생하는 새 사건이므로 행이 쌓이는 것이 맞다.

### 2.5 플랜 / 사용자 한도 컬럼

```sql
ALTER TABLE app.plans
    ADD COLUMN IF NOT EXISTS max_comment_analyses_per_month INTEGER NOT NULL DEFAULT 5,
    ADD COLUMN IF NOT EXISTS max_comments_per_analysis      INTEGER NOT NULL DEFAULT 1000;

ALTER TABLE app.user_limits
    ADD COLUMN IF NOT EXISTS max_comment_analyses_per_month INTEGER,   -- NULL = 플랜 값
    ADD COLUMN IF NOT EXISTS max_comments_per_analysis      INTEGER;
```

`unlimited` 플랜은 `PLAN_SEEDS`에서 각각 `100000` / `100000`으로 시드한다(사실상 무제한).
기본 플랜은 DEFAULT 값이 그대로 적용되므로 별도 백필이 필요 없다.

### 2.6 마이그레이션 경로

기존 코드베이스의 스키마 관리 방식을 그대로 따른다. 새 마이그레이션 도구를 도입하지 않는다.

| 대상 | 적용 경로 |
|------|-----------|
| 그룹 스키마 `comment_analyses` | `PgBase.metadata`에 모델 등록 → `db_engine.ensure_schema()`의 `_create_missing`이 신규 그룹에 자동 생성 |
| 기존 그룹 소급 적용 | 관리자 **도구** 탭의 `schema_migrator.migrate_all_schemas()` (`force=True`로 전 그룹 순회) |
| `videos.comment_count` | `ensure_schema()`의 `additive_columns` 목록에 `("videos", "comment_count", "bigint")` 추가 |
| 제어 평면 `comment_analysis_runs` | `Base.metadata`에 등록 → `ensure_control_schema()`의 `create_all`이 생성 |
| `plans` / `user_limits` 신규 컬럼 | `create_all`은 컬럼을 추가하지 않으므로 `ensure_control_schema()`에 명시 `ALTER TABLE … ADD COLUMN IF NOT EXISTS` 추가 |
| `PLAN_SEEDS` | 신규 키 2개 추가 (신규 설치 대상) |

## 3. 쿼터 모델 — 가중 크레딧

### 3.1 개념

댓글 500건 분석과 5,000건 분석은 실제 비용이 10배 차이인데 단순 횟수로는 동일하다.
반대로 순수 예산(USD) 방식은 사용자가 잔여량을 체감할 수 없고 실행 전 예측이 불가능하다.
두 방식의 역할을 나눈다.

| 층 | 역할 | 상태 |
|----|------|------|
| **가중 크레딧** | 사용자에게 보이는 계약. "이번 달 5회 중 2회 사용" | 신규 |
| **월 예산 USD** | 뒤에 있는 안전망. 이상 사용·단가 급변 차단 | 이미 구현됨 |

```
credits_for(limit) = max(1, ceil(limit / 1000))
```

| 선택 수량 | 차감 |
|---|---|
| 500건 | 1회 |
| 1,000건 | 1회 |
| 2,000건 | 2회 |
| 5,000건 | 5회 |

실행 전에 UI가 `2회 차감 · 잔여 5 → 3회`를 표시하므로 사용자는 대가를 정확히 알고 누른다.

### 3.2 `quota_service` 확장

`EffectiveLimits` dataclass에 필드 2개를 추가한다. `_merge_limits()`의 `pick()` 헬퍼가
`COALESCE(user_limits, plan)`을 이미 처리하므로 필드명만 추가하면 된다.

```python
max_comment_analyses_per_month: int
max_comments_per_analysis: int
```

**admin은 `effective_limits()`가 `None`을 반환하므로 모든 검사가 무조건 통과한다.**
"관리자는 제약 없이 사용" 요구가 기존 구조로 그대로 충족된다.

신규 함수:

```python
def credits_for(limit: int) -> int
async def count_monthly_credits(session, user_id) -> int
    # SUM(credits) WHERE user_id=? AND created_at >= kst_month_start_utc(now)
async def check_comment_analysis_quota(session, user_id, requested_limit) -> None
    # limits is None            → 통과 (admin)
    # requested_limit > 상한     → QuotaExceeded
    # used + credits_for > 한도  → QuotaExceeded
```

월 경계는 `ai_usage_service.kst_month_start_utc()`를 재사용한다(KST 달력 월).

### 3.3 예산 안전망

`ai_usage_service.check_monthly_budget(session, user_id)`를 분석 시작 전에 그대로 호출한다.
초과 시 기존 `BudgetExceeded` → 400.

## 4. 수집 파이프라인

`app/services/youtube_api.py` 확장.

### 4.1 `list_comment_threads`

```python
async def list_comment_threads(
    self, video_id: str, limit: int, order: str = "relevance"
) -> list[CommentMeta]
```

- `commentThreads.list`, `part=snippet`, `maxResults=100`, `order=relevance`
- `nextPageToken`으로 `limit` 도달까지만 반복. **정확히 `limit`에서 중단**한다
  (마지막 페이지에서 초과분은 잘라낸다)
- 기존 `_get(..., quota_units=1)` 경로를 타므로 **`yt_quota_service` 원장에 자동 기록**된다.
  1,000건 = 10유닛.
- 대댓글(`replies`)은 무시하고 최상위 댓글만 수집

`CommentMeta` = `(comment_id, author, text, like_count, published_at)`.

### 4.2 오류 매핑

| YouTube 오류 | 처리 |
|---|---|
| `commentsDisabled` | `CommentsDisabledError` → 400 "이 영상은 댓글이 비활성화되어 있습니다" |
| `videoNotFound` | 400 "영상을 찾을 수 없습니다" |
| `quotaExceeded` | 400 "YouTube API 할당량을 초과했습니다" |

세 경우 모두 **LLM 호출 전에 판정**되므로 크레딧을 차감하지 않는다(§6.2).

### 4.3 시스템 게이트

`yt_quota_service.system_hard_blocked()`가 True면 시작 단계에서 400으로 거절한다.
그룹이 자체 `polling.youtube_api_key`를 가진 경우 기존 `resolve_youtube_key(group_id)`
폴백 규칙을 그대로 따른다(그룹 키 우선, 없으면 시스템 키).

## 5. 분석 파이프라인

`app/services/comment_analysis_service.py` (신규). 기존 `llm_client`와 그룹
`ai_gateway` 설정을 그대로 사용한다.

### 5.1 1단계 — 분류

- 댓글을 **200건씩 배치**로 나눠 **순차 호출**한다. 병렬 호출은 게이트웨이 레이트리밋을
  건드릴 위험이 있고, 이 기능은 지연보다 안정성이 중요하다.
  1,000건 = 5배치로 30~60초, 상한 5,000건 = 25배치로 2~4분을 예상한다.
- 응답을 `{"0":"p","1":"n","2":"u"}` 형태의 **압축 인덱스 맵**으로 받는다.
  참고 프로젝트처럼 `commentId`/`confidence`를 건별로 되돌려받으면 출력 토큰이
  3~4배로 늘고, 출력 단가가 입력의 8배가량이라 비용을 지배한다.
- 배치 하나가 실패하거나 파싱 불가면 **그 배치만 전부 중립 처리**하고 계속 진행한다.
  `partial=True`, `result.batch_failures` 증가. 전체 분석을 버리지 않는다.
- 인덱스가 누락된 댓글은 중립으로 폴백한다.

### 5.2 2단계 — 인사이트

- 카테고리별 좋아요순 상위 댓글만 추려 **1회 호출**로 3개 카테고리의
  `summary` / `key_points` / `insights`를 한 응답에 받는다
- 카테고리가 비어 있으면(예: 부정 댓글 0건) 해당 카테고리는 LLM에 보내지 않고
  빈 결과로 채운다

### 5.3 프롬프트

그룹 설정 `prompts` 카테고리에 `comment_analysis_prompt` 키를 추가한다.
미설정이면 코드의 기본 프롬프트를 사용한다(`settings_types.PromptsSettings`에
`comment_analysis_prompt: str = ""` 추가, 기존 `analysis_prompt` 패턴과 동일).

그룹 프로필/어휘(`group_profile`)와의 연동은 이번 범위에 넣지 않는다.

### 5.4 사용량 기록

두 호출 모두 `ai_usage_service.record_usage()`로 기록한다.

- `purpose="comment_analysis"`
- `user_id` = **요청자** (시스템 몫이 아님 — 사용자가 직접 유발한 비용)
- `group_id`, `video_pk` 동봉

## 6. 실행 모델과 크레딧 생명주기

### 6.1 실행

FastAPI `BackgroundTasks`로 실행하고 프론트가 폴링한다.
[`InstantAnalyze.tsx`](../../../frontend/src/pages/InstantAnalyze.tsx)의 3초 간격 폴링
패턴을 재사용하되, **타임아웃은 10분**으로 둔다(상한 5,000건이 2~4분 걸리므로 5분은 빠듯하다).

```
POST → 검사 → 행 생성/갱신(status=running) → 원장 기록 → BackgroundTasks 등록 → 202
GET  → 현재 status/result 반환 (폴링 대상)
```

### 6.2 크레딧 차감 시점

| 시점 | 동작 |
|---|---|
| 요청 수신 | 쿼터·예산·게이트 검사 |
| 댓글 수집 **완료 후** | `comment_analysis_runs` 행 삽입 (차감 확정) |
| LLM 실패 | `status=failed` + 원장 행 **삭제 (환급)** |
| 성공 | `comment_count` 채우고 `status=done` |

수집 단계 실패(댓글 비활성화·0건·API 오류)는 원장 기록 **이전**이므로 자연히 무차감이다.
차감을 수집 이후로 미루는 이유는, 실행되지 않은 분석에 크레딧을 물리지 않기 위해서다.

요청 시점 검사와 원장 기록 사이에는 시간 간격이 있어, 한 사용자가 서로 다른 영상에
동시 요청을 날리면 모두 검사를 통과할 수 있다(§6.3의 409는 같은 영상만 막는다).
**원장 삽입과 같은 트랜잭션 안에서 `check_comment_analysis_quota`를 한 번 더 호출**하여
이 창을 닫는다. 재검사에서 걸리면 분석을 중단하고 400으로 되돌린다.

### 6.3 동시 실행 방지

같은 영상에 `status='running'` 행이 있으면 **409 Conflict**를 반환한다.
`UNIQUE(video_pk)` 제약이 경합 시의 최종 방어선이다.

## 7. API

```
POST  /api/groups/{slug}/videos/{video_pk}/comment-analysis   {limit}       → 202
GET   /api/groups/{slug}/videos/{video_pk}/comment-analysis                 → 상태+결과
POST  /api/groups/{slug}/comment-analysis/by-url   {video_url, limit}       → 202 + video_pk
GET   /api/groups/{slug}/comment-analyses          ?limit&offset            → 최근 목록
GET   /api/me/comment-credits    → {used, limit, unlimited, per_analysis_max}
```

`by-url`은 기존 `videos.py`의 `_extract_video_id()` + `_ensure_instant_channel()`을
재사용해 `videos` 행을 만든 뒤 위 경로로 합류한다. 이미 등록된 영상이면 그 행을 쓴다.
**저장 경로가 하나로 통일되어 두 진입점의 결과가 같은 화면에서 조회된다.**

라우터는 `app/routers/comment_analysis.py`로 분리한다.
`videos.py`는 이미 590줄이라 여기에 더 얹지 않는다.

## 8. 프론트엔드

| 파일 | 역할 |
|------|------|
| `pages/CommentAnalysis.tsx` | 사이드바 `💬 댓글 반응`. URL 입력 + 수량 선택 + 잔여 크레딧 + 최근 분석 목록 |
| `pages/VideoComments.tsx` | `/g/:slug/videos/:videoPk/comments`. 비율 막대 + 3탭(긍정/부정/중립) + 카테고리별 요약·키포인트·인사이트 + 대표 댓글 리스트 |
| `components/CommentAnalysisCard.tsx` | VideoDetail 하단 삽입 카드 |
| `api/comments.ts` | API 클라이언트 |

### 8.1 `CommentAnalysisCard` 상태

| 상태 | 표시 |
|---|---|
| 미분석 | 수량 드롭다운 + `분석하기` + `1회 차감 · 잔여 5 → 4회` |
| 실행 중 | 스피너 + 진행 안내 (폴링) |
| 완료 | 긍·부·중 비율 막대 + 건수 + `자세히 보기` |
| 완료 + 신선도 경고 | 위 + `2026-07-28 기준 1,204건 분석 · 이후 87건 추가됨` + `다시 분석` |
| 실패 | 오류 메시지 + `다시 시도` (크레딧 환급됨을 명시) |

### 8.2 수량 드롭다운

옵션은 `500 / 1,000 / 2,000 / 5,000`. `GET /api/me/comment-credits`의
`per_analysis_max`를 넘는 항목은 비활성화하고 `플랜 상한 1,000건`을 병기한다.
관리자는 `unlimited: true`라 전부 활성이며 차감 안내를 표시하지 않는다.

### 8.3 라우팅 / 내비게이션

- `App.tsx`에 라우트 2개 추가: `comment-analysis`, `videos/:videoPk/comments`
- `Layout.tsx`의 `NAV`에 `{ sub: 'comment-analysis', label: '댓글 반응', icon: '💬' }` 추가.
  `adminOnly` 없음 — 모든 사용자에게 노출
- 기존 `즉시 분석`(admin 전용)은 **건드리지 않는다**. 권한 구조 변경 없음

## 9. 오류 처리

| 상황 | HTTP | 메시지 | 차감 |
|---|---|---|---|
| 댓글 비활성화 | 400 | 이 영상은 댓글이 비활성화되어 있습니다 | 없음 |
| 댓글 0건 | 400 | 분석할 댓글이 없습니다 | 없음 |
| 크레딧 소진 | 400 | 이번 달 5회를 모두 사용했습니다 (KST 월초 초기화) | 없음 |
| 요청 수량 > 플랜 상한 | 400 | 플랜 상한(1,000건)을 초과합니다 | 없음 |
| 월 예산 초과 | 400 | 기존 `BudgetExceeded.detail` | 없음 |
| YouTube 하드 게이트 | 400 | YouTube API 할당량을 초과했습니다 | 없음 |
| 이미 실행 중 | 409 | 이미 분석이 진행 중입니다 | 없음 |
| LLM 배치 일부 실패 | 200 | `partial=true` 배지 표시 | 정상 차감 |
| LLM 전체 실패 | — | `status=failed` + `error` | **환급** |

## 10. 비용 추정

댓글 1,000건 기준 1회 분석(추정치 — 그룹의 모델 설정에 따라 달라진다):

| 단계 | 입력 토큰 | 출력 토큰 |
|---|---|---|
| 분류 (200건 × 5배치) | 약 55K | 약 5K (압축 형식) |
| 인사이트 | 약 30K | 약 2K |

Flash급 단가(입력 $0.30 / 출력 $2.50 per 1M) 기준 **1회 약 $0.04**.
월 5회 = 사용자당 **약 $0.2/월**. Pro급 모델을 쓰는 그룹은 대략 10배로 본다.

참고 프로젝트 방식(건별 `commentId`+`confidence` 반환)이면 출력이 20K 토큰대로 늘어
1회 $0.08 수준이 된다. §5.1의 압축 형식이 이 차이를 만든다.

YouTube 유닛은 1,000건에 10유닛으로, 일 10,000유닛 한도 대비 무시할 수준이다.

## 11. 테스트 계획

**단위 (pytest)**

- `credits_for` 경계: 0, 1, 999, 1000, 1001, 2000, 5000
- `check_comment_analysis_quota`: admin(`None`) 통과 / 수량 초과 / 크레딧 초과 / 경계 정확히 도달
- `count_monthly_credits`의 KST 월 경계 (월말 23:59 KST, 월초 00:00 KST)
- `list_comment_threads`가 `limit`에서 정확히 멈추는지 (페이지 경계 초과분 절단)
- 분류 응답 파싱: 정상 / 잘린 JSON / 인덱스 누락 / 알 수 없는 라벨 → 중립 폴백
- 배치 1개 실패 시 나머지 배치가 살아남고 `partial=True`가 서는지
- 크레딧 환급: LLM 실패 시 원장 행이 삭제되는지
- `commentsDisabled` 시 원장 행이 애초에 생기지 않는지

**프론트 (vitest)**

- `CommentAnalysisCard` 상태 5종 렌더
- 수량 드롭다운의 한도 초과 항목 비활성 / 관리자 전체 활성
- 차감 안내 문구가 선택 수량에 따라 갱신되는지
- 신선도 배너 판정: `comment_count` NULL / 동일 / 증가

## 12. 기존 동작 영향 범위

| 영역 | 영향 |
|---|---|
| 사이드바 | 항목 1개 추가 (7 → 8). 기존 항목·권한 무변경 |
| VideoDetail | 하단에 카드 1개 추가. 기존 섹션(요약/상세 분석/분석 정보) 순서·내용 무변경 |
| 영상 분석 파이프라인 | **무변경** |
| 스케줄러 / 폴링 / 알림 / 다이제스트 | **무변경** |
| 기존 테이블 | 무변경. `videos`에 nullable 컬럼 1개만 추가 |
| 기존 API | 무변경 (신규 엔드포인트만 추가) |
| `즉시 분석` (admin 전용) | **무변경** |

## 13. 구현 순서

1. **데이터 계층** — 모델 2개, `videos.comment_count`, 플랜/한도 컬럼, `ensure_control_schema` ALTER, `PLAN_SEEDS` 갱신
2. **쿼터 계층** — `EffectiveLimits` 확장, `credits_for`, `count_monthly_credits`, `check_comment_analysis_quota` + 단위 테스트
3. **수집** — `list_comment_threads`, `comment_count` 파싱, 오류 매핑 + 테스트
4. **분석** — `comment_analysis_service` 2단계 파이프라인, 프롬프트 기본값, `ai_usage` 기록 + 테스트
5. **API** — `routers/comment_analysis.py`, `by-url` 합류, `/api/me/comment-credits`
6. **프론트** — `api/comments.ts` → `CommentAnalysisCard` → `VideoComments` → `CommentAnalysis` → 라우트·NAV 등록
7. **검증** — 관리자/일반 사용자 각각 E2E, 크레딧 소진·환급·신선도 배너 실제 확인

## 14. 추후 검토 (이번 범위 밖)

- 분석 이력 보존 및 시점 간 여론 변화 비교
- 댓글 반응 요약의 텔레그램 발송 / 다이제스트 섹션 편입
- 그룹 프로필·어휘를 반영한 도메인 특화 분류(단순 긍/부/중립을 넘어선 축)
- 대댓글 포함 분석
- 결과 CSV/Excel 내보내기 (참고 프로젝트에 존재)
