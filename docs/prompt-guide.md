# 프롬프트 설계 가이드

그룹 프롬프트를 작성하거나 수정할 때 "출력값이 DB에 어떻게 저장되고 어떻게 활용되는지"를 이해하고 설계하기 위한 실무 가이드.

그룹마다 영상 성격은 달라도 **JSON shape는 일정**해야 다운스트림 AI agent가 일관되게 가공할 수 있다.

---

## 1. 출력 계약 (JSON 키 ↔ DB 컬럼)

프롬프트가 출력하는 JSON 키는 아래 표의 컬럼에만 저장된다. **표에 없는 최상위 키는 코드가 무시하고 버린다.** 새로운 데이터를 영구 저장하려면 DB 컬럼과 코드 추가가 별도로 필요하다.

| JSON 키 | DB 컬럼 | 필수 | 주요 활용처 |
|---------|---------|:---:|------------|
| `one_line` | `video_analysis.one_line` | **필수** | Telegram compact 본문, OG `description` |
| `headline` | `video_analysis.headline` | - | Telegram 제목, OG `og:title`, 카드 헤더 |
| `short_summary_md` | `video_analysis.short_summary_md` | **필수** | Telegram 요약 영역 (400자 권장) |
| `bullet_points` | `video_analysis.bullet_points` | - | Telegram compact 상위 3개 노출 |
| `analysis_sections` | `video_analysis.analysis_sections` | - | 웹/SSR 매거진 본문 |
| `full_analysis_md` | `video_analysis.full_analysis_md` | - | **레거시·폴백 전용** — 신규 프롬프트에서는 사용하지 말 것 |
| `key_points` | `video_analysis.key_points` | - | 영상 내 타임스탬프 점프 링크 |
| `insights` | `video_analysis.insights` | - | 인사이트 배열 |
| `entities` | `video_analysis.entities` | - | 등장 인물·기업·지표 (구조화, 확장 가능) |
| `sentiment` / `brand_tone` | `video_analysis.sentiment` | - | 감성 레이블 (자유 문자열) |
| `confidence_score` | `video_analysis.confidence_score` | - | 분석 신뢰도 (0.0~1.0) |
| `tags` | `tags` 테이블 (`VideoTag` 연결) | - | 태그 페이지·필터 |

> `full_analysis_md`는 과거 데이터(589행) 폴백 표시를 위해 컬럼이 존재하지만, 신규 프롬프트에는 출력하지 않아도 된다. 렌더러가 `analysis_sections` 우선, 없으면 `full_analysis_md` 폴백으로 동작한다.

---

## 2. 필수 vs 선택 필드

### 필수 (없으면 분석 실패 처리)

| 필드 | 요건 |
|------|------|
| `one_line` | 한 문장 요약. 비어 있으면 안 됨 |
| `short_summary_md` | 요약 본문. 비어 있으면 안 됨 |

### 선택 (그룹 성격에 맞지 않으면 생략 가능)

나머지 필드는 모두 선택이다. 선택 필드가 빠져도 분석은 성공 처리된다.

- 타임스탬프가 무의미한 그룹(예: 뉴스 편집본)은 `key_points` 생략.
- 브랜드 분석 그룹은 `sentiment` 대신 `brand_tone`을 같은 DB 컬럼에 담아도 됨.
- 선택 필드라도 **한 번 출력하기로 결정했으면 일관되게** 출력해야 집계·검색이 정상 동작한다.

---

## 3. 본문: `analysis_sections` 작성법

### 구조

```json
"analysis_sections": [
  {
    "key": "overview",
    "title": "캠페인 개요",
    "bullets": [
      "싱가포르 통신사 Singtel의 OTT 'CAST.SG' 가입자 유치용 프로모션 영상임",
      "특정 드라마 방영 일정 고지 + 구독 혜택으로 플랫폼 활성화 목적"
    ]
  },
  {
    "key": "usp_target",
    "title": "핵심 소구점(USP) 및 타겟",
    "bullets": ["..."]
  }
]
```

### 각 필드 규칙

| 필드 | 타입 | 규칙 |
|------|------|------|
| `key` | 영문 스네이크케이스 | 그룹 내에서 일관되게 사용. 예: `overview`, `main_points`, `risk`, `conclusion` |
| `title` | 한국어 문자열 | 화면에 그대로 표시되는 섹션 제목 |
| `bullets` | 문자열 배열 | **항목 하나 = 순수 문장 하나**. 아래 규칙 참조 |

