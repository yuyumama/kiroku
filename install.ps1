param(
    [Parameter(Mandatory = $true)][string]$Target,
    [switch]$Update,
    [ValidateSet('python', 'python3', 'py -3')][string]$PythonCommand = 'python'
)
$ErrorActionPreference = 'Stop'
# 起動名を指定しないときは、このスクリプトを動かせる Python 3.9 以降を探す。
# python が無く python3 だけがある環境や、Microsoft Store の python のスタブだけがある環境のため。
function Find-KirokuPython {
    foreach ($candidate in @('python', 'py -3', 'python3')) {
        $parts = $candidate.Split(' ')
        if (-not (Get-Command $parts[0] -CommandType Application -ErrorAction SilentlyContinue)) { continue }
        $probe = @($parts | Select-Object -Skip 1) + @('-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)')
        try {
            & $parts[0] @probe *> $null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        } catch { }
    }
    return $null
}

function Invoke-KirokuPython([string]$Launcher, [string[]]$Arguments) {
    $runner = $Launcher.Split(' ')
    $all = @($runner | Select-Object -Skip 1) + $Arguments
    try {
        & $runner[0] @all
    } catch [System.Management.Automation.CommandNotFoundException] {
        [Console]::Error.WriteLine("Python の起動名 '$Launcher' を実行できません：$($_.Exception.Message)")
        [Console]::Error.WriteLine("-PythonCommand で、この環境で動く起動名（python、python3、py -3）を指定してください。")
        exit 1
    }
    exit $LASTEXITCODE
}

$explicit = $PSBoundParameters.ContainsKey('PythonCommand')
$launcher = if ($explicit) { $PythonCommand } else { Find-KirokuPython }
if (-not $launcher) {
    [Console]::Error.WriteLine('Python 3.9 以降が見つかりません（python、py -3、python3 を試しました）。-PythonCommand で起動名を指定してください。')
    exit 1
}
$arguments = @((Join-Path $PSScriptRoot 'install.py'), $Target)
# 起動名を指定しない更新では、導入のときに選んだ起動名を install.py が引き継ぐ。
# 指定しない新規導入では、見つけた起動名をフックにも使う。
if ($explicit -or -not $Update) {
    $arguments += @('--python-command', $launcher)
}
if ($Update) { $arguments += '--update' }
Invoke-KirokuPython $launcher $arguments
