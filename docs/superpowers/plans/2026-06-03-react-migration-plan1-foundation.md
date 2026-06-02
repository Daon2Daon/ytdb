# React 마이그레이션 Plan 1 — 기반(Foundation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ytdb에 React 프론트엔드 기반을 구축하고 `/app`에서 그룹 스코프 대시보드가 실데이터로 동작하게 만든다. 기존 vanilla 앱(`/`)과 백엔드 로직은 무손상.

**Architecture:** 백엔드에 비파괴 엔드포인트(stats, db/gateway health, 옵트인 페이지네이션, SPA 서빙)만 추가한다. `frontend/`에 Vite+React+TS 앱을 새로 만들어 `app/static/ui/`로 빌드한다. 그룹 컨텍스트는 URL 경로의 `:slug`로 잡고, API 클라이언트가 ytdb 응답을 페이지가 기대하는 타입으로 정규화하는 어댑터 역할을 한다.

**Tech Stack:** FastAPI · SQLAlchemy(async) · pytest/pytest-asyncio · React 18 · TypeScript · Vite · React Router v6 · Tailwind · Vitest

**관련 스펙:** `docs/superpowers/specs/2026-06-03-react-migration-design.md`

---

## File Structure

### 백엔드 (추가/수정)
- Create `app/schemas/stats.py` — `StatsOut`, `DBHealthOut`, `GatewayHealthOut`, `PaginatedVideos`
- Create `app/routers/stats.py` — `GET /api/groups/{slug}/stats`
- Create `app/routers/health.py` — `GET /api/groups/{slug}/health/db`, `POST /api/groups/{slug}/health/gateway`
- Modify `app/routers/videos.py` — 옵트인 `paged` 페이지네이션(`{items,total,page,page_size}`)
- Modify `app/main.py` — stats/health 라우터 등록 + `/app` SPA 서빙 + catch-all
- Modify `Dockerfile` — Node 빌드 멀티스테이지 추가
- Modify `requirements.txt` — pytest, pytest-asyncio
- Create `tests/conftest.py`, `tests/test_pagination.py`, `tests/test_spa_serving.py`

### 프론트엔드 (신규 `frontend/`)
- Config: `package.json`, `vite.config.ts`, `tsconfig.json`, `tsconfig.node.json`, `tailwind.config.js`, `postcss.config.js`, `index.html`, `vitest.config.ts`
- `src/main.tsx`, `src/App.tsx`, `src/index.css`
- `src/api/http.ts` — `groupClient(slug)` 팩토리 + `request`
- `src/api/types.ts` — 페이지가 소비하는 TS 타입(my-assistant 계약)
- `src/api/adapters.ts` — ytdb 응답 → `types.ts` 정규화 (TDD 핵심)
- `src/api/groups.ts`, `src/api/videos.ts`, `src/api/stats.ts`, `src/api/health.ts`
- `src/group/GroupProvider.tsx`, `src/group/useGroup.ts`
- `src/components/Spinner.tsx`, `ErrorBanner.tsx`, `StatusBadge.tsx`, `Layout.tsx`
- `src/pages/Dashboard.tsx`
- Tests: `src/api/adapters.test.ts`, `src/api/http.test.ts`

**복사 원본 (my-assistant, 읽기 전용 참조):** `/Users/mukymook/cursor-workspace/my-assistant/frontend/youtube/src/`

---

## Task 1: 백엔드 테스트 인프라

**Files:**
- Modify: `requirements.txt`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: requirements에 테스트 의존성 추가**

`requirements.txt` 끝에 추가:

```
# 테스트
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

- [ ] **Step 2: 설치**

Run: `pip install -r requirements.txt`
Expected: pytest, pytest-asyncio 설치 성공.

- [ ] **Step 3: 테스트 패키지 + conftest 생성**

Create `tests/__init__.py` (빈 파일).

Create `tests/conftest.py`:

```python
"""pytest 공용 설정.

- asyncio 모드 auto: async 테스트에 데코레이터 불필요.
- DB가 필요한 통합 테스트는 control DB(DATABASE_URL) 가용 시에만 의미가 있으므로
  개별 테스트에서 별도 fixture/skip을 사용한다. 본 Plan 1의 테스트는 DB 불필요.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
```

Create `pytest.ini` (프로젝트 루트):

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

- [ ] **Step 4: 빈 수집 확인**

Run: `pytest -q`
Expected: "no tests ran" (수집 에러 없음).

- [ ] **Step 5: Commit**

```bash
git add requirements.txt tests/__init__.py tests/conftest.py pytest.ini
git commit -m "test: pytest 인프라 추가 (asyncio auto 모드)"
```

---

## Task 2: stats/health/pagination 스키마

**Files:**
- Create: `app/schemas/stats.py`

- [ ] **Step 1: 스키마 작성**

Create `app/schemas/stats.py`:

```python
"""대시보드용 통계/헬스/페이지네이션 스키마."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from app.schemas.video import VideoListItem


class StatsOut(BaseModel):
    total_channels: int
    active_channels: int
    total_videos: int
    analyzed_videos: int
    pending_videos: int
    failed_videos: int
    notified_videos: int
    total_tags: int


class DBHealthOut(BaseModel):
    healthy: bool
    message: str
    latency_ms: Optional[int] = None


class GatewayHealthOut(BaseModel):
    success: bool
    message: str
    latency_ms: Optional[int] = None


