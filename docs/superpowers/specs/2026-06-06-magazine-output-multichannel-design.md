# 매거진형 출력 + 멀티채널 렌더링 설계

작성일: 2026-06-06

## 배경 / 문제

분석 결과를 (1) 웹앱에서 매거진형으로 가독성 좋게 보여주고, (2) 텔레그램에는
채널에 맞는 간소화 레이아웃 + "자세히 보기" 링크로 발송하는 것이 목표다.

현재 분석 본문은 `video_analysis.full_analysis_md` 한 덩어리 마크다운에
LLM이 `•`(불릿)와 `\n`(줄바꿈)을 **수동으로** 넣는 방식이다. 이 때문에:

- **렌더링이 채널마다 깨진다.** 웹의 `react-markdown`(remark-gfm)은 문단 내부의
  단일 `\n`을 공백(soft break)으로 처리하고, `•`는 마크다운 리스트 기호가 아니라
  평범한 텍스트라 `• A.\n• B.`가 `• A. • B.` 한 줄로 합쳐진다.
  (`frontend/src/pages/VideoDetail.tsx:258`)
- **그룹마다 형식이 제각각이다.** 실데이터(`agent_db.youtube.video_analysis`, 589행)
  확인 결과 경제 그룹은 `### 헤더 + **bold** + 리스트`(정식 마크다운이라 정상 렌더),
  마케팅 그룹은 `•`+단일`\n`(깨짐)을 쓴다. "규칙"이 프롬프트 산문으로만 강제돼
  그룹마다 마크다운을 제각기 발명하고 있다.
- **블롭은 쿼리·재가공이 어렵다.** 본 앱의 핵심 가치는 "DB에 규칙적으로 분리·축적되는
  구조화 데이터를 AI agent가 가공"하는 것이다(실데이터에 `summary_embedding` pgvector
  컬럼이 이미 존재). 자유 마크다운 블롭은 이 목표의 걸림돌이다.

## 핵심 원칙

**콘텐츠(의미) ↔ 표현(레이아웃) 분리.** LLM은 표현(마크다운/HTML)이 아니라
**의미(구조화 데이터)**를 생산하고, 각 채널은 그 데이터를 렌더링만 한다.
DB의 구조화 데이터가 단일 진실의 원천(single source of truth)이며,
웹/텔레그램/공유페이지는 그 데이터의 서로 다른 표현일 뿐이다.

## 목표

1. 분석 본문을 `full_analysis_md`(자유 블롭) → `analysis_sections`(구조화 배열)로 전환.
2. 분석마다 공유 가능한 서버렌더링 HTML 페이지 + 링크 생성(텔레그램 OG 미리보기 지원).
3. 텔레그램 발송에 간소화 옵션 + 공유 링크 첨부.
4. 단일 뷰모델 → 채널별 얇은 프리젠터로 렌더링 통일.
5. 사용자가 DB를 잘 활용하도록 프롬프트 설계 가이드 제공.

## 비목표

- 기존 `full_analysis_md` 컬럼·데이터 **제거하지 않음**(폴백용 보존).
- 과거 589행 데이터 강제 백필 안 함(렌더러 폴백으로 정상 표시).
- React 서버사이드렌더링(Node SSR) 도입 안 함(공유 페이지는 Python/Jinja2로 별도 렌더).
- 로그인/권한 기능 신규 구현 안 함(접근모드 컬럼만 미리 두고, 초기엔 `unlisted`만 동작).

---

## ① 데이터 모델: 구조화 콘텐츠

### `analysis_sections` (신규 컬럼)

`video_analysis.full_analysis_md`(자유 마크다운)를 대체하는 구조화 배열.

```json
"analysis_sections": [
  {
    "key": "overview",
    "title": "캠페인 개요",
    "bullets": [
      "싱가포르 통신사 Singtel이 OTT 'CAST.SG' 가입자 유치용 프로모션 영상임",
      "특정 드라마 방영 일정 고지 + 구독 혜택으로 플랫폼 활성화 목적"
    ]
  },
  { "key": "usp_target", "title": "핵심 소구점(USP) 및 타겟", "bullets": ["..."] }
]
```

- **shape는 전 그룹 공통**: `{ key: string, title: string, bullets: string[] }`.
- **key/title은 그룹 프롬프트가 정의**(경제=`한줄요약/주요주장/결론`, 마케팅=`개요/usp/크리에이티브/시사점`).
  shape만 강제하고 내용은 그룹 성격에 맞게 자유롭게.
- **`bullets` 항목은 인라인 마크다운 허용 문자열**(`**bold**`, `` `code` `` 등).
  경제 그룹의 "**제목**: 설명" 형태 주장도 한 문자열로 표현 가능. 객체로 과설계하지 않음(YAGNI).
