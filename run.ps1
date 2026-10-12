param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) { throw '先に setup.ps1 を実行してください。' }
$env:PYTHONUTF8 = '1'
Write-Host "Manga Blendy: http://127.0.0.1:$Port"
Write-Host '閲覧ではBlenderは起動しません。撮影時は必要なBlender/MCP接続を確認してください。Ctrl+Cでアプリを終了します。'
& '.venv\Scripts\python.exe' -m manga_blendy --port $Port
