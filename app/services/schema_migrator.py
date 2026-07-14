"""전 스키마 순회 마이그레이션 (스펙 D-2 §2).

lazy ensure_schema를 선제·가시적으로 전 그룹에 적용한다. 순차 실행 —
수십 그룹 규모에서 충분히 빠르고 DDL 동시 실행 부하·락 경합을 피한다.
비활성 그룹도 포함(스키마는 데이터 자산). 그룹 단위 격리: 실패해도 계속.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import select

from app.control_db import get_sessionmaker
from app.models.control.group import Group
from app.services.db_engine import DBNotConfiguredError
from app.services.db_engine import data_plane_engine_manager as dpm


@dataclass
class GroupMigrationResult:
    group_id: int
    slug: str
    schema_name: str
    status: str            # 'ok' | 'failed' | 'skipped'(DB 미설정)
    error: str | None
    duration_ms: int


async def _all_groups() -> list[Group]:
    async with get_sessionmaker()() as session:
        return list(
            (await session.execute(select(Group).order_by(Group.group_id))).scalars().all()
        )


async def migrate_all_schemas() -> list[GroupMigrationResult]:
    results: list[GroupMigrationResult] = []
    for group in await _all_groups():
        t0 = time.monotonic()
        status, error = "ok", None
        try:
            await dpm.ensure_schema(group, force=True)
        except DBNotConfiguredError:
            status = "skipped"
        except Exception as e:  # noqa: BLE001 — 그룹 단위 격리
            status, error = "failed", str(e)
        results.append(
            GroupMigrationResult(
                group_id=group.group_id,
                slug=group.slug,
                schema_name=group.schema_name,
                status=status,
                error=error,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        )
    return results


def summarize(results: list[GroupMigrationResult]) -> dict[str, int]:
    return {
        "ok": sum(1 for r in results if r.status == "ok"),
        "failed": sum(1 for r in results if r.status == "failed"),
        "skipped": sum(1 for r in results if r.status == "skipped"),
    }
