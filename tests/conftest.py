"""pytest 공용 설정.

- asyncio 모드 auto: async 테스트에 데코레이터 불필요.
- DB가 필요한 통합 테스트는 control DB(DATABASE_URL) 가용 시에만 의미가 있으므로
  개별 테스트에서 별도 fixture/skip을 사용한다. 본 Plan 1의 테스트는 DB 불필요.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
