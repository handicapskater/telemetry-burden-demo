from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class HeartRate(BaseModel):
    mean_bpm: float | None = None
    max_bpm: float | None = None
    resting_bpm: float | None = None


class HeartRateVariability(BaseModel):
    hrv_ms: float | None = None
    rmssd_ms: float | None = None
    sdnn_ms: float | None = None
    context: str = "WHOOP daily recovery; not an activity HRV measurement"


class TelemetryRecord(BaseModel):
    record_id: str
    source_row: int
    source: str = "WHOOP"
    sources: list[str] = Field(default_factory=list)
    original_ids: dict[str, str | None] = Field(default_factory=dict)
    original_sources: dict[str, str | None] = Field(default_factory=dict)
    activity: str
    original_activities: dict[str, str | None] = Field(default_factory=dict)
    timestamp: str | None = None
    original_timestamps: dict[str, str | None] = Field(default_factory=dict)
    duration_minutes: float | None = None
    distance_miles: float | None = None
    heart_rate: HeartRate = Field(default_factory=HeartRate)
    hrv: HeartRateVariability = Field(default_factory=HeartRateVariability)
    join: dict[str, Any] = Field(default_factory=dict)
    source_columns: dict[str, str] = Field(default_factory=dict)
    original_units: dict[str, str] = Field(default_factory=dict)


class QualityReport(BaseModel):
    total_input_rows: int
    valid_records: int
    rejected_records: int
    missing_physiological_fields: dict[str, int]
    device_coverage: dict[str, int]
    activity_coverage: dict[str, int]
    duplicate_records: int
    ambiguous_matches: int
    warnings: list[str] = Field(default_factory=list)


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    session_id: str = "default"


class AskResponse(BaseModel):
    answer: str
    specialist: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    execution_trace: list[str] = Field(default_factory=list)
    latency_ms: float


class HealthResponse(BaseModel):
    status: str
    records_available: bool
    llama_available: bool


class TelemetryResponse(BaseModel):
    records: list[TelemetryRecord]
    total: int


class MetricsResponse(BaseModel):
    metrics: dict[str, Any]


class AnalysisResponse(BaseModel):
    results: dict[str, Any]


class IngestResponse(BaseModel):
    quality: QualityReport
    records_saved: int
