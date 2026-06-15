#!/usr/bin/env bash
# Deploy FinCrawler-owned multi-region egress proxy fleet (Squid on GCP VMs).
# Each VM gets its own external IP — internal alternative to Bright Data residential.
#
# Usage:
#   ./scripts/gcp/deploy_internal_egress.sh [PROJECT] [NUM_NODES]
#
# After deploy, add printed PROXY_POOL_URLS / INTERNAL_EGRESS_ENDPOINTS to .env.gcp
set -euo pipefail

PROJECT="${1:-$(gcloud config get-value project 2>/dev/null)}"
NUM_NODES="${2:-3}"
REGIONS=(us-central1 us-east1 us-west1)
ZONE_SUFFIX=(a b a)
FIREWALL_RULE="fincrawler-egress-proxy"
TEMPLATE="fincrawler-egress-template"
MIG="fincrawler-egress-pool"
PROXY_USER="${EGRESS_PROXY_USER:-fincrawler}"
PROXY_PASS="${EGRESS_PROXY_PASS:-$(openssl rand -hex 12)}"

echo "Deploying $NUM_NODES internal egress Squid nodes (project=$PROJECT)"

SQUID_CONF_B64=$(base64 < docker/squid/squid.conf | tr -d '\n')

STARTUP_SCRIPT=$(cat <<SCRIPT
#!/bin/bash
set -e
apt-get update -qq
apt-get install -y squid apache2-utils
echo "${SQUID_CONF_B64}" | base64 -d > /etc/squid/squid.conf
htpasswd -bc /etc/squid/passwd ${PROXY_USER} ${PROXY_PASS} 2>/dev/null || true
grep -q 'auth_param basic' /etc/squid/squid.conf || sed -i '1iauth_param basic program /usr/lib/squid/basic_ncsa_auth /etc/squid/passwd\\nauth_param basic children 5\\nauth_param basic realm FinCrawler Egress\\nacl authenticated proxy_auth REQUIRED\\nhttp_access allow authenticated' /etc/squid/squid.conf
systemctl restart squid
SCRIPT
)

if ! gcloud compute firewall-rules describe "$FIREWALL_RULE" --project="$PROJECT" &>/dev/null; then
  gcloud compute firewall-rules create "$FIREWALL_RULE" \
    --project="$PROJECT" \
    --direction=INGRESS \
    --priority=1000 \
    --network=default \
    --action=ALLOW \
    --rules=tcp:3128 \
    --source-ranges=0.0.0.0/0 \
    --target-tags=fincrawler-egress
fi

if ! gcloud compute instance-templates describe "$TEMPLATE" --project="$PROJECT" &>/dev/null; then
  gcloud compute instance-templates create "$TEMPLATE" \
    --project="$PROJECT" \
    --machine-type=e2-small \
    --boot-disk-size=10GB \
    --image-family=ubuntu-2204-lts \
    --image-project=ubuntu-os-cloud \
    --metadata=startup-script="$STARTUP_SCRIPT" \
    --tags=fincrawler-egress
fi

ZONE="${REGIONS[0]}-${ZONE_SUFFIX[0]}"
if ! gcloud compute instance-groups managed describe "$MIG" --zone="$ZONE" --project="$PROJECT" &>/dev/null; then
  gcloud compute instance-groups managed create "$MIG" \
    --project="$PROJECT" \
    --base-instance-name=fincrawler-egress \
    --template="$TEMPLATE" \
    --size="$NUM_NODES" \
    --zone="$ZONE"
else
  gcloud compute instance-groups managed resize "$MIG" --size="$NUM_NODES" --zone="$ZONE" --project="$PROJECT"
fi

echo "Waiting for instances..."
sleep 30

URLS=()
for instance in $(gcloud compute instance-groups managed list-instances "$MIG" --zone="$ZONE" --project="$PROJECT" --format='value(instance)'); do
  ip=$(gcloud compute instances describe "$instance" --zone="$ZONE" --project="$PROJECT" --format='get(networkInterfaces[0].accessConfigs[0].natIP)')
  if [ -n "$ip" ]; then
    URLS+=("http://${PROXY_USER}:${PROXY_PASS}@${ip}:3128")
  fi
done

JOINED=$(IFS=,; echo "${URLS[*]}")
echo ""
echo "=== Add to .env.gcp ==="
echo "ENABLE_INTERNAL_EGRESS=true"
echo "PROXY_BACKEND=internal"
echo "INTERNAL_EGRESS_ENDPOINTS=${JOINED}"
echo "BROWSER_PROXY_ENABLED=true"
echo "PROXY_POOL_REDIS=true"
echo ""
echo "Egress nodes: ${#URLS[@]}"
