"""전역 설정 접근자 (스펙 B-0b §5).

- get/set: app.global_settings 키-값. 시크릿 키는 FERNET_KEY로 암호화.
- resolve_youtube_key: 그룹 스코프 호출용 폴백 — 그룹 polling 키 우선, 없으면 시스템 키.
- 중앙 폴링은 폴백 없이 항상 시스템 키(get_system_youtube_key)를 쓴다.
"""

from __future__ import annotations

import json
from typing import Optional

from cryptography.fernet import InvalidToken
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.control_db import get_sessionmaker
from app.models.control.global_setting import GlobalSetting
from app.services.settings_manager import (
    SettingsSecretError,
    _as_float as _f,
    _as_int as _i,
    _fernet_from_key,
    get_settings_manager,
)

GLOBAL_YOUTUBE_API_KEY = "youtube_api_key"
GLOBAL_CENTRAL_POLL_FLOOR_MIN = "central_poll_floor_min"
DEFAULT_CENTRAL_POLL_FLOOR_MIN = 10

# Phase C: 전역 AI 게이트웨이 (스펙 §5). tagging_model은 미사용이라 전역화 제외.
GLOBAL_AI_BASE_URL = "ai_base_url"
GLOBAL_AI_API_KEY = "ai_api_key"
GLOBAL_AI_PRIMARY_MODEL = "ai_primary_model"
GLOBAL_AI_DIGEST_MODEL = "ai_digest_model"
GLOBAL_AI_MODEL_PRICES = "ai_model_prices"  # JSON: {"모델prefix": {"input": n, "output": n}} ($/1M)

# Phase D-1: 공용 텔레그램 봇 (스펙 §2)
GLOBAL_TELEGRAM_BOT_TOKEN = "telegram_bot_token"

SECRET_KEYS = frozenset({GLOBAL_YOUTUBE_API_KEY, GLOBAL_AI_API_KEY, GLOBAL_TELEGRAM_BOT_TOKEN})


def _get_fernet():
    return _fernet_from_key(app_settings.FERNET_KEY)


async def get_global(session: AsyncSession, key: str) -> Optional[str]:
    row = (
        await session.execute(select(GlobalSetting).where(GlobalSetting.key == key))
    ).scalar_one_or_none()
    if row is None:
        return None
    if row.is_secret:
        if row.value_enc is None:
            return None
        fernet = _get_fernet()
        if fernet is None:
            return None  # 키 없이는 복호 불가 — 미설정과 동일 취급
        try:
            return fernet.decrypt(row.value_enc).decode("utf-8")
        except InvalidToken as e:
            raise SettingsSecretError(f"전역 설정 복호화 실패: {key}") from e
    return row.value


async def set_global(session: AsyncSession, key: str, value: str) -> None:
    """키-값 upsert. SECRET_KEYS는 암호화 저장. 커밋은 호출부 책임."""
    is_secret = key in SECRET_KEYS
    if is_secret:
        fernet = _get_fernet()
        if fernet is None:
            raise SettingsSecretError("시크릿을 저장하려면 FERNET_KEY가 필요합니다.")
        values = {"key": key, "value": None, "value_enc": fernet.encrypt(value.encode("utf-8")), "is_secret": True}
    else:
        values = {"key": key, "value": value, "value_enc": None, "is_secret": False}
    stmt = pg_insert(GlobalSetting).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[GlobalSetting.key],
        set_={"value": stmt.excluded.value, "value_enc": stmt.excluded.value_enc, "is_secret": stmt.excluded.is_secret},
    )
    await session.execute(stmt)


async def get_central_poll_floor_min(session: AsyncSession) -> int:
    raw = await get_global(session, GLOBAL_CENTRAL_POLL_FLOOR_MIN)
    try:
        v = int(raw) if raw is not None else DEFAULT_CENTRAL_POLL_FLOOR_MIN
    except (TypeError, ValueError):
        return DEFAULT_CENTRAL_POLL_FLOOR_MIN
    return v if v > 0 else DEFAULT_CENTRAL_POLL_FLOOR_MIN


