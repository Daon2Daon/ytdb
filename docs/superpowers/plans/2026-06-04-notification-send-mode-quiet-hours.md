# 알림 발송 고도화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ytdb 알림에 발송 모드(즉시/예약), 야간 알림 제한, 예약 회당 제한, 야간 보정 발송, 그룹별 타임존, 저신뢰도 배지를 그룹별 설정으로 추가한다.

**Architecture:** 모든 신규 설정은 `notification` 카테고리의 key/value로 control DB에 저장(스키마 변경 없음). 발송은 youtube_monitor의 동적 Cron 대신 ytdb 패턴인 매 1분 틱(`run_notify_tick_once`)이 활성 그룹을 순회하며 처리한다. 즉시발송 경로는 예약 모드·야간 제한 시 보류하고, 틱이 예약 시각·야간 종료 후 보정 발송을 담당한다. UI는 기존 범용 폼(`SETTING_DEFS`)에 `time`/`timelist` 필드 타입과 `showIf` 조건부 표시를 추가해 확장한다.

**Tech Stack:** Python 3 / FastAPI / SQLAlchemy async / APScheduler(AsyncIOScheduler) / pytest. Frontend: React + TypeScript + Vite + vitest.

설계 출처: `docs/superpowers/specs/2026-06-04-notification-send-mode-quiet-hours-design.md`

---

## File Structure

**Backend**
- Create: `app/services/quiet_hours.py` — 야간 제한 판정 헬퍼(타임존 인자화). 순수 함수.
- Modify: `app/services/settings_types.py` — `NotificationSettings`에 9개 필드 추가.
- Modify: `app/services/settings_manager.py` — `get_notification`이 신규 필드 채움.
- Modify: `app/services/notify_service.py` — `build_message` 저신뢰도 배지, `notify_video` threshold 전달, `notify_pending_batch`/`run_notify_tick_once`/`_matches_scheduled_time` 추가.
- Modify: `app/services/monitor_service.py` — `_notify_after_analysis`에 예약/야간 게이트 추가.
- Modify: `app/services/scheduler.py` — 1분 `youtube_notify_tick` 잡 등록.
- Modify: `app/services/default_settings.py` — notification 기본 시드 추가.

**Backend tests**
- Create: `tests/test_quiet_hours.py`
- Create: `tests/test_notify_schedule.py` — `_matches_scheduled_time`, `build_message` 배지.
- Create: `tests/test_notification_settings_defaults.py` — dataclass 기본값.

**Frontend**
- Modify: `frontend/src/settings/defs.ts` — `FieldType` 확장, `showIf` 추가, notification 필드.
- Modify: `frontend/src/settings/convert.ts` — `time`/`timelist` 환산.
- Modify: `frontend/src/components/SettingsForm.tsx` — `time`/`timelist` 렌더, `showIf` 평가.

**Frontend tests**
- Modify: `frontend/src/settings/convert.test.ts` — `time`/`timelist` 라운드트립.

---

## Task 1: 야간 제한 헬퍼 (quiet_hours.py)

**Files:**
- Create: `app/services/quiet_hours.py`
- Test: `tests/test_quiet_hours.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_quiet_hours.py
"""야간(지정 시간대) 발송 제한 판정. 타임존 인자화."""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.quiet_hours import is_in_quiet_hours, is_quiet_hours_now

KST = ZoneInfo("Asia/Seoul")


def _at(h: int, m: int) -> datetime:
    return datetime(2026, 6, 4, h, m, tzinfo=KST)


def test_same_day_window():
    # 09:00~17:00 → 12:00 제한, 08:00 비제한
    assert is_in_quiet_hours("09:00", "17:00", tz=KST, now=_at(12, 0)) is True
    assert is_in_quiet_hours("09:00", "17:00", tz=KST, now=_at(8, 0)) is False


def test_overnight_window():
    # 22:00~07:00 → 23:00·03:00 제한, 12:00 비제한
    assert is_in_quiet_hours("22:00", "07:00", tz=KST, now=_at(23, 0)) is True
    assert is_in_quiet_hours("22:00", "07:00", tz=KST, now=_at(3, 0)) is True
    assert is_in_quiet_hours("22:00", "07:00", tz=KST, now=_at(12, 0)) is False


def test_boundaries_half_open():
    # [start, end): start 포함, end 제외
    assert is_in_quiet_hours("22:00", "07:00", tz=KST, now=_at(22, 0)) is True
    assert is_in_quiet_hours("22:00", "07:00", tz=KST, now=_at(7, 0)) is False


def test_all_day_when_equal():
    assert is_in_quiet_hours("00:00", "00:00", tz=KST, now=_at(15, 0)) is True


def test_is_quiet_hours_now_disabled_is_false():
    assert is_quiet_hours_now(False, "22:00", "07:00", tz=KST, now=_at(23, 0)) is False


def test_is_quiet_hours_now_enabled_delegates():
    assert is_quiet_hours_now(True, "22:00", "07:00", tz=KST, now=_at(23, 0)) is True


def test_invalid_format_is_safe_false():
    # 형식 오류 → 발송 허용(False)
    assert is_quiet_hours_now(True, "bad", "07:00", tz=KST, now=_at(23, 0)) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mukymook/Library/CloudStorage/SynologyDrive-mookmuky/04.Coding/ytdb && .venv/bin/pytest tests/test_quiet_hours.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.quiet_hours'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/services/quiet_hours.py
"""야간(지정 시간대) Telegram 발송 제한 판정.

타임존을 인자로 받아 그룹별 설정을 지원한다(youtube_monitor의 KST 고정 버전 이식).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def _minutes_from_hhmm(hhmm: str) -> int:
    parts = (hhmm or "").strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"HH:MM 형식이 아님: {hhmm!r}")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"시각 범위 오류: {hhmm!r}")
    return hour * 60 + minute


def is_in_quiet_hours(
    start_hhmm: str,
    end_hhmm: str,
    *,
    tz: ZoneInfo,
    now: datetime | None = None,
) -> bool:
    """현재 시각이 [start, end) 제한 구간에 포함되는지.

    - start < end: 같은 날 구간
    - start > end: 자정을 넘는 구간
    - start == end: 종일 제한
    """
    local = (now or datetime.now(tz)).astimezone(tz)
    cur = local.hour * 60 + local.minute
    start = _minutes_from_hhmm(start_hhmm)
    end = _minutes_from_hhmm(end_hhmm)
    if start == end:
        return True
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end


def is_quiet_hours_now(
    enabled: bool,
    start_hhmm: str,
    end_hhmm: str,
    *,
    tz: ZoneInfo,
    now: datetime | None = None,
) -> bool:
    if not enabled:
        return False
    try:
        return is_in_quiet_hours(start_hhmm, end_hhmm, tz=tz, now=now)
    except ValueError:
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_quiet_hours.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add app/services/quiet_hours.py tests/test_quiet_hours.py
git commit -m "feat: 야간 알림 제한 판정 헬퍼(타임존 인자화)"
```

