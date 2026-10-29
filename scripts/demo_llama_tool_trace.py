"""Capture a live, model-directed telemetry lookup with the installed llama.cpp server.

The installed server (build 4404) rejects OpenAI `tools`. This script uses an
application-level JSON tool request: the model selects a named tool, Python
validates and runs it, and the model receives the result in a second turn.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict

from app.schemas import TelemetryRecord

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.getenv("TELEMETRY_JSON", str(ROOT / "data/processed/telemetry.json")))
OUTPUT = ROOT / "reports/llama_tool_trace.json"
QUESTION = "For episode row_01559, what are the measured mean and maximum heart rate and jerk? What do these measurements establish about pain?"
SYSTEM = (
    "You are an evidence analyst. You do not have telemetry until a tool returns it. "
    "For any question needing measurements, respond with only a JSON tool request in this exact shape: "
    '{"action":"call_tool","tool":"get_episode_telemetry","arguments":{"episode_id":"row_01559"}}. '
    "The only permitted tool is get_episode_telemetry. Never invent measurements."
)


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    episode_id: str


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["call_tool"]
    tool: Literal["get_episode_telemetry"]
    arguments: Arguments


def get_episode_telemetry(episode_id: str) -> dict:
    """Read only validated data from the configured telemetry JSON, by record ID."""
    if not episode_id.startswith("row_") or not episode_id[4:].isdigit():
        raise ValueError("Invalid episode ID")
    rows = json.loads(DATA.read_text(encoding="utf-8"))
    matches = [TelemetryRecord.model_validate(row) for row in rows if row.get("record_id") == episode_id]
    if len(matches) != 1:
        raise ValueError(f"Expected one source record for {episode_id}; found {len(matches)}")
    row = matches[0]
    return {
        "record_id": row.record_id,
        "source": row.source,
        "original_activity": row.original_activities.get("whoop"),
        "timestamp": row.original_timestamps.get("whoop_start"),
        "measurements": {
            "mean_hr_bpm": row.heart_rate.mean_bpm,
            "max_hr_bpm": row.heart_rate.max_bpm,
            "jerk": {"value": row.mechanical.jerk_rms_m_s3, "unit": "m/s^3" if row.mechanical.jerk_rms_m_s3 is not None else None},
        },
        "provenance": {
            "source_row": row.source_row,
            "mean_hr_column": row.source_columns.get("heart_rate.mean_bpm"),
            "max_hr_column": row.source_columns.get("heart_rate.max_bpm"),
            "jerk_column": None,
            "join_ambiguous": row.join.get("ambiguous"),
        },
        "limitations": ["No accelerometer jerk measurement in this source record.",
                        "Heart rate does not independently establish pain.",
                        "The Commuting activity label does not identify a vehicle type or episode pain."],
    }


def _completion(messages: list[dict[str, str]], *, tokens: int) -> tuple[dict, dict]:
    endpoint = os.getenv("LLAMA_BASE_URL", "http://localhost:8080/v1").rstrip("/") + "/chat/completions"
    body = {"model": os.getenv("LLAMA_MODEL", "local"), "temperature": 0, "max_tokens": tokens,
            "response_format": {"type": "json_object"}, "messages": messages}
    response = httpx.post(endpoint, json=body, timeout=90)
    response.raise_for_status()
    return {"endpoint": endpoint, "body": body}, response.json()


def run() -> dict:
    first_messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": QUESTION}]
    first_request, first_response = _completion(first_messages, tokens=160)
    first_content = first_response["choices"][0]["message"]["content"]
    tool_call = ToolCall.model_validate_json(first_content)
    tool_result = get_episode_telemetry(tool_call.arguments.episode_id)
    second_messages = first_messages + [
        {"role": "assistant", "content": first_content},
        {"role": "user", "content": "Tool get_episode_telemetry returned: " + json.dumps(tool_result, separators=(",", ":")) +
         "\nNow answer the original question using only this result. Return JSON with answer, evidence_ids, and missing_measurements. "
         "State that jerk is unavailable and heart rate does not prove pain. Do not invent a vehicle type or pain rating."},
    ]
    second_request, second_response = _completion(second_messages, tokens=260)
    second_content = second_response["choices"][0]["message"]["content"]
    candidate = json.loads(second_content)
    answer = str(candidate.get("answer", ""))
    checks = {
        "model_issued_tool_call": tool_call.action == "call_tool" and tool_call.tool == "get_episode_telemetry",
        "tool_read_verified_record": tool_result["record_id"] == tool_call.arguments.episode_id,
        "model_cited_record": tool_result["record_id"] in json.dumps(candidate),
        "answer_uses_measured_hr": all(str(v) in answer for v in (tool_result["measurements"]["mean_hr_bpm"],
                                                                  tool_result["measurements"]["max_hr_bpm"])),
        "missing_jerk_preserved": tool_result["measurements"]["jerk"]["value"] is None,
        "model_acknowledged_missing_jerk": "jerk" in [str(x).lower() for x in candidate.get("missing_measurements", [])],
        "no_unknown_evidence_ids": set(candidate.get("evidence_ids", [])) <= {tool_result["record_id"]},
        "no_pain_proof": "proves pain" not in answer.lower() and "confirms pain" not in answer.lower(),
    }
    trace = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "protocol": "application-level JSON tool call; installed llama.cpp build 4404 rejects OpenAI tools parameter",
        "question": QUESTION,
        "steps": [
            {"step": "llama_tool_request", "request": first_request, "raw_response": first_response, "model_content": first_content},
            {"step": "python_tool_execution", "validated_call": tool_call.model_dump(), "tool_result": tool_result},
            {"step": "llama_follow_up", "request": second_request, "raw_response": second_response, "model_content": second_content},
        ],
        "validation": {"passed": all(checks.values()), "checks": checks},
    }
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(trace, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return trace


if __name__ == "__main__":
    trace = run()
    print(json.dumps({"model_tool_call": trace["steps"][0]["model_content"],
                      "tool_result": trace["steps"][1]["tool_result"],
                      "model_answer": trace["steps"][2]["model_content"],
                      "validation": trace["validation"]}, indent=2))