async def get_system_youtube_key() -> str:
    """자체 세션으로 시스템 YouTube 키를 읽는다. 미설정이면 ''."""
    async with get_sessionmaker()() as session:
        return (await get_global(session, GLOBAL_YOUTUBE_API_KEY)) or ""


async def resolve_youtube_key(group_id: int) -> str:
    """그룹 스코프 호출용: 그룹 polling 키 우선, 없으면 시스템 키. 둘 다 없으면 ''."""
    polling = await get_settings_manager().get_polling(group_id)
    if polling.youtube_api_key:
        return polling.youtube_api_key
    return await get_system_youtube_key()


async def pick_bootstrap_youtube_key(groups, get_polling) -> Optional[str]:
    """후보 그룹들(호출자가 admin 소유·group_id 순으로 전달) 중 첫 polling 키."""
    for group in groups:
        polling = await get_polling(group.group_id)
        if polling.youtube_api_key:
            return polling.youtube_api_key
    return None


async def bootstrap_global_settings() -> None:
    """시스템 YouTube 키 미설정 시 admin 소유 그룹 키로 1회 시드. 멱등 (스펙 §6).

    Phase A의 AUTH_PASSWORD 부트스트랩과 같은 철학 — 기존 단일 운영자 배포가
    업그레이드 직후에도 설정 변경 없이 폴링 무중단.
    """
    from app.models.control.group import Group
    from app.models.control.user import User

    # 전역 AI 게이트웨이·공용 텔레그램 봇 토큰 시드는 YouTube 키의 early-return과
    # 무관하게 항상 먼저 실행.
    await _seed_global_ai_from_admin_groups()
    await _seed_telegram_bot_token()

    sf = get_sessionmaker()
    async with sf() as session:
        existing = await get_global(session, GLOBAL_YOUTUBE_API_KEY)
        if existing:
            return
        groups = list(
            (
                await session.execute(
                    select(Group)
                    .join(User, User.user_id == Group.owner_user_id)
                    .where(User.role == "admin", Group.is_active.is_(True))
                    .order_by(Group.group_id)
                )
            ).scalars().all()
        )
    key = await pick_bootstrap_youtube_key(
        groups, get_settings_manager().get_polling
    )
    if not key:
        return
    try:
        async with sf() as session:
            async with session.begin():
                await set_global(session, GLOBAL_YOUTUBE_API_KEY, key)
        print("[bootstrap] 시스템 YouTube 키를 admin 그룹 키로 시드했습니다.")
    except SettingsSecretError as e:
        # FERNET_KEY 없는 배포 — 부팅을 막지 않는다. 폴링은 그룹 키 폴백으로 계속
        # 동작하고, 시스템 키는 관리자가 FERNET_KEY 설정 후 API로 넣으면 된다.
        print(f"[bootstrap] 시스템 키 시드 건너뜀({e}) — FERNET_KEY 설정 후 관리자 API로 등록하세요.")


