#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${GCP_PROJECT:-}" ]]; then
  echo "GCP_PROJECT is required"
  exit 1
fi

if [[ -z "${BUCKET:-}" ]]; then
  echo "BUCKET is required"
  exit 1
fi

gcloud config set project "${GCP_PROJECT}" >/dev/null

echo "Enabling required APIs..."
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com \
  --project "${GCP_PROJECT}"

echo "Creating bucket (if missing) with uniform access and versioning..."
if ! gcloud storage buckets describe "gs://${BUCKET}" --project "${GCP_PROJECT}" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${BUCKET}" \
    --project "${GCP_PROJECT}" \
    --location us-central1 \
    --uniform-bucket-level-access
fi
gcloud storage buckets update "gs://${BUCKET}" --project "${GCP_PROJECT}" --versioning

if [[ ! -f "data/unified.duckdb" ]]; then
  echo "Missing data/unified.duckdb"
  exit 1
fi

echo "Uploading canonical database seed..."
if gcloud storage ls "gs://${BUCKET}/db/unified.duckdb" --project "${GCP_PROJECT}" >/dev/null 2>&1; then
  if [[ "${FORCE_SEED:-0}" != "1" ]]; then
    echo "ERROR: canonical DB already exists at gs://${BUCKET}/db/unified.duckdb."
    echo "Re-run with FORCE_SEED=1 to overwrite (existing DB will be backed up first)."
    exit 1
  fi
  ts=$(date +%Y%m%d_%H%M%S)
  echo "Backing up existing DB to gs://${BUCKET}/backups/pre-seed-${ts}_unified.duckdb..."
  gcloud storage cp "gs://${BUCKET}/db/unified.duckdb" "gs://${BUCKET}/backups/pre-seed-${ts}_unified.duckdb" --project "${GCP_PROJECT}"
fi
gcloud storage cp "data/unified.duckdb" "gs://${BUCKET}/db/unified.duckdb" --project "${GCP_PROJECT}"

echo "Creating Artifact Registry repo (if missing)..."
if ! gcloud artifacts repositories describe "uis" --location "us-central1" --project "${GCP_PROJECT}" >/dev/null 2>&1; then
  gcloud artifacts repositories create "uis" \
    --project "${GCP_PROJECT}" \
    --location "us-central1" \
    --repository-format "docker" \
    --description "Huinsight container images"
fi

upsert_secret() {
  local secret_name="$1"
  local secret_value=""
  read -r -s -p "Enter value for ${secret_name}: " secret_value
  echo

  if gcloud secrets describe "${secret_name}" --project "${GCP_PROJECT}" >/dev/null 2>&1; then
    printf "%s" "${secret_value}" | gcloud secrets versions add "${secret_name}" --data-file=- --project "${GCP_PROJECT}" >/dev/null
  else
    printf "%s" "${secret_value}" | gcloud secrets create "${secret_name}" --replication-policy="automatic" --data-file=- --project "${GCP_PROJECT}" >/dev/null
  fi
}

echo "Populate required secrets:"
upsert_secret "FRED_API_KEY"
upsert_secret "GEMINI_API_KEY"
upsert_secret "DEEPSEEK_API_KEY"
upsert_secret "UIS_AUTH_TOKEN"
upsert_secret "UIS_GCS_BUCKET"
upsert_secret "UIS_ALLOWED_ORIGIN"

echo "Setup complete."
