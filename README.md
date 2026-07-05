# ytdb

다중 모니터링 그룹을 지원하는 YouTube 자동 모니터 + AI 분석 + 알림 서비스.

그룹별로 사용하는 AI agent(게이트웨이/모델/프롬프트), DB(스키마), 알림(텔레그램)을 각각 설정하여 관리한다. 상세 설계는 `docs/architecture.md` 참고.

## 요구사항

- Python 3.10+
- PostgreSQL 14+ (제어 평면 + 그룹별 데이터 평면 스키마)

## 설치

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 값 채우기 (CONTROL_DATABASE_URL, FERNET_KEY)
```

## 실행

```bash
uvicorn app.main:app --reload
```

부팅 시 제어 평면 `app` 스키마(groups/settings)를 멱등 생성한다.

- API 문서: `http://localhost:8000/docs`
- 헬스체크: `GET /health`

## 계정

- 최초 부팅 시 `.env`의 `AUTH_USERNAME`/`AUTH_PASSWORD`로 admin 계정이 자동 생성된다
  (로그인 ID는 이메일 형식 — `AUTH_USERNAME`이 이메일이 아니면 `{username}@local`).
- 일반 사용자는 관리자가 발급한 초대 링크(`/signup?token=...`)로 가입한다.
- `AUTH_PASSWORD` 미설정 + 사용자 0명이면 인증 비활성(개발 모드).

## 프리셋과 공유 분석 캐시

- 관리자는 `/api/admin/presets`로 분석 프롬프트 프리셋을 만든다. 프리셋 본문은 불변 —
  수정하려면 새 프리셋을 만들고 구버전을 비활성화한다.
- 그룹 설정 `prompts` 카테고리에 `preset_id`(int)를 저장하면 그 그룹은 프리셋을 사용하며,
  같은 영상×프리셋×모델 분석은 시스템 전체에서 1회만 수행된다(공유 캐시).
- `preset_id`가 없는 그룹(기존 admin 그룹의 직접 프롬프트)은 기존 경로로 동작한다.

## 구조 (P1)

```
app/
├── main.py            FastAPI 진입점 + lifespan
├── config.py          환경설정 (pydantic-settings)
├── control_db.py      제어 평면 async 엔진/세션/Base
├── models/control/    groups, settings 모델
├── services/          settings_manager (그룹별 설정 로더, Fernet)
├── schemas/           Pydantic 입출력 스키마
└── routers/           groups, settings API
```
