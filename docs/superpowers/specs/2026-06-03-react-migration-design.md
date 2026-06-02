# ytdb 프론트엔드 React 마이그레이션 설계

- 작성일: 2026-06-03
- 상태: 설계 승인됨 (구현 계획 수립 대기)

## 배경 & 목표

ytdb는 my-assistant의 YouTube 기능을 독립 발전시킨 프로젝트로, **다중 모니터링 그룹 운영**과 **그룹별 발송 로직**을 핵심 강점으로 갖는다. 그러나 프론트엔드가 단일 vanilla JS(`app/static/app.js`, 845줄)라 my-assistant의 React UI 대비 사용성이 떨어진다(라이브 폴링 부재, 마크다운 미렌더, 페이지네이션 없음, 대시보드 없음 등).

**목표:** my-assistant의 검증된 React UI를 ytdb로 이식하되, ytdb의 다중 그룹·그룹별 설정·발송 로직(모두 백엔드)을 **무손상으로 보존**한다.

### 확정된 결정 (사용자 승인)

1. **현재 프로젝트 수정** (새 프로젝트 ❌). React 마이그레이션은 프론트엔드 전용이며, 보존 대상인 다중 그룹·발송 로직은 전부 백엔드(`app/routers`, `app/services`, `app/models`)에 있다. 새 프로젝트는 그 백엔드를 재구현/복사해야 해 가장 안정적인 부분을 위태롭게 한다.
2. **다중 그룹 + 그룹별 설정 보존** — 각 그룹은 독립적인 AI 게이트웨이·DB·프롬프트·텔레그램 설정을 갖는다. 이는 **이미 백엔드에 완전 구현**돼 있다(제어 평면 `app.settings`의 `(group_id, category, key)` 저장).
3. **그룹 모델: URL 경로에 slug 포함** — `/app/g/:slug/...`.
4. **코드 재사용: my-assistant React 적극 재사용 + 그룹화** — API 클라이언트 한 곳에서 그룹 스코프 주입.
5. **범위: 핵심 우선(v1) + 단계적 컷오버**. 설정은 그룹별 설정이 핵심이라 v1에 포함.

## 비목표 (Out of Scope)

- 백엔드 비즈니스 로직(폴링·분석·발송·스케줄·멀티스키마) 변경 — 추가만 하고 기존 로직은 건드리지 않는다.
- 주간 리뷰 다이제스트의 **상세/목록 화면**은 v2로 미룬다(다이제스트 *설정*은 v1 포함).
- my-assistant 자체 변경.

## 아키텍처 & 레이아웃

```
ytdb/
├─ app/                      # 백엔드 — 무손상 (다중 그룹·발송 로직 유지)
│  ├─ routers/               # v1용 엔드포인트 일부 추가 (§ 백엔드 추가)
│  ├─ services/
│  ├─ models/
│  └─ static/
│     ├─ index.html, app.js  # 기존 vanilla — 컷오버 전까지 생존, 이후 /legacy
│     └─ ui/                 # ← Vite React 빌드 산출물 (신규)
├─ frontend/                 # ← 신규 React 소스 (빌드 시에만 사용, 런타임 무관)
│  ├─ src/
│  │  ├─ api/                # 그룹 스코프 클라이언트
│  │  ├─ group/              # GroupProvider, 그룹 셀렉터 (신규, my-assistant엔 없음)
│  │  ├─ components/         # Spinner·Pagination·StatusBadge·NotifyBadge·ErrorBanner·Layout (복사)
│  │  └─ pages/              # 운영 화면 + 설정 서브페이지 (복사+그룹화)
│  ├─ vite.config.ts         # outDir: ../app/static/ui, base: /static/ui/
│  └─ package.json
```

