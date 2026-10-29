from copy import deepcopy

from pydantic import ValidationError
import pytest

from app.analysis import analyze_records
from app.evidence import build_evidence_payload, deterministic_report
from app.inference import validate_pain_report
from app.schemas import PainBurdenReport
from test_analysis import record


QUESTION = "How could inline skating reduce mechanical pain while maintaining cardiovascular exercise?"


def sample():
    rows = [record("row_1", hr=125)]
    evidence = build_evidence_payload(QUESTION, analyze_records(rows), rows)
    report = deterministic_report(QUESTION, evidence).model_dump()
    return evidence, report


def test_structured_report_validates_and_separates_subject_context_from_measurement():
    evidence, report = sample()
    assert evidence.subject_context.walking_pain_reported is True
    assert "User-provided" in evidence.subject_context.provenance
    assert "walking" not in evidence.activity_statistics
    assert evidence.mechanical_exposure["measured"] is False
    assert evidence.functional_performance["reliable_distance_miles_by_activity"] == {}
    assert PainBurdenReport.model_validate(report).pain_association.confidence == "low"
    assert validate_pain_report(report, evidence, QUESTION)[1] == []
    assert "reports skating relief" in report["conclusion"]
    assert "greater pain" in report["conclusion"] and "does not" in report["conclusion"]
    assert report["psychological_component"]["direct_evidence"] == []


@pytest.mark.parametrize("section,key,text,reason", [
    ("observed_physiological_burden", "findings", "Walking HR was 125 bpm", "Walking measurement"),
    ("observed_physiological_burden", "findings", "Skating HR was 999 bpm", "Unsupported numerical"),
    ("biomechanical_interpretation", "mechanism", "Measured vibration during this ride", "Mechanical exposure"),
    ("psychological_component", "potential_mechanisms", "Subject has anxiety", "psychological diagnosis"),
])
def test_rejects_unsupported_observations(section, key, text, reason):
    evidence, report = sample()
    altered = deepcopy(report)
    if isinstance(altered[section][key], list):
        altered[section][key].append(text)
    else:
        altered[section][key] = text
    accepted, reasons = validate_pain_report(altered, evidence, QUESTION)
    assert accepted is None
    assert any(reason.lower() in item.lower() for item in reasons)


def test_rejects_mode_as_pain_label_and_overconfident_interpretation():
    evidence, report = sample()
    report["conclusion"] = "Skating causes more pain."
    report["pain_association"]["confidence"] = "high"
    accepted, reasons = validate_pain_report(report, evidence, QUESTION)
    assert accepted is None
    assert any("causal assertion" in item for item in reasons)
    assert any("confidence" in item for item in reasons)


def test_rejects_pain_association_phrase_from_exercise_hr_alone():
    evidence, report = sample()
    report["pain_association"]["interpretation"] = "Physiological findings consistent with pain-associated burden"
    accepted, reasons = validate_pain_report(report, evidence, QUESTION)
    assert accepted is None
    assert any("temporally aligned" in item for item in reasons)


def test_missing_evidence_and_invalid_shape_are_rejected():
    evidence, report = sample()
    report["uncertainty"]["missing_data"] = []
    accepted, reasons = validate_pain_report(report, evidence, QUESTION)
    assert accepted is None
    assert any("walking" in item.lower() for item in reasons)
    assert any("mechanical" in item.lower() for item in reasons)
    with pytest.raises(ValidationError):
        PainBurdenReport.model_validate({"question": QUESTION})


def test_rejects_hrv_discrimination_and_unmeasured_comparison_claims():
    evidence, report = sample()
    report["conclusion"] = "Daily HRV may distinguish psychological stress from mechanical pain."
    assert any("HRV" in item for item in validate_pain_report(report, evidence, QUESTION)[1])
    report["conclusion"] = "The dataset suggests skating reduces pain compared with walking."
    assert any("skating-versus-walking" in item for item in validate_pain_report(report, evidence, QUESTION)[1])


def test_rejects_quantified_vibration_without_accelerometer_data():
    evidence, report = sample()
    report["biomechanical_interpretation"]["mechanism"] = "Vehicle vibration was 125 units."
    accepted, reasons = validate_pain_report(report, evidence, QUESTION)
    assert accepted is None
    assert any("mechanical exposure was quantified" in item for item in reasons)


def test_mechanical_metrics_are_only_included_when_supplied():
    rows = [record("row_1")]
    rows[0]["mechanical"] = {"acceleration_rms_m_s2": 0.8}
    evidence = build_evidence_payload(QUESTION, analyze_records(rows), rows)
    assert evidence.mechanical_exposure["measured"] is True
    assert evidence.mechanical_exposure["acceleration_rms_m_s2"] == 0.8
    assert evidence.mechanical_exposure["pelvic_force_newtons"] is None
    assert evidence.mechanical_exposure["seat_to_pelvis_transfer_models"] == []
    assert any("body-coupling" in item for item in evidence.missing_data)
