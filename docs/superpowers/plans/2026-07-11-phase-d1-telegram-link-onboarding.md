# Phase D-1: 공용 봇 텔레그램 연결 + 온보딩 체크리스트 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 일반 사용자가 봇 토큰/chat_id 개념 없이 딥링크 두 번 클릭으로 텔레그램 알림을 연결하고, 신규 사용자가 체크리스트를 따라 가입→그룹→채널→알림 수신까지 UI만으로 완주하게 한다.

**Architecture:** getUpdates long-polling 워커(asyncio 상시 태스크)가 `/start <토큰>`을 수신해 `telegram_destinations`에 바인딩. 발송은 `resolve_notify_target` 3단계 해석(그룹 직접설정→dest_id→owner 첫 destination)이 기존 `NotificationSettings`의 bot_token/chat_ids를 **채워서 반환**하므로 기존 발송 코드(is_sendable·chat 루프)는 전부 무변경. 프로덕션 그룹(직접 설정)은 1순위라 동작 불변.

**Tech Stack:** FastAPI + SQLAlchemy async(제어 평면 `app` 스키마), httpx(Telegram Bot API), pytest(asyncio auto), React+TS(vite).

**설계 문서:** `docs/superpowers/specs/2026-07-11-phase-d1-telegram-link-onboarding-design.md`

**중요 배경 (엔지니어 필독):**
- 제어 평면 모델 `app/models/control/`, 부팅 `ensure_control_schema()` create_all(마이그레이션 없음). ORM 기본값은 반드시 `server_default`.
- 전체 테스트: `.venv_e2e/bin/python -m pytest tests/ -q` (main `.venv` 깨져 있음 — 금지). 기준 **279 passed**. 프런트: `cd frontend && npm run test -- --run && npm run build` (기준 30).
- `postgres-ytdb` MCP는 프로덕션 — 절대 금지.
- 발송 경로 현황: `notif.is_sendable`(= enabled && bot_token && chat_ids) 게이트 5곳, `notif.bot_token/chat_ids` 직접 사용 4곳. **이들을 바꾸지 않는다** — resolve가 notif를 변환해 반환하는 설계.
- 기존 `backfill_notify_baselines()`(notify_service.py:643, main.py:50 부팅 호출)가 "sendable인데 baseline 없는 그룹" 스탬프 담당. dest 연결로 sendable해지는 경우도 이 루프를 resolve로 감싸면 커버되지만 부팅 시에만 돌므로, **신규 그룹은 생성 시점에 baseline을 시드**(Task 7)해 런타임 갭을 없앤다(설계 보완 — 신규 그룹은 생성 이후 게시 영상만 발송, backlog flood 방지 취지 유지).

---

### Task 1: 제어평면 모델 2개 (telegram_destinations / telegram_link_tokens)

**Files:**
- Create: `app/models/control/telegram_destination.py`, `app/models/control/telegram_link_token.py`
- Modify: `app/control_db.py` (`ensure_control_schema` 임포트 목록)
- Test: `tests/test_control_models.py` (추가)

- [ ] **Step 1: 실패하는 테스트 작성 (tests/test_control_models.py 끝에)**

```python
def test_telegram_destination_model():
    from app.models.control.telegram_destination import TelegramDestination
    from sqlalchemy import UniqueConstraint

    cols = {c.name for c in TelegramDestination.__table__.columns}
    assert cols == {"dest_id", "user_id", "chat_kind", "chat_id", "title", "is_active", "linked_at"}
    uqs = [c for c in TelegramDestination.__table__.constraints if isinstance(c, UniqueConstraint)]
    assert any({col.name for col in uq.columns} == {"user_id", "chat_id"} for uq in uqs)


def test_telegram_link_token_model():
    from app.models.control.telegram_link_token import TelegramLinkToken

    cols = {c.name for c in TelegramLinkToken.__table__.columns}
    assert cols == {"token", "user_id", "expires_at", "used_at"}
    assert TelegramLinkToken.__table__.columns["used_at"].nullable is True
```

- [ ] **Step 2: 실패 확인** — Run: `.venv_e2e/bin/python -m pytest tests/test_control_models.py -q` → FAIL(ModuleNotFoundError)

- [ ] **Step 3: 모델 작성**

`app/models/control/telegram_destination.py`:

```python
"""app.telegram_destinations — 공용 봇 연결 대상 (스펙 §2.7, D-1은 private만).

사용자당 여러 destination 가능(재연결은 UNIQUE(user_id, chat_id) upsert).
chat_kind는 그룹채팅방 확장 대비 컬럼만 선반영('private' 고정).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class TelegramDestination(Base):
    __tablename__ = "telegram_destinations"
    __table_args__ = (
        UniqueConstraint("user_id", "chat_id", name="uq_telegram_destinations_user_chat"),
        {"schema": APP_SCHEMA},
    )

    dest_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{APP_SCHEMA}.users.user_id", ondelete="CASCADE"), nullable=False
    )
    chat_kind: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'private'"))
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)  # DM: 텔레그램 표시 이름
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

`app/models/control/telegram_link_token.py`:

```python
"""app.telegram_link_tokens — 딥링크 1회용 연결 토큰 (TTL 10분, 스펙 §1).

DB 저장이라 앱 재시작에도 유효. 만료 토큰은 발급 시 lazy 삭제.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.control_db import APP_SCHEMA, Base


class TelegramLinkToken(Base):
    __tablename__ = "telegram_link_tokens"
    __table_args__ = {"schema": APP_SCHEMA}

    token: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{APP_SCHEMA}.users.user_id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 4: control_db 임포트 추가** — `ensure_control_schema`의 `from app.models.control import (...)` 목록에 `telegram_destination`, `telegram_link_token` 추가(알파벳 순 — `setting` 뒤, `user` 앞).

- [ ] **Step 5: 통과 확인** — Run: `.venv_e2e/bin/python -m pytest tests/test_control_models.py -q` → all passed

- [ ] **Step 6: Commit**

```bash
git add app/models/control/telegram_destination.py app/models/control/telegram_link_token.py app/control_db.py tests/test_control_models.py
git commit -m "feat: telegram_destinations·telegram_link_tokens 테이블 — 공용 봇 연결 기반"
```

---

### Task 2: 전역 봇 토큰 키 + env 시드 + admin 노출

**Files:**
- Modify: `app/services/global_settings.py` (키·SECRET_KEYS·시드·조회 헬퍼)
- Modify: `app/routers/admin.py` (`_GLOBAL_KEYS`·임포트)
- Test: `tests/test_global_settings.py`, `tests/test_admin_api.py` (추가)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_global_settings.py` 끝에:

```python
def test_telegram_bot_token_key_registered():
    from app.services.global_settings import GLOBAL_TELEGRAM_BOT_TOKEN, SECRET_KEYS

    assert GLOBAL_TELEGRAM_BOT_TOKEN == "telegram_bot_token"
    assert GLOBAL_TELEGRAM_BOT_TOKEN in SECRET_KEYS  # Fernet 암호화 저장
```

`tests/test_admin_api.py` 끝에:

```python
def test_global_settings_includes_telegram_bot_token():
    from app.routers.admin import _GLOBAL_KEYS

    assert "telegram_bot_token" in _GLOBAL_KEYS
```

- [ ] **Step 2: 실패 확인** — Run: `.venv_e2e/bin/python -m pytest tests/test_global_settings.py tests/test_admin_api.py -q` → 신규 FAIL

- [ ] **Step 3: global_settings.py 구현**

키 상수 블록에 추가하고 SECRET_KEYS 교체:

```python
# Phase D-1: 공용 텔레그램 봇 (스펙 §2)
GLOBAL_TELEGRAM_BOT_TOKEN = "telegram_bot_token"

SECRET_KEYS = frozenset({GLOBAL_YOUTUBE_API_KEY, GLOBAL_AI_API_KEY, GLOBAL_TELEGRAM_BOT_TOKEN})
```

파일 끝에 조회 헬퍼·시드 추가(`get_system_youtube_key` 패턴):

```python
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
```

`bootstrap_global_settings()` 본문 맨 앞(기존 `_seed_global_ai_from_admin_groups()` 호출 옆)에 `await _seed_telegram_bot_token()` 추가. (config에 `DEFAULT_TELEGRAM_BOT_TOKEN` 필드는 이미 존재 — app/config.py:18, 현재 dead. 확인만.)

- [ ] **Step 4: admin.py** — `_GLOBAL_KEYS` 튜플에 `GLOBAL_TELEGRAM_BOT_TOKEN` 추가 + `from app.services.global_settings import ...` 임포트에 추가. (SECRET_KEYS 기반 마스킹 가드는 자동 적용 — 코드 불필요.)

- [ ] **Step 5: 통과 확인** — Run: `.venv_e2e/bin/python -m pytest tests/test_global_settings.py tests/test_admin_api.py -q` → all passed

- [ ] **Step 6: Commit**

```bash
git add app/services/global_settings.py app/routers/admin.py tests/test_global_settings.py tests/test_admin_api.py
git commit -m "feat: 전역 공용 봇 토큰 키(telegram_bot_token) + env 멱등 시드 + 관리자 노출"
```

---

### Task 3: telegram_link_service — 토큰 발급·검증·바인딩·getMe 캐시

**Files:**
- Create: `app/services/telegram_link_service.py`
- Test: `tests/test_telegram_link_service.py` (신규)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_telegram_link_service.py`:

```python
"""텔레그램 연결 서비스 단위 테스트 (DB·네트워크 불필요 부분)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.telegram_link_service import (
    LINK_TOKEN_TTL_SEC,
    _token_valid,
    build_deep_link,
    parse_start_command,
)


def test_build_deep_link():
    assert build_deep_link("my_bot", "abc123") == "https://t.me/my_bot?start=abc123"


def test_parse_start_command():
    assert parse_start_command("/start abc123") == "abc123"
    assert parse_start_command("/start   abc123  ") == "abc123"
    assert parse_start_command("/start") == ""          # 맨손 /start
    assert parse_start_command("/start@my_bot tok") == "tok"  # 그룹형 접미 허용
    assert parse_start_command("hello") is None          # /start 아님
    assert parse_start_command("") is None


def test_token_valid():
    now = datetime.now(timezone.utc)
    ok = SimpleNamespace(used_at=None, expires_at=now + timedelta(minutes=5))
    used = SimpleNamespace(used_at=now, expires_at=now + timedelta(minutes=5))
    expired = SimpleNamespace(used_at=None, expires_at=now - timedelta(seconds=1))
    assert _token_valid(ok, now) is True
    assert _token_valid(used, now) is False
    assert _token_valid(expired, now) is False
    assert _token_valid(None, now) is False


def test_ttl_constant():
    assert LINK_TOKEN_TTL_SEC == 600
```

- [ ] **Step 2: 실패 확인** — Run: `.venv_e2e/bin/python -m pytest tests/test_telegram_link_service.py -q` → FAIL

- [ ] **Step 3: 구현** — `app/services/telegram_link_service.py`:

```python
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
from sqlalchemy import delete, select
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
```

- [ ] **Step 4: 통과 확인** — Run: `.venv_e2e/bin/python -m pytest tests/test_telegram_link_service.py -q` → 4 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/telegram_link_service.py tests/test_telegram_link_service.py
git commit -m "feat: telegram_link_service 기초 — 딥링크·/start 파싱·토큰 발급/검증/바인딩·getMe 캐시"
```

