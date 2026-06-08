# Custom Message Template Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 텔레그램 알림 메시지에 포함할 필드와 순서를 사용자가 자유롭게 구성할 수 있도록 `message_detail` (full/compact) 고정 모드를 단일 커스텀 렌더링 엔진으로 교체한다.

**Architecture:** `settings_types.py`에 프리셋 상수(PRESET_FULL, PRESET_COMPACT)와 `message_template: dict` 필드를 추가하고, `notify_service.py`에 필드별 렌더러 딕셔너리와 `build_from_template()`을 도입한다. `settings_manager.py`의 파싱에서 기존 `message_detail` 값을 프리셋으로 폴백해 DB 마이그레이션 없이 하위 호환성을 유지한다. 프론트엔드는 `TemplateBuilder` 컴포넌트를 신규 작성해 `SettingsForm`의 `template_builder` 타입으로 연결한다.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy async, React 18, TypeScript, Tailwind CSS, pytest

---

## 파일 맵

| 파일 | 변경 유형 | 내용 |
|---|---|---|
| `app/services/settings_types.py` | 수정 | `PRESET_FULL`, `PRESET_COMPACT` 상수 추가; `message_detail`·`include_share_link` 제거; `message_template: dict` 추가 |
| `app/services/notify_service.py` | 수정 | 필드별 렌더러 함수 16개 + `FIELD_RENDERERS` dict + `build_from_template()` 추가; `build_message()`·`notify_video()` 시그니처 교체; `_build_full()`·`_build_compact()` 제거 |
| `app/services/settings_manager.py` | 수정 | `get_notification()` 파싱: `message_template` JSON 읽기 + `message_detail` 폴백; `include_share_link` 제거 |
| `app/services/default_settings.py` | 수정 | `message_detail`·`include_share_link` 항목 → `message_template` JSON 항목으로 교체 |
| `app/routers/videos.py` | 수정 | `notify_video()` 호출: `detail=`·`include_share_link=` → `template=notif.message_template` |
| `app/services/monitor_service.py` | 수정 | 동일 (2곳) |
| `tests/test_message_format.py` | 수정 | `detail="full/compact"` → `template=PRESET_FULL/PRESET_COMPACT`; `build_from_template` 신규 테스트 추가 |
| `tests/test_notification_settings_defaults.py` | 수정 | `message_detail` → `message_template` 검증 |
| `frontend/src/settings/defs.ts` | 수정 | `FieldType`에 `template_builder` 추가; `message_detail`·`include_share_link` → `message_template` 필드 |
| `frontend/src/settings/convert.ts` | 수정 | `template_builder` 타입 `initialValue()`·`toSaveItem()` 처리 추가 |
| `frontend/src/components/SettingsForm.tsx` | 수정 | `Field` 컴포넌트에 `template_builder` 분기 추가 |
| `frontend/src/components/TemplateBuilder.tsx` | 신규 | 포함 필드 선택·순서 조정 컴포넌트 |

---

## Task 1: 프리셋 상수 + 필드 렌더러 엔진

**Files:**
- Modify: `app/services/settings_types.py`
- Modify: `app/services/notify_service.py`
- Test: `tests/test_message_format.py`

- [ ] **Step 1: `settings_types.py`에 프리셋 상수 추가**

`from dataclasses import dataclass, field` 아래, `@dataclass class NotificationSettings` 위에 삽입:

```python
PRESET_FULL: dict = {"fields": [
    "channel_name", "headline", "analysis_sections", "bullet_points",
    "tags", "published_at", "duration", "video_url", "share_link",
]}

PRESET_COMPACT: dict = {"fields": [
    "headline", "one_line", "short_summary_md",
    "sentiment", "confidence_score",
    "video_url", "share_link",
]}
```

- [ ] **Step 2: `test_message_format.py` — `build_from_template` 실패 테스트 작성**

파일 맨 끝에 추가:

