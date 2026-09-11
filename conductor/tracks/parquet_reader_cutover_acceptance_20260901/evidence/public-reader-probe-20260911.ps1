param(
  [string]$Origin = 'https://plantgeo-main-production.up.railway.app',
  [switch]$Supplemental,
  [string]$OutputDirectory = (Join-Path $PSScriptRoot ('public-reader-probe-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')))
)

$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $OutputDirectory) { throw 'OutputDirectory must be a new directory to preserve existing evidence.' }
$evidenceRoot = (New-Item -ItemType Directory -Path $OutputDirectory).FullName
$products = @(
  @{name='fire'; route='wildfire.getFireDetections'; day='2026-09-09'},
  @{name='water'; route='environmental.getStreamflow'; day='2026-09-11'},
  @{name='weather'; route='wildfire.getWeatherForBbox'; day='2026-09-11'},
  @{name='vegetation'; route='environmental.getVegetationIndex'; day='2026-08-31'},
  @{name='drought'; route='environmental.getDroughtClassification'; day='2026-09-11'},
  @{name='burn-severity'; route='environmental.getBurnSeverity'; day='2026-09-11'},
  @{name='sensors'; route='environmental.getSensorStations'; day='2026-09-09'},
  @{name='fire-perimeters'; route='environmental.getFirePerimeters'; day='2026-09-04'},
  @{name='evacuation-zones'; route='environmental.getEvacuationZones'; day='2026-09-10'},
  @{name='watersheds'; route='environmental.getWatershedBoundaries'; day='2026-08-07'},
  @{name='soil-moisture-surface'; route='environmental.getSoilField'; day='2026-09-02'; extras=@{measure='moisture'; depth='surface'}},
  @{name='temperature-mean'; route='environmental.getClimateField'; day='2026-09-06'; extras=@{signal='air-temperature'; variant='mean'}}
)
$metrics = [System.Collections.Generic.List[object]]::new()
$metricName = 'public-reader-metrics-20260911.json'
if ($Supplemental) {
  $metricName = 'public-reader-supplemental-metrics-20260911.json'
  $products = @(
    @{name='burn-severity-source'; route='environmental.getBurnSeverity'; day='2026-09-11'; bbox='-125,42,-111,49'; zooms=@(0,9,13); samples=@('first')},
    @{name='temperature-mean-historical'; route='environmental.getClimateField'; day='2026-08-06'; bbox='-116.5,43.3,-115.8,43.9'; extras=@{signal='air-temperature'; variant='mean'}; zooms=@(0,9,13); samples=@('first')},
    @{name='water-boise'; route='environmental.getStreamflow'; day='2026-09-11'; bbox='-116.5,43.3,-115.8,43.9'; zooms=@(9); samples=@('first','repeat')},
    @{name='weather-boise'; route='wildfire.getWeatherForBbox'; day='2026-09-11'; bbox='-116.5,43.3,-115.8,43.9'; zooms=@(9); samples=@('first','repeat')},
    @{name='soil-moisture-surface-boise'; route='environmental.getSoilField'; day='2026-09-02'; bbox='-116.5,43.3,-115.8,43.9'; extras=@{measure='moisture'; depth='surface'}; zooms=@(9); samples=@('first','repeat')}
  )
}
foreach ($product in $products) {
  $zooms = if ($product.zooms) { $product.zooms } else { @(0,9,13) }
  $samples = if ($product.samples) { $product.samples } else { @('first','repeat') }
  $bbox = if ($product.bbox) { $product.bbox } else { '-105.5,39.5,-105.0,40.0' }
  foreach ($zoom in $zooms) {
    $readerInput = @{bbox=$bbox; date=$product.day; zoom=$zoom}
    if ($product.extras) { foreach ($key in $product.extras.Keys) { $readerInput[$key] = $product.extras[$key] } }
    $encoded = [Uri]::EscapeDataString((@{json=$readerInput} | ConvertTo-Json -Compress))
    $url = "$Origin/api/trpc/$($product.route)?input=$encoded"
    foreach ($sample in $samples) {
      $fileStem = "reader-$($product.name)-z$zoom-$sample-20260911"
      $stamp = [DateTime]::UtcNow.ToString('o')
      $rawMetric = & curl.exe --silent --show-error --max-time 18 --max-filesize 2000000 --dump-header "$evidenceRoot/$fileStem.headers.txt" --output "$evidenceRoot/$fileStem.json" --write-out '%{json}' $url
      $curlExit = $LASTEXITCODE
      $parsedMetric = $rawMetric | ConvertFrom-Json
      $safeMetric = $parsedMetric | Select-Object url_effective,http_code,time_starttransfer,time_total,size_download,exitcode,errormsg
      $headerFile = "$evidenceRoot/$fileStem.headers.txt"
      @(Get-Content -LiteralPath $headerFile | Where-Object { $_ -match '^(HTTP/|Date:|Content-Type:|Cache-Control:|X-Nextjs-Cache:)' }) | Set-Content -LiteralPath $headerFile
      $entry = [PSCustomObject]@{observed_at_utc=$stamp; product=$product.name; route=$product.route; requested_day=$product.day; bbox=$readerInput.bbox; zoom=$zoom; sample=$sample; cache_state='unproven'; curl_exit=$curlExit; response_file="$fileStem.json"; curl_metrics=$safeMetric}
      $metrics.Add($entry)
      $metrics | ConvertTo-Json -Depth 8 | Set-Content "$evidenceRoot/$metricName"
      [PSCustomObject]@{product=$product.name; zoom=$zoom; sample=$sample; status=$parsedMetric.http_code; ttfb=$parsedMetric.time_starttransfer; total=$parsedMetric.time_total; bytes=$parsedMetric.size_download} | ConvertTo-Json -Compress
    }
  }
}
