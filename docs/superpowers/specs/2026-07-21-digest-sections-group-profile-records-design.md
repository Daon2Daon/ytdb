# Digest 섹션 빌더 · 그룹 프로필 부트스트랩 · 범용 레코드 설계

- 작성일: 2026-07-21
- 상태: 설계 검토 중
- 범위: 3단계 로드맵 전체. 구현 계획(plan)은 단계별로 분리 작성한다.

## 배경 / 문제

실측 근거(프로덕션 DB 2026-07-21 기준):

1. **알림은 커스터마이징이 되는데 digest는 안 된다.** 알림은 `message_template.fields`
   순서 배열 + `FIELD_RENDERERS` 레지스트리 + `TemplateBuilder` UI로 데이터와 표현이
   분리돼 있다. digest는 표현(4개 고정 섹션)이 `DEFAULT_DIGEST_PROMPT`에 하드코딩되고
   산출물이 `summary_md` 단일 블롭이라 항목 추가/제외/순서 조정이 불가능하다.
2. **일반 사용자는 프롬프트를 입력하지 않는다.** knowledge 그룹(28)은 시드된 기본
   분석 프롬프트 원문 그대로(1,320자), digest_prompt 빈 값, digest configs `[]`,
   digest 0건. 기본 digest 프롬프트는 "경제·투자 애널리스트" 페르소나에
   "주목할 종목·이슈" 섹션 — 비투자 그룹에 부적합한 invest 편향.
3. **파워유저의 digest는 이미 원안(불릿 배열)보다 복잡하다.** telco 주간 리뷰는
   회사별 서브섹션(SKT/LG U+ 각 4속성) + 지역별 그룹핑, brand는 여러 문단 산문 섹션.
   둘 다 정성 들인 커스텀 digest_prompt를 운영 중 — 이를 깨면 안 된다.
4. **데이터 품질 문제가 실재한다.** 엔티티 표기 분열(소프트뱅크 28 vs SoftBank 19,
   SK텔레콤 42 vs SK telecom 13), sentiment 어휘 표류(brand: 긍정 79 vs Positive 60).
5. **정보 손실이 실재한다.** digest 프롬프트는 영상 40건까지만 포함
   (`_MAX_VIDEOS_IN_PROMPT`) — telco digest_pk 12는 82건 중 절반이 "외 N건"으로 유실.
6. **행 단위 질의가 불가능하다.** `entities`/`insights`는 영상당 jsonb 블롭.
   invest 스키마(holdings/journal_entries — 한 행 = 한 사실)처럼 "이번 달 특정 기업
   언급 전부" 같은 질의·집계가 안 된다.

## 설계 원칙

**사용자는 작성자(author)가 아니라 선택자(selector)다.**

| 층 | 대상 | 하는 일 |
|---|---|---|
| L0 무설정 | 대부분의 일반 사용자 | 아무것도 안 함. 그룹 이름·카테고리만으로 시스템이 전부 구성 |
| L1 선택자 | 관심 있는 사용자 | 준비된 항목을 메뉴에서 추가/제외/순서 조정 (TemplateBuilder UX 통일) |
| L2 작성자 | 파워유저·관리자 | 페르소나 텍스트, 섹션 지침, record_schema, 전체 커스텀 프롬프트 |

- 생성형 입력(프롬프트·스키마·사전)은 시스템(LLM 부트스트랩 + 자동 축적)이 만들고,
  사용자는 선택·순서 조정·원클릭 승인만 한다.
- 모든 자동화는 실패 시 현행 동작으로 폴백한다. 현행보다 나빠지는 경로는 없다.
- 그룹별 동적 DDL은 하지 않는다. 테이블 구조는 전 그룹 동일, 차이는 데이터
  (record_schema, 사전, 어휘)로 표현한다.

## 전체 구성 요소 맵

```
[Phase 1]
  그룹 프로필(app.settings category='profile')  ← bootstrap_service (LLM 1회)
      ├─ persona          → digest 프롬프트 조립(2층)
      ├─ digest_sections  → DigestScheduleConfig.sections 기본값
      └─ (Phase 2에서 확장: record_schema, vocab)
  digests.digest_sections(jsonb)  ← 구조화 산출물
  digest_view.py                  ← 정규 뷰모델(웹/텔레그램/SSR 공용)
  DigestSectionBuilder(UI)        ← TemplateBuilder 일반화

[Phase 2]
  {schema}.analysis_records       ← 한 행 = 한 사실
  {schema}.entities               ← 자동 축적 엔티티 사전
  records_extractor               ← 2차 경량 LLM 패스(분석 산출물 텍스트 입력)
  vocab mapper                    ← 저장 시 통제 어휘 매핑
  entity maintenance job          ← 별칭 병합 배치

[Phase 3]
  레코드 피벗 섹션(entity_pivot / period_compare / top_records)
  프로필 보강 제안 루프, 병합 승인 UI, L2 편집 화면
```