```python
from app.services.settings_types import PRESET_FULL, PRESET_COMPACT
from app.services.notify_service import build_from_template


def test_build_from_template_preset_full_contains_key_fields():
    v = _video()
    a = _analysis()
    msg = build_from_template(v, a, PRESET_FULL, channel_name="테스트채널", tags=["반도체"])
    assert "🎬 [테스트채널] 신규 영상" in msg
    assert "<b>헤드라인</b>" in msg
    assert "• 주장1" in msg
    assert "🏷 반도체" in msg
    assert "영상 보러가기" in msg


def test_build_from_template_preset_compact_no_bullets():
    v = _video()
    a = _analysis()
    msg = build_from_template(v, a, PRESET_COMPACT)
    assert "• 주장1" not in msg
    assert "🎬" not in msg
    assert "짧은요약" in msg


def test_build_from_template_custom_order():
    v = _video()
    a = _analysis()
    template = {"fields": ["one_line", "headline"]}
    msg = build_from_template(v, a, template)
    assert msg.index("한줄") < msg.index("헤드라인")


def test_build_from_template_low_conf_always_first():
    v = _video()
    a = _analysis(conf=0.3)
    msg = build_from_template(v, a, PRESET_FULL, threshold=0.5)
    assert msg.startswith("⚠️ <b>[저신뢰도 분석]</b>")


def test_build_from_template_unknown_field_skipped():
    v = _video()
    a = _analysis()
    template = {"fields": ["headline", "nonexistent_field"]}
    msg = build_from_template(v, a, template)
    assert "<b>헤드라인</b>" in msg


def test_build_from_template_under_max_len():
    v = _video()
    a = _analysis(full_analysis_md="가" * 6000)
    msg = build_from_template(v, a, PRESET_FULL, channel_name="C", tags=["t"])
    assert len(msg) <= 4096
```

- [ ] **Step 3: 테스트 실행 — 실패 확인**

```
cd /Users/mukymook/Library/CloudStorage/SynologyDrive-mookmuky/04.Coding/ytdb
python -m pytest tests/test_message_format.py::test_build_from_template_preset_full_contains_key_fields -v
```

Expected: `ImportError` 또는 `FAILED` (build_from_template 미구현)

- [ ] **Step 4: `notify_service.py`에 필드 렌더러 함수 16개 추가**

`_build_share_url()` 함수 바로 아래에 삽입 (기존 `_build_compact` 위):

