# 텔레그램 메시지 풍부화 + Digest 영상블록 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 전작(my-assistant)의 풍부한 텔레그램 알림 포맷과 digest 영상블록(`videos_block`)을 ytdb 멀티그룹 구조로 포팅한다.

**Architecture:** Part A는 `build_message`를 채널·full_analysis·bullets·태그·날짜 포함 포맷으로 교체하고 스마트 길이절단을 추가하며, `message_detail`(full/compact) 설정으로 제어한다. Part B는 `aggregate_period`가 영상별 brief를 수집하고 `_build_videos_block`으로 LLM 입력을 구성해 `synthesize_with_llm`이 `.format()`으로 치환하도록 고친다. 카테고리는 토큰 포함 매칭으로 정규화한다.

**Tech Stack:** Python 3 / FastAPI / SQLAlchemy async / pytest. Frontend: React + TS + vitest.

설계: `docs/superpowers/specs/2026-06-04-notify-digest-enrichment-design.md`
레퍼런스: `/Users/mukymook/cursor-workspace/my-assistant/app/services/bots/youtube_bot.py`, `.../youtube/digest_service.py`
테스트 명령: `python -m pytest` (no `.venv/bin/pytest`). 프론트: `cd frontend && npx vitest run` / `npx tsc --noEmit`.

---

## File Structure
- `app/services/notify_service.py` — message 포맷 헬퍼 + build_message 교체 + 태그 조회 + notify_video 시그니처 확장.
- `app/services/monitor_service.py` — `_notify_after_analysis`가 채널명·태그·detail 주입.
- `app/routers/videos.py` — 수동 발송도 채널명·태그·detail 주입.
- `app/services/digest_service.py` — VideoBrief/aggregate_period 확장 + videos_block/entities/카테고리 헬퍼 + synthesize .format().
- `app/services/settings_types.py`, `settings_manager.py`, `default_settings.py` — `message_detail`.
- `frontend/src/settings/defs.ts` — notification에 `message_detail` select.
- tests: `tests/test_message_format.py`, `tests/test_digest_helpers.py`, 기존 `tests/test_notify_schedule.py`(배지 테스트 갱신).

---

## Task 1: message_detail 설정 추가

**Files:**
- Modify: `app/services/settings_types.py` (NotificationSettings)
- Modify: `app/services/settings_manager.py` (get_notification)
- Modify: `app/services/default_settings.py` (notification 시드)
- Test: `tests/test_notification_settings_defaults.py` (기존 파일에 추가)

- [ ] **Step 1: 기존 테스트에 message_detail 기본값 검증 추가**

`tests/test_notification_settings_defaults.py`의 `test_defaults()` 함수 마지막 assert 다음 줄에 추가:

```python
    assert n.message_detail == "full"
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_notification_settings_defaults.py -v`
Expected: FAIL (AttributeError: 'NotificationSettings' object has no attribute 'message_detail')

- [ ] **Step 3: dataclass 필드 추가**

`app/services/settings_types.py`의 `NotificationSettings`에서 `low_confidence_threshold: float = 0.5` 다음 줄에 추가:

```python
    message_detail: str = "full"  # full | compact
```

- [ ] **Step 4: get_notification에 로드 추가**

`app/services/settings_manager.py`의 `get_notification` 내 `return NotificationSettings(` 호출에서 `low_confidence_threshold=...` 줄 다음에 추가:

```python
            message_detail=str(d.get("message_detail") or "full"),
```

- [ ] **Step 5: 기본 시드 추가**

`app/services/default_settings.py`의 `"notification"` 리스트 끝에 추가:

```python
        {"key": "message_detail", "value": "full", "value_type": "string"},
```

- [ ] **Step 6: 통과 확인**

Run: `python -m pytest tests/test_notification_settings_defaults.py -q && python -m pytest -q`
Expected: PASS (전체)

- [ ] **Step 7: Commit**

```bash
git add app/services/settings_types.py app/services/settings_manager.py app/services/default_settings.py tests/test_notification_settings_defaults.py
git commit -m "feat: 알림 message_detail(full/compact) 설정 추가"
```

---

## Task 2: 메시지 포맷 순수 헬퍼

**Files:**
- Modify: `app/services/notify_service.py` (헬퍼 추가)
- Test: `tests/test_message_format.py` (생성)

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_message_format.py` 생성:

```python
"""텔레그램 메시지 포맷 순수 헬퍼 검증."""

from datetime import datetime, timezone

from app.services.notify_service import (
    _to_kst,
    _format_duration,
    _format_bullets,
    _truncate_html,
)


