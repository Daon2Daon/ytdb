# React 마이그레이션 Plan 4 — 주간 리뷰 + 텔레그램 수동발송 + 커스텀 프롬프트 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** v2 마지막 기능 3종을 새 React UI에 추가한다 — ① 주간 리뷰(다이제스트) 목록/상세/생성, ② 영상 상세에서 텔레그램 수동 발송 + 미리보기, ③ 영상별 커스텀 프롬프트 재분석.

**Architecture:** 다이제스트는 기존 엔드포인트(list/get/delete/generate)를 그대로 쓰는 프론트 작업이다. 텔레그램 수동발송은 기존 `notify_video` 서비스를 재사용하는 신규 엔드포인트 1개. 커스텀 프롬프트는 `build_analysis_pipeline`이 이미 `analysis_prompt`를 인자로 받으므로 override 파라미터를 더해 `analyze-now`로 흘려보낸다(파이프라인 로직 무변경, 인자 추가만).

**Tech Stack:** FastAPI · SQLAlchemy(async) · pytest · React 18 · TypeScript · React Router v6 · Tailwind · react-markdown/remark-gfm · Vitest

**관련 스펙:** `docs/superpowers/specs/2026-06-03-react-migration-design.md`
**선행:** Plan 1~3 완료. `videoApi`(`api/videos.ts`)에 `get/remove/analyzeNow`, `settingsApi`(`api/settings.ts`), Layout 네비, GroupProvider 존재. VideoDetail/InstantAnalyze는 Plan 2에서 텔레그램·커스텀프롬프트를 제거한 상태(본 플랜에서 ytdb 방식으로 재추가).

**참고 원본(읽기 전용):**
- 텔레그램/프롬프트 UI 패턴: my-assistant `src/pages/VideoDetail.tsx`, `src/pages/InstantAnalyze.tsx`
- 다이제스트 UI/필드: vanilla `app/static/app.js` (renderDigests 762~800행, openDigest 802~820행) — ytdb 필드명 정확.

---

## File Structure
### 백엔드 (추가/수정)
- Modify `app/services/analyzer.py` — `build_analysis_pipeline`에 `analysis_prompt_override` 파라미터
- Modify `app/services/monitor_service.py` — `analyze_specific_video`에 `custom_prompt` 파라미터
- Modify `app/routers/videos.py` — `analyze-now`가 선택적 `custom_prompt` 본문 수용 + 신규 `POST /{pk}/notify`
- Test `tests/test_analyze_now_prompt.py` — analyze-now 본문 파싱(순수/스키마) 스모크

### 프론트엔드 (`frontend/src/`)
- Create `api/digests.ts` + 타입(`Digest`) in `api/types.ts`
- Modify `api/videos.ts` — `notify(pk, force)`, `analyzeNow(pk, customPrompt?)`
- Create `api/prompts.ts` — 그룹 기본 분석 프롬프트 조회(settings/prompts 재사용)
- Create `pages/Digests.tsx`, `pages/DigestDetail.tsx`
- Modify `pages/VideoDetail.tsx` — 텔레그램 발송/미리보기 + 커스텀 프롬프트 패널 재추가
- Modify `pages/InstantAnalyze.tsx` — 커스텀 프롬프트 패널(선택) 재추가
- Modify `components/Layout.tsx` — 운영 네비에 "주간 리뷰" 추가
- Modify `App.tsx` — `digests`, `digests/:digestPk` 라우트

---

## Task 1: 커스텀 프롬프트 백엔드 배선

**Files:** Modify `app/services/analyzer.py`, `app/services/monitor_service.py`, `app/routers/videos.py`