---

# Phase 1 — Digest 구조화 + 그룹 프로필 부트스트랩 v1

## 1.1 그룹 프로필

새 설정 카테고리 `profile` (app.settings, 기존 settings_manager 경로 재사용):

| key | type | 내용 |
|---|---|---|
| `persona` | string | 분석·digest 프롬프트 앞부분. 예: "지식·교양 콘텐츠를 종합하는 큐레이터다. 독자는 …" |
| `digest_sections` | json | 기본 섹션 배열(아래 1.3 형식). 신규 digest config의 기본값 |
| `bootstrap_status` | string | `none` \| `done` \| `failed` |
| `bootstrap_at` | string | ISO 시각 |

`settings_types.py`에 `GroupProfile` dataclass, `settings_manager.get_profile()` 추가.
Phase 2에서 `record_schema`, `vocab` key가 이 카테고리에 추가된다.

## 1.2 bootstrap_service

`app/services/bootstrap_service.py` 신설.

```python
async def bootstrap_profile(group: Group, *, force: bool = False) -> GroupProfile:
    """그룹 이름·slug·(있으면) 채널명 목록으로 프로필 생성. LLM 1회 호출."""
```

- **호출 지점 1**: `create_group` — `seed_default_settings()` 직후 백그라운드 태스크로
  실행(`asyncio.create_task`). 생성 응답을 지연시키지 않는다. 실패 시
  `bootstrap_status='failed'`로 기록만 하고, 이미 시드된 중립 기본값으로 동작.
- **호출 지점 2**: `POST /api/groups/{slug}/profile/regenerate` (owner/관리자).
  채널 등록 후 재생성 용도. 재생성은 profile만 갱신하고 기존 digest config의
  sections는 건드리지 않는다(사용자가 조정했을 수 있음).
- **LLM 입력**: 그룹 name, slug, 등록 채널명 최대 20개. **출력**: `{persona,
  digest_sections}` JSON. 모델은 `resolve_ai_gateway(group_id)`의
  `digest_model or primary_model`(전역 폴백 포함 — 생성 직후 그룹 키 미설정이어도 동작).
- **원장**: `record_usage(purpose='bootstrap', user_id=owner)`. `budget_ok_for_group`
  게이트 통과 필요(초과 시 skip — 기본값 동작).
- **검증**: 섹션 수 2~8, key 영문 스네이크, guide 300자 이내로 정규화. 불량 응답은
  `failed` 처리.

## 1.3 섹션 모델과 레지스트리

digest 섹션의 정규 형식 (config와 산출물이 공유):

```jsonc
// DigestScheduleConfig.sections (설정)
[
  {"key": "overview",      "kind": "llm",      "title": "핵심 요약",
   "guide": "이번 기간을 가로지르는 3~5개 핵심 흐름을 서술"},
  {"key": "insights",      "kind": "llm",      "title": "핵심 인사이트", "guide": "..."},
  {"key": "top_viewed",    "kind": "computed", "title": "조회수 상위"},
  {"key": "top_tags",      "kind": "computed", "title": "주요 태그"}
]
```

- `kind: "llm"` — LLM이 `body_md`(markdown, `###` 서브헤딩·문단·불릿 허용)를 생성.
  `guide`는 시스템(부트스트랩/기본값)이 작성하며 L2에서만 편집.
- `kind: "computed"` — LLM 불필요. 기존 집계에서 렌더.
  레지스트리(코드 상수): `stats_overview`(기간·영상 수), `sentiment_breakdown`,
  `top_tags`, `top_channels`, `top_viewed`. Phase 3에서 피벗류 추가.
- 순서 = 배열 순서. 추가/제외/▲▼는 최상위 섹션 단위(중첩 편집은 비목표).
- 상한: 섹션 12개.

**카테고리 중립 기본 세트**(부트스트랩 실패·프로필 부재 시 폴백,
`DEFAULT_DIGEST_SECTIONS` 상수):

```
핵심 요약(llm) · 관점 비교(llm) · 핵심 인사이트(llm) · 조회수 상위(computed) · 주요 태그(computed)
```