- **`\n`/`•`를 절대 포함하지 않음** — 줄바꿈·불릿 기호는 렌더러가 100% 통제.
  불릿 기호(`•`)는 필수가 아니며, 각 채널 렌더러가 자신에게 맞는 표현(웹 `<li>`,
  텔레그램 `•`+줄바꿈, SSR `<li>`)을 입힌다. LLM은 가독성 좋은 **순수 문장**만 담는다.

이 패턴은 신규가 아니다. 실데이터의 `entities`가 이미
`{name, type, market, ticker}`처럼 "공통 shape + 그룹별 확장 키"를 JSONB로
저장 중이므로(프롬프트 예시는 `{type,name}`뿐이었음), 검증된 패턴의 반복이다.

### 컬럼 매핑 (LLM JSON 키 → DB 컬럼)

| JSON 키 | DB 컬럼 | 비고 |
|---------|---------|------|
| `one_line` | `one_line` | **필수** |
| `short_summary_md` | `short_summary_md` | **필수**, 텔레그램 요약용 |
| `analysis_sections` | `analysis_sections` (신규) | 웹/SSR 매거진 본문 |
| `headline` | `headline` | 카드 제목·OG title |
| `bullet_points` | `bullet_points` | 핵심 포인트 배열 |
| `key_points` | `key_points` | `[{timestamp, point}]` |
| `insights` | `insights` | 배열 |
| `entities` | `entities` | `{name,type,...확장}` |
| `sentiment`/`brand_tone` | `sentiment` | 자유 문자열(그룹별) |
| `tags` | (태그 테이블) | `{name,type,weight}` |
| `confidence_score` | `confidence_score` | 0.0~1.0 |
| `full_analysis_md` | `full_analysis_md` | **레거시·폴백 전용**(신규 분석은 비워도 됨) |

`REQUIRED_FIELDS`는 현행대로 `{one_line, short_summary_md}` 유지하고
`full_analysis_md`는 필수에서 제외(이미 검증에 없음). `analysis_sections`는 선택값으로,
없으면 폴백.

## ② 공유 HTML 링크 (SSR + OG + 접근모드)

### 라우트

```
GET /v/{group_slug}/{share_token}   → FastAPI + Jinja2 매거진 HTML
```

- `group_slug`로 제어평면 `groups`에서 `schema_name` 해석 → 해당 스키마
  `video_analysis`/`videos`를 `share_token`으로 조회. (토큰 전역유일 부담 회피)
- React SPA(`/g/:slug/...`)와 **별개의 공개 읽기전용 페이지**. SPA는 인증·그룹
  컨텍스트가 필요하고 JS 렌더라 OG 미리보기가 안 되므로 분리한다.

### 토큰 / 접근모드 (신규 컬럼, `videos` 테이블)

`videos`에 둔다(분석 재실행·upsert와 무관하게 안정적 식별자 유지).

| 컬럼 | 타입 | 기본 | 의미 |
|------|------|------|------|
| `share_token` | text unique null | null | `secrets.token_urlsafe(12)`, 추측 불가 |
| `share_visibility` | text | `'unlisted'` | `unlisted`/`restricted`/`private` |

- `unlisted`: URL 보유자 전체 열람(초기 기본).
- `restricted`: 로그인 필요(추후 인증 붙으면 게이트; 그 전엔 `private`와 동일 취급).
- `private`: 404.
- 토큰은 `analyzer.save_to_db`에서 `share_token`이 없을 때만 생성(upsert 시 보존).
  유니크 충돌 시 재생성 1회 재시도.

### OG 메타

Jinja `<head>`에 삽입 → 텔레그램/카톡/슬랙 리치 카드 자동 생성.

- `og:title` = headline (또는 title)
- `og:description` = one_line (없으면 short_summary 앞부분)
- `og:image` = thumbnail_url
- `og:type` = article, `og:url` = canonical 공유 URL

## ③ 채널별 렌더링 파이프라인

### 단일 뷰모델 → N개 프리젠터

```
analysis_sections(+필드)
        │
        ▼
build_view_model(video, analysis) ──► AnalysisView
        │     (레거시 폴백을 여기 한 곳에 격리)
        │
   ┌────┴───────┬──────────────┬───────────────┐
   ▼            ▼              ▼               ▼
 웹(React)  텔레그램 full  텔레그램 compact   SSR 매거진(Jinja)
 h3+ul/li   <b>제목</b>     headline+         OG카드 +
            +•줄바꿈        one_line+top3     풀 레이아웃
                           +📖링크
```

- **`build_view_model(video, analysis) → AnalysisView`**: 정규 뷰모델 dataclass.
  필드: `headline, one_line, short_summary, sections[], bullet_points, key_points,
  insights, entities, tags, sentiment, confidence, share_url, video_url, meta`.
