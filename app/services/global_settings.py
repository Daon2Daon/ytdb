"""전역 설정 접근자 (스펙 B-0b §5).

- get/set: app.global_settings 키-값. 시크릿 키는 FERNET_KEY로 암호화.
- resolve_youtube_key: 그룹 스코프 호출용 폴백 — 그룹 polling 키 우선, 없으면 시스템 키.
- 중앙 폴링은 폴백 없이 항상 시스템 키(get_system_youtube_key)를 쓴다.
"""

from __future__ import annotations

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
    _fernet_from_key,
    get_settings_manager,
)

GLOBAL_YOUTUBE_API_KEY = "youtube_api_key"
GLOBAL_CENTRAL_POLL_FLOOR_MIN = "central_poll_floor_min"
DEFAULT_CENTRAL_POLL_FLOOR_MIN = 10

SECRET_KEYS = frozenset({GLOBAL_YOUTUBE_API_KEY})


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