기존 `DEFAULT_DIGEST_PROMPT`의 invest 페르소나·"주목할 종목·이슈" 섹션은 이 중립
세트로 대체된다(기본 프롬프트 상수는 custom 모드 폴백용으로만 유지).

## 1.4 DigestScheduleConfig 확장

`settings_types.DigestScheduleConfig`에 추가:

```python
sections: list[dict] = field(default_factory=list)  # 비어 있으면 프로필→중립 기본 순 폴백
```

- `digest_config.py`의 normalize/`configs_to_json`에 `sections` 왕복 추가.
  검증: kind ∈ {llm, computed}, computed key는 레지스트리 존재 확인, 상한 12.
- **모드 판정(마이그레이션 불필요)**: `cfg.digest_prompt`가 비어 있지 않으면
  **custom 모드**(현행 전체 프롬프트 경로 그대로 — telco/brand 무변경).
  비어 있으면 **structured 모드**(2층 조립). 명시 플래그를 두지 않고 기존 데이터
  형태로 추론한다.
- 신규 그룹 시드(`DEFAULT_GROUP_SETTINGS['digest']`): configs를 `[]` 대신
  **주간 config 1건(enabled=false, sections=[] → 프로필 폴백)** 으로 시드.
  사용자는 토글 한 번으로 digest를 켠다.

## 1.5 프롬프트 2층 조립 (structured 모드)

`digest_service.synthesize_with_llm` 분기:

```
[1층 페르소나] profile.persona (없으면 중립 문구)
[공용 데이터 블록] 기간·집계·{videos_block} 등 — 기존 placeholder 렌더 함수 재사용
[2층 구조] 선택된 llm 섹션들로 출력 JSON 스키마 동적 생성:
  {"headline": "...",
   "sections": [{"key":"overview","body_md":"..."}, ...],  // 요청한 llm 섹션 key 순서
   "telegram_summary": "..."}
```

- 응답 파싱: 요청한 key만 채택, 누락 섹션은 건너뜀(에러 아님), 여분 key 무시.
- LLM 실패 → 기존 `_fallback_generated` (status='fallback', summary_md 경로) 유지.
- custom 모드는 현행 코드 경로 그대로: `summary_md` 산출, `digest_sections` NULL.

## 1.6 저장: digests.digest_sections

- additive 컬럼: `("digests", "digest_sections", "jsonb")` — `db_engine.ensure_schema`
  의 `additive_columns` 리스트에 추가(전 그룹 자가치유 패턴).
- 저장 형식 = **생성 시점에 확정된 최종 순서의 전체 섹션 배열**(불변 기록):

```jsonc
[
  {"key":"overview","kind":"llm","title":"핵심 요약","body_md":"..."},
  {"key":"top_viewed","kind":"computed","title":"조회수 상위",
   "data":{"items":[{"channel":"AT&T","head":"...","views":12648000}]}}
]
```

- computed 섹션은 구조화 `data`로 저장(웹은 네이티브 렌더, 텔레그램은 텍스트 렌더).
- `summary_md`는 structured 모드에서도 **llm 섹션들을 이어붙인 마크다운으로 함께
  저장**(share OG description, 검색, 하위 호환용). 기존 digest 행은 digest_sections
  NULL → 렌더러가 summary_md 폴백.

## 1.7 뷰모델과 렌더러

`app/services/digest_view.py` 신설 — `analysis_view.py`와 동일 패턴:

```python
@dataclass(frozen=True)
class DigestSection:
    key: str; kind: str; title: str
    body_md: Optional[str] = None      # llm
    data: Optional[dict] = None        # computed

def build_digest_sections(digest) -> list[DigestSection]:
    """digest_sections 우선, 없으면 summary_md 단일 레거시 섹션 + 기존 통계 컬럼 폴백."""
```

소비자 3곳 모두 이 뷰모델만 사용:
- **웹** `DigestDetail.tsx`: sections 배열 순회 렌더(마크다운/데이터). 폴백 시 현행 UI.
- **SSR** `share_page.render_digest_share_html`: 동일.
- **텔레그램**: `telegram_summary`(현행 유지) — 섹션 전체를 보내지 않는다.
  단 computed 섹션이 앞순서면 통계 한 줄 요약을 헤더 아래 덧붙일 수 있게
  훅만 남긴다(비목표에 가까움, 최소 구현).

## 1.8 API / 프론트엔드

