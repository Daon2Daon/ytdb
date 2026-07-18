"""그룹 스코프 헬스 체크 (데이터 평면 DB, AI 게이트웨이)."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.models.control.group import Group
from app.routers.deps import get_group_or_404
from app.schemas.stats import DBHealthOut, GatewayHealthOut
from app.services.db_engine import DBNotConfiguredError, data_plane_engine_manager as dpm
from app.services.global_settings import resolve_ai_gateway
from app.services.llm_client import LiteLLMClient, LiteLLMError

router = APIRouter(prefix="/api/groups/{slug}/health", tags=["health"])


@router.get("/db", response_model=DBHealthOut)
async def db_health(group: Group = Depends(get_group_or_404)) -> DBHealthOut:
    started = time.monotonic()
    try:
        async with dpm.group_session(group) as session:
            await session.execute(text("SELECT 1"))
    except DBNotConfiguredError as e:
        return DBHealthOut(healthy=False, message=str(e))
    except Exception as e:  # noqa: BLE001
        return DBHealthOut(healthy=False, message=f"DB 연결 실패: {e}")
    latency_ms = int((time.monotonic() - started) * 1000)
    return DBHealthOut(healthy=True, message="정상", latency_ms=latency_ms)


@router.post("/gateway", response_model=GatewayHealthOut)
async def gateway_health(group: Group = Depends(get_group_or_404)) -> GatewayHealthOut:
    # 실행 파이프라인과 동일하게 그룹 → 전역 → 기본값 해석 — 전역만 설정된
    # 사용자 그룹에서 거짓 "미설정" 오류를 내지 않는다.
    cfg = await resolve_ai_gateway(group.group_id)
    if not cfg.base_url or not cfg.api_key:
        return GatewayHealthOut(success=False, message="base_url/api_key 미설정")
    started = time.monotonic()
    client = LiteLLMClient(cfg)
    try:
        models = await client.get_models(force_refresh=True)
    except LiteLLMError as e:
        return GatewayHealthOut(success=False, message=str(e))
    except Exception as e:  # noqa: BLE001
        return GatewayHealthOut(success=False, message=f"게이트웨이 연결 실패: {e}")
    finally:
        await client.aclose()
    latency_ms = int((time.monotonic() - started) * 1000)
    return GatewayHealthOut(success=True, message=f"모델 {len(models)}개 확인", latency_ms=latency_ms)
