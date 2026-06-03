# 로그인 인증 설계 (단일 계정 · httpOnly 세션 쿠키)

- 작성일: 2026-06-03
- 상태: 설계 승인됨

## 배경 / 목표

ytdb는 현재 인증이 전혀 없어 `/api`와 UI가 모두 열려 있다(8000 포트). 프로덕션 노출 대비로 **단일 운영자 계정 로그인**을 추가해, 로그인하지 않으면 데이터(API)에 접근하지 못하게 한다.

## 결정 (승인됨)

- **사용자 모델**: 단일 계정(아이디+비밀번호). 사용자 관리/가입/권한 없음.
- **메커니즘**: Starlette `SessionMiddleware` 기반 **서명된 httpOnly 세션 쿠키**(동일 출처 SPA에 최적, XSS 안전, 프론트 거의 무변경).
- **자격증명 저장**: `.env`(기존 시크릿과 동일 위치). DB 테이블 미사용.
- **보호 범위**: **SPA 번들(`/`,`/static`,catch-all)·`/health`는 공개**, **모든 데이터 `/api`는 잠금**(표준 SPA 패턴 — 번들은 받되 데이터는 인증 필요).
- **프로덕션 토글**: `AUTH_PASSWORD` 미설정 시 **인증 비활성(개발 호환, 기존처럼 열림)**, 설정 시 **강제**.

## 비목표
- 비밀번호 변경 UI, 다중 사용자, 역할/권한, 비번 재설정 메일 등(추후 DB 테이블로 승격 가능).

## 설정 (config.py / .env)
```
AUTH_USERNAME: str = "admin"     # 로그인 아이디
AUTH_PASSWORD: str = ""          # 비어 있으면 인증 비활성(개발). 설정 시 강제.
SESSION_SECRET: str = ""         # 세션 쿠키 서명 키. 비면 FERNET_KEY에서 파생.
SESSION_HTTPS_ONLY: bool = False # https 배포 시 True(Secure 쿠키). 현재 http면 False.
```
`AUTH_ENABLED = bool(AUTH_PASSWORD.strip())` 로 파생.

## 백엔드

### 미들웨어 (main.py)
`app.add_middleware(SessionMiddleware, secret_key=<SESSION_SECRET or FERNET_KEY or 생성값>, https_only=SESSION_HTTPS_ONLY, same_site="lax")`. (Starlette SessionMiddleware 쿠키는 HttpOnly 기본.)

### 인증 의존성 (`app/services/auth.py` 또는 routers/auth.py)
```python
def require_auth(request: Request) -> None:
    if not AUTH_ENABLED:
        return  # 비활성: 통과
    if not request.session.get("user"):
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")
```

### 엔드포인트 (`app/routers/auth.py`, prefix `/api/auth`)
- `POST /login` {username, password} → 검증(`secrets.compare_digest`로 상수시간 비교) → `request.session["user"]=username` → `{username}`. 실패 시 401. `AUTH_ENABLED=False`면 400("인증이 설정되지 않았습니다").
- `POST /logout` → `request.session.clear()` → 204
- `GET /me` → `{ authenticated: bool, username: str|null, auth_enabled: bool }` (미로그인이어도 200으로 상태를 알려줌 — 프론트가 로그인 화면 여부 판단)

### 보호 적용 (main.py)
데이터 라우터 등록에 의존성 추가:
`app.include_router(groups.router, dependencies=[Depends(require_auth)])` — groups/channels/videos/tags/digests/actions/logs/settings/stats/health… 단, **auth 라우터는 무인증**, `/health`(meta)와 SPA 서빙은 그대로 공개.
(주: 그룹 스코프 `/api/groups/{slug}/health`는 보호 대상. 무인증 `/health` meta 엔드포인트만 공개.)

## 프론트엔드 (`frontend/src/`)
- `api/auth.ts`: `authApi.me()`, `login(username,password)`, `logout()`.
- `auth/AuthProvider.tsx` + `useAuth`: 마운트 시 `me()` 호출. 상태 `{loading, authEnabled, authenticated, username}`.
  - `loading` → Spinner
  - `authEnabled && !authenticated` → `<LoginPage/>`
  - else → children(앱)
- `pages/Login.tsx`: 아이디/비번 폼 → `login()` → 성공 시 `me()` 재조회 → 앱 진입. 실패 메시지.
- `components/Layout.tsx`: 헤더에 **로그아웃** 버튼(`logout()` → authenticated=false).
- `api/http.ts`: `request()`에 **401 인터셉터** — 모듈 수준 `onUnauthorized` 콜백을 두고, 401 응답 시 호출(AuthProvider가 등록 → authenticated=false로 전환). 동일 출처라 쿠키 자동 전송(credentials 기본 same-origin), 기존 호출 코드 변경 없음.
- `main.tsx`: `<BrowserRouter>` 안에서 `<AuthProvider>`가 `<App/>`을 감싼다.

## 테스트 (pytest, TestClient 쿠키 자동 보관)
- `AUTH_ENABLED=True` 환경에서:
  - 보호 엔드포인트 미로그인 → 401
  - `/login` 잘못된 비번 → 401, 올바른 → 200 + 세션 쿠키
  - 로그인 후 보호 엔드포인트 → 통과(혹은 DB 미설정 등 다른 사유의 비-401)
  - `/logout` 후 보호 엔드포인트 → 401
  - `/health`, `/`(SPA) → 무인증 200/503
  - `AUTH_ENABLED=False`면 `/me`의 auth_enabled=false, 보호 엔드포인트 무인증 접근 가능
- 테스트는 설정값을 monkeypatch로 토글.

## 범위
백엔드: `config.py`, `app/routers/auth.py`, `app/main.py`(미들웨어+의존성 배선), `.env.example`. 프론트: `api/auth.ts`, `auth/AuthProvider.tsx`, `pages/Login.tsx`, `api/http.ts`(401), `components/Layout.tsx`(로그아웃), `main.tsx`. 테스트.