def test_to_kst_utc_to_kst():
    dt = datetime(2026, 5, 30, 2, 5, tzinfo=timezone.utc)  # 11:05 KST
    assert _to_kst(dt) == "2026-05-30 11:05 KST"


def test_format_duration_hms():
    assert _format_duration(14 * 60 + 10) == "14:10"
    assert _format_duration(3661) == "1:01:01"
    assert _format_duration(0) == ""
    assert _format_duration(None) == ""


def test_format_bullets():
    assert _format_bullets(["a", " b ", "", None]) == "• a\n• b"
    assert _format_bullets(None) == ""
    assert _format_bullets("notalist") == ""


def test_truncate_html():
    assert _truncate_html("abcdef", 100) == "abcdef"
    assert _truncate_html("abcdef", 5) == "ab..."
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_message_format.py -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: 헬퍼 구현**

`app/services/notify_service.py` 상단(파일 import 다음, build_message 앞)에 추가. `escape`는 이미 `from html import escape`로 import됨. `timezone`은 Task 6에서 추가했을 수 있으나 없으면 `from datetime import datetime, timezone`을 import에 보장:

```python
_TELEGRAM_MAX_LEN = 4096


def _to_kst(dt) -> str:
    try:
        from zoneinfo import ZoneInfo
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")
    except Exception:
        return str(dt)


def _format_duration(seconds) -> str:
    if not seconds:
        return ""
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _format_bullets(bullet_points) -> str:
    if not isinstance(bullet_points, list):
        return ""
    out = []
    for b in bullet_points:
        if b is None:
            continue
        s = str(b).strip()
        if s:
            out.append(f"• {escape(s)}")
    return "\n".join(out)


def _truncate_html(text: str, max_len: int, suffix: str = "...") -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - len(suffix)] + suffix
```

READ the top of notify_service.py first: ensure `from datetime import datetime, timezone` exists (Task 6 from prior work added `from datetime import datetime, timezone` — confirm; if only `datetime` is imported, add `timezone`).

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_message_format.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add app/services/notify_service.py tests/test_message_format.py
git commit -m "feat: 텔레그램 메시지 포맷 순수 헬퍼(KST/duration/bullets/truncate)"
```

---

## Task 3: build_message 교체 (full/compact + 스마트 절단)

**Files:**
- Modify: `app/services/notify_service.py` (build_message)
- Test: `tests/test_message_format.py` (추가), `tests/test_notify_schedule.py` (기존 배지 테스트 갱신)

- [ ] **Step 1: 실패 테스트 작성 (full/compact 구조)**

`tests/test_message_format.py` 하단에 추가:

```python
from types import SimpleNamespace
from app.services.notify_service import build_message


def _video(**kw):
    base = dict(title="제목", video_url="https://youtu.be/x",
                published_at=datetime(2026, 5, 30, 2, 5, tzinfo=timezone.utc),
                duration_seconds=850)
    base.update(kw)
    return SimpleNamespace(**base)


def _analysis(conf=0.9, **kw):
    base = dict(headline="헤드라인", one_line="한줄", short_summary_md="짧은요약",
                full_analysis_md="### 한 줄 요약\n본문", bullet_points=["주장1", "주장2"],
                sentiment="bullish", confidence_score=conf)
    base.update(kw)
    return SimpleNamespace(**base)


def test_full_contains_rich_fields():
    msg = build_message(_video(), _analysis(), channel_name="증시각도기TV",
                        tags=["반도체", "금리"], detail="full")
    assert "🎬 [증시각도기TV] 신규 영상" in msg
    assert "<b>헤드라인</b>" in msg
    assert "### 한 줄 요약" in msg          # full_analysis_md 포함
    assert "• 주장1" in msg
    assert "🏷 반도체, 금리" in msg
    assert "⏱ 14:10" in msg
    assert '<a href="https://youtu.be/x">영상 보러가기</a>' in msg


def test_full_low_confidence_badge_top():
    msg = build_message(_video(), _analysis(conf=0.3), threshold=0.5, detail="full")
    assert msg.startswith("⚠️ <b>[저신뢰도 분석]</b>")


def test_compact_backward_compatible():
    msg = build_message(_video(), _analysis(), detail="compact")
    assert msg.startswith("<b>헤드라인</b>")
    assert "🎬" not in msg
    assert "신뢰도" in msg


def test_full_smart_truncation_keeps_under_limit():
    big = "가" * 6000
    msg = build_message(_video(), _analysis(full_analysis_md=big),
                        channel_name="C", tags=["t"], detail="full")
    assert len(msg) <= 4096
    assert "영상 보러가기" in msg  # 링크는 보존
