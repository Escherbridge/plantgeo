[CmdletBinding()]
param(
    [switch]$CreateIfMissing,
    [switch]$OpenPsql,
    [int]$ReadyTimeoutSeconds = 60
)

$ErrorActionPreference = 'Stop'

$connection = 'podman-machine-default-root'
$projectName = 'plantgeo-warehouse'
$container = 'plantgeo-warehouse_plantgeo-warehouse_1'
$warehouseDirectory = $PSScriptRoot
$composeFile = Join-Path $warehouseDirectory 'compose.yaml'
$envFile = Join-Path $warehouseDirectory '.env'
$psql = 'C:\Program Files\PostgreSQL\16\bin\psql.exe'

if (-not (Get-Command podman -ErrorAction SilentlyContinue)) {
    throw 'Podman is required. Install it and initialize the default Podman machine first.'
}

$machineState = (& podman machine inspect podman-machine-default --format '{{.State}}' 2>$null).Trim()
if ($machineState -ne 'running') {
    Write-Host 'Starting the default Podman machine...'
    & podman machine start podman-machine-default
}

& podman --connection $connection container exists $container
if ($LASTEXITCODE -ne 0) {
    if (-not $CreateIfMissing) {
        throw "Warehouse container '$container' does not exist. Refusing to create a blank warehouse. Use -CreateIfMissing only for an intentional first-time local setup."
    }
    if (-not (Test-Path -LiteralPath $envFile)) {
        throw "Missing $envFile. Copy .env.example to .env and set PLANTGEO_LOCAL_DB_PASSWORD before first-time setup."
    }

    Write-Host 'Creating the local warehouse from the reviewed compose file...'
    & podman --connection $connection compose --project-name $projectName --env-file $envFile -f $composeFile up -d
    if ($LASTEXITCODE -ne 0) { throw 'Podman Compose could not create the local warehouse.' }
}
else {
    $running = (& podman --connection $connection inspect $container --format '{{.State.Running}}').Trim()
    if ($running -ne 'true') {
        Write-Host 'Starting the existing local warehouse container...'
        & podman --connection $connection start $container | Out-Null
    }
}

$deadline = (Get-Date).AddSeconds($ReadyTimeoutSeconds)
do {
    & podman --connection $connection exec $container pg_isready -U plantgeo_owner -d plantgeo *> $null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $deadline)

if ($LASTEXITCODE -ne 0) {
    throw "The warehouse did not become ready within $ReadyTimeoutSeconds seconds. Inspect it with: podman --connection $connection logs $container"
}

$readOnlyDsn = 'postgresql://plantgeo_owner@127.0.0.1:5442/plantgeo?sslmode=disable&options=-c%20default_transaction_read_only%3Don'
Write-Host ''
Write-Host 'PlantGeo local data warehouse is ready.'
Write-Host "Session-read-only owner DSN (password prompt): $readOnlyDsn"
Write-Host 'This is an owner login with a read-only session default; do not use it for application workloads or writes.'
Write-Host 'Review queries: conductor/review-packet-20260726/queries.sql'

if ($OpenPsql) {
    if (-not (Test-Path -LiteralPath $psql)) {
        throw "psql was not found at $psql. Install PostgreSQL client tools or connect with a GUI using the printed DSN."
    }
    & $psql -X $readOnlyDsn
}