---

## Task 2: NotificationSettings 필드 확장

**Files:**
- Modify: `app/services/settings_types.py` (NotificationSettings)
- Test: `tests/test_notification_settings_defaults.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_notification_settings_defaults.py
"""NotificationSettings 신규 필드 기본값/하위호환 검증."""

from app.services.settings_types import NotificationSettings


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


def test_is_sendable_unchanged():
    # 기존 의미 유지: enabled + bot_token + chat_ids
    assert NotificationSettings().is_sendable is False
    n = NotificationSettings(enabled=True, bot_token="t", chat_ids=["1"])
    assert n.is_sendable is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_notification_settings_defaults.py -v`
Expected: FAIL — `AttributeError`/`TypeError` (신규 필드 없음)

- [ ] **Step 3: Write minimal implementation**

`app/services/settings_types.py`의 `NotificationSettings` dataclass를 아래로 교체(기존 필드 + 신규 9개, `field` import는 이미 있음):

```python
@dataclass
class NotificationSettings:
    """그룹별 텔레그램 알림 설정.

    chat_ids가 비어 있으면 발송하지 않고 분석/데이터만 기록한다.
    chat_ids에 여러 대상을 넣으면 그룹 단위로 복수 채널에 발송한다.
    """

    enabled: bool = True
    bot_token: str = ""
    chat_ids: list[str] = field(default_factory=list)
    parse_mode: str = "HTML"
    # 발송 모드/예약
    send_mode: str = "immediate"  # immediate | scheduled
    scheduled_times: list[str] = field(default_factory=list)  # HH:MM, 최대 10
    scheduled_max_per_run: int = 5
    wait_between_messages_sec: int = 30
    # 야간 제한
    quiet_hours_enabled: bool = False
    quiet_hours_start: str = "22:00"
    quiet_hours_end: str = "07:00"
    timezone: str = "Asia/Seoul"
    # 표시
    low_confidence_threshold: float = 0.5

    @property
    def is_sendable(self) -> bool:
        return bool(self.enabled and self.bot_token and self.chat_ids)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_notification_settings_defaults.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add app/services/settings_types.py tests/test_notification_settings_defaults.py
git commit -m "feat: NotificationSettings에 발송모드·야간제한·저신뢰도 필드 추가"
```

---

## Task 3: get_notification 신규 필드 로드

**Files:**
- Modify: `app/services/settings_manager.py` (get_notification)

설명: DB 의존이라 별도 유닛 테스트는 두지 않고(기존에도 get_notification 테스트 없음), Task 2의 dataclass 기본값이 None/빈값 폴백을 보장한다. `_as_int`/`_as_float`는 이미 존재.

- [ ] **Step 1: 구현 — get_notification 반환부 확장**

`app/services/settings_manager.py`의 `get_notification` 마지막 `return NotificationSettings(...)`를 아래로 교체. `_as_int`, `_as_float`는 파일 상단에 이미 정의되어 있다.

