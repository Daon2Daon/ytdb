"""애플리케이션 환경설정 (.env 로딩)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 제어 평면 PostgreSQL DSN (postgresql+asyncpg://...). 부트스트랩 필수.
    CONTROL_DATABASE_URL: str = ""

    # 설정 시크릿 암호화 키 (Fernet, base64 url-safe 32B).
    FERNET_KEY: str = ""

    # 그룹에 봇 토큰이 미설정일 때의 기본 텔레그램 봇 (선택).
    DEFAULT_TELEGRAM_BOT_TOKEN: str = ""

    # 스케줄러 전역 틱 간격(분). 채널별 실제 폴링 주기는 channel.poll_interval_min으로 판정한다.
    MASTER_POLL_INTERVAL_MIN: int = 12
    PENDING_ANALYSIS_INTERVAL_MIN: int = 12
    # 앱 부팅 시 스케줄러 자동 시작 여부.
    SCHEDULER_ENABLED: bool = True

    # 로그인 인증(단일 계정). AUTH_PASSWORD가 비어 있으면 인증 비활성(개발).
    # 값이 설정되면 모든 /api 데이터 접근에 로그인이 강제된다.
    AUTH_USERNAME: str = "admin"
    AUTH_PASSWORD: str = ""
    # 세션 쿠키 서명 키. 비어 있으면 FERNET_KEY에서 파생한다.
    SESSION_SECRET: str = ""
    # https 배포 시 True(Secure 쿠키). 현재 http(Tailscale) 배포면 False.
    SESSION_HTTPS_ONLY: bool = False


settings = Settings()
