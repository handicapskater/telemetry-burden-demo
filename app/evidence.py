"""Build a compact, provenance-aware payload for pain-burden interpretation."""
from __future__ import annotations

import re
from statistics import median
from typing import Any

from app.schemas import EvidencePayload, PainBurdenReport, SubjectContext


def _focus(question: str, activities: dict[str, Any]) -> list[str]:
    q = question.lower()
    selected: list[str] = []
    if re.search(r"walk|skat|mobility|pelvic|pain", q):
        selected.extend(name for name in ("walking", "skating") if name in activities)
    if re.search(r"vehicle|bus|paratransit|vibrat|jolt|transport|commut|seat", q):
        selected.extend(name for name in ("transportation", "horseback_riding") if name in activities)
    if not selected:
        selected = sorted(activities, key=lambda name: activities[name]["count"], reverse=True)[:3]
    return list(dict.fromkeys(selected))


def build_evidence_payload(question: str, metrics: dict[str, Any], records: list[dict[str, Any]] | None = None) -> EvidencePayload:
    """Select only measured statistics; source reports have separate provenance."""
    activities = metrics.get("activities", {})
    selected = _focus(question, activities)
    summary = {name: {
        "workout_count": activities[name]["count"],
        "heart_rate_sample_count": activities[name]["heart_rate_sample_count"],
        "mean_duration_minutes": activities[name]["mean_duration_minutes"],
        "median_duration_minutes": activities[name]["median_duration_minutes"],
        "mean_burden_proxy": activities[name]["mean_burden_proxy"],
        "burden_classification": activities[name]["burden_classification"],
    } for name in selected}
    hr = {name: {
        "sample_count": activities[name]["heart_rate_sample_count"],
        "mean_bpm": activities[name]["mean_heart_rate_bpm"],
        "median_bpm": activities[name]["median_heart_rate_bpm"],
        "max_bpm": activities[name]["max_heart_rate_bpm"],
        "baseline_bpm": None,
    } for name in selected}
    hrv = {"measurement_context": metrics.get("hrv_context"), "event_specific": False,
           "rmssd_ms": None, "sdnn_ms": None,
           "by_activity_date_context": {name: {
               "sample_count": activities[name]["daily_hrv_sample_count"],
               "mean_daily_recovery_hrv_ms": activities[name]["mean_daily_hrv_ms"],
               "median_daily_recovery_hrv_ms": activities[name]["median_daily_hrv_ms"],
           } for name in selected}}
    comparisons = [item for item in metrics.get("comparisons", [])
                   if item["activity_a"] in selected and item["activity_b"] in selected]
    if comparisons:
        hr["observed_comparisons"] = comparisons
    ids = list(dict.fromkeys(record_id for name in selected for record_id in activities[name]["evidence_ids"][:5]))
    measured_mechanical: dict[str, list[float]] = {key: [] for key in (
        "acceleration_rms_m_s2", "jerk_rms_m_s3", "shock_events_per_minute", "vibration_hz")}
    vehicle_types: set[str] = set()
    seating_positions: set[str] = set()
    sensor_locations: set[str] = set()
    coupling_models: set[str] = set()
    reliable_distances: dict[str, list[float]] = {name: [] for name in selected}
    seen_distance: set[tuple[Any, Any, Any]] = set()
    for row in records or []:
        if row.get("activity") not in selected:
            continue
        mechanical_row = row.get("mechanical") or {}
        for key in measured_mechanical:
            value = mechanical_row.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                measured_mechanical[key].append(float(value))
        if row.get("vehicle_type"):
            vehicle_types.add(str(row["vehicle_type"]))
        if row.get("seating_position"):
            seating_positions.add(str(row["seating_position"]))
        if mechanical_row.get("sensor_location"):
            sensor_locations.add(str(mechanical_row["sensor_location"]))
        if mechanical_row.get("seat_to_pelvis_transfer_model"):
            coupling_models.add(str(mechanical_row["seat_to_pelvis_transfer_model"]))
        join = row.get("join") or {}
        identity = (row.get("timestamp"), (row.get("original_activities") or {}).get("whoop"), row.get("duration_minutes"))
        if (identity not in seen_distance and join.get("likely_match") and not join.get("ambiguous")
                and isinstance(row.get("distance_miles"), (int, float))):
            reliable_distances[row["activity"]].append(float(row["distance_miles"]))
            seen_distance.add(identity)
    has_mechanical = any(measured_mechanical.values())
    distance_medians = {name: round(median(values), 2) for name, values in reliable_distances.items() if values}
    missing = ["Walking workout measurements" if "walking" not in activities else "Comparable walking and skating conditions",
               "Activity-aligned HRV, RMSSD, and SDNN", "Direct pain ratings aligned with telemetry",
               "Comparable baseline HR and exertion, medication, and temperature controls"]
    if not has_mechanical:
        missing.append("Acceleration RMS, jerk, shock frequency, and seat-to-pelvis loading measurements")
    if not coupling_models:
        missing.append("Seat-to-pelvis body-coupling transfer model and sensor placement metadata")
    if not vehicle_types or not seating_positions:
        missing.append("Verified ParaTransit vehicle type and seating position")
    if not distance_medians:
        missing.append("Reliable distance comparison: Strava join candidates are ambiguous")
    mechanical = {"measured": has_mechanical,
                  **{key: round(median(values), 3) if values else None for key, values in measured_mechanical.items()},
                  "pelvic_force_newtons": None,
                  "sensor_locations": sorted(sensor_locations),
                  "seat_to_pelvis_transfer_models": sorted(coupling_models),
                  "vehicle_types": sorted(vehicle_types), "seating_positions": sorted(seating_positions),
                  "note": "Mechanical sensor values are descriptive only; seat-to-pelvis force needs a validated model." if has_mechanical else "No mechanical sensor series or verified ParaTransit ride labels in this CSV; commuting is a generic WHOOP category."}
    return EvidencePayload(
        subject_context=SubjectContext(), activity_statistics=summary,
        heart_rate_statistics=hr, hrv_statistics=hrv,
        mechanical_exposure=mechanical,
        functional_performance={"duration_by_activity_minutes": {
            name: activities[name]["median_duration_minutes"] for name in selected},
            "reliable_distance_miles_by_activity": distance_medians,
            "distance_note": "Only unique likely WHOOP–Strava matches are summarized; ambiguous candidates are excluded." if distance_medians else "Ambiguous WHOOP–Strava candidate joins prevent a reliable activity distance comparison.",
            "self_reported_walking_tolerance": "Limited by pain, per subject report; not measured in this CSV"},
        self_reported_pain={"source": "User-provided subject report, separate from wearable telemetry",
                            "walking": "Substantial pain reported",
                            "skating": "Pain relief and improved mobility reported",
                            "vehicle_vibration": "Discomfort with some ParaTransit vibration and jolts reported"},
        missing_data=missing, evidence_ids=ids,
    )


