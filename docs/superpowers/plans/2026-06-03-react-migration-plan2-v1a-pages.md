# React 마이그레이션 Plan 2 — v1a 나머지 페이지 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Plan 1의 기반 위에 v1a 운영 화면(채널 관리·영상 목록·영상 상세·영상 분석·로그)을 그룹 스코프로 구현한다.

**Architecture:** 백엔드는 비파괴 추가만 한다(영상 목록 `channel_pk` 필터, 로그 필터·옵트인 페이지네이션, 채널 단건 폴링 엔드포인트). 프론트엔드는 my-assistant 페이지를 그룹 스코프 api로 바인딩해 이식하되, ytdb에 없는 v2 기능(텔레그램 수동 발송/미리보기, 영상별 커스텀 프롬프트)은 제외한다. 응답 형태 차이는 api 어댑터(`toVideo`, `toVideoDetail`)가 흡수한다.

**Tech Stack:** FastAPI · SQLAlchemy(async) · pytest · React 18 · TypeScript · React Router v6 · Tailwind · react-markdown/remark-gfm · Vitest

**관련 스펙:** `docs/superpowers/specs/2026-06-03-react-migration-design.md`
**선행:** Plan 1(기반) 완료 — `frontend/` 앱, `groupClient`, `toVideo`, GroupProvider, Layout, Dashboard, 백엔드 stats/health/페이지네이션/SPA 서빙이 이미 존재한다.

**복사 원본 (my-assistant, 읽기 전용):** `/Users/mukymook/cursor-workspace/my-assistant/frontend/youtube/src/`

---

## v2로 명시 제외 (이 플랜에서 구현하지 않음)
- 텔레그램 수동 발송 버튼 + 알림 미리보기 (VideoDetail에서 제거)
- 영상별 커스텀 프롬프트 재분석 (VideoDetail/InstantAnalyze에서 제거)
- 채널 생성 시 `is_active=false`(수동 전용)·`auto_poll_now` 토글 (ytdb `ChannelCreate`에 없음 → 생성 후 행에서 토글)
- 주간 리뷰(다이제스트) 화면, 설정 6종 화면 (Plan 3)

---

## File Structure

### 백엔드 (수정/추가)
- Modify `app/routers/videos.py` — `list_videos`에 `channel_pk` 필터 추가
- Modify `app/routers/logs.py` — `job_type`/`status` 필터 + 옵트인 `paged` 페이지네이션
- Create `app/schemas/stats.py`에 `PaginatedJobLogs` 추가 (기존 파일 수정)
- Create `app/services/monitor_service.py`에 `poll_single_channel(group, channel_pk)` 추가 (기존 파일 수정)
- Modify `app/routers/channels.py` — `POST /channels/{pk}/poll` 엔드포인트

### 프론트엔드 (`frontend/src/`)
- Copy `components/Pagination.tsx`, `components/NotifyBadge.tsx` (my-assistant 그대로)
- Modify `api/types.ts` — `Channel`, `VideoDetail`, `Tag`, `JobLog`, `PaginatedJobLogs`, `InstantAnalyzeResponse`, `PollResponse` 추가
- Modify `api/adapters.ts` — `toVideoDetail` 추가
- Modify `api/videos.ts` — `get`/`remove`/`analyzeNow` + `listPaged`에 `channel_pk`
- Create `api/channels.ts`, `api/logs.ts`, `api/tags.ts`
- Create `pages/Channels.tsx`, `pages/Videos.tsx`, `pages/VideoDetail.tsx`, `pages/InstantAnalyze.tsx`, `pages/Logs.tsx`
- Modify `App.tsx` — Placeholder를 실제 페이지로 교체 + `videos/:videoPk` 라우트 추가
- Tests: `api/adapters.test.ts`에 `toVideoDetail` 케이스 추가

---

## Task 1: 영상 목록에 channel_pk 필터 추가

ytdb `list_videos`는 status/tag 필터만 있다. Videos 페이지의 채널 필터를 위해 `channel_pk`를 추가한다(count 쿼리에도 동일 적용).

**Files:** Modify `app/routers/videos.py`

- [ ] **Step 1:** `list_videos` 시그니처에 파라미터 추가(`tag` 다음 줄):
```python
    channel_pk: int | None = Query(None, description="채널 PK 필터"),
```
- [ ] **Step 2:** 메인 쿼리 필터에 추가. 기존 `if status:` 블록 다음에:
```python
        if channel_pk is not None:
            stmt = stmt.where(Video.channel_pk == channel_pk)
```
- [ ] **Step 3:** count 쿼리에도 동일 적용. paged 분기의 `if status:` 다음에:
```python
            if channel_pk is not None:
                count_stmt = count_stmt.where(Video.channel_pk == channel_pk)
```
- [ ] **Step 4:** 검증 — `python -c "from app.main import app; print('ok')"` → ok. 그리고 `pytest -q` → 기존 5 passed 유지.
- [ ] **Step 5:** Commit
```bash
git add app/routers/videos.py
git commit -m "feat: 영상 목록 channel_pk 필터 추가"
```

---

## Task 2: 로그 필터 + 옵트인 페이지네이션

`logs.py`에 `job_type`/`status` 필터와 `?paged=1` 페이지네이션을 추가한다(vanilla 호환을 위해 옵트인). `PaginatedJobLogs` 스키마를 추가한다.

**Files:** Modify `app/schemas/stats.py`, `app/routers/logs.py`

- [ ] **Step 1:** `app/schemas/stats.py` 끝에 추가. 먼저 상단 import에 `JobLogOut`은 logs.py에 정의돼 있어 순환참조 위험이 있으므로, `PaginatedJobLogs`는 제네릭 대신 logs.py 내부에 정의한다. 따라서 이 스텝은 **생략**하고 Step 2에서 logs.py에 모두 정의한다.

