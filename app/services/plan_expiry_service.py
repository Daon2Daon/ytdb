"""유료 플랜 만료 관리 (스펙 E-1 §2).

- 강등: 만료된 비기본·비unlimited 사용자 → 기본(is_default) 플랜으로 UPDATE.
  DB의 plan_id가 단일 진실 — quota_service·관리자 화면·마이페이지 자동 반영.
  plan_expires_at은 이력으로 보존(강등되면 기본 플랜이라 후보에서 제외 = 자연 멱등).
- 임박 알림(D-7): plan_expiry_notified_at NULL 가드로 1회만. 발송 전에 마킹 —
  발송 실패해도 재발송 폭주 없음(마이페이지 표시가 폴백).
- 알림은 공용 봇의 첫 active destination으로 best-effort — 실패가 강등을 안 막는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.control_db import get_sessionmaker
from app.models.control.plan import Plan
from app.models.control.telegram_destination import TelegramDestination
from app.models.control.user import User

NOTIFY_BEFORE_DAYS = 7


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ExpiryCandidate:
    user_id: int
    email: str
    plan_expires_at: datetime
    plan_expiry_notified_at: datetime | None


def classify(cand: ExpiryCandidate, now: datetime) -> str:
    """'demote' | 'notify' | 'none'. 경계: 만료 시각 지남=강등, 7일 이내(포함)=임박."""
    if cand.plan_expires_at < now:
        return "demote"
    if (
        cand.plan_expires_at <= now + timedelta(days=NOTIFY_BEFORE_DAYS)
        and cand.plan_expiry_notified_at is None
    ):
        return "notify"
    return "none"


async def _load_candidates() -> list[ExpiryCandidate]:
    """만료일이 설정된 비기본·비unlimited 플랜 사용자."""
    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(
                    User.user_id, User.email, User.plan_expires_at, User.plan_expiry_notified_at
                )
                .join(Plan, Plan.plan_id == User.plan_id)
                .where(
                    Plan.is_default.is_(False),
                    Plan.slug != "unlimited",
                    User.plan_expires_at.isnot(None),
                )
            )
        ).all()
    return [ExpiryCandidate(*r) for r in rows]


async def _demote_user(user_id: int) -> None:
    """기본 플랜으로 강등. plan_expires_at은 이력으로 보존."""
    async with get_sessionmaker()() as session:
        async with session.begin():
            default_id = (
                await session.execute(select(Plan.plan_id).where(Plan.is_default.is_(True)))
            ).scalar_one()
            await session.execute(
                update(User).where(User.user_id == user_id).values(plan_id=default_id)
            )


async def _mark_notified(user_id: int) -> None:
    async with get_sessionmaker()() as session:
        async with session.begin():
            await session.execute(
                update(User)
                .where(User.user_id == user_id)
                .values(plan_expiry_notified_at=_now())
            )


async def _send_user_telegram(user_id: int, text: str) -> None:
    """사용자 첫 active destination으로 발송. 미연결이면 무시. 호출부가 예외를 삼킨다."""
    from app.services.global_settings import get_global_telegram_bot_token
    from app.services.telegram_link_service import _send_bot_message

    bot_token = await get_global_telegram_bot_token()
    if not bot_token:
        return
    async with get_sessionmaker()() as session:
        chat_id = (
            await session.execute(
                select(TelegramDestination.chat_id)
                .where(
                    TelegramDestination.user_id == user_id,
                    TelegramDestination.is_active.is_(True),
                )
                .order_by(TelegramDestination.dest_id)
                .limit(1)
            )
        ).scalar_one_or_none()
    if chat_id is None:
        return
    await _send_bot_message(bot_token, chat_id, text)


async def run_plan_expiry_once() -> None:
    """만료 틱: 강등 → 강등 알림, 임박 → 마킹 → 알림. 사용자별 실패 격리."""
    now = _now()
    for cand in await _load_candidates():
        action = classify(cand, now)
        if action == "none":
            continue
        try:
            if action == "demote":
                await _demote_user(cand.user_id)
                print(f"[plan-expiry] {cand.email} 플랜 만료 → 기본 플랜 강등")
                try:
                    await _send_user_telegram(
                        cand.user_id,
                        "이용 중인 플랜이 만료되어 Free로 전환되었습니다. "
                        "연장을 원하시면 관리자에게 문의해 주세요.",
                    )
                except Exception:
                    pass  # 알림 실패가 강등을 못 막는다 (스펙 §2)
            else:  # notify
                await _mark_notified(cand.user_id)
                d = cand.plan_expires_at.astimezone(timezone.utc).date().isoformat()
                try:
                    await _send_user_telegram(
                        cand.user_id,
                        f"이용 중인 플랜이 {d}에 만료될 예정입니다. "
                        "연장을 원하시면 관리자에게 문의해 주세요.",
                    )
                except Exception:
                    pass
        except Exception as e:  # noqa: BLE001 — 사용자 단위 격리
            print(f"[plan-expiry] {cand.email} 처리 실패(계속): {e}")
