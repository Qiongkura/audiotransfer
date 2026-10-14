$ErrorActionPreference = 'Stop'
$projectDirectory = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $compiler /nologo /codepage:65001 /target:winexe /reference:System.Windows.Forms.dll "/out:$projectDirectory\AudioTransfer.exe" "$PSScriptRoot\StudioLauncher.cs"
if ($LASTEXITCODE -ne 0) { throw 'Launcher build failed' }
