"""Load WHOOP/Strava join candidates without collapsing candidate rows."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.schemas import QualityReport, TelemetryRecord

DEFAULT_SCHEMA = Path(__file__).resolve().parents[1] / "data/metadata/schema.json"


def load_csv(path: str | Path) -> list[dict[str, str]]:
    """Read a source CSV from an explicit path, preserving row order and blanks."""
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError("CSV must have a nonempty header with unique columns")
        rows = list(reader)
    if any(None in row for row in rows):
        raise ValueError("CSV row has more fields than its header")
    return rows


def validate_records(
    rows: Sequence[Mapping[str, str]], schema_path: str | Path = DEFAULT_SCHEMA
) -> list[dict[str, str]]:
    """Check the complete source schema; fail rather than discard a malformed row."""
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    expected = set(schema["input_columns"])
    validated = []
    for number, row in enumerate(rows, 1):
        if set(row) != expected or any(value is None for value in row.values()):
            raise ValueError(f"Source row {number} does not match the input schema")
        validated.append(dict(row))
    return validated


def _optional(value: str) -> str | None:
    return value or None


def _number(value: str) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"Invalid numeric value: {value!r}") from exc


def _activity(value: str) -> str:
    lower = value.strip().lower()
    if "skat" in lower:
        return "skating"
    if "walk" in lower:
        return "walking"
    if any(part in lower for part in ("commut", "transport", "e-bike")):
        return "transportation"
    return lower.replace(" ", "_") or "unknown"


def normalize_telemetry(
    rows: Sequence[Mapping[str, str]], schema_path: str | Path = DEFAULT_SCHEMA
) -> list[TelemetryRecord]:
    """Create one schema-compliant record per candidate row."""
    rows = validate_records(rows, schema_path)
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    identity_columns = schema["workout_identity"]
    whoop_counts = Counter(tuple(row[column] for column in identity_columns) for row in rows)
    strava_counts = Counter(row["event_id_strava"] for row in rows if row["event_id_strava"])
    mappings = schema["column_mappings"]
    records = []
    for index, row in enumerate(rows, 1):
        strava_id = row["event_id_strava"]
        whoop_count = whoop_counts[tuple(row[column] for column in identity_columns)]
        strava_count = strava_counts[strava_id] if strava_id else 0
        record = TelemetryRecord(
            record_id=f"row_{index:05d}", source_row=index, source="WHOOP",
            sources=["WHOOP"] + (["Strava"] if strava_id else []),
            original_ids={"whoop": _optional(row["event_id_whoop"]), "strava": _optional(strava_id)},
            original_sources={"whoop": _optional(row["entity_type_whoop"]), "strava": _optional(row["entity_type_strava"])},
            activity=_activity(row["activity_name_whoop"]),
            original_activities={"whoop": _optional(row["activity_name_whoop"]), "strava": _optional(row["activity_name_strava"]), "strava_type": _optional(row["activity_type"])},
            timestamp=_optional(row["start_time_whoop"]),
            original_timestamps={"whoop_start": _optional(row["start_time_whoop"]), "whoop_end": _optional(row["end_time"]), "strava_start": _optional(row["start_time_strava"]), "whoop_date": _optional(row["event_date_whoop"]), "strava_date": _optional(row["event_date_strava"])},
            duration_minutes=_number(row["duration_min"]),
            distance_miles=_number(row["metrics.distance_miles"]),
            heart_rate={"mean_bpm": _number(row["metrics.average_hr_bpm_whoop"]), "max_bpm": _number(row["metrics.max_hr_bpm_whoop"]), "resting_bpm": _number(row["metrics.resting_hr_bpm"])},
            hrv={"hrv_ms": _number(row["metrics.hrv_ms"]), "rmssd_ms": None, "sdnn_ms": None, "context": "WHOOP daily recovery; not an activity HRV measurement"},
            join={"likely_match": row["likely_match"].lower() == "true", "start_delta_min": _number(row["start_delta_min"]), "whoop_candidate_count": whoop_count, "strava_candidate_count": strava_count, "ambiguous": whoop_count > 1 or strava_count > 1},
            source_columns=mappings,
            original_units={"duration_minutes": "minutes", "distance_miles": "miles", "heart_rate": "bpm", "hrv": "ms", "start_delta_min": "minutes"},
        )
        records.append(record)
    return records


def save_json(records: Sequence[TelemetryRecord] | QualityReport, path: str | Path) -> None:
    """Replace the output at an explicit path, giving repeat runs identical content."""
    data: Any = records.model_dump() if isinstance(records, QualityReport) else [
        record.model_dump() if isinstance(record, TelemetryRecord) else record for record in records
    ]
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def generate_quality_report(records: Sequence[TelemetryRecord], total_input_rows: int | None = None) -> QualityReport:
    """Report coverage and repeated workout identities without removing candidates."""
    if total_input_rows is None:
        total_input_rows = len(records)
    if total_input_rows != len(records):
        raise ValueError("Every input row must have a normalized record")
    activity_counts = Counter(record.activity for record in records)
    identities = Counter((record.timestamp, record.original_activities.get("whoop"), record.duration_minutes) for record in records)
    return QualityReport(
        total_input_rows=total_input_rows, valid_records=len(records), rejected_records=0,
        missing_physiological_fields={
            "mean_hr": sum(record.heart_rate.mean_bpm is None for record in records),
            "max_hr": sum(record.heart_rate.max_bpm is None for record in records),
            "hrv": sum(record.hrv.hrv_ms is None for record in records),
            "rmssd": sum(record.hrv.rmssd_ms is None for record in records),
            "sdnn": sum(record.hrv.sdnn_ms is None for record in records),
        },
        device_coverage={"WHOOP": sum("WHOOP" in record.sources for record in records), "Strava": sum("Strava" in record.sources for record in records), "Polar": 0},
        activity_coverage=dict(activity_counts),
        duplicate_records=sum(count - 1 for count in identities.values()),
        ambiguous_matches=sum(bool(record.join.get("ambiguous")) for record in records),
        warnings=[
            "Join candidate rows are not unique workouts; deduplicate WHOOP event keys for statistics.",
            "WHOOP HRV is a daily recovery metric, not activity-measured HRV.",
            "No RMSSD or SDNN fields.",
            "No Polar records.",
            "Walking activity present." if activity_counts["walking"] else "No explicit walking activity; walking-versus-skating comparison unavailable.",
        ],
    )
