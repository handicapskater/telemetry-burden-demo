from fastapi.testclient import TestClient

from app import api


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