```

- [ ] **Step 2: 기존 배지 테스트 갱신 (full 포맷 반영)**

`tests/test_notify_schedule.py`의 기존 3개 배지 테스트는 옛 인라인 배지(`<b>⚠️ `)를 가정하므로 새 full 포맷에 맞게 교체한다. 해당 파일에서 `_video`, `_analysis`, `test_badge_added_below_threshold`, `test_no_badge_at_or_above_threshold`, `test_no_badge_when_confidence_none` 세 함수를 아래로 교체:

```python
def test_badge_added_below_threshold():
    v = SimpleNamespace(title="제목", video_url="https://youtu.be/x",
                        published_at=None, duration_seconds=None)
    a = _analysis(0.3)
    msg = build_message(v, a, threshold=0.5, detail="full")
    assert msg.startswith("⚠️ <b>[저신뢰도 분석]</b>")


def test_no_badge_at_or_above_threshold():
    v = SimpleNamespace(title="제목", video_url="https://youtu.be/x",
                        published_at=None, duration_seconds=None)
    msg = build_message(v, _analysis(0.7), threshold=0.5, detail="full")
    assert "저신뢰도 분석" not in msg


def test_no_badge_when_confidence_none():
    v = SimpleNamespace(title="제목", video_url="https://youtu.be/x",
                        published_at=None, duration_seconds=None)
    msg = build_message(v, _analysis(None), threshold=0.5, detail="full")
    assert "저신뢰도 분석" not in msg
```

그리고 같은 파일의 `_analysis` 헬퍼가 `full_analysis_md`, `bullet_points`를 포함하도록 교체:

```python
def _analysis(conf):
    return SimpleNamespace(
        headline="헤드라인", one_line="한줄", short_summary_md="요약",
        full_analysis_md="본문", bullet_points=["b1"],
        sentiment="중립", confidence_score=conf,
    )
```

- [ ] **Step 3: 실패 확인**

Run: `python -m pytest tests/test_message_format.py tests/test_notify_schedule.py -v`
Expected: FAIL (build_message가 아직 새 시그니처/포맷 아님)

- [ ] **Step 4: build_message 교체**

`app/services/notify_service.py`의 기존 `build_message`를 아래로 교체:

```python
def _build_compact(video, analysis, threshold: float) -> str:
    title = analysis.headline or video.title or ""
    low_conf = (
        analysis.confidence_score is not None
        and float(analysis.confidence_score) < float(threshold)
    )
    badge = "⚠️ " if low_conf else ""
    lines = [f"<b>{badge}{escape(title)}</b>"]
    if analysis.one_line:
        lines.append(escape(analysis.one_line))
    if analysis.short_summary_md:
        lines.append("")
        lines.append(escape(analysis.short_summary_md))
    meta = []
    if analysis.sentiment:
        meta.append(f"감성: {escape(analysis.sentiment)}")
    if analysis.confidence_score is not None:
        meta.append(f"신뢰도: {analysis.confidence_score:.2f}")
    if meta:
        lines.append("")
        lines.append(" | ".join(meta))
    if video.video_url:
        lines.append("")
        lines.append(escape(video.video_url))
    return "\n".join(lines)[:_TELEGRAM_MAX_LEN]


def _render_full(*, low_conf, channel_name, headline, body, bullets_list, tags, meta_parts, url) -> str:
    lines = []
    if low_conf:
        lines.append("⚠️ <b>[저신뢰도 분석]</b>")
        lines.append("")
    if channel_name:
        lines.append(f"<b>🎬 [{escape(channel_name)}] 신규 영상</b>")
        lines.append("")
    if headline:
        lines.append(f"<b>{escape(headline)}</b>")
        lines.append("")
    if body:
        lines.append(escape(body))
        lines.append("")
    bullets = _format_bullets(bullets_list)
    if bullets:
        lines.append(bullets)
        lines.append("")
    if tags:
        lines.append("🏷 " + ", ".join(escape(t) for t in tags))
    if meta_parts:
        lines.append("  ·  ".join(meta_parts))
    lines.append("")
    if url:
        lines.append(f'🔗 <a href="{escape(url, quote=True)}">영상 보러가기</a>')
    return "\n".join(lines)


