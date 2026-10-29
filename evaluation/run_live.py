"""Record five real local llama.cpp outputs and their evidence-validation outcomes."""
from __future__ import annotations

import json
import time
from pathlib import Path

from app.analysis import analyze_records
from app.evidence import build_evidence_payload, deterministic_report, ground_report
from app.inference import LlamaClient, validate_pain_report


ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = [
    "Does higher HR during skating mean skating causes more pain?",
    "How might pelvic trauma affect walking tolerance?",
    "Why might riding in a ParaTransit bus provoke pain without substantial physical exertion?",
    "Can HRV distinguish psychological stress from mechanical pain?",
    "Does the available dataset prove skating reduces pain compared with walking?",
]


def main() -> None:
    records = json.loads((ROOT / "data/processed/telemetry.json").read_text())
    metrics = analyze_records(records)
    outcomes = []
    for question in QUESTIONS:
        evidence = build_evidence_payload(question, metrics, records)
        started = time.perf_counter()
        raw = None
        error = None
        try:
            raw = LlamaClient().generate(question, {"evidence": evidence.model_dump()})
            accepted, reasons = validate_pain_report(raw, evidence, question)
        except Exception as exc:
            accepted, reasons = None, [f"Local inference error: {type(exc).__name__}: {exc}"]
            error = str(exc)
        final = ground_report(accepted, question, evidence) if accepted else deterministic_report(question, evidence)
        outcome = {
            "question": question,
            "latency_seconds": round(time.perf_counter() - started, 2),
            "raw_model_response": raw,
            "validation_reasons": reasons,
            "accepted": accepted is not None,
            "final_report": final.model_dump(),
            "inference_error": error,
        }
        outcomes.append(outcome)
        print(json.dumps({"question": question, "latency_seconds": outcome["latency_seconds"],
                          "accepted": outcome["accepted"], "validation_reasons": reasons,
                          "conclusion": final.conclusion}), flush=True)
    output = ROOT / "evaluation/live_llama_responses.json"
    output.write_text(json.dumps({"model": "local llama.cpp", "questions": outcomes}, indent=2) + "\n")
    print(f"Saved {output}", flush=True)


if __name__ == "__main__":
    main()