---

### Task 4: handle_update + getUpdates 워커 + lifespan 기동

**Files:**
- Modify: `app/services/telegram_link_service.py` (추가)
- Modify: `app/main.py` (lifespan)
- Test: `tests/test_telegram_link_service.py` (추가)

- [ ] **Step 1: 실패하는 테스트 작성 (test_telegram_link_service.py 끝에)**

```python
async def test_handle_update_binds_on_valid_start(monkeypatch):
    from app.services import telegram_link_service as tls

    calls = {}

    async def _fake_consume_in_session(token, chat_id, title):
        calls.update(token=token, chat_id=chat_id, title=title)
        return True

    sent = {}

    async def _fake_send(bot_token, chat_id, text):
        sent.update(chat_id=chat_id, text=text)

    monkeypatch.setattr(tls, "_consume_in_session", _fake_consume_in_session)
    monkeypatch.setattr(tls, "_send_bot_message", _fake_send)

    update = {"message": {
        "chat": {"id": 12345, "type": "private"},
        "from": {"first_name": "길동", "last_name": "홍", "username": "gildong"},
        "text": "/start tok123",
    }}
    await tls.handle_update(update, bot_token="BT")
    assert calls == {"token": "tok123", "chat_id": 12345, "title": "길동 홍"}
    assert "연결 완료" in sent["text"]


async def test_handle_update_ignores_non_private_and_non_start(monkeypatch):
    from app.services import telegram_link_service as tls

    async def _boom(*a, **k):
        raise AssertionError("호출되면 안 됨")

    monkeypatch.setattr(tls, "_consume_in_session", _boom)
    monkeypatch.setattr(tls, "_send_bot_message", _boom)

    await tls.handle_update({"message": {"chat": {"id": 1, "type": "group"}, "text": "/start t"}}, bot_token="BT")
    await tls.handle_update({"message": {"chat": {"id": 1, "type": "private"}, "text": "안녕"}}, bot_token="BT")
    await tls.handle_update({"edited_message": {}}, bot_token="BT")


async def test_handle_update_replies_on_invalid_token(monkeypatch):
    from app.services import telegram_link_service as tls

    async def _fail(token, chat_id, title):
        return False

    sent = {}

    async def _fake_send(bot_token, chat_id, text):
        sent.update(text=text)

    monkeypatch.setattr(tls, "_consume_in_session", _fail)
    monkeypatch.setattr(tls, "_send_bot_message", _fake_send)
    await tls.handle_update({"message": {
        "chat": {"id": 9, "type": "private"}, "from": {"first_name": "x"}, "text": "/start bad",
    }}, bot_token="BT")
    assert "만료" in sent["text"]
```

- [ ] **Step 2: 실패 확인** — Run: `.venv_e2e/bin/python -m pytest tests/test_telegram_link_service.py -q` → 신규 FAIL

- [ ] **Step 3: 구현 (telegram_link_service.py 끝에 추가)**

```python
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
        bot_token = await get_global_telegram_bot_token()
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
```


- [ ] **Step 4: main.py lifespan 기동** — 기존 lifespan(`app/main.py:42`)의 구조를 읽고, `yield` 앞에 워커 기동·`finally`에 종료 추가:

```python
    from contextlib import suppress

    from app.services.telegram_link_service import run_telegram_updates_worker

    tg_task = asyncio.create_task(run_telegram_updates_worker())
    try:
        yield
    finally:
        tg_task.cancel()
        with suppress(asyncio.CancelledError):
            await tg_task
        shutdown_scheduler()
```

(기존 `try: yield finally: shutdown_scheduler()` 블록을 위 형태로 확장. `import asyncio`
상단 추가. 기존 부팅 시퀀스(ensure→bootstrap_auth→backfill→bootstrap_global_settings→
backfill_channel_registry→scheduler)는 그대로 두고 워커 기동은 scheduler 시작 직후.)

- [ ] **Step 5: 통과 확인 + 앱 임포트 리그레션** — Run: `.venv_e2e/bin/python -m pytest tests/test_telegram_link_service.py tests/ -q` → all passed (전체 스위트로 main.py 변경 무해 확인)

- [ ] **Step 6: Commit**

```bash
git add app/services/telegram_link_service.py app/main.py tests/test_telegram_link_service.py
git commit -m "feat: getUpdates long-polling 워커 — /start 바인딩·회신·백오프·lifespan 기동"
```

---

### Task 5: me 텔레그램 API 3종 (link-token / destinations 목록·삭제)

