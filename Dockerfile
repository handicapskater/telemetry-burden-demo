FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY scripts ./scripts
COPY data/metadata ./data/metadata
COPY reports/may23_evidence.json ./reports/may23_evidence.json
RUN mkdir -p data/raw data/processed data/analysis \
    && chmod +x scripts/start.sh

EXPOSE 8000 8501
ENTRYPOINT ["./scripts/start.sh"]
CMD ["api"]