def _build_full(video, analysis, threshold: float, channel_name: str, tags) -> str:
    low_conf = (
        analysis.confidence_score is not None
        and float(analysis.confidence_score) < float(threshold)
    )
    headline = analysis.headline or video.title or ""
    body = analysis.full_analysis_md or analysis.short_summary_md or ""
    bullets_list = analysis.bullet_points if isinstance(analysis.bullet_points, list) else []
    meta_parts = []
    if video.published_at:
        meta_parts.append(f"📅 {_to_kst(video.published_at)}")
    dur = _format_duration(video.duration_seconds)
    if dur:
        meta_parts.append(f"⏱ {dur}")

    def render(b, bl):
        return _render_full(
            low_conf=low_conf, channel_name=channel_name, headline=headline,
            body=b, bullets_list=bl, tags=tags, meta_parts=meta_parts, url=video.video_url,
        )

    text = render(body, bullets_list)
    if len(text) <= _TELEGRAM_MAX_LEN:
        return text
    overflow = len(text) - _TELEGRAM_MAX_LEN + 50
    if len(body) > overflow:
        return render(body[: len(body) - overflow] + "…", bullets_list)
    if bullets_list:
        return render("", bullets_list[:-1])
    return _truncate_html(text, _TELEGRAM_MAX_LEN)


def build_message(video, analysis, threshold: float = 0.0, *,
                  channel_name: str = "", tags=None, detail: str = "full") -> str:
    if detail == "compact":
        return _build_compact(video, analysis, threshold)
    return _build_full(video, analysis, threshold, channel_name, tags or [])
```

- [ ] **Step 5: 통과 확인**

Run: `python -m pytest tests/test_message_format.py tests/test_notify_schedule.py -v && python -m pytest -q`
Expected: PASS (전체)

- [ ] **Step 6: Commit**

```bash
git add app/services/notify_service.py tests/test_message_format.py tests/test_notify_schedule.py
git commit -m "feat: 텔레그램 알림 풍부화(full/compact)+스마트 길이절단"
```

---

## Task 4: notify_video 시그니처 확장 + 태그 조회 + 발송 경로 주입

**Files:**
- Modify: `app/services/notify_service.py` (notify_video, _fetch_video_tags, notify_pending_batch)
- Modify: `app/routers/videos.py` (수동 발송)

설명: DB 의존이라 신규 유닛 테스트 없음. 전체 스위트 통과 + import 무결성으로 검증.

- [ ] **Step 1: 태그 조회 헬퍼 + notify_video 확장**

`app/services/notify_service.py` 상단 import에 `Tag`, `VideoTag` 추가(이미 `Channel`은 import됨):

```python
from app.models.pg.tag import Tag, VideoTag
```

`notify_video` 다음 위치에 헬퍼 추가:

```python
async def _fetch_video_tags(make_session, video_pk: int, limit: int = 8) -> list[str]:
    async with make_session() as sess:
        rows = (
            await sess.execute(
                select(Tag.name)
                .join(VideoTag, VideoTag.tag_pk == Tag.tag_pk)
                .where(VideoTag.video_pk == video_pk)
                .order_by(VideoTag.weight.desc().nullslast(), Tag.name.asc())
                .limit(limit)
            )
        ).all()
    return [r[0] for r in rows]
```

`notify_video` 시그니처와 build_message 호출 교체:

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
    detail: str = "full",
) -> int:
    """그룹의 모든 chat_id에 발송. 성공 건수 반환. 일부 실패해도 나머지는 계속 시도."""
    if not notif.is_sendable:
        return 0
    text = build_message(video, analysis, threshold,
                         channel_name=channel_name, tags=tags or [], detail=detail)
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

- [ ] **Step 2: notify_pending_batch에서 주입**

`notify_pending_batch`의 `notify_video(...)` 호출부를 찾아, 채널명·태그·detail을 넘기도록 교체. 해당 루프는 `for i, (video, analysis) in enumerate(batch):` 이고 batch는 `(v, a)` 튜플이라 채널명이 없으므로, candidates 구성 시 채널을 함께 보존하도록 수정한다. `candidates` 리스트 컴프리헨션과 batch 처리 부분을 아래로 교체:

```python
    candidates = [
        (v, a, ch)
        for (v, a, ch) in rows
        if _passes_notify_baseline(ch.notify_from, v.published_at)
    ]
    if not candidates:
        return 0

    batch = candidates[:max_per]
    remaining = len(candidates) - len(batch)
    timer = JobTimer()
    sent = 0
    try:
        with timer:
            client = httpx.AsyncClient(timeout=20.0)
            try:
                for i, (video, analysis, channel) in enumerate(batch):
                    try:
                        tags = await _fetch_video_tags(make_session, video.video_pk)
                        ok = await notify_video(
                            notif, video, analysis, client, threshold,
                            channel_name=getattr(channel, "channel_name", "") or "",
                            tags=tags, detail=notif.message_detail,
                        )
                        if ok:
                            async with make_session() as sess:
                                async with sess.begin():
                                    await sess.execute(
                                        update(Video)
                                        .where(Video.video_pk == video.video_pk)
                                        .values(notified_at=datetime.now(timezone.utc))
                                    )
                            sent += 1
                    except Exception as exc:
                        print(f"⚠️ {log_label}: video_pk={video.video_pk} 발송 실패 — {exc}")
                    if i < len(batch) - 1 and wait_sec > 0:
                        await asyncio.sleep(wait_sec)
            finally:
                await client.aclose()
    finally:
        msg = f"{log_label}: {sent}/{len(batch)}건 발송" + (
            f", 잔여 약 {remaining}건" if remaining else ""
        )
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_NOTIFY,
            status=STATUS_SUCCESS if sent > 0 else STATUS_FAIL,
            message=msg,
            duration_ms=timer.elapsed_ms,
        )
    return sent