- **레거시 폴백은 이 함수 한 곳에만**: `analysis_sections`가 있으면 그대로,
  없으면(과거 589행) `full_analysis_md`를 단일 마크다운 섹션으로 통과시킨다.
  프리젠터들은 항상 구조화된 `sections`만 받으므로 마크다운 추측 로직이 없다.
- **프리젠터는 얇게**:
  - 웹(React/TS): API가 `analysis_sections`를 그대로 내려주고 컴포넌트가
    각 섹션을 `<h3>` + `<ul><li>`로 렌더. `bullets`의 인라인 마크다운은
    `react-markdown` 인라인 렌더로 처리.
  - 텔레그램 full(Python): 섹션 → `<b>{title}</b>\n• {bullet}\n• {bullet}`.
    텔레그램 HTML은 실제 `\n`을 줄바꿈으로 살린다(`notify_service` 기존 경로).
  - 텔레그램 compact(Python): headline + one_line + `bullet_points` 상위 3개
    + `📖 자세히 보기: {share_url}`.
  - SSR 매거진(Jinja): 뷰모델 → 풀 레이아웃 HTML + OG 메타.

### 텔레그램 간소화 + 링크 (기능 2)

- 기존 `NotificationSettings.message_detail ∈ {full, compact}` 재사용.
- 신규 `NotificationSettings.include_share_link: bool = True` 추가 →
  full/compact 모두 본문 끝에 공유 링크 첨부.
- 기존 `send_telegram`의 `disable_web_page_preview: false` 유지 → SSR 페이지의
  OG 카드가 미리보기로 붙음.

### 레거시 호환 (웹의 `•` 깨짐 보정)

폴백으로 렌더되는 과거 `full_analysis_md` 중 `•`+단일`\n` 형식(마케팅 그룹)은
여전히 깨진다. 웹 `react-markdown`에 `remark-breaks` 플러그인을 추가해
단일 `\n`을 `<br>`로 렌더하면 레거시도 정상화된다. (신규 `analysis_sections`는
영향 없음 — 줄바꿈을 렌더러가 통제하므로.)

## 데이터 흐름

1. monitor → `analyzer.run`: LLM이 `analysis_sections` 포함 JSON 생산.
2. `analyzer.save_to_db`: `analysis_sections` 저장 + `videos.share_token` 보장.
3. 웹 API: 분석 필드(+`analysis_sections`) 응답 → React 매거진 렌더.
4. `notify_service`: `build_view_model` → 텔레그램 프리젠터(full/compact) + share_url.
5. SSR `GET /v/{slug}/{token}`: `build_view_model` → Jinja 매거진 + OG 메타.

## 마이그레이션 / 안전성

본 앱은 Alembic 없이 `db_engine.ensure_schema`의 **추가 전용(additive)** 패턴을 쓴다
(`app/services/db_engine.py:175` `additive_columns` 목록 + `ADD COLUMN IF NOT EXISTS`).

신규 컬럼 3개를 `additive_columns`에 추가(무손실, 무다운타임, 멱등):

```python
additive_columns = [
    ("channels", "notify_from", "timestamptz"),
    ("video_analysis", "analysis_sections", "jsonb"),
    ("videos", "share_token", "text"),         # UNIQUE 인덱스는 별도 멱등 생성
    ("videos", "share_visibility", "text"),
]
```

- `share_token` UNIQUE 제약은 `CREATE UNIQUE INDEX IF NOT EXISTS`로 멱등 생성.
- `share_visibility` 기본값은 애플리케이션 레벨(`'unlisted'`)에서 부여(기존 NULL 행은
  렌더 시 `unlisted`로 간주) 또는 `DEFAULT 'unlisted'`로 ALTER. 기존 589행 무손실.
- 기존 `full_analysis_md` 보존 → 폴백으로 과거 분석 정상 표시.
- SQLAlchemy 모델(`VideoAnalysis`, `Video`)에 대응 속성 추가.

## 설정 변경 (`settings_types.py`)

- `NotificationSettings.include_share_link: bool = True`
- (선택) 그룹 단위 `share.default_visibility: str = 'unlisted'` — 신규 토큰 생성 시 기본값.

## 에러 처리

- LLM이 `analysis_sections` 누락(구프롬프트/실패): 선택값이라 검증 통과,
  폴백 렌더러가 `full_analysis_md` 사용. 신·구 혼재 공존.
- 토큰 미존재/`private`: 404. `restricted`+미인증: (인증 도입 전) 404 처리.
- 텔레그램 길이 초과: 기존 `_build_full` truncation 로직 재사용하되 **공유 링크는 보존**.

## 테스트

