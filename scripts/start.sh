#!/bin/sh
set -eu

case "${1:-api}" in
  api)
    exec python -m uvicorn "${API_APP:-app.api:app}" --host 0.0.0.0 --port 8000
    ;;
  ui)
    exec python -m streamlit run app/ui.py --server.address 0.0.0.0 --server.port 8501 --server.headless true
    ;;
  *)
    echo "Usage: $0 {api|ui}" >&2
    exit 2
    ;;
esac
