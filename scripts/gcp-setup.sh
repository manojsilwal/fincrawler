#!/usr/bin/env bash
# =============================================================================
# gcp-setup.sh — One-shot bootstrap for FinCrawler on a GCP Debian 12 VM
# Run as the default (non-root) user after SSH-ing in:
#   gcloud compute ssh fincrawler-vm --zone=us-central1-a
#   bash <(curl -fsSL https://raw.githubusercontent.com/YOUR_ORG/fincrawler/main/scripts/gcp-setup.sh)
# =============================================================================
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/manojsilwal/fincrawler.git}"
REPO_DIR="${HOME}/fincrawler"
ZONE="${ZONE:-us-central1-a}"

step() { echo -e "\n\033[1;36m▶ $*\033[0m"; }
ok()   { echo -e "\033[1;32m✔ $*\033[0m"; }
warn() { echo -e "\033[1;33m⚠ $*\033[0m"; }

# ---------------------------------------------------------------------------
step "1/5 System update"
sudo apt-get update -qq && sudo apt-get upgrade -y -qq
sudo apt-get install -y -qq curl git ca-certificates gnupg lsb-release
ok "System updated"

# ---------------------------------------------------------------------------
step "2/5 Docker install"
if ! command -v docker &>/dev/null; then
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg \
    | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
    https://download.docker.com/linux/debian \
    $(lsb_release -cs) stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update -qq
  sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
  sudo usermod -aG docker "$USER"
  ok "Docker installed"
else
  ok "Docker already present — skipping"
fi

# ---------------------------------------------------------------------------
step "3/5 Validate FinCrawler repo"
if [ ! -d "$REPO_DIR" ]; then
  warn "FinCrawler repository not found at $REPO_DIR. Cloning now..."
  git clone "$REPO_URL" "$REPO_DIR"
fi
cd "$REPO_DIR"
ok "FinCrawler repo ready at $REPO_DIR"

# ---------------------------------------------------------------------------
step "4/5 Configure secrets"
if [ ! -f .env.gcp ]; then
  if [ -f .env.example ]; then
    cp .env.example .env.gcp
  else
    echo "API_KEY=" > .env.gcp
    echo "LLM_API_KEY=" >> .env.gcp
  fi

  # Auto-generate API Key if not set
  API_KEY=$(openssl rand -hex 32)
  sed -i "s|^API_KEY=.*|API_KEY=${API_KEY}|" .env.gcp

  echo ""
  warn "Action Required: Fill in the remaining secrets in .env.gcp:"
  warn "  nano $REPO_DIR/.env.gcp"
  warn ""
  warn "Required:"
  warn "  LLM_API_KEY    — from DeepSeek/Nvidia"
  warn ""
  warn "Pre-filled:"
  warn "  API_KEY        ✔ (auto-generated)"
  echo ""
  warn "NOTE: Please review $REPO_DIR/.env.gcp on the VM to insert your LLM_API_KEY later."
  echo ""
else
  ok ".env.gcp already exists — skipping"
fi

# ---------------------------------------------------------------------------
step "5/5 Start services"
# Docker group membership requires a new shell; use sg to avoid needing logout
sg docker -c "docker compose -f docker-compose.gcp.yml up -d --build"

echo ""
ok "FinCrawler started."
echo ""
echo "  FinCrawler API → http://localhost:10000 (or via your VM's public IP / Tailscale)"
echo ""
echo "To test the health check:"
echo "  curl http://localhost:10000/health"
echo ""
echo "Watch logs:"
echo "  docker compose -f docker-compose.gcp.yml logs -f"
