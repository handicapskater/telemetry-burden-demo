"""Source and validator checks for the May 23 partial trace."""
from app.may23 import evidence_and_calculations, validate


def test_may23_provenance_and_unavailable_raw_metrics():
    evidence, calculations = evidence_and_calculations()
    assert evidence["episode"]["event_date_local"] == "2026-05-23"
    assert evidence["episode"]["vehicle_type"] == "bus"
    assert evidence["timing_audit_metadata"]["acc_rows"] == 1126728
    assert not any(evidence["source_files"]["raw_file_available"].values())
    assert calculations["duration_clock"] == "92:53"
    assert calculations["hr_baseline_delta_bpm"] is None
    assert calculations["mechanical_exposure_percentiles"] is None


def test_validator_rejects_invented_measurement():
    evidence, _ = evidence_and_calculations()
    source_id = evidence["episode"]["mobility_event_id"]
    candidate = {
        "episode_id": source_id, "summary": "Inventory aggregate HR is 77 bpm.",
        "measured_burden": {"physiological": ["Mean HR: 999 bpm"], "mechanical": []},
        "pain_association": {"interpretation": "Pain is unmeasured", "supporting_evidence": [source_id],
                             "conflicting_evidence": ["No synchronized pain rating"], "alternative_explanations": ["Exertion"]},
        "psychological_component": {"interpretation": "Uncertain", "direct_evidence": [], "hypotheses": []},
        "confidence": {"level": "insufficient", "basis": "No raw samples", "calibrated_probability": None},
        "limitations": ["Raw samples and pain rating absent"], "conclusion": "Pain cannot be determined."
    }
    result = validate(candidate, evidence)
    assert not result["passed"]
    assert not result["checks"]["numerical_claims"]["passed"]
    assert not result["checks"]["metric_alignment"]["passed"]
