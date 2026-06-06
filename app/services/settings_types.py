"""설정 카테고리별 타입 표현(dataclass).

그룹별 데이터 평면 DB 접속 정보. 스키마(schema_name)는 설정이 아니라
app.groups.schema_name에서 오므로 여기에는 포함하지 않는다.
공유 연결 풀의 키는 스키마를 제외한 서버 시그니처다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class NotificationSettings:
    """그룹별 텔레그램 알림 설정.

    chat_ids가 비어 있으면 발송하지 않고 분석/데이터만 기록한다.
    chat_ids에 여러 대상을 넣으면 그룹 단위로 복수 채널에 발송한다.
    """

    enabled: bool = True
    bot_token: str = ""
    chat_ids: list[str] = field(default_factory=list)
    parse_mode: str = "HTML"
    # 발송 모드/예약
    send_mode: str = "immediate"  # immediate | scheduled
    scheduled_times: list[str] = field(default_factory=list)  # HH:MM, 최대 10
    scheduled_max_per_run: int = 5
    wait_between_messages_sec: int = 30
    # 야간 제한
    quiet_hours_enabled: bool = False
    quiet_hours_start: str = "22:00"
    quiet_hours_end: str = "07:00"
    timezone: str = "Asia/Seoul"
    # 표시
    low_confidence_threshold: float = 0.5
    message_detail: str = "full"  # full | compact
    include_share_link: bool = True
    # 발송 기준선: 이 시각 이후 게시(published_at)된 영상만 자동 발송.
    # None이면(sendable인데도) 자동 발송 보류(안전측). 기존 backlog flood 방지.
    notify_baseline_at: Optional[datetime] = None
    # 발송 범위: scheduled 모드에서만 적용.
    # after_activation=활성화 이후 게시분만, all=과거 분석분 포함 전체 순차 발송.
    dispatch_scope: str = "after_activation"  # after_activation | all

    @property
    def is_sendable(self) -> bool:
        return bool(self.enabled and self.bot_token and self.chat_ids)


@dataclass
class AIGatewaySettings:
    """그룹별 AI agent 설정 (litellm Gateway)."""

    base_url: str = "http://litellm:4000"
    api_key: str = ""
    primary_model: str = "gemini/gemini-2.5-flash"
    fallback_model: str = "gemini/gemini-2.5-flash"
    tagging_model: str = "gemini/gemini-2.5-flash"
    digest_model: str = ""
    temperature: float = 0.3
    max_tokens: int = 8192
    daily_budget_usd: float = 2.0


@dataclass
class PromptSettings:
    """그룹별 프롬프트. 비어 있으면 코드 기본값 사용."""

    analysis_prompt: str = ""
    digest_prompt: str = ""


@dataclass
class PollingSettings:
    """그룹별 폴링/쿼터/동시성 설정."""

    master_interval_min: int = 12
    pending_analysis_interval_min: int = 12
    default_channel_interval_min: int = 720
    youtube_api_key: str = ""
    youtube_daily_quota: int = 10000
    window_hours: int = 24
    max_concurrent_channels: int = 5
    max_concurrent_analyses: int = 3
    analysis_interval_sec: int = 120
    stats_refresh_days: int = 30  # 게시 후 N일 이내 영상 stats 갱신. 0이면 비활성.


@dataclass
class DatabaseSettings:
    host: str = ""
    port: int = 5432
    dbname: str = ""
    username: str = ""
    password: str = ""
    sslmode: str = "prefer"

    @property
    def is_configured(self) -> bool:
        return bool(self.host and self.username and self.dbname)

    def server_signature(self) -> str:
        """공유 풀 식별용(비밀번호 제외, 스키마 제외)."""
        return f"{self.host}:{self.port}:{self.dbname}:{self.username}:{self.sslmode}"


@dataclass
class DigestSettings:
    enabled: bool = False
    period_weeks: int = 1
    schedule_day: str = "sun"  # mon..sun
    schedule_time: str = "20:00"  # HH:MM
    timezone: str = "Asia/Seoul"
    telegram_enabled: bool = False
    category: str = ""
