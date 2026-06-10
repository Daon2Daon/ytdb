# 주간 리뷰(Digest) 공개 공유 페이지 + "웹에서 자세히 보기" 링크

작성일: 2026-06-10

## 배경 / 문제

영상 알림에는 "웹에서 자세히 보기" 기능이 이미 있다. 영상 분석이 끝나면
`Video.share_token`(추측 불가 토큰)이 발급되고, 무인증 공개 라우트
`GET /v/{slug}/{token}`가 OG 메타를 포함한 매거진 HTML을 서버사이드로 렌더한다.
텔레그램 메시지 템플릿의 `share_link` 필드가 이 페이지로 가는 링크를 만들며,
사용자는 `template_builder` UI에서 이 필드를 켜고 끌 수 있다.

반면 **주간 리뷰(digest)** 에는 공개 공유 페이지도, 공유 링크도 없다.

- `digest_service.generate_digest_for_group`는 집계·LLM 합성 결과를
  `digests` 테이블에 저장하지만 `share_token`이 없다.
- `_send_digest_telegram`은 `<b>{headline}</b>\n\n{telegram_summary}` 고정
  포맷만 전송한다.
- `DigestDetail.tsx`는 **인증된 SPA 내부 페이지**(`/g/{slug}/digests/{pk}`)
  뿐이라, 텔레그램 수신자(비로그인)는 열 수 없다.

## 목표

영상 패턴을 그대로 따라, 주간 리뷰에도:

1. 디지스트별 공개(무인증·unlisted 토큰) 공유 페이지를 만든다.
2. 디지스트 텔레그램 메시지에 "📖 웹에서 자세히 보기" 링크를 첨부하되,
   **digest 설정의 토글**로 켜고 끌 수 있게 한다.

## 비목표 (이번 범위에서 제외)

- 주간 리뷰 메시지 템플릿 빌더(영상의 `template_builder` 같은 필드 토글 UI).
  이번엔 기존 고정 포맷에 링크 한 줄만 추가한다.
- 공개 페이지의 집계 시각화(감성 분포·상위 태그·상위 채널 차트).
  요약 본문(`summary_md`)만 보여준다.
- digest 전용 정숙시간/예약발송 옵션.

## 결정 사항 (브레인스토밍에서 확정)

| 항목 | 결정 |
|---|---|
| 링크 도착지 | **공개 공유 페이지 신설**(영상 `/v/`와 동일 패턴). 인증 SPA 연결 아님 |
| 텔레그램 링크 제어 | **digest 설정에 boolean 토글** 추가 (`share_link_enabled`) |
| 공개 페이지 내용 | **요약 본문만**(headline + summary_md + 기간·영상 수 + OG 메타) |
| 마크다운 렌더 | 서버에 마크다운 라이브러리 없음 → **의존성 없는 최소 변환기**(헤딩/불릿/볼드/문단) 추가 |

## 설계

### 1. 데이터 모델 — `Digest`에 공유 토큰

`app/models/pg/digest.py`에 `Video`와 동일한 두 컬럼 추가:

```python
share_token: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
share_visibility: Mapped[str | None] = mapped_column(Text, nullable=True)
```

### 2. 기존 스키마 자가치유 (멱등 마이그레이션)

`app/services/db_engine.py`의 `additive_columns`에 추가:

```python
("digests", "share_token", "text"),
("digests", "share_visibility", "text"),
```

그리고 `digests.share_token`에 부분 유니크 인덱스를 추가 (videos와 동일 패턴):

```sql
CREATE UNIQUE INDEX IF NOT EXISTS "ux_{schema}_digests_share_token"
ON "{schema}"."digests" (share_token) WHERE share_token IS NOT NULL
```

`create_all`은 기존 테이블에 컬럼을 추가하지 않으므로 이 ALTER가 필요하다.

### 3. 토큰 발급 — digest 생성 시

`digest_service.generate_digest_for_group`에서 `Digest(...)`를 만들 때
`share_token`/`share_visibility`를 채운다. 기존 헬퍼 재사용:

```python
from app.services.share_token import generate_share_token, DEFAULT_VISIBILITY
# ...
digest = Digest(
    ...,
    share_token=generate_share_token(),
    share_visibility=DEFAULT_VISIBILITY,
)
```

`save=False`(미리보기)일 때도 토큰을 채워도 무방하나, 저장하지 않으면
조회되지 않으므로 영향 없음. 단순화를 위해 항상 채운다.

기존(이미 저장된) 디지스트는 토큰이 없다. 이번 범위에서 소급 발급은 하지
않는다 — 새로 생성되는 디지스트부터 링크가 동작한다.

### 4. 공개 공유 페이지 (무인증)

**라우트** — `app/routers/share.py`에 추가:

```
GET /d/{slug}/{token}  →  HTMLResponse
```

- 슬러그로 그룹 조회 → 그룹 세션에서 `Digest.share_token == token` 조회
- 없거나 `share_visibility != "unlisted"`면 404
- `render_digest_share_html(...)` 결과를 `HTMLResponse`로 반환