- [ ] **Step 1:** `app/services/analyzer.py`의 `build_analysis_pipeline`를 수정. 현재:
```python
async def build_analysis_pipeline(
    group_id: int, notify_callback: Optional[Callable[[int], Awaitable[Any]]] = None
) -> AnalysisPipeline:
    mgr = get_settings_manager()
    ai = await mgr.get_ai_gateway(group_id)
    prompts = await mgr.get_prompts(group_id)
    llm = LiteLLMClient(settings=ai)
    return AnalysisPipeline(
        llm_client=llm,
        ai_settings=ai,
        analysis_prompt=prompts.analysis_prompt,
        notify_callback=notify_callback,
    )
```
시그니처에 `analysis_prompt_override: Optional[str] = None`를 추가하고 `analysis_prompt=` 라인을 교체:
```python
async def build_analysis_pipeline(
    group_id: int,
    notify_callback: Optional[Callable[[int], Awaitable[Any]]] = None,
    analysis_prompt_override: Optional[str] = None,
) -> AnalysisPipeline:
    mgr = get_settings_manager()
    ai = await mgr.get_ai_gateway(group_id)
    prompts = await mgr.get_prompts(group_id)
    llm = LiteLLMClient(settings=ai)
    return AnalysisPipeline(
        llm_client=llm,
        ai_settings=ai,
        analysis_prompt=(analysis_prompt_override.strip() if analysis_prompt_override and analysis_prompt_override.strip() else prompts.analysis_prompt),
        notify_callback=notify_callback,
    )
```

- [ ] **Step 2:** `app/services/monitor_service.py`의 `analyze_specific_video` 시그니처에 `custom_prompt`를 추가하고 파이프라인 빌드에 전달. 현재 `async def analyze_specific_video(group: Group, video_pk: int) -> None:` 와 그 내부 `pipeline = await build_analysis_pipeline(group.group_id)`를 다음으로:
```python
async def analyze_specific_video(group: Group, video_pk: int, custom_prompt: Optional[str] = None) -> None:
```
그리고:
```python
    pipeline = await build_analysis_pipeline(group.group_id, analysis_prompt_override=custom_prompt)
```
(`Optional`은 이미 import되어 있다 — 파일 상단 확인. 없으면 `from typing import Optional` 추가.)

- [ ] **Step 3:** `app/routers/videos.py`의 `analyze-now` 엔드포인트가 선택적 본문을 받도록 수정. 상단에 본문 스키마를 추가(파일 내 다른 Pydantic 정의 근처 또는 함수 위):
```python
from pydantic import BaseModel  # 이미 import돼 있으면 생략

class AnalyzeNowRequest(BaseModel):
    custom_prompt: Optional[str] = None
```
현재 엔드포인트:
```python
@router.post("/{video_pk}/analyze-now", status_code=202)
async def analyze_video_now(
    video_pk: int,
    background: BackgroundTasks,
    group: Group = Depends(get_group_or_404),
) -> dict:
    ...
    background.add_task(analyze_specific_video, group, video_pk)
    return {"status": "started", "video_pk": video_pk}
```
을 다음으로(본문 선택 수용 + custom_prompt 전달):
```python
@router.post("/{video_pk}/analyze-now", status_code=202)
async def analyze_video_now(
    video_pk: int,
    background: BackgroundTasks,
    payload: AnalyzeNowRequest | None = None,
    group: Group = Depends(get_group_or_404),
) -> dict:
    async with dpm.group_session(group) as session:
        async with session.begin():
            result = await session.execute(
                update(Video)
                .where(Video.video_pk == video_pk)
                .values(analysis_status="pending", analysis_error=None, retry_count=0)
            )
            if (result.rowcount or 0) == 0:
                raise HTTPException(status_code=404, detail="영상을 찾을 수 없습니다.")
    custom = payload.custom_prompt if payload else None
    background.add_task(analyze_specific_video, group, video_pk, custom)
    return {"status": "started", "video_pk": video_pk}
```
(기존 본문의 update 로직은 그대로 두고 `payload` 파라미터와 `custom` 전달만 추가한다. 위 코드로 함수 전체를 교체해도 동일하다.)

- [ ] **Step 4:** 검증 — `python -c "from app.main import app; print('ok')"` → ok. `pytest -q` → 기존 통과 유지.
- [ ] **Step 5:** Commit
```bash
git add app/services/analyzer.py app/services/monitor_service.py app/routers/videos.py
git commit -m "feat: analyze-now 커스텀 프롬프트 수용(파이프라인 prompt override)"
```

---

## Task 2: 텔레그램 수동 발송 엔드포인트

**Files:** Modify `app/routers/videos.py`

