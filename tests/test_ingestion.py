import json
from pathlib import Path

import pytest

from app.ingestion import (
    generate_quality_report,
    load_csv,
    normalize_telemetry,
    save_json,
    validate_records,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/raw/whoop_strava_join_candidates.csv"
SCHEMA = ROOT / "data/metadata/schema.json"


def sample_rows():
    columns = json.loads(SCHEMA.read_text())["input_columns"]
    first = {column: "" for column in columns}
    first.update({
        "entity_type_whoop": "whoop_workout_event", "activity_name_whoop": "Inline Skating",
        "start_time_whoop": "2024-01-01T12:00:00Z", "duration_min": "30",
        "metrics.average_hr_bpm_whoop": "120", "metrics.max_hr_bpm_whoop": "150",
        "metrics.hrv_ms": "40", "event_id_strava": "strava_1",
        "entity_type_strava": "strava_activity", "likely_match": "True",
    })
    second = dict(first)
    second["activity_name_strava"] = "Another candidate"
    return [first, second]


@pytest.mark.skipif(not SOURCE.exists(), reason="private source CSV is unavailable in CI")
def test_full_source_matches_contract_and_checked_in_sample(tmp_path):
    rows = load_csv(SOURCE)
    validated = validate_records(rows)
    records = normalize_telemetry(validated)
    quality = generate_quality_report(records)

    assert len(rows) == len(records) == 2601
    assert [record.source_row for record in records] == list(range(1, 2602))
    assert quality.duplicate_records == 1079
    assert quality.ambiguous_matches == 1933
    assert quality.device_coverage == {"WHOOP": 2601, "Strava": 2303, "Polar": 0}
    assert quality.missing_physiological_fields["hrv"] == 63
    assert quality.activity_coverage.get("walking", 0) == 0
    assert all(record.hrv.context.startswith("WHOOP daily recovery") for record in records)
    assert all(record.hrv.rmssd_ms is None and record.hrv.sdnn_ms is None for record in records)

    expected = json.loads((ROOT / "data/processed/telemetry.json").read_text())
    assert [record.model_dump() for record in records] == expected

    output = tmp_path / "telemetry.json"
    save_json(records, output)
    first = output.read_bytes()
    save_json(normalize_telemetry(validate_records(load_csv(SOURCE))), output)
    assert output.read_bytes() == first
    assert len(json.loads(first)) == len(rows)
    save_json(quality, tmp_path / "quality.json")
    assert json.loads((tmp_path / "quality.json").read_text()) == quality.model_dump()


def test_repeated_candidates_remain_distinct_and_ambiguous():
    records = normalize_telemetry(sample_rows(), SCHEMA)
    assert records[0].record_id == "row_00001"
    assert records[1].record_id == "row_00002"
    assert records[0].join["whoop_candidate_count"] == 2
    assert records[1].join["whoop_candidate_count"] == 2
    assert records[0].join["ambiguous"] is True
    assert records[1].join["ambiguous"] is True
    assert generate_quality_report(records).duplicate_records == 1


def test_invalid_schema_fails_without_discarding_rows():
    rows = sample_rows()
    malformed = dict(rows[0])
    malformed.pop("start_time_whoop")
    with pytest.raises(ValueError, match="row 1"):
        validate_records([malformed], SCHEMA)
    with pytest.raises(ValueError, match="Every input row"):
        generate_quality_report(normalize_telemetry(rows[:1], SCHEMA), 2)
