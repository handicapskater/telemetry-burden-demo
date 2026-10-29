from app.analysis import analyze_records


def record(record_id, activity="skating", start="2024-01-01T12:00:00Z", duration=30, hr=120, hrv=50):
    return {
        "record_id": record_id, "activity": activity, "timestamp": start,
        "original_timestamps": {"whoop_start": start},
        "original_activities": {"whoop": activity.title()},
        "duration_minutes": duration, "heart_rate": {"mean_bpm": hr},
        "hrv": {"hrv_ms": hrv},
    }


def test_deduplicates_workout_identity_and_calculates_burden():
    rows = [record("row_1"), record("row_2"), record("row_3", "yoga", "2024-01-02", 60, 90, 40)]
    result = analyze_records(rows)
    assert result["input_rows"] == 3
    assert result["unique_workouts"] == 2
    assert result["duplicate_rows"] == 1
    assert result["activities"]["skating"]["mean_burden_proxy"] == 36
    assert result["activities"]["skating"]["burden_classification"] == "LOWER OBSERVED BURDEN"
    assert result["activities"]["skating"]["median_heart_rate_bpm"] == 120
    assert result["comparisons"] == []
    assert result["activities"]["skating"]["evidence_ids"] == ["row_1"]
    assert "walking" not in result["activities"]
    assert "daily recovery" in result["hrv_context"]


def test_missing_measurements_are_excluded_from_means():
    rows = [record("row_1", hr=None, hrv=None), record("row_2", start="2024-01-02", hr=100, hrv=55)]
    result = analyze_records(rows)["activities"]["skating"]
    assert result["count"] == 2
    assert result["mean_heart_rate_bpm"] == 100
    assert result["mean_daily_hrv_ms"] == 55
    assert result["daily_hrv_sample_count"] == 1
    assert result["burden_sample_count"] == 1


def test_target_comparison_has_signed_hr_difference_and_context():
    rows = [record("row_1", "skating", hr=120), record("row_2", "transportation", "2024-01-02", hr=100)]
    comparison = analyze_records(rows)["comparisons"][0]
    assert (comparison["activity_a"], comparison["activity_b"]) == ("skating", "transportation")
    assert comparison["sample_size_a"] == comparison["sample_size_b"] == 1
    assert comparison["median_hr_difference_b_minus_a"] == {"signed_absolute": -20.0, "signed_percent_of_first": -16.67}
    assert "not activity HRV" in comparison["limitations"][0]
