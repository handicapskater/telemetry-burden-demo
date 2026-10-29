"""Local Llama interpretation and semantic validation of structured evidence."""
from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx
from pydantic import ValidationError

from app.prompts import SYSTEM_PROMPT
from app.schemas import EvidencePayload, PainBurdenReport

NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?%?")
SOURCE_ID = re.compile(r"\brow_\d+\b")
UNSUPPORTED_ASSERTION = re.compile(
    r"\b(?:telemetry|heart rate|hr|hrv)\s+(?:proves?|confirms?|demonstrates?)\s+pain\b|"
    r"\bskating\s+(?:causes?|proves?|means)\s+(?:more\s+)?pain\b|"
    r"\b(?:vehicle|bus|paratransit|vibration)\s+causes?\s+pain\b|"
    r"\bsubject\s+(?:has|experiences|is experiencing)\s+pain\b|"
    r"\bwalking\s+(?:causes|produces)\s+pain\b|"
    r"\b(?:pain|pelvic force|vibration)\s+(?:was|is)\s+(?:proved|confirmed|quantified)\b", re.I,
)
DIAGNOSIS = re.compile(r"\b(?:has|diagnosed with|suffers from)\s+(?:anxiety|ptsd|post-traumatic stress disorder|depression)\b", re.I)


def validate_evidence(candidate: dict[str, Any], *, allowed_numbers: set[str], allowed_ids: set[str]) -> bool:
    """Compatibility check for the original simple answer contract."""
    answer = candidate.get("answer")
    ids = candidate.get("evidence_ids")
    if not isinstance(answer, str) or not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
        return False
    if not set(ids).issubset(allowed_ids) or not set(SOURCE_ID.findall(answer)).issubset(set(ids)):
        return False
    if UNSUPPORTED_ASSERTION.search(answer) or DIAGNOSIS.search(answer):
        return False
    return set(NUMBER.findall(answer)).issubset(allowed_numbers)


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [part for item in value for part in _strings(item)]
    if isinstance(value, dict):
        return [part for item in value.values() for part in _strings(item)]
    return []


def _allowed_numbers(value: Any) -> set[float]:
    numbers: set[float] = set()
    if isinstance(value, bool):
        return numbers
    if isinstance(value, (int, float)):
        numbers.add(float(value))
    elif isinstance(value, dict):
        for item in value.values():
            numbers.update(_allowed_numbers(item))
    elif isinstance(value, list):
        for item in value:
            numbers.update(_allowed_numbers(item))
    return numbers


def validate_pain_report(candidate: dict[str, Any], evidence: EvidencePayload, question: str) -> tuple[PainBurdenReport | None, list[str]]:
    """Pydantic shape plus provenance, numeric, and scientific guardrails."""
    reasons: list[str] = []
    try:
        report = PainBurdenReport.model_validate(candidate)
    except ValidationError as exc:
        return None, [f"Pydantic output validation: {exc.errors()[0]['msg']}"]
    if report.question.strip() != question.strip():
        reasons.append("Question was changed")
    allowed_ids = set(evidence.evidence_ids)
    cited_ids = set(report.observed_physiological_burden.evidence_ids)
    if not cited_ids.issubset(allowed_ids):
        reasons.append("Unknown evidence ID")
    texts = _strings(report.model_dump(exclude={"question"}))
    for source_id in SOURCE_ID.findall(" ".join(texts)):
        if source_id not in allowed_ids or source_id not in cited_ids:
            reasons.append("Unsupported source reference")
            break
    allowed_numbers = _allowed_numbers(evidence.model_dump(exclude={"evidence_ids"}))
    for statement in texts:
        for token in NUMBER.findall(SOURCE_ID.sub("", statement)):
            if float(token.rstrip("%")) not in allowed_numbers:
                reasons.append(f"Unsupported numerical claim: {token}")
                break
        if reasons and reasons[-1].startswith("Unsupported numerical"):
            break
    joined = " ".join(texts)
    if UNSUPPORTED_ASSERTION.search(joined):
        reasons.append("Unsupported pain or causal assertion")
    if DIAGNOSIS.search(joined):
        reasons.append("Unsupported psychological diagnosis")
    critical_missing = ("walking" not in evidence.activity_statistics
                        or not evidence.mechanical_exposure.get("measured")
                        or any("direct pain ratings" in item.lower() for item in evidence.missing_data))
    if critical_missing and report.pain_association.evidence_strength in {"strong", "moderate"}:
        reasons.append("Pain association strength exceeds available evidence")
    if critical_missing and report.pain_association.confidence in {"high", "moderate"}:
        reasons.append("Interpretive confidence exceeds available evidence")
    if ("physiological findings consistent with pain-associated burden" in report.pain_association.interpretation.lower()
            and any("direct pain ratings" in item.lower() for item in evidence.missing_data)):
        reasons.append("Pain-associated phrase requires temporally aligned pain evidence")
    if "walking" not in evidence.activity_statistics:
        observed = " ".join(report.observed_physiological_burden.findings).lower()
        if re.search(r"\bwalking\b.*\b(?:hr|heart rate|bpm|recorded|measured|telemetry)\b|\b(?:hr|heart rate)\b.*\bwalking\b", observed):
            reasons.append("Walking measurement asserted without walking data")
        if not any("walk" in item.lower() for item in report.uncertainty.missing_data):
            reasons.append("Missing walking group not acknowledged")
    if not evidence.mechanical_exposure.get("measured"):
        mech_text = " ".join([report.biomechanical_interpretation.mechanism,
                              *report.biomechanical_interpretation.supporting_evidence,
                              *report.observed_physiological_burden.findings]).lower()
        biomechanics_only = " ".join([report.biomechanical_interpretation.mechanism,
                                        *report.biomechanical_interpretation.supporting_evidence])
        if (re.search(r"\b(?:vibration|acceleration|jerk|shock|pelvic force|pelvic load|impact)\b[^.]{0,35}\b\d+(?:\.\d+)?\b", biomechanics_only, re.I)
                or re.search(r"\b\d+(?:\.\d+)?\s*(?:g|m/s2|m/s\^2|hz|newtons?)\b", biomechanics_only, re.I)):
            reasons.append("Unmeasured mechanical exposure was quantified")
        if re.search(r"(?:measured|recorded|observed|quantified)\s+(?:vehicle\s+)?(?:vibration|acceleration|jerk|shock|pelvic force)", mech_text):
            reasons.append("Mechanical exposure asserted as measured")
        if not any(re.search(r"acceler|vibrat|mechanical|jerk|shock", item, re.I)
                   for item in report.uncertainty.missing_data):
            reasons.append("Missing mechanical measurements not acknowledged")
    if not evidence.mechanical_exposure.get("seat_to_pelvis_transfer_models"):
        if re.search(r"(?:measured|calculated|quantified|observed)\s+pelvic\s+(?:force|load)|pelvic\s+(?:force|load)\s+(?:was|is)\s+\d", joined, re.I):
            reasons.append("Pelvic loading inferred without body-coupling model")
        if not any("coupling" in item.lower() for item in report.uncertainty.missing_data):
            reasons.append("Missing body-coupling model not acknowledged")
    if "hrv" in question.lower() and not re.search(r"cannot|not|insufficient|unable", report.conclusion, re.I):
        reasons.append("HRV discrimination limitation missing")
    if not evidence.hrv_statistics.get("event_specific") and re.search(r"\bhrv\s+(?:may|can|could)\s+distinguish\b", joined, re.I):
        reasons.append("Daily recovery HRV asserted to distinguish pain mechanisms")
    if "walking" not in evidence.activity_statistics and re.search(r"\b(?:dataset|telemetry|data)\s+(?:suggests?|shows?|indicates?)\b.{0,100}\bskating\b.{0,60}\b(?:reduces?|less)\b.{0,30}\bpain\b", joined, re.I):
        reasons.append("Unmeasured skating-versus-walking pain benefit attributed to telemetry")
    if not evidence.mechanical_exposure.get("vehicle_types") and re.search(r"\b(?:recorded|observed|measured)\b.{0,60}\bparatransit\b", joined, re.I):
        reasons.append("Unverified ParaTransit ride asserted as observed")
    if "skat" in question.lower() and "pain" in question.lower():
        if re.search(r"higher\s+hr\s+(?:means|proves|shows)\s+(?:more\s+)?pain", joined, re.I):
            reasons.append("Exercise HR equated with pain")
    if reasons:
        return None, list(dict.fromkeys(reasons))
    return report, []


