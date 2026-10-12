param([string]$PythonPath = "")
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not $PythonPath) {
    $bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundledPython) { $PythonPath = $bundledPython }
    else {
        $setupPythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if (-not $setupPythonCommand) { throw 'Python 3.12以降を導入するか、-PythonPathで実行ファイルを指定してください。' }
        $PythonPath = $setupPythonCommand.Source
    }
}
& $PythonPath -c "import sys; assert sys.version_info >= (3,12), 'Python 3.12 or later required'"
if ($LASTEXITCODE) { throw 'Python 3.12以降が必要です。-PythonPathで正しい実行ファイルを指定してください。' }
New-Item -ItemType Directory -Force -Path '.work\tmp' | Out-Null
$previousTemp = $env:TEMP
$previousTmp = $env:TMP
try {
    $env:TEMP = (Resolve-Path '.work\tmp').Path
    $env:TMP = $env:TEMP
    & $PythonPath -m venv --without-pip '.venv'
    if ($LASTEXITCODE) { throw 'Pythonの作業環境を作成できませんでした。保存先の権限とPythonを確認してください。' }
    # Use the base interpreter's pip: bundled ensurepip can fail in Japanese paths.
    & $PythonPath -m pip --python '.venv\Scripts\python.exe' install -r requirements.lock
    if ($LASTEXITCODE) { throw '依存パッケージの導入に失敗しました。ネット接続とPythonのバージョンを確認してください。' }
    & $PythonPath -m pip --python '.venv\Scripts\python.exe' install --no-deps -e .
    if ($LASTEXITCODE) { throw 'Manga Blendyの導入に失敗しました。上のエラーを確認してください。' }
} finally {
    $env:TEMP = $previousTemp
    $env:TMP = $previousTmp
}
Write-Host 'Ready. Run .\run.ps1 and open http://127.0.0.1:8765'