def _parse_model_prices(raw: Optional[str]) -> dict:
    """단가표 JSON 파싱. 형식 오류는 빈 dict(=단가 없음, cost NULL 경고로 표면화)."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def get_ai_model_prices() -> dict:
    async with get_sessionmaker()() as session:
        raw = await get_global(session, GLOBAL_AI_MODEL_PRICES)
    return _parse_model_prices(raw)


async def resolve_ai_gateway(group_id: int) -> "AIGatewaySettings":
    """유효 AI 게이트웨이 해석: 그룹 명시값 → 전역 → 코드 기본값 (스펙 §5).

    settings_manager.get_ai_gateway와 달리 raw(get_typed)로 그룹 '명시 여부'를
    판별한다 — get_ai_gateway는 기본값을 채워 반환하므로 폴백 판단이 불가능.
    """
    from app.services.settings_types import AIGatewaySettings

    d = await get_settings_manager().get_typed(group_id, "ai_gateway")
    async with get_sessionmaker()() as session:
        g_base = await get_global(session, GLOBAL_AI_BASE_URL)
        g_key = await get_global(session, GLOBAL_AI_API_KEY)
        g_primary = await get_global(session, GLOBAL_AI_PRIMARY_MODEL)
        g_digest = await get_global(session, GLOBAL_AI_DIGEST_MODEL)

    def pick(group_val, global_val, default: str) -> str:
        v = str(group_val or "").strip()
        if v:
            return v
        v = (global_val or "").strip()
        return v if v else default

    return AIGatewaySettings(
        base_url=pick(d.get("base_url"), g_base, "http://litellm:4000"),
        api_key=pick(d.get("api_key"), g_key, ""),
        primary_model=pick(d.get("primary_model"), g_primary, "gemini/gemini-2.5-flash"),
        tagging_model=str(d.get("tagging_model") or "gemini/gemini-2.5-flash"),
        digest_model=pick(d.get("digest_model"), g_digest, ""),
        temperature=_f(d.get("temperature"), 0.3),
        max_tokens=_i(d.get("max_tokens"), 8192),
        daily_budget_usd=_f(d.get("daily_budget_usd"), 2.0),
    )


async def _seed_global_ai_from_admin_groups() -> None:
    """전역 AI 게이트웨이 미시드 시 admin 그룹 설정에서 1회 시드. 멱등.

    bootstrap_global_settings의 YouTube 키 시드와 같은 철학 — 기존 단일 운영자
    배포가 업그레이드 직후 설정 변경 없이 동작. 단가표는 시드하지 않음(관리자 입력).
    """
    from app.models.control.group import Group
    from app.models.control.user import User

    sf = get_sessionmaker()
    async with sf() as session:
        if await get_global(session, GLOBAL_AI_BASE_URL) or await get_global(
            session, GLOBAL_AI_API_KEY
        ):
            return
        groups = list(
            (
                await session.execute(
                    select(Group)
                    .join(User, User.user_id == Group.owner_user_id)
                    .where(User.role == "admin", Group.is_active.is_(True))
                    .order_by(Group.group_id)
                )
            ).scalars().all()
        )
    for group in groups:
        d = await get_settings_manager().get_typed(group.group_id, "ai_gateway")
        base = str(d.get("base_url") or "").strip()
        key = str(d.get("api_key") or "").strip()
        if not (base and key):
            continue
        try:
            async with sf() as session:
                async with session.begin():
                    await set_global(session, GLOBAL_AI_BASE_URL, base)
                    await set_global(session, GLOBAL_AI_API_KEY, key)
                    primary = str(d.get("primary_model") or "").strip()
                    digest = str(d.get("digest_model") or "").strip()
                    if primary:
                        await set_global(session, GLOBAL_AI_PRIMARY_MODEL, primary)
                    if digest:
                        await set_global(session, GLOBAL_AI_DIGEST_MODEL, digest)
            print(f"[bootstrap] 전역 AI 게이트웨이를 그룹 {group.slug} 설정에서 시드했습니다.")
        except SettingsSecretError as e:
            print(f"[bootstrap] 전역 AI 키 시드 건너뜀({e}) — FERNET_KEY 설정 후 관리자 API로 등록하세요.")
        return


async def get_global_telegram_bot_token() -> str:
    """자체 세션으로 공용 봇 토큰을 읽는다. 미설정이면 ''."""
    async with get_sessionmaker()() as session:
        return (await get_global(session, GLOBAL_TELEGRAM_BOT_TOKEN)) or ""


async def _seed_telegram_bot_token() -> None:
    """env DEFAULT_TELEGRAM_BOT_TOKEN이 있고 전역 미시드면 1회 시드. 멱등 (스펙 §2)."""
    from app.config import settings as app_cfg

    env_token = (app_cfg.DEFAULT_TELEGRAM_BOT_TOKEN or "").strip()
    if not env_token:
        return
    sf = get_sessionmaker()
    async with sf() as session:
        if await get_global(session, GLOBAL_TELEGRAM_BOT_TOKEN):
            return
    try:
        async with sf() as session:
            async with session.begin():
                await set_global(session, GLOBAL_TELEGRAM_BOT_TOKEN, env_token)
        print("[bootstrap] 공용 텔레그램 봇 토큰을 env에서 시드했습니다.")
    except SettingsSecretError as e:
        print(f"[bootstrap] 봇 토큰 시드 건너뜀({e}) — FERNET_KEY 설정 후 관리자 API로 등록하세요.")
