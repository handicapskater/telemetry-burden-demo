import json
from copy import deepcopy
from pathlib import Path

from app.episode import build_episode_evidence, build_request, validate_response, _infer


def _real():
    # Four verified source rows are committed so CI needs no private full dataset.
    return json.loads((Path(__file__).resolve().parent / "fixtures/representative_episode_rows.json").read_text())


def _candidate():
    return {
        "episode_id": "row_01559", "summary": "WHOOP records a Commuting workout; vehicle type and pain are unknown.",
        "measured_burden": {"physiological": ["Mean workout HR was 118 bpm (row_01559)."], "mechanical": []},
        "pain_association": {"interpretation": "Pain cannot be established from these measurements.",
                             "supporting_evidence": ["row_01559 shows HR during a workout."],
                             "conflicting_evidence": ["Exercise and other factors can elevate HR."],
                             "alternative_explanations": ["Physical exertion"]},
        "psychological_component": {"interpretation": "Anticipatory stress is hypothetical.", "direct_evidence": [], "hypotheses": ["Stress may affect HR."]},
        "confidence": {"level": "low", "basis": "No episode pain or mechanics", "calibrated_probability": None},
        "limitations": ["No accelerometer or episode-specific pain ratings."],
        "conclusion": "The data show cardiovascular activity, not objectively measured pain."
    }


def test_episode_selection_and_baseline():
    evidence, calc = build_episode_evidence(_real())
    assert evidence["episode"]["episode_id"] == "row_01559"
    assert evidence["episode"]["vehicle_type"] is None
    assert evidence["subject_context"]["pain_reported"] is None
    assert evidence["physiology"]["rmssd_ms"] is None
    assert evidence["mechanics"]["acceleration_rms_g"] is None
    assert evidence["baseline_comparison"]["baseline_evidence_ids"] == ["row_01552", "row_01553", "row_01555"]
    assert calc["mean_hr_above_same_day_resting_bpm"] == 66
    assert calc["daily_recovery_hrv_vs_prior_median_ms"] == -5
    assert calc["sustained_elevated_mechanical_exposure_seconds"] is None
    assert calc["duration_discrepancy_seconds"] == 59


def test_exact_request_is_the_submitted_request(monkeypatch):
    evidence, calc = build_episode_evidence(_real())
    expected = build_request(evidence, calc)
    sent = {}
    class Response:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"choices": [{"message": {"content": json.dumps(_candidate())}}]}
    def post(url, json, timeout):
        sent.update(url=url, body=json)
        return Response()
    monkeypatch.setattr("app.episode.httpx.post", post)
    result = _infer({"evidence": evidence, "calculations": calc, "execution_trace": []})
    assert sent == {"url": expected["endpoint"], "body": expected["body"]}
    assert result["request"] == expected
    assert result["raw_response"]["model_content"] == json.dumps(_candidate())


def test_validation_rejects_unsupported_claims():
    evidence, calc = build_episode_evidence(_real())
    valid = _candidate()
    assert validate_response(valid, evidence, calc)["passed"]
    bad = deepcopy(valid)
    bad["measured_burden"]["physiological"].append("HR was 999 bpm.")
    bad["measured_burden"]["mechanical"].append("Measured acceleration was 2 g.")
    bad["psychological_component"]["interpretation"] = "Subject has anxiety."
    bad["pain_association"]["supporting_evidence"].append("row_99999")
    result = validate_response(bad, evidence, calc)
    assert not result["passed"]
    for name in ("numerical_claims", "missing_mechanics", "no_psychological_diagnosis", "evidence_ids"):
        assert not result["checks"][name]["passed"]
    wrong_metric = deepcopy(valid)
    wrong_metric["measured_burden"]["physiological"] = ["Max HR was 118 bpm."]
    assert not validate_response(wrong_metric, evidence, calc)["checks"]["metric_value_alignment"]["passed"]


def test_confidence_is_not_pain_probability():
    evidence, calc = build_episode_evidence(_real())
    bad = _candidate()
    bad["confidence"]["calibrated_probability"] = 0.9
    assert not validate_response(bad, evidence, calc)["passed"]