- [ ] **Step 1:** `app/routers/videos.py` 상단 import 보강(없는 것만 추가): `from datetime import datetime, timezone`, `from app.models.pg.video_analysis import VideoAnalysis`(이미 있으면 생략), `from app.services.notify_service import notify_video`, `from app.services.settings_manager import get_settings_manager`(이미 있음). 본문 스키마 추가:
```python
class NotifyRequest(BaseModel):
    force: bool = False

class VideoNotifyResponse(BaseModel):
    success: bool
    message: str
    notified_at: Optional[datetime] = None
```
- [ ] **Step 2:** 신규 엔드포인트 추가(예: `delete_video` 아래):
```python
@router.post("/{video_pk}/notify", response_model=VideoNotifyResponse)
async def notify_video_now(
    video_pk: int,
    payload: NotifyRequest | None = None,
    group: Group = Depends(get_group_or_404),
) -> VideoNotifyResponse:
    force = payload.force if payload else False
    notif = await get_settings_manager().get_notification(group.group_id)
    async with dpm.group_session(group) as session:
        video = (
            await session.execute(select(Video).where(Video.video_pk == video_pk))
        ).scalar_one_or_none()
        if video is None:
            raise HTTPException(status_code=404, detail="영상을 찾을 수 없습니다.")
        if video.analysis_status != "done":
            raise HTTPException(status_code=400, detail="분석이 완료된 영상만 발송할 수 있습니다.")
        analysis = (
            await session.execute(select(VideoAnalysis).where(VideoAnalysis.video_pk == video_pk))
        ).scalar_one_or_none()
        if analysis is None:
            raise HTTPException(status_code=400, detail="분석 결과가 없어 발송할 수 없습니다.")
        if video.notified_at is not None and not force:
            return VideoNotifyResponse(
                success=False,
                message="이미 발송된 영상입니다. 재발송하려면 force=true로 요청하세요.",
                notified_at=video.notified_at,
            )
        try:
            sent = await notify_video(notif, video, analysis)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"발송 실패: {e}") from e
        if sent == 0:
            raise HTTPException(status_code=400, detail="발송된 메시지가 없습니다. 알림 설정(봇 토큰/Chat ID)을 확인하세요.")
        now = datetime.now(timezone.utc)
        async with session.begin():
            await session.execute(
                update(Video).where(Video.video_pk == video_pk).values(notified_at=now)
            )
    return VideoNotifyResponse(success=True, message=f"{sent}개 대상에 발송했습니다.", notified_at=now)
```
- [ ] **Step 3:** 검증 — `python -c "from app.main import app; print('ok')"` → ok. `pytest -q` → 통과.
- [ ] **Step 4:** Commit
```bash
git add app/routers/videos.py
git commit -m "feat: 영상 텔레그램 수동 발송 엔드포인트(POST /videos/{pk}/notify)"
```

---

## Task 3: 백엔드 스모크 테스트(선택 본문 파싱)

DB 없이 검증 가능한 부분: analyze-now가 본문 없이도/있어도 라우팅되고, 스키마가 올바른지. TestClient로 404(영상 없음) 경로를 확인(DB 미설정이면 400 가능 — 두 경우 모두 500이 아니면 OK).

**Files:** Create `tests/test_plan4_endpoints.py`

- [ ] **Step 1:** Create `tests/test_plan4_endpoints.py`:
```python
from fastapi.testclient import TestClient
from app.main import app
from app.routers.videos import AnalyzeNowRequest, NotifyRequest, VideoNotifyResponse


def test_analyze_now_request_optional_prompt():
    assert AnalyzeNowRequest().custom_prompt is None
    assert AnalyzeNowRequest(custom_prompt="x").custom_prompt == "x"


def test_notify_request_default_force_false():
    assert NotifyRequest().force is False


def test_notify_response_shape():
    r = VideoNotifyResponse(success=True, message="ok")
    assert r.success is True and r.notified_at is None


def test_routes_registered():
    paths = {r.path for r in app.routes}
    assert "/api/groups/{slug}/videos/{video_pk}/notify" in paths
    assert "/api/groups/{slug}/videos/{video_pk}/analyze-now" in paths
```
- [ ] **Step 2:** `pytest tests/test_plan4_endpoints.py -q` → 4 passed.
- [ ] **Step 3:** Commit
```bash
git add tests/test_plan4_endpoints.py
git commit -m "test: Plan4 엔드포인트 스키마/라우트 스모크"
```

---

