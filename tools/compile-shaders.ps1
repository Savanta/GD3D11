# Kompiluje kazdy shader z D3D11Engine/Shaders i porownuje wynik z lista znanych wyjatkow.
#
# ZMIERZONE 26.11.2026: silnik podaje profil i punkt wejscia Z KODU (vs_5_0/ps_5_0/cs_5_0/...),
# a czesc shaderow wymaga makr definiowanych przy kompilacji. Dlatego nie da sie "po prostu
# skompilowac wszystkiego" - bramka musi znac wyjatki. Lista jest ZMIERZONA, nie zgadywana:
# pierwszy przebieg ja generuje (tools/shader-allowlist.txt), a potem kazdy shader spoza listy
# musi sie skompilowac - to lapie regresje (np. literowka w shaderze) przed graczem.
$ErrorActionPreference = "Continue"
$root = Join-Path $env:GITHUB_WORKSPACE "D3D11Engine"
$allowPath = Join-Path $env:GITHUB_WORKSPACE "tools/shader-allowlist.txt"
$allow = @()
if (Test-Path $allowPath) { $allow = Get-Content $allowPath | Where-Object { $_ -and -not $_.StartsWith("#") } }

$fxc = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Filter fxc.exe -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match "x86" } | Select-Object -First 1 -ExpandProperty FullName
if (-not $fxc) { Write-Host "::error::Nie znaleziono fxc.exe w Windows Kits"; exit 1 }
Write-Host "fxc: $fxc"

$includes = @("-I", $root, "-I", (Join-Path $root "Shaders"), "-I", (Join-Path $root "include"))
$profiles = @("ps_5_0", "vs_5_0", "cs_5_0", "gs_5_0")
$entries = @("main", "PSMain", "VSMain", "CSMain", "GSMain", "HSMain", "DSMain")
$files = Get-ChildItem (Join-Path $root "Shaders") -Recurse -Include *.hlsl, *.fx
Write-Host ("shaderow: " + $files.Count)

$script:printedOne = $false
$fail = @()
foreach ($f in $files) {
    $rel = $f.FullName.Substring($env:GITHUB_WORKSPACE.Length + 1) -replace "\\", "/"
    $ok = $false
    $out = Join-Path $env:TEMP "shader-smoke.cso"
    foreach ($e in $entries) {
        foreach ($p in $profiles) {
            # ZMIERZONE 26.11.2026: "/Fo NUL" odrzuca kazde wywolanie (fxc chce zwyklej sciezki),
            # wiec pierwsza wersja tej bramki uznala wszystkie 225 plikow za zepsute.
            & $fxc /nologo /T $p /E $e /Fo $out @includes $f.FullName *> $null
            if ($LASTEXITCODE -eq 0) { $ok = $true; break }
        }
        if ($ok) { break }
    }
    if (-not $ok -and -not $script:printedOne) {
        # Raz na przebieg pokaz PRAWDZIWY powod, zeby bramka diagnozowala sama siebie.
        $script:printedOne = $true
        Write-Host ("  przyklad bledu fxc (" + $rel + "):")
        & $fxc /nologo /T ps_5_0 /E main /Fo $out @includes $f.FullName 2>&1 | Select-Object -First 8 | ForEach-Object { Write-Host ("    " + $_) }
    }
    if (-not $ok) {
        if ($allow -contains $rel) { Write-Host "  (znany wyjatek) $rel" }
        else { Write-Host "::error file=$rel::shader nie kompiluje sie zadnym profilem"; $fail += $rel }
    }
}
Write-Host ("nie kompiluje sie: " + $fail.Count + " (znanych wyjatkow: " + $allow.Count + ")")
if (-not (Test-Path $allowPath)) { $fail | Sort-Object | Set-Content $allowPath }
if ($fail.Count -gt 0) { exit 1 }
