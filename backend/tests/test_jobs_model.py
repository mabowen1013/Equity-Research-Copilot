from app.models import Job


def test_jobs_table_contains_initial_tracking_columns() -> None:
    columns = Job.__table__.columns

    assert set(columns.keys()) == {
        "id",
        "job_type",
        "company_id",
        "status",
        "progress",
        "retry_count",
        "payload",
        "error_message",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
    }


def test_jobs_table_defines_status_and_progress_constraints() -> None:
    constraint_names = {
        constraint.name
        for constraint in Job.__table__.constraints
        if constraint.name is not None
    }

    assert "ck_jobs_status" in constraint_names
    assert "ck_jobs_progress_range" in constraint_names
    assert "ck_jobs_retry_count_nonnegative" in constraint_names