- [ ] **Step 2:** `app/routers/logs.py`를 다음으로 교체:
```python
"""그룹 잡 로그 조회 API."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from app.models.control.group import Group
from app.models.pg.job_log import JobLog
from app.routers.deps import get_group_or_404
from app.services.db_engine import data_plane_engine_manager as dpm

router = APIRouter(prefix="/api/groups/{slug}/logs", tags=["logs"])


class JobLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    log_pk: int
    job_type: str
    channel_pk: Optional[int]
    video_pk: Optional[int]
    status: str
    message: Optional[str]
    duration_ms: Optional[int]
    started_at: datetime


class PaginatedJobLogs(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[JobLogOut]


@router.get("")
async def list_logs(
    group: Group = Depends(get_group_or_404),
    job_type: str | None = Query(None, description="job_type 필터"),
    status: str | None = Query(None, description="status 필터"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    paged: bool = Query(False, description="true면 {items,total,page,page_size} 반환"),
):
    async with dpm.group_session(group) as session:
        stmt = select(JobLog).order_by(JobLog.log_pk.desc()).limit(limit).offset(offset)
        if job_type:
            stmt = stmt.where(JobLog.job_type == job_type)
        if status:
            stmt = stmt.where(JobLog.status == status)
        rows = list((await session.execute(stmt)).scalars().all())

        total = None
        if paged:
            count_stmt = select(func.count()).select_from(JobLog)
            if job_type:
                count_stmt = count_stmt.where(JobLog.job_type == job_type)
            if status:
                count_stmt = count_stmt.where(JobLog.status == status)
            total = (await session.execute(count_stmt)).scalar_one()

    if not paged:
        return rows

    page = offset // limit + 1 if limit else 1
    return PaginatedJobLogs(
        total=total,
        page=page,
        page_size=limit,
        items=[JobLogOut.model_validate(r) for r in rows],
    )
```
(주: 비-paged 경로는 기존과 동일하게 `list[JobLog]`를 반환 → vanilla 호환 유지. `response_model`은 혼합 반환이라 제거.)

- [ ] **Step 3:** 검증 — `python -c "from app.main import app; print('ok')"` → ok. `pytest -q` → 5 passed 유지.
- [ ] **Step 4:** Commit
```bash
git add app/routers/logs.py
git commit -m "feat: 로그 job_type/status 필터 + 옵트인 페이지네이션"
```

---

## Task 3: 채널 단건 폴링 엔드포인트

`monitor_service.py`에 단일 채널 폴링 함수를 추가하고(`_poll_group` 패턴 재사용), `channels.py`에 백그라운드 트리거 엔드포인트를 추가한다.

**Files:** Modify `app/services/monitor_service.py`, `app/routers/channels.py`

- [ ] **Step 1:** `app/services/monitor_service.py`에서 기존 `poll_group`/`analyze_group`(약 527~535행) 근처에 함수를 추가한다. 먼저 `_poll_group`(278행~)과 동일 모듈에 있는 헬퍼들(`get_settings_manager`, `dpm`, `_make_session_factory`, `MonitorService`, `write_job_log`, `JobTimer`, `YouTubeAPIClient`, `YouTubeQuotaExceededError`, `JOB_TYPE_CHANNEL_POLL`, `STATUS_SUCCESS/SKIP/FAIL`, `DBNotConfiguredError`, `Channel`, `select`)를 그대로 사용한다. 다음 함수를 추가:
```python
async def poll_single_channel(group: Group, channel_pk: int) -> None:
    """단일 채널을 즉시 폴링한다(수동 트리거용). _poll_group의 1채널 버전."""
    mgr = get_settings_manager()
    polling = await mgr.get_polling(group.group_id)
    if not polling.youtube_api_key:
        print(f"[{group.slug}] YouTube API 키 미설정 - 단건 폴링 SKIP")
        return
    try:
        await dpm.ensure_schema(group)
        engine = await dpm.get_engine_for_group(group)
    except DBNotConfiguredError:
        print(f"[{group.slug}] DB 미설정 - 단건 폴링 SKIP")
        return

    make_session = _make_session_factory(engine, group.schema_name)
    service = MonitorService(polling=polling)

    async with make_session() as session:
        channel = (
            await session.execute(select(Channel).where(Channel.channel_pk == channel_pk))
        ).scalar_one_or_none()
    if channel is None:
        print(f"[{group.slug}] 채널 없음(channel_pk={channel_pk}) - 단건 폴링 SKIP")
        return

    api_client = YouTubeAPIClient(polling)
    timer = JobTimer()
    try:
        with timer:
            async with make_session() as sess:
                async with sess.begin():
                    new_pks = await service.process_channel(channel, sess, api_client)
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_CHANNEL_POLL,
            status=STATUS_SUCCESS,
            message=f"신규 영상 {len(new_pks)}건 수집" if new_pks else "신규 영상 없음",
            duration_ms=timer.elapsed_ms,
            channel_pk=channel.channel_pk,
        )
    except YouTubeQuotaExceededError as e:
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_CHANNEL_POLL,
            status=STATUS_SKIP,
            message=f"쿼터 초과: {e}",
            duration_ms=timer.elapsed_ms,
            channel_pk=channel.channel_pk,
        )
    except Exception as e:  # noqa: BLE001
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_CHANNEL_POLL,
            status=STATUS_FAIL,
            message=str(e),
            duration_ms=timer.elapsed_ms,
            channel_pk=channel.channel_pk,
        )
    finally:
        await api_client.aclose()
```
IMPORTANT: 추가 전 `monitor_service.py`에서 위 심볼들의 정확한 이름/시그니처를 확인하라(특히 `write_job_log`, `JobTimer`, `JOB_TYPE_CHANNEL_POLL`, `STATUS_*`, `YouTubeQuotaExceededError`가 `_poll_group`에서 쓰이는 그대로). 이름이 다르면 `_poll_group`에서 쓰는 실제 이름에 맞춰라.