```python
        raw_times = d.get("scheduled_times")
        scheduled_times: list[str] = []
        if isinstance(raw_times, list):
            scheduled_times = [str(x).strip() for x in raw_times if str(x).strip()]
        elif isinstance(raw_times, str) and raw_times.strip():
            scheduled_times = [p.strip() for p in raw_times.split(",") if p.strip()]
        return NotificationSettings(
            enabled=bool(d.get("enabled", True)),
            bot_token=str(d.get("bot_token") or ""),
            chat_ids=chat_ids,
            parse_mode=str(d.get("parse_mode") or "HTML"),
            send_mode=str(d.get("send_mode") or "immediate"),
            scheduled_times=scheduled_times,
            scheduled_max_per_run=_as_int(d.get("scheduled_max_per_run"), 5),
            wait_between_messages_sec=_as_int(d.get("wait_between_messages_sec"), 30),
            quiet_hours_enabled=bool(d.get("quiet_hours_enabled", False)),
            quiet_hours_start=str(d.get("quiet_hours_start") or "22:00"),
            quiet_hours_end=str(d.get("quiet_hours_end") or "07:00"),
            timezone=str(d.get("timezone") or "Asia/Seoul"),
            low_confidence_threshold=_as_float(d.get("low_confidence_threshold"), 0.5),
        )
```

- [ ] **Step 2: 회귀 확인 — 전체 테스트**

Run: `.venv/bin/pytest -q`
Expected: PASS (기존 + 신규 통과, 신규 import 오류 없음)

- [ ] **Step 3: Commit**

```bash
git add app/services/settings_manager.py
git commit -m "feat: get_notification이 발송모드·야간제한 신규 필드를 로드"
```

---

## Task 4: 저신뢰도 배지 (build_message)

**Files:**
- Modify: `app/services/notify_service.py` (build_message, notify_video)
- Test: `tests/test_notify_schedule.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_notify_schedule.py
"""저신뢰도 배지 및 예약 시각 매칭 검증."""

from types import SimpleNamespace

from app.services.notify_service import build_message


def _video():
    return SimpleNamespace(title="제목", video_url="https://youtu.be/x")


def _analysis(conf):
    return SimpleNamespace(
        headline="헤드라인",
        one_line="한줄",
        short_summary_md="요약",
        sentiment="중립",
        confidence_score=conf,
    )


def test_badge_added_below_threshold():
    msg = build_message(_video(), _analysis(0.3), threshold=0.5)
    assert msg.startswith("<b>⚠️ ")


def test_no_badge_at_or_above_threshold():
    msg = build_message(_video(), _analysis(0.7), threshold=0.5)
    assert "⚠️" not in msg.split("\n")[0]


def test_no_badge_when_confidence_none():
    msg = build_message(_video(), _analysis(None), threshold=0.5)
    assert "⚠️" not in msg.split("\n")[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_notify_schedule.py -v`
Expected: FAIL — `TypeError: build_message() got an unexpected keyword argument 'threshold'`

- [ ] **Step 3: Write minimal implementation**

`app/services/notify_service.py`의 `build_message`를 교체(시그니처에 `threshold` 추가, 제목 배지):

```python
def build_message(video: Video, analysis: VideoAnalysis, threshold: float = 0.0) -> str:
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
    text = "\n".join(lines)
    return text[:_MAX_LEN]
```

이어서 같은 파일 `notify_video` 시그니처에 `threshold` 추가하고 `build_message` 호출에 전달:

```python
async def notify_video(
    notif: NotificationSettings,
    video: Video,
    analysis: VideoAnalysis,
    client: Optional[httpx.AsyncClient] = None,
    threshold: float = 0.0,
) -> int:
    """그룹의 모든 chat_id에 발송. 성공 건수 반환. 일부 실패해도 나머지는 계속 시도."""
    if not notif.is_sendable:
        return 0
    text = build_message(video, analysis, threshold)
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

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_notify_schedule.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add app/services/notify_service.py tests/test_notify_schedule.py
git commit -m "feat: 저신뢰도 임계값 미만 알림에 ⚠️ 배지 추가"
```

---

## Task 5: 예약 시각 매칭 헬퍼 (_matches_scheduled_time)

**Files:**
- Modify: `app/services/notify_service.py` (_matches_scheduled_time 추가)
- Test: `tests/test_notify_schedule.py` (추가)

- [ ] **Step 1: Write the failing test (기존 파일에 추가)**

`tests/test_notify_schedule.py` 하단에 추가:

```python
from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.notify_service import _matches_scheduled_time

KST = ZoneInfo("Asia/Seoul")


def test_scheduled_match_exact_minute():
    now = datetime(2026, 6, 4, 14, 0, tzinfo=KST)
    assert _matches_scheduled_time(now, ["09:00", "14:00"]) is True


def test_scheduled_no_match():
    now = datetime(2026, 6, 4, 14, 1, tzinfo=KST)
    assert _matches_scheduled_time(now, ["14:00"]) is False


def test_scheduled_empty_list_false():
    now = datetime(2026, 6, 4, 14, 0, tzinfo=KST)
    assert _matches_scheduled_time(now, []) is False


def test_scheduled_ignores_bad_entries():
    now = datetime(2026, 6, 4, 14, 0, tzinfo=KST)
    assert _matches_scheduled_time(now, ["bad", "14:00"]) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_notify_schedule.py -k scheduled -v`
