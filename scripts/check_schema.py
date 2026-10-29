"""Check committed schema, models, and optional local telemetry artifacts."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import schemas

metadata = json.loads((ROOT / "data/metadata/schema.json").read_text(encoding="utf-8"))
assert metadata["record_model"] == "app.schemas.TelemetryRecord"
assert metadata["input_columns"]
assert set(metadata["column_mappings"].values()) <= set(metadata["input_columns"])
for name in metadata["api_models"]:
    assert hasattr(schemas, name), f"Missing schema model: {name}"

raw = ROOT / "data/raw/whoop_strava_join_candidates.csv"
if raw.exists():
    with raw.open(encoding="utf-8-sig", newline="") as handle:
        columns = csv.DictReader(handle).fieldnames
    assert columns == metadata["input_columns"], "CSV columns differ from committed schema"

processed = ROOT / "data/processed/telemetry.json"
if processed.exists():
    records = json.loads(processed.read_text(encoding="utf-8"))
    assert isinstance(records, list)
    for record in records:
        schemas.TelemetryRecord.model_validate(record)
    print(f"Validated {len(records)} telemetry records")
else:
    print("Schema models and metadata valid; no local processed telemetry")
