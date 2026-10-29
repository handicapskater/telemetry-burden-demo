"""Provenance-labelled May 23 partial trace using the existing LangGraph/Llama stack."""
from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path
from typing import Any, TypedDict

import httpx
from langgraph.graph import START, END, StateGraph

from app.episode import EPISODE_SYSTEM_PROMPT, EpisodeModelResponse

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = Path(os.getenv("HANDICAPSKATER_RESEARCH", str(Path.home() / "repos/hs-portal-release")))
INVENTORY = RESEARCH / "analysis/transportation_exposure/TRANSPORTATION_SESSION_INVENTORY.csv"
REFERENCE = RESEARCH / "analysis/transport_burden_01/REFERENCE.json"
TIMING = RESEARCH / "analysis/transportation_exposure/timing_forensics_20260919/SESSION_CLOCK_AUDIT.csv"
QUESTION = ("Analyze the May 23 ParaTransit bus episode. Explain measured physiological and mechanical burden, "
            "possible pelvic pain mechanisms, autonomic responses, and psychological stress. Identify supporting "
            "evidence, conflicting evidence, alternative explanations, uncertainty, and confidence. "
            "Distinguish observations from hypotheses.")


def evidence_and_calculations() -> tuple[dict[str, Any], dict[str, Any]]:
    if not INVENTORY.is_file() or not REFERENCE.is_file():
        # The audit artifact is a committed, provenance-labelled evidence snapshot for the Docker demo.
        snapshot = ROOT / "reports/may23_evidence.json"
        saved = json.loads(snapshot.read_text(encoding="utf-8"))
        return saved["evidence"], saved["calculations"]
    with INVENTORY.open(newline="", encoding="utf-8") as stream:
        matches = [r for r in csv.DictReader(stream) if r["logical_session_id"] == "ParaTransit_Bus_2026-05-23T210816"]
    if len(matches) != 1:
        raise ValueError(f"Expected one May 23 inventory row; found {len(matches)}")
    row = matches[0]
    ref = json.loads(REFERENCE.read_text(encoding="utf-8"))
    with TIMING.open(newline="", encoding="utf-8") as stream:
        timing_matches = [r for r in csv.DictReader(stream) if r["logical_session_id"] == row["logical_session_id"]]
    if len(timing_matches) != 1:
        raise ValueError("May 23 timing audit row unavailable")
    timing = timing_matches[0]
    session = row["logical_session_id"]
    raw_dir = RESEARCH / "data/raw/kubios/archive/2026-07-31/mobility_source_process_20260731T192421Z_f1a2eaf0"
    raw_files = {kind: str(raw_dir / f"{session}_{kind}.csv") for kind in ("Acc", "RRI", "ECG", "Markers")}
    available = {kind: Path(path).is_file() for kind, path in raw_files.items()}
    if any(available.values()):
        raise ValueError("Raw source files found: this partial-summary workflow must be upgraded to recompute from raw samples")
    fields = ("mobility_event_id", "logical_session_id", "raw_event_label", "event_date_local", "event_start_local",
              "event_duration_seconds", "vehicle_type", "provider_service", "quality_status", "seat_position",
              "seat_authority", "whoop_average_hr_bpm", "whoop_max_hr_bpm", "has_acc", "has_rri", "has_ecg",
              "independent_pain_rating_available", "pain_annotation_type", "acc_source_hash", "rri_source_hash",
              "ecg_source_hash", "markers_source_hash", "estimated_sampling_rate_hz", "source_acc_unit",
              "normalized_acc_unit", "timing_alignment_status")
    identity = {key: row[key] for key in fields}
    duration = int(row["event_duration_seconds"])
    evidence = {
        "status": "PARTIAL: original sample files and private frozen run outputs unavailable locally",
        "episode": identity,
        "source_record_ids": [row["mobility_event_id"], row["kubios_session_id"]],
        "source_files": {"inventory": str(INVENTORY), "reference": str(REFERENCE), "raw_expected": raw_files,
                         "raw_file_available": available},
        "timing_audit_metadata": {"source": str(TIMING), "status": "reported row counts; raw samples not inspected",
                                  "acc_rows": int(timing["acc_rows"]), "rri_rows": int(timing["rri_rows"]),
                                  "ecg_rows": int(timing["ecg_rows"]), "markers_rows": int(timing["markers_rows"]),
                                  "acc_start": timing["acc_start"], "rri_start": timing["rri_start"],
                                  "ecg_start": timing["ecg_start"]},
        "reported_derived_findings": {
            "provenance": "Frozen descriptive reference headline; values cannot be independently recomputed here",
            "reference_run_id": ref["reference_run_id"],
            "run_manifest_sha256": ref["run_manifest_sha256"],
            "may23_headline_verbatim": ref["headline_findings"]["2026-05-23_bus"],
            "cohort": ref["headline_findings"]["population"],
            "duration_reference_minutes": ref["headline_findings"]["duration_reference_minutes"]},
        "missing": ["Raw timestamped ACC X/Y/Z, RRI, ECG and marker observations", "Original minute-level RMS/jerk/shock distributions and P90 thresholds",
                    "Raw windows behind the reported 0.2169 g, 12.94 g/s, 22-minute P90, +54.25 ms SDNN and +10.09 bpm HR figures",
                    "Same-context HR and HRV baseline windows and full calculation code/results", "Governed seat position", "Synchronized pain rating or psychological assessment", "Seat-to-pelvis transfer model"],
        "confounders": ["Physical activity and posture", "Respiration and ectopic beats", "Medication, temperature and stress", "Sensor placement and body coupling"],
    }
    calculations = {
        "method": "Python arithmetic on committed inventory fields only; no sample-level reconstruction",
        "duration_seconds": duration,
        "duration_minutes": duration / 60,
        "duration_clock": f"{duration // 60}:{duration % 60:02d}",
        "duration_formula": "event_duration_seconds / 60",
        "mean_hr_bpm": int(row["whoop_average_hr_bpm"]),
        "max_hr_bpm": int(row["whoop_max_hr_bpm"]),
        "hr_baseline_delta_bpm": None,
        "hrv_baseline_delta_ms": None,
        "rms_distribution_g": None,
        "jerk_distribution_g_per_s": None,
        "mechanical_exposure_percentiles": None,
        "sustained_high_burden_minutes": None,
        "vehicle_seat_comparison": None,
        "reason": "The source sample series, comparable baseline windows, and frozen private run outputs are absent; reference headlines are not raw measurements",
    }
    return evidence, calculations