```

(주의: 기존 `candidates`는 `(v, a)` 2-튜플이었음 — 위 교체로 `(v, a, ch)` 3-튜플로 바꾸고 batch 루프도 3-튜플 언패킹으로 맞춤.)

- [ ] **Step 3: 수동 발송(videos.py)에서 주입**

`app/routers/videos.py`의 `sent = await notify_video(notif, video, analysis)`를 교체. 채널명은 `video.source_channel_name`(있으면), 태그는 그룹 세션으로 조회:

```python
    from app.services.notify_service import _fetch_video_tags
    make_session = lambda: dpm.group_session(group)
    try:
        tags = await _fetch_video_tags(make_session, video_pk)
    except Exception:
        tags = []
    try:
        sent = await notify_video(
            notif, video, analysis,
            channel_name=getattr(video, "source_channel_name", "") or "",
            tags=tags, detail=notif.message_detail,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"발송 실패: {e}") from e
```

READ `app/routers/videos.py` around the call first to confirm `group` and `dpm` are in scope (dpm = data_plane_engine_manager; `dpm.group_session(group)` is used elsewhere in the same file). If the session helper name differs, match the existing usage in that file.

- [ ] **Step 4: 회귀/무결성 확인**

Run: `python -c "import app.services.notify_service, app.routers.videos; print('ok')" && python -m pytest -q`
Expected: ok + 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/notify_service.py app/routers/videos.py
git commit -m "feat: 발송 경로에 채널명·태그·message_detail 주입 + 태그 조회"
```

---

## Task 5: 분석 직후 발송(_notify_after_analysis) 주입

**Files:**
- Modify: `app/services/monitor_service.py` (_notify_after_analysis)

- [ ] **Step 1: 구현**

`_notify_after_analysis`에서 이미 `channel`, `video`, `analysis`, `make_session`, `notif`가 로컬에 있다. 발송 호출 `sent = await notify_video(notif, video, analysis, threshold=notif.low_confidence_threshold)`를 교체:

```python
            from app.services.notify_service import _fetch_video_tags
            try:
                tags = await _fetch_video_tags(make_session, video_pk)
            except Exception:
                tags = []
            sent = await notify_video(
                notif, video, analysis, threshold=notif.low_confidence_threshold,
                channel_name=getattr(channel, "channel_name", "") or "",
                tags=tags, detail=notif.message_detail,
            )
```

READ `_notify_after_analysis` first to confirm `channel` variable name (it is loaded as `channel` earlier in the function) and indentation context.

- [ ] **Step 2: 회귀 확인**

Run: `python -m pytest -q`
Expected: PASS (기존 test_notify_baseline 포함)

- [ ] **Step 3: Commit**

```bash
git add app/services/monitor_service.py
git commit -m "feat: 분석 직후 발송에 채널명·태그·message_detail 주입"
```

---

## Task 6: 프론트엔드 message_detail 필드

**Files:**
- Modify: `frontend/src/settings/defs.ts`

- [ ] **Step 1: notification 필드에 select 추가**

`frontend/src/settings/defs.ts`의 `SETTING_DEFS.notification` 배열에서 `low_confidence_threshold` 항목 다음에 추가:

```typescript
    { key: 'message_detail', label: '메시지 상세도', type: 'select', options: ['full', 'compact'], help: 'full=전체 분석·핵심주장·태그 포함, compact=한줄+짧은요약' },
```

- [ ] **Step 2: 타입체크/빌드**

Run: `cd frontend && npx tsc --noEmit && npx vitest run`
Expected: tsc 클린, 테스트 통과 (select 타입은 기존 지원, convert 변경 불필요 — 기본 string 처리)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/settings/defs.ts
git commit -m "feat: 알림 설정에 메시지 상세도(full/compact) 필드"
```

---

## Task 7: digest 순수 헬퍼 (videos_block/entities/카테고리/감성)

**Files:**
- Modify: `app/services/digest_service.py` (헬퍼 + VideoBrief + 상수)
- Test: `tests/test_digest_helpers.py` (생성)

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_digest_helpers.py` 생성:

```python
"""digest 순수 헬퍼 검증."""

from app.services.digest_service import (
    VideoBrief,
    split_category_tokens,
    _format_entities,
    _build_videos_block,
    _sentiment_summary_text,
)


def test_split_category_tokens():
    assert split_category_tokens("경제, 투자, 재테크") == ["경제", "투자", "재테크"]
    assert split_category_tokens("투자, 투자 ,재테크") == ["투자", "재테크"]
    assert split_category_tokens("") == []
    assert split_category_tokens(None) == []


def test_format_entities():
    ents = [{"type": "company", "name": "삼성전자"}, {"type": "ticker", "name": "NVDA"}]
    assert _format_entities(ents) == "삼성전자, NVDA"
    assert _format_entities(["연준", "금리"]) == "연준, 금리"
    assert _format_entities(None) == ""


def test_sentiment_summary_text():
    txt = _sentiment_summary_text({"bullish": 3, "bearish": 1})
    assert "긍정 3" in txt and "부정 1" in txt


def test_build_videos_block():
    briefs = [
        VideoBrief(channel_name="A채널", headline="헤드", one_line="한줄", title="t",
                   sentiment="bullish", bullet_points=["주장1", "주장2"],
                   insights=["인사이트1"], entities=[{"type": "company", "name": "삼성전자"}]),
    ]
    block = _build_videos_block(briefs, total=1)
    assert "[A채널] 헤드 (논조: 긍정)" in block
    assert "• 주장1" in block
    assert "▶ 인사이트: 인사이트1" in block
    assert "· 등장: 삼성전자" in block


def test_build_videos_block_remaining():
    briefs = [
        VideoBrief(channel_name=f"C{i}", headline=f"h{i}", one_line=None, title=None,
                   sentiment="neutral", bullet_points=None, insights=None, entities=None)
        for i in range(45)
    ]
    block = _build_videos_block(briefs, total=45)
    assert "외 5건" in block  # 40개 표시 + 나머지 5
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_digest_helpers.py -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: 구현**

`app/services/digest_service.py`에 추가. 기존 `@dataclass class DigestAggregate` 위/근처에 `VideoBrief`와 상수·헬퍼 추가:

```python
_MAX_VIDEOS_IN_PROMPT = 40
_MAX_BULLETS_PER_VIDEO = 3
_MAX_INSIGHTS_PER_VIDEO = 3
_MAX_ENTITIES_PER_VIDEO = 6

_SENTIMENT_KO = {
    "bullish": "긍정",
    "bearish": "부정",
    "neutral": "중립",
    "mixed": "혼조",
    "unknown": "미상",
}


@dataclass
class VideoBrief:
    channel_name: str
    headline: Optional[str]
    one_line: Optional[str]
    title: Optional[str]
    sentiment: Optional[str]
    bullet_points: Optional[Any] = None
    insights: Optional[Any] = None
    entities: Optional[Any] = None


def split_category_tokens(raw: Optional[str]) -> list[str]:
    s = (raw or "").strip()
    if not s:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for part in s.split(","):
        t = part.strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _format_entities(entities: Optional[Any]) -> str:
    if not isinstance(entities, list):
        return ""
    names: list[str] = []
    for e in entities:
        name = str(e.get("name") if isinstance(e, dict) else e or "").strip()
        if name:
            names.append(name)
        if len(names) >= _MAX_ENTITIES_PER_VIDEO:
            break
    return ", ".join(names)


def _sentiment_summary_text(breakdown: dict) -> str:
    parts = []
    for key in ("bullish", "bearish", "neutral", "mixed", "unknown"):
        if breakdown.get(key):
            parts.append(f"{_SENTIMENT_KO[key]} {breakdown[key]}")
    return ", ".join(parts) if parts else "데이터 없음"


def _build_videos_block(videos: list["VideoBrief"], total: int) -> str:
    lines: list[str] = []
    shown = videos[:_MAX_VIDEOS_IN_PROMPT]
    for v in shown:
        head = (v.headline or v.one_line or v.title or "").strip()
        senti = _SENTIMENT_KO.get(v.sentiment or "unknown", v.sentiment or "미상")
        lines.append(f"- [{v.channel_name}] {head} (논조: {senti})")
        if v.one_line and v.one_line.strip() and v.one_line.strip() != head:
            lines.append(f"  {v.one_line.strip()}")
        bullets = v.bullet_points if isinstance(v.bullet_points, list) else []
        for b in bullets[:_MAX_BULLETS_PER_VIDEO]:
            s = str(b).strip()
            if s:
                lines.append(f"  • {s}")
        insights = v.insights if isinstance(v.insights, list) else []
        for ins in insights[:_MAX_INSIGHTS_PER_VIDEO]:
            s = str(ins).strip()
            if s:
                lines.append(f"  ▶ 인사이트: {s}")
        ent = _format_entities(v.entities)
        if ent:
            lines.append(f"  · 등장: {ent}")
    remaining = total - len(shown)
    if remaining > 0:
        lines.append(f"... 외 {remaining}건")
    return "\n".join(lines)
