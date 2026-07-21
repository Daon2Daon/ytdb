# Phase 1: Digest 섹션 빌더 + 그룹 프로필 부트스트랩 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Digest 산출물을 순서 조정 가능한 구조화 섹션으로 만들고, 그룹 생성 시 LLM이 그룹에 맞는 페르소나·섹션 구성을 자동 생성해 프롬프트를 쓰지 않는 사용자도 카테고리에 맞는 digest를 받게 한다.

**Architecture:** 알림의 "순서 배열 + 렌더러 레지스트리" 패턴을 digest에 이식한다. 섹션은 `{key, kind, title, guide/body_md}`이고 `kind`는 `llm`(LLM이 body_md 생성) 또는 `computed`(집계에서 data 생성). `digest_prompt`가 비어 있으면 structured 모드(2층 프롬프트 조립), 비어 있지 않으면 현행 custom 모드로 자동 분기 — 기존 파워유저 경로 무변경. 산출물은 `digests.digest_sections`(jsonb)에 저장하고 `summary_md`는 파생물로 함께 저장해 하위 호환.

**Tech Stack:** FastAPI, SQLAlchemy(async, schema_translate_map 멀티테넌트), Pydantic, pytest(asyncio auto), React + TypeScript + Vite + Vitest.

---

## 아키텍처 노트 (구현 전 필독)

- **멀티테넌트 DDL**: 데이터 평면 테이블은 `app/models/pg/*`에 정의하고 `db_engine.ensure_schema`가 그룹 스키마에 멱등 생성한다. **기존 테이블에 컬럼 추가는 create_all이 못 하므로** `ensure_schema`의 `additive_columns` 리스트에 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`로 명시해야 한다 ([db_engine.py:181](app/services/db_engine.py)).
- **설정 저장**: 그룹 설정은 `app.settings`(control DB)에 `(group_id, category, key, value, value_type)` 행으로 저장. `SettingsManager.get_typed(group_id, category)`가 `{key: coerced_value}` dict를 준다. dataclass는 `app/services/settings_types.py`, 빌더는 `SettingsManager.get_*`.
- **순수 함수 우선 TDD**: 이 프로젝트 테스트는 대부분 DB 없이 도는 순수 함수 테스트다(`tests/test_digest_configs.py` 참고, `conftest.py`는 asyncio auto만 제공). LLM·DB를 타는 로직은 **순수 조립 함수로 분리**해 단위 테스트하고, 통합부는 monkeypatch로 얇게 검증한다.
- **LLM 호출**: `LiteLLMClient(ai_settings).chat(model, messages, temperature, max_tokens, response_format={"type":"json_object"})` → `.content`(str), `.input_tokens`, `.output_tokens`. 모델은 `resolve_ai_gateway(group_id)`가 전역 폴백 포함해 준다.
- **원장·예산**: LLM 호출 후 `record_usage(user_id, group_id, purpose, model, input_tokens, output_tokens)`. 그룹 예산 게이트는 `budget_ok_for_group(group)` → `(ok: bool, reason: str)`.
- **커밋**: 각 Task 끝에서 커밋. 커밋 메시지 말미에 아래 trailer를 포함한다.
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  ```
- **테스트 실행**: 백엔드 `pytest tests/<file> -v`, 프론트 `cd frontend && npx vitest run <file>`.

---

## 파일 구조

**생성**
- `app/services/digest_sections.py` — 섹션 레지스트리, 중립 기본값, 정규화/검증, computed 데이터 빌드, structured 프롬프트 조립·파싱, sections→markdown. (순수 함수 모음)
- `app/services/group_profile.py` — `GroupProfile` dataclass + `parse_profile(dict)` (순수).
- `app/services/bootstrap_service.py` — 그룹 프로필 LLM 부트스트랩(통합).
- `app/services/digest_view.py` — digest 산출물 정규 뷰모델(`build_digest_sections`).
- `app/routers/profile.py` — `GET/POST /api/groups/{slug}/profile`.
- `frontend/src/components/OrderedItemsBuilder.tsx` — 추가/제외/순서 UI 일반화(알림·digest 공유).
- `frontend/src/components/DigestSectionBuilder.tsx` — 섹션 편집 UI.
- 각 백엔드 파일에 대응하는 `tests/test_*.py`, 프론트 `*.test.ts(x)`.

**수정**
- `app/services/settings_types.py` — `DigestScheduleConfig.sections` 필드 추가.
- `app/services/digest_config.py` — sections 왕복·검증.
- `app/services/settings_manager.py` — `get_profile()` 추가.
- `app/services/digest_service.py` — structured/custom 모드 분기, sections 저장, summary_md 파생.
- `app/services/default_settings.py` — digest 시드를 config 1건으로.
- `app/models/pg/digest.py` — `digest_sections` 컬럼.
- `app/services/db_engine.py` — additive 컬럼 등록.
- `app/schemas/digest.py` — `DigestOut.digest_sections`.
- `app/routers/digests.py` 또는 `main.py` — profile 라우터 등록.
- `app/routers/groups.py` — create_group 백그라운드 부트스트랩.
- `frontend/src/api/types.ts` — `DigestScheduleConfig.sections`, `Digest.digest_sections`.
- `frontend/src/components/DigestConfigsEditor.tsx` — 섹션 빌더 + 고급 프롬프트 접힘.
- `frontend/src/pages/DigestDetail.tsx` — 섹션 렌더.
- `frontend/src/components/TemplateBuilder.tsx` — OrderedItemsBuilder 기반으로 리팩터.

---

## Task 1: 섹션 레지스트리 · 중립 기본값 · 정규화

**Files:**
- Create: `app/services/digest_sections.py`
- Test: `tests/test_digest_sections.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_digest_sections.py
"""Digest 섹션 레지스트리·정규화 단위 테스트."""

from __future__ import annotations

from app.services.digest_sections import (
    COMPUTED_SECTIONS,
    DEFAULT_DIGEST_SECTIONS,
    SECTION_KIND_COMPUTED,
    SECTION_KIND_LLM,
    normalize_sections,
    resolve_sections,
)


def test_default_sections_are_valid_and_neutral():
    # 중립 기본값: 투자 전용 키('주목할 종목·이슈')가 없어야 한다.
    keys = [s["key"] for s in DEFAULT_DIGEST_SECTIONS]
    assert "overview" in keys
    assert all(s["kind"] in (SECTION_KIND_LLM, SECTION_KIND_COMPUTED) for s in DEFAULT_DIGEST_SECTIONS)
    # computed 섹션 key는 레지스트리에 존재해야 한다.
    for s in DEFAULT_DIGEST_SECTIONS:
        if s["kind"] == SECTION_KIND_COMPUTED:
            assert s["key"] in COMPUTED_SECTIONS


def test_normalize_drops_invalid_kind_and_unknown_computed():
    raw = [
        {"key": "overview", "kind": "llm", "title": "요약", "guide": "핵심"},
        {"key": "bogus", "kind": "weird", "title": "x"},          # 잘못된 kind → drop
        {"key": "not_a_real_computed", "kind": "computed", "title": "y"},  # 미등록 computed → drop
        {"key": "top_tags", "kind": "computed", "title": "태그"},  # 유효
    ]
    out = normalize_sections(raw)
    assert [s["key"] for s in out] == ["overview", "top_tags"]
    assert out[0]["guide"] == "핵심"


def test_normalize_enforces_cap_and_defaults_title():
    raw = [{"key": f"s{i}", "kind": "llm"} for i in range(20)]
    out = normalize_sections(raw)
    assert len(out) == 12  # 상한
    assert out[0]["title"]  # 제목 누락 시 기본 채움


def test_resolve_sections_falls_back():
    # cfg 섹션 → profile 섹션 → 중립 기본값 순
    assert resolve_sections([], []) == DEFAULT_DIGEST_SECTIONS
    prof = [{"key": "overview", "kind": "llm", "title": "P"}]
    assert resolve_sections([], prof) == normalize_sections(prof)
    cfg = [{"key": "insights", "kind": "llm", "title": "C"}]
    assert resolve_sections(cfg, prof) == normalize_sections(cfg)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_digest_sections.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.digest_sections`

- [ ] **Step 3: Write minimal implementation**

