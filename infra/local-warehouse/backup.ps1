param(
    [Parameter(Mandatory)]
    [string]$OutputDirectory,
    [Parameter(Mandatory)]
    [string]$Container,
    [int]$KeepLatest = 8
)

$ErrorActionPreference = "Stop"
if ($KeepLatest -lt 1) {
    throw "KeepLatest must be at least one."
}

$backupRoot = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
& podman exec $Container pg_isready -U plantgeo_owner -d plantgeo | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "The local PlantGeo warehouse is not ready."
}

$timestamp = Get-Date -Format "yyyyMMddTHHmmssZ"
$archivePath = Join-Path $backupRoot "plantgeo-$timestamp.dump"
$manifestPath = "$archivePath.manifest.json"
$containerArchive = "/tmp/plantgeo-$timestamp.dump"
try {
    & podman exec $Container pg_dump -U plantgeo_owner -d plantgeo --format=custom --compress=zstd --file $containerArchive
    if ($LASTEXITCODE -ne 0) {
        throw "pg_dump failed."
    }
    $containerArchiveBytes = [long]((& podman exec $Container stat -c %s $containerArchive).Trim())
    if ($LASTEXITCODE -ne 0 -or $containerArchiveBytes -lt 1) {
        throw "pg_dump did not leave a nonempty archive in the warehouse container."
    }
    & podman cp "${Container}:$containerArchive" $archivePath
    if ($LASTEXITCODE -ne 0) {
        throw "podman could not copy the completed archive from the warehouse container."
    }
} catch {
    Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue
    throw
} finally {
    & podman exec $Container rm -f $containerArchive | Out-Null
}

$archive = Get-Item -LiteralPath $archivePath
if ($archive.Length -lt 1) {
    Remove-Item -LiteralPath $archivePath -Force
    throw "pg_dump produced an empty archive."
}
if ($archive.Length -ne $containerArchiveBytes) {
    Remove-Item -LiteralPath $archivePath -Force
    throw "The copied backup size does not match the verified container archive."
}
$manifest = [ordered]@{
    schema_version = 1
    database = "plantgeo"
    container = $Container
    created_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    archive_file = $archive.Name
    archive_bytes = $archive.Length
    archive_sha256 = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
}
[System.IO.File]::WriteAllText(
    $manifestPath,
    ($manifest | ConvertTo-Json -Depth 4),
    [System.Text.UTF8Encoding]::new($false)
)

$archives = Get-ChildItem -LiteralPath $backupRoot -Filter "plantgeo-*.dump" -File |
    Sort-Object LastWriteTimeUtc -Descending
if ($archives.Count -gt $KeepLatest) {
    $archives | Select-Object -Skip $KeepLatest | ForEach-Object {
        Remove-Item -LiteralPath $_.FullName -Force
        Remove-Item -LiteralPath "$($_.FullName).manifest.json" -Force -ErrorAction SilentlyContinue
    }
}

Write-Output $manifestPath
