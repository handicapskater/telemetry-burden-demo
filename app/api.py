"""FastAPI boundary for the local telemetry workflow."""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile

from app.schemas import (
    AnalysisResponse, AskRequest, AskResponse, HealthResponse, IngestResponse,
    MetricsResponse, QualityReport, TelemetryRecord, TelemetryResponse,
)

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = Path(os.getenv("TELEMETRY_JSON", str(ROOT / "data/processed/telemetry.json")))
QUALITY_PATH = Path(os.getenv("QUALITY_JSON", str(ROOT / "data/metadata/data_quality.json")))
ANALYSIS_PATH = Path(os.getenv("ANALYSIS_JSON", str(ROOT / "data/analysis/burden_results.json")))
app = FastAPI(title="HandicapSkater Physiological Burden Analyzer")


def _records() -> list[TelemetryRecord]:
    if not DATA_PATH.exists():
        raise HTTPException(503, "Telemetry JSON is missing. Ingest a CSV first.")
    try:
        data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        return [TelemetryRecord.model_validate(row) for row in data]
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, f"Telemetry JSON is invalid: {exc}") from exc


def _quality() -> QualityReport | None:
    if not QUALITY_PATH.exists():
        return None
    return QualityReport.model_validate_json(QUALITY_PATH.read_text(encoding="utf-8"))


def _summary(records: list[TelemetryRecord]) -> dict[str, Any]:
    from app.analysis import analyze_records
    return analyze_records([r.model_dump() for r in records])


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    import httpx
    base = os.getenv("LLAMA_BASE_URL", "http://localhost:8080/v1").rstrip("/")
    try:
        llama = httpx.get(base + "/models", timeout=0.5).is_success
    except httpx.HTTPError:
        llama = False
    return HealthResponse(status="ok", records_available=DATA_PATH.exists(), llama_available=llama)


@app.get("/telemetry", response_model=TelemetryResponse)
def telemetry() -> TelemetryResponse:
    records = _records()
    return TelemetryResponse(records=records, total=len(records))


@app.get("/metrics", response_model=MetricsResponse)
def metrics() -> MetricsResponse:
    records = _records()
    return MetricsResponse(metrics={"analysis": _summary(records), "quality": _quality().model_dump() if _quality() else None})


@app.get("/analysis", response_model=AnalysisResponse)
def analysis() -> AnalysisResponse:
    records = _records()
    results = _summary(records)
    ANALYSIS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ANALYSIS_PATH.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    return AnalysisResponse(results=results)


@app.post("/ingest", response_model=IngestResponse)
async def ingest(file: UploadFile = File(...)) -> IngestResponse:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Upload a CSV file.")
    payload = await file.read(25 * 1024 * 1024 + 1)
    if not payload or len(payload) > 25 * 1024 * 1024:
        raise HTTPException(413, "CSV must be nonempty and at most 25 MB.")
    from app.ingestion import load_csv, validate_records, normalize_telemetry, save_json, generate_quality_report
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as temp:
        temp.write(payload)
        temp_path = Path(temp.name)
    try:
        raw = load_csv(temp_path)
        validated = validate_records(raw)
        records = normalize_telemetry(validated)
        quality = generate_quality_report(records)
        if not records:
            raise HTTPException(422, "CSV contains no valid records.")
        save_json(records, DATA_PATH)
        if isinstance(quality, QualityReport):
            report = quality
        else:
            report = QualityReport.model_validate(quality)
        QUALITY_PATH.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        return IngestResponse(quality=report, records_saved=len(records))
    except HTTPException:
        raise
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(422, f"Invalid telemetry CSV: {exc}") from exc
    finally:
        temp_path.unlink(missing_ok=True)


@app.post("/ask", response_model=AskResponse)
def ask(request: AskRequest) -> AskResponse:
    from app.graph import answer_question
    started = time.perf_counter()
    records = _records()
    result = answer_question(request.question, request.session_id, [r.model_dump() for r in records])
    result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return AskResponse.model_validate(result)