def request_for(evidence: dict[str, Any], calculations: dict[str, Any]) -> dict[str, Any]:
    user = (f"Question: {QUESTION}\n"
            "This is a PARTIAL provenance trace. The original sample files and frozen run outputs are absent. "
            "The inventory verifies the bus identity and aggregate WHOOP HR. The frozen reference reports derived exposure figures, "
            "but they cannot be recomputed here. Do not describe absent raw samples, P90 duration, SDNN change or short-window HR change "
            "as independently validated. Distinguish reference-reported findings from observed inventory fields. "
            "No episode pain rating, psychological assessment, governed seat or seat-to-pelvis model is available. "
            "Use only actual source_record_ids. Confidence must be low or insufficient; probability must be null.\n"
            "Cite the inventory mobility_event_id in supporting_evidence for event identity, duration and aggregate HR. "
            "Describe the frozen reference headline only as a reported derived finding.\n"
            "Structured JSON evidence: " + json.dumps(evidence, ensure_ascii=False, separators=(",", ":")) + "\n"
            "Deterministic Python calculations: " + json.dumps(calculations, ensure_ascii=False, separators=(",", ":")))
    return {"endpoint": os.getenv("LLAMA_BASE_URL", "http://localhost:8080/v1").rstrip("/") + "/chat/completions",
            "context_size_tokens": 4096,
            "body": {"model": os.getenv("LLAMA_MODEL", "local"), "temperature": 0, "max_tokens": 850,
                     "response_format": {"type": "json_schema", "json_schema": {"name": "episode_audit", "schema": EpisodeModelResponse.model_json_schema(), "strict": True}},
                     "messages": [{"role": "system", "content": EPISODE_SYSTEM_PROMPT}, {"role": "user", "content": user}]}}