**Files:**
- Modify: `app/schemas/auth.py` (스키마), `app/routers/auth.py` (me_router)
- Test: `tests/test_me_telegram.py` (신규)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_me_telegram.py`:

```python
"""GET/POST/DELETE /api/me/telegram/* — 연결 토큰·destination 관리."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.auth import CurrentUser, require_user
from app.services.auth_service import set_users_exist

USER = CurrentUser(user_id=2, email="u@x.com", display_name="U", role="user")


@pytest.fixture(autouse=True)
def _cleanup():
    set_users_exist(True)
    yield
    set_users_exist(False)
    app.dependency_overrides.clear()


def _as_user():
    async def _u():
        return USER
    app.dependency_overrides[require_user] = _u


def test_routes_registered():
    paths = {r.path for r in app.routes}
    assert "/api/me/telegram/link-token" in paths
    assert "/api/me/telegram/destinations" in paths
    assert "/api/me/telegram/destinations/{dest_id}" in paths


def test_link_token_400_when_bot_unset(monkeypatch):
    _as_user()

    async def _no_token():
        return ""

    monkeypatch.setattr("app.routers.auth.get_global_telegram_bot_token", _no_token)
    c = TestClient(app, raise_server_exceptions=False)
    resp = c.post("/api/me/telegram/link-token")
    assert resp.status_code == 400
    assert "공용 봇" in resp.json()["detail"]


def test_link_token_returns_deep_link(monkeypatch):
    _as_user()

    async def _token():
        return "BT"

    async def _username(bot_token):
        return "my_bot"

    async def _issue(session, user_id):
        from datetime import datetime, timezone
        return "tok123", datetime.now(timezone.utc)

    monkeypatch.setattr("app.routers.auth.get_global_telegram_bot_token", _token)
    monkeypatch.setattr("app.routers.auth.get_bot_username", _username)
    monkeypatch.setattr("app.routers.auth.issue_link_token", _issue)
    c = TestClient(app, raise_server_exceptions=False)
    data = c.post("/api/me/telegram/link-token").json()
    assert data["deep_link"] == "https://t.me/my_bot?start=tok123"
    assert data["expires_in_sec"] == 600
```

- [ ] **Step 2: 실패 확인** — Run: `.venv_e2e/bin/python -m pytest tests/test_me_telegram.py -q` → FAIL(라우트 미등록)

- [ ] **Step 3: 스키마 — app/schemas/auth.py 끝에**

```python
class TelegramLinkResponse(BaseModel):
    deep_link: str
    expires_in_sec: int


class TelegramDestinationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    dest_id: int
    chat_kind: str
    title: Optional[str] = None
    is_active: bool
    linked_at: datetime
```

(`ConfigDict`/`datetime` 임포트 확인 — 파일에 이미 있으면 재사용. **chat_id는 비노출** — 스펙 §4.)

- [ ] **Step 4: 라우터 — app/routers/auth.py 끝(me_router 영역)에**

임포트 추가:

```python
from app.models.control.telegram_destination import TelegramDestination
from app.schemas.auth import TelegramDestinationOut, TelegramLinkResponse
from app.services.global_settings import get_global_telegram_bot_token
from app.services.telegram_link_service import (
    LINK_TOKEN_TTL_SEC,
    build_deep_link,
    get_bot_username,
    issue_link_token,
)
```

```python
@me_router.post("/telegram/link-token", response_model=TelegramLinkResponse)
async def create_telegram_link_token(
    user: CurrentUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> TelegramLinkResponse:
    bot_token = await get_global_telegram_bot_token()
    if not bot_token:
        raise HTTPException(status_code=400, detail="관리자가 공용 봇을 설정해야 합니다.")
    username = await get_bot_username(bot_token)
    if not username:
        raise HTTPException(status_code=400, detail="봇 정보 조회에 실패했습니다. 잠시 후 다시 시도하세요.")
    token, _ = await issue_link_token(session, user.user_id)
    await session.commit()
    return TelegramLinkResponse(
        deep_link=build_deep_link(username, token), expires_in_sec=LINK_TOKEN_TTL_SEC
    )


@me_router.get("/telegram/destinations", response_model=list[TelegramDestinationOut])
async def list_telegram_destinations(
    user: CurrentUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> list[TelegramDestination]:
    rows = (
        await session.execute(
            select(TelegramDestination)
            .where(TelegramDestination.user_id == user.user_id)
            .order_by(TelegramDestination.dest_id)
        )
    ).scalars().all()
    return list(rows)


@me_router.delete("/telegram/destinations/{dest_id}", status_code=204)
async def delete_telegram_destination(
    dest_id: int,
    user: CurrentUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    dest = await session.get(TelegramDestination, dest_id)
    if dest is None or dest.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="연결을 찾을 수 없습니다.")
    await session.delete(dest)
    await session.commit()
```

(`select`/`HTTPException`/`Depends`/`AsyncSession`/`get_session` 기존 임포트 확인.)

- [ ] **Step 5: 통과 확인** — Run: `.venv_e2e/bin/python -m pytest tests/test_me_telegram.py tests/test_me_usage.py tests/test_auth.py -q` → all passed

- [ ] **Step 6: Commit**

```bash
git add app/schemas/auth.py app/routers/auth.py tests/test_me_telegram.py
git commit -m "feat: 마이페이지 텔레그램 연결 API — 딥링크 발급·destination 목록/해제"
```

---

### Task 6: NotificationSettings.dest_id + 설정 PUT 검증

**Files:**
- Modify: `app/services/settings_types.py` (dest_id 필드), `app/services/settings_manager.py` (get_notification 로드)
- Modify: `app/routers/settings.py` (notification PUT dest_id 소유 검증)
- Test: `tests/test_notification_settings_defaults.py` 또는 신규 `tests/test_dest_id_settings.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_dest_id_settings.py`:

```python
"""notification dest_id 설정 로드·PUT 검증 (설계 §5)."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.auth import CurrentUser, require_user
from app.routers.deps import get_group_or_404
from app.services.auth_service import set_users_exist
from app.services.settings_types import NotificationSettings

USER = CurrentUser(user_id=2, email="u@x.com", display_name="U", role="user")


class _FakeGroup:
    group_id = 10
    slug = "g1"
    owner_user_id = 2
    schema_name = "youtube_g1"


@pytest.fixture(autouse=True)
def _cleanup():
    set_users_exist(True)
    yield
    set_users_exist(False)
    app.dependency_overrides.clear()


def test_notification_settings_has_dest_id_default_none():
    assert NotificationSettings().dest_id is None


