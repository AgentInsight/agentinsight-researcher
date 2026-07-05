<#
.SYNOPSIS
  Prepare Playwright chromium binary for offline Docker build (方案 E).
.DESCRIPTION
  Downloads Playwright chromium browser to packages/playwright-browsers/
  using a temporary Docker container. Required by Dockerfile.qa / Dockerfile.offline.
  Run this script on a network-connected machine before building offline images.
.NOTES
  Usage: powershell -ExecutionPolicy Bypass -File scripts\prepare-playwright-chromium.ps1
  Output: packages/playwright-browsers/chromium-*/ (chromium binary + deps)
#>

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$PackagesDir = Join-Path $ProjectRoot "packages"
$PwBrowsersDir = Join-Path $PackagesDir "playwright-browsers"

Write-Host "========== Prepare Playwright Chromium (Offline) ==========" -ForegroundColor Cyan
Write-Host "Project root: $ProjectRoot"
Write-Host "Output dir:   $PwBrowsersDir"
Write-Host ""

# Create output directory
if (-not (Test-Path $PwBrowsersDir)) {
    New-Item -ItemType Directory -Path $PwBrowsersDir -Force | Out-Null
}
# Keep .gitkeep
$gitkeep = Join-Path $PwBrowsersDir ".gitkeep"
if (-not (Test-Path $gitkeep)) {
    "" | Out-File -FilePath $gitkeep -Encoding ascii
}

# Step 1: Pull python:3.12-slim image
Write-Host "[1/5] Pulling python:3.12-slim image..." -ForegroundColor Yellow
docker pull python:3.12-slim
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to pull python:3.12-slim" -ForegroundColor Red
    exit 1
}

# Step 2: Run temp container to install playwright + chromium
Write-Host "[2/5] Installing playwright + chromium in temp container..." -ForegroundColor Yellow
$tempContainer = "pw-chromium-prep"
# Use cmd /c to isolate stderr from PowerShell's error handling (docker writes errors to stderr)
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$null = cmd /c "docker rm -f $tempContainer 2>nul"
$ErrorActionPreference = $prevEAP
# docker run -d may leave container in "Created" state on some Docker Desktop versions
# Use docker start to ensure it's actually running
docker run -d --name $tempContainer python:3.12-slim sleep 3600 2>&1 | Out-Null
docker start $tempContainer 2>&1 | Out-Null
Start-Sleep -Seconds 2
$containerStatus = docker ps --filter "name=$tempContainer" --format "{{.Status}}" 2>&1
if (-not $containerStatus -or $containerStatus -notmatch "Up") {
    Write-Host "ERROR: Failed to create/start temp container (status: $containerStatus)" -ForegroundColor Red
    exit 1
}
Write-Host "  Container running: $containerStatus" -ForegroundColor DarkGray

