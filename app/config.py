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


settings = Settings()
