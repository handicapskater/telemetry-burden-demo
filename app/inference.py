"""Optional local llama.cpp completion with strict evidence checks."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx


NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?%?")
SOURCE_ID = re.compile(r"\brow_\d+\b")
CAUSAL = re.compile(r"\b(caus(?:e|es|ed|al)|because|due to|leads? to|results? in|improves?|boosts?|prevents?|predicts?|demonstrates?|confirms?|diagnoses?|signifies?|indicates?)\b", re.I)


def validate_evidence(candidate: dict[str, Any], *, allowed_numbers: set[str], allowed_ids: set[str]) -> bool:
    """Reject invented numbers, row IDs, or ungrounded evidence lists."""
    answer = candidate.get("answer")
    ids = candidate.get("evidence_ids")
    if not isinstance(answer, str) or not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
        return False
    if not isinstance(candidate.get("limitations", []), list) or not all(isinstance(item, str) for item in candidate.get("limitations", [])):
        return False
    if not set(ids).issubset(allowed_ids):
        return False
    if not set(SOURCE_ID.findall(answer)).issubset(set(ids)):
        return False
    if CAUSAL.search(answer):
        return False
    return set(NUMBER.findall(answer)).issubset(allowed_numbers)


class LlamaClient:
    """OpenAI-compatible HTTP client for the local llama.cpp server."""

    def __init__(self) -> None:
        self.base_url = os.getenv("LLAMA_BASE_URL", "http://localhost:8080/v1").rstrip("/")
        self.model = os.getenv("LLAMA_MODEL", "local")
        self.timeout = float(os.getenv("LLAMA_TIMEOUT_SECONDS", "8"))

    def generate(self, question: str, analysis_results: dict[str, Any]) -> dict[str, Any]:
        draft = analysis_results["draft"]
        evidence_ids = analysis_results["evidence_ids"]
        prompt = (
            "Rewrite the supplied grounded answer for clarity. Return only JSON with "
            "answer (string), evidence_ids (array of strings), limitations (array of strings). "
            "Do not introduce numerical claims, source IDs, or causal claims. Preserve all scientific caveats.\n"
            f"Question: {question}\nGrounded answer: {draft}\nAllowed evidence IDs: {json.dumps(evidence_ids)}"
        )
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            json={"model": self.model, "temperature": 0, "messages": [{"role": "user", "content": prompt}]},
            timeout=self.timeout,
        )
        response.raise_for_status()
        candidate = json.loads(response.json()["choices"][0]["message"]["content"])
        if not isinstance(candidate, dict):
            raise ValueError("local response must be a JSON object")
        return candidate


def local_completion(question: str, draft: str, evidence_ids: list[str]) -> tuple[dict[str, Any] | None, str]:
    """Return an unvalidated local candidate or a reason for fallback."""
    if os.getenv("LLM_PROVIDER", "llama").lower() != "llama":
        return None, "unsupported LLM_PROVIDER; only llama is supported"
    try:
        return LlamaClient().generate(question, {"draft": draft, "evidence_ids": evidence_ids}), "local llama.cpp response received"
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        return None, f"local inference unavailable: {type(exc).__name__}"
