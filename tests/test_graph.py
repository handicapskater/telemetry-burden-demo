from app.graph import answer_question
from app.inference import LlamaClient, validate_evidence
from test_analysis import record


def test_conditional_routing_and_missing_walking(monkeypatch):
    monkeypatch.setattr("app.graph.local_completion", lambda *_: (None, "local inference unavailable: test"))
    rows = [record("row_1")]
    analysis = answer_question("Compare walking with skating", "walking-test", rows)
    assert analysis["specialist"] == "analysis"
    assert "No walking workouts" in analysis["answer"]
    assert analysis["answer"].startswith("[Deterministic fallback]")
    assert analysis["evidence_ids"] == ["row_1"]
    assert analysis["execution_trace"][0] == "supervisor → analysis_specialist"
    assert "evidence_validator" in analysis["execution_trace"][-1]
    data = answer_question("How many source records?", "data-test", rows)
    assert data["specialist"] == "data"
    assert data["metrics"]["unique_workouts"] == 1
    assert answer_question("What WHOOP measurements are available?", "availability-test", rows)["specialist"] == "data"
    assert answer_question("Do you have Polar or activity RMSSD measurements?", "polar-test", rows)["specialist"] == "data"
    pain = answer_question("Does elevated HR prove pain?", "pain-test", rows)
    assert "cannot independently prove pain" in pain["answer"]
    mobility = answer_question("Why might an alternative mobility aid be beneficial?", "mobility-test", rows)
    assert "No walking observations" in mobility["answer"]


def test_evidence_validation_rejects_invented_claims_and_ids():
    allowed = {"1", "30"}
    ids = {"row_1"}
    assert validate_evidence({"answer": "1 workout, 30 minutes, row_1", "evidence_ids": ["row_1"]}, allowed_numbers=allowed, allowed_ids=ids)
    assert not validate_evidence({"answer": "2 workouts", "evidence_ids": ["row_1"]}, allowed_numbers=allowed, allowed_ids=ids)
    assert not validate_evidence({"answer": "row_2", "evidence_ids": ["row_2"]}, allowed_numbers=allowed, allowed_ids=ids)
    assert not validate_evidence({"answer": "1 workout improves recovery", "evidence_ids": ["row_1"]}, allowed_numbers=allowed, allowed_ids=ids)


def test_graph_rejects_unsupported_local_claim(monkeypatch):
    monkeypatch.setattr("app.graph.local_completion", lambda *_: ({"answer": "HR proves pain", "evidence_ids": []}, "local llama.cpp response received"))
    result = answer_question("Does elevated HR prove pain?", "invalid-local-test", [record("row_1")])
    assert result["answer"].startswith("[Deterministic fallback] No:")
    assert result["execution_trace"][-1] == "evidence_validator: deterministic fallback"


def test_llama_client_generate_uses_local_openai_endpoint(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": '{"answer":"grounded","evidence_ids":[]}'}}]}

    calls = []
    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setenv("LLAMA_BASE_URL", "http://localhost:8080/v1")
    monkeypatch.setattr("app.inference.httpx.post", fake_post)
    candidate = LlamaClient().generate("question", {"draft": "grounded", "evidence_ids": []})
    assert candidate["answer"] == "grounded"
    assert calls[0][0] == "http://localhost:8080/v1/chat/completions"


def test_commuting_alias_uses_computed_comparison_and_accepts_grounded_llama(monkeypatch):
    skating = record("row_1")
    commuting = record("row_2")
    commuting["activity"] = "transportation"
    commuting["original_activities"]["whoop"] = "Commuting"
    commuting["original_timestamps"]["whoop_start"] = "2024-01-02T00:00:00Z"
    commuting["heart_rate"]["mean_bpm"] = 80
    monkeypatch.setattr("app.graph.local_completion", lambda *_: (
        {"answer": "Skating has a higher median heart rate than transportation.",
         "evidence_ids": ["row_1", "row_2"], "limitations": []},
        "local llama.cpp response received"))
    result = answer_question("Compare skating and commuting cardiovascular burden", "alias-test", [skating, commuting])
    assert result["specialist"] == "analysis"
    assert result["metrics"]["comparisons"][0]["sample_size_a"] == 1
    assert result["execution_trace"][-1] == "evidence_validator: accepted"
    assert "Computed evidence:" in result["answer"]
    assert "transportation" in result["answer"]