def test_put_dest_id_invalid_ownership_400(monkeypatch):
    async def _u():
        return USER
    async def _g():
        return _FakeGroup()
    app.dependency_overrides[require_user] = _u
    app.dependency_overrides[get_group_or_404] = _g

    async def _not_owned(dest_id, owner_user_id):
        return False

    monkeypatch.setattr("app.routers.settings._dest_owned_and_active", _not_owned)
    c = TestClient(app, raise_server_exceptions=False)
    resp = c.put(
        "/api/groups/g1/settings/notification",
        json={"items": [{"key": "dest_id", "value": "77", "value_type": "int"}]},
    )
    assert resp.status_code == 400
    assert "텔레그램 연결" in resp.json()["detail"]
```

- [ ] **Step 2: 실패 확인** — Run: `.venv_e2e/bin/python -m pytest tests/test_dest_id_settings.py -q` → FAIL

- [ ] **Step 3: settings_types.py** — `NotificationSettings`에 필드 추가(dispatch_scope 아래):

```python
    # 공용 봇 발송 대상(app.telegram_destinations.dest_id). None=미지정.
    # 해석 우선순위는 notify_service.resolve_notify_target 참조 (설계 §5).
    dest_id: Optional[int] = None
```

`is_sendable` property는 **변경하지 않는다** — resolve가 bot_token/chat_ids를 채워
반환하므로 기존 판정이 그대로 유효.

- [ ] **Step 4: settings_manager.get_notification** — 반환 생성자에 추가:

```python
            dest_id=_as_int(d.get("dest_id"), 0) or None,
```

- [ ] **Step 5: settings.py PUT 검증** — 모듈 수준 헬퍼(테스트 monkeypatch 지점) + notification 분기. 임포트:

```python
from app.models.control.telegram_destination import TelegramDestination
```

헬퍼(파일의 다른 헬퍼들 옆에):

```python
async def _dest_owned_and_active(dest_id: int, owner_user_id: int) -> bool:
    async with get_sessionmaker()() as session:
        dest = await session.get(TelegramDestination, dest_id)
        return bool(dest is not None and dest.user_id == owner_user_id and dest.is_active)
```

`put_settings`의 기존 `if category == "polling":` 검증 블록 **옆에** 추가:

```python
    if category == "notification":
        for item in payload.items:
            if item.key != "dest_id":
                continue
            raw = str(item.value or "").strip()
            if raw in ("", "0"):
                continue  # 클리어 허용
            try:
                did = int(raw)
            except ValueError:
                continue  # 타입 오류는 set_values 검증에 맡김
            if group.owner_user_id is None:
                raise HTTPException(
                    status_code=400,
                    detail="이 그룹은 직접 봇 설정을 사용합니다(텔레그램 연결 선택 불가).",
                )
            if not await _dest_owned_and_active(did, group.owner_user_id):
                raise HTTPException(status_code=400, detail="유효하지 않은 텔레그램 연결입니다.")
```

(주의: put_settings에는 이미 `before_sendable` 계산용 notification 분기가 뒤에 있음 —
이 검증은 그보다 **앞**, `_reject_blocked_puts` 다음에 배치. `get_sessionmaker`는
settings.py에 이미 임포트돼 있는지 확인 후 사용.)

- [ ] **Step 6: 통과 확인** — Run: `.venv_e2e/bin/python -m pytest tests/test_dest_id_settings.py tests/test_settings_permissions.py tests/test_notification_settings_defaults.py -q` → all passed

- [ ] **Step 7: Commit**

```bash
git add app/services/settings_types.py app/services/settings_manager.py app/routers/settings.py tests/test_dest_id_settings.py
git commit -m "feat: notification.dest_id — 로드·owner 소유 active 검증 (400)"
```

---

### Task 7: resolve_notify_target 3단계 해석 + 호출부 5곳 감싸기 + 신규 그룹 baseline

**Files:**
- Modify: `app/services/notify_service.py` (resolve 신규 + 2곳 감싸기)
- Modify: `app/services/monitor_service.py` (_notify_after_analysis 감싸기)
- Modify: `app/services/digest_service.py` (_send_digest_telegram owner 파라미터+감싸기)
- Modify: `app/routers/settings.py` (before/after sendable 판정 감싸기)
- Modify: `app/services/default_settings.py` (신규 그룹 baseline 시드)
- Test: `tests/test_resolve_notify_target.py` (신규)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_resolve_notify_target.py`:

```python
"""발송 대상 3단계 해석 (설계 §5) — 기존 그룹 무중단이 제1원칙."""

from types import SimpleNamespace

from app.services.notify_service import resolve_notify_target
from app.services.settings_types import NotificationSettings

DEST = SimpleNamespace(dest_id=7, user_id=2, chat_id=999, is_active=True)


def _patch_db(monkeypatch, *, global_token="GBT", get_result=None, first_active=None):
    from app.services import notify_service as ns

    async def _tok():
        return global_token

    class _S:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def get(self, model, pk):
            return get_result
        async def execute(self, stmt):
            class _R:
                def scalar_one_or_none(_self):
                    return first_active
            return _R()

    monkeypatch.setattr(ns, "get_global_telegram_bot_token", _tok)
    monkeypatch.setattr(ns, "_ctrl_sessionmaker", lambda: (lambda: _S()))


async def test_priority1_direct_settings_untouched(monkeypatch):
    _patch_db(monkeypatch)
    notif = NotificationSettings(bot_token="GROUPBT", chat_ids=["111"], dest_id=7)
    out = await resolve_notify_target(2, notif)
    assert out.bot_token == "GROUPBT" and out.chat_ids == ["111"]  # 기존 경로 그대로


async def test_priority2_dest_id(monkeypatch):
    _patch_db(monkeypatch, get_result=DEST)
    notif = NotificationSettings(dest_id=7)
    out = await resolve_notify_target(2, notif)
    assert out.bot_token == "GBT" and out.chat_ids == ["999"]
    assert out.is_sendable  # enabled 기본 True + 채워진 대상


async def test_priority3_first_active_fallback(monkeypatch):
    _patch_db(monkeypatch, get_result=None, first_active=DEST)
    notif = NotificationSettings()  # dest_id 미지정
    out = await resolve_notify_target(2, notif)
    assert out.chat_ids == ["999"]


async def test_unresolvable_returns_original(monkeypatch):
    _patch_db(monkeypatch, get_result=None, first_active=None)
    notif = NotificationSettings()
    out = await resolve_notify_target(2, notif)
    assert not out.is_sendable  # 대상 없음 — 발송 안 함 유지

    out2 = await resolve_notify_target(None, notif)  # owner 없음(레거시)
    assert not out2.is_sendable

    _patch_db(monkeypatch, global_token="", first_active=DEST)  # 전역 봇 미설정
    out3 = await resolve_notify_target(2, notif)
    assert not out3.is_sendable
```