## Task 4: 다이제스트 API + videoApi 확장 + promptApi

**Files:** Modify `frontend/src/api/types.ts`, `frontend/src/api/videos.ts`; Create `frontend/src/api/digests.ts`, `frontend/src/api/prompts.ts`

- [ ] **Step 1:** `frontend/src/api/types.ts` 끝에 추가:
```ts
export interface TagCount { name: string; count: number }

export interface Digest {
  digest_pk: number
  period_type: string
  period_weeks: number
  period_start: string
  period_end: string
  category: string | null
  video_count: number
  headline: string | null
  summary_md: string | null
  telegram_summary: string | null
  sentiment_breakdown: Record<string, number> | null
  top_tags: TagCount[] | null
  top_channels: TagCount[] | null
  status: string
  error: string | null
  created_at: string
}

export interface VideoNotifyResponse {
  success: boolean
  message: string
  notified_at: string | null
}
```
- [ ] **Step 2:** Create `frontend/src/api/digests.ts`:
```ts
import { groupClient } from './http'
import type { Digest } from './types'

export function digestApi(slug: string) {
  const c = groupClient(slug)
  return {
    list: () => c.get<Digest[]>('/digests'),
    get: (pk: number) => c.get<Digest>(`/digests/${pk}`),
    remove: (pk: number) => c.del<void>(`/digests/${pk}`),
    generate: () => c.post<Digest>('/digests/generate', { save: true }),
  }
}
```
- [ ] **Step 3:** Create `frontend/src/api/prompts.ts` (그룹 기본 분석 프롬프트를 settings에서 추출):
```ts
import { settingsApi } from './settings'

export function promptApi(slug: string) {
  return {
    getAnalysisPrompt: async (): Promise<string> => {
      const items = await settingsApi(slug).get('prompts')
      return items.find((i) => i.key === 'analysis_prompt')?.value ?? ''
    },
  }
}
```
- [ ] **Step 4:** `frontend/src/api/videos.ts`의 `videoApi`에 `notify`와 `analyzeNow(customPrompt?)`를 추가/수정. `analyzeNow`를 교체하고 `notify`를 추가:
```ts
    analyzeNow: (pk: number, customPrompt?: string) =>
      c.post<{ status: string; video_pk: number }>(`/videos/${pk}/analyze-now`, { custom_prompt: customPrompt ?? null }),
    notify: (pk: number, force = false) =>
      c.post<import('./types').VideoNotifyResponse>(`/videos/${pk}/notify`, { force }),
```
(기존 `listPaged`, `get`, `remove`는 유지.)
- [ ] **Step 5:** `cd frontend && npx tsc --noEmit` → 에러 없음.
- [ ] **Step 6:** Commit
```bash
git add frontend/src/api/types.ts frontend/src/api/digests.ts frontend/src/api/prompts.ts frontend/src/api/videos.ts
git commit -m "feat: digests/prompts API + videoApi notify/analyzeNow(prompt)"
```

---

## Task 5: Digests 목록 페이지

**Files:** Create `frontend/src/pages/Digests.tsx`

