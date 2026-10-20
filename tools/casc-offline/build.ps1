param(
    [switch]$ConfigureOnly,
    [switch]$BuildOnly
)

$ErrorActionPreference = 'Stop'
$runner = Join-Path $PSScriptRoot 'build_runner.py'
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) { throw "Missing build runner: $runner" }
if ($ConfigureOnly -and $BuildOnly) { throw 'Choose only one build phase' }
if ($ConfigureOnly) {
    python $runner --configure-only
}
elseif ($BuildOnly) {
    python $runner --build-only
}
else {
    python $runner
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
