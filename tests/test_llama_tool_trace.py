"""Verify the read-only tool dispatch and the two-turn evidence handoff."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import demo_llama_tool_trace as demo


FIXTURE = Path(__file__).parent / "fixtures/representative_episode_rows.json"


def test_tool_reads_verified_source_and_preserves_missing_jerk(monkeypatch):
    monkeypatch.setattr(demo, "DATA", FIXTURE)
    result = demo.get_episode_telemetry("row_01559")
    assert result["measurements"]["mean_hr_bpm"] == 118
    assert result["measurements"]["max_hr_bpm"] == 149
    assert result["measurements"]["jerk"]["value"] is None
    assert result["provenance"]["mean_hr_column"] == "metrics.average_hr_bpm_whoop"
    with pytest.raises(ValueError):
        demo.get_episode_telemetry("../../secret")


def test_model_tool_call_is_dispatched_and_result_returned(monkeypatch, tmp_path):
    monkeypatch.setattr(demo, "DATA", FIXTURE)
    monkeypatch.setattr(demo, "OUTPUT", tmp_path / "trace.json")
    calls = []

    def completion(messages, *, tokens):
        calls.append(messages)
        if len(calls) == 1:
            content = json.dumps({"action": "call_tool", "tool": "get_episode_telemetry",
                                  "arguments": {"episode_id": "row_01559"}})
        else:
            assert '"mean_hr_bpm":118.0' in messages[-1]["content"]
            assert '"value":null' in messages[-1]["content"]
            content = json.dumps({"answer": "row_01559 recorded 118.0 and 149.0 bpm; this does not establish pain.",
                                  "evidence_ids": ["row_01559"], "missing_measurements": ["jerk"]})
        return {"messages": messages}, {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr(demo, "_completion", completion)
    trace = demo.run()
    assert len(calls) == 2
    assert trace["validation"]["passed"]
    assert (tmp_path / "trace.json").exists()
