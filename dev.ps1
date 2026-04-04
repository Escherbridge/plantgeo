Write-Host "===================================" -ForegroundColor Cyan
Write-Host "  PlantGeo - Local Dev Environment" -ForegroundColor Cyan
Write-Host "===================================" -ForegroundColor Cyan
Write-Host ""

# Kill any existing process on port 3001
$existing = Get-NetTCPConnection -LocalPort 3001 -ErrorAction SilentlyContinue
if ($existing) {
    $procIds = $existing.OwningProcess | Sort-Object -Unique
    foreach ($procId in $procIds) {
        Write-Host "  Killing existing process on port 3001 (PID: $procId)..." -ForegroundColor Yellow
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
}

# Check prerequisites
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "Error: Node.js is required. Install from https://nodejs.org" -ForegroundColor Red
    exit 1
}

# Check if Docker/Podman is available
$composeCmd = $null
if (Get-Command podman -ErrorAction SilentlyContinue) {
    $composeCmd = "podman"
} elseif (Get-Command docker -ErrorAction SilentlyContinue) {
    $composeCmd = "docker"
}

# Start infrastructure if available
if ($composeCmd) {
    Write-Host "[1/3] Starting infrastructure (PostGIS + Redis)..." -ForegroundColor Yellow
    & $composeCmd compose up -d
    Write-Host "  Waiting for services to be healthy..."
    Start-Sleep -Seconds 5
} else {
    Write-Host "[1/3] Skipping infrastructure (no Docker/Podman found)" -ForegroundColor Yellow
    Write-Host "  The app will run with demo data only (Redis/PostGIS features disabled)"
}

# Install dependencies if needed
if (-not (Test-Path "node_modules")) {
    Write-Host "[2/3] Installing dependencies..." -ForegroundColor Yellow
    npm install
} else {
    Write-Host "[2/3] Dependencies already installed" -ForegroundColor Yellow
}

# Start Next.js dev server
Write-Host "[3/3] Starting Next.js dev server..." -ForegroundColor Yellow
Write-Host ""
Write-Host "  App:       http://localhost:3001" -ForegroundColor Green
Write-Host "  PostGIS:   localhost:5432" -ForegroundColor Green
Write-Host "  Redis:     localhost:6379" -ForegroundColor Green
Write-Host ""
npm run dev