- **백엔드 경계**: React는 `app/services`·`app/models`·기존 라우팅 로직을 건드리지 않고, 라우터에 v1용 엔드포인트만 *추가*(비파괴).
- **런타임 의존성 없음**: `frontend/`는 빌드 시점에만 필요. 배포 산출물은 `app/static/ui/`의 정적 파일.
- **서빙/공존 방식 (선택안 A)**: Vite를 `app/static/ui/`로 빌드, FastAPI가 `/app/*`에서 React `index.html` 서빙 + SPA catch-all. 기존 vanilla `/`는 무손상 생존. 검증 후 `/` 진입점만 React로 플립(즉시 롤백 가능).

## 그룹 컨텍스트 & 라우팅

### 라우트 구조 (slug를 경로에 포함)

```
/app/g/:slug/                     → Dashboard
/app/g/:slug/channels             → Channels
/app/g/:slug/videos               → Videos (?status=&tag=&channel_pk=&page=)
/app/g/:slug/videos/:videoPk      → VideoDetail
/app/g/:slug/instant-analyze      → InstantAnalyze
/app/g/:slug/logs                 → Logs
/app/g/:slug/settings/database    → DatabaseSettings
/app/g/:slug/settings/ai-gateway  → AIGatewaySettings
/app/g/:slug/settings/runtime     → RuntimeSettings (polling)
/app/g/:slug/settings/notification→ NotificationSettings
/app/g/:slug/settings/prompts     → PromptSettings
/app/g/:slug/settings/digest      → DigestSettings
/app/                             → 그룹 목록 조회 후 첫 그룹으로 리다이렉트 (없으면 "그룹 생성" 화면)
/app/g/:slug/*                    → 해당 그룹 대시보드로 폴백
```

### GroupProvider (신규)

그룹 컨텍스트의 단일 소스:
- 앱 마운트 시 `GET /api/groups` 1회 호출 → 그룹 목록 보관(셀렉터용).
- 현재 `:slug`를 `useParams`로 읽어 활성 그룹으로 노출(`useGroup()` 훅).
- URL의 slug가 목록에 없으면 첫 그룹으로 리다이렉트(또는 빈 상태 화면).
- 그룹 생성/이름수정 모달 보유(현재 vanilla의 `newGroupModal`/`editGroupModal` 기능 이식).

### 그룹 셀렉터 UX (Layout 상단바)

- 드롭다운 변경 시 **현재 페이지를 유지한 채 slug만 교체**해 navigate. 예: `/app/g/invest/videos` → `/app/g/media/videos`.
- PK가 그룹 종속인 경로(VideoDetail 등)는 그룹 전환 시 안전하게 그룹 대시보드로 보낸다.
- 옆에 `+ 새 그룹` · `이름 수정` 버튼.

### API 클라이언트 그룹 주입 (재사용의 핵심 레버)

- my-assistant의 `api/client.ts`는 `/api/...`를 직접 호출. 이를 **`groupClient(slug)` 팩토리**로 감싸 base를 `/api/groups/{slug}`로 고정.
- 페이지는 `useGroup()`의 slug로 `videoApi`·`channelApi` 등을 바인딩 → 그룹 주입이 api 레이어 한 곳에 집중, 페이지 본문은 거의 그대로.
- 전역(그룹 비종속) 엔드포인트(`GET /api/groups`)만 별도 분리.

### 그룹별 설정 — 이미 백엔드에 구현됨

- `app.settings`에 `(group_id, category, key)`로 저장. 카테고리: `database · ai_gateway · prompts · polling · notification · digest`.
- 그룹마다 독립적인 DB 접속·AI 게이트웨이·프롬프트·텔레그램을 갖는다. 시크릿은 Fernet 암호화 저장·마스킹 응답.
- 데이터 평면도 그룹별 분리: 각 그룹은 자기 DB/스키마를 쓰고 `get_database(group_id)`로 접속 해석.
- **마이그레이션 함의**: 모든 데이터 API가 `/api/groups/{slug}/...`라 slug만 맞으면 백엔드가 해당 그룹의 DB·게이트웨이·프롬프트·텔레그램을 자동 선택. 프론트는 slug 바인딩만으로 격리 충족 — 기능 손실 위험 없음.

## 페이지별 이식 매핑