```python
# ── 필드별 렌더러 ──────────────────────────────────────────────────────────

def _render_headline(video, analysis, ctx: dict) -> str:
    title = analysis.headline or getattr(video, "title", "") or ""
    return f"<b>{escape(title)}</b>" if title else ""


def _render_one_line(video, analysis, ctx: dict) -> str:
    val = getattr(analysis, "one_line", None)
    return escape(val) if val else ""


def _render_short_summary(video, analysis, ctx: dict) -> str:
    val = getattr(analysis, "short_summary_md", None)
    return escape(val) if val else ""


def _render_analysis_sections(video, analysis, ctx: dict) -> str:
    from app.services.analysis_view import build_sections
    sections = build_sections(
        getattr(analysis, "analysis_sections", None),
        getattr(analysis, "full_analysis_md", None),
    )
    if sections:
        return _sections_to_telegram_html(sections)
    return escape(getattr(analysis, "short_summary_md", "") or "")


def _render_bullets(video, analysis, ctx: dict) -> str:
    bp = analysis.bullet_points if isinstance(analysis.bullet_points, list) else []
    return _format_bullets(bp)


def _render_key_points(video, analysis, ctx: dict) -> str:
    kp = getattr(analysis, "key_points", None)
    return _format_bullets(kp) if isinstance(kp, list) else ""


def _render_insights(video, analysis, ctx: dict) -> str:
    ins = getattr(analysis, "insights", None)
    if isinstance(ins, list):
        return _format_bullets(ins)
    if isinstance(ins, str) and ins:
        return escape(ins)
    return ""


def _render_entities(video, analysis, ctx: dict) -> str:
    ent = getattr(analysis, "entities", None)
    if not isinstance(ent, list) or not ent:
        return ""
    return "🔖 " + ", ".join(escape(str(e)) for e in ent if e)


def _render_sentiment(video, analysis, ctx: dict) -> str:
    s = getattr(analysis, "sentiment", None)
    return f"감성: {escape(s)}" if s else ""


def _render_confidence(video, analysis, ctx: dict) -> str:
    c = getattr(analysis, "confidence_score", None)
    if c is None:
        return ""
    return f"신뢰도: {float(c):.2f}"


def _render_channel_name(video, analysis, ctx: dict) -> str:
    name = ctx.get("channel_name") or ""
    return f"<b>🎬 [{escape(name)}] 신규 영상</b>" if name else ""


def _render_published_at(video, analysis, ctx: dict) -> str:
    dt = getattr(video, "published_at", None)
    return f"📅 {_to_kst(dt)}" if dt else ""


def _render_duration(video, analysis, ctx: dict) -> str:
    dur = _format_duration(getattr(video, "duration_seconds", None))
    return f"⏱ {dur}" if dur else ""


def _render_tags(video, analysis, ctx: dict) -> str:
    tags = ctx.get("tags") or []
    return "🏷 " + ", ".join(escape(t) for t in tags) if tags else ""


def _render_video_url(video, analysis, ctx: dict) -> str:
    url = getattr(video, "video_url", None)
    return f'🔗 <a href="{escape(url, quote=True)}">영상 보러가기</a>' if url else ""


def _render_share_link(video, analysis, ctx: dict) -> str:
    group_slug = ctx.get("group_slug") or ""
    share_url = _build_share_url(group_slug, video)
    return f'📖 <a href="{escape(share_url, quote=True)}">웹에서 자세히 보기</a>' if share_url else ""


FIELD_RENDERERS: dict[str, Callable] = {
    "headline":          _render_headline,
    "one_line":          _render_one_line,
    "short_summary_md":  _render_short_summary,
    "analysis_sections": _render_analysis_sections,
    "bullet_points":     _render_bullets,
    "key_points":        _render_key_points,
    "insights":          _render_insights,
    "entities":          _render_entities,
    "sentiment":         _render_sentiment,
    "confidence_score":  _render_confidence,
    "channel_name":      _render_channel_name,
    "published_at":      _render_published_at,
    "duration":          _render_duration,
    "tags":              _render_tags,
    "video_url":         _render_video_url,
    "share_link":        _render_share_link,
}


def build_from_template(
    video,
    analysis,
    template: dict,
    *,
    channel_name: str = "",
    tags: list | None = None,
    threshold: float = 0.0,
    group_slug: str = "",
) -> str:
    low_conf = (
        analysis.confidence_score is not None
        and float(analysis.confidence_score) < float(threshold)
    )
    ctx: dict = {"channel_name": channel_name, "tags": tags or [], "group_slug": group_slug}
    parts: list[str] = []
    if low_conf:
        parts.append("⚠️ <b>[저신뢰도 분석]</b>")
    for field_key in template.get("fields", []):
        renderer = FIELD_RENDERERS.get(field_key)
        if renderer:
            rendered = renderer(video, analysis, ctx)
            if rendered:
                parts.append(rendered)
    return _truncate_html("\n\n".join(parts), _TELEGRAM_MAX_LEN)
```

- [ ] **Step 5: 테스트 실행 — 통과 확인**

```
python -m pytest tests/test_message_format.py -k "build_from_template" -v
```

Expected: 6개 모두 PASSED

- [ ] **Step 6: 커밋**

```bash
git add app/services/settings_types.py app/services/notify_service.py tests/test_message_format.py
git commit -m "feat: add PRESET constants and build_from_template() rendering engine"
```