```

READ the top of `digest_service.py` to confirm `from dataclasses import dataclass` and `from typing import Any, Optional` are imported (add `Optional` to the typing import if missing).

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_digest_helpers.py -v && python -m pytest -q`
Expected: PASS (전체)

- [ ] **Step 5: Commit**

```bash
git add app/services/digest_service.py tests/test_digest_helpers.py
git commit -m "feat: digest videos_block/entities/카테고리 토큰/감성 헬퍼 포팅"
```

---

## Task 8: aggregate_period 확장 (VideoBrief 수집 + 카테고리 토큰 매칭)

**Files:**
- Modify: `app/services/digest_service.py` (DigestAggregate, aggregate_period)

설명: DB 의존 — 신규 유닛 테스트 없음. import/스위트 통과로 검증.

- [ ] **Step 1: DigestAggregate에 videos 필드 추가**

`DigestAggregate` dataclass에 필드 추가:

```python
@dataclass
class DigestAggregate:
    video_count: int
    sentiment_breakdown: dict[str, int]
    top_tags: list[dict[str, Any]]
    top_channels: list[dict[str, Any]]
    videos: list["VideoBrief"] = field(default_factory=list)
```

READ the imports: ensure `from dataclasses import dataclass, field` (add `field` if missing).

- [ ] **Step 2: aggregate_period 쿼리·매칭 교체**

`aggregate_period`를 아래로 교체(영상별 brief 수집 + 카테고리 토큰 매칭). 기존 시그니처 유지:

```python
async def aggregate_period(
    session: AsyncSession,
    period_start: datetime,
    period_end: datetime,
    category: str = "",
) -> DigestAggregate:
    rows = (
        await session.execute(
            select(
                Video.video_pk,
                VideoAnalysis.sentiment,
                VideoAnalysis.headline,
                VideoAnalysis.one_line,
                Video.title,
                VideoAnalysis.bullet_points,
                VideoAnalysis.insights,
                VideoAnalysis.entities,
                Channel.channel_name,
                Channel.category,
            )
            .join(VideoAnalysis, VideoAnalysis.video_pk == Video.video_pk)
            .join(Channel, Channel.channel_pk == Video.channel_pk)
            .where(VideoAnalysis.analyzed_at >= period_start, VideoAnalysis.analyzed_at < period_end)
            .order_by(VideoAnalysis.analyzed_at.desc())
        )
    ).all()

    want = category.strip()
    selected = []
    for r in rows:
        if want:
            ch_tokens = split_category_tokens(r.category)
            if want not in ch_tokens:
                continue
        selected.append(r)

    video_pks = [r.video_pk for r in selected]
    sentiment: dict[str, int] = {}
    channel_count: dict[str, int] = {}
    videos: list[VideoBrief] = []
    for r in selected:
        key = (r.sentiment or "unknown").strip() or "unknown"
        sentiment[key] = sentiment.get(key, 0) + 1
        cname = (r.channel_name or "(알 수 없음)").strip() or "(알 수 없음)"
        channel_count[cname] = channel_count.get(cname, 0) + 1
        videos.append(VideoBrief(
            channel_name=cname, headline=r.headline, one_line=r.one_line, title=r.title,
            sentiment=r.sentiment, bullet_points=r.bullet_points,
            insights=r.insights, entities=r.entities,
        ))

    top_channels = [
        {"name": name, "count": count}
        for name, count in sorted(channel_count.items(), key=lambda x: (-x[1], x[0]))[:10]
    ]

    top_tags: list[dict[str, Any]] = []
    if video_pks:
        tag_rows = (
            await session.execute(
                select(Tag.name, func.count(VideoTag.video_pk))
                .join(VideoTag, VideoTag.tag_pk == Tag.tag_pk)
                .where(VideoTag.video_pk.in_(video_pks))
                .group_by(Tag.name)
                .order_by(func.count(VideoTag.video_pk).desc(), Tag.name.asc())
                .limit(20)
            )
        ).all()
        top_tags = [{"name": n, "count": int(c)} for n, c in tag_rows]

    return DigestAggregate(
        video_count=len(video_pks),
        sentiment_breakdown=sentiment,
        top_tags=top_tags,
        top_channels=top_channels,
        videos=videos,
    )
```

