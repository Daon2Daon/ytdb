# 매거진형 출력 + 멀티채널 렌더링 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 분석 본문을 구조화 데이터(`analysis_sections`)로 전환하고, 단일 뷰모델에서 웹·텔레그램·공유 HTML 페이지를 각각 렌더링하며, 텔레그램에 간소화+공유링크를 제공한다.

**Architecture:** LLM은 표현이 아닌 의미(구조화 `bullets[]`)를 생산하고, `build_view_model`이 단일 진실원천을 만들며(레거시 `full_analysis_md` 폴백 포함), 채널별 얇은 프리젠터(웹 React, 텔레그램 HTML, SSR HTML)가 각자 가독성을 입힌다. 마이그레이션은 기존 추가 전용(additive) 패턴을 따른다.

**Tech Stack:** Python 3.10 / FastAPI / SQLAlchemy(async) / PostgreSQL(JSONB) / React + TypeScript + react-markdown / pytest.

설계 출처: `docs/superpowers/specs/2026-06-06-magazine-output-multichannel-design.md`

---

## 파일 구조 (생성/수정 맵)

**Phase 1 — 데이터 모델**
- Modify: `app/models/pg/video_analysis.py` — `analysis_sections` JSONB 컬럼
- Modify: `app/services/db_engine.py:175` — `additive_columns`에 신규 컬럼
- Modify: `app/schemas/video.py` — `AnalysisOut`에 `analysis_sections`
- Modify: `app/services/analyzer.py` — 저장/기본 프롬프트

**Phase 2 — 뷰모델 + 프리젠터(백엔드)**
- Create: `app/services/analysis_view.py` — `AnalysisView` 뷰모델 + `build_view_model` + 텔레그램 섹션 렌더
- Modify: `app/services/notify_service.py` — 섹션 기반 본문 렌더 + 공유링크
- Test: `tests/test_analysis_view.py`, `tests/test_notify_render.py`

**Phase 3 — 프론트엔드**
- Modify: `frontend/src/api/types.ts` — `AnalysisSection`, `AnalysisOut`
- Modify: `frontend/src/pages/VideoDetail.tsx` — 섹션 렌더 + `remark-breaks` 폴백
- Modify: `frontend/package.json` — `remark-breaks`

**Phase 4 — 공유 링크(SSR + OG)**
- Modify: `app/models/pg/video.py` — `share_token`, `share_visibility`
- Modify: `app/services/db_engine.py` — 컬럼 + 유니크 인덱스
- Modify: `app/config.py` — `PUBLIC_BASE_URL`
- Create: `app/services/share_token.py` — 토큰 생성 헬퍼
- Modify: `app/services/analyzer.py` — 저장 시 토큰 보장
- Create: `app/services/share_page.py` — 매거진 HTML 렌더(순수 함수)
- Create: `app/routers/share.py` — 공개 SSR 엔드포인트
- Modify: `app/main.py` — 라우터 등록(무인증) + SPA 폴백 제외
- Modify: `app/services/settings_types.py` — `include_share_link`
- Modify: `app/services/notify_service.py` — 공유링크 첨부
- Test: `tests/test_share_page.py`, `tests/test_share_token.py`

**Phase 5 — 프롬프트 가이드**
- Create: `docs/prompt-guide.md` — 사용자용 프롬프트 설계 가이드
- Modify: `app/services/analyzer.py` — `DEFAULT_ANALYSIS_PROMPT`에 `analysis_sections` 반영

---

## Phase 1 — 구조화 데이터 모델

### Task 1: `analysis_sections` 컬럼 추가 (모델)

**Files:**
- Modify: `app/models/pg/video_analysis.py:34`

- [ ] **Step 1: 모델에 컬럼 추가**

`app/models/pg/video_analysis.py`의 `key_points` 정의 바로 위(라인 34 부근)에 추가:

```python
    # 구조화 분석 본문: [{key, title, bullets[]}]. full_analysis_md를 대체.
    analysis_sections: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
```

- [ ] **Step 2: 추가 전용 마이그레이션 등록**

`app/services/db_engine.py`의 `additive_columns`(라인 175 부근)를 수정:

```python
                additive_columns = [
                    ("channels", "notify_from", "timestamptz"),
                    ("video_analysis", "analysis_sections", "jsonb"),
                ]
```

- [ ] **Step 3: 검증 — import 무결성**

Run: `python -c "from app.models.pg.video_analysis import VideoAnalysis; print(VideoAnalysis.__table__.c.analysis_sections.type)"`
Expected: `JSONB` 출력, 에러 없음.

- [ ] **Step 4: Commit**

```bash
git add app/models/pg/video_analysis.py app/services/db_engine.py
git commit -m "feat: video_analysis에 analysis_sections JSONB 컬럼 추가"
```

### Task 2: 저장 로직에 `analysis_sections` 반영

**Files:**
- Modify: `app/services/analyzer.py:202-240`

- [ ] **Step 1: upsert values에 컬럼 추가**

`app/services/analyzer.py`의 `save_to_db` 내 `pg_insert(VideoAnalysis).values(...)`에서 `full_analysis_md` 줄 아래에 추가:

```python
            full_analysis_md=data.get("full_analysis_md"),
            analysis_sections=data.get("analysis_sections"),
```

- [ ] **Step 2: on_conflict set_ 목록에 추가**

같은 함수 `set_` 컬럼 튜플에서 `"full_analysis_md",` 아래에 `"analysis_sections",` 추가:

```python
                    "full_analysis_md",
                    "analysis_sections",
```

- [ ] **Step 3: 검증 — import**

Run: `python -c "import app.services.analyzer"`
Expected: 에러 없음.

- [ ] **Step 4: Commit**

```bash
git add app/services/analyzer.py
git commit -m "feat: analysis_sections 저장(upsert) 반영"
```

### Task 3: 출력 스키마에 `analysis_sections` 노출

**Files:**
- Modify: `app/schemas/video.py:18`
- Test: `tests/test_analysis_schema.py`

- [ ] **Step 1: 실패 테스트 작성**

Create `tests/test_analysis_schema.py`:

```python
from app.schemas.video import AnalysisOut


def test_analysis_out_exposes_sections():
    fields = AnalysisOut.model_fields
    assert "analysis_sections" in fields
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_analysis_schema.py -v`
Expected: FAIL (`assert "analysis_sections" in fields`).

- [ ] **Step 3: 스키마에 필드 추가**

`app/schemas/video.py`의 `AnalysisOut`에서 `full_analysis_md: Optional[str]` 아래에:

```python
    full_analysis_md: Optional[str]
    analysis_sections: Optional[Any]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_analysis_schema.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_analysis_schema.py app/schemas/video.py
git commit -m "feat: AnalysisOut에 analysis_sections 노출"
```

---

## Phase 2 — 뷰모델 + 백엔드 프리젠터

### Task 4: `AnalysisView` 뷰모델 + `build_view_model` (레거시 폴백 격리)

**Files:**
- Create: `app/services/analysis_view.py`
- Test: `tests/test_analysis_view.py`

- [ ] **Step 1: 실패 테스트 작성**

Create `tests/test_analysis_view.py`:

```python
from app.services.analysis_view import Section, build_sections


def test_build_sections_from_structured():
    raw = [
        {"key": "overview", "title": "개요", "bullets": ["문장1", "문장2"]},
        {"key": "risk", "title": "리스크", "bullets": ["문장3"]},
    ]
    out = build_sections(raw, legacy_md=None)
    assert out == [
        Section(key="overview", title="개요", bullets=["문장1", "문장2"]),
        Section(key="risk", title="리스크", bullets=["문장3"]),
    ]


def test_build_sections_falls_back_to_legacy_markdown():
    out = build_sections(None, legacy_md="### 제목\n본문임")
    assert len(out) == 1
    assert out[0].key == "_legacy"
    assert out[0].title == ""
    assert out[0].markdown == "### 제목\n본문임"
    assert out[0].bullets == []


def test_build_sections_empty_returns_empty_list():
    assert build_sections(None, legacy_md=None) == []
    assert build_sections([], legacy_md="") == []


def test_build_sections_skips_malformed_entries():
    raw = [{"title": "no key ok", "bullets": ["a"]}, "garbage", {"bullets": []}]
    out = build_sections(raw, legacy_md=None)
    assert len(out) == 1
    assert out[0].title == "no key ok"
    assert out[0].bullets == ["a"]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_analysis_view.py -v`
Expected: FAIL (`ModuleNotFoundError: app.services.analysis_view`).

- [ ] **Step 3: 뷰모델 구현**

Create `app/services/analysis_view.py`:

```python
"""분석 결과의 정규 뷰모델.

DB의 구조화 데이터(analysis_sections) 또는 레거시 full_analysis_md를 받아
채널 무관한 단일 표현으로 정규화한다. 모든 채널 프리젠터(웹/텔레그램/SSR)는
이 뷰모델만 소비하며, 마크다운 블롭 추측 로직은 이 파일에만 존재한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass(frozen=True)
class Section:
    """본문 한 섹션. 구조화는 bullets, 레거시는 markdown으로 표현."""

    key: str
    title: str
    bullets: List[str] = field(default_factory=list)
    markdown: Optional[str] = None  # 레거시 폴백 전용


def _clean_str(v: Any) -> str:
    return str(v).strip() if v is not None else ""


def build_sections(
    raw_sections: Any, legacy_md: Optional[str]
) -> List[Section]:
    """구조화 우선, 없으면 레거시 마크다운 단일 섹션으로 폴백."""
    if isinstance(raw_sections, list) and raw_sections:
        out: List[Section] = []
        for item in raw_sections:
            if not isinstance(item, dict):
                continue
            title = _clean_str(item.get("title"))
            bullets = [
                _clean_str(b)
                for b in (item.get("bullets") or [])
                if _clean_str(b)
            ]
            if not title and not bullets:
                continue
            out.append(
                Section(
                    key=_clean_str(item.get("key")) or "section",
                    title=title,
                    bullets=bullets,
                )
            )
        if out:
            return out
    legacy = _clean_str(legacy_md)
    if legacy:
        return [Section(key="_legacy", title="", bullets=[], markdown=legacy)]
    return []
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_analysis_view.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add tests/test_analysis_view.py app/services/analysis_view.py
git commit -m "feat: 분석 뷰모델 build_sections (구조화+레거시 폴백)"
```

### Task 5: 텔레그램 섹션 렌더러

**Files:**
- Modify: `app/services/notify_service.py:155-180`
- Test: `tests/test_notify_render.py`

- [ ] **Step 1: 실패 테스트 작성**

Create `tests/test_notify_render.py`:

```python
from app.services.analysis_view import Section
from app.services.notify_service import _sections_to_telegram_html


def test_sections_render_title_and_bullets_with_newlines():
    sections = [
        Section(key="overview", title="개요", bullets=["첫째임", "둘째임"]),
    ]
    out = _sections_to_telegram_html(sections)
    assert "<b>개요</b>" in out
    # 불릿마다 실제 줄바꿈
    assert "• 첫째임\n• 둘째임" in out


def test_sections_render_inline_bold():
    sections = [Section(key="k", title="t", bullets=["**핵심**: 내용임"])]
    out = _sections_to_telegram_html(sections)
    assert "<b>핵심</b>" in out


def test_legacy_section_uses_markdown_path():
    sections = [Section(key="_legacy", title="", bullets=[], markdown="### 제목\n본문")]
    out = _sections_to_telegram_html(sections)
    assert "<b>제목</b>" in out
    assert "본문" in out
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_notify_render.py -v`
Expected: FAIL (`ImportError: cannot import name '_sections_to_telegram_html'`).

- [ ] **Step 3: 렌더러 구현**

`app/services/notify_service.py`에 `_md_to_telegram_html`(라인 87) 아래에 추가:

```python
def _sections_to_telegram_html(sections) -> str:
    """AnalysisView 섹션들을 텔레그램 HTML로 렌더.

    구조화 섹션: <b>제목</b> 다음 줄부터 '• 문장'을 줄바꿈으로 나열.
    레거시 섹션(markdown): 기존 _md_to_telegram_html 경로 사용.
    """
    blocks = []
    for s in sections:
        if s.markdown:
            blocks.append(_md_to_telegram_html(s.markdown))
            continue
        lines = []
        if s.title:
            lines.append(f"<b>{_escape_plain(s.title)}</b>")
        for b in s.bullets:
            lines.append(f"• {_md_to_telegram_html(b)}")
        if lines:
            blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
```

참고: `_md_to_telegram_html`는 한 줄 입력 시 `### `를 `<b>`로 바꾸지만, 불릿 문자열은
헤더가 아니므로 인라인 `**bold**`만 변환된다. 의도된 동작.

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_notify_render.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add tests/test_notify_render.py app/services/notify_service.py
git commit -m "feat: 텔레그램 섹션 렌더러 _sections_to_telegram_html"
```

### Task 6: 텔레그램 본문을 섹션 기반으로 전환

**Files:**
- Modify: `app/services/notify_service.py:183-218` (`_build_full`)
- Test: `tests/test_notify_render.py` (추가)

- [ ] **Step 1: 실패 테스트 추가**

`tests/test_notify_render.py` 끝에 추가:

```python
from types import SimpleNamespace

from app.services.notify_service import _build_full


def _mk(analysis_sections=None, full_analysis_md=None):
    video = SimpleNamespace(
        title="제목", video_url="https://y/1", published_at=None,
        duration_seconds=None,
    )
    analysis = SimpleNamespace(
        headline="헤드", one_line="한줄", short_summary_md="요약",
        confidence_score=0.9, bullet_points=[],
        analysis_sections=analysis_sections, full_analysis_md=full_analysis_md,
    )
    return video, analysis


def test_build_full_uses_structured_sections():
    video, analysis = _mk(
        analysis_sections=[{"key": "k", "title": "개요", "bullets": ["첫째임"]}]
    )
    out = _build_full(video, analysis, 0.0, "채널", [])
    assert "<b>개요</b>" in out
    assert "• 첫째임" in out


def test_build_full_falls_back_to_legacy_md():
    video, analysis = _mk(full_analysis_md="### 옛제목\n옛본문")
    out = _build_full(video, analysis, 0.0, "채널", [])
    assert "<b>옛제목</b>" in out
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_notify_render.py -k build_full -v`
Expected: FAIL (`_build_full`이 아직 sections를 안 봄 / 속성 없음 에러).

- [ ] **Step 3: `_build_full` 수정**

`app/services/notify_service.py`의 `_build_full`에서 `body = analysis.full_analysis_md or analysis.short_summary_md or ""` 줄을 다음으로 교체:

```python
    from app.services.analysis_view import build_sections

    sections = build_sections(
        getattr(analysis, "analysis_sections", None),
        getattr(analysis, "full_analysis_md", None),
    )
    body = _sections_to_telegram_html(sections) if sections else (analysis.short_summary_md or "")
```

그리고 같은 함수 내 `render()` 호출에서 `body`를 `_md_to_telegram_html`로 다시 감싸지
않도록, `_render_full`에 전달되는 `body`는 이미 HTML임을 표시한다. `_render_full`(라인 166)의:

```python
    if body:
        lines.append(_md_to_telegram_html(body))
```

를 다음으로 교체(이중 변환 방지):

```python
    if body:
        lines.append(body)
```

> 주의: 이제 `_build_full`이 `body`를 항상 HTML로 만들어 넘기므로 `_render_full`은
> 추가 변환을 하지 않는다. 레거시 폴백도 `_sections_to_telegram_html`이 변환을 끝낸다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_notify_render.py -v`
Expected: PASS (전체).

- [ ] **Step 5: 회귀 확인**

Run: `pytest tests/ -k notify -v`
Expected: 기존 notify 관련 테스트 PASS (실패 시 이중변환/줄바꿈 회귀 점검).

- [ ] **Step 6: Commit**

```bash
git add tests/test_notify_render.py app/services/notify_service.py
git commit -m "feat: 텔레그램 본문을 구조화 섹션 기반으로 렌더(레거시 폴백)"
```

---

## Phase 3 — 프론트엔드 렌더링