try {
    # Install playwright
    Write-Host "  Installing playwright Python package..." -ForegroundColor DarkGray
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $null = cmd /c "docker exec $tempContainer pip install playwright>=1.49 2>nul"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ERROR: pip install playwright failed" -ForegroundColor Red
        $ErrorActionPreference = $prevEAP
        exit 1
    }
    $ErrorActionPreference = $prevEAP

    # Install chromium with deps
    Write-Host "  Downloading chromium binary (this may take 3-5 minutes)..." -ForegroundColor DarkGray
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $null = cmd /c "docker exec $tempContainer playwright install --with-deps chromium 2>nul"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ERROR: playwright install chromium failed" -ForegroundColor Red
        $ErrorActionPreference = $prevEAP
        exit 1
    }
    $ErrorActionPreference = $prevEAP

    # Verify installation
    Write-Host "  Verifying chromium installation..." -ForegroundColor DarkGray
    $verifyResult = docker exec $tempContainer sh -c "ls /root/.cache/ms-playwright/ 2>&1"
    Write-Host "  Installed browsers: $verifyResult"

    # Step 3: Copy chromium binary from container to host
    Write-Host "[3/5] Copying chromium binary to packages/playwright-browsers/..." -ForegroundColor Yellow

    # Copy entire ms-playwright directory
    docker cp "${tempContainer}:/root/.cache/ms-playwright/." $PwBrowsersDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ERROR: docker cp failed" -ForegroundColor Red
        exit 1
    }

    Write-Host "  Files copied:" -ForegroundColor DarkGray
    Get-ChildItem $PwBrowsersDir | ForEach-Object { Write-Host "    $($_.Name)" -ForegroundColor DarkGray }

    # Calculate size
    $sizeBytes = (Get-ChildItem $PwBrowsersDir -Recurse | Measure-Object -Property Length -Sum).Sum
    $sizeMB = [math]::Round($sizeBytes / 1MB, 2)
    Write-Host "  Total size: $sizeMB MB" -ForegroundColor Green

    # Step 4: Download Chrome .deb dependencies (libnspr4/libnss3/libatk1.0-0/libcups2/libgbm1 etc.)
    # Chrome binary requires these shared libraries; without them it crashes with
    # "error while loading shared libraries: libnspr4.so: cannot open shared object file"
    Write-Host "[4/5] Downloading Chrome .deb dependencies to packages/debs/..." -ForegroundColor Yellow

    $DebsDir = Join-Path $PackagesDir "debs"
    if (-not (Test-Path $DebsDir)) {
        New-Item -ItemType Directory -Path $DebsDir -Force | Out-Null
    }

    # Parse deb.deps from the chromium binary directory (drop version constraints & alternatives)
    $DebDepsFile = Join-Path $PwBrowsersDir "chromium-1228\chrome-linux64\deb.deps"
    if (Test-Path $DebDepsFile) {
        $debDeps = Get-Content $DebDepsFile | ForEach-Object {
            $line = $_.Trim()
            if ($line -and $line -notmatch "^\s*#") {
                # Take first alternative (libcurl3-gnutls | libcurl4 -> libcurl3-gnutls)
                $firstAlt = ($line -split "\|")[0].Trim()
                # Strip version constraint (libasound2 (>= 1.0.17) -> libasound2)
                ($firstAlt -split "\s")[0]
            }
        } | Where-Object { $_ -and $_ -ne "wget" -and $_ -ne "ca-certificates" -and $_ -ne "xdg-utils" }

        Write-Host "  Packages to download: $($debDeps.Count)" -ForegroundColor DarkGray

        # Update apt index in temp container
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $null = cmd /c "docker exec $tempContainer apt-get update 2>nul"
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  WARNING: apt-get update failed, skipping .deb download" -ForegroundColor Yellow
            $ErrorActionPreference = $prevEAP
        } else {
            $ErrorActionPreference = $prevEAP
            # Download .deb packages (download-only, no install)
            $pkgsList = ($debDeps -join " ")
            $prevEAP = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            $null = cmd /c "docker exec $tempContainer apt-get install --download-only -y --no-install-recommends $pkgsList 2>nul"
            if ($LASTEXITCODE -ne 0) {
                Write-Host "  WARNING: apt-get install --download-only failed, skipping .deb download" -ForegroundColor Yellow
            } else {
                $ErrorActionPreference = $prevEAP
                # Copy .deb packages from container cache to packages/debs/
                docker cp "${tempContainer}:/var/cache/apt/archives/." $DebsDir
                if ($LASTEXITCODE -ne 0) {
                    Write-Host "  WARNING: docker cp .deb packages failed" -ForegroundColor Yellow
                } else {
                    $debCount = (Get-ChildItem $DebsDir -Filter "*.deb").Count
                    $debSize = [math]::Round((Get-ChildItem $DebsDir -Filter "*.deb" | Measure-Object -Property Length -Sum).Sum / 1MB, 2)
                    Write-Host "  Total .deb files in packages/debs/: $debCount ($debSize MB)" -ForegroundColor Green
                }
            }
            $ErrorActionPreference = $prevEAP
        }
    } else {
        Write-Host "  WARNING: deb.deps not found at $DebDepsFile, skipping .deb download" -ForegroundColor Yellow
        Write-Host "  Chrome may crash with missing shared libraries." -ForegroundColor Yellow
    }

} finally {
    # Step 5: Cleanup temp container
    Write-Host "[5/5] Cleaning up temp container..." -ForegroundColor Yellow
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $null = cmd /c "docker rm -f $tempContainer 2>nul"
    $ErrorActionPreference = $prevEAP
}

Write-Host ""
Write-Host "========== Preparation Complete ==========" -ForegroundColor Cyan
Write-Host "Chromium binary saved to: $PwBrowsersDir"
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Build offline image: docker-build.qa.bat (or docker-build.offline.sh)"
Write-Host "  2. Verify chromium in container: docker exec agentinsight-agent-1 python -c 'from playwright.async_api import async_playwright; import asyncio; asyncio.run((lambda: (async_playwright().__aenter__(), None))[0]())'"
Write-Host ""