- [ ] **Step 3: 회귀/무결성**

Run: `python -c "import app.services.digest_service; print('ok')" && python -m pytest -q`
Expected: ok + PASS

- [ ] **Step 4: Commit**

```bash
git add app/services/digest_service.py
git commit -m "feat: digest aggregate_period가 영상별 brief 수집 + 카테고리 토큰 매칭"
```

---

## Task 9: synthesize_with_llm가 videos_block을 .format() 치환

**Files:**
- Modify: `app/services/digest_service.py` (synthesize_with_llm)

설명: DB/LLM 의존 — 구현 + 수동 검증.

- [ ] **Step 1: period_label 헬퍼 추가**

`digest_service.py`에 추가:

```python
def _period_label(period_start: datetime, period_end: datetime) -> str:
    return f"{period_start.date()} ~ {period_end.date()}"
```

- [ ] **Step 2: synthesize_with_llm 프롬프트 구성 교체**

`synthesize_with_llm`에서 `context_json = _render_payload(...)`와 `user_msg = f"{prompt}\n\n집계 데이터:\n{context_json}"` 부분을 교체:

```python
    period_label = _period_label(period_start, period_end)
    try:
        user_msg = prompt.format(
            category=category or "전체",
            period_label=period_label,
            video_count=aggregate.video_count,
            sentiment_summary=_sentiment_summary_text(aggregate.sentiment_breakdown),
            top_tags=", ".join(t["name"] for t in aggregate.top_tags[:8]) or "없음",
            videos_block=_build_videos_block(aggregate.videos, aggregate.video_count),
        )
    except (KeyError, IndexError, ValueError):
        # 프롬프트에 알 수 없는 placeholder가 있으면 안전 폴백(발송 자체는 막지 않음).
        context_json = _render_payload(aggregate, period_start, period_end, category)
        videos_block = _build_videos_block(aggregate.videos, aggregate.video_count)
        user_msg = f"{prompt}\n\n집계 데이터:\n{context_json}\n\n영상별 자료:\n{videos_block}"
```

READ `synthesize_with_llm` first to confirm the exact lines and that `prompt`, `category`, `period_start`, `period_end`, `aggregate` are in scope. Keep the rest of the function (LLM 호출/JSON 파싱/반환) unchanged.

- [ ] **Step 3: 회귀/무결성**

Run: `python -c "import app.services.digest_service; print('ok')" && python -m pytest -q`
Expected: ok + PASS

- [ ] **Step 4: 수동 검증 (DB 환경)**

앱 기동 후, 한 그룹에서 digest 1회 생성(스케줄 시각 도달 또는 수동 트리거)하여:
- 생성된 `summary_md`가 영상별 구체 내용을 반영하는지(이전의 일반론 대비).
- 텔레그램 영상 알림이 full 포맷(채널·핵심주장·태그·날짜)으로 오는지.
- compact로 바꾸면 간략 포맷으로 오는지.

- [ ] **Step 5: Commit**

```bash
git add app/services/digest_service.py
git commit -m "feat: digest 프롬프트에 videos_block 등 .format() 치환(영상 내용 주입)"
```

---

## Self-Review (작성자 체크 결과)

**Spec coverage**
- A-1 build_message full/compact: Task 3 ✓
- A-2 스마트 절단: Task 3(_build_full) ✓
- A-3 호출부 채널명·태그·detail: Task 4·5, videos.py Task 4 ✓
- A-4 message_detail 설정: Task 1, 프론트 Task 6 ✓
- B-1 VideoBrief/aggregate_period: Task 7·8 ✓
- B-2 _build_videos_block/_format_entities: Task 7 ✓
- B-3 .format() 치환: Task 9 ✓
- B-4 카테고리 토큰 정규화: Task 7(split)·8(매칭) ✓

**Placeholder scan**: 모든 코드 스텝에 실제 코드 포함. READ 지시는 기존 코드 확인용.

**Type consistency**: `build_message(video, analysis, threshold, *, channel_name, tags, detail)` / `notify_video(..., channel_name, tags, detail)` / `_fetch_video_tags(make_session, video_pk, limit)` / `VideoBrief(channel_name, headline, one_line, title, sentiment, bullet_points, insights, entities)` / `_build_videos_block(videos, total)` / `split_category_tokens` / `_sentiment_summary_text(breakdown)` — 전 태스크 일관. notify_pending_batch candidates를 `(v,a,ch)` 3-튜플로 바꾸는 변경을 Task 4에 명시.
