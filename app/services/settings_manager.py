"""그룹별 설정 로더 (제어 평면 app.settings).

- (group_id, category) 단위 조회/저장.
- is_secret 값은 Fernet으로 암호화 저장, 응답 시 마스킹.
- (group_id, category) 키로 TTL 메모리 캐시.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings as app_settings
from app.control_db import get_sessionmaker
from app.models.control.setting import Setting
from app.services.digest_config import legacy_flat_to_config, parse_digest_configs
from app.services.settings_types import (
    AIGatewaySettings,
    DatabaseSettings,
    DigestScheduleConfig,
    DigestShareSettings,
    NotificationSettings,
    PollingSettings,
    PromptSettings,
)


def _as_float(v: Any, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _as_dt(v: Any) -> datetime | None:
    """UTC ISO 문자열을 tz-aware datetime으로. 빈 값/파싱 실패는 None.

    naive datetime은 UTC로 간주한다.
    """
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(v))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _normalize_dispatch_scope(v: Any) -> str:
    """dispatch_scope 정규화. 유효값만 통과, 그 외는 안전측 기본값."""
    s = str(v or "").strip()
    return s if s in ("after_activation", "all") else "after_activation"


def _detail_to_template(detail) -> dict:
    """레거시 message_detail 문자열을 프리셋으로 변환."""
    from app.services.settings_types import PRESET_COMPACT, PRESET_FULL
    return dict(PRESET_COMPACT) if str(detail or "").strip() == "compact" else dict(PRESET_FULL)


def _parse_message_template(d: dict) -> dict:
    raw = d.get("message_template")
    if isinstance(raw, dict) and "fields" in raw:
        return raw
    return _detail_to_template(d.get("message_detail"))


class SettingsSecretError(RuntimeError):
    """시크릿 복호화 실패 또는 Fernet 키 부재."""


def _fernet_from_key(key: str | None) -> Fernet | None:
    if not (key and key.strip()):
        return None
    try:
        return Fernet(key.strip().encode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise SettingsSecretError(f"FERNET_KEY가 유효하지 않습니다: {e}") from e


def mask_secret(plain: str, keep_last: int = 4) -> str:
    if not plain:
        return ""
    if len(plain) <= keep_last:
        return "*" * len(plain)
    return "*" * (len(plain) - keep_last) + plain[-keep_last:]


def _coerce(raw: str, value_type: str | None) -> Any:
    vt = (value_type or "string").lower()
    if vt == "int":
        return int(raw) if raw not in ("", None) else 0
    if vt == "float":
        return float(raw) if raw not in ("", None) else 0.0
    if vt == "bool":
        return str(raw).lower() in ("1", "true", "yes", "on")
    if vt == "json":
        return json.loads(raw) if raw else None
    return raw


class SettingsManager:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        fernet_key: str | None,
        cache_ttl_sec: float = 60.0,
    ) -> None:
        self._sf = session_factory
        self._fernet = _fernet_from_key(fernet_key)
        self._cache_ttl = cache_ttl_sec
        # (group_id, category) -> (typed dict, expiry)
        self._cache: dict[tuple[int, str], tuple[dict[str, Any], float]] = {}

    def invalidate(self, group_id: int, category: str | None = None) -> None:
        if category is None:
            for key in [k for k in self._cache if k[0] == group_id]:
                self._cache.pop(key, None)
        else:
            self._cache.pop((group_id, category), None)

    def _decrypt(self, row: Setting) -> str:
        if not row.value_enc:
            return ""
        if self._fernet is None:
            raise SettingsSecretError("시크릿을 읽으려면 FERNET_KEY가 필요합니다.")
        try:
            return self._fernet.decrypt(row.value_enc).decode("utf-8")
        except InvalidToken as e:
            raise SettingsSecretError("시크릿 복호화에 실패했습니다.") from e

    def _plain(self, row: Setting) -> str:
        if row.is_secret:
            return self._decrypt(row)
        return row.value if row.value is not None else ""

    async def _fetch_rows(self, group_id: int, category: str) -> list[Setting]:
        async with self._sf() as session:
            result = await session.execute(
                select(Setting).where(
                    Setting.group_id == group_id, Setting.category == category
                )
            )
            return list(result.scalars().all())

    async def get_typed(self, group_id: int, category: str) -> dict[str, Any]:
        """key -> 복호화·타입변환된 값. 캐시 적용."""
        cache_key = (group_id, category)
        now = time.monotonic()
        hit = self._cache.get(cache_key)
        if hit is not None and now < hit[1]:
            return hit[0]

        rows = await self._fetch_rows(group_id, category)
        typed = {r.key: _coerce(self._plain(r), r.value_type) for r in rows}
        self._cache[cache_key] = (typed, now + self._cache_ttl)
        return typed

    async def get_database(self, group_id: int) -> DatabaseSettings:
        """그룹의 데이터 평면 DB 접속 설정(타입 변환)."""
        d = await self.get_typed(group_id, "database")
        return DatabaseSettings(
            host=str(d.get("host") or ""),
            port=int(d.get("port") or 5432),
            dbname=str(d.get("dbname") or ""),
            username=str(d.get("username") or ""),
            password=str(d.get("password") or ""),
            sslmode=str(d.get("sslmode") or "prefer"),
        )

    async def get_ai_gateway(self, group_id: int) -> AIGatewaySettings:
        d = await self.get_typed(group_id, "ai_gateway")
        return AIGatewaySettings(
            base_url=str(d.get("base_url") or "http://litellm:4000"),
            api_key=str(d.get("api_key") or ""),
            primary_model=str(d.get("primary_model") or "gemini/gemini-2.5-flash"),
            tagging_model=str(d.get("tagging_model") or "gemini/gemini-2.5-flash"),
            digest_model=str(d.get("digest_model") or ""),
            temperature=_as_float(d.get("temperature"), 0.3),
            max_tokens=_as_int(d.get("max_tokens"), 8192),
            daily_budget_usd=_as_float(d.get("daily_budget_usd"), 2.0),
        )

    async def get_prompts(self, group_id: int) -> PromptSettings:
        d = await self.get_typed(group_id, "prompts")
        raw_preset = d.get("preset_id")
        try:
            preset_id = int(raw_preset) if raw_preset not in (None, "", 0, "0") else None
        except (TypeError, ValueError):
            preset_id = None
        return PromptSettings(
            analysis_prompt=str(d.get("analysis_prompt") or ""),
            digest_prompt=str(d.get("digest_prompt") or ""),
            preset_id=preset_id,
        )

    async def get_polling(self, group_id: int) -> PollingSettings:
        d = await self.get_typed(group_id, "polling")
        master = _as_int(d.get("master_interval_min"), 12)
        pending = d.get("pending_analysis_interval_min")
        return PollingSettings(
            master_interval_min=master,
            pending_analysis_interval_min=_as_int(pending, master) if pending not in (None, "") else master,
            default_channel_interval_min=_as_int(d.get("default_channel_interval_min"), 720),
            youtube_api_key=str(d.get("youtube_api_key") or ""),
            youtube_daily_quota=_as_int(d.get("youtube_daily_quota"), 10000),
            window_hours=_as_int(d.get("window_hours"), 24),
            max_concurrent_channels=_as_int(d.get("max_concurrent_channels"), 5),
            max_concurrent_analyses=_as_int(d.get("max_concurrent_analyses"), 3),
            analysis_interval_sec=_as_int(d.get("analysis_interval_sec"), 120),
            stats_refresh_days=_as_int(d.get("stats_refresh_days"), 30),
        )

    async def get_notification(self, group_id: int) -> NotificationSettings:
        d = await self.get_typed(group_id, "notification")
        raw_ids = d.get("chat_ids")
        chat_ids: list[str] = []
        if isinstance(raw_ids, list):
            chat_ids = [str(x).strip() for x in raw_ids if str(x).strip()]
        elif isinstance(raw_ids, str) and raw_ids.strip():
            chat_ids = [p.strip() for p in raw_ids.split(",") if p.strip()]
        # 단일 chat_id 키도 허용(레거시/단순 입력)
        single = d.get("chat_id")
        if single and str(single).strip() and str(single).strip() not in chat_ids:
            chat_ids.append(str(single).strip())
        raw_times = d.get("scheduled_times")
        scheduled_times: list[str] = []
        if isinstance(raw_times, list):
            scheduled_times = [str(x).strip() for x in raw_times if str(x).strip()]
        elif isinstance(raw_times, str) and raw_times.strip():
            scheduled_times = [p.strip() for p in raw_times.split(",") if p.strip()]
        return NotificationSettings(
            enabled=bool(d.get("enabled", True)),
            bot_token=str(d.get("bot_token") or ""),
            chat_ids=chat_ids,
            parse_mode=str(d.get("parse_mode") or "HTML"),
            send_mode=str(d.get("send_mode") or "immediate"),
            scheduled_times=scheduled_times,
            scheduled_max_per_run=_as_int(d.get("scheduled_max_per_run"), 5),
            wait_between_messages_sec=_as_int(d.get("wait_between_messages_sec"), 30),
            quiet_hours_enabled=bool(d.get("quiet_hours_enabled", False)),
            quiet_hours_start=str(d.get("quiet_hours_start") or "22:00"),
            quiet_hours_end=str(d.get("quiet_hours_end") or "07:00"),
            timezone=str(d.get("timezone") or "Asia/Seoul"),
            low_confidence_threshold=_as_float(d.get("low_confidence_threshold"), 0.5),
            message_template=_parse_message_template(d),
            notify_baseline_at=_as_dt(d.get("notify_baseline_at")),
            dispatch_scope=_normalize_dispatch_scope(d.get("dispatch_scope")),
            # 음수/비정상 값은 PUT 검증이 막고, 발송 시 resolve가 재조회로 방어 — 로드는 관대하게
            dest_id=_as_int(d.get("dest_id"), 0) or None,
        )

    async def get_digest_configs(self, group_id: int) -> list[DigestScheduleConfig]:
        d = await self.get_typed(group_id, "digest")
        raw_configs = d.get("configs")
        if raw_configs is not None:
            parsed = parse_digest_configs(raw_configs)
            if parsed:
                return parsed
        legacy = legacy_flat_to_config(d)
        return [legacy] if legacy else []

    async def get_digest_config_by_id(
        self, group_id: int, config_id: str
    ) -> DigestScheduleConfig | None:
        cid = (config_id or "").strip()
        if not cid:
            return None
        for cfg in await self.get_digest_configs(group_id):
            if cfg.id == cid:
                return cfg
        return None

    async def get_digest_share_settings(self, group_id: int) -> DigestShareSettings:
        d = await self.get_typed(group_id, "digest")
        return DigestShareSettings(
            share_link_enabled=bool(d.get("share_link_enabled", True)),
        )

    async def get_digest(self, group_id: int) -> DigestScheduleConfig:
        """레거시 호환: 첫 번째 digest 설정 또는 기본값."""
        configs = await self.get_digest_configs(group_id)
        if configs:
            return configs[0]
        return DigestScheduleConfig(id="default", name="Digest 1")

    async def list_for_api(self, group_id: int, category: str) -> list[dict[str, Any]]:
        """API 응답용. 시크릿은 마스킹하여 반환."""
        rows = await self._fetch_rows(group_id, category)
        out: list[dict[str, Any]] = []
        for r in rows:
            plain = self._plain(r)
            out.append(
                {
                    "key": r.key,
                    "value": mask_secret(plain) if r.is_secret else plain,
                    "value_type": r.value_type,
                    "is_secret": r.is_secret,
                    "description": r.description,
                }
            )
        return out

    async def set_values(self, group_id: int, category: str, items: list[dict[str, Any]]) -> None:
        """카테고리 내 key들을 upsert.

        - is_secret 항목에 빈 value가 들어오면(마스킹 값 재전송 등) 기존 시크릿을 보존한다.
        """
        async with self._sf() as session:
            async with session.begin():
                existing = {
                    r.key: r for r in await self._fetch_rows_in_session(session, group_id, category)
                }
                for item in items:
                    key = item["key"]
                    is_secret = bool(item.get("is_secret", False))
                    value_type = item.get("value_type", "string")
                    raw_value = item.get("value")
                    row = existing.get(key)

                    if is_secret:
                        if raw_value in (None, ""):
                            # 시크릿 미변경: 신규면 빈 값으로 생성, 기존이면 보존
                            if row is None:
                                session.add(
                                    Setting(
                                        group_id=group_id,
                                        category=category,
                                        key=key,
                                        value=None,
                                        value_enc=None,
                                        value_type=value_type,
                                        is_secret=True,
                                        description=item.get("description"),
                                    )
                                )
                            continue
                        # 마스킹 값을 그대로 재전송한 경우 기존 시크릿을 보존한다.
                        if row is not None and row.value_enc:
                            if str(raw_value) == mask_secret(self._plain(row)):
                                continue
                        enc = self._encrypt(str(raw_value))
                        if row is None:
                            session.add(
                                Setting(
                                    group_id=group_id,
                                    category=category,
                                    key=key,
                                    value=None,
                                    value_enc=enc,
                                    value_type=value_type,
                                    is_secret=True,
                                    description=item.get("description"),
                                )
                            )
                        else:
                            row.value = None
                            row.value_enc = enc
                            row.value_type = value_type
                            row.is_secret = True
                            if item.get("description") is not None:
                                row.description = item.get("description")
                    else:
                        str_value = "" if raw_value is None else str(raw_value)
                        if row is None:
                            session.add(
                                Setting(
                                    group_id=group_id,
                                    category=category,
                                    key=key,
                                    value=str_value,
                                    value_enc=None,
                                    value_type=value_type,
                                    is_secret=False,
                                    description=item.get("description"),
                                )
                            )
                        else:
                            row.value = str_value
                            row.value_enc = None
                            row.value_type = value_type
                            row.is_secret = False
                            if item.get("description") is not None:
                                row.description = item.get("description")
        self.invalidate(group_id, category)

    async def _fetch_rows_in_session(
        self, session: AsyncSession, group_id: int, category: str
    ) -> list[Setting]:
        result = await session.execute(
            select(Setting).where(Setting.group_id == group_id, Setting.category == category)
        )
        return list(result.scalars().all())

    def _encrypt(self, plain: str) -> bytes:
        if self._fernet is None:
            raise SettingsSecretError("시크릿을 저장하려면 FERNET_KEY가 필요합니다.")
        return self._fernet.encrypt(plain.encode("utf-8"))


_manager: Optional[SettingsManager] = None


def get_settings_manager() -> SettingsManager:
    global _manager
    if _manager is None:
        _manager = SettingsManager(
            session_factory=get_sessionmaker(),
            fernet_key=app_settings.FERNET_KEY,
            cache_ttl_sec=60.0,
        )
    return _manager
