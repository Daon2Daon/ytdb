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