---

## Task 2: `build_message()` + `notify_video()` 시그니처 교체 및 기존 함수 제거

**Files:**
- Modify: `app/services/notify_service.py`
- Modify: `tests/test_message_format.py`

- [ ] **Step 1: `test_message_format.py` 기존 테스트 `detail=` → `template=` 업데이트**

아래 함수들을 수정한다:

```python
# test_full_contains_rich_fields 수정
def test_full_contains_rich_fields():
    from app.services.settings_types import PRESET_FULL
    msg = build_message(_video(), _analysis(), channel_name="증시각도기TV",
                        tags=["반도체", "금리"], template=PRESET_FULL)
    assert "🎬 [증시각도기TV] 신규 영상" in msg
    assert "<b>헤드라인</b>" in msg
    assert "<b>한 줄 요약</b>" in msg
    assert "• 주장1" in msg
    assert "🏷 반도체, 금리" in msg
    assert "⏱ 14:10" in msg
    assert '<a href="https://youtu.be/x">영상 보러가기</a>' in msg


# test_full_low_confidence_badge_top 수정
def test_full_low_confidence_badge_top():
    from app.services.settings_types import PRESET_FULL
    msg = build_message(_video(), _analysis(conf=0.3), threshold=0.5, template=PRESET_FULL)
    assert msg.startswith("⚠️ <b>[저신뢰도 분석]</b>")


# test_compact_backward_compatible 수정
def test_compact_backward_compatible():
    from app.services.settings_types import PRESET_COMPACT
    msg = build_message(_video(), _analysis(), template=PRESET_COMPACT)
    assert msg.startswith("<b>헤드라인</b>")
    assert "🎬" not in msg
    assert "신뢰도" in msg


# test_full_smart_truncation_keeps_under_limit 수정
def test_full_smart_truncation_keeps_under_limit():
    from app.services.settings_types import PRESET_FULL
    big = "가" * 6000
    msg = build_message(_video(), _analysis(full_analysis_md=big),
                        channel_name="C", tags=["t"], template=PRESET_FULL)
    assert len(msg) <= 4096
    assert "영상 보러가기" in msg


# test_full_truncation_preserves_link_with_html_heavy_body 수정
def test_full_truncation_preserves_link_with_html_heavy_body():
    from app.services.settings_types import PRESET_FULL
    heavy = "<&>" * 3000
    a = _analysis(full_analysis_md=heavy, bullet_points=[])
    msg = build_message(_video(), a, channel_name="C", tags=["t"], template=PRESET_FULL)
    assert len(msg) <= 4096
    assert "영상 보러가기" in msg


# test_full_truncation_many_huge_bullets_under_limit 수정
def test_full_truncation_many_huge_bullets_under_limit():
    from app.services.settings_types import PRESET_FULL
    huge_bullets = ["가" * 500 for _ in range(20)]
    a = _analysis(full_analysis_md="짧은본문", bullet_points=huge_bullets)
    msg = build_message(_video(), a, channel_name="C", tags=["t"], template=PRESET_FULL)
    assert len(msg) <= 4096
    assert "영상 보러가기" in msg


# test_build_message_full_body_has_bold 수정
def test_build_message_full_body_has_bold():
    from app.services.settings_types import PRESET_FULL
    v = _video(published_at=None, duration_seconds=None)
    a = _analysis(full_analysis_md="### 결론\n금리 위험 높음")
    msg = build_message(v, a, channel_name="C", tags=[], template=PRESET_FULL)
    assert "<b>결론</b>" in msg
```

- [ ] **Step 2: 테스트 실행 — 현재 실패 확인**

```
python -m pytest tests/test_message_format.py -v
```

Expected: `test_full_*`, `test_compact_*` 실패 (`unexpected keyword argument 'template'`)

- [ ] **Step 3: `notify_service.py`에서 `build_message()` 시그니처 교체**

기존 `build_message()` 함수를 아래로 교체:

