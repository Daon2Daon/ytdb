"""데이터 평면(그룹별 스키마) 비동기 엔진 매니저.

- 연결 풀은 물리 서버 단위로 공유한다(서버 시그니처 = DSN에서 스키마 제외).
  같은 서버를 쓰는 그룹이 늘어도 풀 수는 늘지 않는다.
- 그룹 격리는 schema_translate_map으로 SCHEMA_TOKEN을 그룹의 schema_name으로
  변환하여 달성한다(연결 상태를 변형하지 않는 무상태 방식).
- 이벤트 루프별로 엔진을 분리한다(asyncpg가 루프 간 연결 공유를 허용하지 않음).
"""

from __future__ import annotations

import asyncio
import weakref
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Protocol
from urllib.parse import quote_plus

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models.pg.base import SCHEMA_TOKEN, PgBase
from app.services.settings_manager import get_settings_manager
from app.services.settings_types import DatabaseSettings


class DBNotConfiguredError(RuntimeError):
    """그룹의 데이터 평면 DB 접속 설정이 없는 상태."""


class GroupRef(Protocol):
    group_id: int
    schema_name: str


def _build_dsn(cfg: DatabaseSettings) -> str:
    user = quote_plus(cfg.username)
    pwd = quote_plus(cfg.password or "")
    auth = f"{user}:{pwd}" if pwd else user
    return f"postgresql+asyncpg://{auth}@{cfg.host}:{int(cfg.port)}/{quote_plus(cfg.dbname)}"


def _connect_args(sslmode: str | None) -> dict[str, Any]:
    mode = (sslmode or "prefer").lower().strip()
    if mode == "disable":
        return {"ssl": False}
    if mode == "require":
        return {"ssl": True}
    # allow / prefer: asyncpg 기본 동작에 위임
    return {}


class DataPlaneEngineManager:
    def __init__(self) -> None:
        # loop -> {server_sig: (engine, server_sig)}
        self._engines: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, dict[str, tuple[AsyncEngine, str]]
        ] = weakref.WeakKeyDictionary()
        # ensure_schema를 성공한 (server_sig, schema_name). 루프 무관하게 중복 DDL 방지.
        self._initialized: set[tuple[str, str]] = set()
        # 동시 요청(채널/영상 탭 병렬 로드 등)이 같은 스키마를 중복 생성하지 않도록 잠금.
        self._ensure_locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def _shared_engine(self, cfg: DatabaseSettings) -> AsyncEngine:
        loop = asyncio.get_running_loop()
        sig = cfg.server_signature()
        per_loop = self._engines.get(loop)
        if per_loop is None:
            per_loop = {}
            self._engines[loop] = per_loop
        entry = per_loop.get(sig)
        if entry is not None:
            return entry[0]
        engine = create_async_engine(
            _build_dsn(cfg),
            pool_size=5,
            max_overflow=5,
            pool_pre_ping=True,
            connect_args=_connect_args(cfg.sslmode),
        )
        per_loop[sig] = (engine, sig)
        return engine

    async def _cfg(self, group: GroupRef) -> DatabaseSettings:
        cfg = await get_settings_manager().get_database(group.group_id)
        if not cfg.is_configured:
            raise DBNotConfiguredError(
                f"그룹(group_id={group.group_id})의 DB 설정이 없습니다. "
                "settings/database를 먼저 입력하세요."
            )
        return cfg

    async def get_engine_for_group(self, group: GroupRef) -> AsyncEngine:
        return await self._shared_engine(await self._cfg(group))

    def session_for_group(self, engine: AsyncEngine, schema_name: str) -> AsyncSession:
        """공유 엔진 풀을 그대로 쓰되, 이 그룹의 스키마로 바인딩한 세션을 반환."""
        bound = engine.execution_options(schema_translate_map={SCHEMA_TOKEN: schema_name})
        return async_sessionmaker(bound, expire_on_commit=False)()

    @asynccontextmanager
    async def group_session(self, group: GroupRef) -> AsyncIterator[AsyncSession]:
        """그룹 스키마를 보장하고 바인딩된 세션을 열어주는 컨텍스트 매니저."""
        await self.ensure_schema(group)
        engine = await self.get_engine_for_group(group)
        session = self.session_for_group(engine, group.schema_name)
        try:
            yield session
        finally:
            await session.close()

    def _ensure_lock(self, key: tuple[str, str]) -> asyncio.Lock:
        lock = self._ensure_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._ensure_locks[key] = lock
        return lock

    async def ensure_schema(self, group: GroupRef) -> None:
        """그룹 스키마와 데이터 평면 테이블을 멱등 생성한다."""
        cfg = await self._cfg(group)
        key = (cfg.server_signature(), group.schema_name)
        if key in self._initialized:
            return
        async with self._ensure_lock(key):
            if key in self._initialized:
                return
            engine = await self._shared_engine(cfg)
            bound = engine.execution_options(
                schema_translate_map={SCHEMA_TOKEN: group.schema_name}
            )
            async with bound.begin() as conn:
                schema_exists = (
                    await conn.execute(
                        text("SELECT 1 FROM pg_namespace WHERE nspname = :n"),
                        {"n": group.schema_name},
                    )
                ).first() is not None
                if not schema_exists:
                    try:
                        await conn.execute(
                            text(f'CREATE SCHEMA IF NOT EXISTS "{group.schema_name}"')
                        )
                    except IntegrityError:
                        # 동시 생성 등으로 이미 생긴 경우 무시하고 테이블 생성만 진행.
                        pass

                existing = {
                    r[0]
                    for r in (
                        await conn.execute(
                            text(
                                "SELECT table_name FROM information_schema.tables "
                                "WHERE table_schema = :s"
                            ),
                            {"s": group.schema_name},
                        )
                    ).fetchall()
                }

                def _create_missing(sync_conn) -> None:
                    for table in PgBase.metadata.sorted_tables:
                        if table.name not in existing:
                            table.create(sync_conn, checkfirst=False)

                await conn.run_sync(_create_missing)

                # 기존 스키마 자가치유: 추가 컬럼을 멱등 패치한다.
                # (create_all은 기존 테이블에 컬럼을 추가하지 않으므로 명시 ALTER)
                additive_columns = [
                    ("channels", "notify_from", "timestamptz"),
                    ("video_analysis", "analysis_sections", "jsonb"),
                    ("videos", "share_token", "text"),
                    ("videos", "share_visibility", "text"),
                ]
                for tbl, col, coltype in additive_columns:
                    await conn.execute(
                        text(
                            f'ALTER TABLE "{group.schema_name}"."{tbl}" '
                            f'ADD COLUMN IF NOT EXISTS "{col}" {coltype}'
                        )
                    )

                await conn.execute(
                    text(
                        f'CREATE UNIQUE INDEX IF NOT EXISTS '
                        f'"ux_{group.schema_name}_videos_share_token" '
                        f'ON "{group.schema_name}"."videos" (share_token) '
                        f'WHERE share_token IS NOT NULL'
                    )
                )
            self._initialized.add(key)

    async def dispose_current_loop(self) -> None:
        loop = asyncio.get_running_loop()
        per_loop = self._engines.pop(loop, None)
        if not per_loop:
            return
        for engine, _ in per_loop.values():
            await engine.dispose()


data_plane_engine_manager = DataPlaneEngineManager()
