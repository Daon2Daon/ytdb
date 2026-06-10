# 주간 리뷰 공개 공유 페이지 + "웹에서 자세히 보기" 링크 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 주간 리뷰(digest)에 영상과 동일한 무인증 공개 공유 페이지를 만들고, 디지스트 텔레그램 메시지에 설정 토글로 켤 수 있는 "웹에서 자세히 보기" 링크를 첨부한다.

**Architecture:** 영상 공유(`Video.share_token` → `GET /v/{slug}/{token}` → `render_share_html`) 패턴을 디지스트에 복제한다. `Digest`에 `share_token`/`share_visibility` 컬럼을 추가하고, 디지스트 생성 시 토큰을 발급한다. 새 무인증 라우트 `GET /d/{slug}/{token}`가 `summary_md`를 의존성 없는 최소 마크다운 변환기로 렌더한다. 텔레그램 링크는 `DigestSettings.share_link_enabled` 토글로 제어한다.

**Tech Stack:** Python 3.10+, FastAPI, SQLAlchemy(async), PostgreSQL(스키마별 데이터 평면), pytest, React/TypeScript(설정 폼).

설계 문서: `docs/superpowers/specs/2026-06-10-digest-share-page-design.md`

---

## 파일 구조

- `app/models/pg/digest.py` — `Digest` 모델에 공유 컬럼 추가
- `app/services/db_engine.py` — 기존 스키마 멱등 ALTER + 유니크 인덱스
- `app/services/share_page.py` — `_render_markdown_min`, `render_digest_share_html` (순수 렌더 함수)
- `app/services/digest_service.py` — 토큰 발급 + `build_digest_telegram_text`(순수) + `_send_digest_telegram` 배선
- `app/routers/share.py` — `GET /d/{slug}/{token}` 무인증 라우트
- `app/main.py` — SPA 폴백 `d/` 가드
- `app/services/settings_types.py` — `DigestSettings.share_link_enabled`
- `app/services/settings_manager.py` — `get_digest` 파싱
- `app/services/default_settings.py` — 기본 시드
- `frontend/src/settings/defs.ts` — 토글 UI 필드
- 테스트: `tests/test_share_page.py`(확장), `tests/test_digest_telegram_link.py`(신규), `tests/test_spa_serving.py`(확장), `tests/test_digest_share_settings.py`(신규)

---

## Task 1: 최소 마크다운 변환기

**Files:**
- Modify: `app/services/share_page.py`
- Test: `tests/test_share_page.py`

순수 함수부터 만든다. 서버에 마크다운 라이브러리가 없으므로, `summary_md`의
`## 헤딩`, `- 불릿`, `**볼드**`, 일반 문단만 처리하는 의존성 없는 변환기를 만든다.
모든 입력은 먼저 이스케이프하여 XSS를 막는다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_share_page.py` 끝에 추가:

```python
from app.services.share_page import _render_markdown_min


def test_markdown_min_headings_bullets_bold():
    md = "## 주요 내용\n- 항목 하나\n- 항목 둘\n\n본문 **굵게** 끝"
    html = _render_markdown_min(md)
    assert "<h2>주요 내용</h2>" in html
    assert "<li>항목 하나</li>" in html
    assert "<li>항목 둘</li>" in html
    assert "<ul>" in html and "</ul>" in html
    assert "<strong>굵게</strong>" in html
    assert "<p>본문 <strong>굵게</strong> 끝</p>" in html


def test_markdown_min_h3_and_paragraph():
    html = _render_markdown_min("### 소제목\n그냥 문장")
    assert "<h3>소제목</h3>" in html
    assert "<p>그냥 문장</p>" in html


