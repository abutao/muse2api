$ErrorActionPreference = 'Stop'
$base = 'http://127.0.0.1:8090'
$jar = Join-Path $env:TEMP 'mp_smoke.txt'
if (Test-Path $jar) { Remove-Item $jar -Force }
$login = Invoke-RestMethod -Uri "$base/api/login" -Method Post -ContentType 'application/json' -Body '{"user":"admin","password":"change-me"}' -SessionVariable sess
$tok = Invoke-RestMethod -Uri "$base/api/generate/token" -Method Post -ContentType 'application/json' -Body '{}' -WebSession $sess
Write-Host "TOKEN_OK expires_in=$($tok.expires_in)"
$headers = @{ Authorization = "Bearer $($tok.token)" }
$create = Invoke-RestMethod -Uri "$base/v1/images/tasks" -Method Post -ContentType 'application/json' -Headers $headers -Body '{"model":"muse-image","prompt":"history smoke win edge"}' -WebSession $sess
$create | ConvertTo-Json -Depth 6 -Compress | Write-Host
$tid = $create.id
if (-not $tid) { $tid = $create.task_id }
Write-Host "TASK_ID=$tid"
Start-Sleep -Seconds 1
$poll = Invoke-RestMethod -Uri "$base/v1/images/tasks/$tid" -Method Get -Headers $headers -WebSession $sess
$poll | ConvertTo-Json -Depth 8 -Compress | Write-Host
$st = [string]$poll.status
$url = $null
if ($poll.result -and $poll.result.data -and $poll.result.data[0]) { $url = $poll.result.data[0].url }
elseif ($poll.data -and $poll.data[0]) { $url = $poll.data[0].url }
Write-Host "POLL_STATUS=$st URL=$url"
$gallery = Invoke-RestMethod -Uri "$base/api/generate/gallery?limit=9" -Method Get -WebSession $sess
Write-Host ("GALLERY_COUNT=" + $gallery.count)
$gallery.items | Select-Object -First 3 | ForEach-Object {
  Write-Host ("GALLERY_ITEM id=$($_.id) url=$($_.url) media_urls=$($_.media_urls -join ',') prompt=$($_.prompt)")
}
$recs = Invoke-RestMethod -Uri "$base/api/records?page_size=10&api_type=image" -Method Get -WebSession $sess
Write-Host ("RECORDS_TOTAL=" + $recs.total)
$recs.items | Select-Object -First 5 | ForEach-Object {
  $mu = if ($_.media_urls) { ($_.media_urls -join ',') } else { '' }
  Write-Host ("REC id=$($_.id) method=$($_.method) status=$($_.status) task=$($_.upstream_task_id) media=$mu prompt=$($_.prompt_preview)")
}
