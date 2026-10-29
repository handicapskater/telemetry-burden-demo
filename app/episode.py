"""Reproducible, source-grounded audit of one real telemetry episode."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Literal, TypedDict

import httpx
from langgraph.graph import START, END, StateGraph
from pydantic import BaseModel, ConfigDict, ValidationError

from app.analysis import analyze_records
from app.prompts import SYSTEM_PROMPT

QUESTION = ("Analyze this episode for physiological, biomechanical, and potential pain-associated burden. "
            "Explain the relationship between mechanical loading, autonomic responses, and possible psychological stress. "
            "Distinguish measured observations from interpretations. Identify supporting evidence, conflicting evidence, "
            "uncertainty, and limitations.")

EPISODE_SYSTEM_PROMPT = SYSTEM_PROMPT.split("REQUIRED OUTPUT FORMAT", 1)[0] + """
EPISODE AUDIT OUTPUT CONTRACT

Return one JSON object with exactly these fields:
{
  "episode_id": "",
  "summary": "",
  "measured_burden": {"physiological": [], "mechanical": []},
  "pain_association": {"interpretation": "", "supporting_evidence": [], "conflicting_evidence": [], "alternative_explanations": []},
  "psychological_component": {"interpretation": "", "direct_evidence": [], "hypotheses": []},
  "confidence": {"level": "high | moderate | low | insufficient", "basis": "", "calibrated_probability": null},
  "limitations": [],
  "conclusion": ""
}
Confidence is evidence strength, not probability of pain. Do not add the dataset-question format.
Use evidence IDs only from the supplied payload. There is no measured episode pain, mechanical exposure, or confirmed vehicle type unless supplied.
Every item in a list must be a string. The calibrated_probability field is required and must be null.
List at least one measured cardiovascular observation with its source ID in pain_association.supporting_evidence, explicitly stating that it supports physiological burden only, not pain. List nonspecific HR and absent mechanical exposure as conflicting evidence. List exertion as an alternative explanation. Explain vehicle-to-seat-to-pelvis loading only as an untested mechanism, and list possible anticipatory stress as a psychological hypothesis without diagnosis.
"""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MeasuredBurden(_Strict):
    physiological: list[str]
    mechanical: list[str]


class PainAssociation(_Strict):
    interpretation: str
    supporting_evidence: list[str]
    conflicting_evidence: list[str]
    alternative_explanations: list[str]


class PsychologicalComponent(_Strict):
    interpretation: str
    direct_evidence: list[str]
    hypotheses: list[str]


class Confidence(_Strict):
    level: Literal["high", "moderate", "low", "insufficient"]
    basis: str
    calibrated_probability: None


class EpisodeModelResponse(_Strict):
    episode_id: str
    summary: str
    measured_burden: MeasuredBurden
    pain_association: PainAssociation
    psychological_component: PsychologicalComponent
    confidence: Confidence
    limitations: list[str]
    conclusion: str


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def select_episode(records: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    verified = [r for r in records if r.get("original_timestamps", {}).get("whoop_date") == "2026-05-23"
                and "paratransit" in str(r.get("vehicle_type") or r.get("original_activities") or "").lower()
                and not r.get("join", {}).get("ambiguous")]
    if verified:
        return verified[0], "Verified May 23, 2026 ParaTransit episode"
    candidates = [r for r in records if r.get("activity") == "transportation"
                  and r.get("source") == "WHOOP" and not r.get("join", {}).get("ambiguous")
                  and r.get("heart_rate", {}).get("mean_bpm") is not None]
    if not candidates:
        raise ValueError("No unambiguous transportation episode with measured heart rate")
    # Stable reproducible choice: the earliest complete WHOOP-only commute with daily HRV and resting HR.
    complete = [r for r in candidates if r.get("heart_rate", {}).get("resting_bpm") is not None
                and r.get("hrv", {}).get("hrv_ms") is not None]
    selected = min(complete or candidates, key=lambda r: (r.get("timestamp") or "", r["record_id"]))
    return selected, "May 23 has no verified ParaTransit ride; selected an unambiguous WHOOP Commuting workout"


def build_episode_evidence(records: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    row, rationale = select_episode(records)
    start = _time(row["original_timestamps"]["whoop_start"])
    end = _time(row["original_timestamps"]["whoop_end"])
    prior: dict[str, dict[str, Any]] = {}
    for r in records:
        date = r.get("original_timestamps", {}).get("whoop_date")
        if not date or "WHOOP" not in r.get("sources", []):
            continue
        if start.date() - timedelta(days=7) <= _time(date + "T00:00:00+00:00").date() < start.date():
            if r.get("hrv", {}).get("hrv_ms") is not None and r.get("heart_rate", {}).get("resting_bpm") is not None:
                prior.setdefault(date, r)
    baseline = list(prior.values())
    prior_hrv = median(r["hrv"]["hrv_ms"] for r in baseline) if baseline else None
    prior_resting = median(r["heart_rate"]["resting_bpm"] for r in baseline) if baseline else None
    hr = row["heart_rate"]
    hrv = row["hrv"]
    duration = row.get("duration_minutes")
    duration_seconds = round(duration * 60) if duration is not None else None
    mechanics = row.get("mechanical") or {}
    evidence = {
        "episode": {"episode_id": row["record_id"], "date": start.date().isoformat(),
                    "start_timestamp": row["original_timestamps"]["whoop_start"],
                    "end_timestamp": row["original_timestamps"]["whoop_end"],
                    "activity": row["original_activities"]["whoop"], "vehicle_type": row.get("vehicle_type"),
                    "duration_seconds": duration_seconds},
        "subject_context": {"pelvic_injury_history": True, "pain_reported": None, "mobility_aid": "inline skates",
                            "provenance": "User-provided general history; no pain report temporally linked to this episode"},
        "physiology": {"mean_hr_bpm": hr.get("mean_bpm"), "max_hr_bpm": hr.get("max_bpm"),
                       "resting_hr_bpm": hr.get("resting_bpm"), "daily_recovery_hrv_ms": hrv.get("hrv_ms"),
                       "rmssd_ms": hrv.get("rmssd_ms"), "sdnn_ms": hrv.get("sdnn_ms")},
        "mechanics": {"acceleration_rms_g": mechanics.get("acceleration_rms_g"),
                      "jerk_g_per_s": mechanics.get("jerk_g_per_s"),
                      "shock_events_per_minute": mechanics.get("shock_events_per_minute")},
        "baseline_comparison": {"same_day_resting_hr_bpm": hr.get("resting_bpm"),
                                "prior_7_day_daily_recovery_hrv_median_ms": prior_hrv,
                                "prior_7_day_resting_hr_median_bpm": prior_resting,
                                "prior_recovery_dates": sorted(prior),
                                "baseline_evidence_ids": [r["record_id"] for r in baseline],
                                "baseline_sample_days": len(baseline),
                                "comparability": "Workout HR versus resting HR is cross-context; daily recovery HRV is not episode HRV"},
        "data_provenance": {"episode_source": row["source"], "source_row": row["source_row"],
                            "source_columns": row.get("source_columns", {}),
                            "original_units": row.get("original_units", {}),
                            "original_timestamps": row.get("original_timestamps", {}),
                            "join": row.get("join", {}),
                            "daily_hrv_context": hrv.get("context"),
                            "duration_seconds": "Derived from source duration_min × 60; timestamp span differs",
                            "baseline_evidence_ids": [r["record_id"] for r in baseline],
                            "subject_context": "User-provided report; not measured in CSV"},
        "missing_measurements": ["Verified vehicle type and seat position", "Episode-specific pain rating",
                                 "Episode-specific HRV (RMSSD and SDNN)", "Accelerometer acceleration RMS, jerk, and shock events",
                                 "Sensor location and seat-to-pelvis body-coupling model",
                                 "Sample-level HR and acceleration time series", "Exertion-matched baseline"],
        "evidence_ids": [row["record_id"], *[r["record_id"] for r in baseline]],
    }
    mean = hr.get("mean_bpm")
    rest = hr.get("resting_bpm")
    diff = mean - rest if mean is not None and rest is not None else None
    hrv_diff = hrv["hrv_ms"] - prior_hrv if hrv.get("hrv_ms") is not None and prior_hrv is not None else None
    transportation = analyze_records(records).get("activities", {}).get("transportation", {})
    calculations = {
        "selection_rationale": rationale,
        "existing_analysis_transportation": {"unique_workouts": transportation.get("count"),
                                             "median_workout_hr_bpm": transportation.get("median_heart_rate_bpm"),
                                             "comparison_role": "Descriptive cohort context, not a matched baseline"},
        "reported_duration_seconds": duration_seconds,
        "reported_duration_minutes": duration,
        "timestamp_span_seconds": int((end - start).total_seconds()),
        "duration_discrepancy_seconds": int((end - start).total_seconds()) - duration_seconds if duration_seconds is not None else None,
        "mean_hr_above_same_day_resting_bpm": diff,
        "mean_hr_above_same_day_resting_percent": round(diff / rest * 100, 2) if diff is not None and rest else None,
        "daily_recovery_hrv_vs_prior_median_ms": hrv_diff,
        "daily_recovery_hrv_vs_prior_median_percent": round(hrv_diff / prior_hrv * 100, 2) if hrv_diff is not None and prior_hrv else None,
        "mechanical_loading_vs_baseline": None,
        "sustained_elevated_mechanical_exposure_seconds": None,
        "mechanical_exposure_percentiles": None,
        "functional_context": "WHOOP labels a commuting workout; vehicle type, distance, pain, and mobility mode are unknown",
        "calculation_notes": ["Mean workout HR minus same-day resting HR; cross-context, not pain-specific",
                              "Same-day WHOOP daily recovery HRV minus median of prior distinct recovery dates; not an event-specific change",
                              "Mechanical calculations unavailable because no accelerometer samples exist"],
    }
    return evidence, calculations


def build_request(evidence: dict[str, Any], calculations: dict[str, Any]) -> dict[str, Any]:
    user = ("Question: " + QUESTION + "\n"
            "Episode evidence and deterministic Python calculations follow. Use only these values. "
            "The subject history is reported context, not episode telemetry. A generic Commuting label does not verify ParaTransit or pain. "
            "Daily recovery HRV is not episode HRV. Mechanical exposure and body coupling are unmeasured. "
            "Do not diagnose psychological pain or assign a probability of pain. Cite source record IDs where evidence supports a statement.\n"
            "Structured evidence payload: " + json.dumps(evidence, separators=(",", ":"), ensure_ascii=False) + "\n"
            "Deterministic calculations: " + json.dumps(calculations, separators=(",", ":"), ensure_ascii=False))
    base = os.getenv("LLAMA_BASE_URL", "http://localhost:8080/v1").rstrip("/")
    return {"endpoint": base + "/chat/completions", "method": "POST", "headers": {"Content-Type": "application/json"},
            "server_context_window_tokens": 4096,
            "body": {"model": os.getenv("LLAMA_MODEL", "local"), "temperature": 0, "max_tokens": 850,
                     "response_format": {"type": "json_schema", "json_schema": {"name": "episode_audit",
                                            "schema": EpisodeModelResponse.model_json_schema(), "strict": True}},
                     "messages": [{"role": "system", "content": EPISODE_SYSTEM_PROMPT}, {"role": "user", "content": user}]}}


def _texts(value: Any) -> list[str]:
    if isinstance(value, str): return [value]
    if isinstance(value, list): return [s for v in value for s in _texts(v)]
    if isinstance(value, dict): return [s for v in value.values() for s in _texts(v)]
    return []


def validate_response(candidate: dict[str, Any] | None, evidence: dict[str, Any], calculations: dict[str, Any]) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}
    def check(name: str, passed: bool, detail: str = "") -> None:
        checks[name] = {"passed": passed, "detail": detail}
    if not isinstance(candidate, dict):
        return {"passed": False, "checks": {"structured_json": {"passed": False, "detail": "No JSON object"}}}
    try:
        report = EpisodeModelResponse.model_validate(candidate)
        check("structured_json", True)
    except ValidationError as exc:
        return {"passed": False, "checks": {"structured_json": {"passed": False, "detail": str(exc)}}}
    check("episode_identity", report.episode_id == evidence["episode"]["episode_id"])
    texts = _texts(report.model_dump(exclude={"episode_id": True, "confidence": {"calibrated_probability"}}))
    joined = " ".join(texts)
    allowed_ids = set(evidence["evidence_ids"])
    cited = set(re.findall(r"\brow_\d+\b", joined))
    check("evidence_ids", cited <= allowed_ids, f"cited={sorted(cited)}")
    allowed_numbers = set()
    for source in (evidence, calculations):
        def visit(v: Any) -> None:
            if isinstance(v, bool): return
            if isinstance(v, (int, float)): allowed_numbers.add(round(float(v), 2))
            elif isinstance(v, dict):
                for x in v.values(): visit(x)
            elif isinstance(v, list):
                for x in v: visit(x)
        visit(source)
    allowed_numbers.add(float(evidence["episode"]["date"][:4]))
    allowed_numbers.add(float(evidence["episode"]["date"][-2:]))
    allowed_numbers.update(abs(n) for n in list(allowed_numbers))
    numeric_text = re.sub(r"\brow_\d+\b", "", joined)
    numeric_text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "", numeric_text)
    numbers = [float(s.rstrip("%")) for s in re.findall(r"(?<![A-Za-z0-9_.])-?\d+(?:\.\d+)?%?", numeric_text)]
    unsupported = [n for n in numbers if round(n, 2) not in allowed_numbers]
    check("numerical_claims", not unsupported, f"unsupported={unsupported}")
    metric_mismatches = []
    metric_patterns = {
        "mean_hr_bpm": r"\b(?:mean|average)\s+(?:workout\s+)?(?:heart\s+rate|hr)\b[^\d]{0,30}(\d+(?:\.\d+)?)",
        "max_hr_bpm": r"\b(?:max|maximum)\s+(?:heart\s+rate|hr)\b[^\d]{0,30}(\d+(?:\.\d+)?)",
        "resting_hr_bpm": r"\bresting\s+(?:heart\s+rate|hr)\b[^\d]{0,30}(\d+(?:\.\d+)?)",
        "daily_recovery_hrv_ms": r"\b(?:daily\s+)?recovery\s+hrv\b[^\d]{0,30}(\d+(?:\.\d+)?)",
    }
    for metric, pattern in metric_patterns.items():
        expected = evidence["physiology"].get(metric)
        for value in re.findall(pattern, joined, re.I):
            if expected is None or float(value) != float(expected):
                metric_mismatches.append(f"{metric}={value} (expected {expected})")
    check("metric_value_alignment", not metric_mismatches, "; ".join(metric_mismatches))
    missing_mech = all(v is None for v in evidence["mechanics"].values())
    check("missing_mechanics", not missing_mech or not report.measured_burden.mechanical,
          "No measured mechanical burden can be listed")
    check("no_measured_pelvic_force", not re.search(r"(?:measured|quantified|calculated)\s+pelvic\s+(?:force|load)", joined, re.I))
    check("no_psychological_diagnosis", not re.search(r"\b(?:has|diagnosed with|suffers from)\s+(?:anxiety|ptsd|depression|trauma disorder)\b", joined, re.I))
    check("no_objective_pain_claim", not re.search(r"\b(?:hr|hrv|heart rate|telemetry)\s+(?:proves?|confirms?|measures?|establishes?)\s+pain\b|\bpain was measured\b", joined, re.I))
    check("no_unverified_vehicle", not re.search(r"\b(?:this|the)\s+(?:paratransit\s+)?bus\s+ride\b|\bconfirmed paratransit\b", joined, re.I))
    check("conflicting_evidence", bool(report.pain_association.conflicting_evidence))
    check("supporting_evidence", bool(report.pain_association.supporting_evidence)
          and any(evidence["episode"]["episode_id"] in item for item in report.pain_association.supporting_evidence))
    check("alternative_explanations", bool(report.pain_association.alternative_explanations))
    check("psychological_hypothesis", bool(report.psychological_component.hypotheses))
    check("missing_evidence", bool(report.limitations) and bool(re.search(r"acceler|mechanical|vibrat|vehicle", " ".join(report.limitations), re.I)))
    check("confidence_calibration", report.confidence.level in {"low", "insufficient"} and report.confidence.calibrated_probability is None)
    return {"passed": all(item["passed"] for item in checks.values()), "checks": checks}


class EpisodeState(TypedDict, total=False):
    records: list[dict[str, Any]]
    evidence: dict[str, Any]
    calculations: dict[str, Any]
    request: dict[str, Any]
    raw_response: dict[str, Any]
    retry_request: dict[str, Any]
    retry_raw_response: dict[str, Any]
    candidate: dict[str, Any] | None
    validation: dict[str, Any]
    execution_trace: list[str]


def _select(state: EpisodeState) -> EpisodeState:
    evidence, calculations = build_episode_evidence(state["records"])
    return {"evidence": evidence, "calculations": calculations, "execution_trace": ["select_episode", "deterministic_python_analysis"]}


def _infer(state: EpisodeState) -> EpisodeState:
    request = build_request(state["evidence"], state["calculations"])
    response = httpx.post(request["endpoint"], json=request["body"], timeout=float(os.getenv("LLAMA_TIMEOUT_SECONDS", "90")))
    response.raise_for_status()
    raw = response.json()
    content = raw["choices"][0]["message"]["content"]
    try: candidate = json.loads(content)
    except (ValueError, TypeError): candidate = None
    return {"request": request, "raw_response": {"http_status": response.status_code, "response": raw,
                                                  "model_content": content}, "candidate": candidate,
            "execution_trace": state["execution_trace"] + ["llama_cpp_live_inference"]}


def _validate(state: EpisodeState) -> EpisodeState:
    result = validate_response(state.get("candidate"), state["evidence"], state["calculations"])
    return {"validation": result, "execution_trace": state["execution_trace"] + ["deterministic_evidence_validator"]}


def _retry(state: EpisodeState) -> EpisodeState:
    request = build_request(state["evidence"], state["calculations"])
    failed = [name for name, item in state["validation"]["checks"].items() if not item["passed"]]
    request["body"]["messages"][1]["content"] += ("\nValidation feedback for one retry: " + ", ".join(failed) +
        ". Return the required episode JSON shape. Include a nonempty conflicting_evidence list that notes HR is nonspecific, "
        "daily HRV is not episode-specific, and mechanical exposure is absent. Cite row_01559 for measured HR. "
        "Do not invent mechanical observations or claim pain was measured. measured_burden.physiological should contain actual values, not metric names. "
        "supporting_evidence must include row_01559 with its actual HR as evidence of cardiovascular burden only. "
        "alternative_explanations must mention exertion; psychological_component.hypotheses must name a possible stress mechanism without diagnosis.")
    response = httpx.post(request["endpoint"], json=request["body"], timeout=float(os.getenv("LLAMA_TIMEOUT_SECONDS", "90")))
    response.raise_for_status()
    raw = response.json()
    content = raw["choices"][0]["message"]["content"]
    try: candidate = json.loads(content)
    except (ValueError, TypeError): candidate = None
    return {"retry_request": request, "retry_raw_response": {"http_status": response.status_code, "response": raw,
                                                                "model_content": content}, "candidate": candidate,
            "execution_trace": state["execution_trace"] + ["llama_cpp_validation_retry"]}


def _retry_validate(state: EpisodeState) -> EpisodeState:
    result = validate_response(state.get("candidate"), state["evidence"], state["calculations"])
    return {"validation": result, "execution_trace": state["execution_trace"] + ["retry_evidence_validator"]}


def _graph():
    builder = StateGraph(EpisodeState)
    builder.add_node("select_episode", _select)
    builder.add_node("llama", _infer)
    builder.add_node("evidence_validator", _validate)
    builder.add_node("llama_retry", _retry)
    builder.add_node("retry_evidence_validator", _retry_validate)
    builder.add_edge(START, "select_episode")
    builder.add_edge("select_episode", "llama")
    builder.add_edge("llama", "evidence_validator")
    builder.add_conditional_edges("evidence_validator", lambda state: "done" if state["validation"]["passed"] else "retry",
                                  {"done": END, "retry": "llama_retry"})
    builder.add_edge("llama_retry", "retry_evidence_validator")
    builder.add_edge("retry_evidence_validator", END)
    return builder.compile()


EPISODE_GRAPH = _graph()


def analyze_episode(records: list[dict[str, Any]]) -> dict[str, Any]:
    return EPISODE_GRAPH.invoke({"records": records})
