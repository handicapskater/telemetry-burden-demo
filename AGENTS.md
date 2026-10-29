# Worktree ownership

- Integration lead: `main`, shared schema, API, integration, demonstration, GitHub.
- Telemetry Data Engineer: `feature/ingestion`, `app/ingestion.py`, `tests/test_ingestion.py` only.
- Physiological Analytics Engineer: `feature/analysis`, `app/analysis.py`, `app/graph.py`, `app/inference.py`, `tests/test_analysis.py`, `tests/test_graph.py` only.
- UI Engineer: `feature/ui`, `app/ui.py` only.
- Deployment Engineer: `feature/deployment`, `Dockerfile`, `compose.yaml`, `.env.example`, `requirements.txt`, `.github/workflows/ci.yaml`, `scripts/`, `README.md` only.

Each agent commits its branch and reports the commit hash. Only the integration lead merges into main. Follow `app/schemas.py` and `data/metadata/schema.json`. Do not commit raw or processed telemetry. HRV is daily recovery context, not activity HRV. No walking or Polar observations may be invented.