def test_markdown_min_escapes_html():
    html = _render_markdown_min("## <script>alert(1)</script>\n- <b>x</b>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_share_page.py -v -k markdown_min`
Expected: FAIL — `ImportError: cannot import name '_render_markdown_min'`

- [ ] **Step 3: 최소 구현**

`app/services/share_page.py`의 `from app.services.analysis_view import Section`
아래에 추가(파일 상단에는 이미 `from html import escape`, `from typing import List, Optional`가 있음):

```python
import re

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _inline(text: str) -> str:
    """이스케이프 후 인라인 **굵게**만 <strong>으로 변환."""
    safe = escape(text)
    return _BOLD_RE.sub(r"<strong>\1</strong>", safe)


def _render_markdown_min(md: str) -> str:
    """의존성 없는 최소 마크다운 → HTML.

    지원: ## / ### 헤딩, - / • 불릿(ul로 묶음), **굵게**, 일반 문단.
    그 외 문법은 일반 텍스트로 남긴다. 모든 입력은 이스케이프한다.
    """
    lines = (md or "").replace("\r\n", "\n").split("\n")
    out: List[str] = []
    bullets: List[str] = []

    def flush_bullets() -> None:
        if bullets:
            out.append("<ul>" + "".join(f"<li>{b}</li>" for b in bullets) + "</ul>")
            bullets.clear()

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            flush_bullets()
            continue
        if stripped.startswith("### "):
            flush_bullets()
            out.append(f"<h3>{_inline(stripped[4:].strip())}</h3>")
        elif stripped.startswith("## "):
            flush_bullets()
            out.append(f"<h2>{_inline(stripped[3:].strip())}</h2>")
        elif stripped.startswith("- ") or stripped.startswith("• "):
            bullets.append(_inline(stripped[2:].strip()))
        else:
            flush_bullets()
            out.append(f"<p>{_inline(stripped)}</p>")
    flush_bullets()
    return "\n".join(out)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_share_page.py -v -k markdown_min`
Expected: PASS (3 passed)

- [ ] **Step 5: 커밋**

```bash
git add app/services/share_page.py tests/test_share_page.py
git commit -m "feat: add dependency-free minimal markdown renderer for share pages"
```

---

## Task 2: 디지스트 공유 HTML 렌더러

**Files:**
- Modify: `app/services/share_page.py`
- Test: `tests/test_share_page.py`

`render_digest_share_html` 순수 함수를 만든다. headline + summary_md(Task 1 변환기) +
기간·영상 수 + OG 메타. 썸네일/이미지 OG는 없음.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_share_page.py` 끝에 추가:

```python
from app.services.share_page import render_digest_share_html


def test_render_digest_includes_og_and_body():
    html = render_digest_share_html(
        headline="이번 주 핵심임",
        summary_md="## 주요 내용\n- 첫째 줄임\n- 둘째 줄임",
        period_label="2026-06-01 ~ 2026-06-08",
        video_count=12,
        category="경제",
        canonical_url="https://h/d/eco/tok123",
    )
    assert '<meta property="og:title" content="이번 주 핵심임"' in html
    assert '<meta property="og:type" content="article"' in html
    assert '<meta property="og:url" content="https://h/d/eco/tok123"' in html
    assert "<h2>주요 내용</h2>" in html
    assert "<li>첫째 줄임</li>" in html
    assert "2026-06-01 ~ 2026-06-08" in html
    assert "12" in html


def test_render_digest_escapes_html_in_headline():
    html = render_digest_share_html(
        headline="<script>",
        summary_md="본문임",
        period_label="",
        video_count=0,
        category=None,
        canonical_url="https://h/d/x/y",
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_share_page.py -v -k digest`
Expected: FAIL — `ImportError: cannot import name 'render_digest_share_html'`

- [ ] **Step 3: 최소 구현**

`app/services/share_page.py` 끝에 추가:

```python
def render_digest_share_html(
    *,
    headline: Optional[str],
    summary_md: Optional[str],
    period_label: str,
    video_count: int,
    category: Optional[str],
    canonical_url: str,
) -> str:
    og_title = headline or "주간 리뷰"
    og_desc = (summary_md or "").strip().replace("\n", " ")[:160]
    metas = [
        _meta("og:title", og_title),
        _meta("og:description", og_desc),
        _meta("og:type", "article"),
        _meta("og:url", canonical_url),
    ]
    cat = f" · {escape(category)}" if category else ""
    meta_line = f"{escape(period_label)} · 분석 영상 {video_count}건{cat}"
    body = _render_markdown_min(summary_md or "")
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(og_title)}</title>
{chr(10).join(metas)}
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 720px;
         margin: 0 auto; padding: 1.5rem; color: #1f2937; line-height: 1.7; }}
  h1 {{ font-size: 1.5rem; }}
  h2 {{ font-size: 1.15rem; margin-top: 1.8rem; }}
  h3 {{ font-size: 1.02rem; margin-top: 1.2rem; }}
  ul {{ padding-left: 1.2rem; }}
  li {{ margin: .3rem 0; }}
  .meta {{ color: #9ca3af; font-size: .85rem; }}
</style>
</head>
<body>
<h1>{escape(headline or "주간 리뷰")}</h1>
<p class="meta">{meta_line}</p>
{body}
</body>
</html>"""
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_share_page.py -v -k digest`
Expected: PASS (2 passed)

- [ ] **Step 5: 커밋**

```bash
git add app/services/share_page.py tests/test_share_page.py
git commit -m "feat: add render_digest_share_html server-side renderer"
```

---

## Task 3: 텔레그램 메시지 본문 빌더 (순수 함수)

**Files:**
- Modify: `app/services/digest_service.py`
- Test: `tests/test_digest_telegram_link.py` (신규)

`_send_digest_telegram`에서 보낼 텍스트를 만드는 부분을 순수 함수로 분리해
링크 첨부 로직을 단위 테스트한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_digest_telegram_link.py` 신규:

```python
from app.services.digest_service import build_digest_telegram_text, _build_digest_share_url


def test_share_url_built_when_token_and_base(monkeypatch):
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "PUBLIC_BASE_URL", "https://h", raising=False)
    url = _build_digest_share_url("eco", "tok123")
    assert url == "https://h/d/eco/tok123"


def test_share_url_empty_without_base(monkeypatch):
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "PUBLIC_BASE_URL", "", raising=False)
    assert _build_digest_share_url("eco", "tok123") == ""


def test_text_includes_link_when_enabled(monkeypatch):
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "PUBLIC_BASE_URL", "https://h", raising=False)
    text = build_digest_telegram_text(
        headline="핵심임", telegram_summary="요약임",
        slug="eco", share_token="tok123", share_link_enabled=True,
    )
    assert "<b>핵심임</b>" in text
    assert "요약임" in text
    assert 'https://h/d/eco/tok123' in text
    assert "웹에서 자세히 보기" in text


def test_text_excludes_link_when_disabled(monkeypatch):
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "PUBLIC_BASE_URL", "https://h", raising=False)
    text = build_digest_telegram_text(
        headline="핵심임", telegram_summary="요약임",
        slug="eco", share_token="tok123", share_link_enabled=False,
    )
    assert "웹에서 자세히 보기" not in text


def test_text_excludes_link_when_no_token():
    text = build_digest_telegram_text(
        headline="핵심임", telegram_summary="요약임",
        slug="eco", share_token=None, share_link_enabled=True,
    )
    assert "웹에서 자세히 보기" not in text
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_digest_telegram_link.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_digest_telegram_text'`

- [ ] **Step 3: 최소 구현**

`app/services/digest_service.py`에서 `from html import escape`가 없으면 import 추가
(상단 import 블록). 그리고 `_send_digest_telegram` 함수 정의 바로 위에 추가:

```python
def _build_digest_share_url(slug: str, share_token: Optional[str]) -> str:
    from app.config import settings as app_settings

    base = (app_settings.PUBLIC_BASE_URL or "").rstrip("/")
    if not base or not slug or not share_token:
        return ""
    return f"{base}/d/{slug}/{share_token}"


def build_digest_telegram_text(
    *,
    headline: str,
    telegram_summary: str,
    slug: str,
    share_token: Optional[str],
    share_link_enabled: bool,
) -> str:
    text = f"<b>{headline}</b>\n\n{telegram_summary}"
    if share_link_enabled:
        url = _build_digest_share_url(slug, share_token)
        if url:
            text += f'\n\n📖 <a href="{escape(url, quote=True)}">웹에서 자세히 보기</a>'
    return text
```

`from html import escape`를 상단 import에 추가(없을 경우):

```python
from html import escape
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_digest_telegram_link.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: 커밋**

```bash
git add app/services/digest_service.py tests/test_digest_telegram_link.py
git commit -m "feat: add build_digest_telegram_text with toggleable share link"
```

---

## Task 4: `Digest` 모델에 공유 컬럼 + 스키마 자가치유

**Files:**
- Modify: `app/models/pg/digest.py`
- Modify: `app/services/db_engine.py:175-196`
- Test: `tests/test_digest_share_settings.py` (신규)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_digest_share_settings.py` 신규:

```python
from app.models.pg.digest import Digest


def test_digest_model_has_share_columns():
    cols = Digest.__table__.columns
    assert "share_token" in cols
    assert "share_visibility" in cols
    assert cols["share_token"].unique is True
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_digest_share_settings.py -v`
Expected: FAIL — `KeyError: 'share_token'`

- [ ] **Step 3: 모델 컬럼 추가**

`app/models/pg/digest.py`의 `category` 컬럼 정의 아래(또는 `video_count` 부근)에 추가:

```python
    share_token: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    share_visibility: Mapped[str | None] = mapped_column(Text, nullable=True)
```

(`Text`는 이미 import되어 있음.)

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_digest_share_settings.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: 기존 스키마 멱등 ALTER 추가**

`app/services/db_engine.py`의 `additive_columns` 리스트(현재 `videos` 항목들 아래)에 추가:

```python
                    ("digests", "share_token", "text"),
                    ("digests", "share_visibility", "text"),
```

그리고 videos용 유니크 인덱스 `await conn.execute(...)` 블록 **바로 다음**에 digest용 인덱스 추가:

```python
                await conn.execute(
                    text(
                        f'CREATE UNIQUE INDEX IF NOT EXISTS '
                        f'"ux_{group.schema_name}_digests_share_token" '
                        f'ON "{group.schema_name}"."digests" (share_token) '
                        f'WHERE share_token IS NOT NULL'
                    )
                )
```

- [ ] **Step 6: 전체 테스트 그린 확인 (회귀)**

Run: `python -m pytest tests/test_digest_share_settings.py tests/test_default_settings.py -v`
Expected: PASS

- [ ] **Step 7: 커밋**

```bash
git add app/models/pg/digest.py app/services/db_engine.py tests/test_digest_share_settings.py
git commit -m "feat: add share_token/share_visibility to Digest model + idempotent migration"
```

---

## Task 5: 디지스트 생성 시 토큰 발급 + 텔레그램 배선

**Files:**
- Modify: `app/services/digest_service.py`

`generate_digest_for_group`에서 토큰을 발급하고, `_send_digest_telegram`을
Task 3의 순수 빌더와 토큰을 쓰도록 배선한다. (DB 연동 함수라 단위 테스트 없음 —
Task 3·8에서 순수 로직/렌더를 이미 커버. 변경 후 전체 스위트로 회귀만 확인.)

- [ ] **Step 1: 토큰 발급 import 추가**

`app/services/digest_service.py` 상단 import에 추가:

```python
from app.services.share_token import generate_share_token, DEFAULT_VISIBILITY
```

- [ ] **Step 2: Digest 생성 시 토큰 채우기**

`generate_digest_for_group` 안의 `digest = Digest(` 호출에서 `status=status,` 위(또는
인자 목록 끝)에 추가:

```python
            share_token=generate_share_token(),
            share_visibility=DEFAULT_VISIBILITY,
```

- [ ] **Step 3: `_send_digest_telegram` 시그니처/본문 교체**

기존 함수를 아래로 교체:

```python
async def _send_digest_telegram(
    group_id: int,
    headline: str,
    telegram_summary: str,
    *,
    slug: str,
    share_token: Optional[str],
    share_link_enabled: bool,
) -> None:
    notif = await get_settings_manager().get_notification(group_id)
    if not notif.is_sendable:
        return
    text = build_digest_telegram_text(
        headline=headline,
        telegram_summary=telegram_summary,
        slug=slug,
        share_token=share_token,
        share_link_enabled=share_link_enabled,
    )
    import httpx

    async with httpx.AsyncClient(timeout=20.0) as client:
        for chat_id in notif.chat_ids:
            await send_telegram(client, notif.bot_token, chat_id, text, notif.parse_mode)
```

- [ ] **Step 4: 호출부(`run_digest_tick_once`) 인자 전달**

`run_digest_tick_once`의 `_send_digest_telegram(...)` 호출을 교체:

```python
            if cfg.telegram_enabled and digest.telegram_summary:
                await _send_digest_telegram(
                    group.group_id,
                    digest.headline or "주간 리뷰",
                    digest.telegram_summary,
                    slug=group.slug,
                    share_token=digest.share_token,
                    share_link_enabled=cfg.share_link_enabled,
                )
```

> 참고: `cfg.share_link_enabled`는 Task 6에서 `DigestSettings`에 추가한다.
> Task 6을 먼저 끝내거나, 본 Task 4·5·6을 연속 실행한 뒤 한 번에 회귀 검증한다.

- [ ] **Step 5: 회귀 확인 (import/구문)**

Run: `python -c "import app.services.digest_service"`
Expected: 에러 없음 (단, `cfg.share_link_enabled` 참조는 런타임 속성이라 import 시점엔 무관)

- [ ] **Step 6: 커밋**

```bash
git add app/services/digest_service.py
git commit -m "feat: issue share_token on digest creation and wire share link into telegram send"
```

---

## Task 6: 설정 — `share_link_enabled` 토글 (백엔드 + 기본 시드)

**Files:**
- Modify: `app/services/settings_types.py:121-128`
- Modify: `app/services/settings_manager.py:264-272`
- Modify: `app/services/default_settings.py:54-61`
- Test: `tests/test_digest_share_settings.py` (확장)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_digest_share_settings.py`에 추가:

```python
from app.services.settings_types import DigestSettings
from app.services.default_settings import DEFAULT_GROUP_SETTINGS


def test_digest_settings_default_share_link_enabled():
    assert DigestSettings().share_link_enabled is True


def test_default_seed_includes_share_link_enabled():
    keys = {i["key"] for i in DEFAULT_GROUP_SETTINGS["digest"]}
    assert "share_link_enabled" in keys
    item = next(i for i in DEFAULT_GROUP_SETTINGS["digest"] if i["key"] == "share_link_enabled")
    assert item["value"] == "true"
    assert item["value_type"] == "bool"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_digest_share_settings.py -v -k share_link`
Expected: FAIL — `AttributeError: 'DigestSettings' object has no attribute 'share_link_enabled'`

- [ ] **Step 3: 데이터클래스 필드 추가**

`app/services/settings_types.py`의 `DigestSettings`에서 `category: str = ""` 아래에 추가:

```python
    share_link_enabled: bool = True
```

- [ ] **Step 4: 로더 파싱 추가**

`app/services/settings_manager.py`의 `get_digest` 안 `return DigestSettings(`에서
`category=str(d.get("category") or ""),` 아래에 추가:

```python
            share_link_enabled=bool(d.get("share_link_enabled", True)),
```

- [ ] **Step 5: 기본 시드 추가**

`app/services/default_settings.py`의 `"digest"` 배열에서 `telegram_enabled` 항목 아래에 추가:

```python
        {"key": "share_link_enabled", "value": "true", "value_type": "bool"},
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `python -m pytest tests/test_digest_share_settings.py tests/test_default_settings.py -v`
Expected: PASS (전부)

- [ ] **Step 7: 커밋**

```bash
git add app/services/settings_types.py app/services/settings_manager.py app/services/default_settings.py tests/test_digest_share_settings.py
git commit -m "feat: add share_link_enabled toggle to digest settings"
```

---

## Task 7: 공개 라우트 `GET /d/{slug}/{token}` + SPA 폴백 가드

**Files:**
- Modify: `app/routers/share.py`
- Modify: `app/main.py:118-124`
- Test: `tests/test_spa_serving.py` (확장)

라우트 자체는 DB가 필요해 단위 테스트하지 않는다(기존 `/v/` 라우트도 동일).
SPA 폴백 가드는 TestClient로 검증한다.

- [ ] **Step 1: 실패하는 테스트 작성 (SPA 가드)**

`tests/test_spa_serving.py`에 추가:

```python
def test_digest_share_misroute_is_404_not_spa():
    """2-세그먼트 미매칭(/d/onlyone)은 SPA로 흡수되지 않고 404."""
    resp = client.get("/d/onlyone")
    assert resp.status_code == 404
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_spa_serving.py -v -k digest_share_misroute`
Expected: FAIL — 200/503 반환(SPA로 흡수됨)

- [ ] **Step 3: SPA 폴백 가드 추가**

`app/main.py`의 `spa_fallback` 제외 조건에서 `or full_path.startswith("v/")` 아래에 추가:

```python
        or full_path.startswith("d/")
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_spa_serving.py -v`
Expected: PASS (전부)

- [ ] **Step 5: 공개 라우트 구현**

`app/routers/share.py`의 import에 추가:

```python
from app.models.pg.digest import Digest
from app.services.share_page import render_digest_share_html
```

(기존에 `from app.services.share_page import render_share_html`가 있으므로 한 줄로 합쳐도 됨.)

파일 끝에 핸들러 추가:

```python
@router.get("/d/{slug}/{token}", response_class=HTMLResponse, include_in_schema=False)
async def digest_share_page(slug: str, token: str) -> HTMLResponse:
    group = await get_group_by_slug_or_404(slug)
    async with dpm.group_session(group) as session:
        digest = (
            await session.execute(select(Digest).where(Digest.share_token == token))
        ).scalar_one_or_none()
        if digest is None:
            raise HTTPException(status_code=404)
        if (digest.share_visibility or "unlisted") != "unlisted":
            raise HTTPException(status_code=404)
        period_label = f"{_kst(digest.period_start)} ~ {_kst(digest.period_end)}"
        html = render_digest_share_html(
            headline=digest.headline,
            summary_md=digest.summary_md,
            period_label=period_label,
            video_count=digest.video_count or 0,
            category=digest.category,
            canonical_url=f"{app_settings.PUBLIC_BASE_URL}/d/{slug}/{token}",
        )
    return HTMLResponse(content=html)
```

> `_kst`, `get_group_by_slug_or_404`, `dpm`, `app_settings`, `select`,
> `HTTPException`, `HTMLResponse`는 이 파일에 이미 import/정의되어 있다.

- [ ] **Step 6: import/구문 회귀 확인**

Run: `python -c "import app.routers.share; import app.main"`
Expected: 에러 없음

- [ ] **Step 7: 커밋**

```bash
git add app/routers/share.py app/main.py tests/test_spa_serving.py
git commit -m "feat: add public GET /d/{slug}/{token} digest share route + SPA guard"
```

---

## Task 8: 프론트엔드 설정 폼 토글

**Files:**
- Modify: `frontend/src/settings/defs.ts:77-85`

- [ ] **Step 1: 토글 필드 추가**

`frontend/src/settings/defs.ts`의 `digest` 배열에서 `telegram_enabled` 항목 아래에 추가:

```ts
    { key: 'share_link_enabled', label: '웹에서 자세히 보기 링크 첨부', type: 'bool' },
```

- [ ] **Step 2: 타입체크/빌드 확인**

Run: `cd frontend && npm run build`
Expected: 빌드 성공(타입 에러 없음)

- [ ] **Step 3: 커밋**

```bash
git add frontend/src/settings/defs.ts
git commit -m "feat: add 웹에서 자세히 보기 링크 toggle to digest settings form"
```

---

## Task 9: 최종 회귀 + 수동 검증

**Files:** 없음 (검증 전용)

- [ ] **Step 1: 전체 백엔드 테스트**

Run: `python -m pytest -q`
Expected: 전부 PASS (신규 테스트 포함)

- [ ] **Step 2: 수동 스모크 (선택, DB 가용 시)**

1. 설정에서 digest `enabled`/`telegram_enabled`/`share_link_enabled`를 켠다.
2. `POST /api/groups/{slug}/digests/generate`로 디지스트를 생성한다.
3. 응답의 `digest_pk`로 DB에서 `share_token`을 확인한다.
4. 브라우저로 `{PUBLIC_BASE_URL}/d/{slug}/{token}` 접속 → headline·요약이 보이고,
   잘못된 토큰은 404인지 확인한다.
5. 스케줄 발송 시 텔레그램 메시지 끝에 "📖 웹에서 자세히 보기" 링크가 붙는지,
   `share_link_enabled`를 끄면 사라지는지 확인한다.

- [ ] **Step 3: 브랜치 정리**

`superpowers:finishing-a-development-branch` 스킬로 머지/PR 여부를 결정한다.

---

## Self-Review 결과

- **스펙 커버리지:** 모델 컬럼(Task 4) · 마이그레이션(Task 4) · 토큰 발급(Task 5) ·
  공개 라우트(Task 7) · 최소 마크다운 렌더(Task 1) · 디지스트 렌더(Task 2) ·
  SPA 가드(Task 7) · 설정 토글(Task 6) · 텔레그램 링크(Task 3·5) · 프론트 토글(Task 8) —
  스펙의 7개 설계 섹션 모두 대응됨.
- **의존 순서 주의:** Task 5 Step 4가 `cfg.share_link_enabled`(Task 6 산출물)를 참조한다.
  실행은 Task 1→2→3→4→6→5→7→8 순서를 권장(또는 4·5·6을 연속 실행 후 회귀).
- **타입 일관성:** `build_digest_telegram_text`/`_build_digest_share_url`/
  `render_digest_share_html`/`_render_markdown_min` 시그니처가 정의·호출부에서 일치.
- **플레이스홀더:** 없음. 모든 코드 스텝에 실제 코드 포함.
