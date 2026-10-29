from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class HeartRate(BaseModel):
    mean_bpm: float | None = None
    max_bpm: float | None = None
    resting_bpm: float | None = None


class HeartRateVariability(BaseModel):
    hrv_ms: float | None = None
    rmssd_ms: float | None = None
    sdnn_ms: float | None = None
    context: str = "WHOOP daily recovery; not an activity HRV measurement"


class MechanicalMeasurements(BaseModel):
    acceleration_rms_m_s2: float | None = None
    jerk_rms_m_s3: float | None = None
    shock_events_per_minute: float | None = None
    vibration_hz: float | None = None
    sensor_location: str | None = None
    seat_to_pelvis_transfer_model: str | None = None


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
    mechanical: MechanicalMeasurements = Field(default_factory=MechanicalMeasurements)
    vehicle_type: str | None = None
    seating_position: str | None = None
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
    validation_reasons: list[str] = Field(default_factory=list)
    execution_trace: list[str] = Field(default_factory=list)
    latency_ms: float
    report: PainBurdenReport | None = None


class SubjectContext(BaseModel):
    pelvic_injury_history: bool = True
    walking_pain_reported: bool = True
    skating_relief_reported: bool = True
    vehicle_vibration_pain_reported: bool = True
    provenance: str = "User-provided history and self-report; not verified by this telemetry CSV"


class EvidencePayload(BaseModel):
    subject_context: SubjectContext = Field(default_factory=SubjectContext)
    activity_statistics: dict[str, Any] = Field(default_factory=dict)
    heart_rate_statistics: dict[str, Any] = Field(default_factory=dict)
    hrv_statistics: dict[str, Any] = Field(default_factory=dict)
    mechanical_exposure: dict[str, Any] = Field(default_factory=dict)
    functional_performance: dict[str, Any] = Field(default_factory=dict)
    self_reported_pain: dict[str, Any] = Field(default_factory=dict)
    missing_data: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class ObservedPhysiologicalBurden(BaseModel):
    model_config = ConfigDict(extra="forbid")
    findings: list[str]
    evidence_ids: list[str]


class BiomechanicalInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mechanism: str
    supporting_evidence: list[str]
    alternative_explanations: list[str]


class PainAssociation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interpretation: str
    evidence_strength: Literal["strong", "moderate", "limited", "insufficient"]
    confidence: Literal["high", "moderate", "low", "insufficient"]
    reasoning: str


class PsychologicalComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    potential_mechanisms: list[str]
    direct_evidence: list[str]
    limitations: list[str]


class MobilityAidImplications(BaseModel):
    model_config = ConfigDict(extra="forbid")
    functional_interpretation: str
    supporting_evidence: list[str]
    limitations: list[str]


class Uncertainty(BaseModel):
    model_config = ConfigDict(extra="forbid")
    missing_data: list[str]
    confounders: list[str]
    causal_limitations: list[str]


class PainBurdenReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str
    observed_physiological_burden: ObservedPhysiologicalBurden
    biomechanical_interpretation: BiomechanicalInterpretation
    pain_association: PainAssociation
    psychological_component: PsychologicalComponent
    mobility_aid_implications: MobilityAidImplications
    uncertainty: Uncertainty
    conclusion: str


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


AskResponse.model_rebuild()