def deterministic_report(question: str, evidence: EvidencePayload) -> PainBurdenReport:
    """Safe structured answer when local model output cannot be accepted."""
    q = question.lower()
    vehicle = bool(re.search(r"paratransit|vehicle|bus|vibrat|jolt|seat", q))
    walking = "walk" in q or "pelvic trauma" in q
    skating = "skat" in q
    hrv_question = "hrv" in q
    activities = evidence.activity_statistics
    findings = []
    for name, values in activities.items():
        hr = evidence.heart_rate_statistics[name]
        if hr["median_bpm"] is not None:
            findings.append(f"{name}: median workout HR {hr['median_bpm']} bpm among {hr['sample_count']} workouts with mean HR; median duration {values['median_duration_minutes']} minutes.")
    if hrv_question:
        findings.append("WHOOP HRV values are daily recovery context and cannot distinguish event-specific mechanical pain from psychological stress.")
    if vehicle:
        mechanism = "Road jolts may transmit vibration through a vehicle seat and body to pelvic structures, potentially provoking pain and autonomic arousal. The CSV does not measure that chain."
    elif walking:
        mechanism = "Repeated walking loads may stress previously injured pelvic structures and provoke pain or protective responses. No walking workouts or impact measurements test this mechanism here."
    elif skating:
        mechanism = "Continuous rolling may reduce some repetitive impacts compared with walking, depending on technique and terrain. No impact measurements or walking comparison test this mechanism here."
    else:
        mechanism = "Pelvic loading, pain anticipation, and autonomic responses may interact, but this dataset does not isolate their contributions."
    if hrv_question:
        conclusion = "Daily recovery HRV cannot distinguish psychological stress from mechanical pain. Both may coexist; neither can be diagnosed from this telemetry."
    elif vehicle:
        conclusion = "The subject reports discomfort in some ParaTransit rides; vehicle vibration is a plausible mechanism, but this dataset has no verified ride or mechanical exposure measurements."
    elif walking:
        conclusion = "The subject reports walking pain. Pelvic loading is a plausible mechanism, but no walking telemetry or mechanical measurements establish a physiological walking burden here."
    elif skating:
        conclusion = "The subject reports skating relief. Skating HR can reflect exercise intensity and does not establish greater pain; no walking comparison or impact data prove pain reduction."
    else:
        conclusion = "The reported pain is relevant context. The measured HR and daily recovery HRV do not independently establish pain or its cause."
    strength = "insufficient" if walking or vehicle or "prove" in q else "limited"
    report = {
        "question": question,
        "observed_physiological_burden": {"findings": findings, "evidence_ids": evidence.evidence_ids},
        "biomechanical_interpretation": {"mechanism": mechanism,
            "supporting_evidence": ["User-provided history and pain reports; not a mechanical measurement"],
            "alternative_explanations": ["Exercise intensity", "Medication, temperature, fitness, and activity context"]},
        "pain_association": {"interpretation": "The self-reported pain is relevant. Telemetry does not isolate a pain-specific autonomic response.",
            "evidence_strength": strength,
            "confidence": "insufficient" if strength == "insufficient" else "low",
            "reasoning": "No synchronized pain ratings, walking comparison, mechanical exposure series, or event-specific HRV are available."},
        "psychological_component": {"potential_mechanisms": ["Anticipatory stress and pain-related vigilance may accompany physical loading"],
            "direct_evidence": [],
            "limitations": ["No validated psychological assessment or synchronized psychological self-report; no diagnosis can be made"]},
        "mobility_aid_implications": {"functional_interpretation": "The subject reports skating improves mobility and reduces painful loading; this functional report is not a measured causal comparison.",
            "supporting_evidence": ["Subject self-report", "Observed skating workout duration when available"],
            "limitations": ["No walking telemetry, impact measurements, or reliable distance comparison"]},
        "uncertainty": {"missing_data": evidence.missing_data,
            "confounders": ["Exercise intensity", "Fitness", "Medication", "Temperature", "Activity and environmental conditions"],
            "causal_limitations": ["Heart rate does not prove pain", "Daily recovery HRV is not event-specific", "Self-report and wearable measurements have different provenance"]},
        "conclusion": conclusion,
    }
    return PainBurdenReport.model_validate(report)


def ground_report(report: PainBurdenReport, question: str, evidence: EvidencePayload) -> PainBurdenReport:
    """Attach Python measurements when Llama leaves measured sections blank."""
    grounded = deterministic_report(question, evidence)
    result = report.model_copy(deep=True)
    if not result.observed_physiological_burden.findings:
        result.observed_physiological_burden.findings = grounded.observed_physiological_burden.findings
    if not result.observed_physiological_burden.evidence_ids:
        result.observed_physiological_burden.evidence_ids = grounded.observed_physiological_burden.evidence_ids
    if (not evidence.mechanical_exposure.get("measured") and
            not re.search(r"\b(may|might|could|plausib|propos|hypoth)\b", result.biomechanical_interpretation.mechanism, re.I)):
        result.biomechanical_interpretation.mechanism = grounded.biomechanical_interpretation.mechanism
    if not result.psychological_component.potential_mechanisms:
        result.psychological_component.potential_mechanisms = grounded.psychological_component.potential_mechanisms
    return result