- [ ] **Step 2:** `app/routers/channels.py`에 엔드포인트 추가. 상단 import에 추가: `from fastapi import BackgroundTasks`(기존 fastapi import 줄에 병합), `from app.services.monitor_service import poll_single_channel`(기존 MonitorService import 줄 근처). `delete_channel` 아래에 추가:
```python
@router.post("/{channel_pk}/poll", status_code=202)
async def poll_channel(
    channel_pk: int,
    background: BackgroundTasks,
    group: Group = Depends(get_group_or_404),
) -> dict:
    """단일 채널을 백그라운드에서 즉시 폴링한다."""
    background.add_task(poll_single_channel, group, channel_pk)
    return {"status": "started", "channel_pk": channel_pk, "message": "폴링을 시작했습니다. 잠시 후 영상/로그를 확인하세요."}
```
- [ ] **Step 3:** 검증 — `python -c "from app.main import app; print('ok')"` → ok. `pytest -q` → 5 passed.
- [ ] **Step 4:** Commit
```bash
git add app/services/monitor_service.py app/routers/channels.py
git commit -m "feat: 채널 단건 폴링 엔드포인트(POST /channels/{pk}/poll)"
```

---

## Task 4: Pagination + NotifyBadge 컴포넌트 복사

**Files:** Create `frontend/src/components/Pagination.tsx`, `frontend/src/components/NotifyBadge.tsx`

- [ ] **Step 1:** my-assistant 원본을 byte-identical 복사:
  - `/Users/mukymook/cursor-workspace/my-assistant/frontend/youtube/src/components/Pagination.tsx` → `frontend/src/components/Pagination.tsx`
  - `/Users/mukymook/cursor-workspace/my-assistant/frontend/youtube/src/components/NotifyBadge.tsx` → `frontend/src/components/NotifyBadge.tsx`
  둘 다 props만 받는 순수 컴포넌트라 import 변경 불필요.
  주의: `Pagination.tsx`는 `visible.map`에서 Fragment에 key가 없어 React 경고가 날 수 있다. 원본 유지하되, 만약 빌드(`tsc`)에서 막히면 각 항목을 `<Fragment key={p}>`로 감싸라(원본 동작 보존).
- [ ] **Step 2:** 검증 — `cd frontend && npx tsc --noEmit`. 예상 잔여 에러는 아직 미생성된 페이지를 App.tsx가 참조하지 않으므로 없어야 한다(App.tsx는 Plan 1 상태로 Placeholder 사용 중). 두 컴포넌트가 타입 에러를 내지 않으면 성공.
- [ ] **Step 3:** Commit
```bash
git add frontend/src/components/Pagination.tsx frontend/src/components/NotifyBadge.tsx
git commit -m "feat: Pagination/NotifyBadge 컴포넌트 복사"
```

---

## Task 5: API 타입 확장

**Files:** Modify `frontend/src/api/types.ts`

- [ ] **Step 1:** `frontend/src/api/types.ts` 끝에 추가:
```ts
export interface Channel {
  channel_pk: number
  channel_id: string
  channel_name: string
  channel_handle: string | null
  thumbnail_url: string | null
  category: string | null
  poll_interval_min: number
  is_active: boolean
  notify_enabled: boolean
  last_checked_at: string | null
  last_video_id: string | null
  created_at: string
}

export interface KeyPoint {
  timestamp?: string
  point?: string
}

export interface VideoDetail {
  video_pk: number
  video_id: string
  video_url: string
  title: string
  description: string | null
  thumbnail_url: string | null
  published_at: string
  duration_seconds: number | null
  view_count: number | null
  like_count: number | null
  analysis_status: 'pending' | 'processing' | 'done' | 'failed'
  analysis_error: string | null
  notified_at: string | null
  source_channel_name: string | null
  retry_count: number | null
  tags: string[]
  // analysis(평탄화)
  one_line: string | null
  headline: string | null
  short_summary_md: string | null
  full_analysis_md: string | null
  bullet_points: string[] | null
  key_points: KeyPoint[] | null
  insights: string[] | null
  entities: unknown[] | null
  sentiment: string | null
  confidence_score: number | null
  model_name: string | null
  analyzed_at: string | null
}

export interface Tag {
  tag_pk: number
  name: string
  tag_type: string
  video_count: number
}

export interface JobLog {
  log_pk: number
  job_type: string
  channel_pk: number | null
  video_pk: number | null
  status: string
  message: string | null
  duration_ms: number | null
  started_at: string
}

export interface PaginatedJobLogs {
  total: number
  page: number
  page_size: number
  items: JobLog[]
}

export interface InstantAnalyzeResponse {
  video_pk: number
  video_id: string
  existing: boolean
  queued: boolean
}

export interface PollResponse {
  status: string
  channel_pk: number
  message: string
}
```
- [ ] **Step 2:** `cd frontend && npx tsc --noEmit` — App.tsx가 아직 이 타입들을 안 쓰므로 에러 없어야 함(타입 선언만 추가).
- [ ] **Step 3:** Commit
```bash
git add frontend/src/api/types.ts
git commit -m "feat: Channel/VideoDetail/Tag/JobLog 등 API 타입 추가"
```

---

## Task 6: toVideoDetail 어댑터 (TDD)