```python
# app/services/digest_sections.py
"""Digest 섹션 레지스트리·정규화·조립 (순수 함수).

섹션 형식: {"key": str, "kind": "llm"|"computed", "title": str, "guide": str}
- llm:      LLM이 body_md(markdown)를 생성.
- computed: 집계에서 data(dict)를 생성 (LLM 불필요).
설정(config)과 산출물(digest_sections)이 같은 key/kind/title을 공유한다.
"""

from __future__ import annotations

from typing import Any

SECTION_KIND_LLM = "llm"
SECTION_KIND_COMPUTED = "computed"

MAX_SECTIONS = 12
_MAX_GUIDE_LEN = 300

# computed 섹션 레지스트리: key -> 기본 제목. 데이터는 build_computed_data가 만든다.
COMPUTED_SECTIONS: dict[str, str] = {
    "stats_overview": "이번 기간 개요",
    "sentiment_breakdown": "평가 분포",
    "top_tags": "주요 태그",
    "top_channels": "주요 채널",
    "top_viewed": "조회수 상위",
}

# 카테고리 중립 기본 세트(부트스트랩 실패·프로필 부재 시 폴백).
DEFAULT_DIGEST_SECTIONS: list[dict[str, Any]] = [
    {"key": "overview", "kind": SECTION_KIND_LLM, "title": "핵심 요약",
     "guide": "이번 기간을 가로지르는 3~5개 핵심 흐름을 개조식으로 서술"},
    {"key": "perspectives", "kind": SECTION_KIND_LLM, "title": "관점 비교",
     "guide": "합의된 관점과 엇갈리는 관점을 구분해 대비"},
    {"key": "insights", "kind": SECTION_KIND_LLM, "title": "핵심 인사이트",
     "guide": "시청자가 실제 판단에 쓸 수 있는 구체적 인사이트"},
    {"key": "top_viewed", "kind": SECTION_KIND_COMPUTED, "title": "조회수 상위"},
    {"key": "top_tags", "kind": SECTION_KIND_COMPUTED, "title": "주요 태그"},
]


def _clean(v: Any) -> str:
    return str(v).strip() if v is not None else ""


def normalize_sections(raw: Any) -> list[dict[str, Any]]:
    """외부 입력을 검증된 섹션 배열로. 불량 항목은 drop, 상한 적용."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = _clean(item.get("key"))
        kind = _clean(item.get("kind"))
        if not key or kind not in (SECTION_KIND_LLM, SECTION_KIND_COMPUTED):
            continue
        if kind == SECTION_KIND_COMPUTED and key not in COMPUTED_SECTIONS:
            continue
        title = _clean(item.get("title"))
        if not title:
            title = COMPUTED_SECTIONS.get(key, key) if kind == SECTION_KIND_COMPUTED else key
        section: dict[str, Any] = {"key": key, "kind": kind, "title": title}
        guide = _clean(item.get("guide"))[:_MAX_GUIDE_LEN]
        if kind == SECTION_KIND_LLM and guide:
            section["guide"] = guide
        out.append(section)
        if len(out) >= MAX_SECTIONS:
            break
    return out


def resolve_sections(
    cfg_sections: Any, profile_sections: Any
) -> list[dict[str, Any]]:
    """설정 섹션 우선, 없으면 프로필 섹션, 그것도 없으면 중립 기본값."""
    cfg = normalize_sections(cfg_sections)
    if cfg:
        return cfg
    prof = normalize_sections(profile_sections)
    if prof:
        return prof
    return DEFAULT_DIGEST_SECTIONS
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_digest_sections.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add app/services/digest_sections.py tests/test_digest_sections.py
git commit -m "feat: digest 섹션 레지스트리·중립 기본값·정규화

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 2: computed 데이터 빌드 · sections→markdown

**Files:**
- Modify: `app/services/digest_sections.py`
- Test: `tests/test_digest_sections.py`

- [ ] **Step 1: Write the failing test (append to file)**

```python
# tests/test_digest_sections.py 에 추가
from app.services.digest_sections import build_computed_data, sections_to_markdown


class _FakeAgg:
    video_count = 3
    sentiment_breakdown = {"긍정": 2, "부정": 1}
    top_tags = [{"name": "AI", "count": 5}, {"name": "5G", "count": 2}]
    top_channels = [{"name": "KT", "count": 4}]

    class _V:
        def __init__(self, ch, head, views):
            self.channel_name = ch; self.headline = head; self.one_line = None
            self.title = None; self.view_count = views
    videos = [_V("AT&T", "네트워크 보증", 12648000), _V("KT", "AI 팝업", 5000)]


def test_build_computed_data_top_tags():
    data = build_computed_data("top_tags", _FakeAgg())
    assert data["items"][0]["name"] == "AI"


def test_build_computed_data_stats_overview():
    data = build_computed_data("stats_overview", _FakeAgg())
    assert data["video_count"] == 3


def test_build_computed_data_top_viewed_sorted():
    data = build_computed_data("top_viewed", _FakeAgg())
    assert data["items"][0]["channel"] == "AT&T"  # 조회수 큰 순


def test_build_computed_data_unknown_key_empty():
    assert build_computed_data("nope", _FakeAgg()) == {}


def test_sections_to_markdown_renders_llm_and_computed():
    sections = [
        {"key": "overview", "kind": "llm", "title": "핵심 요약", "body_md": "- 흐름 A\n- 흐름 B"},
        {"key": "top_tags", "kind": "computed", "title": "주요 태그",
         "data": {"items": [{"name": "AI", "count": 5}]}},
    ]
    md = sections_to_markdown(sections)
    assert "## 핵심 요약" in md
    assert "흐름 A" in md
    assert "## 주요 태그" in md
    assert "AI" in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_digest_sections.py -k "computed or markdown" -v`
Expected: FAIL — `ImportError: cannot import name 'build_computed_data'`

- [ ] **Step 3: Write minimal implementation (append to digest_sections.py)**

```python
def _fmt_views(n: Any) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    if n >= 10000:
        return f"{n / 10000:.1f}만"
    if n >= 1000:
        return f"{n / 1000:.1f}천"
    return str(n)


def build_computed_data(key: str, agg: Any) -> dict[str, Any]:
    """computed 섹션의 표시용 data(dict). 미등록 key는 빈 dict."""
    if key == "stats_overview":
        return {"video_count": getattr(agg, "video_count", 0)}
    if key == "sentiment_breakdown":
        return {"breakdown": dict(getattr(agg, "sentiment_breakdown", {}) or {})}
    if key == "top_tags":
        return {"items": list(getattr(agg, "top_tags", []) or [])[:20]}
    if key == "top_channels":
        return {"items": list(getattr(agg, "top_channels", []) or [])[:10]}
    if key == "top_viewed":
        vids = [v for v in getattr(agg, "videos", []) or [] if getattr(v, "view_count", 0)]
        vids.sort(key=lambda v: v.view_count or 0, reverse=True)
        items = []
        for v in vids[:6]:
            head = (getattr(v, "headline", None) or getattr(v, "one_line", None)
                    or getattr(v, "title", None) or "").strip()
            items.append({"channel": getattr(v, "channel_name", ""), "head": head,
                          "views": v.view_count})
        return {"items": items}
    return {}


def _computed_to_markdown(section: dict[str, Any]) -> str:
    key = section.get("key")
    data = section.get("data") or {}
    lines: list[str] = []
    if key == "stats_overview":
        lines.append(f"- 분석 영상 {data.get('video_count', 0)}건")
    elif key == "sentiment_breakdown":
        for k, v in (data.get("breakdown") or {}).items():
            lines.append(f"- {k}: {v}")
    elif key in ("top_tags", "top_channels"):
        for it in data.get("items") or []:
            lines.append(f"- {it.get('name')} ({it.get('count')})")
    elif key == "top_viewed":
        for it in data.get("items") or []:
            views = _fmt_views(it.get("views"))
            suffix = f" · 조회 {views}" if views else ""
            lines.append(f"- [{it.get('channel')}] {it.get('head')}{suffix}")
    return "\n".join(lines)


def sections_to_markdown(sections: list[dict[str, Any]]) -> str:
    """산출 섹션 배열(body_md/data 포함) → 단일 마크다운. summary_md·공유페이지·폴백용."""
    blocks: list[str] = []
    for s in sections:
        title = _clean(s.get("title"))
        header = f"## {title}" if title else ""
        if s.get("kind") == SECTION_KIND_LLM:
            body = _clean(s.get("body_md"))
        else:
            body = _computed_to_markdown(s)
        if not body:
            continue
        blocks.append(f"{header}\n{body}".strip())
    return "\n\n".join(blocks)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_digest_sections.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add app/services/digest_sections.py tests/test_digest_sections.py
git commit -m "feat: computed 섹션 데이터 빌드·sections→markdown

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 3: structured 프롬프트 조립 · 응답 파싱 · 섹션 병합

**Files:**
- Modify: `app/services/digest_sections.py`
- Test: `tests/test_digest_sections.py`

- [ ] **Step 1: Write the failing test (append)**

```python
# tests/test_digest_sections.py 에 추가
from app.services.digest_sections import (
    assemble_output_sections,
    build_structured_prompt,
    parse_structured_response,
)


def test_build_structured_prompt_includes_persona_and_llm_keys():
    sections = [
        {"key": "overview", "kind": "llm", "title": "핵심 요약", "guide": "핵심 흐름"},
        {"key": "top_tags", "kind": "computed", "title": "주요 태그"},
    ]
    prompt = build_structured_prompt(
        persona="지식 큐레이터다.", data_block="영상: 3건", sections=sections
    )
    assert "지식 큐레이터다." in prompt
    assert "영상: 3건" in prompt
    assert "overview" in prompt          # llm 섹션 key가 출력 스키마에 포함
    assert "핵심 흐름" in prompt          # guide 포함
    assert "top_tags" not in prompt.split("출력")[-1]  # computed는 LLM에 요청 안 함


def test_parse_structured_response_maps_requested_keys():
    raw = '{"headline":"H","sections":[{"key":"overview","body_md":"본문"},' \
          '{"key":"unknown","body_md":"무시"}],"telegram_summary":"T"}'
    headline, bodies, tg = parse_structured_response(raw, requested_keys=["overview"])
    assert headline == "H"
    assert bodies == {"overview": "본문"}   # 요청 key만 채택
    assert tg == "T"


def test_parse_structured_response_bad_json():
    headline, bodies, tg = parse_structured_response("not json", requested_keys=["overview"])
    assert headline == "" and bodies == {} and tg == ""


def test_assemble_output_sections_merges_bodies_and_data():
    sections = [
        {"key": "overview", "kind": "llm", "title": "핵심 요약", "guide": "x"},
        {"key": "top_tags", "kind": "computed", "title": "주요 태그"},
    ]
    out = assemble_output_sections(
        sections, llm_bodies={"overview": "본문"}, agg=_FakeAgg()
    )
    assert out[0] == {"key": "overview", "kind": "llm", "title": "핵심 요약", "body_md": "본문"}
    assert out[1]["kind"] == "computed"
    assert out[1]["data"]["items"][0]["name"] == "AI"


def test_assemble_skips_llm_section_with_no_body():
    sections = [{"key": "overview", "kind": "llm", "title": "요약", "guide": "x"}]
    out = assemble_output_sections(sections, llm_bodies={}, agg=_FakeAgg())
    assert out == []  # body 없으면 건너뜀
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_digest_sections.py -k "structured or assemble" -v`
Expected: FAIL — `ImportError: cannot import name 'build_structured_prompt'`

