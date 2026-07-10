"""제어 평면(app 스키마) PostgreSQL 비동기 엔진/세션.

groups/settings 등 그룹 정의와 설정을 보관하는 신뢰원(信賴源).
데이터 평면(그룹별 스키마) 엔진은 services/db_engine.py에서 별도로 관리한다.
"""

from __future__ import annotations

from typing import AsyncIterator, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# 제어 평면 스키마명. 사용자 설정 대상이 아니라 고정값이다.
APP_SCHEMA = "app"


class Base(DeclarativeBase):
    """제어 평면 ORM Base."""


_engine: Optional[AsyncEngine] = None
_sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        if not settings.CONTROL_DATABASE_URL:
            raise RuntimeError(
                "CONTROL_DATABASE_URL이 설정되지 않았습니다. .env를 확인하세요."
            )
        _engine = create_async_engine(settings.CONTROL_DATABASE_URL, pool_pre_ping=True)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 의존성: 제어 평면 세션."""
    async with get_sessionmaker()() as session:
        yield session


async def ensure_control_schema() -> None:
    """app 스키마와 제어 평면 테이블을 멱등 생성한다."""
    # 모델을 임포트해 Base.metadata에 등록되도록 한다.
    from app.models.control import (  # noqa: F401
        ai_usage,
        analysis_cache,
        analysis_delivery,
        channel_registry,
        channel_subscription,
        global_setting,
        group,
        invitation,
        plan,
        prompt_preset,
        setting,
        user,
        user_limit,
    )

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{APP_SCHEMA}"'))
        await conn.run_sync(Base.metadata.create_all)
        # 기존 설치 업그레이드: create_all은 기존 테이블에 컬럼을 추가하지 않는다.
        await conn.execute(
            text(
                f'ALTER TABLE "{APP_SCHEMA}".groups '
                f"ADD COLUMN IF NOT EXISTS owner_user_id BIGINT "
                f'REFERENCES "{APP_SCHEMA}".users(user_id)'
            )
        )
        # Phase B 업그레이드: 기존 설치의 deliveries 중복 행 정리 후 UNIQUE 추가.
        # create_all은 기존 테이블에 제약을 추가하지 않으므로 명시적으로 처리한다.
        has_uq = (
            await conn.execute(
                text(
                    "SELECT 1 FROM pg_constraint "
                    "WHERE conname = 'uq_analysis_deliveries_user_cache'"
                )
            )
        ).first()
        if has_uq is None:
            # 중복 중 가장 오래된 행(delivery_id 최소)만 유지
            await conn.execute(
                text(
                    f'DELETE FROM "{APP_SCHEMA}".analysis_deliveries a '
                    f'USING "{APP_SCHEMA}".analysis_deliveries b '
                    "WHERE a.user_id = b.user_id AND a.cache_id = b.cache_id "
                    "AND a.delivery_id > b.delivery_id"
                )
            )
            await conn.execute(
                text(
                    f'ALTER TABLE "{APP_SCHEMA}".analysis_deliveries '
                    "ADD CONSTRAINT uq_analysis_deliveries_user_cache "
                    "UNIQUE (user_id, cache_id)"
                )
            )