Expected: FAIL — `ImportError: cannot import name '_matches_scheduled_time'`

- [ ] **Step 3: Write minimal implementation**

`app/services/notify_service.py` 상단 import 근처에 `from datetime import datetime` 추가(없으면), 그리고 함수 추가:

```python
def _matches_scheduled_time(now_local: "datetime", scheduled_times: list[str]) -> bool:
    """now_local의 HH:MM이 예약 시각 목록 중 하나와 분 단위로 일치하는지."""
    cur = f"{now_local.hour:02d}:{now_local.minute:02d}"
    valid = set()
    for t in scheduled_times:
        parts = str(t).strip().split(":")
        if len(parts) != 2:
            continue
        try:
            h, m = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if 0 <= h <= 23 and 0 <= m <= 59:
            valid.add(f"{h:02d}:{m:02d}")
    return cur in valid
```

파일 상단에 `from datetime import datetime` import가 없다면 추가한다(타입 힌트 문자열이라 런타임 필수는 아니지만 명시).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_notify_schedule.py -k scheduled -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add app/services/notify_service.py tests/test_notify_schedule.py
git commit -m "feat: 예약 발송 시각 분단위 매칭 헬퍼 추가"
```

---

## Task 6: 미발송 배치 발송 (notify_pending_batch)

**Files:**
- Modify: `app/services/notify_service.py` (notify_pending_batch 추가)

설명: DB(데이터 평면) 의존이라 유닛 테스트 대신 구현 후 Task 10에서 수동 검증한다. baseline 게이트는 `monitor_service._passes_notify_baseline`를 재사용한다(DRY).

- [ ] **Step 1: 구현 — notify_pending_batch 추가**

`app/services/notify_service.py` 상단 import에 다음 추가:

```python
import asyncio
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pg.channel import Channel
from app.services.job_logger import (
    JOB_TYPE_NOTIFY,
    STATUS_FAIL,
    STATUS_SUCCESS,
    JobTimer,
    write_job_log,
)

