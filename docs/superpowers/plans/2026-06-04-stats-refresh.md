# 조회수·좋아요 주기 갱신 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 게시 후 N일(기본 30) 이내 영상의 view_count·like_count를 하루 1회 YouTube API로 갱신해 신선한 지표를 유지한다.

**Architecture:** `PollingSettings.stats_refresh_days`(그룹별, 0=비활성) 설정에 따라, 매 1440분 스케줄 잡이 활성 그룹을 순회하며 윈도우 내 영상의 video_id를 모아 `get_video_details`(50개 batch)로 fresh stats를 받아 제자리 UPDATE한다. 별도 스냅샷 컬럼·DB 마이그레이션 없음.

**Tech Stack:** Python 3 / FastAPI / SQLAlchemy async / APScheduler / pytest. Frontend: React+TS.

설계: `docs/superpowers/specs/2026-06-04-stats-refresh-design.md`
테스트 명령: `python -m pytest` (no `.venv/bin/pytest`). 프론트: `cd frontend && npx tsc --noEmit && npm run build`.

---

## File Structure
- `app/services/settings_types.py` — `PollingSettings.stats_refresh_days`.
- `app/services/settings_manager.py` — `get_polling`에서 로드.
- `app/services/default_settings.py` — polling 시드.
- `app/services/job_logger.py` — `JOB_TYPE_STATS` 상수.
- `app/services/monitor_service.py` — 순수 헬퍼(`_stats_window_cutoff`, `_apply_stats`) + `run_stats_refresh_once`.
- `app/services/scheduler.py` — 1440분 잡 등록.
- `frontend/src/settings/defs.ts` — polling에 필드.
- tests: `tests/test_stats_refresh.py`.

---

## Task 1: stats_refresh_days 설정 추가

**Files:**
- Modify: `app/services/settings_types.py`, `app/services/settings_manager.py`, `app/services/default_settings.py`
- Test: `tests/test_stats_refresh.py` (생성)

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_stats_refresh.py` 생성:

```python
"""조회수·좋아요 주기 갱신 검증."""

from app.services.settings_types import PollingSettings


def test_polling_stats_refresh_default():
    assert PollingSettings().stats_refresh_days == 30
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_stats_refresh.py -v`
Expected: FAIL (AttributeError: stats_refresh_days 없음)

- [ ] **Step 3: dataclass 필드 추가**

`app/services/settings_types.py`의 `PollingSettings`에서 `analysis_interval_sec: int = 120` 다음 줄에 추가:

```python
    stats_refresh_days: int = 30  # 게시 후 N일 이내 영상 stats 갱신. 0이면 비활성.
```

- [ ] **Step 4: get_polling 로드**

`app/services/settings_manager.py`의 `get_polling` 내 `return PollingSettings(` 호출에서 `analysis_interval_sec=...` 줄 다음에 추가:

```python
            stats_refresh_days=_as_int(d.get("stats_refresh_days"), 30),
```

- [ ] **Step 5: 기본 시드 추가**

`app/services/default_settings.py`의 `DEFAULT_GROUP_SETTINGS["polling"]` 리스트 끝에 추가:

```python
        {"key": "stats_refresh_days", "value": "30", "value_type": "int"},
```

- [ ] **Step 6: 통과 확인**

Run: `python -m pytest tests/test_stats_refresh.py -q && python -m pytest -q`
Expected: 전체 PASS

- [ ] **Step 7: Commit**

```bash
git add app/services/settings_types.py app/services/settings_manager.py app/services/default_settings.py tests/test_stats_refresh.py
git commit -m "feat: PollingSettings에 stats_refresh_days(기본 30) 추가"
```

---

## Task 2: stats 갱신 순수 헬퍼

**Files:**
- Modify: `app/services/monitor_service.py` (헬퍼 추가)
- Test: `tests/test_stats_refresh.py` (추가)

- [ ] **Step 1: 실패 테스트 추가**

`tests/test_stats_refresh.py` 하단에 추가:

```python
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

from app.services.monitor_service import _stats_window_cutoff, _build_stats_map


def test_stats_window_cutoff():
    now = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)
    assert _stats_window_cutoff(now, 30) == now - timedelta(days=30)
    assert _stats_window_cutoff(now, 1) == now - timedelta(days=1)


def test_build_stats_map():
    metas = [
        SimpleNamespace(video_id="a", view_count=100, like_count=10),
        SimpleNamespace(video_id="b", view_count=None, like_count=5),
    ]
    m = _build_stats_map(metas)
    assert m == {"a": (100, 10), "b": (None, 5)}


def test_build_stats_map_empty():
    assert _build_stats_map([]) == {}
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_stats_refresh.py -k "window or stats_map" -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: 헬퍼 구현**

`app/services/monitor_service.py`에 추가(예: `_active_groups` 근처 모듈 레벨). 파일 상단에 이미 `from datetime import datetime, timedelta, timezone`이 import되어 있다(확인 후 없으면 추가):

