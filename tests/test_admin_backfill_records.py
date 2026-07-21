def test_backfill_module_exports():
    from app.services import records_backfill
    assert hasattr(records_backfill, "backfill_records_for_group")
