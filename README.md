# HandicapSkater Physiological Burden Analyzer

The application uses the committed `app/schemas.py` models and `data/metadata/schema.json` column mapping. WHOOP HRV is daily recovery context, not an activity HRV measurement. The inspected source contains no Polar observations and no explicit walking activity; the application must not invent either.

## Prepare data

The source CSV and generated telemetry are local data and are ignored by Git. Obtain the authorized source export and copy it to the expected name:

```sh
mkdir -p data/raw
cp /path/to/whoop_strava_join_candidates.csv data/raw/whoop_strava_join_candidates.csv
python3 scripts/check_schema.py
python3 scripts/foundation_convert.py
python3 scripts/check_schema.py
```

The first check confirms that the CSV headers match the committed schema. The converter writes `data/processed/telemetry.json`, `data/metadata/data_quality.json`, and `data/metadata/schema.json`; the second check validates the converted records. CSV upload through `/ingest` is an alternative to local conversion. Do not commit raw CSVs, generated telemetry, or model files. The checked-in quality report describes the inspected source only; rerun conversion or ingestion for a different export.

## Run on macOS with Docker Desktop

Use an existing compatible GGUF model on the Mac. Start `llama-server` in a separate terminal:

```sh
llama-server -m /absolute/path/to/model.gguf --host 0.0.0.0 --port 8080
```

The Mac can use its own acceleration; the Compose stack does not run llama or request a Docker GPU. The server must listen on `0.0.0.0` so Docker Desktop can reach it. Keep the model on the host and outside the repository. Docker Desktop exposes the Mac to containers as `host.docker.internal` ([Docker networking](https://docs.docker.com/desktop/features/networking/networking-how-tos/)); the server flags are documented by [llama.cpp](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).

```sh
cp .env.example .env
docker compose up --build
```

Open the API at `http://localhost:8000/docs` and the UI at `http://localhost:8501`. Compose mounts `./data` into both services so local ingestion and conversion results persist. Set `API_PORT` or `UI_PORT` in `.env` if the host ports are occupied. Inside the stack, `LLAMA_BASE_URL` points to `http://host.docker.internal:8080/v1`; `API_BASE_URL` points the UI at `http://api:8000`.

## Local checks

With Python 3.11:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/check_schema.py
python -m pytest -v
```

The schema check validates committed model names and column mappings, checks CSV headers when the source is present, and validates each processed record when telemetry is present. CI imports application modules, runs the schema check, and runs pytest. CI does not require private CSVs or a llama server.

## Interview demonstration

1. Open the dashboard and show the candidate row count, unique WHOOP workout count, device coverage, and missing walking category.
2. Ask "What WHOOP measurements are available?" to show supervisor routing to the data specialist.
3. Ask "Compare skating and commuting cardiovascular burden" to show deterministic statistics and the analysis specialist.
4. Ask "Compare walking and skating heart rates" to show abstention where walking is absent.
5. Ask "Does elevated HR prove pain?" to show the scientific boundary.
6. Expand the execution trace and source record IDs; check `/health` to show local Llama availability.

The source contains 2,601 candidate rows and repeated WHOOP/Strava joins. Statistics use unique WHOOP workout identities. WHOOP HRV is a daily recovery value, so it cannot be treated as an HRV measurement during skating or commuting. Elevated HR or altered HRV does not independently establish pain or a causal benefit of skating.

## Physiological and pain-associated interpretation

The exact local Llama system prompt is in `app/prompts.py` (`SYSTEM_PROMPT`). `app/evidence.py` builds the structured payload from Python statistics and places the user-provided pelvic injury and pain reports in separate `subject_context` and `self_reported_pain` fields. `app/inference.py` validates the JSON response with `PainBurdenReport` from `app/schemas.py`, checks numbers and source IDs, and rejects unsupported pain, diagnosis, or mechanical claims. An invalid or unavailable model response produces a labeled deterministic report.

The LangGraph path is supervisor → data or analysis specialist → biomechanical interpretation → pain/autonomic interpretation → psychological interpretation → local Llama → evidence validator. Python computes measurements. Llama explains possible mechanisms and gives qualitative confidence in the interpretation; it does not label an activity as painful based on its mode or HR.

Accelerometer RMS, jerk, and shocks are included only when supplied with units in normalized JSON. Without sensor placement and a validated seat-to-pelvis transfer model, the app does not infer pelvic force. The current CSV has no accelerometer or body-coupling measurements. Its Strava join candidates also do not provide a reliable distance comparison. Generic WHOOP commuting workouts are not verified ParaTransit rides.

An uploaded CSV may add the optional columns `acceleration_rms_m_s2`, `jerk_rms_m_s3`, `shock_events_per_minute`, `vibration_hz`, `sensor_location`, `seat_to_pelvis_transfer_model`, `vehicle_type`, and `seating_position` to the existing source columns. Missing optional values remain null. An acceleration measurement describes sensor exposure; the app still requires sensor placement and a validated coupling model before discussing pelvic force.

Run the five live model questions and save raw candidates, validation decisions, and final reports:

```sh
PYTHONPATH=. python evaluation/run_live.py
```

The generated `evaluation/live_llama_responses.json` is a local research artifact. Review it before sharing: it includes the subject context and model responses. The dashboard's “Physiological and Pain-Associated Burden” section separates measured burden, proposed biomechanical mechanisms, psychological hypotheses, functional implications, confidence, and missing evidence.

The mechanism descriptions are hypotheses, not results from this subject's CSV. Published experiments have measured [skating impact shock compared with running](https://pubmed.ncbi.nlm.nih.gov/9268965/) and found that [pelvic posture changes seated vibration transmission](https://pubmed.ncbi.nlm.nih.gov/9049667/); neither establishes this subject's skating-versus-walking or ParaTransit loading. An experimental study found [HRV did not quantify pain intensity](https://pubmed.ncbi.nlm.nih.gov/23659489/), reinforcing the app's rule against inferring pain from HRV alone.
