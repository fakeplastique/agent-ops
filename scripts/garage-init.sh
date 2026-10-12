#!/usr/bin/env bash
# One-time setup of the dev Garage started by `docker compose --profile storage up -d garage`:
# a one-node layout, the bucket and an access key. Safe to run twice. Prints the settings for the API.
set -euo pipefail
cd "$(dirname "$0")/.."

BUCKET="${S3_BUCKET:-agent-ops-files}"
KEY_ID="GK0123456789abcdef01234567"   # "GK" + 24 hex; a dev-only credential
SECRET="abababababababababababababababababababababababababababababababab"  # 64 hex
g() { docker compose --profile storage exec -T garage /garage "$@"; }

for _ in $(seq 1 30); do g status >/dev/null 2>&1 && break; sleep 1; done
NODE="$(g node id -q | cut -d@ -f1)"
if g layout show | grep -q "No nodes"; then
  g layout assign -z dc1 -c 1G "$NODE"
  g layout apply --version 1
fi
g bucket create "$BUCKET" 2>/dev/null || true
g key import --yes -n agent-ops "$KEY_ID" "$SECRET" >/dev/null 2>&1 || true  # already there on a re-run
g bucket allow --read --write --owner "$BUCKET" --key "$KEY_ID"

cat <<EOF
FILE_STORAGE=s3
S3_ENDPOINT_URL=http://localhost:${GARAGE_PORT:-3900}
S3_BUCKET=$BUCKET
S3_ACCESS_KEY=$KEY_ID
S3_SECRET_KEY=$SECRET
S3_REGION=garage
EOF