```python
def build_message(
    video,
    analysis,
    threshold: float = 0.0,
    *,
    channel_name: str = "",
    tags=None,
    template: dict | None = None,
    group_slug: str = "",
) -> str:
    from app.services.settings_types import PRESET_FULL
    return build_from_template(
        video, analysis, template or PRESET_FULL,
        channel_name=channel_name, tags=tags or [],
        threshold=threshold, group_slug=group_slug,
    )
```

- [ ] **Step 4: `notify_service.py`에서 `notify_video()` 시그니처 교체**

기존 `notify_video()` 함수 시그니처와 내부 `build_message` 호출을 교체:

```python
async def notify_video(
    notif: NotificationSettings,
    video: Video,
    analysis: VideoAnalysis,
    client: Optional[httpx.AsyncClient] = None,
    threshold: float = 0.0,
    *,
    channel_name: str = "",
    tags=None,
    template: dict | None = None,
    group_slug: str = "",
) -> int:
    """그룹의 모든 chat_id에 발송. 성공 건수 반환. 일부 실패해도 나머지는 계속 시도."""
    if not notif.is_sendable:
        return 0
    text = build_message(
        video, analysis, threshold,
        channel_name=channel_name, tags=tags or [],
        template=template, group_slug=group_slug,
    )
    own_client = client is None
    cl = client or httpx.AsyncClient(timeout=20.0)
    sent = 0
    errors: list[str] = []
    try:
        for chat_id in notif.chat_ids:
            try:
                await send_telegram(cl, notif.bot_token, chat_id, text, notif.parse_mode)
                sent += 1
            except Exception as e:
                errors.append(str(e))
    finally:
        if own_client:
            await cl.aclose()
    if errors and sent == 0:
        raise RuntimeError("; ".join(errors)[:500])
    return sent
```

- [ ] **Step 5: `notify_service.py`에서 `_build_compact()` + `_build_full()` 제거**

두 함수 전체를 삭제한다. (렌더러 함수들로 대체됨)

- [ ] **Step 6: 테스트 전체 실행 — 통과 확인**

```
python -m pytest tests/test_message_format.py -v
```

Expected: 전체 PASSED

- [ ] **Step 7: 커밋**

```bash
git add app/services/notify_service.py tests/test_message_format.py
git commit -m "feat: replace build_message/notify_video with template-based API, remove _build_full/_build_compact"
```

---

## Task 3: 설정 모델 + 파서 + 호출부 교체

**Files:**
- Modify: `app/services/settings_types.py`
- Modify: `app/services/settings_manager.py`
- Modify: `app/services/default_settings.py`
- Modify: `app/routers/videos.py`
- Modify: `app/services/monitor_service.py`
- Modify: `app/services/notify_service.py` (notify_pending_batch)
- Test: `tests/test_notification_settings_defaults.py`

- [ ] **Step 1: `test_notification_settings_defaults.py` 업데이트**

```python
"""NotificationSettings 신규 필드 기본값/하위호환 검증."""

from app.services.settings_types import NotificationSettings, PRESET_FULL


def test_defaults():
    n = NotificationSettings()
    assert n.send_mode == "immediate"
    assert n.scheduled_times == []
    assert n.scheduled_max_per_run == 5
    assert n.wait_between_messages_sec == 30
    assert n.quiet_hours_enabled is False
    assert n.quiet_hours_start == "22:00"
    assert n.quiet_hours_end == "07:00"
    assert n.timezone == "Asia/Seoul"
    assert n.low_confidence_threshold == 0.5
    assert n.message_template == PRESET_FULL


def test_is_sendable_unchanged():
    assert NotificationSettings().is_sendable is False
    n = NotificationSettings(enabled=True, bot_token="t", chat_ids=["1"])
    assert n.is_sendable is True
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

```
python -m pytest tests/test_notification_settings_defaults.py -v
```

Expected: `AttributeError: 'NotificationSettings' object has no attribute 'message_template'`

- [ ] **Step 3: `settings_types.py` — `NotificationSettings` 필드 교체**

`message_detail: str = "full"` 줄과 `include_share_link: bool = True` 줄을 삭제하고, 그 자리에 아래를 추가:

```python
message_template: dict = field(default_factory=lambda: dict(PRESET_FULL))
```

(파일 상단 `from dataclasses import dataclass, field` 는 이미 있음)

- [ ] **Step 4: `settings_manager.py` — `get_notification()` 파싱 업데이트**

`_normalize_dispatch_scope` 함수 아래에 헬퍼 추가:

```python
def _detail_to_template(detail: Any) -> dict:
    """레거시 message_detail 문자열을 프리셋으로 변환."""
    from app.services.settings_types import PRESET_COMPACT, PRESET_FULL
    return dict(PRESET_COMPACT) if str(detail or "").strip() == "compact" else dict(PRESET_FULL)
