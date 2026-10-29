"""Run the real episode LangGraph and preserve its request, response, and audit."""
from __future__ import annotations

import json
from pathlib import Path

from app.episode import analyze_episode

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/processed/telemetry.json"
REPORTS = ROOT / "reports"


def _write_json(name: str, value: object) -> None:
    (REPORTS / name).write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def main() -> None:
    records = json.loads(DATA.read_text(encoding="utf-8"))
    state = analyze_episode(records)
    REPORTS.mkdir(exist_ok=True)
    evidence, calculations = state["evidence"], state["calculations"]
    request, response, validation = state["request"], state["raw_response"], state["validation"]
    retry_request, retry_response = state.get("retry_request"), state.get("retry_raw_response")
    _write_json("representative_episode_input.json", evidence)
    _write_json("representative_episode_prompt.json", {"initial": request, "retry": retry_request})
    _write_json("representative_episode_response.json", {"initial": response, "retry": retry_response})
    _write_json("representative_episode_validation.json", validation)
    candidate = state.get("candidate") or {}
    assoc = candidate.get("pain_association") or {}
    conf = candidate.get("confidence") or {}
    lines = [
        "# Representative episode: WHOOP Commuting workout",
        "",
        "## Identity and context",
        "",
        f"- Episode: `{evidence['episode']['episode_id']}`; source row {evidence['data_provenance']['source_row']}; source WHOOP.",
        f"- Date/time: {evidence['episode']['start_timestamp']} to {evidence['episode']['end_timestamp']}.",
        "- May 23, 2026 has no verified ParaTransit ride. This episode is a generic WHOOP Commuting workout; vehicle type and pain during the episode are unknown.",
        "- Pelvic injury and inline-skating mobility context are user-reported; they were not independently verified by the CSV.",
        "",
        "## Source telemetry and provenance",
        "",
        "```json", json.dumps(evidence, indent=2, ensure_ascii=False), "```", "",
        "## Deterministic burden calculations", "",
        "```json", json.dumps(calculations, indent=2, ensure_ascii=False), "```", "",
        "The reported duration is 840 seconds (14 source minutes × 60), while the endpoint timestamp span is 899 seconds; the 59-second discrepancy is retained.",
        "Mean workout HR 118 bpm − same-day resting HR 52 bpm = 66 bpm (126.92% of resting). This is a cross-context comparison, not pain-specific.",
        "Same-day daily recovery HRV 29 ms − median of prior three distinct recovery dates (24, 34, 41 ms) = −5 ms (−14.71%). These daily values do not establish event-specific autonomic change.",
        "Mechanical loading relative to baseline, sustained elevated mechanical exposure, and exposure percentiles are null because accelerometer samples are absent.",
        "",
        "## Exact llama.cpp inference request", "",
        f"Endpoint: `{request['endpoint']}`; model: `{request['body']['model']}`; temperature: {request['body']['temperature']}; maximum output tokens: {request['body']['max_tokens']}; server context window: {request['server_context_window_tokens']} tokens; response format: `{request['body']['response_format']}`.",
        "", "### Exact system message", "", "```text", request["body"]["messages"][0]["content"], "```", "",
        "### Exact user message", "", "```text", request["body"]["messages"][1]["content"], "```", "",
        "## Actual raw Llama response", "", "```json", json.dumps(response, indent=2, ensure_ascii=False), "```", "",
        "## Validation retry", "",
        *( ["### Exact retry system message", "", "```text", retry_request["body"]["messages"][0]["content"], "```", "",
            "### Exact retry user message", "", "```text", retry_request["body"]["messages"][1]["content"], "```", "",
            "### Actual raw retry response", "", "```json", json.dumps(retry_response, indent=2, ensure_ascii=False), "```", ""] if retry_request and retry_response else ["No retry was needed.", ""] ),
        "## Supporting evidence", "", *[f"- {x}" for x in assoc.get("supporting_evidence", [])], "",
        "## Conflicting evidence", "", *[f"- {x}" for x in assoc.get("conflicting_evidence", [])], "",
        "## Alternative explanations", "", *[f"- {x}" for x in assoc.get("alternative_explanations", [])], "",
        "## Confidence and scientific limitations", "",
        f"- Measurement confidence: WHOOP aggregate workout HR and daily recovery values are present in source row; no independently checked sensor accuracy. Source duration and timestamp span disagree by 59 seconds.",
        "- Statistical confidence: no inferential confidence interval or significance test is warranted for one episode and three distinct baseline days.",
        f"- Interpretive confidence from model: `{conf.get('level', 'unavailable')}`. Basis: {conf.get('basis', 'unavailable')}. Calibrated probability: {conf.get('calibrated_probability')!r}.",
        *[f"- {x}" for x in candidate.get("limitations", [])], "",
        "## Deterministic evidence validation", "", "```json", json.dumps(validation, indent=2, ensure_ascii=False), "```", "",
        "A validation failure is retained as an audit finding. The raw model response above is never replaced with a deterministic summary.",
    ]
    (REPORTS / "representative_episode.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"episode_id": evidence["episode"]["episode_id"], "validation_passed": validation["passed"],
                      "report": str(REPORTS / "representative_episode.md")}, indent=2))


if __name__ == "__main__":
    main()