- [ ] **Step 1:** Create `frontend/src/pages/Digests.tsx`:
```tsx
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import dayjs from 'dayjs'
import { useGroup } from '../group/useGroup'
import { digestApi } from '../api/digests'
import type { Digest } from '../api/types'
import Spinner from '../components/Spinner'
import ErrorBanner from '../components/ErrorBanner'

export default function Digests() {
  const { activeSlug } = useGroup()
  const [items, setItems] = useState<Digest[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [generating, setGenerating] = useState(false)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      setItems(await digestApi(activeSlug).list())
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [activeSlug])

  const handleGenerate = async () => {
    setGenerating(true)
    try {
      await digestApi(activeSlug).generate()
      await load()
    } catch (e) {
      alert((e as Error).message)
    } finally {
      setGenerating(false)
    }
  }

  const handleDelete = async (pk: number) => {
    if (!window.confirm('이 주간 리뷰를 삭제할까요?')) return
    try {
      await digestApi(activeSlug).remove(pk)
      setItems((prev) => prev.filter((d) => d.digest_pk !== pk))
    } catch (e) {
      alert((e as Error).message)
    }
  }

  if (loading) return <Spinner />

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">주간 리뷰</h1>
        <button onClick={handleGenerate} disabled={generating}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-60">
          {generating ? '생성 중...' : '지금 생성'}
        </button>
      </div>
      {error && <ErrorBanner message={error} onRetry={load} />}
      {items.length === 0 ? (
        <div className="bg-white rounded-xl shadow-sm py-16 text-center text-gray-400">
          <p className="text-5xl mb-3">📊</p>
          <p>주간 리뷰가 없습니다. "지금 생성"으로 만들어 보세요.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {items.map((d) => (
            <div key={d.digest_pk} className="bg-white rounded-xl shadow-sm p-4 flex items-center gap-4">
              <Link to={`/g/${activeSlug}/digests/${d.digest_pk}`} className="flex-1 min-w-0">
                <p className="font-medium text-gray-900 truncate">{d.headline || '주간 리뷰'}</p>
                <p className="text-xs text-gray-500 mt-0.5">
                  {dayjs(d.period_start).format('YYYY-MM-DD')} ~ {dayjs(d.period_end).format('YYYY-MM-DD')}
                  {' · '}영상 {d.video_count}건 · {d.status}
                </p>
              </Link>
              <button onClick={() => handleDelete(d.digest_pk)}
                className="px-2.5 py-1.5 text-xs rounded bg-red-50 text-red-500 hover:bg-red-100 shrink-0">삭제</button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
```
- [ ] **Step 2:** `cd frontend && npx tsc --noEmit` → 에러 없음.
- [ ] **Step 3:** Commit
```bash
git add frontend/src/pages/Digests.tsx
git commit -m "feat: Digests 목록 페이지(생성/삭제)"
```

---

## Task 6: DigestDetail 페이지

**Files:** Create `frontend/src/pages/DigestDetail.tsx`

- [ ] **Step 1:** Create `frontend/src/pages/DigestDetail.tsx`:
```tsx
import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import dayjs from 'dayjs'
import { useGroup } from '../group/useGroup'
import { digestApi } from '../api/digests'
import type { Digest } from '../api/types'
import Spinner from '../components/Spinner'
import ErrorBanner from '../components/ErrorBanner'

export default function DigestDetail() {
  const { activeSlug } = useGroup()
  const { digestPk } = useParams<{ digestPk: string }>()
  const [digest, setDigest] = useState<Digest | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = async () => {
    if (!digestPk) return
    setLoading(true)
    setError(null)
    try {
      setDigest(await digestApi(activeSlug).get(Number(digestPk)))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [activeSlug, digestPk])

  if (loading) return <Spinner />
  if (error) return <ErrorBanner message={error} onRetry={load} />
  if (!digest) return null

  return (
    <div className="space-y-5 max-w-3xl">
      <div className="flex items-center gap-2 text-sm text-gray-500">
        <Link to={`/g/${activeSlug}/digests`} className="hover:text-blue-600">주간 리뷰</Link>
        <span>/</span>
        <span className="text-gray-700 truncate">{digest.headline || '주간 리뷰'}</span>
      </div>

      <div className="bg-white rounded-xl shadow-sm p-5 space-y-2">
        <h1 className="text-xl font-bold text-gray-900">{digest.headline || '주간 리뷰'}</h1>
        <p className="text-sm text-gray-500">
          {dayjs(digest.period_start).format('YYYY-MM-DD')} ~ {dayjs(digest.period_end).format('YYYY-MM-DD')}
          {' · '}분석 영상 {digest.video_count}건 · 상태 {digest.status}
        </p>
        {digest.error && <p className="text-sm text-red-600">{digest.error}</p>}
      </div>

      {digest.summary_md && (
        <div className="bg-white rounded-xl shadow-sm p-5">
          <h2 className="font-semibold text-gray-800 mb-3">요약</h2>
          <article className="prose prose-sm max-w-none text-gray-700 break-words">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{digest.summary_md}</ReactMarkdown>
          </article>
        </div>
      )}

      {digest.top_tags && digest.top_tags.length > 0 && (
        <div className="bg-white rounded-xl shadow-sm p-5">
          <h2 className="font-semibold text-gray-800 mb-3">상위 태그</h2>
          <div className="flex flex-wrap gap-2">
            {digest.top_tags.map((t) => (
              <span key={t.name} className="px-2.5 py-0.5 bg-gray-100 text-gray-600 rounded-full text-xs">
                {t.name} ({t.count})
              </span>
            ))}
          </div>
        </div>
      )}

      {digest.top_channels && digest.top_channels.length > 0 && (
        <div className="bg-white rounded-xl shadow-sm p-5">
          <h2 className="font-semibold text-gray-800 mb-3">상위 채널</h2>
          <ul className="space-y-1 text-sm text-gray-700">
            {digest.top_channels.map((c) => <li key={c.name}>{c.name} ({c.count})</li>)}
          </ul>
        </div>
      )}

      {digest.sentiment_breakdown && Object.keys(digest.sentiment_breakdown).length > 0 && (
        <div className="bg-white rounded-xl shadow-sm p-5">
          <h2 className="font-semibold text-gray-800 mb-3">감성 분포</h2>
          <div className="flex flex-wrap gap-3 text-sm text-gray-700">
            {Object.entries(digest.sentiment_breakdown).map(([k, v]) => (
              <span key={k}>{k}: {v}</span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
```
- [ ] **Step 2:** `cd frontend && npx tsc --noEmit` → 에러 없음.
- [ ] **Step 3:** Commit
```bash
git add frontend/src/pages/DigestDetail.tsx
git commit -m "feat: DigestDetail 페이지(요약 마크다운·상위 태그/채널·감성)"
```