MakeSession = Callable[[], AsyncSession]
```

파일 하단에 함수 추가:

```python
async def notify_pending_batch(
    notif: NotificationSettings,
    make_session: MakeSession,
    *,
    max_per: int,
    wait_sec: int,
    threshold: float,
    log_label: str,
) -> int:
    """미발송·분석완료 영상을 오래된 순으로 배치 발송한다.

    대상: analysis_status='done' AND notified_at IS NULL AND 채널 notify_enabled
          AND baseline(notify_from) 통과. 최대 max_per건, 건당 wait_sec 대기.
    각 성공 건은 notified_at을 기록한다. 배치 종료 후 job_log 1건 기록.
    반환: 성공 발송 건수.
    """
    from app.services.monitor_service import _passes_notify_baseline

    max_per = max(1, min(50, int(max_per)))

    async with make_session() as sess:
        rows = (
            await sess.execute(
                select(Video, VideoAnalysis, Channel)
                .join(VideoAnalysis, VideoAnalysis.video_pk == Video.video_pk)
                .join(Channel, Channel.channel_pk == Video.channel_pk)
                .where(Video.analysis_status == "done")
                .where(Video.notified_at.is_(None))
                .where(Channel.notify_enabled.is_(True))
                .order_by(Video.published_at.asc())
            )
        ).all()

    candidates = [
        (v, a)
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
                for i, (video, analysis) in enumerate(batch):
                    try:
                        ok = await notify_video(notif, video, analysis, client, threshold)
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

- [ ] **Step 2: 회귀 확인 (import/문법)**

Run: `.venv/bin/pytest -q`
Expected: PASS (순환 import 없음 — `_passes_notify_baseline`는 함수 내부 지연 import)

- [ ] **Step 3: Commit**

```bash
git add app/services/notify_service.py
git commit -m "feat: 미발송 영상 배치 발송 함수(notify_pending_batch)"
```

---

## Task 7: 발송 틱 (run_notify_tick_once)

**Files:**
- Modify: `app/services/notify_service.py` (run_notify_tick_once 추가)

설명: digest_tick 패턴을 따른다. 활성 그룹 순회 + 그룹별 데이터 평면 세션. DB 의존이라 Task 10에서 수동 검증.

- [ ] **Step 1: 구현 — run_notify_tick_once 추가**

`app/services/notify_service.py` 하단에 추가:

```python
async def run_notify_tick_once() -> None:
    """매 1분 호출. 활성 그룹별로 예약발송/야간 보정 발송을 수행한다.

    - scheduled 모드: 그룹 tz 기준 현재 분이 예약 시각과 일치하고, 야간 제한 중이
      아니면 배치 발송.
    - immediate 모드 + 야간 제한 활성: 야간이 끝난 뒤(현재 비-야간) 보류분을 보정 발송.
    """
    from zoneinfo import ZoneInfo

    from app.control_db import get_sessionmaker
    from app.models.control.group import Group
    from app.services.db_engine import (
        DBNotConfiguredError,
        data_plane_engine_manager as dpm,
    )
    from app.services.quiet_hours import is_quiet_hours_now
    from app.services.settings_manager import get_settings_manager

    sf = get_sessionmaker()
    mgr = get_settings_manager()
    async with sf() as session:
        groups = list(
            (await session.execute(select(Group).where(Group.is_active.is_(True))))
            .scalars()
            .all()
        )

    for group in groups:
        try:
            notif = await mgr.get_notification(group.group_id)
            if not notif.is_sendable:
                continue
            try:
                tz = ZoneInfo(notif.timezone)
            except Exception:
                tz = ZoneInfo("Asia/Seoul")
            now_local = datetime.now(tz)
            quiet_now = is_quiet_hours_now(
                notif.quiet_hours_enabled,
                notif.quiet_hours_start,
                notif.quiet_hours_end,
                tz=tz,
                now=now_local,
            )

            if notif.send_mode == "scheduled":
                if quiet_now:
                    continue
                if not _matches_scheduled_time(now_local, notif.scheduled_times):
                    continue
                log_label = "예약발송 회차"
            elif notif.send_mode == "immediate":
                if not notif.quiet_hours_enabled or quiet_now:
                    continue
                log_label = "야간 보정 발송"
            else:
                continue

            try:
                engine = await dpm.get_engine_for_group(group)
            except DBNotConfiguredError:
                continue
            make_session = lambda: dpm.session_for_group(engine, group.schema_name)
            await notify_pending_batch(
                notif,
                make_session,
                max_per=notif.scheduled_max_per_run,
                wait_sec=notif.wait_between_messages_sec,
                threshold=notif.low_confidence_threshold,
                log_label=log_label,
            )
        except DBNotConfiguredError:
            continue
        except Exception as e:
            print(f"[{group.slug}] notify tick 실패: {e}")
```

참고: monitor_service의 `_poll_group`에서 `dpm.get_engine_for_group`/`dpm.session_for_group` 사용법이 동일하다.

- [ ] **Step 2: 회귀 확인**

Run: `.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add app/services/notify_service.py
git commit -m "feat: 1분 틱 발송 처리(run_notify_tick_once)"
```

---

## Task 8: 즉시발송 경로 게이트 (_notify_after_analysis)

**Files:**
- Modify: `app/services/monitor_service.py` (_notify_after_analysis)

설명: 예약 모드/야간 제한 시 즉시발송을 보류한다. 발송 시 threshold 전달.

- [ ] **Step 1: 구현 — get_notification 직후 게이트 추가**

`app/services/monitor_service.py`의 `_notify_after_analysis` 내 다음 블록:

```python
    notif = await get_settings_manager().get_notification(group.group_id)
    if not notif.is_sendable:
        return
```

바로 아래에 게이트 추가:

```python
    # 예약 발송 모드: 즉시 발송하지 않고 보류(틱이 예약 시각에 일괄 발송).
    if notif.send_mode == "scheduled":
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_NOTIFY,
            status=STATUS_SKIP,
            message="예약발송 대기(send_mode=scheduled)",
            channel_pk=channel_pk,
            video_pk=video_pk,
        )
        return

    # 즉시 발송 + 야간 제한: 보류(틱이 제한 종료 후 보정 발송).
    if notif.quiet_hours_enabled:
        from zoneinfo import ZoneInfo

        from app.services.quiet_hours import is_quiet_hours_now

        try:
            tz = ZoneInfo(notif.timezone)
        except Exception:
            tz = ZoneInfo("Asia/Seoul")
        if is_quiet_hours_now(
            notif.quiet_hours_enabled,
            notif.quiet_hours_start,
            notif.quiet_hours_end,
            tz=tz,
        ):
            await write_job_log(
                make_session,
                job_type=JOB_TYPE_NOTIFY,
                status=STATUS_SKIP,
                message="야간 보류(quiet hours)",
                channel_pk=channel_pk,
                video_pk=video_pk,
            )
            return
```

- [ ] **Step 2: 구현 — 발송 호출에 threshold 전달**

같은 함수에서 `sent = await notify_video(notif, video, analysis)`를 교체:

```python
            sent = await notify_video(
                notif, video, analysis, threshold=notif.low_confidence_threshold
            )
```

- [ ] **Step 3: 회귀 확인**

Run: `.venv/bin/pytest -q`
Expected: PASS (기존 test_notify_baseline 포함 통과)

- [ ] **Step 4: Commit**

```bash
git add app/services/monitor_service.py
git commit -m "feat: 즉시발송 경로에 예약모드·야간제한 보류 게이트 추가"
```

---

## Task 9: 스케줄러 틱 잡 등록

**Files:**
- Modify: `app/services/scheduler.py`

- [ ] **Step 1: 구현 — JOB 상수 + import**

`app/services/scheduler.py` 상단 import에 추가:

```python
from app.services.notify_service import run_notify_tick_once
```

JOB 상수 그룹에 추가:

```python
JOB_NOTIFY_TICK = "youtube_notify_tick"
```

- [ ] **Step 2: 구현 — setup_jobs에 1분 잡 등록**

`setup_jobs()`의 `run_digest_tick_once` 잡 등록 다음에 추가:

```python
    scheduler.add_job(
        run_notify_tick_once,
        trigger="interval",
        minutes=1,
        id=JOB_NOTIFY_TICK,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
```

- [ ] **Step 3: 회귀 확인**

Run: `.venv/bin/pytest -q`
Expected: PASS (순환 import 없음 — scheduler는 이미 monitor/digest 서비스를 import)

- [ ] **Step 4: Commit**

```bash
git add app/services/scheduler.py
git commit -m "feat: 알림 발송 틱을 1분 주기 스케줄 잡으로 등록"
```

---

## Task 10: 기본 시드 추가 + 백엔드 수동 검증

**Files:**
- Modify: `app/services/default_settings.py`

- [ ] **Step 1: 구현 — notification 기본 시드 확장**

`DEFAULT_GROUP_SETTINGS`의 `"notification"` 리스트를 교체:

```python
    "notification": [
        {"key": "enabled", "value": "true", "value_type": "bool"},
        {"key": "parse_mode", "value": "HTML", "value_type": "string"},
        {"key": "send_mode", "value": "immediate", "value_type": "string"},
        {"key": "scheduled_max_per_run", "value": "5", "value_type": "int"},
        {"key": "wait_between_messages_sec", "value": "30", "value_type": "int"},
        {"key": "quiet_hours_enabled", "value": "false", "value_type": "bool"},
        {"key": "quiet_hours_start", "value": "22:00", "value_type": "string"},
        {"key": "quiet_hours_end", "value": "07:00", "value_type": "string"},
        {"key": "timezone", "value": "Asia/Seoul", "value_type": "string"},
        {"key": "low_confidence_threshold", "value": "0.5", "value_type": "float"},
    ],
```

- [ ] **Step 2: 회귀 확인**

Run: `.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 3: 수동 검증 (DB 가용 환경)**

앱을 기동하고(`docker compose up` 또는 로컬), 다음을 확인:
- 새 그룹 생성 시 notification 설정에 9개 신규 키가 시드됨.
- 즉시발송(기본): 분석 완료 영상이 기존처럼 즉시 발송됨.
- 야간 제한 ON + 현재 야간 시간대로 설정 → 분석 완료 영상이 보류(job_log "야간 보류"),
  제한 종료 후 1~2분 내 틱이 보정 발송.
- 예약 모드 + scheduled_times에 현재+2분 등록 → 해당 분 틱에서 일괄 발송, 즉시발송 경로는 보류 로그.

(자동화 어려운 경로이므로 로그/Telegram 수신으로 확인.)

- [ ] **Step 4: Commit**

```bash
git add app/services/default_settings.py
git commit -m "feat: 그룹 기본 시드에 알림 발송모드·야간제한 기본값 추가"
```

---

## Task 11: 프론트 필드 정의 (defs.ts)

**Files:**
- Modify: `frontend/src/settings/defs.ts`

- [ ] **Step 1: 구현 — FieldType/FieldDef 확장**

`FieldType`에 `'time' | 'timelist'` 추가:

```typescript
export type FieldType =
  | 'string' | 'int' | 'float' | 'textarea' | 'bool'
  | 'select' | 'model_select' | 'chatlist' | 'int_days' | 'int_hours'
  | 'time' | 'timelist'
```

`FieldDef`에 `showIf` 추가:

```typescript
export interface FieldDef {
  key: string
  label: string
  type?: FieldType
  secret?: boolean
  options?: string[]
  help?: string
  showIf?: { key: string; equals: string | boolean }
}
```

- [ ] **Step 2: 구현 — notification 필드 정의 교체**

`SETTING_DEFS.notification` 배열을 교체:

```typescript
  notification: [
    { key: 'enabled', label: '알림 활성화', type: 'bool' },
    { key: 'bot_token', label: '텔레그램 봇 토큰', secret: true },
    { key: 'chat_ids', label: 'Chat ID 목록', type: 'chatlist' },
    { key: 'parse_mode', label: 'parse_mode', type: 'select', options: ['HTML', 'MarkdownV2', 'None'], help: '일반적으로 HTML 권장' },
    { key: 'send_mode', label: '발송 모드', type: 'select', options: ['immediate', 'scheduled'], help: 'immediate=분석 즉시 발송, scheduled=예약 시각에 일괄 발송' },
    { key: 'scheduled_times', label: '예약 발송 시각', type: 'timelist', help: 'HH:MM, 최대 10개. 각 시각마다 미발송분을 일괄 발송', showIf: { key: 'send_mode', equals: 'scheduled' } },
    { key: 'scheduled_max_per_run', label: '회당 최대 발송 건수', type: 'int', help: '예약 회차당 발송 상한(1~50)', showIf: { key: 'send_mode', equals: 'scheduled' } },
    { key: 'wait_between_messages_sec', label: '건별 대기(초)', type: 'int', help: '예약·보정 발송 시 건 간 대기(스팸 방지)', showIf: { key: 'send_mode', equals: 'scheduled' } },
    { key: 'quiet_hours_enabled', label: '야간 알림 제한', type: 'bool', help: '지정 시간대에는 발송하지 않고 보류 후 종료 시 자동 발송' },
    { key: 'quiet_hours_start', label: '제한 시작', type: 'time', showIf: { key: 'quiet_hours_enabled', equals: true } },
    { key: 'quiet_hours_end', label: '제한 종료', type: 'time', help: '종료가 시작보다 이르면 자정을 넘기는 구간', showIf: { key: 'quiet_hours_enabled', equals: true } },
    { key: 'timezone', label: '시간대', help: '야간·예약 판정 기준 (예: Asia/Seoul)' },
    { key: 'low_confidence_threshold', label: '저신뢰도 임계값', type: 'float', help: '0~1. 이 값 미만 분석은 알림 제목에 ⚠️ 표시' },
  ],
```

- [ ] **Step 3: 빌드 확인 (타입체크)**

Run: `cd frontend && npm run build`
Expected: 성공 (단, SettingsForm/convert 미반영 시 타입 OK — 신규 type 값은 아직 미사용이어도 문자열 리터럴 유니온이라 통과)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/settings/defs.ts
git commit -m "feat: 알림 설정 폼에 발송모드·야간제한 필드 정의 추가"
```

---

## Task 12: 프론트 값 환산 (convert.ts) + 테스트

**Files:**
- Modify: `frontend/src/settings/convert.ts`
- Test: `frontend/src/settings/convert.test.ts`

- [ ] **Step 1: Write the failing test (기존 파일에 추가)**

`frontend/src/settings/convert.test.ts`의 `describe('initialValue')` 내부에 추가:

```typescript
  it('timelist: JSON 배열 파싱', () => {
    expect(initialValue({ key: 'scheduled_times', label: '', type: 'timelist' }, item('["09:00","14:00"]'))).toEqual(['09:00', '14:00'])
  })
  it('timelist: null이면 빈 배열', () => {
    expect(initialValue({ key: 'scheduled_times', label: '', type: 'timelist' }, item(null))).toEqual([])
  })
  it('time: 문자열 그대로', () => {
    expect(initialValue({ key: 'quiet_hours_start', label: '', type: 'time' }, item('22:00'))).toBe('22:00')
  })
```

그리고 `describe('toSaveItem')` 내부에 추가:

```typescript
  it('timelist: 배열 → JSON 저장(json)', () => {
    expect(toSaveItem({ key: 'scheduled_times', label: '', type: 'timelist' }, ['09:00', '14:00'])).toEqual({ key: 'scheduled_times', value: '["09:00","14:00"]', value_type: 'json', is_secret: false })
  })
  it('time: 문자열 → string 저장', () => {
    expect(toSaveItem({ key: 'quiet_hours_start', label: '', type: 'time' }, '22:00')).toEqual({ key: 'quiet_hours_start', value: '22:00', value_type: 'string', is_secret: false })
  })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/settings/convert.test.ts`
Expected: FAIL (timelist는 chatlist 분기를 안 타 string으로 떨어짐 → 불일치)

- [ ] **Step 3: Write minimal implementation**

`frontend/src/settings/convert.ts`의 `initialValue`에서 `chatlist` 분기 다음에 추가:

```typescript
  if (def.type === 'timelist') {
    try {
      const arr = JSON.parse(raw || '[]')
      return Array.isArray(arr) ? arr.map(String) : []
    } catch {
      return String(raw || '').split(',').map((s) => s.trim()).filter(Boolean)
    }
  }
```

`toSaveItem`에서 `chatlist` 분기 다음에 추가:

```typescript
  if (def.type === 'timelist') {
    const times = (value as string[]).map((s) => s.trim()).filter(Boolean)
    return { key: def.key, value: JSON.stringify(times), value_type: 'json', is_secret: false }
  }
```

(`time` 타입은 기본 분기에서 string으로 처리되므로 별도 코드 불필요 — `value_type === 'string'`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/settings/convert.test.ts`
Expected: PASS (전체 통과)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/settings/convert.ts frontend/src/settings/convert.test.ts
git commit -m "feat: 설정 폼 time/timelist 값 환산 추가"
```

---

## Task 13: 폼 렌더 (SettingsForm.tsx)

**Files:**
- Modify: `frontend/src/components/SettingsForm.tsx`

설명: `time` 입력, `timelist` 칩 에디터, `showIf` 조건부 표시를 추가한다.

- [ ] **Step 1: 구현 — showIf 평가로 필드 렌더 필터링**

`SettingsForm` 컴포넌트의 `defs.map(...)` 렌더를 교체. `form` 상태 기준으로 `showIf`를 평가한다:

```tsx
      {defs.map((d) => {
        if (d.showIf) {
          const cur = form[d.showIf.key]
          if (cur !== d.showIf.equals) return null
        }
        return (
          <Field
            key={d.key}
            def={d}
            value={form[d.key]}
            isSet={Boolean(itemMap[d.key]?.value)}
            models={models}
            onChange={(v) => set(d.key, v)}
          />
        )
      })}
```

주의: `bool` 필드값은 boolean, `select` 값은 string이므로 `cur !== equals` 비교가 타입까지 일치해야 한다(`send_mode`는 'scheduled' 문자열, `quiet_hours_enabled`는 true boolean — defs의 `equals` 타입과 일치).

- [ ] **Step 2: 구현 — Field에 time/timelist 분기 추가**

`Field` 함수의 `def.type === 'chatlist'` 분기 다음(=== 'int_days' 앞)에 추가:

```tsx
      ) : def.type === 'time' ? (
        <input
          type="time"
          value={value as string}
          onChange={(e) => onChange(e.target.value)}
          className="w-40 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      ) : def.type === 'timelist' ? (
        <TimeList value={value as string[]} onChange={onChange} />
```

- [ ] **Step 3: 구현 — TimeList 컴포넌트 추가**

파일 하단(`ChatList` 함수 다음)에 추가:

```tsx
function TimeList({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const [draft, setDraft] = useState('')
  const [err, setErr] = useState('')
  const sorted = (arr: string[]) =>
    [...arr].sort((a, b) => {
      const m = (t: string) => {
        const [h, mm] = t.split(':').map(Number)
        return h * 60 + mm
      }
      return m(a) - m(b)
    })
  const add = () => {
    const t = draft.trim()
    if (!t) return
    if (!/^([01]?\d|2[0-3]):[0-5]\d$/.test(t)) { setErr('HH:MM 형식으로 입력하세요'); return }
    if (value.includes(t)) { setErr('이미 등록된 시각입니다'); return }
    if (value.length >= 10) { setErr('최대 10개까지 등록할 수 있습니다'); return }
    onChange(sorted([...value, t]))
    setDraft('')
    setErr('')
  }
  const remove = (t: string) => onChange(value.filter((x) => x !== t))
  return (
    <div className="space-y-2">
      {value.length === 0 ? (
        <p className="text-xs text-gray-400 italic">등록된 예약 시각이 없습니다.</p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {value.map((t) => (
            <span key={t} className="inline-flex items-center gap-1.5 px-3 py-1 bg-blue-50 border border-blue-200 rounded-full text-sm font-medium text-blue-700">
              {t}
              <button type="button" onClick={() => remove(t)} className="text-blue-400 hover:text-red-500">×</button>
            </span>
          ))}
        </div>
      )}
      <div className="flex gap-2">
        <input
          type="time"
          value={draft}
          onChange={(e) => { setDraft(e.target.value); setErr('') }}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add() } }}
          className="w-40 border border-gray-300 rounded-lg px-3 py-2 text-sm"
        />
        <button type="button" onClick={add} disabled={value.length >= 10} className="px-3 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50">추가</button>
      </div>
      {err && <p className="text-xs text-red-500">{err}</p>}
    </div>
  )
}
```

(`useState`는 파일 상단에서 이미 import됨.)

- [ ] **Step 4: 빌드 + 테스트 확인**

Run: `cd frontend && npm run build && npx vitest run`
Expected: 빌드 성공, 전체 테스트 PASS

- [ ] **Step 5: 수동 검증 (UI)**

설정 → Notification 화면에서:
- 발송 모드 select를 `scheduled`로 바꾸면 예약 시각/회당 건수/건별 대기 필드가 나타남.
- `immediate`로 바꾸면 사라짐.
- 야간 알림 제한 토글 ON 시 제한 시작/종료 time 입력 노출.
- 예약 시각 칩 추가/삭제·중복/10개 초과 검증 동작.
- 저장 후 새로고침 시 값 유지(timelist는 JSON, time은 string으로 저장됨).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/SettingsForm.tsx
git commit -m "feat: 설정 폼에 time 입력·예약시각 칩 에디터·조건부 표시 추가"
```

---

## Self-Review (작성자 체크 결과)

**Spec coverage**
- send_mode(즉시/예약): Task 2,3,7,8,11 ✓
- 야간 제한 + 타임존: Task 1,3,7,8,11 ✓
- 예약 회당 제한(max_per/wait): Task 2,3,6,11 ✓
- 야간 보정 발송: Task 7,8 ✓
- 저신뢰도 배지: Task 4,8 ✓
- 기본 시드: Task 10 ✓
- UI(범용 폼 확장, time/timelist/showIf): Task 11,12,13 ✓
- scheduled 야간 겹침 skip: Task 7 ✓

**Placeholder scan**: 모든 코드 스텝에 실제 코드 포함. Task 7의 중복 호출은 Step 2에서 정리하도록 명시.

**Type consistency**: `build_message(video, analysis, threshold)`, `notify_video(..., threshold)`, `notify_pending_batch(..., max_per, wait_sec, threshold, log_label)`, `_matches_scheduled_time(now_local, scheduled_times)`, `is_quiet_hours_now(enabled, start, end, tz=, now=)` — 전 태스크에서 시그니처 일관. 프론트 `showIf: {key, equals}` defs↔SettingsForm 일치, `value_type: 'json'`(timelist)·`'string'`(time) convert↔get_notification 파싱 일치.
