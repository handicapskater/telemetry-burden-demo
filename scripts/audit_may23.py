"""Capture a live, provenance-labelled partial May 23 inference trace."""
from __future__ import annotations

import json
from pathlib import Path

from app.may23 import analyze_may23

REPORTS = Path(__file__).resolve().parents[1] / "reports"


def dump(name: str, value: object) -> None:
    (REPORTS / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    state = analyze_may23()
    REPORTS.mkdir(exist_ok=True)
    evidence, calculations = state["evidence"], state["calculations"]
    request, response, validation = state["request"], state["raw_response"], state["validation"]
    dump("may23_evidence.json", {"evidence": evidence, "calculations": calculations})
    dump("may23_prompt.json", {"initial": request, "retry": state.get("retry_request")})
    dump("may23_response.json", {"initial": response, "retry": state.get("retry_raw_response")})
    dump("may23_validation.json", {"initial": state.get("initial_validation"), "final": validation})
    candidate = state.get("candidate") or {}
    assoc = candidate.get("pain_association") or {}
    lines = [
        "# May 23, 2026 ParaTransit bus: partial inference trace", "",
        "## 1. Episode identity and provenance", "",
        f"Inventory record `{evidence['episode']['mobility_event_id']}` identifies the bus at {evidence['episode']['event_start_local']} local time. "
        f"Session `{evidence['episode']['logical_session_id']}`; seat position `{evidence['episode']['seat_position']}`. "
        "The committed research inventory and frozen reference are available; original raw CSVs and private run outputs are absent.", "",
        "## 2. Raw timestamped telemetry", "",
        "No raw timestamped sensor observations are available locally. The inventory gives one event timestamp and aggregate WHOOP HR, "
        "with source hashes and declared component availability. Sample counts and XYZ values cannot be inspected from the absent CSVs.", "",
        "```json", json.dumps(evidence["episode"], indent=2, ensure_ascii=False), "```", "",
        "## 3. Deterministic calculations and burden summary", "",
        "Duration = inventory event_duration_seconds / 60 = 5573 / 60 = 92.883333 minutes (92:53). "
        "Inventory mean HR is 77 bpm and max HR is 106 bpm. HR change, SDNN change, RMS/jerk distributions, "
        "P90 duration, shock events, and vehicle/seat contrasts cannot be recomputed without source samples and comparison windows. "
        "The frozen reference reports P75 exposure, but this is a provenance-labelled prior result, not this trace's calculation.", "",
        "```json", json.dumps(calculations, indent=2, ensure_ascii=False), "```", "",
        "## 4. Exact structured JSON evidence payload", "", "```json", json.dumps(evidence, indent=2, ensure_ascii=False), "```", "",
        "## 5. Complete Llama system prompt", "", "```text", request["body"]["messages"][0]["content"], "```", "",
        "## 6. Complete Llama user prompt", "", "```text", request["body"]["messages"][1]["content"], "```", "",
        "## 7. Model inference settings", "", "```json", json.dumps({"endpoint": request["endpoint"], "context_size_tokens": request["context_size_tokens"],
            "model": request["body"]["model"], "temperature": request["body"]["temperature"], "max_tokens": request["body"]["max_tokens"],
            "response_format": request["body"]["response_format"]}, indent=2), "```", "",
        "## 8. Actual unedited Llama response", "", "```text", response["model_content"], "```", "",
        *( ["### Rejected initial response and exact retry", "",
            "Initial validation: `FAIL`.", "", "```json", json.dumps(state["initial_validation"], indent=2), "```", "",
            "### Exact retry user prompt", "", "```text", state["retry_request"]["body"]["messages"][1]["content"], "```", "",
            "### Actual unedited retry response", "", "```text", state["retry_raw_response"]["model_content"], "```", ""]
            if state.get("retry_request") else []),
        "## 9. Supporting evidence", "", *[f"- {x}" for x in assoc.get("supporting_evidence", [])], "",
        "## 10. Conflicting evidence", "", *[f"- {x}" for x in assoc.get("conflicting_evidence", [])], "",
        "## 11. Confidence assessment", "", json.dumps(candidate.get("confidence"), ensure_ascii=False), "",
        "## 12. Missing data and limitations", "", *[f"- {x}" for x in evidence["missing"]],
        *[f"- Model: {x}" for x in candidate.get("limitations", [])], "",
        "## 13. Validator results", "", f"Decision: **{'PASS' if validation['passed'] else 'FAIL'}**", "",
        "```json", json.dumps(validation, indent=2, ensure_ascii=False), "```", "",
        "## 14. Final scientific interpretation", "",
        "The event identity, duration and aggregate HR are documented in the committed inventory. The frozen reference reports prolonged "
        "mechanical exposure, but absent sample files prevent independent verification of RMS, jerk, P90 duration, HRV transition, "
        "or short-window HR change. A link to pelvic pain or psychological stress remains a hypothesis because no synchronized pain "
        "or psychological assessment and no seat-to-pelvis transfer measurement are available. Model text is retained unedited even if validation fails.", "",
    ]
    report = REPORTS / "may23_complete_inference_trace.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    print(report.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
