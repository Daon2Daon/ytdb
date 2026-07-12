"""공용 봇 텔레그램 연결 (Phase D-1, 스펙 §3): 토큰 발급·검증·바인딩·getUpdates 워커.

단일 소유 지점. 발송은 notify_service(resolve_notify_target)가 담당 — 여기는 연결만.
getUpdates는 단일 소비자: 단일 컨테이너 배포 전제(다중 인스턴스 시 webhook 교체,
수신부 handle_update가 분리돼 있어 교체 국소적).
"""

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.control_db import get_sessionmaker
from app.models.control.telegram_destination import TelegramDestination
from app.models.control.telegram_link_token import TelegramLinkToken
from app.services.global_settings import get_global_telegram_bot_token

LINK_TOKEN_TTL_SEC = 600
_TG_API = "https://api.telegram.org"

# getMe 캐시: 토큰 문자열 → username. 토큰이 바뀌면 키가 달라져 자동 재조회.
_bot_username_cache: dict[str, str] = {}


def build_deep_link(bot_username: str, token: str) -> str:
    return f"https://t.me/{bot_username}?start={token}"


def parse_start_command(text: str) -> Optional[str]:
    """'/start <token>' → token, '/start' → '', /start 아님 → None."""
    s = (text or "").strip()
    if not s.startswith("/start"):
        return None
    head, _, rest = s.partition(" ")
    if head not in ("/start",) and not head.startswith("/start@"):
        return None
    return rest.strip()


def _token_valid(row, now: datetime) -> bool:
    return bool(row is not None and row.used_at is None and row.expires_at > now)


async def get_bot_username(bot_token: str) -> str:
    """getMe로 봇 username 조회(캐시). 실패 시 ''(캐시 안 함 — 다음 호출에 재시도)."""
    if bot_token in _bot_username_cache:
        return _bot_username_cache[bot_token]
    try:
        async with httpx.AsyncClient(timeout=10.0) as cl:
            resp = await cl.get(f"{_TG_API}/bot{bot_token}/getMe")
            username = (resp.json().get("result") or {}).get("username") or ""
    except Exception:
        return ""
    if username:
        _bot_username_cache[bot_token] = username
    return username


async def issue_link_token(session: AsyncSession, user_id: int) -> tuple[str, datetime]:
    """1회용 토큰 발급. 같은 유저의 만료 토큰은 lazy 삭제. 커밋은 호출부 책임."""
    now = datetime.now(timezone.utc)
    await session.execute(
        delete(TelegramLinkToken).where(
            TelegramLinkToken.user_id == user_id, TelegramLinkToken.expires_at <= now
        )
    )
    token = secrets.token_urlsafe(24)
    expires = now + timedelta(seconds=LINK_TOKEN_TTL_SEC)
    session.add(TelegramLinkToken(token=token, user_id=user_id, expires_at=expires))
    return token, expires


async def consume_link_token(session: AsyncSession, token: str, chat_id: int, title: str) -> bool:
    """토큰 검증 후 destination upsert + used_at 마킹. 커밋은 호출부 책임."""
    row = await session.get(TelegramLinkToken, token)
    now = datetime.now(timezone.utc)
    if not _token_valid(row, now):
        return False
    stmt = pg_insert(TelegramDestination).values(
        user_id=row.user_id, chat_kind="private", chat_id=chat_id, title=title or None
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_telegram_destinations_user_chat",
        set_={"title": stmt.excluded.title, "is_active": True},
    )
    await session.execute(stmt)
    row.used_at = now
    return True


async def _send_bot_message(bot_token: str, chat_id: int, text: str) -> None:
    """연결 확인/실패 회신. 실패는 무시(회신은 부가 기능)."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as cl:
            await cl.post(
                f"{_TG_API}/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
            )
    except Exception:
        pass


async def _consume_in_session(token: str, chat_id: int, title: str) -> bool:
    async with get_sessionmaker()() as session:
        async with session.begin():
            return await consume_link_token(session, token, chat_id, title)


def _display_name(frm: dict) -> str:
    parts = [frm.get("first_name") or "", frm.get("last_name") or ""]
    name = " ".join(p for p in parts if p).strip()
    return name or (frm.get("username") or "")


async def handle_update(update: dict, *, bot_token: str) -> None:
    """/start <token> private 메시지만 처리. 그 외 무시 (스펙 §3)."""
    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    if chat.get("type") != "private":
        return
    token = parse_start_command(msg.get("text") or "")
    if token is None:
        return
    chat_id = chat.get("id")
    if chat_id is None:
        return
    if not token:  # 맨손 /start — 딥링크 없이 봇을 연 경우 안내만
        await _send_bot_message(
            bot_token, int(chat_id), "마이페이지의 '텔레그램 연결' 버튼으로 시작해 주세요."
        )
        return
    ok = await _consume_in_session(token, int(chat_id), _display_name(msg.get("from") or {}))
    if ok:
        await _send_bot_message(bot_token, int(chat_id), "✅ 연결 완료 — 이제 분석 알림을 받습니다.")
    else:
        await _send_bot_message(
            bot_token, int(chat_id), "링크가 만료됐습니다. 마이페이지에서 다시 연결해 주세요."
        )


async def run_telegram_updates_worker() -> None:
    """상시 long-polling 루프 (스펙 §3). 토큰 미설정이면 60초마다 재확인(idle).

    모든 예외를 삼키고 지수 백오프(최대 60s) — 워커 죽음이 앱을 못 깨뜨린다.
    """
    offset = 0
    backoff = 1
    while True:
        try:
            bot_token = await get_global_telegram_bot_token()
        except Exception:
            bot_token = ""  # DB 미구성 등 — idle로 안전 대기 (워커는 죽지 않는다)
        if not bot_token:
            await asyncio.sleep(60)
            continue
        try:
            async with httpx.AsyncClient(timeout=35.0) as cl:
                resp = await cl.get(
                    f"{_TG_API}/bot{bot_token}/getUpdates",
                    params={"offset": offset, "timeout": 25},
                )
                updates = resp.json().get("result") or []
            for u in updates:
                offset = max(offset, int(u.get("update_id", 0)) + 1)
                try:
                    await handle_update(u, bot_token=bot_token)
                except Exception as e:  # noqa: BLE001 — 개별 업데이트 실패 격리
                    print(f"[tg-worker] update 처리 실패(무시): {e}")
            backoff = 1
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — 네트워크 등 일시 장애
            print(f"[tg-worker] 폴링 실패, {backoff}s 후 재시도: {e}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
