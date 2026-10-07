[CmdletBinding()]
param(
    [switch]$SinFFmpeg
)

$ErrorActionPreference = 'Stop'
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Get-Command python -ErrorAction Stop

function Find-Program([string]$Name) {
    $Found = Get-Command $Name -ErrorAction SilentlyContinue
    if ($Found) { return $Found.Source }

    $WingetLinks = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\$Name.exe"
    if (Test-Path -LiteralPath $WingetLinks) { return $WingetLinks }

    $Packages = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    if (Test-Path -LiteralPath $Packages) {
        $Candidate = Get-ChildItem -LiteralPath $Packages -Filter "$Name.exe" -File -Recurse |
            Select-Object -First 1 -ExpandProperty FullName
        if ($Candidate) { return $Candidate }
    }
    return $null
}

& $Python.Source -m pip install --upgrade pyinstaller

$PyInstallerArgs = @(
    '--noconfirm',
    '--clean',
    '--onefile',
    '--console',
    '--name', 'convertir_hls_windows',
    '--distpath', (Join-Path $ProjectDir 'dist'),
    '--workpath', (Join-Path $ProjectDir 'build'),
    '--specpath', $ProjectDir
)

if (-not $SinFFmpeg) {
    $FFmpeg = Find-Program 'ffmpeg'
    $FFprobe = Find-Program 'ffprobe'
    if (-not $FFmpeg -or -not $FFprobe) {
        throw "No se encontro FFmpeg. Instala primero con: winget install --id Gyan.FFmpeg --exact"
    }
    $PyInstallerArgs += @('--add-binary', "$FFmpeg;.", '--add-binary', "$FFprobe;.")
}

$PyInstallerArgs += (Join-Path $ProjectDir 'convertir_hls.py')
& $Python.Source -m PyInstaller @PyInstallerArgs

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller termino con codigo $LASTEXITCODE"
}

Write-Host "`nEjecutable creado en: $ProjectDir\dist\convertir_hls_windows.exe"