### Task 7: 프론트 타입 + `remark-breaks` 의존성

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/package.json`

- [ ] **Step 1: 타입 추가**

`frontend/src/api/types.ts`의 `KeyPoint` 인터페이스 아래에 추가:

```typescript
export interface AnalysisSection {
  key: string
  title: string
  bullets: string[]
}
```

그리고 `VideoDetail`(또는 분석을 담는 인터페이스) 내 분석 필드 정의에서 `full_analysis_md`
옆에 추가(해당 인터페이스를 열어 확인 후):

```typescript
  analysis_sections: AnalysisSection[] | null
```

- [ ] **Step 2: remark-breaks 설치**

Run:
```bash
cd frontend && npm install remark-breaks@^4.0.0
```
Expected: `package.json` dependencies에 `remark-breaks` 추가, 에러 없음.

- [ ] **Step 3: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음(신규 필드 미사용이어도 통과).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/types.ts frontend/package.json frontend/package-lock.json
git commit -m "feat(fe): AnalysisSection 타입 + remark-breaks 의존성"
```

### Task 8: 상세 화면에서 섹션 렌더 + 레거시 폴백

**Files:**
- Modify: `frontend/src/pages/VideoDetail.tsx:254-260`

- [ ] **Step 1: import 추가**

`frontend/src/pages/VideoDetail.tsx` 상단 import에 추가:

```typescript
import remarkBreaks from 'remark-breaks'
```

- [ ] **Step 2: 상세 분석 블록 교체**

`{video.full_analysis_md ? (...) : ...}` 블록(라인 254 부근)에서 상세 분석 렌더부를 다음으로 교체.
`video.analysis_sections`가 있으면 구조화 렌더, 없으면 기존 마크다운(+remark-breaks)으로 폴백:

```tsx
        {video.analysis_sections && video.analysis_sections.length > 0 ? (
          <div className="bg-white rounded-xl shadow-sm p-4 sm:p-5">
            <h2 className="font-semibold text-gray-800 mb-4">상세 분석</h2>
            <div className="space-y-5">
              {video.analysis_sections.map((sec) => (
                <section key={sec.key}>
                  {sec.title && (
                    <h3 className="font-semibold text-gray-800 mb-2">{sec.title}</h3>
                  )}
                  <ul className="space-y-1.5">
                    {sec.bullets.map((b, i) => (
                      <li key={i} className="flex gap-2 text-sm text-gray-700">
                        <span className="text-blue-400 shrink-0">•</span>
                        <article className="prose prose-sm max-w-none min-w-0 break-words">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{b}</ReactMarkdown>
                        </article>
                      </li>
                    ))}
                  </ul>
                </section>
              ))}
            </div>
          </div>
        ) : video.full_analysis_md ? (
          <div className="bg-white rounded-xl shadow-sm p-4 sm:p-5">
            <h2 className="font-semibold text-gray-800 mb-4">상세 분석</h2>
            <article className="prose prose-sm max-w-none text-gray-700 break-words overflow-x-auto">
              <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>{video.full_analysis_md}</ReactMarkdown>
            </article>
          </div>
        ) : video.analysis_status === 'pending' || video.analysis_status === 'processing' ? (
          <div className="bg-white rounded-xl shadow-sm p-8 text-center text-gray-400">
            <div className="text-4xl mb-2">⏳</div>
            <p>분석이 진행 중입니다...</p>
          </div>
        ) : null}
```

> 참고: `video.analysis_sections`는 API 응답 `analysis` 객체 내부에 있을 수 있다.
> 현 코드가 `video.full_analysis_md`로 평탄화 접근하므로, 동일 평탄화 매핑이 있다면
> `video.analysis_sections`로 접근한다(없으면 `video.analysis?.analysis_sections`).
> 작업 시 `frontend/src/api/videos.ts`의 매핑을 확인해 일관된 경로를 사용할 것.

- [ ] **Step 3: 빌드 검증**

Run: `cd frontend && npm run build`
Expected: 빌드 성공.

- [ ] **Step 4: 수동 검증**

`npm run dev` 후 분석 완료 영상 상세 진입 → (a) `analysis_sections` 있는 신규 분석은
섹션별 제목 + 줄바꿈된 불릿으로 표시, (b) 레거시 `•` 영상은 줄바꿈이 살아서 표시됨 확인.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/VideoDetail.tsx
git commit -m "feat(fe): 상세 분석 섹션 렌더 + 레거시 remark-breaks 폴백"
```

---

## Phase 4 — 공유 링크 (SSR + OG + 접근모드)

### Task 9: 공유 토큰/접근모드 컬럼

**Files:**
- Modify: `app/models/pg/video.py:41`
- Modify: `app/services/db_engine.py`

- [ ] **Step 1: 모델 컬럼 추가**

`app/models/pg/video.py`의 `source_channel_name` 아래에 추가:

```python
    # 공유 페이지 토큰/접근모드
    share_token: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    share_visibility: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 2: additive 컬럼 + 유니크 인덱스**

`app/services/db_engine.py`의 `additive_columns`를 확장:

```python
                additive_columns = [
                    ("channels", "notify_from", "timestamptz"),
                    ("video_analysis", "analysis_sections", "jsonb"),
                    ("videos", "share_token", "text"),
                    ("videos", "share_visibility", "text"),
                ]
```

그리고 `additive_columns` for 루프(라인 178-184) **직후**에 멱등 유니크 인덱스 생성 추가:

