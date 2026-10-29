from fastapi.testclient import TestClient

from app import api
from app.schemas import PainBurdenReport, TelemetryRecord


def test_missing_json_reports_actionable_error(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DATA_PATH", tmp_path / "missing.json")
    response = TestClient(api.app).get("/telemetry")
    assert response.status_code == 503
    assert "Ingest a CSV" in response.json()["detail"]


def test_upload_rejects_non_csv_and_empty_file():
    client = TestClient(api.app)
    wrong_type = client.post("/ingest", files={"file": ("notes.txt", b"content", "text/plain")})
    assert wrong_type.status_code == 400
    empty = client.post("/ingest", files={"file": ("telemetry.csv", b"", "text/csv")})
    assert empty.status_code == 413


def test_ask_returns_pydantic_validated_pain_report(monkeypatch, tmp_path):
    record = TelemetryRecord(record_id="row_00001", source_row=1, activity="skating",
                             timestamp="2024-01-01T12:00:00Z", duration_minutes=30,
                             heart_rate={"mean_bpm": 120, "max_bpm": 150})
    path = tmp_path / "telemetry.json"
    path.write_text("[" + record.model_dump_json() + "]")
    monkeypatch.setattr(api, "DATA_PATH", path)
    monkeypatch.setattr("app.graph.local_completion", lambda *_: (None, "offline test"))
    response = TestClient(api.app).post("/ask", json={
        "question": "Does higher HR during skating mean skating causes more pain?",
        "session_id": "api-pain-test"})
    assert response.status_code == 200
    report = PainBurdenReport.model_validate(response.json()["report"])
    assert report.pain_association.confidence in {"low", "insufficient"}
    assert "does not establish greater pain" in report.conclusion
