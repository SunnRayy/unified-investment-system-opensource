#!/usr/bin/env bash
#
# setup-cloud-scheduler.sh — Scaffolding for scheduled IBKR Flex auto-fetch.
#
# Creates a Cloud Scheduler HTTP job that periodically POSTs to the Huinsight
# cloud-reachable fetch endpoint so the IBKR Flex report refreshes without a
# manual upload. This is the deferred "Cloud Scheduler infra" item from
# Workstream C (V6.5.0) — the fetch *endpoint* already ships; this wires the
# scheduled *trigger*.
#
# STATUS: scaffolding. Review docs/deployment/cloud-scheduler-ibkr.md before
# running — the AUTH section has one decision you must make (OIDC vs bearer).
#
# Prerequisites (owner-supplied):
#   - gcloud authenticated to the target project
#   - Cloud Run service already deployed (deploy/cloud-run-service.yaml)
#   - IBKR_FLEX_TOKEN / IBKR_FLEX_QUERY_ID already set as Cloud Run secrets
#     (the fetch endpoint reads them server-side; the scheduler never sees them)
#
# Usage:
#   GCP_PROJECT=my-proj bash deploy/setup-cloud-scheduler.sh
#   # override any default via env var, e.g. SCHEDULE="0 22 * * 1-5"
#
set -euo pipefail

# ── Config (override via env) ───────────────────────────────────────────────
PROJECT="${GCP_PROJECT:?Set GCP_PROJECT}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-uis-dashboard}"
JOB_NAME="${JOB_NAME:-uis-ibkr-fetch}"
READER="${READER:-ibkr}"
# Weekdays at 18:05 America/New_York (after US market close). Cron is 5 fields.
SCHEDULE="${SCHEDULE:-5 18 * * 1-5}"
TIMEZONE="${TIMEZONE:-America/New_York}"
# Dedicated least-privilege service account for the scheduler identity.
SCHED_SA_NAME="${SCHED_SA_NAME:-uis-scheduler}"
SCHED_SA_EMAIL="${SCHED_SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
# Auth mode: "oidc" (recommended; needs middleware carve-out — see docs) or
# "bearer" (works today, no code change; token sits in the job config).
AUTH_MODE="${AUTH_MODE:-oidc}"
# For AUTH_MODE=bearer only: the app bearer token "<password>.<version>".
UIS_BEARER_TOKEN="${UIS_BEARER_TOKEN:-}"

echo "Project=${PROJECT} Region=${REGION} Service=${SERVICE} Job=${JOB_NAME} Auth=${AUTH_MODE}"

# ── Resolve the live service URL + target endpoint ──────────────────────────
SERVICE_URL="$(gcloud run services describe "${SERVICE}" \
  --project "${PROJECT}" --region "${REGION}" --format='value(status.url)')"
# Cloud Run serves the API under the /api prefix (UIS_SERVE_STATIC=1).
TARGET_URL="${SERVICE_URL}/api/settings/sources/fetch/${READER}"
echo "Target: POST ${TARGET_URL}"

# ── Enable the Cloud Scheduler API (idempotent) ─────────────────────────────
gcloud services enable cloudscheduler.googleapis.com --project "${PROJECT}"

# ── Create the dedicated scheduler service account (idempotent) ─────────────
if ! gcloud iam service-accounts describe "${SCHED_SA_EMAIL}" --project "${PROJECT}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${SCHED_SA_NAME}" \
    --project "${PROJECT}" \
    --display-name "Huinsight Cloud Scheduler (IBKR fetch)"
fi

# ── Create or update the scheduler job ──────────────────────────────────────
# Determine create-vs-update so the script is re-runnable.
ACTION=create
if gcloud scheduler jobs describe "${JOB_NAME}" \
     --project "${PROJECT}" --location "${REGION}" >/dev/null 2>&1; then
  ACTION=update
fi

COMMON_ARGS=(
  "${JOB_NAME}"
  --project "${PROJECT}"
  --location "${REGION}"
  --schedule "${SCHEDULE}"
  --time-zone "${TIMEZONE}"
  --uri "${TARGET_URL}"
  --http-method POST
  --headers "Content-Type=application/json"
  --message-body '{}'
  --attempt-deadline 320s
  --max-retry-attempts 3
  --min-backoff 30s
)

if [[ "${AUTH_MODE}" == "oidc" ]]; then
  # RECOMMENDED. Scheduler presents a Google-signed OIDC token for SCHED_SA.
  # NOTE: the Huinsight app currently gates this endpoint with its own bearer-token
  # middleware. Until the middleware accepts the scheduler's OIDC identity for
  # the fetch path (see docs/deployment/cloud-scheduler-ibkr.md → "OIDC carve-out"),
  # the request will 401. Grant run.invoker so GCP-level auth is also satisfied.
  gcloud run services add-iam-policy-binding "${SERVICE}" \
    --project "${PROJECT}" --region "${REGION}" \
    --member "serviceAccount:${SCHED_SA_EMAIL}" \
    --role roles/run.invoker
  gcloud scheduler jobs "${ACTION}" http "${COMMON_ARGS[@]}" \
    --oidc-service-account-email "${SCHED_SA_EMAIL}" \
    --oidc-token-audience "${SERVICE_URL}"
elif [[ "${AUTH_MODE}" == "bearer" ]]; then
  # QUICK-START, no code change. The app bearer token is placed in the job
  # config (visible to anyone with cloudscheduler.viewer) — restrict IAM and
  # rotate the token periodically. Prefer OIDC for anything beyond a trial.
  : "${UIS_BEARER_TOKEN:?AUTH_MODE=bearer requires UIS_BEARER_TOKEN=<password>.<version>}"
  gcloud scheduler jobs "${ACTION}" http "${COMMON_ARGS[@]}" \
    --headers "Authorization=Bearer ${UIS_BEARER_TOKEN}"
else
  echo "Unknown AUTH_MODE='${AUTH_MODE}' (use 'oidc' or 'bearer')" >&2
  exit 2
fi

echo "Done. Inspect with: gcloud scheduler jobs describe ${JOB_NAME} --location ${REGION}"
echo "Force a run with:   gcloud scheduler jobs run ${JOB_NAME} --location ${REGION}"