---

## Task 7: VideoDetail에 텔레그램 발송/미리보기 + 커스텀 프롬프트 재추가

Plan 2에서 제거했던 두 기능을 ytdb api로 재추가한다. my-assistant `VideoDetail.tsx`의 해당 블록을 참고하되, api는 `videoApi(activeSlug)`로 바인딩.

**Files:** Modify `frontend/src/pages/VideoDetail.tsx`

- [ ] **Step 1:** 현재 `frontend/src/pages/VideoDetail.tsx`를 읽는다. 다음을 추가한다(기존 라이브 폴링/마크다운/삭제 로직은 유지):
  - imports에 추가: `import { promptApi } from '../api/prompts'`.
  - 상태 추가:
    ```ts
    const [notifying, setNotifying] = useState(false)
    const [promptOpen, setPromptOpen] = useState(false)
    const [customPrompt, setCustomPrompt] = useState('')
    const [promptLoaded, setPromptLoaded] = useState(false)
    ```
  - 핸들러 추가(컴포넌트 내부):
    ```ts
    const handleOpenPrompt = async () => {
      if (!promptLoaded) {
        try { setCustomPrompt(await promptApi(activeSlug).getAnalysisPrompt()) } catch { /* 무시 */ }
        setPromptLoaded(true)
      }
      setPromptOpen((v) => !v)
    }

    const handleNotify = async (force = false) => {
      if (!video) return
      if (video.analysis_status !== 'done') { alert('분석 완료 후 발송할 수 있습니다.'); return }
      if (video.notified_at && !force && !window.confirm('이미 발송된 영상입니다. 다시 발송할까요?')) return
      setNotifying(true)
      try {
        const res = await videoApi(activeSlug).notify(Number(videoPk), force || Boolean(video.notified_at))
        await silentRefresh()
        alert(res.message)
      } catch (e) { alert((e as Error).message) }
      finally { setNotifying(false) }
    }
    ```
  - `handleReanalyze`에서 커스텀 프롬프트를 사용하도록: `await videoApi(activeSlug).analyzeNow(Number(videoPk))` 를 `await videoApi(activeSlug).analyzeNow(Number(videoPk), promptOpen && customPrompt.trim() ? customPrompt.trim() : undefined)` 로 교체. 폴링 로직 유지.
  - 헤더 액션 버튼 영역에 두 버튼 추가(YouTube/재분석/삭제 버튼 옆):
    ```tsx
    <button onClick={handleOpenPrompt} disabled={reanalyzing || notifying}
      className={`px-3 py-1.5 text-xs rounded-lg font-medium disabled:opacity-60 ${promptOpen ? 'bg-amber-100 text-amber-700' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}>
      {promptOpen ? '프롬프트 닫기' : '프롬프트 수정'}
    </button>
    <button onClick={() => handleNotify(false)} disabled={notifying || reanalyzing || video.analysis_status !== 'done'}
      className="px-3 py-1.5 bg-sky-50 text-sky-700 text-xs rounded-lg hover:bg-sky-100 disabled:opacity-60 font-medium">
      {notifying ? '발송 중...' : video.notified_at ? 'Telegram 재발송' : 'Telegram 발송'}
    </button>
    ```
    그리고 재분석 버튼 라벨을 `{reanalyzing ? '분석 중...' : promptOpen ? '이 프롬프트로 재분석' : '재분석'}`로.
  - 커스텀 프롬프트 패널(헤더 아래) 추가:
    ```tsx
    {promptOpen && (
      <div className="mt-3 border border-amber-200 rounded-lg bg-amber-50 p-3 space-y-2">
        <p className="text-xs font-semibold text-amber-700">이 영상 전용 분석 프롬프트 (기본 프롬프트 기반)</p>
        <textarea value={customPrompt} onChange={(e) => setCustomPrompt(e.target.value)} rows={10} spellCheck={false}
          className="w-full border border-amber-300 rounded-lg px-3 py-2 text-xs font-mono bg-white resize-y" />
      </div>
    )}
    ```
  - 분석 결과 아래(또는 적절한 위치)에 Telegram 미리보기 블록 추가(`video.full_analysis_md` 또는 `video.headline`이 있을 때):
    ```tsx
    {(video.headline || video.full_analysis_md || (video.bullet_points && video.bullet_points.length > 0)) && (
      <div className="bg-gray-800 rounded-xl p-4 text-gray-100 text-xs space-y-2 break-words">
        <p className="text-gray-400 uppercase tracking-wide">Telegram 알림 미리보기</p>
        <p className="font-bold">🎬 [{video.source_channel_name || '모니터 채널'}] 신규 영상</p>
        {video.headline && <p className="font-semibold">{video.headline}</p>}
        {video.full_analysis_md && (
          <div className="text-gray-200 whitespace-pre-wrap font-sans max-h-48 overflow-y-auto border border-gray-600 rounded-lg p-2">
            {video.full_analysis_md.length > 1200 ? `${video.full_analysis_md.slice(0, 1200)}…` : video.full_analysis_md}
          </div>
        )}
        {video.tags.length > 0 && <p className="text-blue-300">🏷 {video.tags.slice(0, 8).join(', ')}</p>}
        {video.notified_at && <p className="text-green-400">✅ 발송됨: {dayjs(video.notified_at).format('MM/DD HH:mm')}</p>}
      </div>
    )}
    ```
- [ ] **Step 2:** `cd frontend && npx tsc --noEmit` → 에러 없음(미사용 변수 없도록 확인).
- [ ] **Step 3:** Commit
```bash
git add frontend/src/pages/VideoDetail.tsx
git commit -m "feat: VideoDetail 텔레그램 발송/미리보기 + 커스텀 프롬프트 재분석"
```

---

## Task 8: InstantAnalyze 커스텀 프롬프트(선택)

**Files:** Modify `frontend/src/pages/InstantAnalyze.tsx`

- [ ] **Step 1:** 현재 파일을 읽고 커스텀 프롬프트 토글을 재추가. ytdb instant 엔드포인트는 custom_prompt를 받지 않으므로(영상 등록 후 분석은 analyze-now 경로가 아님), **이 화면의 커스텀 프롬프트는 "신규 영상 등록 후 자동 분석"에는 적용되지 않는다.** 따라서 사용자 혼선을 막기 위해 InstantAnalyze에는 커스텀 프롬프트를 **추가하지 않고**, 안내 문구만 유지한다. → 이 Task는 **스킵**(작업 없음). (커스텀 프롬프트는 VideoDetail의 재분석에서만 제공.)
- [ ] **Step 2:** 변경 없음 — 커밋 없음.

---

## Task 9: Layout 네비 + App 라우트

**Files:** Modify `frontend/src/components/Layout.tsx`, `frontend/src/App.tsx`

- [ ] **Step 1:** `frontend/src/components/Layout.tsx`의 운영 메뉴 `NAV` 배열에 주간 리뷰 항목을 추가(logs 앞 또는 뒤):
```ts
  { sub: 'digests', label: '주간 리뷰', icon: '📊' },
