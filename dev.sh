#!/usr/bin/env bash
set -e

echo "==================================="
echo "  PlantGeo — Local Dev Environment"
echo "==================================="
echo ""

# Check prerequisites
command -v node >/dev/null 2>&1 || { echo "Error: Node.js is required. Install from https://nodejs.org"; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "Error: npm is required."; exit 1; }

# Check if Docker/Podman is available
COMPOSE_CMD=""
if command -v podman >/dev/null 2>&1 && command -v podman-compose >/dev/null 2>&1; then
  COMPOSE_CMD="podman compose"
elif command -v docker >/dev/null 2>&1; then
  COMPOSE_CMD="docker compose"
fi

# Start infrastructure if Docker/Podman available
if [ -n "$COMPOSE_CMD" ]; then
  echo "[1/3] Starting infrastructure (PostGIS + Redis)..."
  $COMPOSE_CMD up -d
  echo "  Waiting for services to be healthy..."
  sleep 5
else
  echo "[1/3] Skipping infrastructure (no Docker/Podman found)"
  echo "  The app will run with demo data only"
fi

# Install dependencies if needed
if [ ! -d "node_modules" ]; then
  echo "[2/3] Installing dependencies..."
  npm install
else
  echo "[2/3] Dependencies already installed"
fi

# Start Next.js dev server
echo "[3/3] Starting Next.js dev server..."
echo ""
echo "  App:       http://localhost:3000"
echo "  PostGIS:   localhost:5432"
echo "  Redis:     localhost:6379"
echo ""
npm run dev