- 설정: 기존 digest configs GET/PUT 경로에 `sections` 필드 왕복 추가.
- `GET /api/groups/{slug}/profile` · `POST .../profile/regenerate`(owner/관리자).
- digests 조회 응답에 `digest_sections` 포함.
- **DigestConfigsEditor**: 섹션 빌더 추가 — `TemplateBuilder`를
  `OrderedItemsBuilder`(항목 렌더 prop 일반화)로 추출해 알림·digest가 공유.
  digest_prompt textarea는 "고급(전체 프롬프트 직접 작성)" 접힘 영역으로 이동,
  값이 있으면 "이 설정은 커스텀 프롬프트 모드로 동작합니다" 배지 표시.
- **프로필 확인 카드**: 그룹 생성 직후 대시보드에 "이 그룹은 이렇게 요약합니다"
  카드(섹션 목록 표시 + [다이제스트 켜기] [항목 조정] 버튼).
- **넛지**: Digests 페이지에서 분석 ≥ 10건 & 활성 config 0건이면 활성화 배너.

## 1.9 Phase 1 테스트

- digest_config: sections 왕복·검증·상한, digest_prompt 유무에 따른 모드 추론.
- 프롬프트 조립: 섹션 세트 → 출력 스키마 문자열 스냅샷, 응답 파싱(누락/여분/불량 JSON).
- digest_view: digest_sections 有/無(레거시 폴백), computed data 렌더.
- bootstrap_service: 정상 응답 정규화, LLM 실패 → failed + 기본값 동작, 원장 기록.
- 회귀: custom 모드(digest_prompt 존재) 경로가 바이트 단위로 현행과 동일한지
  (telco/brand 시나리오 픽스처).
- ensure_schema: digest_sections 컬럼 additive 패치.

---

# Phase 2 — analysis_records + 통제 어휘 + 엔티티 사전(자동)

## 2.1 프로필 확장

`profile` 카테고리에 추가:

| key | type | 내용 |
|---|---|---|
| `record_schema` | json | 아래 형식. 부트스트랩이 생성, L2에서 편집 |
| `vocab` | json | 통제 어휘. `{"sentiment": {"label":"평가", "values":["긍정","부정","혼조"], "synonyms":{"positive":"긍정","bullish":"긍정"}}}` |

record_schema 형식:

```jsonc
{
  "version": 1,
  "types": [
    {"type_key": "campaign", "label": "캠페인",
     "fields": [
       {"key": "entity",   "label": "브랜드/사업자", "datatype": "entity", "required": true},
       {"key": "message",  "label": "핵심 메시지",   "datatype": "text"},
       {"key": "tone",     "label": "톤앤매너",     "datatype": "text"},
       {"key": "budget",   "label": "예상 규모",     "datatype": "number"},
       {"key": "aired_on", "label": "집행 시점",     "datatype": "date"}
     ]}
  ]
}
```

datatype은 `entity | text | number | date` 4종만. `entity`는 승격 컬럼
`entity_name`으로, number/date 첫 필드는 `value_num`/`event_date`로 승격,
나머지는 `attrs`에 저장. 부트스트랩 v2가 그룹 생성/재생성 시 record_schema·vocab도
함께 생성한다(Phase 1 부트스트랩 출력에 두 key 추가).

## 2.2 analysis_records 테이블

`app/models/pg/analysis_record.py` 신설(전 그룹 동일 DDL — `ensure_schema`의
create_missing이 자동 생성):

```sql
CREATE TABLE {schema}.analysis_records (
  record_pk      bigserial PRIMARY KEY,
  video_pk       bigint NOT NULL REFERENCES {schema}.videos(video_pk) ON DELETE CASCADE,
  record_type    text   NOT NULL,
  schema_version int    NOT NULL DEFAULT 1,
  position       int    NOT NULL DEFAULT 0,
  entity_name    text,            -- 정규화(사전 canonical) 후 저장
  value_text     text,            -- 핵심 서술 1줄
  value_num      numeric,
  event_date     date,
  attrs          jsonb  NOT NULL DEFAULT '{}',
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_analysis_records_type_entity ON ...(record_type, entity_name);
CREATE INDEX ix_analysis_records_video ON ...(video_pk);
```

`(video_pk, record_type, position)` UNIQUE — 재분석 시 해당 video의 records를
delete-insert(멱등).

## 2.3 records_extractor — 2차 경량 LLM 패스

**분석 본 호출에 record 지시를 주입하지 않는다.** 이유: 분석 프롬프트가 그룹별로
달라지면 공유 분석 캐시(app.analysis_cache, 프리셋 키 참여)가 깨진다. 대신:

- `app/services/records_extractor.py`: 입력 = 저장된 분석 산출물 텍스트
  (one_line, analysis_sections, entities, insights — 영상 재시청 없음) +
  그룹 record_schema + 사전 canonical 상위 N개(표기 통일 힌트) + vocab.
  출력 = `{"records":[{type, fields...}]}`.
- 모델: `tagging_model`(경량) 사용. 원장 `purpose='records'`.
- 실행 지점: `save_analysis_to_group` 완료 후 비동기 후처리(분석 성공을 지연·실패
  시키지 않음). **캐시 적중 경로에서도 동일하게 실행** — 공유 캐시 문제 원천 해소.
- 파싱은 관대: type 미정의·필수 누락 행만 drop, 여분 필드는 attrs에 수용.
  record_schema 없는 그룹은 전체 skip(무비용).
- 선택적 backfill: 관리자 액션으로 기존 구조화 분석 행(telco 294건 등)에 소급
  실행 가능(스케줄 잡 아님, 수동 트리거).

## 2.4 통제 어휘 매핑

- 분석 저장 시 `sentiment`(및 record 필드 중 vocab 대상)를
  `vocab.synonyms` → canonical 값으로 매핑. 대소문자·공백 정규화 포함.
- 미매핑 값은 원문 그대로 저장하되 `{schema}.job_logs`가 아닌 profile 내
  `vocab_pending` 목록(최근 50개)에 적재 — Phase 3 보강 제안의 입력.
- digest `sentiment_breakdown` 집계는 매핑 후 값 기준(긍정/Positive 분열 해소).

## 2.5 엔티티 사전 — 자동 축적

`app/models/pg/entity.py` 신설:

```sql
CREATE TABLE {schema}.entities (
  entity_pk      bigserial PRIMARY KEY,
  canonical_name text NOT NULL UNIQUE,
  aliases        jsonb NOT NULL DEFAULT '[]',
  attrs          jsonb NOT NULL DEFAULT '{}',   -- region, tier 등 (Phase 3 피벗 축)
  status         text  NOT NULL DEFAULT 'auto', -- auto | confirmed
  mention_count  int   NOT NULL DEFAULT 0,
  first_seen     timestamptz NOT NULL DEFAULT now(),
  last_seen      timestamptz NOT NULL DEFAULT now()
);
```

- record 저장 시: entity 필드 값을 canonical/alias(대소문자 무시)로 조회 →
  적중 시 canonical로 치환 저장 + mention_count/last_seen 갱신, 미적중 시
  status='auto'로 신규 등록. **사용자 등록 대기 없음.**
- **병합 배치**(스케줄러 일일 틱, 그룹당): 신규 auto 엔티티가 있을 때만
  기존 상위 엔티티 목록과 함께 경량 LLM 1회 호출로 동일 대상 클러스터 판정.
  `confidence=high`만 자동 병합(alias 흡수 + `analysis_records.entity_name`
  UPDATE + job_logs 기록), 그 외는 병합 후보로 attrs에 보류(Phase 3 승인 UI 입력).
  병합 이력은 job_logs(message에 from→to)로 추적, 되돌리기는 수동(비목표).

## 2.6 Phase 2 테스트

- records_extractor: 스키마 주입 프롬프트 조립, 관대 파싱(불량 type/필수 누락/여분
  필드), 승격 컬럼 매핑, delete-insert 멱등.
- vocab mapper: synonym 매핑, 미매핑 pending 적재, breakdown 집계 정규화.
- entities: upsert·alias 적중·mention_count, 병합 배치(자동/보류 분기, records
  UPDATE 동반), 캐시 적중 경로에서 extractor 실행.
- ensure_schema: 신규 테이블 2종 생성(신규·기존 스키마 모두).
- 회귀: record_schema 없는 그룹은 분석 경로 완전 무변경.

---

# Phase 3 — 레코드 피벗 섹션 · 보강 루프 · 승인/편집 UI

## 3.1 computed 섹션 확장(레코드 기반)

섹션 레지스트리에 추가 — 모두 SQL 집계가 데이터를 만들고, 서술이 필요한 것만
digest 본 호출에 압축 JSON으로 동봉한다(영상 40건 제한과 무관하게 **전수** 집계):