```python
                await conn.execute(
                    text(
                        f'CREATE UNIQUE INDEX IF NOT EXISTS '
                        f'"ux_{group.schema_name}_videos_share_token" '
                        f'ON "{group.schema_name}"."videos" (share_token) '
                        f'WHERE share_token IS NOT NULL'
                    )
                )
```

- [ ] **Step 3: 검증**

Run: `python -c "from app.models.pg.video import Video; print(Video.__table__.c.share_token.unique)"`
Expected: `True`, 에러 없음.

- [ ] **Step 4: Commit**

```bash
git add app/models/pg/video.py app/services/db_engine.py
git commit -m "feat: videos에 share_token/share_visibility + 유니크 인덱스"
```

### Task 10: 토큰 생성 헬퍼 + 저장 시 보장

**Files:**
- Create: `app/services/share_token.py`
- Test: `tests/test_share_token.py`
- Modify: `app/services/analyzer.py` (`save_to_db`)

- [ ] **Step 1: 실패 테스트**

Create `tests/test_share_token.py`:

```python
from app.services.share_token import generate_share_token


def test_token_is_urlsafe_and_unique():
    a = generate_share_token()
    b = generate_share_token()
    assert a != b
    assert len(a) >= 12
    assert all(c.isalnum() or c in "-_" for c in a)
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_share_token.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: 헬퍼 구현**

Create `app/services/share_token.py`:

```python
"""공유 페이지 토큰 생성."""

from __future__ import annotations

import secrets

DEFAULT_VISIBILITY = "unlisted"


def generate_share_token() -> str:
    """추측 불가한 URL-safe 토큰."""
    return secrets.token_urlsafe(12)
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_share_token.py -v`
Expected: PASS.

- [ ] **Step 5: 저장 시 토큰 보장**

`app/services/analyzer.py`의 `save_to_db` 시작부(메서드 본문 첫 줄)에 영상 토큰 보장 로직 추가.
`from app.services.share_token import generate_share_token, DEFAULT_VISIBILITY`를 파일 상단 import에 추가하고,
`save_to_db` 내 `data = result.data` 다음에:

```python
        await session.execute(
            update(Video)
            .where(Video.video_pk == video_pk, Video.share_token.is_(None))
            .values(share_token=generate_share_token(), share_visibility=DEFAULT_VISIBILITY)
        )
```

- [ ] **Step 6: import 검증**

Run: `python -c "import app.services.analyzer"`
Expected: 에러 없음.

- [ ] **Step 7: Commit**

```bash
git add tests/test_share_token.py app/services/share_token.py app/services/analyzer.py
git commit -m "feat: 공유 토큰 생성 + 분석 저장 시 멱등 보장"
```

### Task 11: 공개 베이스 URL 설정

**Files:**
- Modify: `app/config.py:39`

- [ ] **Step 1: 설정 추가**

`app/config.py`의 `Settings` 클래스 `SESSION_HTTPS_ONLY` 아래에 추가:

```python
    # 공유 링크 생성용 외부 공개 베이스 URL (예: https://ytdb.example.com). 끝 슬래시 없이.
    PUBLIC_BASE_URL: str = ""
```

- [ ] **Step 2: 검증**

Run: `python -c "from app.config import settings; print(repr(settings.PUBLIC_BASE_URL))"`
Expected: `''` 출력.

- [ ] **Step 3: Commit**

```bash
git add app/config.py
git commit -m "feat: PUBLIC_BASE_URL 설정 추가(공유 링크용)"
```

### Task 12: 매거진 HTML 렌더 (순수 함수)

**Files:**
- Create: `app/services/share_page.py`
- Test: `tests/test_share_page.py`

- [ ] **Step 1: 실패 테스트**

Create `tests/test_share_page.py`:

```python
from app.services.analysis_view import Section
from app.services.share_page import render_share_html


def test_render_includes_og_meta_and_sections():
    html = render_share_html(
        title="제목임",
        headline="헤드라인임",
        one_line="한줄요약임",
        thumbnail_url="https://img/x.jpg",
        canonical_url="https://h/v/eco/abc",
        sections=[Section(key="k", title="개요", bullets=["문장1임", "문장2임"])],
        tags=["태그1"],
        published_at_kst="2026-06-06 12:00 KST",
    )
    assert '<meta property="og:title" content="헤드라인임"' in html
    assert '<meta property="og:description" content="한줄요약임"' in html
    assert '<meta property="og:image" content="https://img/x.jpg"' in html
    assert "개요" in html
    assert "문장1임" in html