**렌더러** — `app/services/share_page.py`에 `render_digest_share_html()` 추가:

- 입력: `title/headline`, `summary_md`, `period_start/period_end`(KST 문자열),
  `video_count`, `category`, `canonical_url`
- OG 메타: `og:title`=headline, `og:description`=summary_md 앞부분 발췌,
  `og:type`=article, `og:url`=canonical. 썸네일 없음(이미지 OG 생략)
- 본문: `_render_markdown_min(summary_md)` 결과 + 기간·영상 수 헤더
- 기존 `render_share_html`과 CSS/레이아웃을 최대한 공유

**최소 마크다운 변환기** — `share_page.py` 내 순수 함수
`_render_markdown_min(md: str) -> str`:

- 먼저 모든 텍스트를 `html.escape` (XSS 방지)
- 라인 단위 처리:
  - `## ` / `### ` → `<h2>` / `<h3>`
  - `- ` / `• ` → `<li>` (연속 라인을 `<ul>`로 묶음)
  - 빈 줄 → 문단 경계
  - 그 외 → `<p>`
- 인라인 `**bold**` → `<strong>` (이스케이프 후 토큰 치환)
- 처리 못 한 마크다운 문법은 그대로 텍스트로 남김(안전)

### 5. SPA 폴백 가드

`app/main.py`의 `spa_fallback` 제외 목록에 `d/` 추가
(영상 `v/`와 동일 — 2-세그먼트 패턴 미매칭 시 React로 새지 않도록):

```python
or full_path.startswith("v/")
or full_path.startswith("d/")
```

### 6. 텔레그램 메시지에 링크 첨부 (설정 토글)

**설정 타입** — `app/services/settings_types.py`의 `DigestSettings`:

```python
share_link_enabled: bool = True
```

**로더** — `app/services/settings_manager.py`의 `get_digest`:

```python
share_link_enabled=bool(d.get("share_link_enabled", True)),
```

**기본 시드** — `app/services/default_settings.py`의 `"digest"` 배열:

```python
{"key": "share_link_enabled", "value": "true", "value_type": "bool"},
```

**메시지 생성** — `digest_service._send_digest_telegram`:

- 시그니처에 `slug`, `share_token`, `share_link_enabled`(또는 `cfg`)를 전달받도록 조정
- `share_link_enabled and share_token and PUBLIC_BASE_URL`이면 본문 끝에 한 줄 추가:

  ```
  \n\n📖 <a href="{PUBLIC_BASE_URL}/d/{slug}/{token}">웹에서 자세히 보기</a>
  ```

- URL 빌드는 `notify_service._build_share_url`과 동일한 규칙(다만 `/d/` 경로).
  중복을 피하기 위해 공유 URL 빌더를 경로 인자(`/v/` vs `/d/`)를 받도록
  일반화하거나, digest용 소형 헬퍼를 둔다(구현 시 결정).

**호출부** — `digest_service.run_digest_tick_once`에서
`_send_digest_telegram(group.group_id, ..., slug=group.slug,
share_token=digest.share_token, share_link_enabled=cfg.share_link_enabled)`로
인자를 넘긴다.

### 7. 프론트엔드

`frontend/src/settings/defs.ts`의 `digest` 배열에 토글 필드 추가:

```ts
{ key: 'share_link_enabled', label: '웹에서 자세히 보기 링크 첨부', type: 'bool' },
```

(선택) `DigestDetail.tsx`에 공개 공유 링크 복사 버튼은 이번 범위 밖 — 생략.

## 테스트 계획

- **토큰 발급**: `generate_digest_for_group`로 만든 digest에 `share_token`이
  채워지고 `share_visibility == "unlisted"`인지.
- **공개 페이지**: `GET /d/{slug}/{token}` 200 + headline/요약이 본문에 포함.
  잘못된 토큰 → 404. `share_visibility`가 unlisted가 아니면 → 404.
- **마크다운 변환기**: `## 주요 내용` → `<h2>`, `- 항목` → `<li>`,
  `**굵게**` → `<strong>`, `<script>` 입력이 이스케이프되는지(XSS).
- **텔레그램 링크 토글**: `share_link_enabled=True`이고 토큰 있으면 메시지에
  `/d/{slug}/{token}` 링크 포함, `False`면 미포함, 토큰 없으면 미포함.
- **SPA 폴백**: `/d/x`(미매칭) 형태가 React index로 새지 않고 404.

기존 `tests/test_share_page.py`, `tests/test_notify_render.py`,
`tests/test_spa_serving.py` 패턴을 참고한다.

## 참고한 기존 구현

- 영상 공유 토큰 발급: `app/services/analyzer.py:207` (`save_to_db`)
- 영상 공유 라우트: `app/routers/share.py` (`GET /v/{slug}/{token}`)
- 공유 HTML 렌더: `app/services/share_page.py` (`render_share_html`)
- 공유 링크 렌더(텔레그램): `app/services/notify_service.py:249`
  (`_render_share_link`, `_build_share_url`)
- 스키마 자가치유: `app/services/db_engine.py:175` (`additive_columns`)
