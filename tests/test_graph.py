from app.analysis import analyze_records
from app.evidence import build_evidence_payload, deterministic_report
from app.graph import answer_question
from app.inference import LlamaClient
from app.schemas import PainBurdenReport
from test_analysis import record


def test_supervisor_and_stages_preserve_missing_walking(monkeypatch):
    monkeypatch.setattr("app.graph.local_completion", lambda *_: (None, "offline test"))
    rows = [record("row_1")]
    result = answer_question("Compare walking with skating", "walking-test", rows)
    assert result["specialist"] == "analysis"
    assert "No walking workouts" in result["answer"]
    assert "walking" in " ".join(result["report"]["uncertainty"]["missing_data"]).lower()
    assert result["report"]["pain_association"]["confidence"] == "insufficient"
    assert result["execution_trace"][0] == "supervisor → analysis_specialist"
    assert any("biomechanical_interpretation" in step for step in result["execution_trace"])
    assert any("pain_autonomic_interpretation" in step for step in result["execution_trace"])
    assert any("psychological_interpretation" in step for step in result["execution_trace"])
    data = answer_question("What WHOOP measurements are available?", "data-test", rows)
    assert data["specialist"] == "data"
    assert data["metrics"]["unique_workouts"] == 1


def test_llama_receives_subject_context_and_computed_statistics(monkeypatch):
    rows = [record("row_1")]
    question = "Does skating cause pain?"
    evidence = build_evidence_payload(question, analyze_records(rows), rows)
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": deterministic_report(question, evidence).model_dump_json()}}]}

    def fake_post(url, **kwargs):
        calls.append(kwargs["json"])
        return Response()

    monkeypatch.setattr("app.inference.httpx.post", fake_post)
    candidate = LlamaClient().generate(question, {"evidence": evidence.model_dump()})
    PainBurdenReport.model_validate(candidate)
    system, user = calls[0]["messages"]
    assert system["role"] == "system"
    assert "mode being used" in system["content"]
    assert "pelvic_injury_history" in user["content"]
    assert '"walking_pain_reported":true' in user["content"]
    assert '"median_bpm":120.0' in user["content"]
    assert '"workout_count":1' in user["content"]


def test_graph_accepts_valid_structured_report_and_retains_existing_comparison(monkeypatch):
    skating = record("row_1")
    commuting = record("row_2", "transportation", "2024-01-02", 30, 80)

    def grounded(question, evidence):
        return deterministic_report(question, evidence).model_dump(), "local llama.cpp response received"

    monkeypatch.setattr("app.graph.local_completion", grounded)
    result = answer_question("Compare skating and commuting cardiovascular burden", "comparison-test", [skating, commuting])
    assert result["metrics"]["comparisons"][0]["sample_size_a"] == 1
    assert result["report"]["pain_association"]["confidence"] == "low"
    assert result["execution_trace"][-1] == "evidence_validator: accepted"
    assert result["evidence_ids"]


def test_validator_attaches_python_burden_when_llama_omits_findings(monkeypatch):
    def sparse(question, evidence):
        candidate = deterministic_report(question, evidence).model_dump()
        candidate["observed_physiological_burden"] = {"findings": [], "evidence_ids": []}
        candidate["psychological_component"]["potential_mechanisms"] = []
        return candidate, "local llama.cpp response received"

    monkeypatch.setattr("app.graph.local_completion", sparse)
    result = answer_question("Does higher HR during skating mean skating causes more pain?",
                             "sparse-test", [record("row_1")])
    assert result["execution_trace"][-1] == "evidence_validator: accepted"
    assert "Measured burden:" in result["answer"]
    assert "120.0 bpm" in result["answer"]
    assert result["report"]["observed_physiological_burden"]["evidence_ids"] == ["row_1"]
    assert result["report"]["psychological_component"]["potential_mechanisms"]


def test_highest_hr_and_unsupported_question_remain_operational(monkeypatch):
    monkeypatch.setattr("app.graph.local_completion", lambda *_: (None, "offline test"))
    rows = [record("row_1"), record("row_2", "yoga", "2024-01-02", 30, 80)]
    rows[0]["heart_rate"]["max_bpm"] = 150
    rows[1]["heart_rate"]["max_bpm"] = 170
    highest = answer_question("Which activity has the highest HR?", "highest-test", rows)
    assert "Highest observed workout maximum HR" in highest["answer"]
    assert "yoga" in highest["answer"]
    unrelated = answer_question("What is my favorite color?", "unsupported-test", rows)
    assert "telemetry cannot answer this question" in unrelated["answer"]
    assert unrelated["execution_trace"][-2] == "llama: abstention"