```python
def _stats_window_cutoff(now: datetime, days: int) -> datetime:
    """stats 갱신 대상 cutoff: now - days일."""
    return now - timedelta(days=days)


def _build_stats_map(metas) -> dict[str, tuple]:
    """VideoMeta 리스트 → {video_id: (view_count, like_count)}."""
    return {m.video_id: (m.view_count, m.like_count) for m in metas if m.video_id}
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_stats_refresh.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/monitor_service.py tests/test_stats_refresh.py
git commit -m "feat: stats 갱신 순수 헬퍼(window cutoff / stats map)"
```

---

## Task 3: JOB_TYPE_STATS 상수

**Files:**
- Modify: `app/services/job_logger.py`

- [ ] **Step 1: 상수 추가**

`app/services/job_logger.py`에서 `JOB_TYPE_NOTIFY = "notify"` 다음 줄에 추가:

```python
JOB_TYPE_STATS = "stats"
```

- [ ] **Step 2: 무결성 확인**

Run: `python -c "from app.services.job_logger import JOB_TYPE_STATS; print(JOB_TYPE_STATS)"`
Expected: `stats`

- [ ] **Step 3: Commit**

```bash
git add app/services/job_logger.py
git commit -m "feat: job_logger에 JOB_TYPE_STATS 추가"
```

---

## Task 4: run_stats_refresh_once 잡

**Files:**
- Modify: `app/services/monitor_service.py`

설명: DB/API 의존 — 신규 유닛 테스트 없음(헬퍼는 Task 2에서 테스트). import/스위트 통과로 검증.

- [ ] **Step 1: 구현**

`app/services/monitor_service.py`에 함수 추가(모듈 레벨, 예: `run_pending_analysis_once` 근처). 기존 import 활용: `select`, `update`(sqlalchemy), `Video`, `YouTubeAPIClient`, `YouTubeQuotaExceededError`, `dpm`, `DBNotConfiguredError`, `get_settings_manager`, `_active_groups`, `_make_session_factory`, `write_job_log`, `JobTimer`. job_logger에서 `JOB_TYPE_STATS`, `STATUS_SUCCESS`, `STATUS_SKIP`를 import에 추가(파일 상단 job_logger import 블록에 `JOB_TYPE_STATS` 추가).

```python
async def run_stats_refresh_once() -> None:
    """게시 후 N일 이내 영상의 view_count·like_count를 YouTube API로 갱신한다.

    그룹별 polling.stats_refresh_days(0이면 비활성)에 따라 윈도우 내 영상의
    video_id를 모아 get_video_details로 fresh stats를 받아 제자리 UPDATE한다.
    """
    mgr = get_settings_manager()
    groups = await _active_groups()
    for group in groups:
        try:
            polling = await mgr.get_polling(group.group_id)
            if int(polling.stats_refresh_days or 0) <= 0:
                continue
            if not polling.youtube_api_key:
                continue
            try:
                await dpm.ensure_schema(group)
                engine = await dpm.get_engine_for_group(group)
            except DBNotConfiguredError:
                continue

            make_session = _make_session_factory(engine, group.schema_name)
            cutoff = _stats_window_cutoff(datetime.now(timezone.utc), int(polling.stats_refresh_days))

            async with make_session() as sess:
                rows = (
                    await sess.execute(
                        select(Video.video_pk, Video.video_id)
                        .where(Video.published_at >= cutoff)
                    )
                ).all()
            if not rows:
                continue
            id_to_pk = {vid: pk for (pk, vid) in rows if vid}
            if not id_to_pk:
                continue

            api_client = YouTubeAPIClient(polling)
            timer = JobTimer()
            updated = 0
            try:
                with timer:
                    try:
                        metas = await api_client.get_video_details(list(id_to_pk.keys()))
                    except YouTubeQuotaExceededError as exc:
                        print(f"[{group.slug}] stats 갱신: quota 초과 — {exc}")
                        continue
                    stats_map = _build_stats_map(metas)
                    async with make_session() as sess:
                        async with sess.begin():
                            for video_id, (vc, lc) in stats_map.items():
                                pk = id_to_pk.get(video_id)
                                if pk is None:
                                    continue
                                await sess.execute(
                                    update(Video)
                                    .where(Video.video_pk == pk)
                                    .values(view_count=vc, like_count=lc)
                                )
                                updated += 1
            finally:
                await api_client.aclose()
                await write_job_log(
                    make_session,
                    job_type=JOB_TYPE_STATS,
                    status=STATUS_SUCCESS,
                    message=f"stats 갱신: {updated}/{len(id_to_pk)}건",
                    duration_ms=timer.elapsed_ms,
                )
        except DBNotConfiguredError:
            continue
        except Exception as e:
            print(f"[{group.slug}] stats 갱신 실패: {e}")
```