| key | params | 산출 |
|---|---|---|
| `entity_pivot` | record_type, group_by(attrs 축 예: region), top_k | 엔티티별 레코드 요약 블록(telco '경쟁사 집중 분석' 재현) |
| `period_compare` | record_type | 직전 기간 대비 신규/소멸/변화 엔티티·주장('지난주 대비 변화' 근거화) |
| `top_records` | record_type, order_by(value_num 등) | 수치 상위 레코드 표 |

- llm 서술이 붙는 피벗 섹션은 kind를 `hybrid`로 구분: data(SQL) + body_md(LLM 서술).
- digest 프롬프트에는 피벗 결과 JSON이 `{records_block}` placeholder로 추가된다
  (custom 모드 사용자도 placeholder로 활용 가능).

## 3.2 프로필 보강 제안 루프

- 조건: 분석 누적 10건 도달, 이후 월 1회. 최근 분석 표본 + vocab_pending +
  병합 보류 후보를 입력으로 부트스트랩 LLM 재호출 → 제안 diff
  (섹션 추가 제안, record 필드 추가, vocab 확장, 엔티티 attrs 채움).
- 제안은 자동 적용하지 않고 카드로 노출: "○○ 항목을 추적 목록에 추가할까요?
  [적용] [무시]". 적용 시 profile 갱신 + record_schema version 증가.

## 3.3 승인 · 편집 UI

- **병합 승인 큐**: 보류된 엔티티 병합 후보 목록, 원클릭 승인/거절.
- **L2 편집**: record_schema 필드 빌더(OrderedItemsBuilder 재사용, datatype 4종),
  섹션 guide·persona 텍스트 편집(owner/관리자), vocab 값 편집.
- 모든 편집 화면은 설정의 별도 "데이터 프로필" 탭 — 기본 설정 화면을 오염시키지 않음.

## 3.4 Phase 3 테스트

- 피벗 SQL 집계(전수 포함 — 40건 초과 시나리오), hybrid 섹션 조립·렌더.
- period_compare: 이전 기간 부재/스키마 버전 상이 처리.
- 보강 제안: diff 생성·적용·버전 증가, 무시 시 무변경.
- 승인 큐: 승인 → 병합 실행 경로가 배치 병합과 동일 코드 사용.

---

## 비목표

- 레거시 `full_analysis_md`-only 행의 마이그레이션(기존 방침 유지 — 폴백이 처리).
- 그룹별 동적 테이블 DDL, 사용자 정의 datatype.
- 텔레그램 digest 메시지의 섹션 전체 전송(현행 telegram_summary 유지).
- 중첩 섹션 편집 UI(서브섹션은 body_md 내부 마크다운으로만 표현).
- 병합 자동 되돌리기(로그 기반 수동 대응).
- E-2(PG 결제·약관)와의 결합.

## 리스크와 완화

| 리스크 | 완화 |
|---|---|
| 부트스트랩 품질 편차 | 확인 카드로 가시화 + regenerate + 중립 기본값 폴백. 자동 적용 범위는 신규 그룹의 초기값뿐 |
| 기존 파워유저 경로 파손 | digest_prompt 존재 = custom 모드 자동 추론, 해당 경로 코드 무변경 + 회귀 픽스처 |
| record_schema 진화로 과거 레코드 불일치 | schema_version 컬럼, attrs 관대 수용, 집계는 승격 컬럼 위주 |
| 자동 병합 오판 | high confidence만 자동, 나머지 보류→승인 큐, job_logs 이력 |
| LLM 비용 증가 | bootstrap 그룹당 1~2회, records는 경량 모델 텍스트 입력(영상 재분석 없음), 병합 배치는 신규 엔티티 있을 때만. 전부 ai_usage 원장 기록 + 월 예산 게이트 적용 |
| 공유 분석 캐시 무효화 | 분석 본 프롬프트 무변경 원칙(records는 2차 패스) |
| digest_sections/summary_md 이중 저장 불일치 | summary_md는 llm 섹션 연결 파생물로만 생성(단일 생성 지점) |

## 배포 순서

1. Phase 1: DDL 자가치유(additive 컬럼)는 배포 시 schema_migrator가 전 그룹 적용.
   기존 config 동작 무변경 확인 후 신규 그룹 시드 변경 활성.
2. Phase 2: 테이블 2종 추가 → records_extractor를 record_schema 보유 그룹부터
   점진 적용(신규 그룹 → 기존 그룹 regenerate 시).
3. Phase 3: 피벗 섹션은 레코드 축적량이 있는 그룹부터 노출.

각 Phase는 독립 배포 가능하며, 이전 Phase 산출물만 전제한다.