class PaginatedVideos(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[VideoListItem]
```

- [ ] **Step 2: import 확인**

Run: `python -c "from app.schemas.stats import StatsOut, DBHealthOut, GatewayHealthOut, PaginatedVideos; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add app/schemas/stats.py
git commit -m "feat: 대시보드 stats/health/pagination 스키마 추가"
```

---

## Task 3: 영상 목록 옵트인 페이지네이션

기존 vanilla(`/`)는 `GET .../videos`를 평면 리스트로 소비한다. 깨뜨리지 않기 위해 `?paged=1`일 때만 `PaginatedVideos`를 반환한다. 페이지 번호 계산은 순수 함수로 분리해 DB 없이 테스트한다.

**Files:**
- Modify: `app/routers/videos.py:104-136`
- Create: `tests/test_pagination.py`

- [ ] **Step 1: 페이지 계산 순수 함수 테스트 작성**

Create `tests/test_pagination.py`:

```python
from app.routers.videos import _page_number


def test_page_number_first_page():
    assert _page_number(limit=20, offset=0) == 1


def test_page_number_third_page():
    assert _page_number(limit=20, offset=40) == 3


def test_page_number_zero_limit_defaults_to_one():
    assert _page_number(limit=0, offset=0) == 1
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_pagination.py -q`
Expected: FAIL — `ImportError: cannot import name '_page_number'`

- [ ] **Step 3: videos.py 수정**

`app/routers/videos.py` 상단 import에 `from app.schemas.stats import PaginatedVideos` 추가.

`list_videos` 함수 바로 위에 순수 헬퍼 추가:

```python
def _page_number(limit: int, offset: int) -> int:
    if limit <= 0:
        return 1
    return offset // limit + 1
```

`list_videos`의 시그니처에 `paged` 파라미터를 추가하고 `response_model`을 제거(혼합 반환). 데코레이터를 다음으로 교체:

```python
@router.get("")
async def list_videos(
    group: Group = Depends(get_group_or_404),
    status: str | None = Query(None, description="analysis_status 필터"),
    tag: str | None = Query(None, description="태그명 필터"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    paged: bool = Query(False, description="true면 {items,total,page,page_size} 반환"),
):
```

함수 본문 끝(`return items` 직전)에서, `paged`이면 total을 세어 `PaginatedVideos`로 감싸 반환한다. 기존 `rows` 조회 블록 다음에 다음 로직으로 교체:

```python
    items: list[VideoListItem] = []
    for video, headline, one_line in rows:
        item = VideoListItem.model_validate(video)
        item.headline = headline
        item.one_line = one_line
        items.append(item)

    if not paged:
        return items

    async with dpm.group_session(group) as session:
        count_stmt = select(func.count()).select_from(Video)
        if status:
            count_stmt = count_stmt.where(Video.analysis_status == status)
        if tag:
            count_stmt = (
                count_stmt.join(VideoTag, VideoTag.video_pk == Video.video_pk)
                .join(Tag, Tag.tag_pk == VideoTag.tag_pk)
                .where(Tag.name == tag)
            )
        total = (await session.execute(count_stmt)).scalar_one()

    return PaginatedVideos(
        total=int(total),
        page=_page_number(limit, offset),
        page_size=limit,
        items=items,
    )
```

`app/routers/videos.py` 상단 import에 `func`가 없으면 추가: `from sqlalchemy import func` (기존 `from sqlalchemy import ...` 줄에 병합).

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_pagination.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: 앱 import 무결성 확인**

Run: `python -c "from app.main import app; print('ok')"`
Expected: `ok` (라우터 로딩 에러 없음)

- [ ] **Step 6: Commit**

```bash
git add app/routers/videos.py tests/test_pagination.py
git commit -m "feat: 영상 목록 옵트인 페이지네이션(?paged=1), vanilla 호환 유지"
```

---

## Task 4: stats 엔드포인트

**Files:**
- Create: `app/routers/stats.py`
- Modify: `app/main.py` (라우터 등록)

- [ ] **Step 1: 라우터 작성**

Create `app/routers/stats.py`:

```python
"""그룹 스코프 대시보드 통계."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.models.control.group import Group
from app.models.pg.channel import Channel
from app.models.pg.tag import Tag
from app.models.pg.video import Video
from app.routers.deps import get_group_or_404
from app.schemas.stats import StatsOut
from app.services.db_engine import data_plane_engine_manager as dpm

router = APIRouter(prefix="/api/groups/{slug}/stats", tags=["stats"])


@router.get("", response_model=StatsOut)
async def get_stats(group: Group = Depends(get_group_or_404)) -> StatsOut:
    async with dpm.group_session(group) as session:
        total_channels = (
            await session.execute(select(func.count()).select_from(Channel))
        ).scalar_one()
        active_channels = (
            await session.execute(
                select(func.count()).select_from(Channel).where(Channel.is_active.is_(True))
            )
        ).scalar_one()
        total_videos = (
            await session.execute(select(func.count()).select_from(Video))
        ).scalar_one()
        analyzed_videos = (
            await session.execute(
                select(func.count()).select_from(Video).where(Video.analysis_status == "done")
            )
        ).scalar_one()
        pending_videos = (
            await session.execute(
                select(func.count()).select_from(Video).where(Video.analysis_status == "pending")
            )
        ).scalar_one()
        failed_videos = (
            await session.execute(
                select(func.count()).select_from(Video).where(Video.analysis_status == "failed")
            )
        ).scalar_one()
        notified_videos = (
            await session.execute(
                select(func.count()).select_from(Video).where(Video.notified_at.is_not(None))
            )
        ).scalar_one()
        total_tags = (
            await session.execute(select(func.count()).select_from(Tag))
        ).scalar_one()

    return StatsOut(
        total_channels=int(total_channels),
        active_channels=int(active_channels),
        total_videos=int(total_videos),
        analyzed_videos=int(analyzed_videos),
        pending_videos=int(pending_videos),
        failed_videos=int(failed_videos),
        notified_videos=int(notified_videos),
        total_tags=int(total_tags),
    )
```

- [ ] **Step 2: main.py에 등록**

`app/main.py`의 라우터 import 줄을 다음으로 교체:

```python
from app.routers import actions, channels, digests, groups, health, logs, settings, stats, tags, videos
```

`app.include_router(logs.router)` 다음 줄에 추가:

```python
app.include_router(stats.router)
app.include_router(health.router)
```

(health 라우터는 Task 5에서 생성하므로, Task 5 완료 전까지 import 에러가 난다. Task 5와 함께 검증한다. 먼저 진행하려면 이 단계에서 `health`/`health.router` 두 곳을 잠시 빼고 Task 5에서 추가해도 된다.)

- [ ] **Step 3: 라우터 단독 import 확인**

Run: `python -c "import app.routers.stats; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add app/routers/stats.py app/main.py
git commit -m "feat: 그룹 스코프 stats 엔드포인트"
```

---

## Task 5: db/gateway health 엔드포인트

**Files:**
- Create: `app/routers/health.py`

- [ ] **Step 1: 라우터 작성**

Create `app/routers/health.py`:

```python
"""그룹 스코프 헬스 체크 (데이터 평면 DB, AI 게이트웨이)."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.models.control.group import Group
from app.routers.deps import get_group_or_404
from app.schemas.stats import DBHealthOut, GatewayHealthOut
from app.services.db_engine import DBNotConfiguredError, data_plane_engine_manager as dpm
from app.services.llm_client import LiteLLMClient, LiteLLMError
from app.services.settings_manager import get_settings_manager

router = APIRouter(prefix="/api/groups/{slug}/health", tags=["health"])


@router.get("/db", response_model=DBHealthOut)
async def db_health(group: Group = Depends(get_group_or_404)) -> DBHealthOut:
    started = time.monotonic()
    try:
        async with dpm.group_session(group) as session:
            await session.execute(text("SELECT 1"))
    except DBNotConfiguredError as e:
        return DBHealthOut(healthy=False, message=str(e))
    except Exception as e:  # noqa: BLE001
        return DBHealthOut(healthy=False, message=f"DB 연결 실패: {e}")
    latency_ms = int((time.monotonic() - started) * 1000)
    return DBHealthOut(healthy=True, message="정상", latency_ms=latency_ms)


@router.post("/gateway", response_model=GatewayHealthOut)
async def gateway_health(group: Group = Depends(get_group_or_404)) -> GatewayHealthOut:
    cfg = await get_settings_manager().get_ai_gateway(group.group_id)
    if not cfg.base_url or not cfg.api_key:
        return GatewayHealthOut(success=False, message="base_url/api_key 미설정")
    started = time.monotonic()
    client = LiteLLMClient(cfg)
    try:
        models = await client.get_models(force_refresh=True)
    except LiteLLMError as e:
        return GatewayHealthOut(success=False, message=str(e))
    except Exception as e:  # noqa: BLE001
        return GatewayHealthOut(success=False, message=f"게이트웨이 연결 실패: {e}")
    finally:
        await client.aclose()
    latency_ms = int((time.monotonic() - started) * 1000)
    return GatewayHealthOut(success=True, message=f"모델 {len(models)}개 확인", latency_ms=latency_ms)
```

- [ ] **Step 2: import 확인 + 앱 무결성**

Run: `python -c "import app.routers.health; from app.main import app; print('ok')"`
Expected: `ok` (Task 4의 main.py 등록과 합쳐져 health 라우터까지 로딩)

- [ ] **Step 3: Commit**

```bash
git add app/routers/health.py
git commit -m "feat: 그룹 스코프 db/gateway 헬스 엔드포인트"
```

---

## Task 6: SPA 서빙 + catch-all 라우트

React 빌드물(`app/static/ui/`)을 `/app/*`에서 서빙한다. 빌드 전이라도 라우트 등록 자체와 폴백 동작은 TestClient로 검증한다(파일 부재 시 404가 아니라 의도된 응답).

**Files:**
- Modify: `app/main.py`
- Create: `tests/test_spa_serving.py`

- [ ] **Step 1: 서빙 테스트 작성**

Create `tests/test_spa_serving.py`:

```python
from fastapi.testclient import TestClient

from app.main import app


def test_api_route_not_captured_by_spa():
    """존재하지 않는 /api 경로는 SPA로 흡수되지 않고 404여야 한다."""
    client = TestClient(app)
    resp = client.get("/api/groups/__nope__/does-not-exist")
    assert resp.status_code == 404


def test_legacy_root_still_served():
    """기존 vanilla 진입점(/)이 살아있어야 한다."""
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "<html" in resp.text.lower()
```

- [ ] **Step 2: 실패/통과 기준 확인**

Run: `pytest tests/test_spa_serving.py -q`
Expected: `test_legacy_root_still_served` PASS, `test_api_route_not_captured_by_spa` PASS (현재도 매칭 안 되면 404). 둘 다 통과하지 않으면 Step 3 후 재확인.

- [ ] **Step 3: main.py에 SPA 서빙 추가**

`app/main.py`의 기존 `app.mount("/static", ...)`와 index 라우트는 유지한다. 그 아래(파일 맨 끝)에 추가:

```python
UI_DIR = STATIC_DIR / "ui"


@app.get("/app", include_in_schema=False)
@app.get("/app/{spa_path:path}", include_in_schema=False)
async def spa(spa_path: str = "") -> FileResponse:
    """React SPA 진입점. 정적 자산은 /static/ui/ 로 로드되고,
    클라이언트 라우팅 경로(/app/...)는 모두 index.html로 폴백한다."""
    index_file = UI_DIR / "index.html"
    if not index_file.exists():
        return JSONResponse(  # type: ignore[return-value]
            status_code=503,
            content={"detail": "UI가 아직 빌드되지 않았습니다. frontend에서 npm run build 후 사용하세요."},
        )
    return FileResponse(str(index_file))
```

- [ ] **Step 4: 테스트 + 앱 무결성**

Run: `pytest tests/test_spa_serving.py -q && python -c "from app.main import app; print('ok')"`
Expected: 2 passed, `ok`

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_spa_serving.py
git commit -m "feat: /app SPA 서빙 + catch-all 폴백 (vanilla / 유지)"
```

---

## Task 7: 프론트엔드 스캐폴딩

my-assistant의 설정을 복사하되 ytdb에 맞게 경로/이름만 바꾼다.

**Files (Create):** `frontend/package.json`, `frontend/vite.config.ts`, `frontend/vitest.config.ts`, `frontend/tsconfig.json`, `frontend/tsconfig.node.json`, `frontend/tailwind.config.js`, `frontend/postcss.config.js`, `frontend/index.html`, `frontend/src/main.tsx`, `frontend/src/index.css`, `frontend/.gitignore`

- [ ] **Step 1: package.json**

Create `frontend/package.json`:

```json
{
  "name": "ytdb-ui",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview",
    "test": "vitest run"
  },
  "dependencies": {
    "dayjs": "^1.11.13",
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-markdown": "^9.0.1",
    "react-router-dom": "^6.26.2",
    "remark-gfm": "^4.0.0"
  },
  "devDependencies": {
    "@types/react": "^18.3.5",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "autoprefixer": "^10.4.20",
    "jsdom": "^25.0.0",
    "postcss": "^8.4.47",
    "tailwindcss": "^3.4.14",
    "typescript": "^5.9.3",
    "vite": "^5.4.8",
    "vitest": "^2.1.0"
  }
}
```

- [ ] **Step 2: vite.config.ts**

Create `frontend/vite.config.ts`:

```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  base: '/static/ui/',
  build: {
    outDir: path.resolve(__dirname, '../app/static/ui'),
    emptyOutDir: true,
  },
  server: {
    proxy: { '/api': 'http://localhost:8000' },
  },
})
```

- [ ] **Step 3: vitest.config.ts**

Create `frontend/vitest.config.ts`:

```ts
import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'jsdom',
    globals: true,
  },
})
```

- [ ] **Step 4: tsconfig.json**

Create `frontend/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "types": ["vitest/globals"]
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

Create `frontend/tsconfig.node.json`:

```json
{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "allowSyntheticDefaultImports": true
  },
  "include": ["vite.config.ts", "vitest.config.ts"]
}
```

- [ ] **Step 5: Tailwind/PostCSS**

Create `frontend/tailwind.config.js`:

```js
/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: { extend: {} },
  plugins: [],
}
```

Create `frontend/postcss.config.js`:

```js
export default {
  plugins: { tailwindcss: {}, autoprefixer: {} },
}
```

- [ ] **Step 6: index.html + 엔트리**

Create `frontend/index.html`:

```html
<!DOCTYPE html>
<html lang="ko">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>ytdb · YouTube 모니터</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

Create `frontend/src/index.css`:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

Create `frontend/src/main.tsx`:

```tsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter basename="/app">
      <App />
    </BrowserRouter>
  </React.StrictMode>,
)
```

Create `frontend/.gitignore`:

```
node_modules
dist
```

- [ ] **Step 7: 의존성 설치**

Run: `cd frontend && npm install`
Expected: 설치 성공, `node_modules` 생성.

- [ ] **Step 8: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vite.config.ts frontend/vitest.config.ts frontend/tsconfig.json frontend/tsconfig.node.json frontend/tailwind.config.js frontend/postcss.config.js frontend/index.html frontend/src/main.tsx frontend/src/index.css frontend/.gitignore
git commit -m "feat: frontend 스캐폴딩 (Vite+React+TS, base /static/ui)"
```

---

## Task 8: API 타입 + groupClient (HTTP 코어)

**Files:**
- Create: `frontend/src/api/types.ts`
- Create: `frontend/src/api/http.ts`
- Create: `frontend/src/api/http.test.ts`

- [ ] **Step 1: 페이지가 소비하는 타입 정의**

Create `frontend/src/api/types.ts`:

```ts
export interface Group {
  group_id: number
  slug: string
  name: string
  schema_name: string
  is_active: boolean
  description: string | null
}

export interface VideoSummary {
  one_line: string
  headline: string | null
}

export interface Video {
  video_pk: number
  video_id: string
  video_url: string
  title: string
  thumbnail_url: string | null
  published_at: string
  duration_seconds: number | null
  view_count: number | null
  analysis_status: 'pending' | 'processing' | 'done' | 'failed'
  notified_at: string | null
  summary: VideoSummary | null
  source_channel_name: string | null
}

export interface PaginatedVideos {
  total: number
  page: number
  page_size: number
  items: Video[]
}

export interface Stats {
  total_channels: number
  active_channels: number
  total_videos: number
  analyzed_videos: number
  pending_videos: number
  failed_videos: number
  notified_videos: number
  total_tags: number
}

export interface DBHealthResponse {
  healthy: boolean
  message: string
  latency_ms: number | null
}

export interface GatewayHealthResponse {
  success: boolean
  message: string
  latency_ms?: number
}
```

- [ ] **Step 2: groupClient 테스트 작성**

Create `frontend/src/api/http.test.ts`:

```ts
import { describe, it, expect, vi, afterEach } from 'vitest'
import { groupClient } from './http'

afterEach(() => vi.restoreAllMocks())

function mockFetch(status: number, body: unknown) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
}

describe('groupClient', () => {
  it('그룹 slug를 base 경로에 주입한다', async () => {
    const f = mockFetch(200, { ok: true })
    const client = groupClient('invest')
    await client.get('/videos?paged=1')
    expect(f).toHaveBeenCalledWith(
      '/api/groups/invest/videos?paged=1',
      expect.objectContaining({ method: 'GET' }),
    )
  })

  it('비정상 응답이면 detail 메시지로 throw한다', async () => {
    mockFetch(400, { detail: 'DB 설정이 없습니다.' })
    const client = groupClient('invest')
    await expect(client.get('/stats')).rejects.toThrow('DB 설정이 없습니다.')
  })

  it('204는 undefined를 반환한다', async () => {
    mockFetch(204, {})
    const client = groupClient('invest')
    await expect(client.del('/videos/1')).resolves.toBeUndefined()
  })
})
```

- [ ] **Step 3: 실패 확인**

Run: `cd frontend && npx vitest run src/api/http.test.ts`
Expected: FAIL — `Cannot find module './http'`

- [ ] **Step 4: http.ts 구현**

Create `frontend/src/api/http.ts`:

```ts
async function request<T>(url: string, init: RequestInit): Promise<T> {
  const resp = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...init.headers },
    ...init,
  })
  if (!resp.ok) {
    const data = await resp.json().catch(() => null)
    const detail = data && typeof data.detail === 'string' ? data.detail : resp.statusText
    throw new Error(detail)
  }
  if (resp.status === 204) return undefined as T
  return resp.json()
}

export function groupClient(slug: string) {
  const base = `/api/groups/${slug}`
  return {
    get: <T>(path: string) => request<T>(`${base}${path}`, { method: 'GET' }),
    post: <T>(path: string, body?: unknown) =>
      request<T>(`${base}${path}`, {
        method: 'POST',
        body: body === undefined ? undefined : JSON.stringify(body),
      }),
    patch: <T>(path: string, body: unknown) =>
      request<T>(`${base}${path}`, { method: 'PATCH', body: JSON.stringify(body) }),
    del: <T>(path: string) => request<T>(`${base}${path}`, { method: 'DELETE' }),
  }
}

export type GroupClient = ReturnType<typeof groupClient>

// 전역(그룹 비종속) 호출용.
export const rootApi = {
  get: <T>(path: string) => request<T>(`/api${path}`, { method: 'GET' }),
  post: <T>(path: string, body?: unknown) =>
    request<T>(`/api${path}`, {
      method: 'POST',
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(`/api${path}`, { method: 'PATCH', body: JSON.stringify(body) }),
}
```

- [ ] **Step 5: 통과 확인**

Run: `cd frontend && npx vitest run src/api/http.test.ts`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/api/http.ts frontend/src/api/http.test.ts
git commit -m "feat: groupClient HTTP 코어 + API 타입"
```

---

## Task 9: 정규화 어댑터 (핵심)

ytdb의 영상 목록 항목은 `headline`/`one_line`이 평면이다. 페이지는 `summary.{one_line,headline}` 중첩을 기대한다. 어댑터가 이 변환을 담당한다.

**Files:**
- Create: `frontend/src/api/adapters.ts`
- Create: `frontend/src/api/adapters.test.ts`

- [ ] **Step 1: 어댑터 테스트 작성**

Create `frontend/src/api/adapters.test.ts`:

```ts
import { describe, it, expect } from 'vitest'
import { toVideo } from './adapters'

describe('toVideo', () => {
  it('평면 headline/one_line을 summary로 감싼다', () => {
    const raw = {
      video_pk: 1,
      video_id: 'abc',
      video_url: 'https://y/abc',
      title: 'T',
      thumbnail_url: null,
      published_at: '2026-06-01T00:00:00Z',
      duration_seconds: 120,
      analysis_status: 'done',
      notified_at: null,
      headline: '헤드라인',
      one_line: '한 줄',
    }
    const v = toVideo(raw)
    expect(v.summary).toEqual({ one_line: '한 줄', headline: '헤드라인' })
    expect(v.view_count).toBeNull()
    expect(v.source_channel_name).toBeNull()
  })

  it('one_line이 없으면 summary는 null', () => {
    const raw = {
      video_pk: 2,
      video_id: 'd',
      video_url: 'u',
      title: 'T2',
      thumbnail_url: null,
      published_at: '2026-06-01T00:00:00Z',
      duration_seconds: null,
      analysis_status: 'pending',
      notified_at: null,
      headline: null,
      one_line: null,
    }
    const v = toVideo(raw)
    expect(v.summary).toBeNull()
  })
})
```

- [ ] **Step 2: 실패 확인**

Run: `cd frontend && npx vitest run src/api/adapters.test.ts`
Expected: FAIL — `Cannot find module './adapters'`

- [ ] **Step 3: 어댑터 구현**

Create `frontend/src/api/adapters.ts`:

```ts
import type { Video } from './types'

/** ytdb VideoListItem(평면 headline/one_line) → 페이지용 Video(summary 중첩). */
export function toVideo(raw: Record<string, any>): Video {
  const oneLine: string | null = raw.one_line ?? null
  const headline: string | null = raw.headline ?? null
  const summary = oneLine ? { one_line: oneLine, headline } : null
  return {
    video_pk: raw.video_pk,
    video_id: raw.video_id,
    video_url: raw.video_url,
    title: raw.title,
    thumbnail_url: raw.thumbnail_url ?? null,
    published_at: raw.published_at,
    duration_seconds: raw.duration_seconds ?? null,
    view_count: raw.view_count ?? null,
    analysis_status: raw.analysis_status,
    notified_at: raw.notified_at ?? null,
    summary,
    source_channel_name: raw.source_channel_name ?? null,
  }
}
```

- [ ] **Step 4: 통과 확인**

Run: `cd frontend && npx vitest run src/api/adapters.test.ts`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/adapters.ts frontend/src/api/adapters.test.ts
git commit -m "feat: ytdb 영상 응답 정규화 어댑터(toVideo)"
```

---

## Task 10: 도메인 API 모듈 (groups/videos/stats/health)

**Files:**
- Create: `frontend/src/api/groups.ts`, `frontend/src/api/videos.ts`, `frontend/src/api/stats.ts`, `frontend/src/api/health.ts`

- [ ] **Step 1: groups.ts (전역)**

Create `frontend/src/api/groups.ts`:

```ts
import { rootApi } from './http'
import type { Group } from './types'

export const groupApi = {
  list: () => rootApi.get<Group[]>('/groups'),
  create: (body: { slug: string; name: string; schema_name?: string }) =>
    rootApi.post<Group>('/groups', body),
  rename: (slug: string, name: string) =>
    rootApi.patch<Group>(`/groups/${slug}`, { name }),
}
```

- [ ] **Step 2: videos.ts (그룹 스코프 + 어댑터)**

Create `frontend/src/api/videos.ts`:

```ts
import { groupClient } from './http'
import { toVideo } from './adapters'
import type { PaginatedVideos } from './types'

export function videoApi(slug: string) {
  const c = groupClient(slug)
  return {
    listPaged: async (params: {
      status?: string
      tag?: string
      limit?: number
      offset?: number
    }): Promise<PaginatedVideos> => {
      const q = new URLSearchParams({ paged: '1' })
      if (params.status) q.set('status', params.status)
      if (params.tag) q.set('tag', params.tag)
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
  }
}
```

- [ ] **Step 3: stats.ts**

Create `frontend/src/api/stats.ts`:

```ts
import { groupClient } from './http'
import type { Stats } from './types'

export function statsApi(slug: string) {
  return {
    get: () => groupClient(slug).get<Stats>('/stats'),
  }
}
```

- [ ] **Step 4: health.ts**

Create `frontend/src/api/health.ts`:

```ts
import { groupClient } from './http'
import type { DBHealthResponse, GatewayHealthResponse } from './types'

export function healthApi(slug: string) {
  const c = groupClient(slug)
  return {
    db: () => c.get<DBHealthResponse>('/health/db'),
    gateway: () => c.post<GatewayHealthResponse>('/health/gateway'),
  }
}
```

- [ ] **Step 5: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음 (사용처가 없어 unused 경고가 나면 무시 가능하나, noUnusedLocals는 export된 심볼엔 적용 안 됨).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/groups.ts frontend/src/api/videos.ts frontend/src/api/stats.ts frontend/src/api/health.ts
git commit -m "feat: 도메인 API 모듈(groups/videos/stats/health)"
```

---

## Task 11: GroupProvider + useGroup

**Files:**
- Create: `frontend/src/group/useGroup.ts`
- Create: `frontend/src/group/GroupProvider.tsx`

- [ ] **Step 1: context + 훅**

Create `frontend/src/group/useGroup.ts`:

```ts
import { createContext, useContext } from 'react'
import type { Group } from '../api/types'

export interface GroupContextValue {
  groups: Group[]
  activeSlug: string
  activeGroup: Group | undefined
  reloadGroups: () => Promise<void>
}

export const GroupContext = createContext<GroupContextValue | null>(null)

export function useGroup(): GroupContextValue {
  const ctx = useContext(GroupContext)
  if (!ctx) throw new Error('useGroup must be used within GroupProvider')
  return ctx
}
```

- [ ] **Step 2: Provider**

Create `frontend/src/group/GroupProvider.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react'
import { useParams, useNavigate, Outlet } from 'react-router-dom'
import { groupApi } from '../api/groups'
import type { Group } from '../api/types'
import { GroupContext } from './useGroup'
import Spinner from '../components/Spinner'

export default function GroupProvider() {
  const { slug } = useParams<{ slug: string }>()
  const navigate = useNavigate()
  const [groups, setGroups] = useState<Group[]>([])
  const [loading, setLoading] = useState(true)

  const reloadGroups = useCallback(async () => {
    const list = await groupApi.list()
    setGroups(list)
    return
  }, [])

  useEffect(() => {
    reloadGroups().finally(() => setLoading(false))
  }, [reloadGroups])

  // slug가 비었거나 목록에 없으면 첫 그룹으로 보정.
  useEffect(() => {
    if (loading) return
    if (groups.length === 0) return
    const found = slug && groups.some((g) => g.slug === slug)
    if (!found) navigate(`/g/${groups[0].slug}/`, { replace: true })
  }, [loading, groups, slug, navigate])

  if (loading) return <Spinner />
  if (groups.length === 0) {
    return (
      <div className="min-h-screen flex items-center justify-center text-gray-500">
        그룹이 없습니다. 그룹을 먼저 생성하세요. (v1b에서 생성 UI 제공)
      </div>
    )
  }

  const activeGroup = groups.find((g) => g.slug === slug)
  return (
    <GroupContext.Provider
      value={{ groups, activeSlug: slug ?? '', activeGroup, reloadGroups }}
    >
      <Outlet />
    </GroupContext.Provider>
  )
}
```

- [ ] **Step 3: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: `Spinner` 미존재 에러(다음 Task에서 생성). 그 외 그룹 로직 에러 없어야 함. (Task 12 후 재검증)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/group/useGroup.ts frontend/src/group/GroupProvider.tsx
git commit -m "feat: GroupProvider + useGroup (slug 경로 기반 그룹 컨텍스트)"
```

---

## Task 12: 공통 컴포넌트 (Spinner/ErrorBanner/StatusBadge)

my-assistant 원본을 복사한다(그룹 무관, 변경 없음).

**Files:**
- Create: `frontend/src/components/Spinner.tsx`, `ErrorBanner.tsx`, `StatusBadge.tsx`

- [ ] **Step 1: 원본 복사**

`/Users/mukymook/cursor-workspace/my-assistant/frontend/youtube/src/components/Spinner.tsx`, `ErrorBanner.tsx`, `StatusBadge.tsx` 세 파일을 읽어 동일 내용으로 `frontend/src/components/`에 생성한다(import 경로가 상대경로뿐이라 수정 불필요).

- [ ] **Step 2: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음 (Task 11의 Spinner 참조도 해소).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Spinner.tsx frontend/src/components/ErrorBanner.tsx frontend/src/components/StatusBadge.tsx
git commit -m "feat: 공통 컴포넌트 복사(Spinner/ErrorBanner/StatusBadge)"
```

---

## Task 13: Layout + 그룹 셀렉터

my-assistant `Layout.tsx`를 기반으로 하되, ① 사이드바 메뉴 경로를 `/g/:slug/...`로, ② 상단에 그룹 셀렉터를 추가한다. v1a 메뉴만 노출(설정/주간리뷰는 v1b/v2에서 추가).

**Files:**
- Create: `frontend/src/components/Layout.tsx`

- [ ] **Step 1: Layout 작성**

Create `frontend/src/components/Layout.tsx`:

```tsx
import { NavLink, Outlet, useNavigate, useLocation } from 'react-router-dom'
import { useGroup } from '../group/useGroup'

const NAV = [
  { sub: '', label: '대시보드', icon: '🏠', end: true },
  { sub: 'channels', label: '채널 관리', icon: '📺' },
  { sub: 'videos', label: '영상 목록', icon: '🎬' },
  { sub: 'instant-analyze', label: '영상 분석', icon: '🔍' },
  { sub: 'logs', label: 'Logs', icon: '📋' },
]

export default function Layout() {
  const { groups, activeSlug } = useGroup()
  const navigate = useNavigate()
  const location = useLocation()

  // 그룹 전환: 현재 페이지(첫 경로 세그먼트)를 유지하되, PK 종속 상세 경로면 대시보드로.
  const onSwitchGroup = (slug: string) => {
    const after = location.pathname.replace(/^\/g\/[^/]+/, '')
    const seg = after.split('/').filter(Boolean)[0] ?? ''
    const safe = ['channels', 'videos', 'instant-analyze', 'logs'].includes(seg) ? seg : ''
    navigate(`/g/${slug}/${safe}`)
  }

  const linkClass = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors whitespace-nowrap ${
      isActive ? 'bg-blue-600 text-white font-medium' : 'text-gray-700 hover:bg-gray-100'
    }`

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      <header className="bg-white border-b border-gray-200 px-4 py-3 flex items-center gap-3">
        <span className="font-bold text-gray-800">ytdb</span>
        <select
          value={activeSlug}
          onChange={(e) => onSwitchGroup(e.target.value)}
          className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm"
        >
          {groups.map((g) => (
            <option key={g.slug} value={g.slug}>{g.name} ({g.slug})</option>
          ))}
        </select>
      </header>

      <div className="flex flex-col lg:flex-row flex-1 max-w-7xl mx-auto w-full px-3 sm:px-4 py-4 gap-4 lg:gap-6">
        <aside className="w-full lg:w-52 shrink-0">
          <nav className="flex flex-row lg:flex-col gap-1 overflow-x-auto bg-white rounded-xl shadow-sm p-2 lg:p-3 lg:sticky lg:top-6">
            {NAV.map((item) => (
              <NavLink
                key={item.sub}
                to={`/g/${activeSlug}/${item.sub}`}
                end={item.end}
                className={linkClass}
              >
                <span>{item.icon}</span>
                <span>{item.label}</span>
              </NavLink>
            ))}
          </nav>
        </aside>
        <main className="flex-1 min-w-0 w-full">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Layout.tsx
git commit -m "feat: Layout + 그룹 셀렉터(현재 페이지 유지 전환)"
```

---

## Task 14: Dashboard 페이지

my-assistant `Dashboard.tsx`를 기반으로, ① `useGroup()`의 slug로 api 바인딩, ② 헬스 링크 경로를 `/g/:slug/...`로, ③ 최근 24h 대신 1페이지 최신 영상(`listPaged`)로 단순화.

**Files:**
- Create: `frontend/src/pages/Dashboard.tsx`

- [ ] **Step 1: Dashboard 작성**

Create `frontend/src/pages/Dashboard.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import dayjs from 'dayjs'
import { useGroup } from '../group/useGroup'
import { statsApi } from '../api/stats'
import { healthApi } from '../api/health'
import { videoApi } from '../api/videos'
import type { Stats, DBHealthResponse, GatewayHealthResponse, Video } from '../api/types'
import Spinner from '../components/Spinner'
import ErrorBanner from '../components/ErrorBanner'
import StatusBadge from '../components/StatusBadge'

function StatCard({ label, value, color }: { label: string; value: number | string; color?: string }) {
  return (
    <div className="bg-white rounded-xl shadow-sm p-5 flex flex-col gap-1">
      <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">{label}</span>
      <span className={`text-3xl font-bold ${color ?? 'text-gray-900'}`}>{value}</span>
    </div>
  )
}

export default function Dashboard() {
  const { activeSlug } = useGroup()
  const [stats, setStats] = useState<Stats | null>(null)
  const [db, setDb] = useState<DBHealthResponse | null>(null)
  const [gw, setGw] = useState<GatewayHealthResponse | null>(null)
  const [recent, setRecent] = useState<Video[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const [s, h, g, v] = await Promise.allSettled([
        statsApi(activeSlug).get(),
        healthApi(activeSlug).db(),
        healthApi(activeSlug).gateway(),
        videoApi(activeSlug).listPaged({ limit: 12, offset: 0 }),
      ])
      if (s.status === 'fulfilled') setStats(s.value)
      if (h.status === 'fulfilled') setDb(h.value)
      if (g.status === 'fulfilled') setGw(g.value)
      if (v.status === 'fulfilled') setRecent(v.value.items)
      // stats가 실패하면(예: DB 미설정) 에러 배너로 안내.
      if (s.status === 'rejected') setError((s.reason as Error).message)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [activeSlug])

  if (loading) return <Spinner />

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-900">대시보드</h1>

      {error && <ErrorBanner message={error} onRetry={load} />}

      <div className="space-y-2">
        {db && !db.healthy && (
          <div className="rounded-lg bg-red-50 border border-red-300 px-4 py-3 text-red-700 text-sm flex items-center gap-2">
            <span className="font-semibold">DB 오류</span>
            <span>{db.message}</span>
          </div>
        )}
        {db?.healthy && (
          <div className="rounded-lg bg-green-50 border border-green-200 px-4 py-3 text-green-700 text-sm">
            DB 정상 {db.latency_ms != null && `· 응답 ${db.latency_ms}ms`}
          </div>
        )}
        {gw && !gw.success && (
          <div className="rounded-lg bg-orange-50 border border-orange-300 px-4 py-3 text-orange-700 text-sm">
            <span className="font-semibold">AI Gateway 오류</span> {gw.message}
          </div>
        )}
        {gw?.success && (
          <div className="rounded-lg bg-green-50 border border-green-200 px-4 py-3 text-green-700 text-sm">
            AI Gateway 정상 · {gw.message}
          </div>
        )}
      </div>

      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard label="전체 채널" value={stats.total_channels} />
          <StatCard label="활성 채널" value={stats.active_channels} color="text-blue-600" />
          <StatCard label="전체 영상" value={stats.total_videos} />
          <StatCard label="분석 완료" value={stats.analyzed_videos} color="text-green-600" />
          <StatCard label="분석 대기" value={stats.pending_videos} color="text-yellow-600" />
          <StatCard label="분석 실패" value={stats.failed_videos} color="text-red-600" />
          <StatCard label="알림 발송" value={stats.notified_videos} />
          <StatCard label="전체 태그" value={stats.total_tags} />
        </div>
      )}

      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold text-gray-800">최신 영상</h2>
          <Link to={`/g/${activeSlug}/videos`} className="text-blue-600 text-sm hover:underline">전체 보기 →</Link>
        </div>
        {recent.length === 0 ? (
          <p className="text-gray-500 text-sm text-center py-8">표시할 영상이 없습니다.</p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {recent.map((v) => (
              <div key={v.video_pk} className="bg-white rounded-xl shadow-sm overflow-hidden">
                {v.thumbnail_url ? (
                  <img src={v.thumbnail_url} alt={v.title} className="w-full aspect-video object-cover" />
                ) : (
                  <div className="w-full aspect-video bg-gray-100 flex items-center justify-center text-gray-400 text-4xl">🎬</div>
                )}
                <div className="p-3 space-y-1.5">
                  <p className="text-sm font-medium text-gray-900 line-clamp-2">{v.title}</p>
                  {v.summary?.one_line && <p className="text-xs text-gray-500 line-clamp-1">{v.summary.one_line}</p>}
                  <div className="flex items-center justify-between gap-2">
                    <StatusBadge status={v.analysis_status} />
                    <span className="text-xs text-gray-400">{dayjs(v.published_at).format('MM/DD HH:mm')}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
```

- [ ] **Step 2: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/Dashboard.tsx
git commit -m "feat: 그룹 스코프 Dashboard(통계 카드·헬스 배너·최신 영상)"
```

---

## Task 15: App 라우터 조립 + 빌드

**Files:**
- Create: `frontend/src/App.tsx`

- [ ] **Step 1: App.tsx 작성**

Create `frontend/src/App.tsx`:

```tsx
import { useEffect } from 'react'
import { Routes, Route, Navigate, useNavigate } from 'react-router-dom'
import GroupProvider from './group/GroupProvider'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import { groupApi } from './api/groups'

// v1a 나머지 페이지는 Plan 2에서 라우트 추가.
function Placeholder({ name }: { name: string }) {
  return <div className="text-gray-500">{name} — Plan 2에서 구현 예정</div>
}

// 루트 진입: 첫 그룹으로 보정.
function RootRedirect() {
  const navigate = useNavigate()
  useEffect(() => {
    groupApi.list().then((groups) => {
      if (groups.length > 0) navigate(`/g/${groups[0].slug}/`, { replace: true })
    })
  }, [navigate])
  return <div className="min-h-screen flex items-center justify-center text-gray-400">로딩 중…</div>
}

export default function App() {
  return (
    <Routes>
      <Route path="/g/:slug" element={<GroupProvider />}>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="channels" element={<Placeholder name="채널 관리" />} />
          <Route path="videos" element={<Placeholder name="영상 목록" />} />
          <Route path="instant-analyze" element={<Placeholder name="영상 분석" />} />
          <Route path="logs" element={<Placeholder name="Logs" />} />
          <Route path="*" element={<Navigate to="." replace />} />
        </Route>
      </Route>
      <Route path="/" element={<RootRedirect />} />
      <Route path="*" element={<RootRedirect />} />
    </Routes>
  )
}
```

- [ ] **Step 2: 타입체크 + 빌드**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: 빌드 성공, `app/static/ui/index.html` 및 assets 생성.

- [ ] **Step 3: 빌드 산출물 확인**

Run: `ls ../app/static/ui/`
Expected: `index.html`, `assets/` 존재.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat: App 라우터 조립(그룹 라우트 + 루트 리다이렉트) + 빌드"
```

> 주: `app/static/ui/` 빌드 산출물의 커밋 여부는 Task 16에서 Dockerfile 전략과 함께 결정한다(기본: 빌드 산출물은 커밋하지 않고 Docker 빌드 단계에서 생성).

---

## Task 16: Dockerfile 멀티스테이지 + .dockerignore + .gitignore

**Files:**
- Modify: `Dockerfile`
- Modify: `.gitignore`
- Modify: `.dockerignore`

- [ ] **Step 1: app/static/ui를 git 추적 제외**

`.gitignore`에 추가:

```
# React 빌드 산출물 (Docker 빌드 단계에서 생성)
app/static/ui/
```

이미 커밋된 산출물이 있으면 추적 해제: `git rm -r --cached app/static/ui` (없으면 생략).

- [ ] **Step 2: Dockerfile 멀티스테이지**

`Dockerfile` 전체를 다음으로 교체:

```dockerfile
# --- 1단계: React 빌드 ---
FROM node:22-slim AS ui-build
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build
# 산출물: /ui/../app/static/ui 가 아니라, vite outDir이 상대경로이므로
# 빌드 컨텍스트 내 위치를 명시적으로 옮긴다.
# vite.config.ts의 outDir(../app/static/ui)에 맞춰 /app/static/ui로 생성됨.

# --- 2단계: Python 런타임 ---
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt
COPY app ./app
# React 빌드 산출물 복사 (ui-build 단계에서 /app/static/ui로 생성된 것)
COPY --from=ui-build /app/static/ui ./app/static/ui
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

> 주의: `ui-build` 단계의 WORKDIR은 `/ui`이고 vite `outDir`은 `../app/static/ui`이므로 산출물은 컨테이너의 `/app/static/ui`에 생성된다. 위 `COPY --from=ui-build /app/static/ui`가 이를 가리킨다. (테스트 의존성 pytest 등은 런타임 이미지에 포함되지만 무해. 최소화하려면 별도 requirements-dev로 분리 가능 — 본 Plan에서는 YAGNI로 생략.)

- [ ] **Step 3: .dockerignore에서 frontend가 제외되지 않도록 확인**

`.dockerignore`를 열어 `frontend` 또는 `**/node_modules`가 frontend 소스를 통째로 막지 않는지 확인한다. `node_modules`만 무시되어야 한다. 필요시 추가:

```
frontend/node_modules
app/static/ui
```

(빌드 컨텍스트에서 로컬 node_modules/산출물 제외 — Docker 내부에서 재생성)

- [ ] **Step 4: Docker 빌드 검증**

Run: `docker build -t ytdb:plan1 .`
Expected: 빌드 성공. 두 단계 모두 통과, 최종 이미지에 `/app/app/static/ui/index.html` 포함.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile .gitignore .dockerignore
git commit -m "build: Dockerfile 멀티스테이지(Node UI 빌드 → Python 런타임)"
```

---

## Task 17: 수동 통합 검증 (DB 필요)

자동 테스트로 못 잡는 실제 데이터 흐름을 확인한다. 그룹 1개 이상과 DB 설정이 되어 있어야 한다.

- [ ] **Step 1: 앱 기동**

Run: `uvicorn app.main:app --reload --port 8000`
(별도 터미널에서) Run: `cd frontend && npm run dev`
Expected: Vite dev 서버 기동(예: 5173), `/api` 프록시 → 8000.

- [ ] **Step 2: 그룹 존재 확인**

브라우저에서 `http://localhost:5173/app/` 접속.
Expected: 그룹이 있으면 첫 그룹 대시보드(`/app/g/<slug>/`)로 리다이렉트. 없으면 "그룹이 없습니다" 안내.

- [ ] **Step 3: 대시보드 데이터 확인**

Expected:
- 통계 카드 8종 표시(채널/영상/대기/실패/알림/태그).
- DB 정상 배너 또는 DB 미설정 시 오류 배너.
- AI Gateway 배너(설정돼 있으면 정상, 아니면 오류).
- 최신 영상 카드 그리드(없으면 "표시할 영상이 없습니다").

- [ ] **Step 4: 그룹 격리 확인 (그룹 2개 이상)**

상단 셀렉터로 다른 그룹 전환 → URL이 `/app/g/<other>/`로 바뀌고 통계/영상이 그 그룹 것으로 갱신되는지 확인.
Expected: A 그룹 수치와 B 그룹 수치가 독립적.

- [ ] **Step 5: 프로덕션 서빙 확인 (빌드본)**

Run: `cd frontend && npm run build`
브라우저에서 `http://localhost:8000/app/` 접속(uvicorn 직접).
Expected: 빌드된 React 앱이 `/app`에서 동작. 기존 `http://localhost:8000/`(vanilla)도 여전히 정상.

- [ ] **Step 6: 검증 메모 커밋(선택)**

검증 중 발견한 이슈가 있으면 후속 태스크로 기록한다. 코드 변경이 없으면 커밋 생략.

---

## Self-Review 결과 (작성자 기록)

- **스펙 커버리지**: §아키텍처(Task 7,16), §그룹 라우팅/Provider(Task 11,13,15), §api 그룹 주입+정규화(Task 8,9,10), §대시보드(Task 14)+백엔드 stats/health(Task 4,5), §페이지네이션 total 옵트인(Task 3), §SPA 서빙(Task 6). v1a 나머지 페이지·v1b 설정·컷오버는 Plan 2/3로 명시 분리.
- **플레이스홀더**: 페이지/컴포넌트 복사 태스크(12,13,14)는 실재하는 my-assistant 원본 경로를 지정 — 플레이스홀더 아님. `Placeholder` 컴포넌트(Task 15)는 Plan 2 연결 지점으로 의도된 임시물.
- **타입 일관성**: `groupClient`(get/post/patch/del), `videoApi(slug).listPaged`, `statsApi(slug).get`, `healthApi(slug).{db,gateway}`, `useGroup().activeSlug` — Task 간 시그니처 일치 확인.
- **위험**: stats/health는 DB 의존이라 자동 단위테스트 대신 순수 헬퍼(`_page_number`)만 단위테스트하고 통합은 Task 17 수동 검증. Docker 런타임에 pytest 포함되는 점은 무해(YAGNI로 dev 분리 생략).