### `bullets` 작성 규칙

**허용:**
- 한국어 개조식 문장 (`~함`, `~임`)
- 인라인 강조: `**굵게**` 또는 `` `코드` ``

**금지:**
- 불릿 기호 직접 삽입 (`•`, `-`, `*`)
- 번호 매기기 (`1.`, `2.`)
- 줄바꿈 문자 (`\n`)

> 불릿 기호와 줄바꿈은 **렌더러가 100% 통제**한다. LLM이 직접 넣으면 채널마다 깨진다.
> 웹은 `<li>`, Telegram은 `• bullet\n`, SSR은 `<li>`로 각자 렌더링한다.

### 좋은 예 / 나쁜 예

**나쁜 예 — bullets에 기호·줄바꿈 포함:**
```json
"bullets": ["• 첫째 주장임.\n• 둘째 주장임."]
```
- 문제: 단일 문자열 안에 불릿 기호(`•`)와 줄바꿈(`\n`)이 박혀 있음
- Telegram에서 `• 첫째 주장임.• 둘째 주장임.` 로 합쳐지거나 이중 불릿 발생
- 웹 `react-markdown`에서 단일 `\n`이 공백으로 처리되어 한 줄로 합쳐짐

**좋은 예 — 순수 문장 배열:**
```json
"bullets": ["첫째 주장임", "**핵심**: 둘째 주장임"]
```
- 각 항목이 독립된 문장
- 인라인 `**bold**` 강조만 사용
- 렌더러가 적절한 불릿·줄바꿈을 채널에 맞게 추가

### 섹션 키 예시 (그룹별)

| 그룹 | 권장 key 목록 |
|------|-------------|
| 경제·주식 | `overview`, `main_thesis`, `evidence`, `risk`, `conclusion` |
| 마케팅·광고 | `overview`, `usp_target`, `creative_analysis`, `implications` |
| 기술·제품 | `overview`, `features`, `comparison`, `verdict` |
| 시사·뉴스 | `summary`, `background`, `implications`, `key_figures` |

그룹 내에서 `key` 값을 일관되게 유지하면 "마케팅 그룹의 `usp_target` 섹션만 모아보기" 같은 구조화 쿼리가 가능해진다.

---

## 4. 머신리더블 필드 활용

이 필드들이 "DB 축적 데이터 기반 AI agent 가공"의 핵심 재료다. 자유 문장보다 구조화 필드에 정보를 담을수록 활용도가 높아진다.

### `entities` — 등장 인물·기업·지표

기본 shape:
```json
{"name": "삼성전자", "type": "company"}
```

경제 그룹 확장 예시 (market, ticker 추가):
```json
{"name": "삼성전자", "type": "company", "market": "KRX", "ticker": "005930"}
```

- `type` 허용값: `person`, `company`, `ticker`, `metric`
- 경제 그룹이라면 프롬프트에 `market`(거래소)과 `ticker`(종목코드) 키 출력을 명시해야 DB에 저장됨
- 종목·기업 단위 집계·검색이 가능해져 "삼성전자 언급 영상 모아보기" 기능 구현 가능

### `tags` — 분류 태그

```json
{"name": "5G요금제", "type": "topic", "weight": 0.9}
```

- `type` 허용값: `topic`, `ticker`, `person`, `sector`
- **그룹 공통 분류 체계를 프롬프트에 명시**해 표기를 정규화할 것
  - 나쁜 예: `"5g 요금제"`, `"5G 요금제"`, `"5G요금제"` 혼재
  - 좋은 예: 프롬프트에 `"5G요금제"` 고정 형태 명시 → 태그 페이지·필터 품질 보장
- `weight`: 0.0~1.0, 해당 영상에서 이 태그의 중요도

### `key_points` — 타임스탬프 점프

```json
{"timestamp": "00:03:45", "point": "금리 인상 근거 제시"}
```

- 영상 내 중요 구간으로 바로 이동하는 링크로 활용됨
- 타임스탬프가 무의미한 그룹(편집 뉴스, 쇼츠 등)은 생략 권장

---

## 5. 고정값 필드 규칙

### `sentiment` / `brand_tone`

- **자유 문자열**. DB enum 강제 없음.
- 그룹별로 의미를 자유롭게 정의 가능:
  - 경제 그룹: `bullish`, `bearish`, `neutral`, `mixed`
  - 마케팅 그룹: `trendy`, `informative`, `emotional`, `promotional`