ytdb `GET /videos/{pk}`는 `{...video, tags: string[], analysis: {...} | null}`를 반환한다. 페이지는 평탄화된 `VideoDetail`을 기대한다. 어댑터가 `analysis.*`를 최상위로 끌어올린다.

**Files:** Modify `frontend/src/api/adapters.ts`, `frontend/src/api/adapters.test.ts`

- [ ] **Step 1:** `frontend/src/api/adapters.test.ts`에 케이스 추가(기존 import 줄에 `toVideoDetail` 추가):
```ts
import { toVideo, toVideoDetail } from './adapters'
```
그리고 파일 끝에:
```ts
describe('toVideoDetail', () => {
  it('중첩 analysis를 최상위로 평탄화한다', () => {
    const raw = {
      video_pk: 5,
      video_id: 'v5',
      video_url: 'u',
      title: 'T',
      description: '설명',
      thumbnail_url: null,
      published_at: '2026-06-01T00:00:00Z',
      duration_seconds: 300,
      view_count: 1000,
      like_count: 50,
      analysis_status: 'done',
      analysis_error: null,
      notified_at: null,
      tags: ['반도체', 'AI'],
      analysis: {
        one_line: '한 줄',
        headline: '헤드라인',
        short_summary_md: '요약',
        bullet_points: ['p1', 'p2'],
        full_analysis_md: '## 분석',
        key_points: [{ timestamp: '0:10', point: 'x' }],
        insights: ['i1'],
        entities: [],
        sentiment: '긍정',
        confidence_score: 0.8,
        model_name: 'gemini',
        analyzed_at: '2026-06-01T01:00:00Z',
      },
    }
    const v = toVideoDetail(raw)
    expect(v.full_analysis_md).toBe('## 분석')
    expect(v.headline).toBe('헤드라인')
    expect(v.bullet_points).toEqual(['p1', 'p2'])
    expect(v.tags).toEqual(['반도체', 'AI'])
    expect(v.confidence_score).toBe(0.8)
    expect(v.retry_count).toBeNull()
  })

  it('analysis가 null이면 분석 필드는 모두 null', () => {
    const raw = {
      video_pk: 6,
      video_id: 'v6',
      video_url: 'u',
      title: 'T',
      description: null,
      thumbnail_url: null,
      published_at: '2026-06-01T00:00:00Z',
      duration_seconds: null,
      view_count: null,
      like_count: null,
      analysis_status: 'pending',
      analysis_error: null,
      notified_at: null,
      tags: [],
      analysis: null,
    }
    const v = toVideoDetail(raw)
    expect(v.full_analysis_md).toBeNull()
    expect(v.headline).toBeNull()
    expect(v.bullet_points).toBeNull()
    expect(v.sentiment).toBeNull()
  })
})
```
- [ ] **Step 2:** `cd frontend && npx vitest run src/api/adapters.test.ts` → 새 2개 FAIL (toVideoDetail 미존재).
- [ ] **Step 3:** `frontend/src/api/adapters.ts`에 추가(상단 import에 `VideoDetail` 추가: `import type { Video, VideoDetail } from './types'`):
```ts
/** ytdb VideoDetail(중첩 analysis) → 페이지용 평탄화 VideoDetail. */
export function toVideoDetail(raw: Record<string, any>): VideoDetail {
  const a = raw.analysis ?? null
  return {
    video_pk: raw.video_pk,
    video_id: raw.video_id,
    video_url: raw.video_url,
    title: raw.title,
    description: raw.description ?? null,
    thumbnail_url: raw.thumbnail_url ?? null,
    published_at: raw.published_at,
    duration_seconds: raw.duration_seconds ?? null,
    view_count: raw.view_count ?? null,
    like_count: raw.like_count ?? null,
    analysis_status: raw.analysis_status,
    analysis_error: raw.analysis_error ?? null,
    notified_at: raw.notified_at ?? null,
    source_channel_name: raw.source_channel_name ?? null,
    retry_count: raw.retry_count ?? null,
    tags: Array.isArray(raw.tags) ? raw.tags : [],
    one_line: a?.one_line ?? null,
    headline: a?.headline ?? null,
    short_summary_md: a?.short_summary_md ?? null,
    full_analysis_md: a?.full_analysis_md ?? null,
    bullet_points: a?.bullet_points ?? null,
    key_points: a?.key_points ?? null,
    insights: a?.insights ?? null,
    entities: a?.entities ?? null,
    sentiment: a?.sentiment ?? null,
    confidence_score: a?.confidence_score ?? null,
    model_name: a?.model_name ?? null,
    analyzed_at: a?.analyzed_at ?? null,
  }
}
```
- [ ] **Step 4:** `cd frontend && npx vitest run src/api/adapters.test.ts` → 4 passed (기존 2 + 신규 2).
- [ ] **Step 5:** Commit
```bash
git add frontend/src/api/adapters.ts frontend/src/api/adapters.test.ts
git commit -m "feat: toVideoDetail 어댑터(중첩 analysis 평탄화)"
```

---

## Task 7: API 모듈 확장/추가 (videos/channels/logs/tags)

**Files:** Modify `frontend/src/api/videos.ts`; Create `frontend/src/api/channels.ts`, `frontend/src/api/logs.ts`, `frontend/src/api/tags.ts`

