"""데이터 평면 모델 패키지.

import 시 모든 모델을 PgBase.metadata에 등록한다(create_all 대상).
"""

from app.models.pg.analysis_record import AnalysisRecord
from app.models.pg.base import SCHEMA_TOKEN, PgBase
from app.models.pg.channel import Channel
from app.models.pg.deleted_video import DeletedVideo
from app.models.pg.digest import Digest
from app.models.pg.entity import Entity
from app.models.pg.job_log import JobLog
from app.models.pg.tag import Tag, VideoTag
from app.models.pg.video import Video
from app.models.pg.video_analysis import VideoAnalysis

__all__ = [
    "SCHEMA_TOKEN",
    "PgBase",
    "Channel",
    "Video",
    "VideoAnalysis",
    "Tag",
    "VideoTag",
    "JobLog",
    "DeletedVideo",
    "Digest",
    "AnalysisRecord",
    "Entity",
]