- **집계 일관성을 위해 그룹 내 후보 값을 프롬프트에 명시할 것**:
  ```
  sentiment는 반드시 "bullish"/"bearish"/"neutral"/"mixed" 중 하나로 출력.
  ```
  이렇게 하지 않으면 같은 의미를 "강세" / "bullish" / "상승" 등 다양한 형태로 출력해 집계가 어려워진다.

### `confidence_score`

- **0.0 이상 1.0 이하**의 부동소수점 숫자.
- `1.0` 출력 금지 (완벽한 확신은 없다는 원칙).
- 영상 내용이 불분명하거나 발화자 주장에 근거가 부족할수록 낮게.
- 범위를 벗어난 값(음수, 1.0 초과)은 코드가 `null`로 처리한다.

---

## 6. 채널별 출력 매핑

분석 결과 하나가 어떤 채널에서 어떻게 표현되는지 파악하면 각 필드의 중요도를 가늠할 수 있다.

| 필드 | Telegram compact | Telegram full | 웹 매거진 | SSR 공유 페이지 |
|------|:---:|:---:|:---:|:---:|
| `headline` | 제목 | 제목 | 카드 헤더 | OG `og:title`, 페이지 제목 |
| `one_line` | 본문 첫 줄 | - | 카드 요약 | OG `description` |
| `short_summary_md` | 요약 영역 | 요약 영역 | - | - |
| `bullet_points` | 상위 3개 노출 | - | 하이라이트 | - |
| `analysis_sections` | (링크로 유도) | 섹션별 렌더 | 매거진 본문 | 매거진 본문 |
| `key_points` | - | - | 영상 점프 링크 | 영상 점프 링크 |
| `entities` / `tags` | - | - | 메타 영역 | 메타 영역 |
| 공유 URL | 본문 끝 첨부 | 본문 끝 첨부 | - | (페이지 자체가 공유 URL) |

**Telegram compact**는 `headline` + `one_line` + `bullet_points` 상위 3개 + 공유 링크로 구성된다. `analysis_sections`는 compact에 직접 표시되지 않고 "자세히 보기" 링크로 유도된다.

**웹 매거진**은 `analysis_sections`를 각 섹션 `<h3>` + `<ul><li>` 로 렌더링한다. `bullets`의 인라인 마크다운(`**bold**`)은 react-markdown 인라인 렌더로 처리된다.

**SSR 공유 페이지**(`/v/{group_slug}/{share_token}`)는 Jinja2로 서버 렌더링한 HTML이며, OG 메타가 삽입되어 Telegram·카카오·슬랙 리치 카드 미리보기를 지원한다.

---

## 7. 품질 체크리스트

프롬프트를 작성·수정할 때 아래 항목을 확인한다.

### 기본 구조

- [ ] `one_line`과 `short_summary_md`가 반드시 출력되도록 명시했는가
- [ ] 필요한 선택 필드를 명시했는가 (그룹 성격에 맞게)
- [ ] JSON 형식 예시를 프롬프트에 포함시켰는가

### `analysis_sections`

- [ ] `{key, title, bullets[]}` shape를 정확히 명시했는가
- [ ] `key`가 영문 스네이크케이스이며 그룹 내 일관된 목록으로 정의됐는가
- [ ] `bullets` 항목에 `•`, `-`, 번호, `\n`을 넣지 말라고 명시했는가
- [ ] 인라인 `**bold**`만 허용함을 명시했는가

### 텍스트 품질

- [ ] 모든 텍스트 한국어 개조식(`~함`, `~임`) 출력을 지시했는가
- [ ] 행위 서술 금지 표현을 명시했는가 (`~을 제시했다`, `~을 설명했다` 등)
- [ ] 영상에서 확인 안 된 사실은 생략/null, `confidence_score` 하향 지시를 포함했는가

### 머신리더블 필드

- [ ] `entities` 표기가 정규화되도록 명시했는가 (예: 기업명 통일)
- [ ] `tags` 후보 목록을 명시해 표기를 정규화했는가
- [ ] `sentiment` 후보 값을 명시해 집계 일관성을 확보했는가
- [ ] `confidence_score` 범위(0.0~1.0, 절대 1.0 금지)를 명시했는가

### 최종 점검

- [ ] 프롬프트의 JSON 예시에 표에 없는 최상위 키가 포함되지 않았는가
- [ ] 그룹 내 다른 분석 결과와 `analysis_sections.key` 목록이 일치하는가