- [ ] **Step 1:** `frontend/src/api/videos.ts`를 다음으로 교체(`get`/`remove`/`analyzeNow` 추가, `listPaged`에 `channel_pk`):
```ts
import { groupClient } from './http'
import { toVideo, toVideoDetail } from './adapters'
import type { PaginatedVideos, VideoDetail } from './types'

export function videoApi(slug: string) {
  const c = groupClient(slug)
  return {
    listPaged: async (params: {
      status?: string
      tag?: string
      channel_pk?: number
      limit?: number
      offset?: number
    }): Promise<PaginatedVideos> => {
      const q = new URLSearchParams({ paged: '1' })
      if (params.status) q.set('status', params.status)
      if (params.tag) q.set('tag', params.tag)
      if (params.channel_pk != null) q.set('channel_pk', String(params.channel_pk))
      if (params.limit != null) q.set('limit', String(params.limit))
      if (params.offset != null) q.set('offset', String(params.offset))
      const raw = await c.get<any>(`/videos?${q}`)
      return {
        total: raw.total,
        page: raw.page,
        page_size: raw.page_size,
        items: (raw.items as any[]).map(toVideo),
      }
    },
    get: async (pk: number): Promise<VideoDetail> => toVideoDetail(await c.get<any>(`/videos/${pk}`)),
    remove: (pk: number) => c.del<void>(`/videos/${pk}`),
    analyzeNow: (pk: number) => c.post<{ status: string; video_pk: number }>(`/videos/${pk}/analyze-now`),
  }
}
```
- [ ] **Step 2:** Create `frontend/src/api/channels.ts`:
```ts
import { groupClient } from './http'
import type { Channel, PollResponse } from './types'

export function channelApi(slug: string) {
  const c = groupClient(slug)
  return {
    list: () => c.get<Channel[]>('/channels'),
    add: (body: {
      channel_input: string
      category?: string
      poll_interval_min?: number
      backfill?: boolean
    }) => c.post<Channel>('/channels', body),
    update: (
      pk: number,
      patch: Partial<Pick<Channel, 'is_active' | 'notify_enabled' | 'poll_interval_min' | 'category'>>,
    ) => c.patch<Channel>(`/channels/${pk}`, patch),
    remove: (pk: number) => c.del<void>(`/channels/${pk}`),
    poll: (pk: number) => c.post<PollResponse>(`/channels/${pk}/poll`),
  }
}
```
- [ ] **Step 3:** Create `frontend/src/api/logs.ts`:
```ts
import { groupClient } from './http'
import type { PaginatedJobLogs } from './types'

export function logApi(slug: string) {
  const c = groupClient(slug)
  return {
    listPaged: (params: { job_type?: string; status?: string; limit?: number; offset?: number }) => {
      const q = new URLSearchParams({ paged: '1' })
      if (params.job_type) q.set('job_type', params.job_type)
      if (params.status) q.set('status', params.status)
      if (params.limit != null) q.set('limit', String(params.limit))
      if (params.offset != null) q.set('offset', String(params.offset))
      return c.get<PaginatedJobLogs>(`/logs?${q}`)
    },
  }
}
```
- [ ] **Step 4:** Create `frontend/src/api/tags.ts`:
```ts
import { groupClient } from './http'
import type { Tag } from './types'

export function tagApi(slug: string) {
  return {
    list: (minCount = 1, limit = 200) =>
      groupClient(slug).get<Tag[]>(`/tags?min_count=${minCount}&limit=${limit}`),
  }
}
```
- [ ] **Step 5:** `cd frontend && npx tsc --noEmit` → 에러 없음(App.tsx는 아직 이 모듈들을 직접 안 씀).
- [ ] **Step 6:** Commit
```bash
git add frontend/src/api/videos.ts frontend/src/api/channels.ts frontend/src/api/logs.ts frontend/src/api/tags.ts
git commit -m "feat: videos 확장 + channels/logs/tags API 모듈"
```

---

## Task 8: Channels 페이지

my-assistant `pages/Channels.tsx`를 기반으로 이식하되, ① api를 `channelApi(activeSlug)`로 바인딩, ② **추가 폼을 ytdb `ChannelCreate`에 맞게 교체**(필드: channel_input, category, poll_interval_min, backfill), ③ 폴링 버튼은 `channelApi(activeSlug).poll(pk)`.

**Files:** Create `frontend/src/pages/Channels.tsx`

- [ ] **Step 1:** my-assistant `Channels.tsx`를 읽어 복사한 뒤 아래 변경을 적용한다(나머지 — `ToggleSwitch`, 목록 테이블, 활성/비활성 섹션, 토글/카테고리/주기 blur 저장, 삭제 모달 — 은 그대로 둔다):
  - imports: `import { channelApi } from '../api/client'` / `import type { Channel } from '../api/client'`를 다음으로 교체:
    ```ts
    import { channelApi } from '../api/channels'
    import type { Channel } from '../api/types'
    import { useGroup } from '../group/useGroup'
    ```
  - 컴포넌트 본문 첫 줄에 `const { activeSlug } = useGroup()` 추가. 이후 모든 `channelApi.xxx(` 호출을 `channelApi(activeSlug).xxx(`로 교체(`list`, `update`, `remove`, `poll`).
  - `addForm` state를 ytdb 스키마에 맞게 교체:
    ```ts
    const [addForm, setAddForm] = useState({ channel_input: '', category: '', poll_interval_min: 720, backfill: false })
    ```
  - `handleAdd`의 본문 add 호출을 교체:
    ```ts
      const ch = await channelApi(activeSlug).add({
        channel_input: addForm.channel_input,
        category: addForm.category || undefined,
        poll_interval_min: addForm.poll_interval_min,
        backfill: addForm.backfill,
      })
      setChannels((prev) => [ch, ...prev])
      setAdding(false)
      setAddForm({ channel_input: '', category: '', poll_interval_min: 720, backfill: false })
    ```
  - 추가 폼 JSX에서 **"수동 분석 전용 등록" 체크박스 블록과 `auto_poll_now`("즉시 모니터링") 체크박스, `is_active` 조건부 렌더를 제거**한다. 모니터링 주기 입력은 항상 표시. `notify_enabled` 체크박스도 제거(생성 후 행에서 토글). 대신 "과거 영상 수집" 체크박스를 추가:
    ```tsx
    <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
      <input
        type="checkbox"
        checked={addForm.backfill}
        onChange={(e) => setAddForm({ ...addForm, backfill: e.target.checked })}
      />
      과거 영상 수집(등록 시 1회)
    </label>
    ```
  - 목록 테이블의 `handlePoll`은 `channelApi(activeSlug).poll(ch.channel_pk)`를 호출하고 `r.message`를 alert(기존 로직 유지, api 바인딩만 변경).
