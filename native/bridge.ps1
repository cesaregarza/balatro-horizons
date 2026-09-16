param([ValidateSet('rpc','launch','stop')][string]$Mode='rpc', [int]$Port=12346, [switch]$Calibration, [ValidatePattern("^[a-f0-9]{32}$")][string]$InstanceId)
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$runtime = 'D:\BalatroHorizonsRuntime'
if ($Mode -eq 'launch') {
  if (Test-Path "$runtime\process.json") {
    $old = Get-Content "$runtime\process.json" -Raw | ConvertFrom-Json
    $running = Get-Process -Id $old.pid -ErrorAction SilentlyContinue
    if ($running -and $running.Path -eq "$runtime\Balatro.exe") { throw 'Isolated runtime already running' }
  }
  $env:LOVELY_MOD_DIR = "$runtime\Mods"
  $env:BH_ISOLATED_RUNTIME = '1'
  if (-not $InstanceId) { $InstanceId = [guid]::NewGuid().ToString('N') }
  $env:BH_INSTANCE_ID = $InstanceId
  $env:BH_RUNTIME = $runtime
  $env:BH_CALIBRATION = $(if ($Calibration) {'1'} else {'0'})
  $env:BH_TOKEN = (Get-Content "$runtime\token.txt" -Raw).Trim()
  $env:BALATROBOT_HOST = '127.0.0.1'
  $env:BALATROBOT_PORT = "$Port"
  $env:BALATROBOT_HEADLESS = '0'
  $env:BALATROBOT_FAST = '0'
  $env:BALATROBOT_GAMESPEED = '1'
  $env:BALATROBOT_ANIMATION_FPS = '60'
  $env:BALATROBOT_DEBUG = '0'
  $process = Start-Process -FilePath "$runtime\Balatro.exe" -WorkingDirectory $runtime -PassThru -RedirectStandardOutput "$runtime\stdout.log" -RedirectStandardError "$runtime\stderr.log"
  $record = @{instance_id=$InstanceId;pid=$process.Id; started=$process.StartTime.ToUniversalTime().ToString('o')}
  $record | ConvertTo-Json -Compress | Set-Content "$runtime\process.json"
  $record | ConvertTo-Json -Compress
  exit 0
}
if ($Mode -eq 'stop') {
  if (Test-Path "$runtime\process.json") {
    $record = Get-Content "$runtime\process.json" -Raw | ConvertFrom-Json
    $process = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
    if ((-not $InstanceId -or $record.instance_id -eq $InstanceId) -and $process -and $process.Path -eq "$runtime\Balatro.exe" -and $process.StartTime.ToUniversalTime().ToString('o') -eq $record.started) {
      Stop-Process -Id $process.Id
    }
  }
  '{"stopped":true}'
  exit 0
}
$allowed = @('health','bh_inspect','bh_action','bh_request_status','bh_rules','bh_fixture','start','menu','save','load','select','skip','cash_out','next_round','reroll','rearrange','pack')
while ($null -ne ($line = [Console]::ReadLine())) {
  try {
    $request = $line | ConvertFrom-Json
    if ($allowed -notcontains $request.method) { throw 'RPC_METHOD_FORBIDDEN' }
    if (-not $request.params) { $request | Add-Member -NotePropertyName params -NotePropertyValue @{} -Force }
    $request.params | Add-Member -NotePropertyName _bh_token -NotePropertyValue (Get-Content "$runtime\token.txt" -Raw).Trim() -Force
    $body = $request | ConvertTo-Json -Depth 80 -Compress
    $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -Method Post -ContentType 'application/json' -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 90 -UseBasicParsing
    [Console]::WriteLine($response.Content)
  } catch {
    [Console]::WriteLine('{"bridge_error":"RPC_TRANSPORT_FAILURE"}')
  }
}
