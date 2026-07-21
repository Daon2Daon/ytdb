from app.models.pg import AnalysisRecord, Entity, PgBase


def test_models_registered_in_metadata():
    names = set(PgBase.metadata.tables.keys())
    assert any(n.endswith(".analysis_records") for n in names)
    assert any(n.endswith(".entities") for n in names)


def test_analysis_record_columns():
    cols = {c.name for c in AnalysisRecord.__table__.columns}
    assert {
        "record_pk", "video_pk", "record_type", "schema_version",
        "position", "entity_name", "value_text", "value_num",
        "event_date", "attrs", "created_at",
    } <= cols


def test_analysis_record_unique_constraint():
    uniques = [
        set(c.name for c in con.columns)
        for con in AnalysisRecord.__table__.constraints
        if con.__class__.__name__ == "UniqueConstraint"
    ]
    assert {"video_pk", "record_type", "position"} in uniques


def test_entity_columns_and_unique():
    cols = {c.name for c in Entity.__table__.columns}
    assert {
        "entity_pk", "canonical_name", "aliases", "attrs",
        "status", "mention_count", "first_seen", "last_seen",
    } <= cols
    uniques = [
        set(c.name for c in con.columns)
        for con in Entity.__table__.constraints
        if con.__class__.__name__ == "UniqueConstraint"
    ]
    assert {"canonical_name"} in uniques