```

`get_notification()` 내부의 `NotificationSettings(...)` 생성 부분에서:

삭제:
```python
message_detail=str(d.get("message_detail") or "full"),
include_share_link=bool(d.get("include_share_link", True)),
```

추가:
```python
message_template=_parse_message_template(d),
```

그리고 `get_notification()` 바로 위에 아래 함수 추가:

```python
def _parse_message_template(d: dict) -> dict:
    from app.services.settings_types import PRESET_FULL
    raw = d.get("message_template")
    if isinstance(raw, dict) and "fields" in raw:
        return raw
    return _detail_to_template(d.get("message_detail"))
```

(`get_typed()`가 `value_type="json"` 행에 대해 이미 `json.loads()`를 수행하므로 `raw`는 파싱된 dict 또는 `None`이다.)

- [ ] **Step 5: `default_settings.py` — notification 기본값 교체**

파일 상단 `import json` 추가 (없으면). `from app.services.settings_types import ...` import에 `PRESET_FULL` 추가:

```python
import json
from app.services.settings_types import PRESET_FULL
```

`notification` 카테고리 항목 중 아래 두 줄을:

```python
{"key": "message_detail", "value": "full", "value_type": "string"},
{"key": "include_share_link", "value": "true", "value_type": "bool"},
```

아래 한 줄로 교체:

```python
{"key": "message_template", "value": json.dumps(PRESET_FULL), "value_type": "json"},
```

- [ ] **Step 6: 테스트 실행 — 통과 확인**

```
python -m pytest tests/test_notification_settings_defaults.py -v
```

Expected: PASSED

- [ ] **Step 7: `notify_pending_batch()` 호출 업데이트**

`notify_service.py`의 `notify_pending_batch()` 내부 `notify_video()` 호출에서:

삭제:
```python
tags=tags, detail=notif.message_detail,
group_slug=group_slug,
include_share_link=notif.include_share_link,
```

교체:
```python
tags=tags, template=notif.message_template,
group_slug=group_slug,
```

- [ ] **Step 8: `app/routers/videos.py` 호출 업데이트**

`notify_video()` 호출에서:

삭제:
```python
tags=tags, detail=notif.message_detail,
group_slug=group.slug,
include_share_link=notif.include_share_link,
```

교체:
```python
tags=tags, template=notif.message_template,
group_slug=group.slug,
```

- [ ] **Step 9: `app/services/monitor_service.py` 호출 업데이트 (2곳)**

`monitor_service.py`에서 `notify_video()` 호출이 2곳 있다. 각 호출에서:

삭제:
```python
tags=tags, detail=notif.message_detail,
```
와 `include_share_link=notif.include_share_link,`

교체:
```python
tags=tags, template=notif.message_template,
```

- [ ] **Step 10: 전체 테스트 실행**

```
python -m pytest tests/ -v
```

Expected: 전체 PASSED (기존 테스트 포함)

- [ ] **Step 11: 커밋**

```bash
git add app/services/settings_types.py app/services/settings_manager.py \
        app/services/default_settings.py app/services/notify_service.py \
        app/routers/videos.py app/services/monitor_service.py \
        tests/test_notification_settings_defaults.py
