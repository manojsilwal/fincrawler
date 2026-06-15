#!/usr/bin/env bash
# Deploy FinCrawler browser-grid worker pool on GCP (2–4 VMs).
# Usage: ./scripts/gcp/deploy_browser_grid.sh [NUM_WORKERS] [ZONE]
set -euo pipefail

NUM_WORKERS="${1:-3}"
ZONE="${2:-us-central1-a}"
PROJECT="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
MIG_NAME="fincrawler-browser-grid"
TEMPLATE_NAME="fincrawler-browser-grid-template"
REPO_DIR="${REPO_DIR:-/opt/fincrawler}"

echo "Deploying $NUM_WORKERS browser-grid workers in $ZONE (project: $PROJECT)"

STARTUP_SCRIPT=$(cat <<'SCRIPT'
#!/bin/bash
set -e
apt-get update -qq
apt-get install -y docker.io docker-compose-plugin git
systemctl enable docker && systemctl start docker

mkdir -p /opt/fincrawler
cd /opt/fincrawler

# Expect repo cloned or synced separately; run worker container
if [ -f docker-compose.gcp.yml ]; then
  docker compose -f docker-compose.gcp.yml up browser-grid-worker -d --scale browser-grid-worker=1
fi
SCRIPT
)

# Instance template
gcloud compute instance-templates create "$TEMPLATE_NAME" \
  --project="$PROJECT" \
  --machine-type=e2-standard-2 \
  --boot-disk-size=30GB \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --metadata=startup-script="$STARTUP_SCRIPT" \
  --tags=fincrawler-browser-grid \
  --scopes=cloud-platform

# Managed instance group
if gcloud compute instance-groups managed describe "$MIG_NAME" --zone="$ZONE" --project="$PROJECT" &>/dev/null; then
  gcloud compute instance-groups managed set-instance-template "$MIG_NAME" \
    --template="$TEMPLATE_NAME" --zone="$ZONE" --project="$PROJECT"
  gcloud compute instance-groups managed resize "$MIG_NAME" \
    --size="$NUM_WORKERS" --zone="$ZONE" --project="$PROJECT"
else
  gcloud compute instance-groups managed create "$MIG_NAME" \
    --project="$PROJECT" \
    --base-instance-name=browser-grid \
    --template="$TEMPLATE_NAME" \
    --size="$NUM_WORKERS" \
    --zone="$ZONE"
fi

echo "Done. Workers: $NUM_WORKERS"
echo "Ensure Redis is reachable from workers (REDIS_URL in .env.gcp)"
echo "Scale: gcloud compute instance-groups managed resize $MIG_NAME --size=N --zone=$ZONE"