| 페이지 | 출처 (my-assistant) | 그룹화 변경 | UX 개선 (vanilla 대비) | 백엔드 필요 | 단계 |
|---|---|---|---|---|---|
| Dashboard | `Dashboard.tsx` | 통계·헬스 그룹 스코프 | 통계 카드 + DB/Gateway 헬스 배너 + 최근 24h | stats·health 엔드포인트 | v1a |
| Channels | `Channels.tsx` | api slug 바인딩 | 채널별 개별 폴링·토글 스위치·추가 시 즉시폴링 | 채널 단건 poll | v1a |
| Videos | `Videos.tsx` | slug 바인딩, 태그 드롭다운 | 페이지네이션·필터 URL 영속·썸네일 카드 | list 응답 `total` | v1a |
| VideoDetail | `VideoDetail.tsx` | slug 바인딩 | 모달→전용 화면·라이브 폴링·마크다운 렌더·신뢰도 바 | reanalyze/analyze-now(기존) | v1a |
| InstantAnalyze | `InstantAnalyze.tsx` | slug 바인딩 | 등록 후 완료까지 폴링→상세 자동 이동 | instant(기존) | v1a |
| Logs | `Jobs.tsx` | slug 바인딩 | 테이블·잡타입/상태 필터·요약 배지·30초 자동갱신·페이지네이션 | logs 필터·`total` | v1a |
| Settings 6종 | `pages/settings/*` | slug 바인딩 | 폼 UI 일관화 | 없음 (엔드포인트 기존) | v1b |
| 공통 컴포넌트 | Spinner·Pagination·StatusBadge·NotifyBadge·ErrorBanner·Layout | Layout에 그룹 셀렉터 삽입 | 일관된 로딩/에러/빈 상태 | — | v1a |

**v1 내부 순서:**
- **v1a (운영 화면)**: 그룹 셀렉터 + 대시보드 + 채널 + 영상 목록/상세 + 영상분석 + 로그.
- **v1b (설정 + 그룹 관리)**: 설정 6종 + 그룹 생성/이름수정 모달.
- 둘 다 갖춘 뒤 컷오버.

**v2로 남김**: 다이제스트 상세/목록 화면, 텔레그램 수동 발송/미리보기, 영상별 커스텀 프롬프트 재분석. v1 공존 기간 동안 이 기능들은 vanilla 앱에서 계속 사용 가능.

**핵심 원칙**: my-assistant 페이지의 JSX·로직은 최대한 보존하고, 바뀌는 것은 ① api 인스턴스의 그룹 바인딩, ② 라우트 경로(`/youtube/...` → `/app/g/:slug/...`) 두 가지뿐.

## 데이터 흐름 & 라이브 폴링

- **기본 흐름**: 페이지 마운트 → `useGroup()`의 slug로 바인딩된 api 호출 → `loading`/`error`/`data` 상태 → Spinner / ErrorBanner(재시도) / 콘텐츠 렌더.
- **라이브 폴링** (vanilla 대비 핵심 개선):
  - *VideoDetail*: 재분석/즉시분석 트리거 후 2초 간격 `silentRefresh`, 상태가 `done|failed`가 되면 자동 중단, 3분 안전 타임아웃.
  - *InstantAnalyze*: 등록 후 완료까지 폴링 → 상세로 자동 이동.
  - *Logs*: 30초 간격 자동 새로고침.
- **필터 영속**: 영상/로그 목록 필터·페이지를 URL 쿼리(`useSearchParams`)에 보관 → 새로고침·뒤로가기·공유에 유지.
- **그룹 전환 시**: slug가 바뀌면 React Router가 페이지를 재마운트 → 새 그룹 데이터 로드. 폴링 타이머는 언마운트 시 정리.

## 필요한 백엔드 추가 (비파괴, 추가만)