git commit -m "feat: replace message_detail/include_share_link with message_template in settings and call sites"
```

---

## Task 4: 프론트엔드 TemplateBuilder UI

**Files:**
- Create: `frontend/src/components/TemplateBuilder.tsx`
- Modify: `frontend/src/settings/defs.ts`
- Modify: `frontend/src/settings/convert.ts`
- Modify: `frontend/src/components/SettingsForm.tsx`

- [ ] **Step 1: `frontend/src/components/TemplateBuilder.tsx` 신규 작성**

```typescript
import { useState } from 'react'

export interface MessageTemplate {
  fields: string[]
}

const PRESET_FULL: MessageTemplate = {
  fields: ['channel_name', 'headline', 'analysis_sections', 'bullet_points',
           'tags', 'published_at', 'duration', 'video_url', 'share_link'],
}

const PRESET_COMPACT: MessageTemplate = {
  fields: ['headline', 'one_line', 'short_summary_md',
           'sentiment', 'confidence_score', 'video_url', 'share_link'],
}

const ALL_FIELDS: { key: string; label: string }[] = [
  { key: 'channel_name',      label: '채널명' },
  { key: 'headline',          label: '헤드라인' },
  { key: 'one_line',          label: '한 줄 요약' },
  { key: 'short_summary_md',  label: '짧은 요약' },
  { key: 'analysis_sections', label: '상세 분석 본문' },
  { key: 'bullet_points',     label: '핵심 주장' },
  { key: 'key_points',        label: '핵심 포인트' },
  { key: 'insights',          label: '인사이트' },
  { key: 'entities',          label: '언급 개체' },
  { key: 'sentiment',         label: '감성' },
  { key: 'confidence_score',  label: '신뢰도 점수' },
  { key: 'published_at',      label: '게시일' },
  { key: 'duration',          label: '영상 길이' },
  { key: 'tags',              label: '태그' },
  { key: 'video_url',         label: '영상 링크' },
  { key: 'share_link',        label: '웹 공유 링크' },
]

const labelOf = (key: string) => ALL_FIELDS.find((f) => f.key === key)?.label ?? key

interface Props {
  value: MessageTemplate
  onChange: (v: MessageTemplate) => void
}

