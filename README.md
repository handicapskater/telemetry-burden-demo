# Telemetry application deployment

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

The first check confirms that the CSV headers match the committed schema. The converter writes `data/processed/telemetry.json`, `data/metadata/data_quality.json`, and `data/metadata/schema.json`; the second check validates the converted records. Once the integration branch supplies `app/ingestion.py` and an API route, CSV upload through that route is an alternative to local conversion. Do not commit raw CSVs, generated telemetry, or model files. The checked-in quality report describes the inspected source only; rerun conversion or ingestion for a different export.

## Run on macOS with Docker Desktop

Install or build `llama-server` on the Mac and download a compatible GGUF model to a host path. Start the server in a separate terminal:

```sh
llama-server -m /absolute/path/to/model.gguf --host 0.0.0.0 --port 8080
```

The Mac can use its own acceleration; the Compose stack does not run llama or request a Docker GPU. The server must listen on `0.0.0.0` so Docker Desktop can reach it. Keep the model on the host and outside the repository. Docker Desktop exposes the Mac to containers as `host.docker.internal` ([Docker networking](https://docs.docker.com/desktop/features/networking/networking-how-tos/)); the server flags are documented by [llama.cpp](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).

```sh
cp .env.example .env
docker compose up --build
```

Open the API at `http://localhost:8000/docs` and the UI at `http://localhost:8501`. Compose mounts `./data` into both services so local ingestion and conversion results persist. Set `API_PORT` or `UI_PORT` in `.env` if the host ports are occupied. The default API entry point is `app.api:app`; set `API_APP` in `.env` if integration uses another FastAPI module. The UI entry point is `app/ui.py`. Inside the stack, `LLAMA_BASE_URL` points to `http://host.docker.internal:8080/v1`; `API_BASE_URL` points the UI at `http://api:8000`. These components become runnable after the integration branch adds the API and UI implementations.

## Local checks

With Python 3.11:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/check_schema.py
python -m pytest -v
```

Run pytest after the test branches are integrated. The schema check validates committed model names and column mappings, checks CSV headers when the source is present, and validates each processed record when telemetry is present. CI imports application modules, runs the schema check, and runs pytest when test files exist. CI does not require private CSVs or a llama server.
