# Light machine-load sampler for renderer A/B runs (read-only; touches no process).
# Every $Every seconds: total CPU %, the AzerothSurvivors.exe PIDs alive, and the top 5 processes by CPU
# used since the previous sample. Used to tell machine contention apart from a real build cost.
# Usage: powershell -NoProfile -File cpu_sampler.ps1 -Out <log> -Seconds 600 [-Every 3]
param([string]$Out, [int]$Seconds = 600, [int]$Every = 3)
$prev = @{}
Get-Process | ForEach-Object { $prev[$_.Id] = $_.CPU }
$end = (Get-Date).AddSeconds($Seconds)
while ((Get-Date) -lt $end) {
    Start-Sleep -Seconds $Every
    $total = [math]::Round((Get-Counter '\Processor(_Total)\% Processor Time' -ErrorAction SilentlyContinue).CounterSamples[0].CookedValue, 0)
    $procs = Get-Process
    $deltas = foreach ($p in $procs) {
        if ($null -ne $p.CPU) {
            $d = $p.CPU - $(if ($prev.ContainsKey($p.Id)) { $prev[$p.Id] } else { 0 })
            [pscustomobject]@{ Id = $p.Id; Name = $p.ProcessName; D = $d }
        }
    }
    $prev = @{}
    $procs | ForEach-Object { $prev[$_.Id] = $_.CPU }
    $games = ($procs | Where-Object { $_.ProcessName -eq 'AzerothSurvivors' } | ForEach-Object { $_.Id }) -join ','
    $top = ($deltas | Sort-Object D -Descending | Select-Object -First 5 | ForEach-Object { '{0}:{1}={2:N1}s' -f $_.Name, $_.Id, $_.D }) -join ' '
    '{0} cpu={1}% games=[{2}] top: {3}' -f (Get-Date -Format 'HH:mm:ss'), $total, $games, $top | Out-File -FilePath $Out -Append -Encoding utf8
}
