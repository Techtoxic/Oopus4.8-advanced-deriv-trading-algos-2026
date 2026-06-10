# Fable compile-all: downloads the session branch, copies EAs into MT5,
# compiles each with MetaEditor, prints a result table.
# Usage: powershell -ExecutionPolicy Bypass -File fable_compile.ps1 -Pat <github_pat>
param([string]$Pat)

$ErrorActionPreference = 'Continue'
$repo = 'Techtoxic/Oopus4.8-advanced-deriv-trading-algos-2026'
$branch = 'fable-session-2026-06-10'
$work = 'C:\FableCompile'

New-Item -ItemType Directory -Force -Path $work | Out-Null
Set-Location $work

Write-Host "[1/5] downloading branch zip..."
$H = @{ Authorization = "token $Pat"; 'User-Agent' = 'fable' }
Invoke-WebRequest -Headers $H -Uri "https://api.github.com/repos/$repo/zipball/$branch" -OutFile fable.zip
if (Test-Path src) { Remove-Item -Recurse -Force src }
Expand-Archive -Force fable.zip -DestinationPath src
$srcdir = (Get-ChildItem src -Directory | Select-Object -First 1).FullName
Write-Host "  src: $srcdir"

Write-Host "[2/5] locating MetaEditor..."
$me = $null
foreach ($root in @('C:\Program Files', 'C:\Program Files (x86)')) {
    $found = Get-ChildItem $root -Recurse -Filter 'metaeditor64.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) { $me = $found.FullName; break }
}
if (-not $me) { Write-Host 'FATAL: metaeditor64.exe not found'; exit 1 }
Write-Host "  metaeditor: $me"

Write-Host "[3/5] locating MQL5 data folder..."
$dest = $null
$roots = Get-ChildItem "$env:APPDATA\MetaQuotes\Terminal" -Directory -ErrorAction SilentlyContinue
foreach ($r in $roots) {
    if (Test-Path "$($r.FullName)\MQL5\Experts") { $dest = "$($r.FullName)\MQL5\Experts\Fable2026"; break }
}
if (-not $dest) {
    # portable installs: MQL5 next to the terminal exe
    $tdir = Split-Path $me
    if (Test-Path "$tdir\MQL5\Experts") { $dest = "$tdir\MQL5\Experts\Fable2026" }
}
if (-not $dest) { Write-Host 'FATAL: MQL5 data folder not found'; exit 1 }
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Write-Host "  dest: $dest"

Write-Host "[4/5] copying sources..."
foreach ($d in @('MQL5-ENHANCED', 'ELITE-MT5', 'HYBRID-BOTS', 'PRIVATE-FABLE')) {
    Copy-Item "$srcdir\$d\*.mq5" $dest -Force -ErrorAction SilentlyContinue
    Copy-Item "$srcdir\$d\*.mqh" $dest -Force -ErrorAction SilentlyContinue
}
Get-ChildItem "$dest\*.mq5" | ForEach-Object { Write-Host "  $($_.Name)" }

Write-Host "[5/5] compiling..."
$results = @()
Get-ChildItem "$dest\*.mq5" | ForEach-Object {
    $log = "$($_.FullName).log"
    if (Test-Path $log) { Remove-Item $log -Force }
    Start-Process -FilePath $me -ArgumentList "/compile:`"$($_.FullName)`"", "/log:`"$log`"" -Wait -NoNewWindow
    $tail = ''
    if (Test-Path $log) {
        $content = Get-Content $log -Encoding Unicode -ErrorAction SilentlyContinue
        if (-not $content) { $content = Get-Content $log -ErrorAction SilentlyContinue }
        $tail = ($content | Select-String 'Result' | Select-Object -Last 1).ToString()
    }
    $results += [pscustomobject]@{ EA = $_.Name; Result = $tail }
}

Write-Host ''
Write-Host '================ COMPILE RESULTS ================'
$results | Format-Table -AutoSize | Out-String | Write-Host
$results | ConvertTo-Json | Out-File "$work\results.json" -Encoding utf8
Write-Host "saved: $work\results.json"