- [ ] **Step 2:** `cd frontend && npx tsc --noEmit`. App.tsx가 아직 Channels를 라우트하지 않으므로(Plan 1 Placeholder), 이 파일 자체에 타입 에러만 없으면 된다.
- [ ] **Step 3:** Commit
```bash
git add frontend/src/pages/Channels.tsx
git commit -m "feat: Channels 페이지(추가 폼 ytdb 스키마 적응 + 채널별 폴링)"
```

---

## Task 9: Videos 페이지

my-assistant `pages/Videos.tsx`를 기반으로 이식. ① `videoApi(activeSlug).listPaged` 사용(채널/상태/태그 필터 + 페이지네이션), ② 채널/태그 드롭다운은 `channelApi(activeSlug).list`/`tagApi(activeSlug).list`, ③ 상세 링크 `/youtube/videos/:pk` → `/g/:slug/videos/:pk`, ④ 삭제는 `videoApi(activeSlug).remove`.

**Files:** Create `frontend/src/pages/Videos.tsx`

- [ ] **Step 1:** my-assistant `Videos.tsx`를 읽어 복사 후 변경:
  - imports를 교체:
    ```ts
    import { videoApi } from '../api/videos'
    import { channelApi } from '../api/channels'
    import { tagApi } from '../api/tags'
    import type { Video, Channel, Tag } from '../api/types'
    import { useGroup } from '../group/useGroup'
    ```
    (`StatusBadge`, `NotifyBadge`, `Pagination`, `Spinner`, `ErrorBanner`, `dayjs`, `useSearchParams`, `Link` import는 유지.)
  - 컴포넌트 첫 줄에 `const { activeSlug } = useGroup()`.
  - `load`의 Promise.all을 교체:
    ```ts
      const [r, chs, tgs] = await Promise.all([
        videoApi(activeSlug).listPaged({ channel_pk: channelPk, tag: tagFilter, status: statusFilter, limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE }),
        channelApi(activeSlug).list(),
        tagApi(activeSlug).list(2, 50),
      ])
      setVideos(r.items); setTotal(r.total); setChannels(chs); setTags(tgs)
    ```
    (my-assistant 원본은 `analysis_status`/`page`/`page_size`를 썼지만 ytdb listPaged는 `status`/`limit`/`offset`을 받는다 — 위처럼 매핑. `useEffect` 의존성 배열은 원본 그대로 `[page, channelPk, tagFilter, statusFilter]` 유지.)
  - 모든 `videoApi.remove(` → `videoApi(activeSlug).remove(`.
  - 상세 링크 `to={`/youtube/videos/${v.video_pk}`}` → `to={`/g/${activeSlug}/videos/${v.video_pk}`}`.
  - 나머지(필터 바, 카드 목록, 삭제 모달, Pagination)는 그대로. `v.summary?.one_line`, `v.source_channel_name`, `v.view_count`는 `toVideo`가 채워준다.
- [ ] **Step 2:** `cd frontend && npx tsc --noEmit` — 이 파일에 타입 에러 없어야 함.
- [ ] **Step 3:** Commit
```bash
git add frontend/src/pages/Videos.tsx
git commit -m "feat: Videos 페이지(필터·페이지네이션·삭제, 그룹 스코프)"
```

---

## Task 10: VideoDetail 페이지 (v2 기능 제외)

my-assistant `pages/VideoDetail.tsx`를 기반으로 이식하되 **텔레그램 발송/미리보기와 커스텀 프롬프트를 제거**하고, 재분석은 `analyzeNow`로 트리거 후 라이브 폴링한다.

**Files:** Create `frontend/src/pages/VideoDetail.tsx`

- [ ] **Step 1:** my-assistant `VideoDetail.tsx`를 읽어 복사 후 다음을 적용:
  - imports 교체:
    ```ts
    import { videoApi } from '../api/videos'
    import type { VideoDetail as VideoDetailType } from '../api/types'
    import { useGroup } from '../group/useGroup'
    ```
    (`ReactMarkdown`, `remarkGfm`, `dayjs`, `Spinner`, `ErrorBanner`, `StatusBadge`, `useParams`/`Link`/`useNavigate` 유지. `promptApi` import 제거.)
  - 컴포넌트 첫 줄에 `const { activeSlug } = useGroup()`.
  - **제거할 것 (전부 삭제):**
    - 커스텀 프롬프트 관련 state(`promptOpen`, `customPrompt`, `promptLoaded`), `handleOpenPrompt`, "프롬프트 수정" 버튼, amber 프롬프트 패널 JSX.
    - 텔레그램 관련: `notifying` state, `handleNotify`, "Telegram 발송/재발송" 버튼, "Telegram 알림 미리보기" 블록(`showTelegramPreview` 포함).
  - **데이터 로드:** `load`/`silentRefresh`에서 `videoApi.get(Number(videoPk))` → `videoApi(activeSlug).get(Number(videoPk))`.
  - **재분석:** `handleReanalyze`에서 `videoApi.reanalyze(Number(videoPk), prompt)` → `videoApi(activeSlug).analyzeNow(Number(videoPk))` (prompt 인자 제거). 폴링 로직(2초 간격 `silentRefresh`, done/failed 시 중단, 3분 타임아웃)은 그대로 유지. 재분석 버튼 라벨은 `reanalyzing ? '분석 중...' : '재분석'`.
  - **삭제:** `videoApi.remove(...)` → `videoApi(activeSlug).remove(...)`, 성공 시 `navigate(`/g/${activeSlug}/videos`)`. 상단 브레드크럼 "영상 목록" 링크 `to="/youtube/videos"` → `to={`/g/${activeSlug}/videos`}`.
  - **태그 링크:** `to={`/youtube/videos?tag=...`}` → `to={`/g/${activeSlug}/videos?tag=${encodeURIComponent(t)}`}`.
  - **retry_count 가드:** 분석 오류 블록의 `재시도: {video.retry_count}회`를 `{video.retry_count != null && <p className="text-gray-500">재시도: {video.retry_count}회</p>}`로 감싼다(ytdb는 retry_count를 상세에 안 줄 수 있어 null 가능).
  - 나머지(헤더, 썸네일, 상태/메타, 태그, 설명 details, 상세 분석 ReactMarkdown, 요약, 분석 정보+ConfidenceBar, 분석 오류, 삭제 확인 모달)는 유지. 필드는 `toVideoDetail`가 평탄화해 제공한다.