- [ ] **Step 2: 실패 확인** — Run: `.venv_e2e/bin/python -m pytest tests/test_resolve_notify_target.py -q` → FAIL

- [ ] **Step 3: notify_service.py — resolve 구현**

임포트 추가(상단):

```python
from dataclasses import replace as _dc_replace

from app.control_db import get_sessionmaker as _ctrl_sessionmaker
from app.models.control.telegram_destination import TelegramDestination
from app.services.global_settings import get_global_telegram_bot_token
```

함수 추가(send_telegram 인근):

```python
async def resolve_notify_target(
    owner_user_id: Optional[int], notif: NotificationSettings
) -> NotificationSettings:
    """발송 대상 3단계 해석 (설계 §5). bot_token/chat_ids를 채운 사본을 반환하므로
    기존 발송 코드(is_sendable·chat 루프)가 무변경으로 동작한다.

    1. 그룹 bot_token+chat_ids 직접 설정 → 그대로 (기존 그룹 무중단)
    2. dest_id 명시 → (전역 봇, 해당 destination)
    3. owner의 첫 active destination(dest_id 오름차순) → 자동 폴백
    해석 불가 시 원본 반환(is_sendable False → 기존 '데이터만 기록' 동작).
    """
    if notif.bot_token and notif.chat_ids:
        return notif
    if owner_user_id is None:
        return notif
    global_token = await get_global_telegram_bot_token()
    if not global_token:
        return notif
    dest = None
    async with _ctrl_sessionmaker()() as session:
        if notif.dest_id:
            cand = await session.get(TelegramDestination, notif.dest_id)
            if cand is not None and cand.user_id == owner_user_id and cand.is_active:
                dest = cand
        if dest is None:
            # dest_id 미지정 또는 무효(삭제/비활성) → 첫 active로 자연 폴백 (설계 §5)
            dest = (
                await session.execute(
                    select(TelegramDestination)
                    .where(
                        TelegramDestination.user_id == owner_user_id,
                        TelegramDestination.is_active.is_(True),
                    )
                    .order_by(TelegramDestination.dest_id)
                    .limit(1)
                )
            ).scalar_one_or_none()
    if dest is None:
        return notif
    return _dc_replace(notif, bot_token=global_token, chat_ids=[str(dest.chat_id)])
```

(`select`·`Optional`·`NotificationSettings` 임포트는 파일에 이미 있는지 확인.)

- [ ] **Step 4: 호출부 5곳 감싸기** — 각 지점에서 `notif = await ...get_notification(...)` **바로 다음 줄**에 한 줄 추가. 정확한 지점(라인은 현재 기준, 실제 파일 읽고 확인):

1. `app/services/monitor_service.py` `_notify_after_analysis`(:429 인근):
   `notif = await resolve_notify_target(group.owner_user_id, notif)` (임포트: `from app.services.notify_service import resolve_notify_target` — 이 파일은 notify_service를 이미 임포트하는지 확인, 순환 주의: notify_service는 monitor_service를 임포트하지 않음 → 안전)
2. `app/services/notify_service.py` scheduled 디스패치 루프(:574 인근, `for group in groups:` 안):
   `notif = await resolve_notify_target(group.owner_user_id, notif)`
3. `app/services/notify_service.py` `backfill_notify_baselines` 루프(:664 인근): 동일 한 줄 — dest 연결로 sendable해진 그룹도 부팅 시 baseline 스탬프.
4. `app/services/digest_service.py` `_send_digest_telegram`(:499): 시그니처에 `owner_user_id: Optional[int] = None` 키워드 파라미터 추가, `notif = await get_settings_manager().get_notification(group_id)` 다음에 감싸기. 호출부(`generate_digest_for_group` 안, `_send_digest_telegram(` 호출 — grep으로 위치 확인)에 `owner_user_id=group.owner_user_id` 전달.
5. `app/routers/settings.py` `put_settings`의 before/after sendable 판정(:149·:158 인근):
   `before_sendable = (...).is_sendable` → 두 곳 모두 resolve 경유로 교체:
   ```python
   _n = await mgr.get_notification(group.group_id)
   before_sendable = (await resolve_notify_target(group.owner_user_id, _n)).is_sendable
   ```
   (after 동일. 임포트 추가. 이로써 dest_id PUT으로 sendable 전환 시 baseline 자동 스탬프.)

- [ ] **Step 5: 신규 그룹 baseline 시드** — `app/services/default_settings.py`의 notification 시드 목록에 `notify_baseline_at`이 `""`로 있음(:51 인근). `seed_default_settings` 함수를 읽고, notification 카테고리 적용 시 `notify_baseline_at` 값을 호출 시각으로 교체:

```python
# seed_default_settings 안, notification 항목 적용 직전:
from datetime import datetime, timezone

items = [
    {**it, "value": datetime.now(timezone.utc).isoformat()}
    if it["key"] == "notify_baseline_at" else it
    for it in items
]
```

