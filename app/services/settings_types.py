"""설정 카테고리별 타입 표현(dataclass).

그룹별 데이터 평면 DB 접속 정보. 스키마(schema_name)는 설정이 아니라
app.groups.schema_name에서 오므로 여기에는 포함하지 않는다.
공유 연결 풀의 키는 스키마를 제외한 서버 시그니처다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


PRESET_FULL: dict = {"fields": [
    "channel_name", "headline", "analysis_sections", "bullet_points",
    "tags", "published_at", "duration", "video_url", "share_link",
]}

PRESET_COMPACT: dict = {"fields": [
    "headline", "one_line", "short_summary_md",
    "sentiment", "confidence_score",
    "video_url", "share_link",
]}


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
    message_template: dict = field(default_factory=lambda: dict(PRESET_FULL))
    # 발송 기준선: 이 시각 이후 게시(published_at)된 영상만 자동 발송.
    # None이면(sendable인데도) 자동 발송 보류(안전측). 기존 backlog flood 방지.
    notify_baseline_at: Optional[datetime] = None
    # 발송 범위: scheduled 모드에서만 적용.
    # after_activation=활성화 이후 게시분만, all=과거 분석분 포함 전체 순차 발송.
    dispatch_scope: str = "after_activation"  # after_activation | all
    # 공용 봇 발송 대상(app.telegram_destinations.dest_id). None=미지정.
    # 해석 우선순위는 notify_service.resolve_notify_target 참조 (설계 §5).
    dest_id: Optional[int] = None

    @property
    def is_sendable(self) -> bool:
        return bool(self.enabled and self.bot_token and self.chat_ids)


@dataclass
class AIGatewaySettings:
    """그룹별 AI agent 설정 (litellm Gateway)."""

    base_url: str = "http://litellm:4000"
    api_key: str = ""
    primary_model: str = "gemini/gemini-2.5-flash"
    tagging_model: str = "gemini/gemini-2.5-flash"
    digest_model: str = ""
    temperature: float = 0.3
    max_tokens: int = 8192
    daily_budget_usd: float = 2.0


@dataclass
class PromptSettings:
    """그룹별 프롬프트. 비어 있으면 코드 기본값 사용.

    preset_id가 설정되면 프리셋(app.prompt_presets)이 우선한다 — 해석은
    preset_service.resolve_prompts()가 담당. 직접 프롬프트는 관리자 그룹 전용.
    """

    analysis_prompt: str = ""
    digest_prompt: str = ""
    preset_id: Optional[int] = None


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


VALID_PERIOD_DAYS = frozenset({1, 7, 30})
VALID_SCHEDULE_DAYS = frozenset({"mon", "tue", "wed", "thu", "fri", "sat", "sun"})
MAX_DIGEST_CONFIGS = 10


@dataclass
class DigestScheduleConfig:
    """그룹 digest 스케줄 1건 (configs JSON 배열 원소)."""

    id: str
    name: str = ""
    enabled: bool = False
    period_days: int = 7  # 1 | 7 | 30
    schedule_time: str = "20:00"  # HH:MM
    schedule_day: str = "sun"  # 7일 전용
    schedule_dom: int = 1  # 30일 전용, 1..28
    timezone: str = "Asia/Seoul"
    category: str = ""
    digest_prompt: str = ""
    telegram_enabled: bool = False


@dataclass
class DigestShareSettings:
    """그룹 공통 digest 텔레그램 공유 링크 설정."""

    share_link_enabled: bool = True


def period_type_from_days(period_days: int) -> str:
    if period_days == 1:
        return "daily"
    if period_days == 30:
        return "monthly"
    return "weekly"


def period_label_from_days(period_days: int) -> str:
    if period_days == 1:
        return "일간"
    if period_days == 30:
        return "월간"
    return "주간"


# 레거시 호환 alias (일부 테스트·import)
DigestSettings = DigestScheduleConfig