주의: `with timer:` 블록 안에서 `continue`(quota)하면 `finally`가 실행되어 job_log를 남기되 updated=0. 의도된 동작(skip 기록). `continue`는 for 루프의 다음 그룹으로 이동한다(with/finally 정상 처리).

job_logger import 블록 수정: 파일 상단의 `from app.services.job_logger import (...)`에 `JOB_TYPE_STATS`를 추가한다(`STATUS_SUCCESS`, `STATUS_SKIP`, `JobTimer`, `write_job_log`는 이미 있음 — READ로 확인).

- [ ] **Step 2: import/무결성 확인**

Run: `python -c "import app.services.monitor_service as m; print(hasattr(m,'run_stats_refresh_once'))"`
Expected: True

Run: `python -m pytest -q`
Expected: 전체 PASS

- [ ] **Step 3: Commit**

```bash
git add app/services/monitor_service.py
git commit -m "feat: 조회수·좋아요 주기 갱신 잡(run_stats_refresh_once)"
```

---

## Task 5: 스케줄러 잡 등록

**Files:**
- Modify: `app/services/scheduler.py`

- [ ] **Step 1: import + 상수**

`app/services/scheduler.py` 상단 import에서 `from app.services.monitor_service import run_master_poll_once, run_pending_analysis_once`를 다음으로 교체:

```python
from app.services.monitor_service import (
    run_master_poll_once,
    run_pending_analysis_once,
    run_stats_refresh_once,
)
```

JOB 상수 그룹(`JOB_NOTIFY_TICK = ...` 다음)에 추가:

```python
JOB_STATS_REFRESH = "youtube_stats_refresh"
```

- [ ] **Step 2: setup_jobs에 잡 등록**

`setup_jobs()`의 `run_notify_tick_once` 잡 등록 블록 다음에 추가:

```python
    scheduler.add_job(
        run_stats_refresh_once,
        trigger="interval",
        minutes=1440,
        id=JOB_STATS_REFRESH,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
```

- [ ] **Step 3: 무결성 확인**

Run: `python -c "import app.services.scheduler as s; print(s.JOB_STATS_REFRESH)"`
Expected: `youtube_stats_refresh`

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add app/services/scheduler.py
git commit -m "feat: stats 갱신 잡을 1일 주기 스케줄로 등록"
```

---

## Task 6: 프론트 stats_refresh_days 필드

**Files:**
- Modify: `frontend/src/settings/defs.ts`

- [ ] **Step 1: polling 카테고리에 필드 추가**

`frontend/src/settings/defs.ts`의 `SETTING_DEFS.polling` 배열 끝(`max_concurrent_analyses` 다음)에 추가:

```typescript
    { key: 'stats_refresh_days', label: '조회수 갱신 기간(일)', type: 'int', help: '게시 후 N일 이내 영상의 조회수·좋아요를 매일 갱신. 0이면 끔.' },
```

- [ ] **Step 2: 타입체크/빌드**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: tsc 클린, 빌드 성공

- [ ] **Step 3: Commit**

```bash
git add frontend/src/settings/defs.ts
git commit -m "feat: Monitoring 설정에 조회수 갱신 기간 필드"
```

---

## Task 7: 수동 검증 (DB/API 환경)

설명: 코드 변경 없음. 실 환경 확인 절차.

- [ ] **Step 1: 갱신 동작 확인**

앱 기동 후(또는 잡 수동 트리거), 한 그룹에서:
- 윈도우 내 영상의 `view_count`가 갱신 전후로 변경되는지 DB로 확인.
- `job_logs`에 job_type='stats', "stats 갱신: N/M건" 기록 확인.
- `stats_refresh_days=0`으로 설정 시 해당 그룹이 skip되는지 확인.
- quota 초과 시 그룹 skip + 로그만, 다른 그룹은 계속되는지 확인.

---

## Self-Review (작성자 체크)

**Spec coverage**
- 설정 stats_refresh_days(기본30/0비활성): Task 1, 프론트 Task 6 ✓
- 갱신 잡(활성그룹·윈도우·batch·제자리 UPDATE·삭제skip·job_log): Task 4 ✓
- 순수 헬퍼: Task 2 ✓
- JOB_TYPE_STATS: Task 3 ✓
- 스케줄러 1일 잡: Task 5 ✓
- 에러처리(quota/DBNotConfigured/일반): Task 4 ✓
- DB 마이그레이션 없음: 제자리 UPDATE만 — 전 태스크 확인 ✓

**Placeholder scan**: 모든 코드 스텝에 실제 코드. READ 지시는 기존 코드 확인용.

**Type consistency**: `_stats_window_cutoff(now, days)`, `_build_stats_map(metas)→dict[str,tuple]`, `run_stats_refresh_once()`, `JOB_TYPE_STATS`, `YouTubeAPIClient(polling)`, `get_video_details(list)→VideoMeta(view_count/like_count/video_id)`, `PollingSettings.stats_refresh_days` — 전 태스크 일관.