class LlamaClient:
    """OpenAI-compatible HTTP client for a host-side llama.cpp server."""

    def __init__(self) -> None:
        self.base_url = os.getenv("LLAMA_BASE_URL", "http://localhost:8080/v1").rstrip("/")
        self.model = os.getenv("LLAMA_MODEL", "local")
        self.timeout = float(os.getenv("LLAMA_TIMEOUT_SECONDS", "45"))

    def generate(self, question: str, analysis_results: dict[str, Any]) -> dict[str, Any]:
        evidence = EvidencePayload.model_validate(analysis_results["evidence"])
        absent = []
        if "walking" not in evidence.activity_statistics:
            absent.append("walking telemetry")
        if not evidence.mechanical_exposure.get("measured"):
            absent.append("accelerometer exposure")
        if not evidence.mechanical_exposure.get("seat_to_pelvis_transfer_models"):
            absent.append("body-coupling transfer model")
        if any("direct pain ratings" in item.lower() for item in evidence.missing_data):
            absent.append("synchronized pain ratings")
        confidence_instruction = ("Set pain_association.evidence_strength to limited or insufficient and confidence to low or insufficient. "
                                  if absent else "Calibrate evidence strength and confidence to the supplied measurements without claiming proof of pain. ")
        user_message = (
            f"Question: {question}\n"
            "The subject context below is user-provided history, distinct from measured telemetry. "
            "Use only these computed measurements and missing-data flags. "
            "Give concise, specific explanations and preserve uncertainty.\n"
            f"Structured evidence payload: {evidence.model_dump_json(exclude_none=False)}\n"
            f"For THIS payload these are absent: {', '.join(absent) or 'none of the listed sources'}. "
            + confidence_instruction
            +
            "Do not write the phrase 'Physiological findings consistent with pain-associated burden' from exercise HR alone. "
            "Name each absent input in uncertainty.missing_data. "
            "Never state that this dataset measures a ParaTransit ride or establishes pain. Copy the exact question into the question field."
        )
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            json={"model": self.model, "temperature": 0, "max_tokens": 850,
                  "response_format": {"type": "json_object"},
                  "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                               {"role": "user", "content": user_message}]},
            timeout=self.timeout,
        )
        response.raise_for_status()
        candidate = json.loads(response.json()["choices"][0]["message"]["content"])
        if not isinstance(candidate, dict):
            raise ValueError("local response must be a JSON object")
        return candidate


def local_completion(question: str, evidence: EvidencePayload) -> tuple[dict[str, Any] | None, str]:
    if os.getenv("LLM_PROVIDER", "llama").lower() != "llama":
        return None, "unsupported LLM_PROVIDER; only llama is supported"
    try:
        return LlamaClient().generate(question, {"evidence": evidence.model_dump()}), "local llama.cpp response received"
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        return None, f"local inference unavailable: {type(exc).__name__}"