- [ ] **Step 3: Write minimal implementation (append)**

```python
import json


def build_structured_prompt(
    *, persona: str, data_block: str, sections: list[dict[str, Any]]
) -> str:
    """페르소나(1층) + 데이터 블록 + llm 섹션 출력 스키마(2층)로 프롬프트 조립."""
    persona = persona.strip() or "너는 유튜브 콘텐츠를 종합하는 애널리스트다."
    llm_sections = [s for s in sections if s.get("kind") == SECTION_KIND_LLM]
    schema_lines = []
    for s in llm_sections:
        guide = _clean(s.get("guide")) or s.get("title") or s.get("key")
        schema_lines.append(f'    {{"key": "{s["key"]}", "body_md": "<{guide}>"}}')
    sections_schema = ",\n".join(schema_lines)
    return f"""{persona}

아래 자료를 바탕으로 이번 기간을 한국어 개조식('~함','~임')으로 종합하라.
개별 영상 나열이 아니라 여러 영상에 걸친 흐름을 묶어 서술할 것.

## 자료
{data_block}

## 출력 형식
반드시 아래 JSON으로만 출력. sections 배열은 지정된 key를 순서대로 포함:
{{
  "headline": "<이모지 1~2개 포함, 이번 기간 핵심 한 줄(40자 이내)>",
  "sections": [
{sections_schema}
  ],
  "telegram_summary": "<텔레그램용 400자 이내 일반 텍스트 브리핑>"
}}"""


def parse_structured_response(
    raw: str, *, requested_keys: list[str]
) -> tuple[str, dict[str, str], str]:
    """LLM JSON 응답 → (headline, {key: body_md}, telegram_summary). 실패 시 빈 값."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return "", {}, ""
    if not isinstance(data, dict):
        return "", {}, ""
    wanted = set(requested_keys)
    bodies: dict[str, str] = {}
    for item in data.get("sections") or []:
        if not isinstance(item, dict):
            continue
        key = _clean(item.get("key"))
        body = _clean(item.get("body_md"))
        if key in wanted and body:
            bodies[key] = body
    return _clean(data.get("headline")), bodies, _clean(data.get("telegram_summary"))


def assemble_output_sections(
    sections: list[dict[str, Any]], *, llm_bodies: dict[str, str], agg: Any
) -> list[dict[str, Any]]:
    """설정 섹션 순서대로 산출 섹션 배열 생성. llm은 body_md, computed는 data."""
    out: list[dict[str, Any]] = []
    for s in sections:
        base = {"key": s["key"], "kind": s["kind"], "title": s.get("title", "")}
        if s["kind"] == SECTION_KIND_LLM:
            body = llm_bodies.get(s["key"], "")
            if not body:
                continue
            out.append({**base, "body_md": body})
        else:
            out.append({**base, "data": build_computed_data(s["key"], agg)})
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_digest_sections.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add app/services/digest_sections.py tests/test_digest_sections.py
git commit -m "feat: structured digest 프롬프트 조립·응답 파싱·섹션 병합

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 4: DigestScheduleConfig.sections 필드 + 왕복 직렬화

**Files:**
- Modify: `app/services/settings_types.py:134` (DigestScheduleConfig)
- Modify: `app/services/digest_config.py`
- Test: `tests/test_digest_configs.py`

- [ ] **Step 1: Write the failing test (append to tests/test_digest_configs.py)**

```python
# tests/test_digest_configs.py 에 추가
from app.services.digest_config import configs_to_json, normalize_schedule_config


def test_schedule_config_roundtrips_sections():
    raw = {
        "name": "주간",
        "period_days": 7,
        "sections": [
            {"key": "overview", "kind": "llm", "title": "요약", "guide": "핵심"},
            {"key": "top_tags", "kind": "computed", "title": "태그"},
            {"key": "bad", "kind": "weird"},  # drop 대상
        ],
    }
    cfg = normalize_schedule_config(raw, index=0)
    assert [s["key"] for s in cfg.sections] == ["overview", "top_tags"]
    js = configs_to_json([cfg])
    assert js[0]["sections"][0]["key"] == "overview"


def test_schedule_config_defaults_sections_empty():
    cfg = normalize_schedule_config({"name": "x"}, index=0)
    assert cfg.sections == []  # 비어 있으면 프로필/기본값으로 폴백(런타임)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_digest_configs.py -k sections -v`
Expected: FAIL — `AttributeError: 'DigestScheduleConfig' object has no attribute 'sections'`

- [ ] **Step 3: Write implementation**

In `app/services/settings_types.py`, add field to `DigestScheduleConfig` (after `telegram_enabled`):

```python
    telegram_enabled: bool = False
    sections: list[dict] = field(default_factory=list)  # 비면 프로필→중립 기본값 폴백(런타임)
```

In `app/services/digest_config.py`, add import at top:

```python
from app.services.digest_sections import normalize_sections
```

In `normalize_schedule_config`, add before the `return DigestScheduleConfig(`:

```python
    sections = normalize_sections(raw.get("sections"))
```

and add `sections=sections,` to the `DigestScheduleConfig(...)` constructor call.

In `configs_to_json`, add `"sections": c.sections,` inside the dict for each config.