(정확한 함수 구조에 맞춰 적용 — DEFAULTS dict 자체는 수정하지 말 것(모듈 로드 시각 고정
버그). 근거: 신규 그룹은 생성 이후 게시 영상만 자동 발송 — dest 연결만으로 sendable해져도
baseline 부재로 발송 보류되는 온보딩 갭 제거, backlog flood 방지 취지 유지.)

- [ ] **Step 6: 통과 확인 + 리그레션** — Run: `.venv_e2e/bin/python -m pytest tests/test_resolve_notify_target.py tests/ -q` → all passed (기존 notify/digest/settings 테스트 무변경 통과 = 우선순위 1 무중단 증명)

- [ ] **Step 7: Commit**

```bash
git add app/services/notify_service.py app/services/monitor_service.py app/services/digest_service.py app/routers/settings.py app/services/default_settings.py tests/test_resolve_notify_target.py
git commit -m "feat: 발송 대상 3단계 해석(직접설정→dest_id→첫 destination) + 신규 그룹 baseline 시드"
```

---

### Task 8: 프런트 — 마이페이지 텔레그램 연결 섹션

**Files:**
- Modify: `frontend/src/api/me.ts`, `frontend/src/pages/MyPage.tsx`

- [ ] **Step 1: me.ts 확장**

```typescript
export interface TelegramDestination {
  dest_id: number
  chat_kind: string
  title: string | null
  is_active: boolean
  linked_at: string
}

export interface TelegramLinkResponse {
  deep_link: string
  expires_in_sec: number
}

// meApi에 추가:
  telegramLinkToken: () => rootApi.post<TelegramLinkResponse>('/me/telegram/link-token'),
  telegramDestinations: () => rootApi.get<TelegramDestination[]>('/me/telegram/destinations'),
  deleteTelegramDestination: (destId: number) =>
    rootApi.del<void>(`/me/telegram/destinations/${destId}`),
```

(`rootApi.post`가 body 없이 호출 가능한지 http.ts 확인 — 필요하면 `post(path, {})`.)

- [ ] **Step 2: MyPage.tsx — "텔레그램 연결" 섹션 추가** (기존 카드 스타일 `bg-white rounded-xl shadow-sm p-4`):

- 상태: `destinations`, `linkError`, `polling`(boolean).
- 마운트 시 `meApi.telegramDestinations()` 로드.
- 목록: 각 행 `title ?? '연결됨'` + `dayjs(linked_at)` 포맷 + "해제" 버튼(`deleteTelegramDestination` → 목록 재조회).
- "연결하기" 버튼: `telegramLinkToken()` → `window.open(deep_link, '_blank')` → 3초 간격 폴링 시작(기존 목록 길이 기억, 새 destination 감지 시 목록 갱신+폴링 중단, 최대 2분 후 자동 중단). 400 응답이면 detail을 안내 문구로 표시.
- 폴링 중 문구: "텔레그램에서 '시작'을 누르면 자동으로 연결됩니다…".

- [ ] **Step 3: 빌드 확인** — Run: `cd frontend && npm run test -- --run && npm run build` → 성공

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/me.ts frontend/src/pages/MyPage.tsx
git commit -m "feat: 마이페이지 텔레그램 연결 — 딥링크 열기·자동 감지 폴링·해제"
```

---

### Task 9: 프런트 — 온보딩 체크리스트 + 그룹 0개 랜딩 + dest 선택 렌더

**Files:**
- Create: `frontend/src/components/OnboardingChecklist.tsx`
- Modify: `frontend/src/App.tsx` (그룹 0개 랜딩), `frontend/src/pages/Dashboard.tsx` (카드), `frontend/src/settings/defs.ts` + `frontend/src/pages/Settings.tsx` (notification dest 선택), `frontend/src/api/settings.ts` (필요시)
- Test: `frontend/src/components/OnboardingChecklist.test.tsx` (스텝 판정 로직)

- [ ] **Step 1: OnboardingChecklist.tsx**

- 순수 판정 함수를 분리해 export(테스트 대상):

```typescript
export interface OnboardingState {
  groupCount: number
  channelCount: number      // 현재 활성 그룹 기준 (그룹 없으면 0)
  destinationCount: number
}

export function onboardingSteps(s: OnboardingState) {
  return [
    { key: 'group', label: '모니터링 그룹 만들기', done: s.groupCount > 0 },
    { key: 'channel', label: '채널 추가하기', done: s.channelCount > 0 },
    { key: 'telegram', label: '텔레그램 연결하기', done: s.destinationCount > 0 },
  ]
}