def test_render_escapes_html_in_content():
    html = render_share_html(
        title="t", headline="<script>", one_line="a & b",
        thumbnail_url=None, canonical_url="https://h/v/x/y",
        sections=[], tags=[], published_at_kst="",
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_share_page.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: 렌더 구현**

Create `app/services/share_page.py`:

```python
"""공유용 매거진 HTML 렌더(서버사이드, OG 메타 포함).

뷰모델(Section 리스트 + 메타)만 받아 완성된 HTML 문자열을 반환하는 순수 함수.
React SPA와 분리된 공개 읽기전용 페이지이며, 텔레그램 등 크롤러용 OG 메타를 담는다.
"""

from __future__ import annotations

from html import escape
from typing import List, Optional

from app.services.analysis_view import Section


def _meta(prop: str, content: str) -> str:
    return f'<meta property="{prop}" content="{escape(content, quote=True)}">'


def _render_sections(sections: List[Section]) -> str:
    blocks = []
    for s in sections:
        if s.markdown:
            # 레거시: 마크다운 원문을 <pre>로 안전 표시(간이). 신규는 구조화 경로 사용.
            blocks.append(f'<pre class="legacy">{escape(s.markdown)}</pre>')
            continue
        items = "".join(f"<li>{escape(b)}</li>" for b in s.bullets)
        title = f"<h2>{escape(s.title)}</h2>" if s.title else ""
        blocks.append(f'<section>{title}<ul>{items}</ul></section>')
    return "\n".join(blocks)


def render_share_html(
    *,
    title: str,
    headline: Optional[str],
    one_line: Optional[str],
    thumbnail_url: Optional[str],
    canonical_url: str,
    sections: List[Section],
    tags: List[str],
    published_at_kst: str,
) -> str:
    og_title = headline or title or ""
    og_desc = one_line or ""
    metas = [
        _meta("og:title", og_title),
        _meta("og:description", og_desc),
        _meta("og:type", "article"),
        _meta("og:url", canonical_url),
    ]
    if thumbnail_url:
        metas.append(_meta("og:image", thumbnail_url))
    tag_html = ""
    if tags:
        tag_html = '<p class="tags">' + " ".join(
            f"#{escape(t)}" for t in tags
        ) + "</p>"
    hero = (
        f'<img class="hero" src="{escape(thumbnail_url, quote=True)}" alt="">'
        if thumbnail_url
        else ""
    )
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
  .hero {{ width: 100%; border-radius: 12px; margin-bottom: 1rem; }}
  h1 {{ font-size: 1.5rem; }}
  .one-line {{ color: #6b7280; font-style: italic; }}
  h2 {{ font-size: 1.15rem; margin-top: 1.8rem; }}
  ul {{ padding-left: 1.2rem; }}
  li {{ margin: .3rem 0; }}
  .tags {{ color: #2563eb; font-size: .9rem; }}
  .meta {{ color: #9ca3af; font-size: .85rem; }}
  pre.legacy {{ white-space: pre-wrap; font-family: inherit; }}
</style>
</head>
<body>
{hero}
<h1>{escape(headline or title)}</h1>
<p class="one-line">{escape(one_line or "")}</p>
<p class="meta">{escape(published_at_kst)}</p>
{_render_sections(sections)}
{tag_html}
</body>
</html>"""
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_share_page.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add tests/test_share_page.py app/services/share_page.py
git commit -m "feat: 공유 매거진 HTML 렌더(OG 메타 포함)"
```

### Task 13: 공개 SSR 엔드포인트

**Files:**
- Create: `app/routers/share.py`
- Modify: `app/main.py:62-74, 110-122`

- [ ] **Step 1: 라우터 구현**

Create `app/routers/share.py`:

```python
"""공개 공유 페이지(무인증). GET /v/{slug}/{token} → 매거진 HTML."""

from __future__ import annotations

from datetime import timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.config import settings as app_settings
from app.models.pg.tag import Tag, VideoTag
from app.models.pg.video import Video
from app.models.pg.video_analysis import VideoAnalysis
from app.routers.deps import get_group_by_slug_or_404
from app.services.analysis_view import build_sections
from app.services.db_engine import data_plane_engine_manager as dpm
from app.services.share_page import render_share_html

router = APIRouter(tags=["share"])


def _kst(dt) -> str:
    if dt is None:
        return ""
    try:
        from zoneinfo import ZoneInfo
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")
    except Exception:
        return str(dt)


@router.get("/v/{slug}/{token}", response_class=HTMLResponse, include_in_schema=False)
async def share_page(slug: str, token: str) -> HTMLResponse:
    group = await get_group_by_slug_or_404(slug)
    async with dpm.group_session(group) as session:
        video = (
            await session.execute(select(Video).where(Video.share_token == token))
        ).scalar_one_or_none()
        if video is None:
            raise HTTPException(status_code=404)
        visibility = video.share_visibility or "unlisted"
        if visibility != "unlisted":
            # restricted/private는 인증 도입 전까지 비공개 처리
            raise HTTPException(status_code=404)
        analysis = (
            await session.execute(
                select(VideoAnalysis).where(VideoAnalysis.video_pk == video.video_pk)
            )
        ).scalar_one_or_none()
        tag_rows = (
            await session.execute(
                select(Tag.name)
                .join(VideoTag, VideoTag.tag_pk == Tag.tag_pk)
                .where(VideoTag.video_pk == video.video_pk)
            )
        ).scalars().all()

    sections = build_sections(
        getattr(analysis, "analysis_sections", None) if analysis else None,
        getattr(analysis, "full_analysis_md", None) if analysis else None,
    )
    canonical = f"{app_settings.PUBLIC_BASE_URL}/v/{slug}/{token}"
    html = render_share_html(
        title=video.title,
        headline=getattr(analysis, "headline", None) if analysis else None,
        one_line=getattr(analysis, "one_line", None) if analysis else None,
        thumbnail_url=video.thumbnail_url,
        canonical_url=canonical,
        sections=sections,
        tags=list(tag_rows),
        published_at_kst=_kst(video.published_at),
    )
    return HTMLResponse(content=html)
```

- [ ] **Step 2: `get_group_by_slug_or_404` 확인/추가**

`app/routers/deps.py`를 열어 슬러그로 그룹을 조회하는 무인증 헬퍼가 있는지 확인한다.
`get_group_or_404`가 FastAPI `Depends`용(요청 의존성)이라면, 슬러그 문자열로 직접 호출 가능한
함수가 필요하다. 없으면 `deps.py`에 추가:

```python
from app.control_db import control_session_factory  # 기존 패턴에 맞춰 import
from app.models.control.group import Group
from sqlalchemy import select


async def get_group_by_slug_or_404(slug: str) -> Group:
    async with control_session_factory() as session:
        group = (
            await session.execute(select(Group).where(Group.slug == slug))
        ).scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=404, detail="그룹을 찾을 수 없습니다.")
    return group
```

> 실제 제어세션 팩토리 이름은 `app/control_db.py`와 기존 `deps.py`의 `get_group_or_404`
> 구현을 참고해 일치시킬 것(예: `AsyncSessionLocal`, `get_control_session`).

- [ ] **Step 3: main.py에 무인증 등록 + SPA 폴백 제외**

`app/main.py` import에 `share` 추가(라인 19), 그리고 `auth.router` 등록(라인 62) 아래에 **무인증으로** 추가:

```python
from app.routers import actions, auth, channels, digests, groups, health, logs, settings, share, stats, tags, videos
...
app.include_router(auth.router)
app.include_router(share.router)  # 공개 공유 페이지(무인증)
```

그리고 `spa_fallback`의 제외 조건(라인 115-120)에 `/v/` 보호 추가:

```python
    if (
        full_path.startswith("api")
        or full_path.startswith("static")
        or full_path == "health"
        or full_path.startswith("legacy")
        or full_path.startswith("v/")
    ):
        raise HTTPException(status_code=404)
```

- [ ] **Step 4: import/기동 검증**

Run: `python -c "import app.main"`
Expected: 에러 없음.

- [ ] **Step 5: 수동 검증**

서버 기동 후 분석 완료 영상의 `share_token`을 DB에서 확인하고
`GET /v/{slug}/{token}` 접속 → 매거진 HTML 반환, `<meta property="og:title"` 포함 확인.
없는 토큰 → 404 확인.

- [ ] **Step 6: Commit**

```bash
git add app/routers/share.py app/routers/deps.py app/main.py
git commit -m "feat: 공개 SSR 공유 페이지 엔드포인트 /v/{slug}/{token}"
```

### Task 14: 텔레그램에 공유 링크 첨부 + 설정

**Files:**
- Modify: `app/services/settings_types.py:48`
- Modify: `app/services/notify_service.py` (`_render_full`, `_build_compact`, 호출부)
- Modify: `app/services/default_settings.py` (notification 기본값)

- [ ] **Step 1: 설정 필드 추가**

`app/services/settings_types.py`의 `NotificationSettings`에서 `message_detail` 아래에:

```python
    message_detail: str = "full"  # full | compact
    include_share_link: bool = True
```

- [ ] **Step 2: 기본 설정에 노출**

`app/services/default_settings.py`의 notification 기본 항목에 `parse_mode` 항목 근처(라인 40 부근) 추가:

```python
        {"key": "include_share_link", "value": "true", "value_type": "bool"},
```

(기존 항목 포맷과 동일하게 맞출 것 — 해당 파일의 value_type 표기 규칙 확인 후 적용.)

- [ ] **Step 3: 공유 URL 빌더 + 첨부**

`app/services/notify_service.py`에 헬퍼 추가(`_format_bullets` 아래):

```python
def _share_url(video) -> str:
    from app.config import settings as app_settings

    base = (app_settings.PUBLIC_BASE_URL or "").rstrip("/")
    slug = getattr(video, "_group_slug", "") or ""
    token = getattr(video, "share_token", None)
    if not base or not slug or not token:
        return ""
    return f"{base}/v/{slug}/{token}"
```

> `video`에는 그룹 슬러그가 없으므로, `notify_video` 호출 경로에서 slug를 전달해야 한다.
> 가장 단순한 방법: `notify_video`/`build_message`에 `group_slug: str = ""` 인자를 추가하고
> 렌더 함수까지 전달한다. 아래 단계에서 시그니처를 확장한다.

- [ ] **Step 4: 시그니처에 group_slug 전파**

`build_message`(라인 221), `_build_full`, `_build_compact`, `_render_full`, `notify_video`에
`group_slug: str = ""` 키워드 인자를 추가하고, compact/full 본문 끝에 링크를 덧붙인다.

`_build_compact` 반환 직전(라인 152 `return` 앞)에:

```python
    if include_share_link:
        url = share_url
        if url:
            lines.append("")
            lines.append(f'📖 <a href="{escape(url, quote=True)}">자세히 보기</a>')
    return "\n".join(lines)[:_TELEGRAM_MAX_LEN]
```

`_render_full`의 기존 영상 링크(라인 178-179) 아래에:

```python
    if share_url:
        lines.append(f'📖 <a href="{escape(share_url, quote=True)}">웹에서 자세히 보기</a>')
```

> `include_share_link`/`share_url`는 호출부에서 계산해 전달한다. `_build_full`/`_build_compact`가
> `_share_url(video)`를 호출하고 `NotificationSettings.include_share_link`를 받도록 한다.
> 정확한 인자 흐름은 `notify_video` → `build_message` → `_build_*` 순서로 일관되게 연결할 것.

- [ ] **Step 5: 테스트 추가**

`tests/test_notify_render.py`에 추가:

```python
def test_compact_appends_share_link(monkeypatch):
    import app.services.notify_service as ns
    video, analysis = _mk(analysis_sections=[{"key":"k","title":"t","bullets":["a임"]}])
    video.share_token = "tok123"
    out = ns._build_compact(video, analysis, 0.0, share_url="https://h/v/eco/tok123", include_share_link=True)
    assert "자세히 보기" in out
    assert "https://h/v/eco/tok123" in out
```

> `_build_compact` 시그니처를 `(video, analysis, threshold, *, share_url="", include_share_link=False)`로
> 확장하는 것을 전제로 한다. 시그니처 확정 후 테스트의 인자도 일치시킬 것.

- [ ] **Step 6: 테스트 통과 확인**

Run: `pytest tests/test_notify_render.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/services/settings_types.py app/services/default_settings.py app/services/notify_service.py tests/test_notify_render.py
git commit -m "feat: 텔레그램 공유 링크 첨부 + include_share_link 설정"
```

---

## Phase 5 — 프롬프트 설계 가이드 + 기본 프롬프트

### Task 15: 사용자용 프롬프트 가이드 문서

**Files:**
- Create: `docs/prompt-guide.md`

- [ ] **Step 1: 가이드 작성**

Create `docs/prompt-guide.md` — 스펙 문서의 "④ 프롬프트 설계 가이드" 섹션을 사용자용
독립 문서로 옮긴다. 다음 7개 항목을 포함:
1. 출력 계약(JSON 키 ↔ DB 컬럼 매핑표)
2. 필수(`one_line`, `short_summary_md`) vs 선택
3. 본문은 `analysis_sections [{key,title,bullets}]`로 — `bullets`는 기호/`\n` 없는 순수 문장, 인라인 `**bold**`만
4. 머신리더블 필드 활용(`entities` 확장키, `tags` 정규화, `key_points`)
5. 고정값 필드(`sentiment`/`brand_tone` 자유문자열·그룹내 일관, `confidence_score`)
6. 텔레그램·웹 매핑(headline/one_line/short_summary_md/bullet_points/analysis_sections 용도)
7. 품질 컨트롤 체크리스트

> 내용은 스펙 파일 `docs/superpowers/specs/2026-06-06-magazine-output-multichannel-design.md`의
> "④" 섹션을 그대로 옮기되, "DB 컬럼" 표는 Task 1·3의 실제 필드와 일치시킨다.

- [ ] **Step 2: Commit**

```bash
git add docs/prompt-guide.md
git commit -m "docs: 사용자용 프롬프트 설계 가이드"
```

### Task 16: 기본 분석 프롬프트를 `analysis_sections`로 갱신

**Files:**
- Modify: `app/services/analyzer.py:30-63` (`DEFAULT_ANALYSIS_PROMPT`)

- [ ] **Step 1: 출력 형식 JSON 교체**

`DEFAULT_ANALYSIS_PROMPT`의 출력 JSON 예시에서 `"full_analysis_md": "string",`를 다음으로 교체:

```json
  "analysis_sections": [
    {"key": "string(영문 스네이크케이스)", "title": "string(한국어 제목)", "bullets": ["string"]}
  ],
```

그리고 "## 분석 요청 항목"의 전체 분석 설명을 다음으로 교체:

```
- 전체 분석(analysis_sections): 섹션 배열로 작성. 각 섹션은 {key, title, bullets}.
  bullets는 기호(•,-,번호)나 줄바꿈 없이 한 문장씩 담은 문자열 배열. 강조는 인라인 **굵게**만 사용.
  줄바꿈·불릿은 화면이 자동 처리하므로 텍스트에 넣지 말 것.
```

- [ ] **Step 2: PROMPT_VERSION 증가**

`app/services/analyzer.py:28`의 `PROMPT_VERSION = "v3.0"`을 `"v4.0"`으로 변경.

- [ ] **Step 3: import 검증**

Run: `python -c "import app.services.analyzer; print(app.services.analyzer.PROMPT_VERSION)"`
Expected: `v4.0`.

- [ ] **Step 4: Commit**

```bash
git add app/services/analyzer.py
git commit -m "feat: 기본 프롬프트를 analysis_sections 구조화 출력으로 갱신(v4.0)"
```

---

## Self-Review 결과 (스펙 대비)

- **① 데이터 모델**: Task 1·2·3(컬럼/저장/스키마), Task 16(프롬프트) — 커버.
- **② 공유 링크**: Task 9(토큰컬럼)·11(베이스URL)·12(HTML)·13(SSR) — 커버.
- **③ 렌더링 파이프라인**: Task 4(뷰모델)·5·6(텔레그램)·8(웹) — 커버.
- **레거시 폴백**: Task 4(격리)·6(텔레그램)·8(웹 remark-breaks)·12(SSR pre) — 커버.
- **텔레그램 간소화+링크**: Task 14 — 커버.
- **프롬프트 가이드**: Task 15 — 커버.
- **마이그레이션 안전성**: Task 1·9 additive — 커버.

**주의(실행 시 확인 필요한 결합점):**
- 프론트 분석 필드 접근 경로(`video.analysis_sections` vs `video.analysis?.analysis_sections`):
  `frontend/src/api/videos.ts` 매핑 확인 후 Task 7·8에서 일치시킬 것.
- `deps.py`의 제어세션 팩토리 정확한 이름: Task 13에서 기존 `get_group_or_404` 구현 참고.
- `notify_service`의 `group_slug` 전파 경로: Task 14에서 `notify_video`→`build_message`→`_build_*`
  시그니처를 일관되게 연결할 것. 호출처(`app/routers/videos.py`의 notify, `monitor_service`의
  `_notify_after_analysis`, `notify_pending_batch`)에 slug 전달 추가 필요.
- `default_settings.py`의 value_type 표기 규칙 확인 후 Task 14 Step 2 적용.