In `legacy_flat_to_config`, no change needed (sections defaults to `[]`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_digest_configs.py -v`
Expected: PASS (all, including existing)

- [ ] **Step 5: Commit**

```bash
git add app/services/settings_types.py app/services/digest_config.py tests/test_digest_configs.py
git commit -m "feat: DigestScheduleConfig에 sections 필드·왕복 직렬화

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 5: GroupProfile dataclass + parse + settings_manager.get_profile

**Files:**
- Create: `app/services/group_profile.py`
- Modify: `app/services/settings_manager.py` (add `get_profile`)
- Test: `tests/test_group_profile.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_group_profile.py
"""그룹 프로필 파싱 단위 테스트."""

from __future__ import annotations

from app.services.group_profile import GroupProfile, parse_profile


def test_parse_profile_empty_gives_defaults():
    p = parse_profile({})
    assert p.persona == ""
    assert p.digest_sections == []
    assert p.bootstrap_status == "none"


def test_parse_profile_reads_fields():
    p = parse_profile({
        "persona": "지식 큐레이터다.",
        "digest_sections": [{"key": "overview", "kind": "llm", "title": "요약"}],
        "bootstrap_status": "done",
        "bootstrap_at": "2026-07-21T00:00:00+00:00",
    })
    assert p.persona == "지식 큐레이터다."
    assert p.digest_sections[0]["key"] == "overview"
    assert p.bootstrap_status == "done"


def test_parse_profile_drops_invalid_sections():
    p = parse_profile({"digest_sections": [{"key": "x", "kind": "bad"}]})
    assert p.digest_sections == []  # normalize_sections가 걸러냄
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_group_profile.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.group_profile`

- [ ] **Step 3: Write implementation**

```python
# app/services/group_profile.py
"""그룹 프로필(app.settings category='profile') 표현·파싱 (순수)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.digest_sections import normalize_sections


@dataclass
class GroupProfile:
    persona: str = ""
    digest_sections: list[dict] = field(default_factory=list)
    bootstrap_status: str = "none"   # none | done | failed
    bootstrap_at: str = ""


def parse_profile(d: dict[str, Any]) -> GroupProfile:
    return GroupProfile(
        persona=str(d.get("persona") or "").strip(),
        digest_sections=normalize_sections(d.get("digest_sections")),
        bootstrap_status=str(d.get("bootstrap_status") or "none").strip() or "none",
        bootstrap_at=str(d.get("bootstrap_at") or "").strip(),
    )
```

In `app/services/settings_manager.py`, add import near other settings_types imports:

```python
from app.services.group_profile import GroupProfile, parse_profile
```

Add method after `get_prompts` (around line 210):

```python
    async def get_profile(self, group_id: int) -> GroupProfile:
        d = await self.get_typed(group_id, "profile")
        return parse_profile(d)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_group_profile.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add app/services/group_profile.py app/services/settings_manager.py tests/test_group_profile.py
git commit -m "feat: GroupProfile 파싱·settings_manager.get_profile

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 6: bootstrap_service (프로필 LLM 부트스트랩)

**Files:**
- Create: `app/services/bootstrap_service.py`
- Test: `tests/test_bootstrap_service.py`

부트스트랩은 LLM·DB를 타므로, **응답 정규화 순수 함수**를 분리해 그것을 단위 테스트하고, 통합 함수는 monkeypatch로 얇게 검증한다.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bootstrap_service.py
"""그룹 프로필 부트스트랩 테스트."""

from __future__ import annotations

import pytest

from app.services.bootstrap_service import normalize_bootstrap_output


def test_normalize_bootstrap_output_valid():
    raw = (
        '{"persona":"지식 큐레이터다.",'
        '"digest_sections":[{"key":"overview","kind":"llm","title":"요약","guide":"핵심"},'
        '{"key":"top_tags","kind":"computed","title":"태그"}]}'
    )
    persona, sections = normalize_bootstrap_output(raw)
    assert persona == "지식 큐레이터다."
    assert [s["key"] for s in sections] == ["overview", "top_tags"]


def test_normalize_bootstrap_output_too_few_sections_uses_default():
    # 섹션 2개 미만이면 부실 응답 → 중립 기본값 사용
    raw = '{"persona":"P","digest_sections":[{"key":"overview","kind":"llm","title":"요약"}]}'
    persona, sections = normalize_bootstrap_output(raw)
    assert persona == "P"
    assert len(sections) >= 2  # DEFAULT_DIGEST_SECTIONS로 대체


def test_normalize_bootstrap_output_bad_json_raises():
    with pytest.raises(ValueError):
        normalize_bootstrap_output("not json")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bootstrap_service.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.bootstrap_service`

- [ ] **Step 3: Write implementation**

```python
# app/services/bootstrap_service.py
"""그룹 생성 시 LLM으로 프로필(persona + digest 섹션)을 자동 생성한다.

프롬프트를 쓰지 않는 사용자를 위해 그룹 이름·카테고리·채널로 카테고리에 맞는
digest 구성을 시드한다. 실패 시 중립 기본값으로 조용히 폴백한다(현행 대비 무열화).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.models.control.group import Group
from app.services.ai_usage_service import budget_ok_for_group, record_usage
from app.services.digest_sections import DEFAULT_DIGEST_SECTIONS, normalize_sections
from app.services.global_settings import resolve_ai_gateway
from app.services.llm_client import LiteLLMClient
from app.services.settings_manager import get_settings_manager

_BOOTSTRAP_PROMPT = """너는 유튜브 모니터링 그룹의 요약 리포트를 설계하는 어시스턴트다.
아래 그룹에 맞는 (1) 리포트 작성자 페르소나 한 문장과 (2) 주간 리포트 섹션 4~6개를 제안하라.

## 그룹 정보
- 이름: {name}
- 설명: {description}
- 등록 채널(일부): {channels}

## 섹션 규칙
- kind는 'llm'(LLM이 서술) 또는 'computed'(집계 자동) 중 하나.
- computed는 다음 key만 허용: top_tags, top_channels, top_viewed, sentiment_breakdown, stats_overview.
- llm 섹션의 key는 영문 스네이크케이스, guide는 한 줄 작성 지침.
- 이 그룹 주제에 맞게. 투자 전용 표현('종목') 강요 금지.

## 출력 형식 (JSON만)
{{
  "persona": "<이 리포트를 쓰는 애널리스트를 한 문장으로>",
  "digest_sections": [
    {{"key": "overview", "kind": "llm", "title": "핵심 요약", "guide": "..."}},
    {{"key": "top_viewed", "kind": "computed", "title": "조회수 상위"}}
  ]
}}"""


def normalize_bootstrap_output(raw: str) -> tuple[str, list[dict]]:
    """LLM 응답 → (persona, sections). 섹션 2개 미만이면 중립 기본값. 불량 JSON은 ValueError."""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("bootstrap 응답이 객체가 아님")
    persona = str(data.get("persona") or "").strip()
    sections = normalize_sections(data.get("digest_sections"))
    if len(sections) < 2:
        sections = DEFAULT_DIGEST_SECTIONS
    return persona, sections


async def _channel_names(group: Group, limit: int = 20) -> list[str]:
    """그룹 데이터 평면에서 등록 채널명 최대 limit개. 실패 시 빈 목록."""
    from sqlalchemy import select
    from app.models.pg.channel import Channel
    from app.services.db_engine import data_plane_engine_manager as dpm

    try:
        async with dpm.group_session(group) as session:
            rows = await session.execute(select(Channel.channel_name).limit(limit))
            return [r[0] for r in rows.all() if r[0]]
    except Exception:
        return []


async def bootstrap_profile(group: Group, *, force: bool = False) -> None:
    """프로필을 생성해 app.settings category='profile'에 저장. 실패는 status만 기록."""
    mgr = get_settings_manager()
    if not force:
        existing = await mgr.get_profile(group.group_id)
        if existing.bootstrap_status == "done":
            return

    ok, _reason = await budget_ok_for_group(group)
    if not ok:
        await _save_status(group.group_id, "failed")
        return

    channels = await _channel_names(group)
    prompt = _BOOTSTRAP_PROMPT.format(
        name=group.name or group.slug,
        description=group.description or "(설명 없음)",
        channels=", ".join(channels) if channels else "(아직 없음)",
    )
    ai = await resolve_ai_gateway(group.group_id)
    model = ai.digest_model or ai.primary_model
    client = LiteLLMClient(ai)
    try:
        chat = await client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=min(ai.max_tokens or 2048, 2048),
            response_format={"type": "json_object"},
        )
        await record_usage(
            user_id=group.owner_user_id, group_id=group.group_id,
            purpose="bootstrap", model=model,
            input_tokens=chat.input_tokens, output_tokens=chat.output_tokens,
        )
        persona, sections = normalize_bootstrap_output(chat.content)
        await mgr.set_values(group.group_id, "profile", [
            {"key": "persona", "value": persona, "value_type": "string"},
            {"key": "digest_sections", "value": json.dumps(sections, ensure_ascii=False),
             "value_type": "json"},
            {"key": "bootstrap_status", "value": "done", "value_type": "string"},
            {"key": "bootstrap_at", "value": datetime.now(timezone.utc).isoformat(),
             "value_type": "string"},
        ])
    except Exception as e:  # noqa: BLE001 — 부트스트랩 실패는 그룹 동작을 막지 않는다
        print(f"[bootstrap] {group.slug} 실패: {e}")
        await _save_status(group.group_id, "failed")
    finally:
        await client.aclose()


async def _save_status(group_id: int, status: str) -> None:
    await get_settings_manager().set_values(group_id, "profile", [
        {"key": "bootstrap_status", "value": status, "value_type": "string"},
    ])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_bootstrap_service.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add app/services/bootstrap_service.py tests/test_bootstrap_service.py
git commit -m "feat: 그룹 프로필 LLM 부트스트랩 서비스

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 7: digests.digest_sections 컬럼 + additive 마이그레이션 + 스키마/타입

**Files:**
- Modify: `app/models/pg/digest.py`
- Modify: `app/services/db_engine.py` (additive_columns)
- Modify: `app/schemas/digest.py` (DigestOut)
- Test: `tests/test_digest_model_sections.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_digest_model_sections.py
"""digest 모델·스키마 sections 컬럼 존재 확인."""

from __future__ import annotations

from app.models.pg.digest import Digest
from app.schemas.digest import DigestOut


def test_digest_model_has_digest_sections_column():
    assert "digest_sections" in Digest.__table__.columns


def test_digest_out_has_digest_sections_field():
    assert "digest_sections" in DigestOut.model_fields
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_digest_model_sections.py -v`
Expected: FAIL — `AssertionError` (컬럼/필드 없음)

- [ ] **Step 3: Write implementation**

In `app/models/pg/digest.py`, after the `top_channels` column (line ~40):

```python
    top_channels: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    digest_sections: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
```

In `app/services/db_engine.py`, add to the `additive_columns` list (after the digests entries ~line 191):

```python
                    ("digests", "config_name", "text"),
                    ("digests", "digest_sections", "jsonb"),
```

In `app/schemas/digest.py`, add to `DigestOut` after `top_channels`:

```python
    top_channels: Optional[Any]
    digest_sections: Optional[Any] = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_digest_model_sections.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add app/models/pg/digest.py app/services/db_engine.py app/schemas/digest.py tests/test_digest_model_sections.py
git commit -m "feat: digests.digest_sections 컬럼·additive 마이그레이션·DigestOut

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 8: digest_service 모드 분기 + 구조화 생성·저장

**Files:**
- Modify: `app/services/digest_service.py`
- Test: `tests/test_digest_structured.py`

`DigestGenerated`에 `sections` 필드를 추가하고, `generate_digest_for_group`이 `cfg.digest_prompt` 유무로 custom/structured를 분기한다. 순수 조립은 Task 1~3에서 검증됐으므로, 여기서는 **분기·저장·폴백**을 monkeypatch로 검증한다.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_digest_structured.py
"""digest structured 모드 생성·모드 분기 테스트."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services import digest_service
from app.services.digest_service import DigestAggregate, DigestGenerated, synthesize_with_llm


def _agg():
    return DigestAggregate(
        video_count=2, sentiment_breakdown={"긍정": 2},
        top_tags=[{"name": "AI", "count": 3}], top_channels=[{"name": "KT", "count": 2}],
        videos=[],
    )


@pytest.mark.anyio
async def test_synthesize_structured_builds_sections(monkeypatch):
    # LLM·게이트웨이·프로필·프리셋을 가짜로 대체
    class _Chat:
        content = '{"headline":"H","sections":[{"key":"overview","body_md":"본문"}],' \
                  '"telegram_summary":"T"}'
        input_tokens = 1; output_tokens = 1

    class _Client:
        def __init__(self, *a, **k): pass
        async def chat(self, **k): return _Chat()
        async def aclose(self): pass

    class _AI:
        digest_model = ""; primary_model = "m"; base_url = "u"; max_tokens = 2048

    class _Prof:
        persona = "지식 큐레이터다."
        digest_sections = [{"key": "overview", "kind": "llm", "title": "요약", "guide": "핵심"}]

    monkeypatch.setattr(digest_service, "LiteLLMClient", _Client)
    async def _ai(_g): return _AI()
    monkeypatch.setattr(digest_service, "resolve_ai_gateway", _ai)
    async def _rec(**k): return None
    monkeypatch.setattr(digest_service, "record_usage", _rec)

    class _Mgr:
        async def get_profile(self, _g): return _Prof()
    monkeypatch.setattr(digest_service, "get_settings_manager", lambda: _Mgr())

    from app.services import preset_service
    async def _rp(_g):
        from app.services.preset_service import ResolvedPrompts
        return ResolvedPrompts(analysis_prompt="", digest_prompt="", preset_id=None)
    monkeypatch.setattr(preset_service, "resolve_prompts", _rp)

    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    end = datetime(2026, 7, 8, tzinfo=timezone.utc)
    gen = await synthesize_with_llm(
        group_id=1, aggregate=_agg(), period_start=start, period_end=end,
        category="", digest_prompt="",   # ← 빈 프롬프트 = structured 모드
        period_days=7, owner_user_id=1,
    )
    assert gen.headline == "H"
    keys = [s["key"] for s in gen.sections]
    assert "overview" in keys           # llm 섹션
    assert gen.summary_md               # 파생 markdown 존재
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_digest_structured.py -v`
Expected: FAIL — `AttributeError: 'DigestGenerated' object has no attribute 'sections'` (또는 조립 미구현)

- [ ] **Step 3: Write implementation**

In `app/services/digest_service.py`:

3a. Add import near other digest imports:

```python
from app.services.digest_sections import (
    assemble_output_sections,
    build_structured_prompt,
    parse_structured_response,
    resolve_sections,
    sections_to_markdown,
    SECTION_KIND_LLM,
)
```

3b. Add `sections` to `DigestGenerated`:

```python
@dataclass
class DigestGenerated:
    headline: str
    summary_md: str
    telegram_summary: str
    model_name: str
    sections: list[dict] = field(default_factory=list)
```

3c. In `synthesize_with_llm`, replace the body from `model = ...` through the return. Branch on `digest_prompt` (non-empty → custom, existing behavior; empty → structured). Structured path:

```python
    ai = await resolve_ai_gateway(group_id)
    from app.services.preset_service import resolve_prompts

    prompts = await resolve_prompts(group_id)
    model = ai.digest_model or ai.primary_model
    period_label = _period_label(period_start, period_end)

    custom_prompt = (digest_prompt or prompts.digest_prompt or "").strip()
    if custom_prompt:
        # === custom 모드: 현행 경로 그대로 (기존 코드 유지) ===
        prompt = custom_prompt
        try:
            user_msg = prompt.format(
                category=category or "전체",
                period_label=period_label,
                video_count=aggregate.video_count,
                sentiment_summary=_sentiment_summary_text(aggregate.sentiment_breakdown),
                top_tags=", ".join(t["name"] for t in aggregate.top_tags[:8]) or "없음",
                top_channels=", ".join(f"{c['name']}({c['count']})" for c in aggregate.top_channels[:10]) or "없음",
                top_viewed=_build_top_viewed_block(aggregate.videos),
                previous_digest=previous_digest,
                videos_block=_build_videos_block(aggregate.videos, aggregate.video_count),
            )
        except (KeyError, IndexError, ValueError):
            context_json = _render_payload(aggregate, period_start, period_end, category)
            videos_block = _build_videos_block(aggregate.videos, aggregate.video_count)
            user_msg = f"{prompt}\n\n집계 데이터:\n{context_json}\n\n영상별 자료:\n{videos_block}"
        sections_spec = None
    else:
        # === structured 모드: 프로필 페르소나 + 섹션 조립 ===
        profile = await get_settings_manager().get_profile(group_id)
        sections_spec = resolve_sections([], profile.digest_sections)
        data_block = (
            f"기간: {period_label}\n"
            f"분석 영상 수: {aggregate.video_count}\n"
            f"감성 분포: {_sentiment_summary_text(aggregate.sentiment_breakdown)}\n"
            f"주요 태그: {', '.join(t['name'] for t in aggregate.top_tags[:8]) or '없음'}\n"
            f"직전 리포트: {previous_digest}\n\n"
            f"영상별 자료:\n{_build_videos_block(aggregate.videos, aggregate.video_count)}"
        )
        user_msg = build_structured_prompt(
            persona=getattr(profile, "persona", ""),
            data_block=data_block, sections=sections_spec,
        )

    client = LiteLLMClient(ai)
    try:
        chat = await client.chat(
            model=model,
            messages=[{"role": "user", "content": user_msg}],
            temperature=0.2,
            max_tokens=min(ai.max_tokens or 4096, 4096),
            response_format={"type": "json_object"},
        )
        await record_usage(
            user_id=owner_user_id, group_id=group_id, purpose="digest", model=model,
            input_tokens=chat.input_tokens, output_tokens=chat.output_tokens,
        )
        if sections_spec is None:
            # custom 모드 파싱(현행)
            data = json.loads(chat.content)
            headline = str(data.get("headline") or "").strip() or _fallback_headline(period_days)
            summary_md = str(data.get("summary_md") or "").strip() or \
                f"- 분석 영상 수: {aggregate.video_count}\n- 감성 분포: {aggregate.sentiment_breakdown}"
            telegram_summary = str(data.get("telegram_summary") or "").strip() or summary_md[:900]
            return DigestGenerated(
                headline=headline, summary_md=summary_md,
                telegram_summary=telegram_summary[:900], model_name=model,
            )
        # structured 모드 파싱·조립
        llm_keys = [s["key"] for s in sections_spec if s["kind"] == SECTION_KIND_LLM]
        headline, bodies, telegram_summary = parse_structured_response(
            chat.content, requested_keys=llm_keys
        )
        out_sections = assemble_output_sections(sections_spec, llm_bodies=bodies, agg=aggregate)
        summary_md = sections_to_markdown(out_sections)
        if not headline:
            headline = _fallback_headline(period_days)
        if not summary_md:
            summary_md = f"- 분석 영상 수: {aggregate.video_count}"
        if not telegram_summary:
            telegram_summary = summary_md[:900]
        return DigestGenerated(
            headline=headline, summary_md=summary_md,
            telegram_summary=telegram_summary[:900], model_name=model,
            sections=out_sections,
        )
    finally:
        await client.aclose()
```

3d. In `generate_digest_for_group`, add `digest_sections=generated.sections or None,` to the `Digest(...)` constructor (after `top_channels=agg.top_channels,`).

3e. In `_fallback_generated`, no sections needed (returns sections=[] by default) — the fallback stays summary_md-based.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_digest_structured.py tests/test_digest_configs.py tests/test_digest_helpers.py -v`
Expected: PASS (all — existing digest tests still green)

- [ ] **Step 5: Commit**

```bash
git add app/services/digest_service.py tests/test_digest_structured.py
git commit -m "feat: digest structured/custom 모드 분기·섹션 생성·저장

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 9: digest_view 정규 뷰모델

**Files:**
- Create: `app/services/digest_view.py`
- Test: `tests/test_digest_view.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_digest_view.py
"""digest 정규 뷰모델 테스트."""

from __future__ import annotations

from app.services.digest_view import build_digest_sections


class _D:
    def __init__(self, sections=None, summary_md=None):
        self.digest_sections = sections
        self.summary_md = summary_md


def test_build_from_digest_sections():
    d = _D(sections=[
        {"key": "overview", "kind": "llm", "title": "요약", "body_md": "본문"},
        {"key": "top_tags", "kind": "computed", "title": "태그",
         "data": {"items": [{"name": "AI", "count": 3}]}},
    ])
    out = build_digest_sections(d)
    assert out[0].kind == "llm" and out[0].body_md == "본문"
    assert out[1].kind == "computed" and out[1].data["items"][0]["name"] == "AI"


def test_fallback_to_summary_md_when_no_sections():
    d = _D(sections=None, summary_md="## 요약\n- 레거시")
    out = build_digest_sections(d)
    assert len(out) == 1
    assert out[0].kind == "llm"
    assert "레거시" in out[0].body_md


def test_empty_when_nothing():
    assert build_digest_sections(_D()) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_digest_view.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.digest_view`

- [ ] **Step 3: Write implementation**

```python
# app/services/digest_view.py
"""Digest 산출물의 정규 뷰모델.

digest_sections(구조화) 우선, 없으면 summary_md 단일 레거시 섹션으로 폴백.
모든 프리젠터(웹/SSR)는 이 뷰모델만 소비한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class DigestSectionView:
    key: str
    kind: str
    title: str
    body_md: Optional[str] = None
    data: Optional[dict] = field(default=None)


def build_digest_sections(digest: Any) -> list[DigestSectionView]:
    raw = getattr(digest, "digest_sections", None)
    if isinstance(raw, list) and raw:
        out: list[DigestSectionView] = []
        for s in raw:
            if not isinstance(s, dict):
                continue
            out.append(DigestSectionView(
                key=str(s.get("key") or "section"),
                kind=str(s.get("kind") or "llm"),
                title=str(s.get("title") or ""),
                body_md=(str(s["body_md"]) if s.get("body_md") else None),
                data=(s.get("data") if isinstance(s.get("data"), dict) else None),
            ))
        if out:
            return out
    legacy = (getattr(digest, "summary_md", None) or "").strip()
    if legacy:
        return [DigestSectionView(key="_legacy", kind="llm", title="", body_md=legacy)]
    return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_digest_view.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add app/services/digest_view.py tests/test_digest_view.py
git commit -m "feat: digest 정규 뷰모델(build_digest_sections)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 10: 신규 그룹 digest 시드를 config 1건으로

**Files:**
- Modify: `app/services/default_settings.py:56-59`
- Test: `tests/test_default_settings.py`

- [ ] **Step 1: Write the failing test (append to tests/test_default_settings.py)**

```python
# tests/test_default_settings.py 에 추가
import json as _json
from app.services.default_settings import DEFAULT_GROUP_SETTINGS


def test_digest_seed_has_one_disabled_weekly_config():
    items = {i["key"]: i for i in DEFAULT_GROUP_SETTINGS["digest"]}
    configs = _json.loads(items["configs"]["value"])
    assert len(configs) == 1
    assert configs[0]["enabled"] is False       # 사용자가 토글로 켠다
    assert configs[0]["period_days"] == 7
    assert configs[0]["digest_prompt"] == ""    # structured 모드로 동작
    assert configs[0]["sections"] == []         # 런타임에 프로필/기본값 폴백
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_default_settings.py -k digest_seed -v`
Expected: FAIL — 현재 시드는 `configs=[]`

- [ ] **Step 3: Write implementation**

In `app/services/default_settings.py`, replace the `"digest"` entry:

```python
    "digest": [
        {"key": "configs", "value": json.dumps([{
            "id": "default-weekly",
            "name": "주간 리뷰",
            "enabled": False,
            "period_days": 7,
            "schedule_time": "20:00",
            "schedule_day": "sun",
            "schedule_dom": 1,
            "timezone": "Asia/Seoul",
            "category": "",
            "digest_prompt": "",
            "telegram_enabled": False,
            "sections": [],
        }], ensure_ascii=False), "value_type": "json"},
        {"key": "share_link_enabled", "value": "true", "value_type": "bool"},
    ],
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_default_settings.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add app/services/default_settings.py tests/test_default_settings.py
git commit -m "feat: 신규 그룹 digest 시드를 비활성 주간 config 1건으로

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 11: profile 라우터 + create_group 백그라운드 부트스트랩

**Files:**
- Create: `app/routers/profile.py`
- Modify: `app/main.py` (라우터 등록)
- Modify: `app/routers/groups.py:91` (부트스트랩 트리거)
- Test: `tests/test_profile_api.py`

먼저 main.py의 라우터 등록 패턴을 확인한다:

```bash
grep -n "include_router\|from app.routers" app/main.py | head -30
```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_profile_api.py
"""profile 라우터·부트스트랩 트리거 스모크 테스트(순수/모킹)."""

from __future__ import annotations

from app.routers import profile as profile_router


def test_profile_router_has_expected_routes():
    paths = {r.path for r in profile_router.router.routes}
    assert "/api/groups/{slug}/profile" in paths


def test_regenerate_route_registered():
    methods = set()
    for r in profile_router.router.routes:
        if r.path == "/api/groups/{slug}/profile/regenerate":
            methods |= r.methods
    assert "POST" in methods
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_profile_api.py -v`
Expected: FAIL — `ModuleNotFoundError: app.routers.profile`

- [ ] **Step 3: Write implementation**

```python
# app/routers/profile.py
"""그룹 프로필 조회·재생성 API."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.models.control.group import Group
from app.routers.deps import get_group_or_404
from app.services.bootstrap_service import bootstrap_profile
from app.services.settings_manager import get_settings_manager

router = APIRouter(prefix="/api/groups/{slug}/profile", tags=["profile"])


@router.get("")
async def get_profile(group: Group = Depends(get_group_or_404)) -> dict:
    p = await get_settings_manager().get_profile(group.group_id)
    return {
        "persona": p.persona,
        "digest_sections": p.digest_sections,
        "bootstrap_status": p.bootstrap_status,
        "bootstrap_at": p.bootstrap_at,
    }


@router.post("/regenerate")
async def regenerate_profile(group: Group = Depends(get_group_or_404)) -> dict:
    await bootstrap_profile(group, force=True)
    p = await get_settings_manager().get_profile(group.group_id)
    return {
        "persona": p.persona,
        "digest_sections": p.digest_sections,
        "bootstrap_status": p.bootstrap_status,
    }
```

In `app/main.py`, register the router alongside the existing ones (mirror the digests router registration):

```python
from app.routers import profile as profile_router
# ... near other app.include_router(...) calls:
app.include_router(profile_router.router)
```

In `app/routers/groups.py`, add import at top:

```python
import asyncio
from app.services.bootstrap_service import bootstrap_profile
```

In `create_group`, after `await seed_default_settings(group.group_id)` (line 91), add:

```python
    # 프로필 부트스트랩은 백그라운드로 — 생성 응답을 지연시키지 않는다.
    # 실패해도 시드된 중립 기본값으로 digest가 동작한다.
    asyncio.create_task(bootstrap_profile(group))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_profile_api.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Run full backend suite to check for regressions**

Run: `pytest -q`
Expected: PASS (기존 테스트 포함 전부 green). 실패 시 해당 테스트를 열어 수정.

- [ ] **Step 6: Commit**

```bash
git add app/routers/profile.py app/main.py app/routers/groups.py tests/test_profile_api.py
git commit -m "feat: 프로필 API·create_group 백그라운드 부트스트랩

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 12: 프론트 타입 + OrderedItemsBuilder 추출

**Files:**
- Modify: `frontend/src/api/types.ts`
- Create: `frontend/src/components/OrderedItemsBuilder.tsx`
- Modify: `frontend/src/components/TemplateBuilder.tsx`
- Test: `frontend/src/components/OrderedItemsBuilder.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/components/OrderedItemsBuilder.test.tsx
import { describe, it, expect } from 'vitest'
import { moveItem } from './OrderedItemsBuilder'

describe('moveItem', () => {
  it('moves an item down', () => {
    expect(moveItem(['a', 'b', 'c'], 0, 1)).toEqual(['b', 'a', 'c'])
  })
  it('moves an item up', () => {
    expect(moveItem(['a', 'b', 'c'], 2, -1)).toEqual(['a', 'c', 'b'])
  })
  it('is a no-op at the boundary', () => {
    expect(moveItem(['a', 'b'], 0, -1)).toEqual(['a', 'b'])
    expect(moveItem(['a', 'b'], 1, 1)).toEqual(['a', 'b'])
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/OrderedItemsBuilder.test.tsx`
Expected: FAIL — cannot import `moveItem`

- [ ] **Step 3: Write implementation**

```tsx
// frontend/src/components/OrderedItemsBuilder.tsx
import type { ReactNode } from 'react'

export function moveItem<T>(items: T[], idx: number, dir: -1 | 1): T[] {
  const target = idx + dir
  if (target < 0 || target >= items.length) return items
  const next = [...items]
  ;[next[idx], next[target]] = [next[target], next[idx]]
  return next
}

export interface OrderedItem {
  key: string
  label: string
}

interface Props {
  included: OrderedItem[]
  available: OrderedItem[]
  onMove: (idx: number, dir: -1 | 1) => void
  onRemove: (key: string) => void
  onAdd: (key: string) => void
  renderExtra?: (key: string) => ReactNode  // 섹션 guide 편집 등 항목별 부가 UI
}

export default function OrderedItemsBuilder({
  included, available, onMove, onRemove, onAdd, renderExtra,
}: Props) {
  return (
    <div className="space-y-3">
      {included.length > 0 && (
        <div className="border border-gray-200 rounded-lg divide-y divide-gray-100">
          {included.map((item, idx) => (
            <div key={item.key} className="px-3 py-2 text-sm">
              <div className="flex items-center gap-2">
                <span className="flex-1 text-gray-700">{item.label}</span>
                <button type="button" onClick={() => onMove(idx, -1)} disabled={idx === 0}
                  className="px-1 text-gray-400 hover:text-gray-700 disabled:opacity-30">▲</button>
                <button type="button" onClick={() => onMove(idx, 1)}
                  disabled={idx === included.length - 1}
                  className="px-1 text-gray-400 hover:text-gray-700 disabled:opacity-30">▼</button>
                <button type="button" onClick={() => onRemove(item.key)}
                  className="px-1 text-red-400 hover:text-red-600">×</button>
              </div>
              {renderExtra?.(item.key)}
            </div>
          ))}
        </div>
      )}
      {available.length > 0 && (
        <div className="border border-dashed border-gray-200 rounded-lg divide-y divide-gray-100">
          {available.map((item) => (
            <div key={item.key} className="flex items-center gap-2 px-3 py-2 text-sm text-gray-400">
              <span className="flex-1">{item.label}</span>
              <button type="button" onClick={() => onAdd(item.key)}
                className="px-2 py-0.5 text-xs text-blue-500 border border-blue-200 rounded hover:bg-blue-50">+ 추가</button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
```

In `frontend/src/api/types.ts`, add to `DigestScheduleConfig` (after `digest_prompt: string`):

```ts
  telegram_enabled: boolean
  sections?: DigestSection[]
}

export interface DigestSection {
  key: string
  kind: 'llm' | 'computed'
  title: string
  guide?: string
  body_md?: string
  data?: Record<string, unknown>
}
```

And add to `Digest` (after `top_channels`):

```ts
  top_channels: TagCount[] | null
  digest_sections?: DigestSection[] | null
```

Refactor `TemplateBuilder.tsx` to use `moveItem` (replace its inline `move` swap logic with `onChange({ fields: moveItem(included, idx, dir) })`, importing `moveItem`). Keep TemplateBuilder's existing props/behavior — this is a no-behavior-change refactor to share the swap helper.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/OrderedItemsBuilder.test.tsx && npx tsc --noEmit`
Expected: PASS + no type errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/OrderedItemsBuilder.tsx frontend/src/components/OrderedItemsBuilder.test.tsx frontend/src/components/TemplateBuilder.tsx frontend/src/api/types.ts
git commit -m "feat: OrderedItemsBuilder 추출·digest 타입에 sections 추가

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 13: DigestSectionBuilder + DigestConfigsEditor 통합

**Files:**
- Create: `frontend/src/components/DigestSectionBuilder.tsx`
- Modify: `frontend/src/components/DigestConfigsEditor.tsx`
- Test: `frontend/src/components/DigestSectionBuilder.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/components/DigestSectionBuilder.test.tsx
import { describe, it, expect } from 'vitest'
import { COMPUTED_SECTION_DEFS, addSection, removeSection } from './DigestSectionBuilder'

describe('DigestSectionBuilder helpers', () => {
  it('exposes computed section catalog', () => {
    const keys = COMPUTED_SECTION_DEFS.map((d) => d.key)
    expect(keys).toContain('top_tags')
    expect(keys).toContain('top_viewed')
  })
  it('adds a computed section with title from catalog', () => {
    const out = addSection([], { key: 'top_tags', kind: 'computed' })
    expect(out).toHaveLength(1)
    expect(out[0].title).toBe('주요 태그')
  })
  it('removes a section by key', () => {
    const secs = [
      { key: 'overview', kind: 'llm' as const, title: '요약' },
      { key: 'top_tags', kind: 'computed' as const, title: '태그' },
    ]
    expect(removeSection(secs, 'overview').map((s) => s.key)).toEqual(['top_tags'])
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/DigestSectionBuilder.test.tsx`
Expected: FAIL — cannot import from `./DigestSectionBuilder`

- [ ] **Step 3: Write implementation**

```tsx
// frontend/src/components/DigestSectionBuilder.tsx
import type { DigestSection } from '../api/types'
import OrderedItemsBuilder, { moveItem } from './OrderedItemsBuilder'

export const COMPUTED_SECTION_DEFS: { key: string; title: string }[] = [
  { key: 'stats_overview', title: '이번 기간 개요' },
  { key: 'sentiment_breakdown', title: '평가 분포' },
  { key: 'top_tags', title: '주요 태그' },
  { key: 'top_channels', title: '주요 채널' },
  { key: 'top_viewed', title: '조회수 상위' },
]

const LLM_PRESETS: { key: string; title: string; guide: string }[] = [
  { key: 'overview', title: '핵심 요약', guide: '이번 기간을 가로지르는 3~5개 핵심 흐름을 서술' },
  { key: 'perspectives', title: '관점 비교', guide: '합의된 관점과 엇갈리는 관점을 구분해 대비' },
  { key: 'insights', title: '핵심 인사이트', guide: '시청자가 실제 판단에 쓸 수 있는 구체적 인사이트' },
]

const ALL_ADDABLE = [
  ...LLM_PRESETS.map((p) => ({ key: p.key, label: `${p.title} (LLM)` })),
  ...COMPUTED_SECTION_DEFS.map((c) => ({ key: c.key, label: `${c.title} (자동)` })),
]

export function addSection(
  sections: DigestSection[], add: { key: string; kind: 'llm' | 'computed' },
): DigestSection[] {
  if (add.kind === 'computed') {
    const def = COMPUTED_SECTION_DEFS.find((d) => d.key === add.key)
    return [...sections, { key: add.key, kind: 'computed', title: def?.title ?? add.key }]
  }
  const preset = LLM_PRESETS.find((p) => p.key === add.key)
  return [...sections, {
    key: add.key, kind: 'llm', title: preset?.title ?? add.key, guide: preset?.guide ?? '',
  }]
}

export function removeSection(sections: DigestSection[], key: string): DigestSection[] {
  return sections.filter((s) => s.key !== key)
}

interface Props {
  sections: DigestSection[]
  onChange: (s: DigestSection[]) => void
}

export default function DigestSectionBuilder({ sections, onChange }: Props) {
  const includedKeys = new Set(sections.map((s) => s.key))
  const available = ALL_ADDABLE.filter((a) => !includedKeys.has(a.key))
  const kindOf = (key: string): 'llm' | 'computed' =>
    COMPUTED_SECTION_DEFS.some((c) => c.key === key) ? 'computed' : 'llm'

  return (
    <OrderedItemsBuilder
      included={sections.map((s) => ({
        key: s.key,
        label: `${s.title}${s.kind === 'computed' ? ' (자동)' : ''}`,
      }))}
      available={available}
      onMove={(idx, dir) => onChange(moveItem(sections, idx, dir))}
      onRemove={(key) => onChange(removeSection(sections, key))}
      onAdd={(key) => onChange(addSection(sections, { key, kind: kindOf(key) }))}
      renderExtra={(key) => {
        const s = sections.find((x) => x.key === key)
        if (!s || s.kind !== 'llm') return null
        const idx = sections.findIndex((x) => x.key === key)
        return (
          <input
            className="mt-1 w-full border border-gray-200 rounded px-2 py-1 text-xs text-gray-600"
            placeholder="작성 지침 (선택)"
            value={s.guide ?? ''}
            onChange={(e) => {
              const next = [...sections]
              next[idx] = { ...s, guide: e.target.value }
              onChange(next)
            }}
          />
        )
      }}
    />
  )
}
```

In `DigestConfigsEditor.tsx`:

3a. Add imports:

```tsx
import DigestSectionBuilder from './DigestSectionBuilder'
import type { DigestSection } from '../api/types'
```

3b. In `newConfig`, add `sections: []` to the returned object.

3c. In `parseConfigs`'s `.map`, add:

```tsx
          sections: Array.isArray(item.sections) ? (item.sections as DigestSection[]) : [],
```

3d. Replace the "Digest 프롬프트" `<Field>` block (lines 235-241) with a section builder plus a collapsed advanced prompt. Insert before the telegram checkbox:

```tsx
            <div>
              <p className="text-sm font-medium text-gray-700 mb-1">리포트 섹션</p>
              {cfg.digest_prompt.trim() ? (
                <p className="text-xs text-amber-600 mb-2">
                  이 설정은 커스텀 프롬프트 모드로 동작합니다. 아래 고급 설정의 프롬프트를 비우면 섹션 편집이 적용됩니다.
                </p>
              ) : (
                <DigestSectionBuilder
                  sections={cfg.sections ?? []}
                  onChange={(s) => update(index, { sections: s })}
                />
              )}
              <p className="text-xs text-gray-400 mt-1">
                비워 두면 그룹 프로필의 추천 구성이 자동 적용됩니다.
              </p>
            </div>

            <details className="text-sm">
              <summary className="cursor-pointer text-gray-500">고급: 전체 프롬프트 직접 작성</summary>
              <textarea
                className="mt-2 w-full border border-gray-300 rounded-lg px-3 py-2 text-sm min-h-[120px]"
                value={cfg.digest_prompt}
                onChange={(e) => update(index, { digest_prompt: e.target.value })}
                placeholder="여기에 프롬프트를 쓰면 섹션 편집 대신 이 프롬프트로 동작합니다."
              />
            </details>
```

3e. In `handleSave`, `configs` is already serialized via `JSON.stringify(configs)` — since `sections` is now part of each config object, it is included automatically. No change needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/DigestSectionBuilder.test.tsx && npx tsc --noEmit`
Expected: PASS + no type errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/DigestSectionBuilder.tsx frontend/src/components/DigestSectionBuilder.test.tsx frontend/src/components/DigestConfigsEditor.tsx
git commit -m "feat: digest 섹션 빌더 UI·고급 프롬프트 접힘 모드

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 14: DigestDetail 섹션 렌더

**Files:**
- Modify: `frontend/src/pages/DigestDetail.tsx`
- Test: `frontend/src/pages/DigestDetail.sections.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/pages/DigestDetail.sections.test.tsx
import { describe, it, expect } from 'vitest'
import { toRenderSections } from './DigestDetail'
import type { Digest } from '../api/types'

const base: Partial<Digest> = {
  digest_pk: 1, headline: 'H', period_start: '2026-07-01', period_end: '2026-07-08',
  video_count: 2, status: 'done', summary_md: null,
}

describe('toRenderSections', () => {
  it('uses digest_sections when present', () => {
    const d = { ...base, digest_sections: [
      { key: 'overview', kind: 'llm', title: '요약', body_md: '본문' },
    ] } as Digest
    const out = toRenderSections(d)
    expect(out[0].title).toBe('요약')
    expect(out[0].body_md).toBe('본문')
  })
  it('falls back to summary_md', () => {
    const d = { ...base, digest_sections: null, summary_md: '## 요약\n- 레거시' } as Digest
    const out = toRenderSections(d)
    expect(out).toHaveLength(1)
    expect(out[0].body_md).toContain('레거시')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/pages/DigestDetail.sections.test.tsx`
Expected: FAIL — cannot import `toRenderSections`

- [ ] **Step 3: Write implementation**

In `DigestDetail.tsx`, add an exported pure helper near the top (after imports) and use it in render:

```tsx
import type { Digest, DigestSection } from '../api/types'

export function toRenderSections(digest: Digest): DigestSection[] {
  const secs = digest.digest_sections
  if (Array.isArray(secs) && secs.length) return secs
  if (digest.summary_md && digest.summary_md.trim()) {
    return [{ key: '_legacy', kind: 'llm', title: '요약', body_md: digest.summary_md }]
  }
  return []
}

function computedToMarkdown(s: DigestSection): string {
  const items = (s.data?.items as { name?: string; count?: number; channel?: string; head?: string; views?: number }[]) ?? []
  if (s.key === 'top_tags' || s.key === 'top_channels') {
    return items.map((it) => `- ${it.name} (${it.count})`).join('\n')
  }
  if (s.key === 'top_viewed') {
    return items.map((it) => `- [${it.channel}] ${it.head}`).join('\n')
  }
  const breakdown = (s.data?.breakdown as Record<string, number>) ?? {}
  if (s.key === 'sentiment_breakdown') {
    return Object.entries(breakdown).map(([k, v]) => `- ${k}: ${v}`).join('\n')
  }
  if (s.key === 'stats_overview') {
    return `- 분석 영상 ${(s.data?.video_count as number) ?? 0}건`
  }
  return ''
}
```

Replace the `{digest.summary_md && (...)}` block (lines 55-62) with:

```tsx
      {toRenderSections(digest).map((s) => (
        <div key={s.key} className="bg-white rounded-xl shadow-sm p-5">
          {s.title && <h2 className="font-semibold text-gray-800 mb-3">{s.title}</h2>}
          <article className="prose prose-sm max-w-none text-gray-700 break-words">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {s.kind === 'computed' ? computedToMarkdown(s) : (s.body_md ?? '')}
            </ReactMarkdown>
          </article>
        </div>
      ))}
```

Keep the existing top_tags / top_channels / sentiment_breakdown blocks below as-is — they still render from the digest's dedicated columns for backward compatibility with legacy digests.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/pages/DigestDetail.sections.test.tsx && npx tsc --noEmit`
Expected: PASS + no type errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/DigestDetail.tsx frontend/src/pages/DigestDetail.sections.test.tsx
git commit -m "feat: DigestDetail 구조화 섹션 렌더·레거시 폴백

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 15: 프로필 확인 카드 + digest 활성화 넛지

**Files:**
- Create: `frontend/src/components/ProfileCard.tsx`
- Modify: `frontend/src/pages/Digests.tsx` (넛지 배너 + 카드 배치)
- Modify: `frontend/src/api/` (profile API 클라이언트 — 기존 api 패턴 따라)
- Test: `frontend/src/components/ProfileCard.test.tsx`

먼저 기존 api 클라이언트 패턴 확인:

```bash
sed -n '1,40p' frontend/src/api/digests.ts
```

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/components/ProfileCard.test.tsx
import { describe, it, expect } from 'vitest'
import { profileSummaryLine } from './ProfileCard'

describe('profileSummaryLine', () => {
  it('summarizes section titles', () => {
    const line = profileSummaryLine([
      { key: 'overview', kind: 'llm', title: '핵심 요약' },
      { key: 'top_tags', kind: 'computed', title: '주요 태그' },
    ])
    expect(line).toBe('핵심 요약 · 주요 태그')
  })
  it('handles empty', () => {
    expect(profileSummaryLine([])).toBe('기본 구성')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/ProfileCard.test.tsx`
Expected: FAIL — cannot import `profileSummaryLine`

- [ ] **Step 3: Write implementation**

Create the profile API client `frontend/src/api/profile.ts` mirroring `digests.ts` structure (use the `http` helper the codebase already uses):

```ts
// frontend/src/api/profile.ts
import { http } from './http'
import type { DigestSection } from './types'

export interface GroupProfile {
  persona: string
  digest_sections: DigestSection[]
  bootstrap_status: string
  bootstrap_at?: string
}

export const profileApi = (slug: string) => ({
  get: () => http.get<GroupProfile>(`/api/groups/${slug}/profile`),
  regenerate: () => http.post<GroupProfile>(`/api/groups/${slug}/profile/regenerate`, {}),
})
```

(If `http` signature differs, match the exact pattern used in `frontend/src/api/digests.ts` — check Step 1's output and adapt the method calls accordingly.)

```tsx
// frontend/src/components/ProfileCard.tsx
import type { DigestSection } from '../api/types'

export function profileSummaryLine(sections: DigestSection[]): string {
  if (!sections.length) return '기본 구성'
  return sections.map((s) => s.title).join(' · ')
}

interface Props {
  sections: DigestSection[]
  status: string
  onRegenerate: () => void
  regenerating: boolean
}

export default function ProfileCard({ sections, status, onRegenerate, regenerating }: Props) {
  return (
    <div className="bg-white rounded-xl shadow-sm p-5 space-y-2">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold text-gray-800">이 그룹의 리포트 구성</h2>
        <button
          type="button" onClick={onRegenerate} disabled={regenerating}
          className="px-3 py-1 text-xs border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50"
        >
          {regenerating ? '생성 중...' : '다시 생성'}
        </button>
      </div>
      <p className="text-sm text-gray-600">{profileSummaryLine(sections)}</p>
      {status === 'failed' && (
        <p className="text-xs text-amber-600">자동 구성에 실패해 기본 구성으로 동작 중입니다. ‘다시 생성’을 눌러보세요.</p>
      )}
    </div>
  )
}
```

In `Digests.tsx`, load the profile on mount and render `<ProfileCard>` at the top. Add a nudge banner when there are analyses but no enabled digest config — the exact condition uses data already available on the page (analysis count / configs). Minimal version: always show ProfileCard; show a banner linking to settings when `bootstrap_status !== 'done'` OR no digest exists. Wire `regenerate` via `profileApi(slug).regenerate()`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/ProfileCard.test.tsx && npx tsc --noEmit`
Expected: PASS + no type errors

- [ ] **Step 5: Build the frontend to catch integration errors**

Run: `cd frontend && npm run build`
Expected: build succeeds

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/ProfileCard.tsx frontend/src/components/ProfileCard.test.tsx frontend/src/api/profile.ts frontend/src/pages/Digests.tsx
git commit -m "feat: 프로필 확인 카드·재생성·digest 넛지

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 16: 전체 검증 + 배포 노트

**Files:**
- Test: 전체 스위트

- [ ] **Step 1: 백엔드 전체 테스트**

Run: `pytest -q`
Expected: 전부 PASS. 실패 시 해당 테스트를 열어 원인 수정 후 재실행.

- [ ] **Step 2: 프론트 전체 테스트 + 타입 + 빌드**

Run: `cd frontend && npx vitest run && npx tsc --noEmit && npm run build`
Expected: 전부 PASS / 에러 없음.

- [ ] **Step 3: 실제 앱 구동 검증 (verify 스킬)**

`/verify` 또는 `run` 스킬로 앱을 띄우고 다음을 육안 확인:
1. 신규 그룹 생성 → Digests 페이지에 프로필 카드가 뜨는지(부트스트랩 지연 시 status 표시).
2. 기존 그룹(telco/brand) digest 설정에서 프롬프트가 채워진 config는 "커스텀 프롬프트 모드" 배지가 뜨고 섹션 빌더가 숨겨지는지.
3. 프롬프트가 빈 config는 섹션 빌더가 뜨고 추가/제외/순서 조정이 되는지.
4. structured 모드 digest를 수동 생성(`POST /digests/generate`) 시 DigestDetail에 섹션이 렌더되는지.

- [ ] **Step 4: 배포 노트 확인**

- `digest_sections` 컬럼은 `ensure_schema`의 additive 패치로 배포 시 전 그룹에 자동 적용된다. 배포 후 관리자 콘솔의 스키마 마이그레이터(`migrate_all_schemas`)를 한 번 실행해 전 그룹 선반영을 확인.
- 기존 그룹은 `bootstrap_status='none'` 상태다. 프로필은 그룹별 "다시 생성"으로 소급 생성하거나, digest_prompt가 채워진 그룹은 그대로 custom 모드로 동작하므로 조치 불필요.

- [ ] **Step 5: 최종 커밋 (문서/메모)**

```bash
git add -A
git commit -m "chore: Phase 1 digest 섹션·프로필 부트스트랩 검증 완료

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 자기 리뷰 체크리스트 (구현 전 확인)

- **스펙 커버리지**: 섹션 모델(§1.3)=T1~3, DigestScheduleConfig.sections(§1.4)=T4, 프로필(§1.1)=T5, 부트스트랩(§1.2)=T6·T11, 컬럼(§1.6)=T7, 2층 조립(§1.5)=T3·T8, 뷰모델(§1.7)=T9, 시드(§1.4)=T10, API/프론트(§1.8)=T11~15, 테스트(§1.9)=각 Task. ✅
- **모드 추론 일관성**: `cfg.digest_prompt.trim()` 비어있음 → structured, 채워짐 → custom. 백엔드(T8)·프론트(T13) 동일 기준. ✅
- **타입 일관성**: 섹션 dict 키 `{key, kind, title, guide, body_md, data}`가 백엔드(digest_sections.py)·프론트(types.ts DigestSection)에서 동일. computed 키 목록도 백엔드 COMPUTED_SECTIONS과 프론트 COMPUTED_SECTION_DEFS 동일(top_tags/top_channels/top_viewed/sentiment_breakdown/stats_overview). ✅
- **하위 호환**: structured 모드도 summary_md를 파생 저장(T8) → 기존 share_page·레거시 digest 렌더 무변경. custom 모드는 기존 코드 경로 유지. ✅
