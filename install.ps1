param(
    [Parameter(Mandatory = $true)][string]$Target,
    [switch]$Update,
    [ValidateSet('python', 'python3', 'py -3')][string]$PythonCommand = 'python'
)
$ErrorActionPreference = 'Stop'
$runner = $PythonCommand.Split(' ')
$arguments = @($runner | Select-Object -Skip 1)
$arguments += @((Join-Path $PSScriptRoot 'install.py'), $Target)
# 起動名を指定しない更新では、導入のときに選んだ起動名を install.py が引き継ぐ。
if ($PSBoundParameters.ContainsKey('PythonCommand') -or -not $Update) {
    $arguments += @('--python-command', $PythonCommand)
}
if ($Update) { $arguments += '--update' }
& $runner[0] @arguments
exit $LASTEXITCODE