```
- [ ] **Step 2:** `frontend/src/App.tsx`에 import + 라우트 추가:
```ts
import Digests from './pages/Digests'
import DigestDetail from './pages/DigestDetail'
```
`<Route element={<Layout />}>` 안에(logs 다음):
```tsx
<Route path="digests" element={<Digests />} />
<Route path="digests/:digestPk" element={<DigestDetail />} />
```
- [ ] **Step 3:** 검증 — `cd frontend && npx tsc --noEmit` → 에러 없음. `npm run build` → 성공. `app/static/ui`가 git stage 후보가 아님을 확인.
- [ ] **Step 4:** Commit
```bash
git add frontend/src/components/Layout.tsx frontend/src/App.tsx
git commit -m "feat: 주간 리뷰 네비 + digests 라우트"
```

---

## Task 10: 전체 검증 게이트

- [ ] **Step 1:** `cd frontend && npx tsc --noEmit && npx vitest run && npm run build` → tsc clean, vitest 전부 통과, 빌드 성공.
- [ ] **Step 2:** `cd .. && pytest -q` → 통과(기존 + Plan4 스모크), `python -c "from app.main import app; print('ok')"` → ok.
- [ ] **Step 3:** `git status --short` → 클린(모드 아티팩트만 있으면 `git checkout --`로 정리).

---

## Task 11: 수동 통합 검증 (DB 필요)

- [ ] **Step 1:** 백엔드 재시작(코드 변경 반영) + `npm run dev` → `http://localhost:5173/app/`.
- [ ] **Step 2:** 주간 리뷰: 네비 → "지금 생성" → 목록에 추가 → 상세(요약 마크다운·상위 태그/채널·감성) → 삭제.
- [ ] **Step 3:** 영상 상세: 분석 완료 영상에서 "Telegram 발송" → 텔레그램 수신 확인, 미리보기 블록에 "발송됨" 표시. "재발송" 동작.
- [ ] **Step 4:** 영상 상세: "프롬프트 수정" → 기본 프롬프트 로드 → 수정 후 "이 프롬프트로 재분석" → 라이브 폴링으로 결과 갱신(커스텀 프롬프트 반영 확인).
- [ ] **Step 5:** 그룹 격리: 다른 그룹에서 다이제스트/알림 설정이 독립적인지.

