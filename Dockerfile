FROM node:20-slim AS node-builder

WORKDIR /build
COPY ux-command-center/ ./ux-command-center/
RUN cd ux-command-center && npm ci && npm run build

# Pre-compress the text assets at build time rather than per-request.
#
# The app was serving a 1.8 MB JavaScript bundle uncompressed — verified against
# the live service, which returned no `content-encoding` even when the request
# offered gzip, br and deflate. That is ~1.34 MB of avoidable transfer on every
# uncached load, and the instance runs on a single CPU shared with the ~17
# parallel API calls the dashboard fires on mount.
#
# Done here, at build time, instead of with Starlette's GZipMiddleware, for two
# reasons. Compressing once during the image build costs the request path
# nothing, which matters more than usual at 1 vCPU. And GZipMiddleware would
# break the sync log: its GZipResponder writes each chunk into a GzipFile and
# never flushes while `more_body` is true, so a `text/event-stream` response
# accumulates in the compressor instead of reaching the browser — the live sync
# progress view would hang rather than stream.
#
# `-k` keeps the original so a client that sends no Accept-Encoding still works.
# The count check is the point of the `test`: if the output path is ever wrong,
# this step would otherwise compress nothing, succeed, and ship the uncompressed
# bundle again — a silent regression of exactly the bug it was added to fix.
RUN find /build/output/ux-command-center -type f \
        \( -name '*.js' -o -name '*.css' -o -name '*.html' -o -name '*.svg' \
           -o -name '*.json' -o -name '*.map' \) \
        -size +1k -exec gzip -9 -k {} \; \
    && GZ=$(find /build/output/ux-command-center -name '*.gz' | wc -l) \
    && echo "pre-compressed ${GZ} static assets" \
    && test "${GZ}" -gt 0

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
