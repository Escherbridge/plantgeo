param(
    [Parameter(Mandatory)]
    [string]$ArchivePath,
    [Parameter(Mandatory)]
    [string]$Container,
    [switch]$IUnderstandThisReplacesData
)

$ErrorActionPreference = "Stop"
if (-not $IUnderstandThisReplacesData) {
    throw "Pass -IUnderstandThisReplacesData only after selecting the target database and verifying this archive."
}

$resolvedArchive = [System.IO.Path]::GetFullPath($ArchivePath)
if (-not (Test-Path -LiteralPath $resolvedArchive -PathType Leaf)) {
    throw "Archive does not exist: $resolvedArchive"
}
$manifestPath = "$resolvedArchive.manifest.json"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Archive manifest does not exist: $manifestPath"
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$actualHash = (Get-FileHash -LiteralPath $resolvedArchive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($manifest.archive_sha256 -ne $actualHash) {
    throw "Archive checksum does not match its manifest."
}

$containerArchive = "/tmp/plantgeo-restore-$PID.dump"
try {
    & podman cp $resolvedArchive "${Container}:$containerArchive"
    if ($LASTEXITCODE -ne 0) {
        throw "podman could not copy the verified archive into the warehouse container."
    }
    & podman exec $Container pg_restore -U plantgeo_owner -d plantgeo --clean --if-exists --exit-on-error $containerArchive
    if ($LASTEXITCODE -ne 0) {
        throw "pg_restore failed; inspect the target before retrying."
    }
} finally {
    & podman exec $Container rm -f $containerArchive | Out-Null
}
& podman exec $Container pg_isready -U plantgeo_owner -d plantgeo
if ($LASTEXITCODE -ne 0) {
    throw "Restore completed but the target did not pass the readiness check."
}