- [ ] **Step 2:** `cd frontend && npx tsc --noEmit` — 이 파일 타입 에러 없어야 함. (제거된 promptApi/notify 참조가 남아있지 않은지 확인.)
- [ ] **Step 3:** Commit
```bash
git add frontend/src/pages/VideoDetail.tsx
git commit -m "feat: VideoDetail 페이지(라이브 폴링·마크다운, 텔레그램/커스텀프롬프트 제외)"
```

---

## Task 11: InstantAnalyze 페이지 (커스텀 프롬프트 제외)

my-assistant `pages/InstantAnalyze.tsx`를 기반으로, 커스텀 프롬프트 제거 + ytdb instant 엔드포인트/응답에 맞춤.

**Files:** Create `frontend/src/pages/InstantAnalyze.tsx`

- [ ] **Step 1:** my-assistant `InstantAnalyze.tsx`를 읽어 복사 후 변경:
  - imports 교체: `import { instantApi, promptApi, videoApi } from '../api/client'` 제거. 대신:
    ```ts
    import { videoApi } from '../api/videos'
    import { groupClient } from '../api/http'
    import type { InstantAnalyzeResponse } from '../api/types'
    import { useGroup } from '../group/useGroup'
    ```
  - 컴포넌트 첫 줄에 `const { activeSlug } = useGroup()`.
  - **커스텀 프롬프트 제거:** `promptOpen`, `customPrompt`, `promptLoaded` state, `handlePromptToggle`, 프롬프트 토글 버튼 + amber 패널 JSX 전부 삭제.
  - **제출 로직 교체** — ytdb instant 엔드포인트는 `POST /videos/instant {video_url}` → `{video_pk, video_id, existing, queued}` (message 없음). `handleSubmit` 내부를 다음으로:
    ```ts
      try {
        const res = await groupClient(activeSlug).post<InstantAnalyzeResponse>('/videos/instant', { video_url: url.trim() })
        if (res.existing) {
          setMessage('이미 분석된 영상입니다. 결과로 이동합니다.')
          setPhase('done')
          setTimeout(() => navigate(`/g/${activeSlug}/videos/${res.video_pk}`), 1000)
          return
        }
        setMessage('분석 대기열에 등록되었습니다. 완료되면 결과로 이동합니다.')
        setPhase('analyzing')
        const videoPk = res.video_pk
        pollingRef.current = setInterval(async () => {
          try {
            const detail = await videoApi(activeSlug).get(videoPk)
            if (detail.analysis_status === 'done' || detail.analysis_status === 'failed') {
              stopPolling(); setPhase('done')
              setTimeout(() => navigate(`/g/${activeSlug}/videos/${videoPk}`), 800)
            }
          } catch { /* 폴링 실패 무시 */ }
        }, 3000)
        setTimeout(() => {
          if (pollingRef.current) { stopPolling(); setPhase('done'); navigate(`/g/${activeSlug}/videos/${videoPk}`) }
        }, 5 * 60 * 1000)
      } catch (err) {
        setPhase('error'); setMessage((err as Error).message)
      }
    ```
  - 나머지(URL 입력, 상태 메시지, 분석 중 스피너, 제출 버튼, 안내 박스)는 유지.
- [ ] **Step 2:** `cd frontend && npx tsc --noEmit` — 이 파일 타입 에러 없어야 함.
- [ ] **Step 3:** Commit
```bash
git add frontend/src/pages/InstantAnalyze.tsx
git commit -m "feat: InstantAnalyze 페이지(폴링→상세 이동, 커스텀프롬프트 제외)"
```

---

## Task 12: Logs 페이지

my-assistant `pages/Jobs.tsx`를 기반으로 이식. `logApi(activeSlug).listPaged` 사용.

**Files:** Create `frontend/src/pages/Logs.tsx`

- [ ] **Step 1:** my-assistant `Jobs.tsx`를 읽어 복사 후 변경:
  - imports: `import { jobApi, JobLog } from '../api/client'` → 
    ```ts
    import { logApi } from '../api/logs'
    import type { JobLog } from '../api/types'
    import { useGroup } from '../group/useGroup'
    ```
    (`Spinner`, `ErrorBanner`, `Pagination` 유지.)
  - 컴포넌트 첫 줄에 `const { activeSlug } = useGroup()`.
  - `fetchJobs`의 `jobApi.list({...})` 호출을 교체:
    ```ts
      const res = await logApi(activeSlug).listPaged({
        job_type: jobType || undefined,
        status: status || undefined,
        limit: PAGE_SIZE,
        offset: (p - 1) * PAGE_SIZE,
      })
      setItems(res.items); setTotal(res.total); setLastRefreshed(new Date())
    ```
    (`fetchJobs`의 `useCallback` 의존성에 `activeSlug` 추가: `[jobType, status, page, activeSlug]`.)
  - 나머지(요약 배지, 필터, 테이블, 30초 자동 새로고침, Pagination)는 그대로.
