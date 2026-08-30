FROM node:20-slim AS node-builder

WORKDIR /build
COPY ux-command-center/ ./ux-command-center/
RUN cd ux-command-center && npm ci && npm run build

FROM python:3.11-slim AS runtime

ARG APP_VERSION=dev
ARG APP_SHA=local
ENV APP_VERSION=${APP_VERSION}
ENV APP_SHA=${APP_SHA}

# libstdc++6 required by duckdb binary; no compiler toolchain needed (wheels are pre-built)
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY config/ ./config/
COPY main.py .
# Public seed packs only (example/empty). Private packs (e.g. private-ray/)
# are gitignored and never reach this build context — they're restored from
# GCS at startup instead (Program OSR WS-3b, src/api/main.py's lifespan).
COPY seeds/ ./seeds/
COPY --from=node-builder /build/output ./output

RUN useradd -m -u 1000 appuser \
    && chown -R appuser:appuser /app \
    && mkdir -p /tmp/data /tmp/sources \
    && chown -R appuser:appuser /tmp/data /tmp/sources

USER appuser

EXPOSE 8080

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