export function onboardingComplete(s: OnboardingState): boolean {
  return onboardingSteps(s).every((x) => x.done)
}
```

- 컴포넌트: props로 `state: OnboardingState`와 `activeSlug: string | null`을 받아 카드 렌더 — 각 스텝 ✓/번호 + 미완 스텝에 링크(그룹: 그룹 생성 UI(GroupModals 재사용 또는 상단 그룹 추가 버튼 안내), 채널: `/g/{slug}/channels`, 텔레그램: `/me`). `onboardingComplete`면 null 반환. `role !== 'user'`면 null(부모에서 걸러도 됨 — 컴포넌트 안에서 useAuth로 처리해 단일화).
- 데이터 로딩 훅 `useOnboardingState()`: useGroup()의 groups + `channelApi(slug).list()`(그룹 있을 때) + `meApi.telegramDestinations()` — 프런트 API 이름은 실제 파일(`api/channels.ts`) 확인 후 사용.

- [ ] **Step 2: 노출 2곳**

- `App.tsx` RootRedirect(:20-34): 그룹 0개일 때 문구 대신 `<OnboardingChecklist ...>` + 그룹 생성 진입(기존 `GroupModals.tsx`의 생성 모달 재사용 — export 형태 확인; user 생성 시 서버가 slug/schema를 자동 생성하므로 name만 받는 간이 폼도 가능. GroupModals가 slug 입력을 요구하면 user 경로에선 name을 slug 칸에 그대로 넣어도 서버가 무시함 — 간단한 쪽 선택).
- `Dashboard.tsx` 상단: `useAuth()` role=user이고 `!onboardingComplete(state)`일 때 카드 렌더.

- [ ] **Step 3: notification dest 선택 렌더**

- `defs.ts` notification 목록에 `{ key: 'dest_id', label: '발송 대상(텔레그램 연결)', type: 'dest_select', help: '마이페이지에서 연결한 텔레그램으로 발송합니다' }` 추가 + `FieldType`에 `'dest_select'` 추가.
- `Settings.tsx`: `dest_select` 타입 커스텀 렌더 — `meApi.telegramDestinations()`로 옵션 로드, `<select>`(빈 옵션 "미지정(자동)" = value ''→0 클리어, 각 destination은 `title` 표시, value=dest_id). 기존 select 렌더 패턴 재사용. admin에게도 동일 렌더(본인 destinations — admin 그룹은 직접 설정이 1순위라 참고용).

- [ ] **Step 4: 스텝 판정 테스트** — `OnboardingChecklist.test.tsx`:

```typescript
import { describe, expect, it } from 'vitest'
import { onboardingComplete, onboardingSteps } from './OnboardingChecklist'

describe('onboardingSteps', () => {
  it('신규 사용자: 전부 미완', () => {
    const s = { groupCount: 0, channelCount: 0, destinationCount: 0 }
    expect(onboardingSteps(s).map((x) => x.done)).toEqual([false, false, false])
    expect(onboardingComplete(s)).toBe(false)
  })
  it('그룹+채널만: 텔레그램 미완', () => {
    const s = { groupCount: 1, channelCount: 2, destinationCount: 0 }
    expect(onboardingSteps(s).map((x) => x.done)).toEqual([true, true, false])
  })
  it('전부 완료 시 complete', () => {
    expect(onboardingComplete({ groupCount: 1, channelCount: 1, destinationCount: 1 })).toBe(true)
  })
})
```

- [ ] **Step 5: 빌드·테스트 확인** — Run: `cd frontend && npm run test -- --run && npm run build` → 성공(기존 30 + 신규 3)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/OnboardingChecklist.tsx frontend/src/components/OnboardingChecklist.test.tsx frontend/src/App.tsx frontend/src/pages/Dashboard.tsx frontend/src/settings/defs.ts frontend/src/pages/Settings.tsx frontend/src/api/settings.ts frontend/src/api/me.ts
git commit -m "feat: 온보딩 체크리스트 카드·그룹0 랜딩·notification 발송 대상 선택"
```

(`git add` 목록은 실제 수정 파일에 맞춰 조정.)

---

### Task 10: 전체 리그레션 + 상위 스펙 갱신

**Files:**
- Modify: `docs/superpowers/specs/2026-07-03-multi-tenant-design.md` (§7 D행)

- [ ] **Step 1: 전체 테스트** — Run: `.venv_e2e/bin/python -m pytest tests/ -q` → all passed (279 + 신규 전부). `cd frontend && npm run test -- --run && npm run build` → 성공.

- [ ] **Step 2: 상위 스펙 §7 D행 갱신** — D행을 D-1/D-2로 분해 표기:

`D. 온보딩·운영` 행의 내용 셀 앞에 `(D-1 구현 완료 2026-XX-XX — 봇 딥링크 연결·온보딩 체크리스트, 설계 2026-07-11-phase-d1-telegram-link-onboarding-design.md. D-2 잔여: YouTube 쿼터 카운터·전 스키마 마이그레이션 도구)` 추가(실제 날짜로, 표 구조 유지).

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-07-03-multi-tenant-design.md
git commit -m "docs: Phase D-1 구현 반영 — 공용 봇 연결·온보딩 완료 표기 (D-2 잔여 명시)"
```

---

## 실 DB E2E (구현 완료 후, 별도 세션 체크포인트)

테스트 DB `100.115.13.102`(`.env` CONTROL_DATABASE_URL), **실 봇 토큰 필요**(BotFather 생성).
**주의: `postgres-ytdb` MCP 금지. 앱 자체 엔진만** (`PYTHONPATH=. .venv_e2e/bin/python`,
httpx ASGITransport, 로그인 경로 `/api/auth/login`).

1. 부팅: 테이블 2개 생성 확인. env `DEFAULT_TELEGRAM_BOT_TOKEN` 설정 후 부팅 → 전역
   시드 확인(멱등 재확인).
2. 임시 user 로그인 → `POST /api/me/telegram/link-token` → deep_link 수신 → **실제
   텔레그램에서 /start 탭** → 워커가 destination 생성 + "연결 완료" 회신 수신 확인.
3. destinations 목록 조회·타 유저로 삭제 시도(404)·본인 삭제(204).
4. 그룹 생성(baseline 자동 시드 확인) → notification dest_id 없이 → 분석 1건 발생 →
   **우선순위 3(첫 destination)으로 실제 텔레그램 알림 수신**.
5. dest_id 명시 PUT(소유 검증 400 케이스 포함) → 우선순위 2 발송 확인.
6. destination 해제 → 발송 skip(데이터만) 확인. 재연결 → 재개.
7. 기존 e2e_a(직접 bot_token 설정 그룹) 발송 경로 불변 확인(우선순위 1).
8. cleanup: 임시 유저/그룹/스키마/destination/토큰 정리.
