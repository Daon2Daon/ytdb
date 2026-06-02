"""데이터 평면(그룹별 스키마) ORM Base.

모델은 schema를 하드코딩하지 않고 심볼릭 토큰(SCHEMA_TOKEN)으로 선언한다.
런타임에 세션/연결의 schema_translate_map이 토큰을 그룹의 실제 schema_name으로 변환한다.
이로써 단일 모델 정의로 모든 그룹 스키마를 다루고, 연결 풀을 서버 단위로 공유한다.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase

# 모든 데이터 평면 테이블의 심볼릭 스키마 토큰.
SCHEMA_TOKEN = "ytgroup"


class PgBase(DeclarativeBase):
    pass