- [ ] **Step 2:** `cd frontend && npx tsc --noEmit` — 이 파일 타입 에러 없어야 함.
- [ ] **Step 3:** Commit
```bash
git add frontend/src/pages/Logs.tsx
git commit -m "feat: Logs 페이지(필터·요약배지·자동갱신·페이지네이션)"
```

---

## Task 13: App 라우터에 실제 페이지 연결 + 빌드

**Files:** Modify `frontend/src/App.tsx`

- [ ] **Step 1:** `frontend/src/App.tsx`에서 Placeholder를 실제 페이지로 교체. import 추가:
```ts
import Channels from './pages/Channels'
import Videos from './pages/Videos'
import VideoDetail from './pages/VideoDetail'
import InstantAnalyze from './pages/InstantAnalyze'
import Logs from './pages/Logs'
```
라우트 블록을 교체:
```tsx
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="channels" element={<Channels />} />
          <Route path="videos" element={<Videos />} />
          <Route path="videos/:videoPk" element={<VideoDetail />} />
          <Route path="instant-analyze" element={<InstantAnalyze />} />
          <Route path="logs" element={<Logs />} />
          <Route path="*" element={<Navigate to="." replace />} />
        </Route>
```
그리고 `Placeholder` 함수 정의를 제거(더 이상 사용 안 함).
- [ ] **Step 2:** `cd frontend && npx tsc --noEmit` → 에러 없음. 이어서 `npm run build` → 성공, `../app/static/ui/` 갱신.
- [ ] **Step 3:** Commit
```bash
git add frontend/src/App.tsx
git commit -m "feat: v1a 페이지 라우트 연결(채널/영상/상세/분석/로그) + 빌드"
```

---

## Task 14: 수동 통합 검증 (DB 필요)

- [ ] **Step 1:** 백엔드 `uvicorn app.main:app --reload --port 8000`, 프론트 `cd frontend && npm run dev`.
- [ ] **Step 2:** `/app/g/<slug>/channels` — 채널 추가(@handle/URL), 카테고리/주기 blur 저장, 활성/알림 토글, "모니터링" 버튼(단건 폴링 → 알림 메시지), 삭제 확인.
- [ ] **Step 3:** `/app/g/<slug>/videos` — 채널/상태/태그 필터, 페이지네이션, 카드 클릭 → 상세, 삭제. 필터가 URL 쿼리에 반영되는지(새로고침 유지).
- [ ] **Step 4:** 상세 화면 — 마크다운 렌더, "재분석" 클릭 시 상태가 processing→done로 **자동 갱신**(라이브 폴링), 태그 클릭 시 필터된 목록으로 이동, 삭제 후 목록 복귀. (텔레그램/프롬프트 버튼이 없어야 함 = v2)
- [ ] **Step 5:** `/app/g/<slug>/instant-analyze` — URL 입력 → 등록 → 분석 완료까지 폴링 → 상세 자동 이동. 이미 분석된 URL은 즉시 상세 이동.
- [ ] **Step 6:** `/app/g/<slug>/logs` — job_type/status 필터, 요약 배지, 30초 자동 새로고침, 페이지네이션.
- [ ] **Step 7:** 그룹 2개 이상에서 격리 확인(A 그룹 채널/영상/로그가 B에 안 보임). 빌드본(`npm run build` 후 `http://localhost:8000/app/`)과 vanilla(`/`) 동시 정상.

---

## Self-Review 결과 (작성자 기록)

- **스펙 커버리지**: Channels(Task 8)·Videos(9)·VideoDetail(10)·InstantAnalyze(11)·Logs(12) = §3 매핑 v1a 나머지 전부. 백엔드: channel_pk 필터(1)·로그 필터/페이지네이션(2)·채널 단건 폴링(3). 라이브 폴링(VideoDetail/InstantAnalyze), 필터 URL 영속(Videos), 자동 새로고침(Logs) 모두 포함.
- **v2 제외 일관성**: 텔레그램 발송/미리보기·커스텀 프롬프트는 Task 10/11에서 명시 제거. 채널 생성의 is_active/auto_poll_now는 ytdb 스키마에 맞춰 제외(Task 8) — 생성 후 행 토글로 대체.
- **타입/시그니처 일관성**: `videoApi(slug).{listPaged,get,remove,analyzeNow}`, `channelApi(slug).{list,add,update,remove,poll}`, `logApi(slug).listPaged`, `tagApi(slug).list`, `toVideoDetail`, 타입 `Channel/VideoDetail/Tag/JobLog/PaginatedJobLogs/InstantAnalyzeResponse/PollResponse` — Task 5~7에서 정의, 8~13에서 동일 사용.
- **백엔드 비파괴**: videos/logs 비-paged 경로 무변경(vanilla 호환), 채널 폴링은 신규 엔드포인트, monitor_service는 함수 추가만.
- **위험**: ① `poll_single_channel`이 `_poll_group`의 헬퍼 이름에 의존 → Task 3에서 실제 이름 확인 지시. ② 페이지 이식은 copy+적응이라 누락 위험 → 각 Task에 정확한 변경 목록 명시, tsc로 검출. ③ Pagination Fragment key 경고 가능 → Task 4에 대응 지시.