- `build_view_model`: (a) 신규 sections, (b) 레거시 `full_analysis_md`, (c) 빈 값.
- 텔레그램 프리젠터: full/compact 줄바꿈 정확성, 링크 첨부, 길이 초과 시 링크 보존.
- SSR 엔드포인트: 토큰 해석, OG 태그 존재, `private`→404, 그룹 슬러그 매핑.
- 마이그레이션: 기존 스키마에 additive 컬럼·유니크 인덱스 멱등 적용.

---

## ④ 프롬프트 설계 가이드 (사용자용)

그룹 프롬프트를 작성/수정할 때 "출력값이 DB에 어떻게 저장되고 어떻게 활용되는지"를
이해하고 설계하기 위한 가이드. 그룹별 영상 성격은 달라도 **shape는 일정**해야
다운스트림 AI agent가 일관되게 가공할 수 있다.

### 1. 출력 계약(JSON 키 ↔ DB) 이해

- 위 "컬럼 매핑" 표의 **키만 DB에 저장**된다. 표에 없는 새 최상위 키를 출력해도
  코드가 무시하고 **버린다**. 새 데이터를 영구 저장하려면 컬럼·코드 추가가 필요하다.
- 따라서 프롬프트는 표의 키를 정확한 이름·타입으로 출력하도록 지시해야 한다.

### 2. 필수 vs 선택

- **필수**: `one_line`, `short_summary_md`. 누락 시 분석 실패 처리.
- **선택**: 그 외 전부. 그룹 성격에 안 맞으면 비워도 분석은 성공한다.
  (예: 타임스탬프가 무의미한 그룹은 `key_points` 생략)

### 3. 본문은 `analysis_sections`로 (블롭 금지)

- 본문을 `full_analysis_md` 자유 마크다운으로 쓰지 말고
  `analysis_sections: [{key, title, bullets[]}]`로 출력하게 한다.
- **`bullets`는 짧고 독립적인 문장 배열**. `•`, `-`, 번호, `\n`을 **직접 넣지 말 것**
  (렌더러가 줄바꿈·불릿을 그린다). 강조는 인라인 `**bold**`만 허용.
- `key`는 영문 스네이크케이스로 그룹 내 일관되게(예: `overview`, `risk`, `thesis`).
  `title`은 화면에 보일 한국어 제목.
- 좋은 예 / 나쁜 예:
  - 나쁨: `"bullets": ["• 첫째.\n• 둘째."]` (불릿·줄바꿈을 문자열에 박음)
  - 좋음: `"bullets": ["첫째 주장임", "**핵심**: 둘째 주장임"]`

### 4. 머신리더블 필드를 적극 활용

- `entities`: `{name, type}` + 그룹 확장 키(예: 경제 그룹 `market`, `ticker`).
  → 종목·기업 단위 집계·검색이 가능해진다.
- `tags`: `{name, type, weight}`. 그룹 공통 분류 체계를 프롬프트에 명시해
  태그 표기를 정규화(예: "5G요금제" 고정)하면 태그 페이지·필터 품질이 올라간다.
- `key_points`: `{timestamp, point}` — 영상 내 점프용.
- 이 필드들이 곧 "축적 데이터 기반 AI agent 가공"의 재료다. 자유 문장보다
  구조화 필드에 정보를 담을수록 활용도가 높다.

### 5. 고정값 필드 규칙

- `sentiment`/`brand_tone`: **자유 문자열**(그룹별 의미 부여 가능 — 경제 `bullish`,
  마케팅 `trendy`). enum 강제 안 함. 단 그룹 내에서는 값 집합을 일정하게 유지하도록
  프롬프트에 후보를 명시할 것(집계 일관성).
- `confidence_score`: 0.0~1.0 숫자 필수. 무조건 1.0 금지, 근거 약할수록 낮게.

### 6. 텔레그램·웹 어디에 쓰이는지 알고 쓰기

- `headline`(40자 내, 이모지 1~2): 카드 제목 + OG title.
- `one_line`: 한 줄 요약 + OG description + 텔레그램 compact 본문.
- `short_summary_md`: 텔레그램 요약 영역(400자 권장).
- `bullet_points`: 텔레그램 compact "상위 3개" 노출. 중요도 순으로 정렬해 출력.
- `analysis_sections`: 웹·SSR 매거진 본문(텔레그램 compact에는 안 나감 → 링크로 유도).

### 7. 품질 컨트롤 체크리스트(프롬프트에 포함 권장)

- [ ] 모든 텍스트 한국어 개조식(`~함`, `~임`).
- [ ] `bullets`에 `\n`·`•`·번호 없음(인라인 `**bold**`만).
- [ ] `analysis_sections`의 `key`가 그룹 내 일관.
- [ ] `entities`/`tags` 표기 정규화.
- [ ] 환각 방지: 영상에서 확인 안 된 사실은 생략/null, `confidence_score` 하향.