def validate(candidate: Any, evidence: dict[str, Any]) -> dict[str, Any]:
    checks = {}
    def put(name: str, ok: bool, detail: str = "") -> None: checks[name] = {"passed": ok, "detail": detail}
    try:
        report = EpisodeModelResponse.model_validate(candidate)
        put("pydantic", True)
    except Exception as exc:
        return {"passed": False, "checks": {"pydantic": {"passed": False, "detail": str(exc)}}}
    text = json.dumps(report.model_dump(), ensure_ascii=False)
    episode = evidence["episode"]
    put("identity", report.episode_id == episode["mobility_event_id"])
    ids = set(re.findall(r"\b(?:mev|nr)_[a-f0-9]{64}\b", text))
    put("evidence_ids", ids <= set(evidence["source_record_ids"]), str(sorted(ids)))
    put("supporting_evidence", bool(report.pain_association.supporting_evidence) and
        evidence["episode"]["mobility_event_id"] in " ".join(report.pain_association.supporting_evidence))
    put("conflicting_evidence", bool(report.pain_association.conflicting_evidence))
    put("missing_data", bool(report.limitations) and bool(re.search("raw|sample|baseline|rating", " ".join(report.limitations), re.I)))
    put("confidence", report.confidence.level in ("low", "insufficient") and report.confidence.calibrated_probability is None)
    put("no_measured_pain", not re.search(r"(?:telemetry|HR|HRV) (?:proves?|measures?|confirms?) pain|pain was measured", text, re.I))
    put("no_diagnosis", not re.search(r"(?:has|diagnosed with) (?:anxiety|PTSD|depression)", text, re.I))
    put("no_pelvic_force", not re.search(r"(?:measured|calculated|quantified) pelvic (?:force|load)", text, re.I))
    put("no_raw_claim", not re.search(r"(?:raw samples|raw telemetry) (?:show|prove|confirm)", text, re.I))
    hr = evidence["episode"]
    mismatches = []
    for pattern, expected in ((r"(?:mean|average)\s+(?:heart rate|HR)\s*:?\s*(\d+(?:\.\d+)?)", hr["whoop_average_hr_bpm"]),
                              (r"(?:max|maximum)\s+(?:heart rate|HR)\s*:?\s*(\d+(?:\.\d+)?)", hr["whoop_max_hr_bpm"])):
        mismatches.extend(value for value in re.findall(pattern, text, re.I) if float(value) != float(expected))
    put("metric_alignment", not mismatches, f"mismatched={mismatches}")
    allowed_numbers = {3., 77., 106., 5573., 92., 53., 92.88333333333334, 59., 3888., 2026., 5., 23., 0., 75., 90., 62.8833, 67.7, 90.8, 38., 0.2169, 12.94, 22., 54.25, 10.09}
    allowed_numbers.update(float(value) for key, value in evidence["timing_audit_metadata"].items() if isinstance(value, int))
    number_text = re.sub(r"\b(?:mev|nr)_[a-f0-9]{64}\b|\b\d{4}-\d{2}-\d{2}(?:T\d\d:\d\d:\d\d)?\b", "", text)
    nums = [float(n) for n in re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?", number_text)]
    unsupported = [n for n in nums if n not in allowed_numbers]
    put("numerical_claims", not unsupported, f"unsupported={unsupported}")
    return {"passed": all(v["passed"] for v in checks.values()), "checks": checks}


class State(TypedDict, total=False):
    evidence: dict[str, Any]
    calculations: dict[str, Any]
    request: dict[str, Any]
    raw_response: dict[str, Any]
    candidate: dict[str, Any] | None
    validation: dict[str, Any]
    initial_validation: dict[str, Any]
    retry_request: dict[str, Any]
    retry_raw_response: dict[str, Any]
    execution_trace: list[str]


def _source(_: State) -> State:
    evidence, calculations = evidence_and_calculations()
    return {"evidence": evidence, "calculations": calculations, "execution_trace": ["source_inventory", "deterministic_partial_analysis"]}


def _infer(state: State) -> State:
    request = request_for(state["evidence"], state["calculations"])
    response = httpx.post(request["endpoint"], json=request["body"], timeout=float(os.getenv("LLAMA_TIMEOUT_SECONDS", "120")))
    response.raise_for_status()
    raw = response.json()
    content = raw["choices"][0]["message"]["content"]
    try: candidate = json.loads(content)
    except (ValueError, TypeError): candidate = None
    return {"request": request, "raw_response": {"http_status": response.status_code, "response": raw, "model_content": content},
            "candidate": candidate, "execution_trace": state["execution_trace"] + ["live_llama_inference"]}


def _validate(state: State) -> State:
    result = validate(state.get("candidate"), state["evidence"])
    return {"validation": result, "initial_validation": result,
            "execution_trace": state["execution_trace"] + ["pydantic_evidence_validation"]}


def _retry(state: State) -> State:
    request = request_for(state["evidence"], state["calculations"])
    record_id = state["evidence"]["episode"]["mobility_event_id"]
    failed = [k for k, v in state["validation"]["checks"].items() if not v["passed"]]
    request["body"]["messages"][1]["content"] += (
        "\nValidation retry. Failed checks: " + ", ".join(failed) + ". "
        f"Set episode_id exactly to {record_id}. Cite this same ID in pain_association.supporting_evidence "
        "for the inventory's identity, duration and aggregate HR, while saying these do not establish pain. "
        "The source label says 3 people; this number is supported. Return all schema fields exactly.")
    response = httpx.post(request["endpoint"], json=request["body"], timeout=float(os.getenv("LLAMA_TIMEOUT_SECONDS", "120")))
    response.raise_for_status()
    raw = response.json()
    content = raw["choices"][0]["message"]["content"]
    try: candidate = json.loads(content)
    except (ValueError, TypeError): candidate = None
    return {"retry_request": request, "retry_raw_response": {"http_status": response.status_code, "response": raw, "model_content": content},
            "candidate": candidate, "execution_trace": state["execution_trace"] + ["live_llama_validation_retry"]}


def _retry_validate(state: State) -> State:
    return {"validation": validate(state.get("candidate"), state["evidence"]),
            "execution_trace": state["execution_trace"] + ["retry_pydantic_evidence_validation"]}


builder = StateGraph(State)
builder.add_node("source", _source)
builder.add_node("llama", _infer)
builder.add_node("validate", _validate)
builder.add_node("retry", _retry)
builder.add_node("retry_validate", _retry_validate)
builder.add_edge(START, "source")
builder.add_edge("source", "llama")
builder.add_edge("llama", "validate")
builder.add_conditional_edges("validate", lambda state: "done" if state["validation"]["passed"] else "retry",
                              {"done": END, "retry": "retry"})
builder.add_edge("retry", "retry_validate")
builder.add_edge("retry_validate", END)
MAY23_GRAPH = builder.compile()


def analyze_may23() -> State:
    return MAY23_GRAPH.invoke({})
