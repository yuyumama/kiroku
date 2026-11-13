param(
    [Parameter(Mandatory = $true)][string]$Target,
    [switch]$All,
    [switch]$KeepReports,
    [switch]$DryRun,
    [switch]$Yes,
    [ValidateSet('python', 'python3', 'py -3')][string]$PythonCommand = 'python'
)
$ErrorActionPreference = 'Stop'
$runner = $PythonCommand.Split(' ')
$arguments = @($runner | Select-Object -Skip 1)
$arguments += @((Join-Path $PSScriptRoot 'uninstall.py'), $Target)
if ($All) { $arguments += '--all' }
if ($KeepReports) { $arguments += '--keep-reports' }
if ($DryRun) { $arguments += '--dry-run' }
if ($Yes) { $arguments += '--yes' }
& $runner[0] @arguments
exit $LASTEXITCODE