---

## Self-Review 결과 (작성자 기록)

- **스펙 커버리지**: 주간 리뷰(Task5/6/9), 텔레그램 수동발송(Task2 백엔드 + Task7 UI), 커스텀 프롬프트(Task1 백엔드 + Task7 UI). = §"v2로 남김" 3종 전부.
- **백엔드 최소·비파괴**: analyzer는 인자 추가만(기존 호출 무영향, override 기본 None), analyze_specific_video는 선택 인자 추가, analyze-now는 선택 본문(기존 무본문 호출도 동작), notify는 신규 엔드포인트. 기존 스케줄러/파이프라인 로직 무변경.
- **타입/시그니처 일관성**: `digestApi(slug).{list,get,remove,generate}`, `videoApi(slug).{notify,analyzeNow(customPrompt?)}`, `promptApi(slug).getAnalysisPrompt`, 타입 `Digest`/`VideoNotifyResponse`/`TagCount` — Task4~9 동일 사용. 백엔드 `AnalyzeNowRequest`/`NotifyRequest`/`VideoNotifyResponse`는 Task1/2 정의, Task3 테스트.
- **결정**: InstantAnalyze 커스텀 프롬프트는 ytdb instant 경로가 prompt를 받지 않아 혼선 방지 위해 제외(Task8 스킵). 커스텀 프롬프트는 VideoDetail 재분석에 한정.
- **위험**: ① VideoDetail 재추가는 기존 파일 수정이라 미사용 변수/누락 위험 → tsc로 검출, 정확한 추가 지점 명시. ② notify_video가 `notif.is_sendable`이 아니면 0 반환 → 엔드포인트가 400으로 안내. ③ analyze-now 본문 옵셔널 파싱(`AnalyzeNowRequest | None = None`)은 FastAPI에서 본문 없는 요청도 허용.
- **이로써 Plan 1~4로 v1a + 설정 + v2 전 기능 이식 완료. 이후는 vanilla 제거 및 `/` 컷오버(별도 마무리 단계).**