| 변경 | 위치 | 이유 |
|---|---|---|
| 목록 응답에 `total` 추가 (옵트인 `?paged=1` → `{items, total}`) | `app/routers/videos.py`, `app/routers/logs.py` | 페이지네이션 UI (vanilla 무수정 위해 옵트인) |
| 그룹 스코프 통계 엔드포인트 | 신규 `GET /api/groups/{slug}/stats` | 대시보드 카드 |
| DB/Gateway 헬스 엔드포인트 (그룹 스코프) | 신규 | 대시보드·Layout 헬스 배너 |
| SPA catch-all 라우트 + `/app` 서빙 | `app/main.py` | React 클라이언트 라우팅 폴백 |
| 채널 단건 폴링 | `app/routers/channels.py` 확인/추가 | 채널별 "모니터링" 버튼 |
| Dockerfile 프론트 빌드 단계 (멀티스테이지) | `Dockerfile` | `app/static/ui` 산출물 포함 |

> `total` 추가는 응답 형태가 바뀌므로 기존 vanilla `loadVideos`에 영향. **`?paged=1` 옵트인**으로 신규 형태를 노출해 vanilla는 무수정 유지(권장).

## 컷오버 & 테스트 전략

### 공존 → 컷오버 단계

1. `frontend/` 스캐폴딩, Vite를 `app/static/ui/`로 빌드. main.py에 `/app/*` 서빙 + SPA catch-all 추가. 이 시점에 vanilla `/`는 무손상.
2. v1a(운영 화면) 이식 → `/app`에서 실데이터 검증.
3. v1b(설정 + 그룹 관리) 이식 → 그룹 생성→설정→모니터링 전체 흐름 검증.
4. 기능 동등성 확인 후 컷오버: main.py `/` 진입점을 React `index.html`로 교체. vanilla는 `/legacy`로 보존(즉시 롤백 경로).
5. 안정화 후 v2(다이제스트 등)와 vanilla 제거.

### 롤백

문제 발생 시 `/` 진입점을 vanilla로 되돌리는 한 줄 — 데이터·백엔드 무관.

### 테스트 전략

- **백엔드 (추가분)**: 신규 엔드포인트(stats, health, `total` 페이지네이션, 채널 단건 폴링)는 추가 시 pytest 테스트 동반(TDD). 기존 라우터는 회귀 방지용 스모크 테스트. (참고: my-assistant `tests/youtube/`.)
- **프론트엔드**: 그룹 바인딩 api 클라이언트(slug 주입)·GroupProvider 리다이렉트 로직 등 순수 로직은 Vitest 단위 테스트. 페이지 컴포넌트는 수동 검증 중심.
- **수동 검증 체크리스트** (그룹 2개 이상):
  1. 데이터 격리 — A 그룹 영상이 B에 안 보임.
  2. 그룹별 설정 격리 — 서로 다른 게이트웨이/프롬프트/텔레그램 적용.
  3. 라이브 폴링 진행/중단.
  4. 필터 URL 영속.
  5. 컷오버/롤백.

### 위험 & 완화

- *`total` 응답 변경이 vanilla 깨뜨림* → `?paged=1` 옵트인으로 vanilla 무수정.
- *그룹별 DB 미설정 상태* → 백엔드가 `DBNotConfiguredError`(400) 반환 → 프론트는 ErrorBanner로 "DB 설정 필요" 안내 + 설정 링크.
- *Docker 빌드에 Node 필요* → 멀티스테이지(Node 빌드 → Python 런타임 COPY)로 격리.

## 미해결 / 구현 계획에서 확정할 사항

- 통계 엔드포인트가 반환할 정확한 지표 집합(채널/영상/대기/실패/알림/태그 등) 및 그룹별 DB 미설정 시 동작.
- 헬스 엔드포인트를 그룹 스코프로 둘지 전역으로 둘지(그룹별 DB·게이트웨이가 다르므로 그룹 스코프가 자연스러움).
- 채널 단건 폴링 엔드포인트의 기존 존재 여부 확인(없으면 신규).
- React 라우터 base(`/app`)와 Vite `base`(`/static/ui/`)의 정합 — 정적 자산 경로 vs 클라이언트 라우트 경로 분리 처리.