export default function TemplateBuilder({ value, onChange }: Props) {
  const included = value.fields
  const available = ALL_FIELDS.filter((f) => !included.includes(f.key))

  const move = (idx: number, dir: -1 | 1) => {
    const next = [...included]
    const target = idx + dir
    if (target < 0 || target >= next.length) return
    ;[next[idx], next[target]] = [next[target], next[idx]]
    onChange({ fields: next })
  }

  const remove = (key: string) => onChange({ fields: included.filter((k) => k !== key) })
  const add = (key: string) => onChange({ fields: [...included, key] })
  const applyPreset = (preset: MessageTemplate) => onChange({ fields: [...preset.fields] })

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => applyPreset(PRESET_FULL)}
          className="px-3 py-1 text-xs border border-gray-300 rounded-lg hover:bg-gray-50"
        >
          Full 기본값으로
        </button>
        <button
          type="button"
          onClick={() => applyPreset(PRESET_COMPACT)}
          className="px-3 py-1 text-xs border border-gray-300 rounded-lg hover:bg-gray-50"
        >
          Compact 기본값으로
        </button>
      </div>

      {included.length > 0 && (
        <div className="border border-gray-200 rounded-lg divide-y divide-gray-100">
          {included.map((key, idx) => (
            <div key={key} className="flex items-center gap-2 px-3 py-2 text-sm">
              <span className="flex-1 text-gray-700">{labelOf(key)}</span>
              <button
                type="button"
                onClick={() => move(idx, -1)}
                disabled={idx === 0}
                className="px-1 text-gray-400 hover:text-gray-700 disabled:opacity-30"
              >▲</button>
              <button
                type="button"
                onClick={() => move(idx, 1)}
                disabled={idx === included.length - 1}
                className="px-1 text-gray-400 hover:text-gray-700 disabled:opacity-30"
              >▼</button>
              <button
                type="button"
                onClick={() => remove(key)}
                className="px-1 text-red-400 hover:text-red-600"
              >×</button>
            </div>
          ))}
        </div>
      )}

      {available.length > 0 && (
        <div className="border border-dashed border-gray-200 rounded-lg divide-y divide-gray-100">
          {available.map((f) => (
            <div key={f.key} className="flex items-center gap-2 px-3 py-2 text-sm text-gray-400">
              <span className="flex-1">{f.label}</span>
              <button
                type="button"
                onClick={() => add(f.key)}
                className="px-2 py-0.5 text-xs text-blue-500 border border-blue-200 rounded hover:bg-blue-50"
              >+ 추가</button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: `frontend/src/settings/defs.ts` 업데이트**

`FieldType` 타입에 `'template_builder'` 추가:

```typescript
export type FieldType =
  | 'string' | 'int' | 'float' | 'textarea' | 'bool'
  | 'select' | 'model_select' | 'chatlist' | 'int_days' | 'int_hours'
  | 'time' | 'timelist' | 'template_builder'
```

`notification` 카테고리에서 `message_detail` 행과 `include_share_link` 행을 삭제하고, 그 자리에 추가:

```typescript
{ key: 'message_template', label: '메시지 템플릿', type: 'template_builder',
  help: '포함할 필드를 선택하고 ▲▼로 순서를 조정하세요. 위 두 버튼으로 기본값 복원 가능.' },
```

- [ ] **Step 3: `frontend/src/settings/convert.ts` 업데이트**

`initialValue()` 함수에서 `return raw == null ? '' : String(raw)` 바로 위에 추가:

```typescript
if (def.type === 'template_builder') {
  try {
    const parsed = JSON.parse(raw || '{}')
    return (parsed && Array.isArray(parsed.fields)) ? parsed : { fields: [] }
  } catch {
    return { fields: [] }
  }
}
```

`toSaveItem()` 함수에서 `if (def.type === 'bool')` 분기 위에 추가:

```typescript
if (def.type === 'template_builder') {
  return { key: def.key, value: JSON.stringify(value), value_type: 'json', is_secret: false }
}
```

`FormValue` 타입도 object를 허용하도록 수정:

```typescript
export type FormValue = string | boolean | string[] | object
```

- [ ] **Step 4: `frontend/src/components/SettingsForm.tsx` 업데이트**

파일 상단에 import 추가:

```typescript
import TemplateBuilder, { type MessageTemplate } from './TemplateBuilder'
```

`Field` 컴포넌트 내부 `def.type === 'timelist'` 분기 아래에 추가:

```typescript
) : def.type === 'template_builder' ? (
  <TemplateBuilder
    value={value as MessageTemplate}
    onChange={onChange}
  />
```

- [ ] **Step 5: 프론트엔드 빌드 확인**

```bash
cd /Users/mukymook/Library/CloudStorage/SynologyDrive-mookmuky/04.Coding/ytdb/frontend
npm run build 2>&1 | tail -20
```

Expected: `✓ built in` 메시지, 에러 없음

- [ ] **Step 6: 커밋**

```bash
cd /Users/mukymook/Library/CloudStorage/SynologyDrive-mookmuky/04.Coding/ytdb
git add frontend/src/components/TemplateBuilder.tsx \
        frontend/src/settings/defs.ts \
        frontend/src/settings/convert.ts \
        frontend/src/components/SettingsForm.tsx
git commit -m "feat: add TemplateBuilder component and wire template_builder field type"
```
